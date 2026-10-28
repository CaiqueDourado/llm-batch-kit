"""Request parts and response readers.

A prompt is either a string or a list of parts. Parts are plain dicts, so they can be stored, hashed and
sent inline in a batch request (where SDK objects such as ``PIL.Image`` are not accepted).
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any


def sha(text: str | bytes) -> str:
    """Short SHA-256 used for signatures (16 hex characters)."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()[:16]


def text(value: str) -> dict:
    """A text part."""
    return {"text": value or ""}


def image(data: bytes, mime: str = "image/jpeg") -> dict:
    """An image part from the file's bytes, sent inline as base64 text."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("image() takes the image bytes: pass open(path, 'rb').read(), not a path or base64")
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(bytes(data)).decode("ascii")}}


def parts_of(prompt: Any) -> list[dict]:
    """Normalise a prompt (string or list of parts) to a list of parts."""
    if isinstance(prompt, str):
        return [text(prompt)]
    if isinstance(prompt, (list, tuple)):
        if not prompt:
            raise ValueError("empty list of parts")
        for p in prompt:
            if not isinstance(p, dict) or not ({"text", "inline_data"} & set(p)):
                raise TypeError("a part must be text(...) or image(...)")
        return list(prompt)
    raise TypeError(f"prompt must be a string or a list of parts, not {type(prompt).__name__}")


def prompt_sha(prompt: Any) -> str:
    """Stable signature of a prompt. Images enter by the hash of their bytes, so the manifest stays small;
    reordering the parts changes the signature."""
    if isinstance(prompt, str):
        return sha(prompt)
    pieces = []
    for p in parts_of(prompt):
        if "text" in p:
            pieces.append("t:" + sha(p["text"] or ""))
        else:
            d = p["inline_data"]
            pieces.append(f"i:{d.get('mime_type', '')}:{sha(d.get('data') or '')}")
    return sha("|".join(pieces))


def text_of(resp: Any) -> str:
    """Text of a response without relying on ``resp.text``, which raises or returns None when the answer is
    only an image. Joins the text parts; an answer with no text gives ``""``."""
    try:
        t = getattr(resp, "text", None)
        if t:
            return t.strip()
    except Exception:  # noqa: BLE001 - the SDK property can raise on non-text answers
        pass
    out = []
    for cand in getattr(resp, "candidates", None) or []:
        for part in getattr(getattr(cand, "content", None), "parts", None) or []:
            t = getattr(part, "text", None)
            if t:
                out.append(t)
    return "".join(out).strip()


def images_of(resp: Any) -> list[tuple[str, bytes]]:
    """``(mime_type, bytes)`` of every image part of a response. Base64 strings are decoded."""
    out = []
    for cand in getattr(resp, "candidates", None) or []:
        for part in getattr(getattr(cand, "content", None), "parts", None) or []:
            blob = getattr(part, "inline_data", None)
            data = getattr(blob, "data", None) if blob else None
            if data is None:
                continue
            if isinstance(data, str):
                try:
                    data = base64.b64decode(data)
                except ValueError:
                    continue
            out.append((getattr(blob, "mime_type", None) or "", data))
    return out
