from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.cases import router as cases_router
from app.api.chat import router as chat_router
from app.api.intake import router as intake_router
from app.api.review import router as review_router
from app.db import get_connection, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = get_connection()
    try:
        init_db(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="Appeals & Grievances Review Copilot", lifespan=lifespan)

# Local dev only -- the reviewer UI (Vite dev server) has no auth in MVP1, per
# CLAUDE.md; this must never widen beyond localhost origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cases_router)
app.include_router(review_router)
app.include_router(chat_router)
app.include_router(intake_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
