"""Groq LLM helpers (OpenAI-compatible chat completions API)."""
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def _post(payload, retries=2):
    for attempt in range(retries + 1):
        r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
                          json={"model": MODEL, **payload}, timeout=90)
        if r.status_code == 429 and attempt < retries:  # free-tier rate limit: wait and retry
            time.sleep(4 * (attempt + 1))
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
