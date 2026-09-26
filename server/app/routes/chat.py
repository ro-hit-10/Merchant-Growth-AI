from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.agents.chat_graph import run_chat_agent
from app.agents.llm import MissingApiKeyError
from app.agents.service_agent import match_auto_reply
from app.store.db import db

router = APIRouter()


class ChatBody(BaseModel):
    message: str


class CustomerQueryBody(BaseModel):
    merchantId: str
    query: str


@router.post("/chat/{merchant_id}")
def chat(merchant_id: str, body: ChatBody, request: Request):
    data = request.app.state.data
    if merchant_id not in data.merchants_by_id:
        raise HTTPException(status_code=404, detail="merchant not found")
    if not body.message:
        raise HTTPException(status_code=400, detail="message is required")

    try:
        return run_chat_agent(merchant_id, body.message, data)
    except MissingApiKeyError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/chat/{merchant_id}/history")
def chat_history(merchant_id: str):
    return db.get_chat_history(merchant_id)


@router.post("/service/customer-query")
def customer_query(body: CustomerQueryBody, request: Request):
    data = request.app.state.data
    merchant = data.merchants_by_id.get(body.merchantId)
    if not merchant:
        raise HTTPException(status_code=404, detail="merchant not found")
    return match_auto_reply(body.query, merchant, data.campaign_templates)
