from fastapi import APIRouter

from app.store.db import db

router = APIRouter()


@router.get("/audit-log/{merchant_id}")
def audit_log(merchant_id: str):
    return db.get_audit_log(merchant_id)
