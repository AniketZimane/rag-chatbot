# VideoRAG — Full-Stack RAG Chatbot for Video Analysis

A production-grade RAG (Retrieval-Augmented Generation) chatbot that ingests YouTube and Instagram Reels, embeds their transcripts into a vector database, and lets creators ask analytical questions about engagement, content, and strategy — with streaming responses and cited sources.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND (React)                        │
│  URL Inputs → Ingest → Video Cards + Chat Panel + Suggestions   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP / SSE
┌──────────────────────────▼──────────────────────────────────────┐
│                    BACKEND (FastAPI)                             │
│                                                                 │
│  POST /ingest ─────► ingestion.py                               │
│    yt-dlp + youtube-transcript-api                              │
│    → metadata extraction (views/likes/comments/followers)       │
│    → engagement_rate = (likes + comments) / views × 100        │
│    → RecursiveCharacterTextSplitter (500 tok, 100 overlap)      │
│    → OpenAI text-embedding-3-small → ChromaDB                   │
│                                                                 │
│  POST /chat/stream ─► rag_chain.py                              │
│    → Chroma similarity search (top-k=6)                         │
│    → LangChain ChatOpenAI streaming                             │
│    → Per-session memory (last 20 turns)                         │
│    → SSE token stream + source citations                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                    ChromaDB (local persist)                      │
│  Collection: video_transcripts                                  │
│  Metadata per chunk: video_id, creator, engagement_rate, etc.   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Frontend | React + TypeScript | Fastest iteration, rich SSE support |
| Backend | FastAPI | Async-native, perfect for streaming |
| Orchestration | LangChain | Retriever + prompt chaining, memory |
| Embeddings | `text-embedding-3-small` | $0.02/1M tokens — 5× cheaper than large, 90% of quality |
| Vector DB | ChromaDB (local) | Zero infra cost for dev; swap to Qdrant/Pinecone for prod |
| LLM | `gpt-4o-mini` | 5× cheaper than gpt-4o, streams fast, good reasoning |
| Transcript | `youtube-transcript-api` + `yt-dlp` | No auth required, best OSS coverage |

---

## Setup

### Prerequisites

- Python 3.11+
- Node 18+
- OpenAI API key

### Backend

```bash
cd backend
cp .env.example .env
# Fill in OPENAI_API_KEY in .env

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm start
# Opens at http://localhost:3000
```

---

## Key Design Decisions

### Chunk Size: 500 tokens, 100 overlap

- **Why 500?** Short enough to retrieve precise moments (a hook, a CTA), long enough for context.
- **Why 100 overlap?** 20% overlap prevents splitting a key sentence across two chunks, losing meaning.
- Tested against 256 and 1024: 500 gives best precision-recall tradeoff for short-form video content.

### Embedding Model: `text-embedding-3-small`

- **Cost:** $0.02/1M tokens vs $0.13/1M for large (6.5× cheaper)
- **Quality:** MTEB benchmark shows ~5% gap — acceptable for transcript retrieval where exact terminology matters more than semantic nuance.
- At 1000 creators/day with ~2000 tokens/video: **$0.08/day** vs $0.52/day for large.

### Vector DB: ChromaDB → Qdrant for scale

- **Dev:** ChromaDB local — zero infra, no auth, instant setup.
- **Prod (1000 creators/day):** Qdrant Cloud — supports horizontal scaling, payload filtering, and <50ms p99 latency at 10M+ vectors. Pinecone is simpler but 3× pricier.
- Migration is one import swap — `langchain_qdrant.Qdrant` vs `langchain_chroma.Chroma`.

### LLM: `gpt-4o-mini` default, `gpt-4o` optional

- gpt-4o-mini: **$0.15/1M input, $0.60/1M output** — roughly $0.002/query.
- At 20 queries/creator/day × 1000 creators = **$40/day** vs $200/day for gpt-4o.
- Swap via `LLM_MODEL=gpt-4o` in `.env` for higher-stakes analysis.

### Memory: Last 20 turns in-process

- Per-session dict in FastAPI process memory.
- **For production:** Move to Redis with `RedisChatMessageHistory` from LangChain — one line change, supports horizontal scaling and session TTL.

---

## Scaling to 1000 Creators/Day

### Cost breakdown (daily)

| Item | Unit cost | Daily (1000 creators) |
|---|---|---|
| Embeddings (3-small) | $0.02/1M tok | ~$0.10 |
| LLM (gpt-4o-mini, 20 queries/creator) | $0.002/query | ~$40 |
| ChromaDB → Qdrant Cloud | $79/mo | ~$2.60/day |
| Compute (FastAPI, 2 vCPU) | ~$30/mo | ~$1/day |
| **Total** | | **~$44/day** |

### Bottlenecks & solutions

1. **Ingestion speed:** Each video ingestion is ~3-5s (yt-dlp + embedding). At 1000/day → use a background task queue (Celery + Redis) and return a job ID immediately.
2. **Embedding throughput:** OpenAI allows 3000 RPM on tier 1. Batch embed chunks in groups of 100 to stay under limits.
3. **Instagram transcripts:** yt-dlp gets captions/descriptions; for actual audio → use AssemblyAI ($0.65/hr) or self-host Whisper on GPU ($0.003/min). At 1000 reels/day × avg 60s = ~$3/day with AssemblyAI.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/ingest` | Ingest two videos → embed → return cards |
| POST | `/chat/stream` | Streaming RAG chat (SSE) |
| DELETE | `/session/{id}` | Clear chat memory |
| GET | `/vectorstore/stats` | Chunk count in DB |

---

## Environment Variables

See `backend/.env.example` for all options.

---

## Production Improvements

- [ ] Replace in-memory session store with Redis
- [ ] Replace ChromaDB with Qdrant Cloud
- [ ] Add Celery task queue for async ingestion at scale
- [ ] Add AssemblyAI integration for Instagram audio transcripts
- [ ] Add rate limiting (slowapi) per creator
- [ ] Add auth (JWT) for multi-tenant support
- [ ] Deploy backend on Railway/Fly.io, frontend on Vercel
