"""The manifest: one JSON file per submitted job, the only record of which answer belongs to which item.

It is written when the job is submitted and rewritten, atomically, after every item collected, so a
collection that dies halfway resumes where it stopped instead of writing twice.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .parts import sha

FOLDER = "batches"


def signature(items: list[dict]) -> str:
    """Hash of the ordered prompt hashes. Editing or reordering the items changes it."""
    return sha("|".join(it.get("prompt_sha") or "" for it in items))


def check(man: dict) -> list[str]:
    """Problems that make collecting unsafe (empty list = fine). Never raises."""
    items = man.get("items")
    if not isinstance(items, list) or not items:
        return ["manifest has no items"]
    problems = []
    if not man.get("job_id"):
        problems.append("manifest has no job_id")
    for i, it in enumerate(items):
        if not it.get("prompt_sha"):
            problems.append(f"item {i} has no prompt_sha")
    if man.get("signature") and man["signature"] != signature(items):
        problems.append("signature does not match: the items were edited or reordered after submission, "
                        "and answers are matched to items by position")
    return problems


def now() -> str:
    """UTC with offset. Local time without an offset stored in a timestamptz column came out hours off."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_path(flow: str, folder: str | os.PathLike = FOLDER) -> Path:
    """A manifest file name that cannot collide.

    Names with one-second resolution collided when one job per item was submitted in a loop: the second
    manifest overwrote the first, and a paid job was left with nothing to collect it. Here the name has
    microseconds and the file is created with O_EXCL, retrying with a suffix.
    """
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{flow or 'batch'}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    n = 1
    while True:
        path = d / (f"{stem}.json" if n == 1 else f"{stem}-{n}.json")
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return path
        except FileExistsError:
            n += 1


def save(man: dict, path: str | os.PathLike) -> Path:
    """Write the manifest atomically (temporary file + rename), so a crash never leaves half a file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def load(path: str | os.PathLike) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def is_pending(man: dict) -> bool:
    return not man.get("closed") and any(not it.get("written") for it in man.get("items", []))


def pending(folder: str | os.PathLike = FOLDER, flow: str | None = None) -> Iterator[tuple[Path, dict]]:
    """Manifests with items still to write, newest first, optionally of one flow.

    An unreadable file is skipped, not raised: one corrupt manifest must not hide hundreds of others.
    The flow comes from the file itself, so a new flow is never invisible to the collector.
    """
    d = Path(folder)
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.json"), reverse=True):
        try:
            man = load(path)
        except (OSError, ValueError):
            continue
        if is_pending(man) and (flow is None or man.get("flow") == flow):
            yield path, man


def flows(folder: str | os.PathLike = FOLDER) -> list[str]:
    """Flows that have at least one pending manifest."""
    return sorted({m["flow"] for _, m in pending(folder) if m.get("flow")})


def summary(man: dict) -> dict[str, Any]:
    items = man.get("items", [])
    return {"flow": man.get("flow"), "model": man.get("model"), "job_id": man.get("job_id"),
            "created_at": man.get("created_at"), "items": len(items),
            "written": sum(1 for it in items if it.get("written")), "attempts": man.get("attempts", 0),
            "closed": man.get("closed")}
