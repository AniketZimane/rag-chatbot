# VideoRAG — compare two videos, ask anything about them

Paste two YouTube or Instagram Reel URLs. Get back engagement metrics, a real transcript, and a chat interface that can answer "why did video A outperform video B?" with citations to the actual words spoken.

Built this because I kept seeing creators eyeball their analytics dashboard and guess. This makes it a conversation.

---

## What it actually does

1. You drop two URLs into the frontend
2. The backend pulls metadata (views, likes, comments, follower count) via yt-dlp — no API keys, no OAuth dance
3. For YouTube it grabs the transcript via `youtube-transcript-api`, falls back to yt-dlp VTT subtitles, falls back to title + description if neither exists
4. For Instagram it downloads the audio and runs it through AssemblyAI. Caption fallback if the key isn't set
5. Transcripts get chunked, embedded via Cohere, stored in ChromaDB
6. You chat. The RAG pipeline retrieves the most relevant chunks across both videos and streams a response with source citations

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND (React)                        │
│  URL Inputs → Ingest → Video Cards + Chat Panel                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP / SSE
┌──────────────────────────▼──────────────────────────────────────┐
│                    BACKEND (FastAPI)                             │
│                                                                 │
│  POST /ingest ─────► ingestion.py                               │
│    yt-dlp → metadata + transcript                               │
│    AssemblyAI → Instagram audio transcription                   │
│    RecursiveCharacterTextSplitter (500 chars, 100 overlap)      │
│    Cohere embed-english-v3.0 → ChromaDB                         │
│                                                                 │
│  POST /chat/stream ─► rag_chain.py                              │
│    Chroma similarity search (top-k=6)                           │
│    Cohere command-r-plus streaming                              │
│    Per-session memory (last 20 turns, in-process dict)          │
│    SSE token stream + chunk-level source citations              │
│                                                                 │
│  slowapi rate limiting — keyed per creator name, IP fallback    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                    ChromaDB (local persist)                      │
│  Collection: video_transcripts                                  │
│  Metadata per chunk: video_id, creator, engagement_rate, ...    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React + TypeScript | SSE support is native, iteration is fast |
| Backend | FastAPI | Async-first, streaming responses without hacks |
| Embeddings | Cohere `embed-english-v3.0` | Better retrieval than OpenAI small on short social content in my tests |
| LLM | Cohere `command-r-plus` | Grounded generation, citation-aware by design, cheaper than GPT-4o at this volume |
| Vector DB | ChromaDB (local) | Zero infra for dev. One import swap to Qdrant for prod |
| Transcription | `youtube-transcript-api` → yt-dlp VTT → AssemblyAI | Layered fallbacks — most YouTube videos have captions, Instagram never does |
| Rate limiting | slowapi | Thin wrapper over limits.py, plays nicely with FastAPI decorators |

---

## Setup

**Prerequisites:** Python 3.11+, Node 18+, ffmpeg on PATH (needed for Instagram audio extraction)

```bash
# Backend
cd backend
cp .env.example .env
# Add your COHERE_API_KEY. ASSEMBLYAI_API_KEY is optional — Instagram falls back to captions without it.

pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm start
# http://localhost:3000
```

---

## Design decisions I can defend

### Chunk size: 500 characters, 100 overlap

Short-form video content is dense. A 60-second Reel might have 150 words. At 1024 characters you're retrieving half the transcript as a single chunk — the retriever can't discriminate between the hook and the CTA. At 256 you lose sentence context and the LLM gets fragments.

500 with 100 overlap hits the sweet spot for this content type: precise enough to retrieve a specific moment, wide enough that a sentence split at a boundary doesn't lose its meaning. The 20% overlap is deliberate — it's the minimum to prevent a key phrase from being split across two chunks and disappearing from both.

I tested 256, 500, and 1024 on a set of 20 videos. 500 gave the best answer relevance on questions like "what did the creator say about their editing process."

### Why ChromaDB and not Pinecone or Qdrant

For a dev/demo setup, ChromaDB is the right call. No account, no API key, no network hop, persists to disk. The entire vector store is a folder you can inspect.

The honest answer for production: ChromaDB doesn't scale horizontally. It's a single-process SQLite-backed store. At ~50k chunks (roughly 500 videos) query latency starts climbing. At 500k it becomes a problem.

Qdrant is the upgrade path. It supports payload filtering (so you can scope retrieval to a specific creator without fetching everything), horizontal scaling, and sub-50ms p99 at 10M+ vectors. Migration is literally one import swap — `langchain_qdrant.Qdrant` instead of `langchain_chroma.Chroma`, same interface.

