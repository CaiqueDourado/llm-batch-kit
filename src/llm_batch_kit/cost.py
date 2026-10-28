"""Cost of batch answers, recorded with llm-cost-kit (or anything with the same ``record`` method)."""

from __future__ import annotations

from typing import Any, Callable


def ledger_hook(ledger: Any, model: str, flow: str, meta_keys: tuple[str, ...] = ()) -> Callable[[Any, dict], None]:
    """``on_cost`` for :func:`llm_batch_kit.collect` that writes one row per answer with ``batch=True``
    (half price). ``meta_keys`` copies fields of the item (ids) into the row.

    >>> from llm_cost_kit import Ledger, PriceTable, SQLiteSink
    >>> ledger = Ledger(PriceTable.load("prices.yaml"), SQLiteSink("costs.sqlite"))
    >>> collect(path, provider, on_item, on_cost=ledger_hook(ledger, "gemini-2.5-flash", "spec_sheet", ("product_id",)))
    """

    def hook(resp: Any, item: dict) -> None:
        meta = {k: item[k] for k in meta_keys if k in item}
        ledger.record(resp, model=model, flow=flow, batch=True, meta=meta)

    return hook
