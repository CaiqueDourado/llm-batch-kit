"""Intent queue: a click records "process record 42 in flow X" and returns at once; a collector daemon
turns queued rows into one batch job and marks them collected later.

The partial unique index is the important part. A record can be in flight only once per flow, so a second
click, a retry or a second admin tab cannot pay for the same work twice; once collected (or failed) it can
be queued again.

Works with any DB-API connection: Postgres (psycopg) in production, SQLite for tests and small setups.
"""

from __future__ import annotations

from typing import Any, Iterable

STATUSES = ("queued", "submitted", "collected", "error")
IN_FLIGHT = ("queued", "submitted")

DDL_POSTGRES = """
create table if not exists batch_queue (
    id            bigint generated always as identity primary key,
    flow          text not null,
    table_name    text not null,
    record_id     text not null,
    args          text,
    requested_by  text,
    status        text not null default 'queued'
                  check (status in ('queued', 'submitted', 'collected', 'error')),
    manifest      text,
    error         text,
    created_at    timestamptz not null default now(),
    submitted_at  timestamptz,
    collected_at  timestamptz
);
create unique index if not exists batch_queue_in_flight
    on batch_queue (flow, table_name, record_id) where status in ('queued', 'submitted');
create index if not exists batch_queue_by_record on batch_queue (table_name, record_id, status);
create index if not exists batch_queue_by_flow on batch_queue (flow, status, created_at);
"""

DDL_SQLITE = """
create table if not exists batch_queue (
    id            integer primary key autoincrement,
    flow          text not null,
    table_name    text not null,
    record_id     text not null,
    args          text,
    requested_by  text,
    status        text not null default 'queued'
                  check (status in ('queued', 'submitted', 'collected', 'error')),
    manifest      text,
    error         text,
    created_at    text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    submitted_at  text,
    collected_at  text
);
create unique index if not exists batch_queue_in_flight
    on batch_queue (flow, table_name, record_id) where status in ('queued', 'submitted');
create index if not exists batch_queue_by_record on batch_queue (table_name, record_id, status);
create index if not exists batch_queue_by_flow on batch_queue (flow, status, created_at);
"""


