import json
import threading
import time
from datetime import datetime, timezone

from app.config import DB_FILE, STORE_DIR

_EMPTY_DB = {
    "audit_log": [],
    "campaign_history": [],
    "reorders": [],
    "loan_applications": [],
    "rm_queue": [],
    "support_tickets": [],
    "insurance_enrollments": [],
    "last_scan": {},  # merchant_id -> {trace, recommendations, scanned_at}
    "chat_history": {},  # merchant_id -> [{role, text, ts}]
    "stock_overrides": {},  # merchant_id -> {item_id: current_stock_qty}
    "active_offers": {},  # merchant_id -> {rec_id: {id, type, label, startedAt}}
}

_lock = threading.Lock()
_audit_seq = 0


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _load():
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        DB_FILE.write_text(json.dumps(_EMPTY_DB, indent=2), encoding="utf-8")
        return json.loads(json.dumps(_EMPTY_DB))
    try:
        data = json.loads(DB_FILE.read_text(encoding="utf-8"))
        merged = json.loads(json.dumps(_EMPTY_DB))
        merged.update(data)
        return merged
    except Exception:
        return json.loads(json.dumps(_EMPTY_DB))


_state = _load()


def _persist():
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    DB_FILE.write_text(json.dumps(_state, indent=2, ensure_ascii=False), encoding="utf-8")


class Db:
    def add_audit_entry(self, entry: dict):
        global _audit_seq
        with _lock:
            _audit_seq += 1
            full = {"id": f"AUD-{int(time.time() * 1000)}-{_audit_seq}", "timestamp": _now_iso(), **entry}
            _state["audit_log"].append(full)
            _persist()
            return full

    def get_audit_log(self, merchant_id: str):
        rows = [a for a in _state["audit_log"] if a["merchantId"] == merchant_id]
        return sorted(rows, key=lambda a: a["timestamp"], reverse=True)

    def get_campaign_history(self, merchant_id: str):
        return [c for c in _state["campaign_history"] if c["merchantId"] == merchant_id]

    def add_campaign(self, entry: dict):
        with _lock:
            _state["campaign_history"].append(entry)
            _persist()

    def get_reorders(self, merchant_id: str):
        return [r for r in _state["reorders"] if r["merchantId"] == merchant_id]

    def add_reorder(self, entry: dict):
        with _lock:
            _state["reorders"].append(entry)
            _persist()

    def get_loan_applications(self, merchant_id: str):
        return [l for l in _state["loan_applications"] if l["merchantId"] == merchant_id]

    def add_loan_application(self, entry: dict):
        with _lock:
            _state["loan_applications"].append(entry)
            _persist()

    def add_rm_queue_item(self, entry: dict):
        with _lock:
            _state["rm_queue"].append(entry)
            _persist()

    def get_rm_queue(self):
        return _state["rm_queue"]

    def add_support_ticket(self, entry: dict):
        with _lock:
            _state["support_tickets"].append(entry)
            _persist()

    def get_support_tickets(self, merchant_id: str):
        return [t for t in _state["support_tickets"] if t["merchantId"] == merchant_id]

    def get_insurance_enrollments(self, merchant_id: str):
        return [e for e in _state["insurance_enrollments"] if e["merchantId"] == merchant_id]

    def add_insurance_enrollment(self, entry: dict):
        with _lock:
            _state["insurance_enrollments"].append(entry)
            _persist()

    def set_last_scan(self, merchant_id: str, payload: dict):
        with _lock:
            _state["last_scan"][merchant_id] = payload
            _persist()

    def get_last_scan(self, merchant_id: str):
        return _state["last_scan"].get(merchant_id)

    def add_recommendation_to_last_scan(self, merchant_id: str, rec: dict):
        with _lock:
            scan = _state["last_scan"].get(merchant_id) or {"trace": [], "recommendations": [], "scannedAt": _now_iso()}
            if not any(r["id"] == rec["id"] for r in scan["recommendations"]):
                scan["recommendations"].append(rec)
            _state["last_scan"][merchant_id] = scan
            _persist()
            return scan

    def get_pending_recommendations(self, merchant_id: str):
        scan = _state["last_scan"].get(merchant_id)
        return scan["recommendations"] if scan else []

    def remove_recommendation(self, merchant_id: str, rec_id: str):
        with _lock:
            scan = _state["last_scan"].get(merchant_id)
            if not scan:
                return
            scan["recommendations"] = [r for r in scan["recommendations"] if r["id"] != rec_id]
            _persist()

    def append_chat(self, merchant_id: str, message: dict):
        with _lock:
            _state["chat_history"].setdefault(merchant_id, []).append(message)
            _persist()

    def get_chat_history(self, merchant_id: str):
        return _state["chat_history"].get(merchant_id, [])

    def get_stock_overrides(self, merchant_id: str):
        return _state["stock_overrides"].get(merchant_id, {})

    def set_stock_override(self, merchant_id: str, item_id: str, new_qty: int):
        with _lock:
            _state["stock_overrides"].setdefault(merchant_id, {})[item_id] = new_qty
            _persist()

    def add_active_offer(self, merchant_id: str, offer: dict):
        with _lock:
            _state["active_offers"].setdefault(merchant_id, {})[offer["id"]] = offer
            _persist()

    def get_active_offers(self, merchant_id: str):
        return list(_state["active_offers"].get(merchant_id, {}).values())

    def remove_active_offer(self, merchant_id: str, rec_id: str):
        with _lock:
            _state["active_offers"].get(merchant_id, {}).pop(rec_id, None)
            _persist()


db = Db()
