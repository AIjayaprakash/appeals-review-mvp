from fastapi import APIRouter, HTTPException

from app import db
from app.models import Case

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("/{case_id}", response_model=Case)
def get_case(case_id: str) -> Case:
    conn = db.get_connection()
    try:
        case = db.get_case(conn, case_id)
    finally:
        conn.close()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case
