"""
RAG Chatbot Backend — FastAPI + LangChain + ChromaDB
Supports YouTube & Instagram Reels ingestion, embedding, and streaming chat.
"""

import os
import re
import json
import asyncio
import hashlib
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from ingestion import ingest_video, get_video_metadata
from rag_chain import build_rag_chain, get_vectorstore, chat_with_memory

load_dotenv()

INGEST_RATE  = os.getenv("RATE_LIMIT_INGEST",  "10/minute")
CHAT_RATE    = os.getenv("RATE_LIMIT_CHAT",    "30/minute")


def _creator_key(request: Request) -> str:
    """Rate-limit key: creator name from body when available, else remote IP."""
    try:
        # body is already parsed by FastAPI; access via request.state if set,
        # otherwise fall back to IP so the limiter never blocks on missing data.
        body = request.state.body if hasattr(request.state, "body") else {}
        creator = (body.get("creator") or "").strip().lower()
        return creator if creator else get_remote_address(request)
    except Exception:
        return get_remote_address(request)


limiter = Limiter(key_func=_creator_key)

# In-memory session store: session_id -> chat_history list
session_store: dict[str, list] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 RAG Chatbot backend starting up...")
    yield
    print("🛑 Shutting down...")

app = FastAPI(title="RAG Video Chatbot", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _cache_body(request: Request, call_next):
    """Pre-parse JSON body into request.state so _creator_key can read it."""
    try:
        request.state.body = await request.json()
    except Exception:
        request.state.body = {}
    return await call_next(request)


# ── Request / Response Models ──────────────────────────────────────────────

class IngestRequest(BaseModel):
    url_a: str
    url_b: str

class ChatRequest(BaseModel):
    session_id: str
    message: str
    video_a_id: str
    video_b_id: str

class VideoCard(BaseModel):
    video_id: str
    url: str
    title: str
    creator: str
    platform: str
    views: int
    likes: int
    comments: int
    follower_count: int
    hashtags: list[str]
    upload_date: str
    duration: int
    engagement_rate: float
    thumbnail: str
    transcript_preview: str


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/ingest")
@limiter.limit(INGEST_RATE)
async def ingest(request: Request, req: IngestRequest):
    """
    Ingest two video URLs → pull metadata + transcript →
    chunk + embed → store in ChromaDB.
    Returns metadata cards for both videos.
    """
    try:
        video_a = await asyncio.to_thread(ingest_video, req.url_a, "A")
        video_b = await asyncio.to_thread(ingest_video, req.url_b, "B")
        return {
            "video_a": video_a,
            "video_b": video_b,
            "session_ready": True,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/stream")
@limiter.limit(CHAT_RATE)
async def chat_stream(request: Request, req: ChatRequest):
    """
    Streaming RAG chat endpoint.
    Maintains per-session memory; returns SSE chunks.
    """
    if req.session_id not in session_store:
        session_store[req.session_id] = []

    history = session_store[req.session_id]

    async def generate() -> AsyncGenerator[str, None]:
        full_response = ""
        sources_sent = False
        try:
            async for chunk in chat_with_memory(
                message=req.message,
                history=history,
                video_a_id=req.video_a_id,
                video_b_id=req.video_b_id,
            ):
                if chunk["type"] == "token":
                    full_response += chunk["content"]
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk['content']})}\n\n"
                elif chunk["type"] == "sources" and not sources_sent:
                    sources_sent = True
                    yield f"data: {json.dumps({'type': 'sources', 'content': chunk['content']})}\n\n"

            # Persist to memory
            history.append({"role": "human", "content": req.message})
            history.append({"role": "ai", "content": full_response})
            # Keep last 20 turns
            session_store[req.session_id] = history[-20:]

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    session_store.pop(session_id, None)
    return {"cleared": True}


@app.get("/vectorstore/stats")
async def vectorstore_stats():
    vs = get_vectorstore()
    count = vs._collection.count()
    return {"total_chunks": count}
