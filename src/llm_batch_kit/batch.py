"""Submit now, collect later.

``submit`` takes ONE list of ``(prompt, item)`` pairs. Two parallel lists (prompts here, records there) can
drift apart, and with answers matched by position a drift writes one product's text on another, silently.
With pairs there is nothing to drift.

``collect`` hands each answer to your ``on_item(item, text, response) -> bool`` and never writes anything
itself. It is idempotent: a collected item is marked ``written`` and skipped next time.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import manifest as M
from .parts import images_of, parts_of, prompt_sha, text_of
from .providers import Answer, Provider

OnItem = Callable[[dict, str, Any], bool]
OnCost = Callable[[Any, dict], None]


@dataclass
class Result:
    path: str
    state: str | None = None
    written: int = 0
    skipped: int = 0          # already written by an earlier collection
    errors: int = 0
    aborted: bool = False
    reason: str = ""
    waiting: bool = False     # job not finished: nothing read, nothing written
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def submit(flow: str, model: str, tasks: Iterable[tuple[Any, dict]], provider: Provider, *,
           config: dict | None = None, folder: str | os.PathLike = M.FOLDER, extra: dict | None = None) -> tuple[Path, dict]:
    """Submit one job with one request per task and write its manifest. Returns ``(path, manifest)``.

    ``tasks``: pairs ``(prompt, item)``. ``prompt`` is a string or a list of parts (``parts.text``,
    ``parts.image``); ``item`` is your dict (ids, anything) and comes back whole in ``on_item``.
    ``config``: plain dict, snake_case (``{"thinking_config": {"thinking_budget": 0}}``).
    """
    tasks = list(tasks)
    if not tasks:
        raise ValueError("nothing to submit")
    items, requests = [], []
    for i, (prompt, item) in enumerate(tasks):
        if item is not None and not isinstance(item, dict):
            raise TypeError("each task is (prompt, item) with item a dict")
        ps = prompt_sha(prompt)
        key = f"{i}:{ps}"
        items.append({**(item or {}), "prompt_sha": ps, "key": key, "written": False})
        requests.append({"parts": parts_of(prompt), "key": key})
    path = M.new_path(flow, folder)
    try:
        job_id = provider.submit(model, requests, config, display_name=path.stem)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    man = {"flow": flow, "provider": getattr(provider, "name", None), "model": model, "job_id": job_id,
           "created_at": M.now(), "config": config or {}, "items": items,
           "signature": M.signature(items), "attempts": 0, **(extra or {})}
    M.save(man, path)
    return path, man


def _bind(man: dict, answers: list[Answer]) -> tuple[list[tuple[dict, Answer]] | None, str]:
    """Pair items with answers: by echoed key when every answer has one, else by position."""
    items = man["items"]
    keys = [(a.metadata or {}).get("key") for a in answers]
    if answers and all(keys):
        by_key = {k: a for k, a in zip(keys, answers)}
        if len(by_key) != len(answers):
            return None, "repeated keys in the answers"
        missing = [it["key"] for it in items if it["key"] not in by_key]
        if missing:
            return None, f"{len(missing)} item(s) without an answer (first: {missing[0]})"
        return [(it, by_key[it["key"]]) for it in items], "key"
    if len(answers) != len(items):
        return None, (f"{len(answers)} answer(s) for {len(items)} item(s): answers are matched by position, "
                      "so nothing was written")
    return list(zip(items, answers)), "position"


def collect(path: str | os.PathLike, provider: Provider, on_item: OnItem, *, on_cost: OnCost | None = None,
            log: Callable[[str], None] | None = None) -> Result:
    """Read a finished job and hand each answer to ``on_item``. Never raises.

    - job still running: nothing is read (``waiting=True``);
    - job failed, manifest edited, or answer count different from item count: nothing is written;
    - an answer with neither text nor image, or with an error, is an error, not empty content;
    - ``on_item`` returning True marks the item written; the manifest is saved after every item;
    - ``on_cost(response, item)`` runs before ``on_item``; if it fails, the item is still written.
    """
    say = log or (lambda _m: None)
    res = Result(path=str(path))

    def note(msg: str) -> None:
        res.messages.append(msg)
        say(msg)

    try:
        man = M.load(path)
    except (OSError, ValueError) as e:
        res.aborted, res.reason = True, f"manifest unreadable: {e}"
        return res
    problems = M.check(man)
    if problems:
        res.aborted, res.reason = True, "; ".join(problems)
        note(f"[{man.get('flow')}] manifest refused, nothing written: {res.reason}")
        return res
    try:
        st = provider.status(man["job_id"])
    except Exception as e:  # noqa: BLE001
        res.aborted, res.reason = True, f"status query failed: {str(e)[:200]}"
        return res
    res.state = st.state
    if not st.final:
        res.waiting = True
        return res
    man["attempts"] = int(man.get("attempts", 0)) + 1
    if not st.ok:
        res.aborted, res.reason = True, f"job ended {st.state}"
        man["closed"] = st.state
        M.save(man, path)
        note(f"[{man.get('flow')}] {res.reason}: nothing written")
        return res
    try:
        answers = provider.answers(man["job_id"])
    except Exception as e:  # noqa: BLE001
        res.aborted, res.reason = True, f"reading answers failed: {str(e)[:200]}"
        M.save(man, path)
        return res
    pairs, how = _bind(man, answers)
    if pairs is None:
        res.aborted, res.reason = True, how
        M.save(man, path)
        note(f"[{man.get('flow')}] {how}")
        return res
    man["bound_by"] = how

    for item, ans in pairs:
        if item.get("written"):
            res.skipped += 1
            continue
        if ans.error:
            res.errors += 1
            item["error"] = str(ans.error)[:300]
            continue
        resp = ans.response
        try:
            text = text_of(resp)
            has_image = bool(images_of(resp))
        except Exception as e:  # noqa: BLE001
            res.errors += 1
            item["error"] = f"unreadable answer: {str(e)[:200]}"
            continue
        if not text and not has_image:
            res.errors += 1
            item["error"] = "answer with no text and no image"
            continue
        if on_cost:
            try:
                on_cost(resp, item)
            except Exception as e:  # noqa: BLE001 - a lost cost row is lost information, not lost content
                note(f"cost not recorded: {str(e)[:150]}")
        try:
            ok = bool(on_item(item, text, resp))
        except Exception as e:  # noqa: BLE001
            ok = False
            res.errors += 1
            item["error"] = f"on_item failed: {str(e)[:200]}"
        if ok:
            item["written"] = True
            item.pop("error", None)
            item["written_at"] = M.now()
            res.written += 1
        M.save(man, path)   # after every item: a crash halfway resumes, it does not write twice
    M.save(man, path)
    return res


def collect_pending(provider: Provider, handlers: dict[str, OnItem], *, folder: str | os.PathLike = M.FOLDER,
                    on_cost: OnCost | None = None, max_attempts: int = 3,
                    log: Callable[[str], None] | None = None) -> list[Result]:
    """Collect every pending manifest whose flow has a handler. A finished job that keeps failing to write is
    closed after ``max_attempts`` collections, so it does not come back forever."""
    say = log or (lambda _m: None)
    out = []
    for path, man in M.pending(folder):
        flow = man.get("flow")
        if flow not in handlers:
            say(f"[{flow}] no handler: skipped {path.name}")
            continue
        if man.get("provider") not in (None, getattr(provider, "name", None)):
            continue
        res = collect(path, provider, handlers[flow], on_cost=on_cost, log=log)
        if not res.waiting:
            m = M.load(path)
            if M.is_pending(m) and int(m.get("attempts", 0)) >= max_attempts:
                m["closed"] = f"gave up after {m['attempts']} collections"
                M.save(m, path)
                say(f"[{flow}] {path.name}: {m['closed']}")
        out.append(res)
    return out
