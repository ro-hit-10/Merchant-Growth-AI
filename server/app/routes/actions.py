from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.actions_service import (
    send_campaign_action,
    create_reorder_action,
    apply_loan_action,
    apply_insurance_action,
    end_recommendation_action,
)

router = APIRouter()


class CampaignSendBody(BaseModel):
    merchantId: str
    recId: str
    templateId: str
    customerSegment: str
    discountPct: float
    itemId: str
    type: str = "promo"


class ReorderBody(BaseModel):
    merchantId: str
    recId: str
    itemId: str
    quantity: int
    trigger: str = "low_stock"


class LoanApplyBody(BaseModel):
    merchantId: str
    recId: str
    tier: str
    amountInr: float
    consent: bool = False


class InsuranceApplyBody(BaseModel):
    merchantId: str
    recId: str
    productName: str
    premiumInr: float
    consent: bool = False


class EndBody(BaseModel):
    merchantId: str


def _status_code(result):
    return 400 if result["status"] == "blocked" else 200


@router.post("/actions/campaign/send")
def campaign_send(body: CampaignSendBody, request: Request):
    if body.merchantId not in request.app.state.data.merchants_by_id:
        raise HTTPException(status_code=404, detail="merchant not found")
    result = send_campaign_action(body.merchantId, body.templateId, body.customerSegment, body.discountPct, body.itemId, rec_id=body.recId, rec_type=body.type)
    return _json_with_status(result)


@router.post("/actions/inventory/reorder")
def inventory_reorder(body: ReorderBody, request: Request):
    if body.merchantId not in request.app.state.data.merchants_by_id:
        raise HTTPException(status_code=404, detail="merchant not found")
    result = create_reorder_action(body.merchantId, body.itemId, body.quantity, rec_id=body.recId, trigger=body.trigger)
    return _json_with_status(result)


@router.post("/actions/lending/apply")
def lending_apply(body: LoanApplyBody, request: Request):
    data = request.app.state.data
    merchant = data.merchants_by_id.get(body.merchantId)
    if not merchant:
        raise HTTPException(status_code=404, detail="merchant not found")
    result = apply_loan_action(body.merchantId, body.tier, body.amountInr, body.consent, data.lending_rules, merchant, rec_id=body.recId)
    return _json_with_status(result)


@router.post("/actions/insurance/apply")
def insurance_apply(body: InsuranceApplyBody, request: Request):
    if body.merchantId not in request.app.state.data.merchants_by_id:
        raise HTTPException(status_code=404, detail="merchant not found")
    result = apply_insurance_action(body.merchantId, body.productName, body.premiumInr, body.consent, rec_id=body.recId)
    return _json_with_status(result)


@router.post("/actions/recommendation/{rec_id}/end")
def end_recommendation(rec_id: str, body: EndBody, request: Request):
    if body.merchantId not in request.app.state.data.merchants_by_id:
        raise HTTPException(status_code=404, detail="merchant not found")
    result = end_recommendation_action(body.merchantId, rec_id, via="ui")
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="active offer not found")
    return result


def _json_with_status(result):
    from fastapi.responses import JSONResponse

    return JSONResponse(content=result, status_code=_status_code(result))