Pinecone would also work but it's 3× the cost for the same performance tier and you're locked in.

### Why Cohere over OpenAI

Two reasons. First, `command-r-plus` has grounded generation built into its training — it's less likely to hallucinate citations than GPT-4o-mini when the context window has multiple sources. For a tool that's explicitly about citing transcript moments, that matters.

Second, `embed-english-v3.0` outperformed `text-embedding-3-small` on my retrieval tests for this specific content type. Social media transcripts are informal, repetitive, and full of filler words. Cohere's embeddings handle that better in my experience — the semantic clustering is tighter on short conversational text.

Cost is roughly comparable at this scale.

### Rate limiting: per creator, not per IP

The obvious approach is IP-based rate limiting. The problem is that a single creator running a batch comparison job from one machine would hit the limit immediately, while a bad actor behind a rotating proxy wouldn't.

Keying on creator name (lowercased, from the request body) means the limit tracks the actual resource being consumed — transcript downloads and embedding calls are per-creator, so the limit should be too. IP fallback handles unauthenticated requests and cases where the body doesn't include a creator field.

10/minute on `/ingest` is conservative — each ingest is a yt-dlp call + AssemblyAI transcription + embedding batch. At 10/minute you're already pushing the AssemblyAI free tier. 30/minute on `/chat/stream` is generous because chat is cheap (just a retrieval + LLM call).

### Session memory: in-process dict, last 20 turns

This is the thing that breaks first at scale and I know it. An in-process dict means sessions don't survive a restart and don't work across multiple workers. It's fine for a demo with one Uvicorn process.

The fix is `RedisChatMessageHistory` from LangChain — one line change, adds a TTL, works across workers. I didn't add it because it adds a Redis dependency that makes local setup harder and this isn't at that scale yet.

20 turns is enough for a focused analysis session. Beyond that the context window fills up and the LLM starts ignoring early turns anyway.

---

## What breaks at 10,000 users

**Ingestion throughput.** Each `/ingest` call is synchronous inside `asyncio.to_thread` — it blocks a thread for 3–8 seconds while yt-dlp runs and AssemblyAI transcribes. At 10k users that thread pool saturates fast. The fix is Celery + Redis: return a job ID immediately, poll for completion. The groundwork is there (ingestion is already a pure function), it just needs the queue wrapper.

**ChromaDB.** A single SQLite file does not handle concurrent writes. Multiple Uvicorn workers will corrupt it. You need to either run one worker (defeats the point) or migrate to Qdrant/Weaviate before you scale horizontally.

**Session store.** As above — in-process dict dies on restart and doesn't work with multiple workers. Redis with a 24h TTL is the fix.

**AssemblyAI costs.** At $0.65/audio-hour, 10k Instagram Reels averaging 45 seconds each = ~125 hours = ~$81/day just in transcription. At that scale you'd want to self-host Whisper on a GPU instance. A g4dn.xlarge on AWS runs ~$0.50/hr and can transcribe roughly 60× real-time, so 10k × 45s = 125 hours of audio transcribed in ~2 hours of GPU time = ~$1/day. The API is the right call until you hit that crossover.

**Cohere rate limits.** `embed-english-v3.0` has a 10k RPM limit on the production tier. At 500 chunks per video × 10k videos/day you're at ~3.5M embedding calls/day, well within limits if spread across the day. Burst ingestion (100 videos at once) will hit it — batch embedding in groups of 96 (Cohere's max batch size) and add a retry with exponential backoff.

---

## API

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/ingest` | Ingest two videos, returns metadata cards. Rate limited 10/min per creator |
| POST | `/chat/stream` | Streaming RAG chat (SSE). Rate limited 30/min per creator |
| DELETE | `/session/{id}` | Clear chat history for a session |
| GET | `/vectorstore/stats` | Total chunk count in ChromaDB |

---

## Environment variables

```bash
# Required
COHERE_API_KEY=

# Optional — Instagram falls back to caption without this
ASSEMBLYAI_API_KEY=

# Tuning (defaults shown)
EMBED_MODEL=embed-english-v3.0
LLM_MODEL=command-r-plus-08-2024
CHROMA_PERSIST_DIR=./chroma_db
TOP_K=6
RATE_LIMIT_INGEST=10/minute
RATE_LIMIT_CHAT=30/minute
```

---

## What's next

- Celery + Redis for async ingestion (the `/ingest` endpoint blocks too long under load)
- Qdrant swap for the vector store (ChromaDB ceiling is real)
- Redis session store (in-process dict doesn't survive restarts)
- JWT auth for multi-tenant support (right now anyone can ingest anything)
- Whisper self-hosted on GPU once AssemblyAI costs justify it
