"""Provider adapters. A provider submits a list of requests as one job, reports the job's state and returns
the answers in the order of the requests.

``GeminiBatch`` wraps the Gemini Batch API (google-genai). ``FakeBatch`` keeps jobs in memory, for tests
and dry runs; it can also misbehave on purpose (reorder, drop or fail answers).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Protocol

FINAL = ("SUCCEEDED", "PARTIALLY_SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED")
OK = ("SUCCEEDED", "PARTIALLY_SUCCEEDED")


@dataclass
class Status:
    state: str          # provider's name, e.g. JOB_STATE_RUNNING
    final: bool
    ok: bool            # final and with answers to read


@dataclass
class Answer:
    response: Any = None                  # the provider's response object for one request
    metadata: dict | None = None          # echoed request metadata, when the provider sends it back
    error: Any = None                     # per-request error, when the provider reports one


def status_of(state: str) -> Status:
    s = str(state or "")
    tail = s.removeprefix("JOB_STATE_").removeprefix("BATCH_STATE_")
    final = tail in FINAL
    return Status(state=s, final=final, ok=final and tail in OK)


class Provider(Protocol):
    name: str

    def submit(self, model: str, requests: list[dict], config: dict | None, display_name: str) -> str: ...
    def status(self, job_id: str) -> Status: ...
    def answers(self, job_id: str) -> list[Answer]: ...


def check_config(config: Any, where: str = "config") -> dict:
    """Batch requests take plain dicts with snake_case keys. An SDK object (``types.ThinkingConfig``) or a
    camelCase key is refused here, before paying for a job that fails."""
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise TypeError(f"{where} must be a plain dict with snake_case keys, not {type(config).__name__}: "
                        "write {'thinking_config': {'thinking_budget': 0}}")
    for k, v in config.items():
        if any(c.isupper() for c in k):
            raise ValueError(f"{where} key {k!r} is camelCase: batch requests take snake_case")
        if isinstance(v, dict):
            check_config(v, f"{where}.{k}")
        elif not isinstance(v, (str, int, float, bool, list, type(None))):
            raise TypeError(f"{where}.{k} is a {type(v).__name__}: use plain values, not SDK objects")
    return config


class GeminiBatch:
    """Gemini Batch API with inline requests (google-genai). Half the price of the same calls made one by one.

    Inline requests have no id that comes back with the answer: answers are matched by position. With
    ``keys=True`` each request also carries ``metadata={"key": ...}`` (a field of the SDK's
    ``InlinedRequest``); if the answers echo it, the collector matches by key and order stops mattering.
    """

    name = "gemini"

    def __init__(self, client: Any = None, api_key: str | None = None, keys: bool = False) -> None:
        if client is None:
            from google import genai  # optional dependency: pip install "llm-batch-kit[gemini]"
            client = genai.Client(api_key=api_key)
        self.client = client
        self.keys = keys

    def submit(self, model: str, requests: list[dict], config: dict | None, display_name: str) -> str:
        cfg = check_config(config)
        src = []
        for r in requests:
            req = {"contents": [{"role": "user", "parts": r["parts"]}]}
            if cfg:
                req["config"] = cfg
            if self.keys:
                req["metadata"] = {"key": r["key"]}
            src.append(req)
        job = self.client.batches.create(model=model, src=src, config={"display_name": display_name})
        return getattr(job, "name", None) or str(job)

    def status(self, job_id: str) -> Status:
        st = getattr(self.client.batches.get(name=job_id), "state", None)
        return status_of(getattr(st, "name", st))

    def answers(self, job_id: str) -> list[Answer]:
        job = self.client.batches.get(name=job_id)
        out = []
        for r in getattr(getattr(job, "dest", None), "inlined_responses", None) or []:
            out.append(Answer(response=getattr(r, "response", None), metadata=getattr(r, "metadata", None),
                              error=getattr(r, "error", None)))
        return out


@dataclass
class _Job:
    model: str
    requests: list[dict]
    state: str = "JOB_STATE_RUNNING"
    answers: list[Answer] = field(default_factory=list)


class FakeBatch:
    """In-memory provider. ``reply(request) -> response`` builds each answer (default: echoes the text).

    >>> fake = FakeBatch()
    >>> # submit through llm_batch_kit.submit(..., provider=fake), then:
    >>> fake.finish()            # every job SUCCEEDED
    """

    name = "fake"

    def __init__(self, reply=None, echo_keys: bool = False) -> None:
        self.jobs: dict[str, _Job] = {}
        self.reply = reply or (lambda req: _FakeResponse("echo: " + " ".join(
            p.get("text", "") for p in req["parts"])))
        self.echo_keys = echo_keys
        self._ids = itertools.count(1)

    def submit(self, model: str, requests: list[dict], config: dict | None, display_name: str) -> str:
        check_config(config)
        job_id = f"batches/fake-{next(self._ids)}"
        self.jobs[job_id] = _Job(model, requests)
        return job_id

    def finish(self, job_id: str | None = None, state: str = "JOB_STATE_SUCCEEDED") -> None:
        for jid, job in self.jobs.items():
            if job_id in (None, jid):
                job.state = state
                job.answers = [Answer(response=self.reply(r),
                                      metadata={"key": r["key"]} if self.echo_keys else None)
                               for r in job.requests]

    def status(self, job_id: str) -> Status:
        return status_of(self.jobs[job_id].state)

    def answers(self, job_id: str) -> list[Answer]:
        return list(self.jobs[job_id].answers)


@dataclass
class _FakeResponse:
    text: str
    usage_metadata: Any = None
    candidates: list = field(default_factory=list)
