import math

from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.rag.knowledge_base import get_campaign_template_store
from app.store.db import db

SYSTEM_PROMPT = """You are the Ops Agent inside Vriddhi, an AI growth copilot for Paytm merchants. You are given \
exact stockout math (already computed, never yours to recalculate) for one catalog item, plus a retrieved \
restock-notification template passage. Write a short, plain-language merchant-facing reasoning sentence explaining \
why this reorder matters — use the exact numbers given, never invent or round differently."""


class RestockNarrative(BaseModel):
    reasoning: str = Field(description="1 sentence, plain language, using the exact item name, stock_qty, avg_daily_units, and days_to_stockout given")


def run_ops_agent(merchant, catalog, data, tracer):
    """Scans a merchant's catalog for items close to running out, using
    units_sold_last_30d as the demand signal and reorder_threshold as the
    safety floor. Items with reorder_threshold == 0 are services, not
    physical inventory, and are skipped. The stockout math is deterministic;
    the LLM's job is only to phrase the merchant-facing explanation,
    grounded in a retrieved restock-template passage."""
    tracer.log("ops_agent", f"Scanning {len(catalog)} catalog items from catalog.csv for merchant {merchant['merchant_id']}.")

    candidates = []
    for item in catalog:
        if item["reorder_threshold"] <= 0:
            continue
        avg_daily_units = item["units_sold_last_30d"] / 30
        days_to_stockout = (item["stock_qty"] / avg_daily_units) if avg_daily_units > 0 else float("inf")
        if item["stock_qty"] <= item["reorder_threshold"] * 1.5:
            candidates.append((item, avg_daily_units, days_to_stockout))

    candidates.sort(key=lambda c: c[2])

    if not candidates:
        tracer.log("ops_agent", "No items are near their reorder threshold — inventory healthy.")
        return []

    hits = get_campaign_template_store(data).similarity_search("low stock restock reminder notification to merchant", k=1)
    if hits:
        retrieved_doc, score = hits[0]
        retrieved_text = retrieved_doc["text"]
        tracer.log("ops_agent", f'RAG retrieval: query="low stock restock reminder" → matched "{retrieved_doc["id"]}" (cosine similarity {score:.3f})')
    else:
        retrieved_text = "No template passage retrieved."

    llm = get_llm().with_structured_output(RestockNarrative)
    recs = []
    for item, avg_daily_units, days_to_stockout in candidates:
        recommended_qty = max(item["reorder_threshold"], math.ceil(avg_daily_units * 30) - item["stock_qty"])

        prompt = (
            f"Item: {item['item_name']}. stock_qty={item['stock_qty']}, reorder_threshold={item['reorder_threshold']}, "
            f"avg_daily_units={avg_daily_units:.1f}, days_to_stockout={round(days_to_stockout)}, "
            f"recommended_reorder_qty={recommended_qty}.\n\n"
            f'Retrieved template passage:\n"{retrieved_text}"'
        )
        narrative = llm.invoke([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])

        tracer.log(
            "ops_agent",
            f"{item['item_name']}: stock_qty={item['stock_qty']}, reorder_threshold={item['reorder_threshold']}, "
            f"sells ~{avg_daily_units:.1f}/day → ~{round(days_to_stockout)} days to stockout. Drafting reorder of {recommended_qty} units.",
            {"itemId": item["item_id"], "daysToStockout": days_to_stockout, "recommendedQty": recommended_qty},
        )

        recs.append({
            "id": f"REC-REORDER-{merchant['merchant_id']}-{item['item_id']}",
            "type": "reorder",
            "merchantId": merchant["merchant_id"],
            "itemId": item["item_id"],
            "itemName": item["item_name"],
            "stockQty": item["stock_qty"],
            "reorderThreshold": item["reorder_threshold"],
            "daysToStockout": round(days_to_stockout),
            "recommendedQty": recommended_qty,
            "reasoning": narrative.reasoning,
        })

    return recs


def _find_catalog_item(catalog, item_name):
    q = item_name.strip().lower()
    exact = [c for c in catalog if c["item_name"].lower() == q]
    if exact:
        return exact[0]
    partial = [c for c in catalog if q in c["item_name"].lower() or c["item_name"].lower() in q]
    return partial[0] if len(partial) == 1 else None


def apply_stock_sold(merchant, catalog, item_name, quantity, tracer):
    """The merchant is reporting a real sale the system doesn't already
    know about (e.g. made in person, not through this Paytm flow) —
    decrease that item's current stock. `catalog` is mutated in place (in
    addition to being persisted) so any other tool reading the same
    catalog list later in this turn sees the update immediately."""
    item = _find_catalog_item(catalog, item_name)
    if not item:
        names = ", ".join(c["item_name"] for c in catalog)
        return {"ok": False, "message": f'I couldn\'t find an item matching "{item_name}" in the catalog. Items on file: {names}.'}

    old_qty = item["stock_qty"]
    new_qty = max(0, old_qty - quantity)
    item["stock_qty"] = new_qty
    db.set_stock_override(merchant["merchant_id"], item["item_id"], new_qty)
    db.add_audit_entry({
        "merchantId": merchant["merchant_id"],
        "actionType": "inventory.stock_sold",
        "requestPayload": {"itemId": item["item_id"], "itemName": item["item_name"], "quantity": quantity},
        "guardrailChecks": [],
        "outcome": "updated",
        "reason": f"Merchant reported selling {quantity} units of {item['item_name']}; stock updated from {old_qty} to {new_qty}.",
    })
    tracer.log("ops_agent", f"Merchant reported selling {quantity} units of {item['item_name']} — stock updated {old_qty} → {new_qty}.")

    note = ""
    if quantity > old_qty:
        note = f" Heads up: you reported selling {quantity}, but only {old_qty} were on record, so stock is floored at 0 — you may want to double-check the count."
    return {"ok": True, "message": f"Updated {item['item_name']}: stock is now {new_qty} (was {old_qty}).{note}"}


def apply_stock_restocked(merchant, catalog, item_name, quantity, tracer):
    """The merchant received new stock (a delivery, a restock) — increase
    that item's current stock. Same in-place-mutation + persistence
    pattern as apply_stock_sold."""
    item = _find_catalog_item(catalog, item_name)
    if not item:
        names = ", ".join(c["item_name"] for c in catalog)
        return {"ok": False, "message": f'I couldn\'t find an item matching "{item_name}" in the catalog. Items on file: {names}.'}

    old_qty = item["stock_qty"]
    new_qty = old_qty + quantity
    item["stock_qty"] = new_qty
    db.set_stock_override(merchant["merchant_id"], item["item_id"], new_qty)
    db.add_audit_entry({
        "merchantId": merchant["merchant_id"],
        "actionType": "inventory.stock_restocked",
        "requestPayload": {"itemId": item["item_id"], "itemName": item["item_name"], "quantity": quantity},
        "guardrailChecks": [],
        "outcome": "updated",
        "reason": f"Merchant reported restocking {quantity} units of {item['item_name']}; stock updated from {old_qty} to {new_qty}.",
    })
    tracer.log("ops_agent", f"Merchant reported restocking {quantity} units of {item['item_name']} — stock updated {old_qty} → {new_qty}.")

    return {"ok": True, "message": f"Updated {item['item_name']}: stock is now {new_qty} (was {old_qty})."}
