"""
rag_chain.py — LangChain RAG pipeline.
- ChromaDB vector store (persistent)
- Cohere embeddings + chat
- Streaming chat with conversation memory
- Source citation per chunk
"""

import os
import asyncio
from typing import AsyncGenerator
from functools import lru_cache

from langchain_cohere import CohereEmbeddings, ChatCohere
from langchain_chroma import Chroma
from langchain_core.messages import HumanMessage, AIMessage


CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")


@lru_cache(maxsize=1)
def get_embeddings() -> CohereEmbeddings:
    return CohereEmbeddings(
        model=os.getenv("EMBED_MODEL", "embed-english-v3.0"),
        cohere_api_key=os.getenv("COHERE_API_KEY"),
    )


@lru_cache(maxsize=1)
def get_vectorstore() -> Chroma:
    return Chroma(
        collection_name="video_transcripts",
        embedding_function=get_embeddings(),
        persist_directory=CHROMA_PERSIST_DIR,
    )


def get_llm() -> ChatCohere:
    return ChatCohere(
        model=os.getenv("LLM_MODEL", "command-r-plus"),
        cohere_api_key=os.getenv("COHERE_API_KEY"),
        streaming=True,
        temperature=0.3,
    )


def build_rag_chain():
    vs = get_vectorstore()
    retriever = vs.as_retriever(search_kwargs={"k": TOP_K})
    return retriever, get_llm()


SYSTEM_PROMPT = """You are an expert social media content analyst helping creators understand their video performance.

You have access to transcripts, metadata, and engagement metrics for two videos:
- Video A: {video_a_meta}
- Video B: {video_b_meta}

Use the retrieved context below to answer the user's question. Always:
1. Cite which video (A or B) each insight comes from
2. Reference specific lines or moments from the transcript when relevant
3. Be data-driven: use engagement rates, view counts, and metrics when comparing
4. Give actionable, specific advice — not generic tips

Retrieved context:
{context}

Conversation history is provided. Maintain continuity across turns.
"""

def format_history(history: list[dict]) -> list:
    messages = []
    for turn in history:
        if turn["role"] == "human":
            messages.append(HumanMessage(content=turn["content"]))
        elif turn["role"] == "ai":
            messages.append(AIMessage(content=turn["content"]))
    return messages


def format_docs_with_sources(docs) -> tuple[str, list[dict]]:
    """Format retrieved docs and extract source citations."""
    context_parts = []
    sources = []
    for i, doc in enumerate(docs):
        meta = doc.metadata
        vid = meta.get("video_id", "?")
        chunk_idx = meta.get("chunk_index", i)
        creator = meta.get("creator", "")
        context_parts.append(
            f"[Video {vid}, Chunk {chunk_idx}] (Creator: {creator})\n{doc.page_content}"
        )
        sources.append({
            "video_id": vid,
            "chunk_index": chunk_idx,
            "creator": creator,
            "title": meta.get("title", ""),
            "text_preview": doc.page_content[:120] + "..." if len(doc.page_content) > 120 else doc.page_content,
        })
    return "\n\n---\n\n".join(context_parts), sources


async def chat_with_memory(
    message: str,
    history: list[dict],
    video_a_id: str,
    video_b_id: str,
) -> AsyncGenerator[dict, None]:
    """
    Async generator that streams tokens + emits sources.
    Uses LangChain retriever + ChatOpenAI with streaming.
    """
    vs = get_vectorstore()
    llm = get_llm()

    # Retrieve relevant chunks — filter to relevant videos if possible
    retriever = vs.as_retriever(
        search_type="similarity",
        search_kwargs={"k": int(os.getenv("TOP_K", "6"))},
    )

    docs = await asyncio.to_thread(retriever.invoke, message)
    context, sources = format_docs_with_sources(docs)

    # Build video meta summaries from ChromaDB
    def get_meta_summary(label: str) -> str:
        try:
            res = vs._collection.get(where={"video_id": label}, limit=1, include=["metadatas"])
            if res and res["metadatas"]:
                m = res["metadatas"][0]
                er = m.get("engagement_rate", "N/A")
                return (
                    f"Creator: {m.get('creator','?')}, "
                    f"Views: {m.get('views','?')}, "
                    f"Likes: {m.get('likes','?')}, "
                    f"Comments: {m.get('comments','?')}, "
                    f"Engagement Rate: {er}%, "
                    f"Followers: {m.get('follower_count','?')}, "
                    f"Uploaded: {m.get('upload_date','?')}, "
                    f"Duration: {m.get('duration','?')}s"
                )
        except Exception:
            pass
        return "Metadata not yet loaded"

    video_a_meta = get_meta_summary("A")
    video_b_meta = get_meta_summary("B")

    # Build prompt
    history_messages = format_history(history)

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                video_a_meta=video_a_meta,
                video_b_meta=video_b_meta,
                context=context,
            ),
        }
    ]
    for m in history_messages:
        if isinstance(m, HumanMessage):
            messages.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            messages.append({"role": "assistant", "content": m.content})

    messages.append({"role": "user", "content": message})

    # Emit sources first
    yield {"type": "sources", "content": sources}

    # Stream tokens
    async for chunk in llm.astream(messages):
        if chunk.content:
            yield {"type": "token", "content": chunk.content}
