"""Minimal client for the local Qwen2.5-VL server (OpenAI-compatible /v1).

No `openai` dependency — just `requests`. Handles model auto-resolution, image
encoding for the vision endpoint, and lenient JSON extraction from replies.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import requests

_MODEL_CACHE: dict[str, str] = {}


def resolve_model(cfg: dict[str, Any]) -> str:
    """Return the served model id, honoring `model: AUTO` in config."""
    llm = cfg.get("llm", {})
    want = str(llm.get("model", "AUTO"))
    if want and want != "AUTO":
        return want
    base = llm["base_url"].rstrip("/")
    if base in _MODEL_CACHE:
        return _MODEL_CACHE[base]
    r = requests.get(f"{base}/models", timeout=10)
    r.raise_for_status()
    model_id = r.json()["data"][0]["id"]
    _MODEL_CACHE[base] = model_id
    return model_id


def _data_uri(image_path: str | Path) -> str:
    raw = Path(image_path).read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def chat(
    cfg: dict[str, Any],
    system: str,
    user_text: str,
    image_paths: list[str] | None = None,
) -> str:
    """Send one chat completion; return the assistant text."""
    llm = cfg.get("llm", {})
    base = llm["base_url"].rstrip("/")
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for p in image_paths or []:
        content.append({"type": "image_url", "image_url": {"url": _data_uri(p)}})
    payload = {
        "model": resolve_model(cfg),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": float(llm.get("temperature", 0.0)),
        "max_tokens": int(llm.get("max_tokens", 400)),
    }
    headers = {"Content-Type": "application/json"}
    key = llm.get("api_key")
    if key and key != "EMPTY":
        headers["Authorization"] = f"Bearer {key}"
    r = requests.post(
        f"{base}/chat/completions",
        headers=headers,
        json=payload,
        timeout=int(llm.get("timeout", 90)),
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply (tolerates fences/prose)."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace.group(0) if brace else None
    if candidate is None:
        return None
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
