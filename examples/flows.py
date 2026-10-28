"""Handlers for the collector daemon: one on_item per flow.

    llm-batch-kit collect --handlers examples.flows:HANDLERS --loop --health collector.json
"""

import sqlite3

db = sqlite3.connect("example.sqlite")


def store_summary(item, text, resp):
    db.execute("insert or replace into summary values (?, ?)", (item["product_id"], text))
    db.commit()
    return True


HANDLERS = {"summary": store_summary}
