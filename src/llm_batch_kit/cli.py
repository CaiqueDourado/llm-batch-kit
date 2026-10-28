"""Command line.

    llm-batch-kit status                                  # pending manifests, per flow
    llm-batch-kit check                                   # manifests that are unsafe to collect (no network)
    llm-batch-kit collect --handlers myflows:HANDLERS --once
    llm-batch-kit collect --handlers myflows:HANDLERS --loop --interval 900 --health collector.json
    llm-batch-kit ddl postgres                            # the batch_queue table
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import manifest as M
from .batch import collect_pending
from .queue import DDL_POSTGRES, DDL_SQLITE


def _load(spec: str):
    mod, _, attr = spec.partition(":")
    sys.path.insert(0, str(Path.cwd()))
    obj = importlib.import_module(mod)
    return getattr(obj, attr) if attr else obj


def _provider(name: str, keys: bool):
    if name == "gemini":
        from .providers import GeminiBatch
        return GeminiBatch(keys=keys)
    return _load(name)()


def cmd_status(a) -> int:
    rows = [M.summary(m) | {"file": p.name} for p, m in M.pending(a.folder, a.flow)]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("nothing pending")
        return 0
    for r in rows:
        print(f"{r['flow']:<20} {r['written']:>5}/{r['items']:<5} attempts {r['attempts']}  {r['job_id']}  {r['file']}")
    return 0


def cmd_check(a) -> int:
    bad = 0
    for path in sorted(Path(a.folder).glob("*.json")):
        try:
            problems = M.check(M.load(path))
        except (OSError, ValueError) as e:
            problems = [f"unreadable: {e}"]
        if problems:
            bad += 1
            print(f"{path.name}: " + "; ".join(problems))
    print(f"{bad} manifest(s) with problems")
    return 1 if bad else 0


def cmd_collect(a) -> int:
    handlers = _load(a.handlers)
    provider = _provider(a.provider, a.keys)
    on_cost = _load(a.on_cost) if a.on_cost else None

    def cycle() -> dict:
        results = collect_pending(provider, handlers, folder=a.folder, on_cost=on_cost,
                                  max_attempts=a.max_attempts, log=print)
        tot = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "manifests": len(results),
               "waiting": sum(r.waiting for r in results), "written": sum(r.written for r in results),
               "errors": sum(r.errors for r in results), "aborted": sum(r.aborted for r in results)}
        print(json.dumps(tot))
        if a.health:
            Path(a.health).write_text(json.dumps({**tot, "pid": __import__("os").getpid()}), encoding="utf-8")
        return tot

    if a.once:
        tot = cycle()
        return 1 if tot["aborted"] else 0
    while True:
        try:
            cycle()
        except Exception as e:  # noqa: BLE001 - the daemon keeps going; the next cycle retries
            print(f"cycle failed: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(a.interval)


def cmd_ddl(a) -> int:
    print((DDL_SQLITE if a.dialect == "sqlite" else DDL_POSTGRES).strip())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="llm-batch-kit", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default=M.FOLDER, help="manifest folder (default: batches)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="pending manifests")
    s.add_argument("--flow")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("check", help="manifests unsafe to collect")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("collect", help="collect finished jobs")
    s.add_argument("--handlers", required=True, help="module:dict mapping flow -> on_item(item, text, resp)")
    s.add_argument("--provider", default="gemini", help="gemini, or module:Class")
    s.add_argument("--keys", action="store_true", help="the jobs were submitted with keys=True")
    s.add_argument("--on-cost", help="module:callable(resp, item), e.g. a ledger_hook")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    s.add_argument("--interval", type=int, default=900, help="seconds between cycles (jobs take 15-40 min)")
    s.add_argument("--max-attempts", type=int, default=3)
    s.add_argument("--health", help="file rewritten after every cycle (for a status page or a monitor)")
    s.set_defaults(fn=cmd_collect)
    s = sub.add_parser("ddl", help="print the batch_queue table")
    s.add_argument("dialect", choices=["postgres", "sqlite"], nargs="?", default="postgres")
    s.set_defaults(fn=cmd_ddl)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
