from app.store.db import db


def get_effective_catalog(data, merchant_id):
    """The dataset's catalog.csv is a fixed snapshot; merchants report real
    stock changes through chat (a sale outside the recorded transactions,
    a restock delivery), and those need to actually change what every
    agent — and the dashboard — sees as "current stock", not just get
    acknowledged in a chat reply. Overrides are persisted per item in the
    db store and merged over the base snapshot here; every catalog read in
    the app should go through this function, never data.catalog_by_merchant
    directly, so nothing (UI, ops agent, festival agent) ever looks at a
    stale number after an update."""
    base = data.catalog_by_merchant.get(merchant_id, [])
    overrides = db.get_stock_overrides(merchant_id)
    if not overrides:
        return [dict(item) for item in base]
    result = []
    for item in base:
        item_copy = dict(item)
        if item["item_id"] in overrides:
            item_copy["stock_qty"] = overrides[item["item_id"]]
        result.append(item_copy)
    return result
