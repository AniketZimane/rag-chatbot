"""
ingestion.py — Pull metadata + transcripts from YouTube & Instagram.
Chunks transcripts and stores them in ChromaDB with metadata tags.
"""

import os
import re
import json
import hashlib
import datetime
import tempfile
import glob
from typing import Optional
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import requests
import assemblyai as aai
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rag_chain import get_vectorstore, get_embeddings

CHUNK_SIZE = 500      # ~125 tokens — sweet spot for retrieval precision
CHUNK_OVERLAP = 100   # 20% overlap prevents context loss at boundaries


def detect_platform(url: str) -> str:
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "instagram.com" in url:
        return "instagram"
    raise ValueError(f"Unsupported platform for URL: {url}")


def extract_youtube_id(url: str) -> str:
    patterns = [
        r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"shorts/([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    raise ValueError(f"Cannot extract YouTube video ID from: {url}")


def fetch_youtube_metadata(url: str) -> dict:
    """Use yt-dlp to fetch YouTube metadata (no auth required)."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "format": "bestaudio/best/bestvideo+bestaudio",
        "ignoreerrors": True,
        "geo_bypass": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("yt-dlp could not extract video info. The video may be private, age-restricted, or unavailable in your region.")

    views = info.get("view_count") or 0
    likes = info.get("like_count") or 0
    comments = info.get("comment_count") or 0
    duration = info.get("duration") or 0
    upload_ts = info.get("upload_date", "")  # YYYYMMDD
    upload_date = (
        f"{upload_ts[:4]}-{upload_ts[4:6]}-{upload_ts[6:8]}"
        if len(upload_ts) == 8
        else upload_ts
    )
    hashtags = info.get("tags", []) or []
    thumbnail = info.get("thumbnail", "")
    channel = info.get("uploader", info.get("channel", "Unknown"))
    # Follower/subscriber count
    follower_count = info.get("channel_follower_count") or info.get("uploader_follower_count") or 0

    return {
        "platform": "youtube",
        "title": info.get("title", "Untitled"),
        "creator": channel,
        "url": url,
        "views": views,
        "likes": likes,
        "comments": comments,
        "follower_count": follower_count,
        "hashtags": hashtags[:10],
        "upload_date": upload_date,
        "duration": duration,
        "thumbnail": thumbnail,
        "description": info.get("description", ""),
        "tags": hashtags,
    }


def fetch_youtube_transcript(video_id: str, info: dict = None) -> str:
    """Fetch transcript via youtube-transcript-api; fallback to yt-dlp subtitles; fallback to metadata."""

    # Attempt 1: youtube-transcript-api
    try:
        entries = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB", "a.en"])
        text = " ".join(e["text"] for e in entries)
        if text.strip():
            return text
    except Exception:
        pass

    # Attempt 2: yt-dlp auto-generated subtitles
    try:
        import tempfile, glob
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "quiet": False,
                "skip_download": True,
                "writeautomaticsub": True,
                "writesubtitles": True,
                "subtitleslangs": ["en", "en-US"],
                "subtitlesformat": "vtt",
                "outtmpl": f"{tmpdir}/%(id)s.%(ext)s",
                "geo_bypass": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            vtt_files = glob.glob(f"{tmpdir}/*.vtt")
            if vtt_files:
                with open(vtt_files[0], "r", encoding="utf-8") as f:
                    raw = f.read()
                text = re.sub(r"<[^>]+>", "", raw)
                text = re.sub(r"\d{2}:\d{2}:\d{2}\.\d+ --> .*", "", text)
                text = re.sub(r"^WEBVTT.*$", "", text, flags=re.MULTILINE)
                text = re.sub(r"^NOTE.*$", "", text, flags=re.MULTILINE)
                text = " ".join(text.split())
                if text.strip():
                    return text
    except Exception:
        pass

    # Attempt 3: fall back to video metadata (title + description + tags)
    if info:
        parts = []
        if info.get("title"):
            parts.append(f"Title: {info['title']}")
        if info.get("description"):
            parts.append(f"Description: {info['description'][:2000]}")
        tags = info.get("tags") or []
        if tags:
            parts.append(f"Tags: {', '.join(tags[:20])}")
        if parts:
            return "[Transcript unavailable — using video metadata]\n\n" + "\n".join(parts)

    return "[Transcript not available for this video]"


def fetch_instagram_metadata(url: str) -> dict:
    """
    Use yt-dlp to pull Instagram Reels metadata.
    Instagram API is heavily restricted; yt-dlp is the best OSS fallback.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestaudio/best/bestvideo+bestaudio",
        "ignoreerrors": True,
        "geo_bypass": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            raise RuntimeError("Could not extract Instagram metadata. The reel may be private or unavailable.")
    except Exception as e:
        raise RuntimeError(
            f"Could not fetch Instagram metadata (may require login for private content): {e}"
        )

    views = (info.get("view_count") or info.get("play_count") or
              info.get("video_view_count") or info.get("tbr") or 0)
    likes = info.get("like_count") or 0
    comments = info.get("comment_count") or 0
    duration = info.get("duration") or 0
    upload_ts = info.get("upload_date", "")
    upload_date = (
        f"{upload_ts[:4]}-{upload_ts[4:6]}-{upload_ts[6:8]}"
        if len(upload_ts) == 8
        else upload_ts
    )
    hashtags = re.findall(r"#\w+", info.get("description", ""))
    thumbnail = info.get("thumbnail", "")
    creator = info.get("uploader", info.get("channel", "Unknown"))
    follower_count = info.get("channel_follower_count") or 0

    description = info.get("description", "")

    return {
        "platform": "instagram",
        "title": info.get("title", description[:80] if description else "Instagram Reel"),
        "creator": creator,
        "url": url,
        "views": views,
        "likes": likes,
        "comments": comments,
        "follower_count": follower_count,
        "hashtags": hashtags[:10],
        "upload_date": upload_date,
        "duration": duration,
        "thumbnail": thumbnail,
        "description": description,
    }


def _download_instagram_audio(url: str, tmpdir: str) -> Optional[str]:
    """Download Instagram Reel audio to tmpdir, return file path or None."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": f"{tmpdir}/audio.%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",
        }],
        "geo_bypass": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    files = glob.glob(f"{tmpdir}/audio.*")
    return files[0] if files else None


def fetch_instagram_transcript(meta: dict) -> str:
    """Transcribe Instagram Reel audio via AssemblyAI; fall back to caption."""
    api_key = os.getenv("ASSEMBLYAI_API_KEY", "")
    if api_key:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = _download_instagram_audio(meta["url"], tmpdir)
                if audio_path:
                    aai.settings.api_key = api_key
                    transcriber = aai.Transcriber()
                    result = transcriber.transcribe(audio_path)
                    if result.text and result.text.strip():
                        return result.text
        except Exception as e:
            print(f"[AssemblyAI] Transcription failed: {e}")

    description = meta.get("description", "")
    if description:
        return f"[Caption/Description]: {description}"
    return "[No transcript available]"


def compute_engagement_rate(views: int, likes: int, comments: int) -> float:
    if views > 0:
        return round((likes + comments) / views * 100, 4)
    if likes + comments > 0:
        # Views unavailable (common for Instagram); use likes+comments as proxy
        return round(likes / (likes + comments) * 100, 4) if comments > 0 else 100.0
    return 0.0


def chunk_and_embed(transcript: str, metadata: dict, video_label: str) -> int:
    """
    Split transcript → embed → upsert into ChromaDB.
    Returns number of chunks stored.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_text(transcript)
    if not chunks:
        chunks = [transcript[:500] or "No content available"]

    vs = get_vectorstore()

    docs_text = []
    docs_meta = []
    docs_ids = []

    for i, chunk in enumerate(chunks):
        chunk_id = hashlib.sha256(f"{video_label}_{i}_{chunk[:50]}".encode()).hexdigest()[:32]
        docs_text.append(chunk)
        docs_meta.append({
            "video_id": video_label,
            "platform": metadata["platform"],
            "creator": metadata["creator"],
            "title": metadata["title"],
            "chunk_index": i,
            "total_chunks": len(chunks),
            "engagement_rate": str(metadata.get("engagement_rate", 0)),
            "views": str(metadata.get("views", 0)),
            "likes": str(metadata.get("likes", 0)),
            "comments": str(metadata.get("comments", 0)),
            "upload_date": metadata.get("upload_date", ""),
            "duration": str(metadata.get("duration", 0)),
            "follower_count": str(metadata.get("follower_count", 0)),
        })
        docs_ids.append(chunk_id)

    # Delete old chunks for this video_label before re-inserting
    try:
        existing = vs._collection.get(where={"video_id": video_label})
        if existing and existing["ids"]:
            vs._collection.delete(ids=existing["ids"])
    except Exception:
        pass

    vs.add_texts(texts=docs_text, metadatas=docs_meta, ids=docs_ids)
    return len(chunks)


def ingest_video(url: str, label: str) -> dict:
    """
    Full ingestion pipeline for one video:
    1. Detect platform
    2. Fetch metadata
    3. Fetch transcript
    4. Compute engagement
    5. Chunk + embed → ChromaDB
    6. Return card data
    """
    platform = detect_platform(url)

    if platform == "youtube":
        meta = fetch_youtube_metadata(url)
        vid_id = extract_youtube_id(url)
        transcript = fetch_youtube_transcript(vid_id, info=meta)
    else:
        meta = fetch_instagram_metadata(url)
        transcript = fetch_instagram_transcript(meta)

    engagement_rate = compute_engagement_rate(
        meta["views"], meta["likes"], meta["comments"]
    )
    meta["engagement_rate"] = engagement_rate
    meta["video_label"] = label

    n_chunks = chunk_and_embed(transcript, meta, label)

    return {
        "video_id": label,
        "url": url,
        "title": meta["title"],
        "creator": meta["creator"],
        "platform": platform,
        "views": meta["views"],
        "likes": meta["likes"],
        "comments": meta["comments"],
        "follower_count": meta["follower_count"],
        "hashtags": meta["hashtags"],
        "upload_date": meta["upload_date"],
        "duration": meta["duration"],
        "engagement_rate": engagement_rate,
        "thumbnail": meta["thumbnail"],
        "transcript_preview": transcript[:300] + "..." if len(transcript) > 300 else transcript,
        "chunks_stored": n_chunks,
    }


def get_video_metadata(video_id: str) -> dict:
    """Retrieve stored metadata from ChromaDB for a given video label."""
    vs = get_vectorstore()
    results = vs._collection.get(
        where={"video_id": video_id},
        limit=1,
        include=["metadatas"],
    )
    if results and results["metadatas"]:
        return results["metadatas"][0]
    return {}
