import base64
import json
import random
from types import SimpleNamespace

import pytest

from llm_batch_kit import (FakeBatch, GeminiBatch, check_config, collect, collect_pending, image, images_of,
                           manifest as M, parts, submit, text, text_of)
from llm_batch_kit.providers import Answer, status_of


def tasks(n=3):
    return [(f"describe product {i}", {"product_id": i}) for i in range(n)]


class Sink:
    def __init__(self, fail_on=()):
        self.rows, self.fail_on = [], set(fail_on)

    def __call__(self, item, txt, resp):
        if item["product_id"] in self.fail_on:
            raise RuntimeError("db down")
        self.rows.append((item["product_id"], txt))
        return True


# ---------- parts ----------

def test_prompt_sha_is_stable_and_order_sensitive():
    a, b = image(b"\x89PNG one", "image/png"), image(b"\x89PNG two", "image/png")
    assert parts.prompt_sha([text("x"), a, b]) == parts.prompt_sha([text("x"), a, b])
    assert parts.prompt_sha([text("x"), a, b]) != parts.prompt_sha([text("x"), b, a])


def test_image_wants_bytes():
    with pytest.raises(TypeError):
        image("photo.jpg")


def test_text_of_does_not_trust_resp_text():
    class ImageOnly:
        @property
        def text(self):
            raise ValueError("no text parts")
        candidates = [SimpleNamespace(content=SimpleNamespace(parts=[
            SimpleNamespace(text=None, inline_data=SimpleNamespace(mime_type="image/png",
                                                                   data=base64.b64encode(b"img").decode()))]))]
    r = ImageOnly()
    assert text_of(r) == ""
    assert images_of(r) == [("image/png", b"img")]


# ---------- manifest ----------

def test_manifest_names_never_collide(tmp_path):
    paths = {M.new_path("img", tmp_path) for _ in range(200)}
    assert len(paths) == 200


def test_pending_skips_corrupt_files(tmp_path):
    fake = FakeBatch()
    submit("a", "m", tasks(), fake, folder=tmp_path)
    (tmp_path / "a_broken.json").write_text("{not json", encoding="utf-8")
    assert [m["flow"] for _, m in M.pending(tmp_path)] == ["a"]
    assert M.flows(tmp_path) == ["a"]


# ---------- config ----------

@pytest.mark.parametrize("bad", [{"thinkingConfig": {"thinkingBudget": 0}}, {"thinking_config": object()}, "x"])
def test_config_must_be_plain_snake_case(bad):
    with pytest.raises((TypeError, ValueError)):
        check_config(bad)


def test_status_names():
    assert status_of("JOB_STATE_SUCCEEDED").ok
    assert status_of("JOB_STATE_PARTIALLY_SUCCEEDED").ok
    assert status_of("JOB_STATE_FAILED").final and not status_of("JOB_STATE_FAILED").ok
    assert not status_of("JOB_STATE_RUNNING").final


# ---------- submit / collect ----------

def test_happy_path_and_idempotent(tmp_path):
    fake, sink = FakeBatch(), Sink()
    path, man = submit("spec", "gemini-2.5-flash", tasks(), fake, folder=tmp_path)
    assert man["items"][0]["product_id"] == 0 and man["items"][0]["written"] is False
    assert collect(path, fake, sink).waiting and sink.rows == []
    fake.finish()
    r = collect(path, fake, sink)
    assert (r.written, r.errors, r.aborted) == (3, 0, False)
    assert sink.rows[1] == (1, "echo: describe product 1")
    r2 = collect(path, fake, sink)
    assert (r2.written, r2.skipped) == (0, 3) and len(sink.rows) == 3
    assert not list(M.pending(tmp_path))


def test_failed_job_writes_nothing_and_closes(tmp_path):
    fake, sink = FakeBatch(), Sink()
    path, _ = submit("spec", "m", tasks(), fake, folder=tmp_path)
    fake.finish(state="JOB_STATE_EXPIRED")
    r = collect(path, fake, sink)
    assert r.aborted and sink.rows == []
    assert M.load(path)["closed"] == "JOB_STATE_EXPIRED"


