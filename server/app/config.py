from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR.parent / "vriddhi_dataset"
STORE_DIR = BASE_DIR / "data-store"
DB_FILE = STORE_DIR / "db.json"

PORT = 4000

GUARDRAILS = {
    "default_max_discount_pct": 15,
    "promo_repeat_window_days": 14,
    "reorder_repeat_window_days": 3,
    "loan_nudge_repeat_window_days": 30,
}
