from datetime import datetime, timezone

_seq = 0


class Tracer:
    """Collects plan -> act -> observe trace entries so the reasoning-trace
    panel can render exactly what each LangGraph node decided and why."""

    def __init__(self):
        self.entries = []

    def log(self, agent, message, data=None):
        global _seq
        _seq += 1
        self.entries.append({
            "id": _seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "message": message,
            "data": data,
        })