class Queue:
    """>>> q = Queue(psycopg.connect(dsn)); q.create()
    >>> q.enqueue("spec_sheet", "products", 42)      # id, or None when 42 is already in flight
    """

    def __init__(self, conn: Any, dialect: str | None = None) -> None:
        self.conn = conn
        mod = type(conn).__module__
        self.dialect = dialect or ("sqlite" if mod.startswith("sqlite3") else "postgres")
        self.p = "?" if self.dialect == "sqlite" else "%s"
        self.now = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')" if self.dialect == "sqlite" else "now()"

    def _q(self, sql: str) -> str:
        return sql.replace("%s", self.p) if self.p != "%s" else sql

    def create(self) -> None:
        cur = self.conn.cursor()
        ddl = DDL_SQLITE if self.dialect == "sqlite" else DDL_POSTGRES
        if self.dialect == "sqlite":
            self.conn.executescript(ddl)
        else:
            cur.execute(ddl)
        self.conn.commit()

    def enqueue(self, flow: str, table: str, record_id: Any, args: str | None = None,
                requested_by: str | None = None) -> int | None:
        """Queue one record. Returns the row id, or None when the record is already in flight in this flow."""
        cur = self.conn.cursor()
        cur.execute(self._q(
            "insert into batch_queue (flow, table_name, record_id, args, requested_by) values (%s, %s, %s, %s, %s) "
            "on conflict (flow, table_name, record_id) where status in ('queued', 'submitted') do nothing "
            "returning id"), (flow, table, str(record_id), args, requested_by))
        row = cur.fetchone()
        self.conn.commit()
        return row[0] if row else None

    def in_flight(self, table: str, record_ids: Iterable[Any]) -> dict[str, list[str]]:
        """``{record_id: [flows in flight]}`` for a page of records (what a grid shows next to each row)."""
        ids = [str(r) for r in record_ids]
        if not ids:
            return {}
        marks = ", ".join([self.p] * len(ids))
        cur = self.conn.cursor()
        cur.execute(f"select record_id, flow from batch_queue where table_name = {self.p} and record_id in ({marks}) "
                    f"and status in ('queued', 'submitted') order by record_id, flow", (table, *ids))
        out: dict[str, list[str]] = {}
        for rid, flow in cur.fetchall():
            out.setdefault(rid, []).append(flow)
        return out

    def take(self, flow: str, limit: int = 1000, *, single: bool = False) -> list[dict]:
        """Queued rows of a flow, oldest first, locked for this transaction on Postgres (SKIP LOCKED, so two
        collectors never take the same rows). ``single=True`` returns rows with their own ``args``: those
        are submitted alone; rows without args go together in one job."""
        cond = "args is not null" if single else "args is null"
        lock = "" if self.dialect == "sqlite" else " for update skip locked"
        cur = self.conn.cursor()
        cur.execute(f"select id, table_name, record_id, args from batch_queue where flow = {self.p} and "
                    f"status = 'queued' and {cond} order by created_at, id limit {int(limit)}{lock}", (flow,))
        return [dict(zip(("id", "table_name", "record_id", "args"), r)) for r in cur.fetchall()]

    def mark_submitted(self, ids: Iterable[int], manifest: str) -> None:
        self._mark(ids, f"status = 'submitted', manifest = {self.p}, submitted_at = {self.now}", (manifest,))

    def mark_collected(self, ids: Iterable[int]) -> None:
        self._mark(ids, f"status = 'collected', collected_at = {self.now}", ())

    def mark_error(self, ids: Iterable[int], error: str) -> None:
        self._mark(ids, f"status = 'error', error = {self.p}, collected_at = {self.now}", (error[:2000],))

    def _mark(self, ids: Iterable[int], assign: str, params: tuple) -> None:
        ids = list(ids)
        if not ids:
            self.conn.commit()
            return
        marks = ", ".join([self.p] * len(ids))
        cur = self.conn.cursor()
        cur.execute(f"update batch_queue set {assign} where id in ({marks})", (*params, *ids))
        self.conn.commit()

    def by_manifest(self, manifest: str) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(f"select id, table_name, record_id, status from batch_queue where manifest = {self.p}", (manifest,))
        return [dict(zip(("id", "table_name", "record_id", "status"), r)) for r in cur.fetchall()]

    def counts(self) -> dict[tuple[str, str], int]:
        cur = self.conn.cursor()
        cur.execute("select flow, status, count(*) from batch_queue group by flow, status")
        return {(f, s): n for f, s, n in cur.fetchall()}


def submit_queued(queue: Queue, flow: str, build, model: str, provider: Any, *, config: dict | None = None,
                  folder: str = "batches", limit: int = 1000) -> list[str]:
    """Turn the queued rows of a flow into batch jobs and mark them submitted.

    ``build(row) -> (prompt, item)`` makes the request of one queued row (``row`` has id, table_name,
    record_id, args). Rows without ``args`` go in one job; each row with its own ``args`` goes alone.
    Returns the manifest paths. If the submission fails, the rows stay queued.
    """
    from .batch import submit

    paths = []
    groups = []
    rows = queue.take(flow, limit)
    if rows:
        groups.append(rows)
    groups += [[r] for r in queue.take(flow, limit, single=True)]
    for rows in groups:
        tasks = []
        for r in rows:
            prompt, item = build(r)
            tasks.append((prompt, {**(item or {}), "queue_id": r["id"], "record_id": r["record_id"]}))
        path, _ = submit(flow, model, tasks, provider, config=config, folder=folder)
        queue.mark_submitted([r["id"] for r in rows], str(path))
        paths.append(str(path))
    return paths


def close_collected(queue: Queue, path: str) -> tuple[int, int]:
    """After a collection, mark the queue rows of a manifest: written items become ``collected``; if the
    manifest is closed (failed job, or gave up), the rest become ``error`` so they can be queued again.
    Returns ``(collected, errors)``."""
    from . import manifest as M

    man = M.load(path)
    done = [it["queue_id"] for it in man["items"] if it.get("written") and "queue_id" in it]
    queue.mark_collected(done)
    failed = []
    if man.get("closed"):
        failed = [it["queue_id"] for it in man["items"] if not it.get("written") and "queue_id" in it]
        queue.mark_error(failed, str(man["closed"]))
    return len(done), len(failed)
