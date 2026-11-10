"""Groq LLM helpers (OpenAI-compatible chat completions API)."""
import json
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def _wait_seconds(resp, attempt):
    """How long Groq asks us to wait (Retry-After header or 'try again in 1.5s'), with a sane fallback."""
    try:
        return min(30.0, float(resp.headers.get("retry-after")) + 0.5)
    except (TypeError, ValueError):
        m = re.search(r"try again in ([\d.]+)(ms|s)", resp.text)
        if m:
            secs = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
            return min(30.0, secs + 0.5)
    return min(30.0, 3.0 * (attempt + 1))


def _post(payload, retries=6):
    for attempt in range(retries + 1):
        r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
                          json={"model": MODEL, **payload}, timeout=90)
        if r.status_code == 429 and attempt < retries:  # free-tier rate limit: wait as long as Groq asks
            time.sleep(_wait_seconds(r, attempt))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Groq error ({r.status_code}): {r.text[:500]}")
        return r.json()["choices"][0]["message"]


def chat(messages, tools=None, temperature=0.2):
    payload = {"messages": messages, "temperature": temperature}
    if tools:
        payload.update({"tools": tools, "tool_choice": "auto", "parallel_tool_calls": False})
    return _post(payload)


def chat_json(system, user, temperature=0):
    msg = _post({"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                 "response_format": {"type": "json_object"}, "temperature": temperature})
    try:
        return json.loads(msg.get("content") or "{}")
    except json.JSONDecodeError:
        return {}
