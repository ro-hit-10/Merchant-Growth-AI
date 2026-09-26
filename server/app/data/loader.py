import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field

from app.config import DATA_DIR


def _read_json(filename):
    with open(DATA_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_csv(filename):
    with open(DATA_DIR / filename, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _group_by(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return grouped


@dataclass
class Dataset:
    merchants: list
    merchants_by_id: dict
    transactions_by_merchant: dict
    catalog_by_merchant: dict
    feedback_by_merchant: dict
    category_benchmarks: dict
    campaign_templates: dict
    lending_rules: dict


def load_data() -> Dataset:
    merchants = _read_json("merchants.json")
    for m in merchants:
        m["avg_monthly_settlement_inr"] = float(m["avg_monthly_settlement_inr"])
        m["settlement_consistency_score"] = float(m["settlement_consistency_score"])

    transactions = _read_csv("transactions.csv")
    for t in transactions:
        t["amount_inr"] = float(t["amount_inr"])

    catalog = _read_csv("catalog.csv")
    for c in catalog:
        c["price_inr"] = float(c["price_inr"])
        c["stock_qty"] = int(c["stock_qty"])
        c["reorder_threshold"] = int(c["reorder_threshold"])
        c["units_sold_last_30d"] = int(c["units_sold_last_30d"])

    feedback = _read_csv("customer_feedback.csv")
    for fbk in feedback:
        fbk["rating"] = int(fbk["rating"])

    category_benchmarks = _read_json("category_benchmarks.json")
    campaign_templates = _read_json("campaign_templates.json")
    lending_rules = _read_json("lending_eligibility_rules.json")

    return Dataset(
        merchants=merchants,
        merchants_by_id={m["merchant_id"]: m for m in merchants},
        transactions_by_merchant=_group_by(transactions, "merchant_id"),
        catalog_by_merchant=_group_by(catalog, "merchant_id"),
        feedback_by_merchant=_group_by(feedback, "merchant_id"),
        category_benchmarks=category_benchmarks,
        campaign_templates=campaign_templates,
        lending_rules=lending_rules,
    )