def test_count_mismatch_writes_nothing(tmp_path):
    fake, sink = FakeBatch(), Sink()
    path, _ = submit("spec", "m", tasks(4), fake, folder=tmp_path)
    fake.finish()
    job = next(iter(fake.jobs.values()))
    job.answers.pop(1)
    r = collect(path, fake, sink)
    assert r.aborted and "matched by position" in r.reason and sink.rows == []


def test_edited_manifest_is_refused(tmp_path):
    fake, sink = FakeBatch(), Sink()
    path, _ = submit("spec", "m", tasks(), fake, folder=tmp_path)
    fake.finish()
    man = M.load(path)
    man["items"].reverse()
    M.save(man, path)
    r = collect(path, fake, sink)
    assert r.aborted and "signature" in r.reason and sink.rows == []


def test_echoed_keys_survive_reordered_answers(tmp_path):
    fake, sink = FakeBatch(echo_keys=True), Sink()
    path, _ = submit("spec", "m", tasks(6), fake, folder=tmp_path)
    fake.finish()
    random.Random(3).shuffle(next(iter(fake.jobs.values())).answers)
    r = collect(path, fake, sink)
    assert r.written == 6 and M.load(path)["bound_by"] == "key"
    assert all(txt == f"echo: describe product {pid}" for pid, txt in sink.rows)


def test_empty_answer_and_answer_error_are_errors(tmp_path):
    replies = iter([SimpleNamespace(text="ok", candidates=[]), SimpleNamespace(text="", candidates=[]),
                    SimpleNamespace(text="ok", candidates=[])])
    fake, sink = FakeBatch(reply=lambda req: next(replies)), Sink()
    path, _ = submit("spec", "m", tasks(), fake, folder=tmp_path)
    fake.finish()
    next(iter(fake.jobs.values())).answers[2] = Answer(error={"code": 400, "message": "blocked"})
    r = collect(path, fake, sink)
    assert (r.written, r.errors) == (1, 2)
    items = M.load(path)["items"]
    assert items[1]["error"] == "answer with no text and no image" and "blocked" in items[2]["error"]


def test_failed_writes_are_retried_alone(tmp_path):
    fake = FakeBatch()
    path, _ = submit("spec", "m", tasks(), fake, folder=tmp_path)
    fake.finish()
    first = Sink(fail_on={1})
    r = collect(path, fake, first)
    assert (r.written, r.errors) == (2, 1)
    second = Sink()
    r = collect(path, fake, second)
    assert (r.written, r.skipped) == (1, 2) and second.rows == [(1, "echo: describe product 1")]


def test_crash_halfway_resumes_without_duplicates(tmp_path):
    fake = FakeBatch()
    path, _ = submit("spec", "m", tasks(5), fake, folder=tmp_path)
    fake.finish()
    written = []

    def dies_on_third(item, txt, resp):
        if len(written) == 2:
            raise KeyboardInterrupt  # the process is killed: not an ordinary exception
        written.append(item["product_id"])
        return True

    with pytest.raises(KeyboardInterrupt):
        collect(path, fake, dies_on_third)
    assert sum(it["written"] for it in M.load(path)["items"]) == 2
    rest = Sink()
    collect(path, fake, rest)
    assert [pid for pid, _ in rest.rows] == [2, 3, 4]


def test_cost_failure_does_not_block_writing(tmp_path):
    fake, sink = FakeBatch(), Sink()
    path, _ = submit("spec", "m", tasks(), fake, folder=tmp_path)
    fake.finish()

    def broken_cost(resp, item):
        raise RuntimeError("ledger down")

    r = collect(path, fake, sink, on_cost=broken_cost)
    assert r.written == 3 and any("cost not recorded" in m for m in r.messages)


