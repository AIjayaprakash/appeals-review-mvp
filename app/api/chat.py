"""POST /cases/{id}/chat -- stub for MVP1.

A future phase wires this to retrieval-augmented chat over the case's documents
and cited policy passages; for now it just validates the case exists and
acknowledges the message, so the reviewer UI has something to call.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db

router = APIRouter(prefix="/cases", tags=["chat"])


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@router.post("/{case_id}/chat", response_model=ChatResponse)
def chat(case_id: str, request: ChatRequest) -> ChatResponse:
    conn = db.get_connection()
    try:
        case = db.get_case(conn, case_id)
    finally:
        conn.close()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return ChatResponse(reply="Chat is not yet implemented in MVP1.")
