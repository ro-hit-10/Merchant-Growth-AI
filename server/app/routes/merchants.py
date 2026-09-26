from fastapi import APIRouter, HTTPException, Request

from app.services.catalog_service import get_effective_catalog

router = APIRouter()


@router.get("/merchants")
def list_merchants(request: Request):
    return request.app.state.data.merchants


@router.get("/merchants/{merchant_id}")
def get_merchant(merchant_id: str, request: Request):
    merchant = request.app.state.data.merchants_by_id.get(merchant_id)
    if not merchant:
        raise HTTPException(status_code=404, detail="merchant not found")
    return merchant


@router.get("/merchants/{merchant_id}/transactions")
def get_transactions(merchant_id: str, request: Request, days: int | None = None):
    data = request.app.state.data
    if merchant_id not in data.merchants_by_id:
        raise HTTPException(status_code=404, detail="merchant not found")
    txns = data.transactions_by_merchant.get(merchant_id, [])
    if days:
        from datetime import datetime

        as_of = max(datetime.strptime(t["date"], "%Y-%m-%d") for t in txns) if txns else None
        if as_of:
            txns = [t for t in txns if (as_of - datetime.strptime(t["date"], "%Y-%m-%d")).days < days]
    return txns


@router.get("/merchants/{merchant_id}/catalog")
def get_catalog(merchant_id: str, request: Request):
    data = request.app.state.data
    if merchant_id not in data.merchants_by_id:
        raise HTTPException(status_code=404, detail="merchant not found")
    return get_effective_catalog(data, merchant_id)


@router.get("/merchants/{merchant_id}/feedback")
def get_feedback(merchant_id: str, request: Request):
    data = request.app.state.data
    if merchant_id not in data.merchants_by_id:
        raise HTTPException(status_code=404, detail="merchant not found")
    return data.feedback_by_merchant.get(merchant_id, [])
