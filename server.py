"""Munshi web app.  Run:  uvicorn server:app --reload   then open http://localhost:8000"""
import io
import os
import uuid

import json

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import graph
import ledger
import paypal_client as pp
import rag

app = FastAPI(title="Munshi")
HERE = os.path.dirname(os.path.abspath(__file__))


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None


class ApproveIn(BaseModel):
    session_id: str
    decision: str            # approve | edit | reject
    args: dict | None = None
    reason: str | None = None


class SessionIn(BaseModel):
    session_id: str | None = None


@app.get("/")
def home():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/api/summary")
def get_summary():
    try:
        return ledger.summary()
    except Exception as e:
        raise HTTPException(502, str(e))


def _safe(fn, sid):
    try:
        return {"session_id": sid, **fn()}
    except Exception as e:
        return {"session_id": sid, "reply": f"Sorry, something went wrong: {e}", "pending": None, "trace": []}


@app.post("/api/chat")
def post_chat(body: ChatIn):
    sid = body.session_id or str(uuid.uuid4())
    return _safe(lambda: graph.run_turn(sid, body.message), sid)


@app.post("/api/approve")
def post_approve(body: ApproveIn):
    decision = {"decision": body.decision, "args": body.args or {}, "reason": body.reason or ""}
    return _safe(lambda: graph.resume(body.session_id, decision), body.session_id)


@app.post("/api/autopilot")
def post_autopilot(body: SessionIn):
    sid = body.session_id or str(uuid.uuid4())
    return _safe(lambda: graph.autopilot(sid), sid)


def _read_upload(name, raw):
    if name.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            raise HTTPException(400, "PDF support needs 'pip install pypdf'. CSV/TXT works without it.")
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(raw)).pages)
    return raw.decode("utf-8", errors="ignore")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    text = _read_upload(file.filename or "", await file.read())
    if not text.strip():
        raise HTTPException(400, "Couldn't read any text from that file.")
    try:
        return ledger.import_statement(f"File name: {file.filename}\n\n{text}")
    except Exception as e:
        raise HTTPException(502, str(e))


@app.post("/api/paypal/webhook")
async def paypal_webhook(request: Request):
    """PayPal calls this when an invoice is paid. Signature-verified and idempotent."""
    event = await request.json()
    try:
        ok = pp.verify_webhook(dict(request.headers), event)
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(400, "signature verification failed")
    is_new = db.save_webhook_event(event.get("id"), event.get("event_type"),
                                   (event.get("resource") or {}).get("invoice", {}).get("id")
                                   or (event.get("resource") or {}).get("id"),
                                   json.dumps(event)[:20000])
    # Duplicate deliveries are acknowledged but not processed twice.
    return {"received": True, "duplicate": not is_new}


@app.get("/api/rules")
def rules(q: str):
    """Transparent RAG: show exactly which rule passages the Tax advisor would retrieve."""
    return rag.search_tax_rules(q, k=4)


@app.get("/api/audit")
def audit():
    """Every human decision on a money/client-touching action: what, when, approved or not, result."""
    return db.audit_log()


@app.post("/api/reset-imports")
def reset_imports():
    db.clear()
    return {"ok": True}


@app.get("/samples/{name}")
def sample(name: str):
    path = os.path.join(HERE, "samples", os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=name)


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
