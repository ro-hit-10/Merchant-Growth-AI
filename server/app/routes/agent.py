from fastapi import APIRouter, HTTPException, Request

from app.agents.llm import MissingApiKeyError
from app.agents.orchestrator_graph import run_orchestrator_scan
from app.services.actions_service import dismiss_recommendation_action
from app.store.db import db

router = APIRouter()


@router.post("/agent/scan/{merchant_id}")
def scan(merchant_id: str, request: Request):
    data = request.app.state.data
    merchant = data.merchants_by_id.get(merchant_id)
    if not merchant:
        raise HTTPException(status_code=404, detail="merchant not found")

    try:
        result = run_orchestrator_scan(merchant, data)
    except MissingApiKeyError as e:
        raise HTTPException(status_code=503, detail=str(e))

    from datetime import datetime, timezone

    db.set_last_scan(merchant_id, {
        "trace": result["trace"],
        "recommendations": result["recommendations"],
        "scannedAt": datetime.now(timezone.utc).isoformat(),
    })
    return result


@router.get("/agent/last-scan/{merchant_id}")
def last_scan(merchant_id: str):
    return db.get_last_scan(merchant_id)


@router.post("/recommendations/{merchant_id}/{rec_id}/dismiss")
def dismiss(merchant_id: str, rec_id: str, request: Request):
    if merchant_id not in request.app.state.data.merchants_by_id:
        raise HTTPException(status_code=404, detail="merchant not found")
    return dismiss_recommendation_action(merchant_id, rec_id, via="ui")
