"""Submit three prompts as one batch job, then collect them.

    python examples/quickstart.py            # offline, with FakeBatch
    GEMINI_API_KEY=... python examples/quickstart.py submit
    GEMINI_API_KEY=... python examples/quickstart.py collect   # 15-40 minutes later
"""

import os
import sqlite3
import sys

from llm_batch_kit import FakeBatch, GeminiBatch, collect, manifest, submit

MODEL = "gemini-2.5-flash"
products = [{"product_id": 1, "name": "Bluetooth headphones"},
            {"product_id": 2, "name": "4-litre air fryer"},
            {"product_id": 3, "name": "electric shower head"}]

db = sqlite3.connect("example.sqlite")
db.execute("create table if not exists summary (product_id integer primary key, text text)")


def store(item, text, resp):
    """Your write. Return True when it is stored: only then the item is marked written."""
    db.execute("insert or replace into summary values (?, ?)", (item["product_id"], text))
    db.commit()
    return True


def run(provider, step):
    if step in ("submit", "all"):
        tasks = [(f"In one sentence, what is {p['name']} for?", {"product_id": p["product_id"]})
                 for p in products]                                  # ONE list of (prompt, item) pairs
        path, man = submit("summary", MODEL, tasks, provider, config={"thinking_config": {"thinking_budget": 0}})
        print("submitted", man["job_id"], "->", path)
    if isinstance(provider, FakeBatch):
        provider.finish()
    if step in ("collect", "all"):
        for path, _ in manifest.pending(flow="summary"):
            r = collect(path, provider, store)
            print(path.name, r.state, f"written={r.written} skipped={r.skipped} errors={r.errors}",
                  "waiting" if r.waiting else r.reason)


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    run(GeminiBatch() if os.environ.get("GEMINI_API_KEY") else FakeBatch(), step)
    print(db.execute("select * from summary").fetchall())
