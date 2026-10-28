from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_batch_kit import FakeBatch, collect, ledger_hook, submit

cost_kit = pytest.importorskip("llm_cost_kit")


def test_batch_answers_are_recorded_at_half_price(tmp_path):
    prices = cost_kit.PriceTable.load(Path(__file__).parent / "prices.json")
    ledger = cost_kit.Ledger(prices, cost_kit.MemorySink())
    usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=500, thoughts_token_count=0,
                            cached_content_token_count=0)
    fake = FakeBatch(reply=lambda req: SimpleNamespace(text="ok", candidates=[], usage_metadata=usage))
    path, _ = submit("spec", "gemini-2.5-flash", [("p", {"product_id": 7})], fake, folder=tmp_path)
    fake.finish()
    collect(path, fake, lambda *a: True, on_cost=ledger_hook(ledger, "gemini-2.5-flash", "spec", ("product_id",)))
    (row,) = ledger.sinks[0].rows
    sync = prices.cost("gemini-2.5-flash", 1000, 500)
    assert row["batch"] is True and row["meta"] == {"product_id": 7}
    assert abs(row["cost_usd"] - sync / 2) < 1e-12