def test_failed_submission_leaves_no_manifest(tmp_path):
    class Down(FakeBatch):
        def submit(self, *a, **k):
            raise ConnectionError("503")

    with pytest.raises(ConnectionError):
        submit("spec", "m", tasks(), Down(), folder=tmp_path)
    assert list(tmp_path.glob("*.json")) == []


def test_pairs_only():
    with pytest.raises(TypeError):
        submit("spec", "m", [("prompt", 42)], FakeBatch())
    with pytest.raises(ValueError):
        submit("spec", "m", [], FakeBatch())


def test_collect_pending_gives_up_after_max_attempts(tmp_path):
    fake = FakeBatch()
    path, _ = submit("spec", "m", tasks(2), fake, folder=tmp_path)
    submit("other", "m", tasks(1), fake, folder=tmp_path)
    fake.finish()
    for _ in range(3):
        collect_pending(fake, {"spec": Sink(fail_on={0}), "other": Sink()}, folder=tmp_path, max_attempts=3)
    man = M.load(path)
    assert man["closed"].startswith("gave up") and [it["written"] for it in man["items"]] == [False, True]
    assert not list(M.pending(tmp_path))


# ---------- Gemini adapter, with a stand-in client ----------

class FakeClient:
    def __init__(self):
        self.created = None
        self.batches = self

    def create(self, model, src, config=None):
        self.created = {"model": model, "src": src, "config": config}
        return SimpleNamespace(name="batches/123")

    def get(self, name):
        resp = [SimpleNamespace(response=SimpleNamespace(text=f"answer {i}", candidates=[]),
                                metadata=None, error=None) for i in range(len(self.created["src"]))]
        return SimpleNamespace(state=SimpleNamespace(name="JOB_STATE_SUCCEEDED"),
                               dest=SimpleNamespace(inlined_responses=resp))


def test_gemini_request_shape(tmp_path):
    cli = FakeClient()
    g = GeminiBatch(client=cli)
    path, man = submit("spec", "gemini-2.5-flash", tasks(2), g, folder=tmp_path,
                       config={"thinking_config": {"thinking_budget": 0}})
    src = cli.created["src"]
    assert src[0] == {"contents": [{"role": "user", "parts": [{"text": "describe product 0"}]}],
                      "config": {"thinking_config": {"thinking_budget": 0}}}
    assert man["job_id"] == "batches/123" and cli.created["config"]["display_name"] == path.stem
    sink = Sink()
    assert collect(path, g, sink).written == 2 and sink.rows[1] == (1, "answer 1")
    GeminiBatch(client=cli, keys=True).submit("m", [{"parts": [text("x")], "key": "0:abc"}], None, "n")
    assert cli.created["src"][0]["metadata"] == {"key": "0:abc"}


def test_manifest_is_readable_json(tmp_path):
    path, _ = submit("spec", "m", [([text("look"), image(b"\xff\xd8 jpeg")], {"id": 1})], FakeBatch(),
                     folder=tmp_path)
    man = json.loads(path.read_text(encoding="utf-8"))
    assert "inline_data" not in json.dumps(man)  # images are hashed, not stored


def test_requests_validate_against_the_sdk_types():
    types = pytest.importorskip("google.genai.types")
    cli = FakeClient()
    GeminiBatch(client=cli, keys=True).submit(
        "gemini-2.5-flash", [{"parts": [text("look"), image(b"\xff\xd8 jpeg")], "key": "0:abc"}],
        {"thinking_config": {"thinking_budget": 0}, "temperature": 0.2}, "n")
    req = types.InlinedRequest.model_validate(cli.created["src"][0])
    assert req.config.thinking_config.thinking_budget == 0 and req.metadata == {"key": "0:abc"}
    assert req.contents[0].parts[1].inline_data.mime_type == "image/jpeg"
    types.CreateBatchJobConfig.model_validate(cli.created["config"])
