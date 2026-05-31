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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from ingestion import ingest_video, get_video_metadata
from rag_chain import build_rag_chain, get_vectorstore, chat_with_memory

load_dotenv()

# In-memory session store: session_id -> chat_history list
session_store: dict[str, list] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 RAG Chatbot backend starting up...")
    yield
    print("🛑 Shutting down...")

app = FastAPI(title="RAG Video Chatbot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
async def ingest(req: IngestRequest):
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
async def chat_stream(req: ChatRequest):
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
