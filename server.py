"""Munshi web app.  Run:  uvicorn server:app --reload   then open http://localhost:8000"""
import io
import os
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import db
import ledger

app = FastAPI(title="Munshi")
HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = {}


class ChatIn(BaseModel):
    message: str
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


@app.post("/api/chat")
def post_chat(body: ChatIn):
    sid = body.session_id or str(uuid.uuid4())
    history = SESSIONS.setdefault(sid, [])
    tools_used = []
    try:
        reply = agent.chat(history, body.message, on_tool=lambda n, a: tools_used.append(n))
    except Exception as e:
        reply = f"Sorry, something went wrong: {e}"
    if len(history) > 40:  # keep the system prompt + recent turns
        SESSIONS[sid] = history[:1] + history[-30:]
    return {"session_id": sid, "reply": reply, "tools_used": tools_used}


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
