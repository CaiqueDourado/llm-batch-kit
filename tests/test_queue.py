import os
import sqlite3

import pytest

from llm_batch_kit import FakeBatch, Queue, close_collected, collect, submit_queued


def queues():
    yield "sqlite", lambda: sqlite3.connect(":memory:")
    dsn = os.environ.get("LLM_BATCH_KIT_PG")
    if dsn:
        import psycopg

        def pg():
            c = psycopg.connect(dsn)
            c.execute("drop table if exists batch_queue")
            c.commit()
            return c
        yield "postgres", pg


@pytest.fixture(params=list(queues()), ids=lambda p: p[0])
def q(request):
    queue = Queue(request.param[1]())
    queue.create()
    return queue


def test_a_record_is_in_flight_once(q):
    assert q.enqueue("spec", "products", 42, requested_by="grid") is not None
    assert q.enqueue("spec", "products", 42, requested_by="second tab") is None
    assert q.enqueue("video", "products", 42) is not None         # another flow is other work
    assert q.in_flight("products", [42, 43]) == {"42": ["spec", "video"]}


def test_requeue_after_collection(q):
    rid = q.enqueue("spec", "products", 7)
    q.mark_submitted([rid], "batches/x.json")
    assert q.enqueue("spec", "products", 7) is None                # submitted is still in flight
    q.mark_collected([rid])
    assert q.enqueue("spec", "products", 7) is not None


def test_queue_to_batch_and_back(q, tmp_path):
    for pid in (1, 2, 3):
        q.enqueue("spec", "products", pid)
    q.enqueue("spec", "products", 9, args="--orientation wide")
    fake = FakeBatch()
    paths = submit_queued(q, "spec", lambda r: (f"write about {r['record_id']} {r['args'] or ''}".strip(),
                                                {"product_id": int(r["record_id"])}),
                          "gemini-2.5-flash", fake, folder=str(tmp_path))
    assert len(paths) == 2                                          # 3 together, the one with args alone
    assert q.counts()[("spec", "submitted")] == 4
    fake.finish()
    stored = {}
    for p in paths:
        collect(p, fake, lambda item, text, resp: stored.setdefault(item["product_id"], text) is not None)
        close_collected(q, p)
    assert stored[9] == "echo: write about 9 --orientation wide"
    assert q.counts() == {("spec", "collected"): 4}


def test_failed_job_frees_the_records(q, tmp_path):
    rid = q.enqueue("spec", "products", 5)
    fake = FakeBatch()
    (path,) = submit_queued(q, "spec", lambda r: ("x", {}), "m", fake, folder=str(tmp_path))
    fake.finish(state="JOB_STATE_FAILED")
    collect(path, fake, lambda *a: True)
    assert close_collected(q, path) == (0, 1)
    assert q.enqueue("spec", "products", 5) is not None and rid is not None


@pytest.mark.skipif(not os.environ.get("LLM_BATCH_KIT_PG"), reason="needs Postgres (LLM_BATCH_KIT_PG)")
def test_two_collectors_never_take_the_same_rows():
    import psycopg
    dsn = os.environ["LLM_BATCH_KIT_PG"]
    a = Queue(psycopg.connect(dsn))
    a.conn.execute("drop table if exists batch_queue")
    a.conn.commit()
    a.create()
    for pid in range(10):
        a.enqueue("spec", "products", pid)
    b = Queue(psycopg.connect(dsn))
    first = a.take("spec", limit=6)          # transaction open: rows locked
    second = b.take("spec", limit=6)         # SKIP LOCKED: gets the other four
    assert len(first) == 6 and len(second) == 4
    assert not {r["id"] for r in first} & {r["id"] for r in second}
    a.conn.rollback()
    b.conn.rollback()
