import React, { useState, useRef, useEffect, useCallback } from 'react';
import './index.css';

const API = 'http://localhost:8000';

// ── Types ──────────────────────────────────────────────────────

interface VideoCard {
  video_id: string;
  url: string;
  title: string;
  creator: string;
  platform: string;
  views: number;
  likes: number;
  comments: number;
  follower_count: number;
  hashtags: string[];
  upload_date: string;
  duration: number;
  engagement_rate: number;
  thumbnail: string;
  transcript_preview: string;
  chunks_stored: number;
}

interface Source {
  video_id: string;
  chunk_index: number;
  creator: string;
  title: string;
  text_preview: string;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: Source[];
  streaming?: boolean;
}

// ── Helpers ────────────────────────────────────────────────────

function fmt(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

function fmtDur(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function uid() {
  return Math.random().toString(36).slice(2);
}

// ── Sub-components ─────────────────────────────────────────────

const PlatformBadge: React.FC<{ platform: string }> = ({ platform }) => (
  <span style={{
    fontSize: 10, fontFamily: 'var(--mono)', fontWeight: 700,
    padding: '2px 7px', borderRadius: 3,
    background: platform === 'youtube' ? '#ff000020' : '#e1306c20',
    color: platform === 'youtube' ? '#ff4444' : '#e1306c',
    border: `1px solid ${platform === 'youtube' ? '#ff444440' : '#e1306c40'}`,
    textTransform: 'uppercase', letterSpacing: 1,
  }}>{platform}</span>
);

const StatBox: React.FC<{ label: string; value: string; accent?: string }> = ({ label, value, accent }) => (
  <div style={{
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 6, padding: '8px 12px', flex: 1, minWidth: 70,
  }}>
    <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--mono)', marginBottom: 2 }}>{label}</div>
    <div style={{ fontSize: 15, fontWeight: 700, color: accent || 'var(--text)', fontFamily: 'var(--mono)' }}>{value}</div>
  </div>
);

const VideoCardUI: React.FC<{ card: VideoCard; label: string }> = ({ card, label }) => {
  const labelColor = label === 'A' ? 'var(--accent)' : 'var(--accent2)';
  return (
    <div style={{
      background: 'var(--surface2)', border: '1px solid var(--border)',
      borderRadius: 12, overflow: 'hidden', flex: 1,
      boxShadow: `0 0 0 1px ${labelColor}22`,
    }}>
      {/* Thumbnail */}
      <div style={{ position: 'relative', aspectRatio: '16/9', background: '#0d0d18', overflow: 'hidden' }}>
        {card.thumbnail ? (
          <img src={card.thumbnail} alt={card.title} style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: 0.85 }} />
        ) : (
          <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 32 }}>▶</div>
        )}
        <div style={{
          position: 'absolute', top: 10, left: 10,
          background: labelColor, color: '#fff',
          fontFamily: 'var(--mono)', fontWeight: 700, fontSize: 13,
          padding: '2px 10px', borderRadius: 4,
        }}>Video {label}</div>
        <div style={{
          position: 'absolute', bottom: 8, right: 10,
          background: '#000000cc', borderRadius: 3, padding: '1px 6px',
          fontFamily: 'var(--mono)', fontSize: 11, color: '#fff',
        }}>{fmtDur(card.duration)}</div>
        <div style={{ position: 'absolute', top: 10, right: 10 }}>
          <PlatformBadge platform={card.platform} />
        </div>
      </div>

      {/* Info */}
      <div style={{ padding: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.35, marginBottom: 4, color: 'var(--text)' }}
          title={card.title}>
          {card.title.length > 80 ? card.title.slice(0, 80) + '…' : card.title}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10, fontFamily: 'var(--mono)' }}>
          @{card.creator} · {fmt(card.follower_count)} followers · {card.upload_date}
        </div>

        {/* Stats row */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
          <StatBox label="VIEWS" value={fmt(card.views)} />
          <StatBox label="LIKES" value={fmt(card.likes)} />
          <StatBox label="COMMENTS" value={fmt(card.comments)} />
          <StatBox label="ENG RATE" value={card.engagement_rate.toFixed(2) + '%'} accent="var(--green)" />
        </div>

        {/* Hashtags */}
        {card.hashtags.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>
            {card.hashtags.slice(0, 6).map((h, i) => (
              <span key={i} style={{
                fontSize: 10, fontFamily: 'var(--mono)',
                background: 'var(--surface)', border: '1px solid var(--border)',
                borderRadius: 3, padding: '1px 6px', color: 'var(--text-muted)',
              }}>{h.startsWith('#') ? h : `#${h}`}</span>
            ))}
          </div>
        )}

        {/* Transcript preview */}
        <div style={{
          fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--mono)',
          background: 'var(--surface)', borderRadius: 5, padding: '6px 9px',
          lineHeight: 1.5, maxHeight: 56, overflow: 'hidden',
          borderLeft: `2px solid ${labelColor}`,
        }}>
          {card.transcript_preview}
        </div>

        <div style={{ marginTop: 8, fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
          {card.chunks_stored} chunks stored in vector DB
        </div>
      </div>
    </div>
  );
};

const SourcePill: React.FC<{ source: Source }> = ({ source }) => (
  <div style={{
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 5, padding: '5px 9px', fontSize: 10,
    fontFamily: 'var(--mono)', color: 'var(--text-muted)',
    borderLeft: `2px solid ${source.video_id === 'A' ? 'var(--accent)' : 'var(--accent2)'}`,
    maxWidth: 240,
  }}>
    <div style={{ fontWeight: 700, color: source.video_id === 'A' ? 'var(--accent)' : 'var(--accent2)', marginBottom: 2 }}>
      Video {source.video_id} · Chunk {source.chunk_index}
    </div>
    <div style={{ opacity: 0.7, lineHeight: 1.4 }}>{source.text_preview.slice(0, 80)}…</div>
  </div>
);

const MessageBubble: React.FC<{ msg: ChatMessage }> = ({ msg }) => {
  const isUser = msg.role === 'user';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start', marginBottom: 14 }}>
      <div style={{
        maxWidth: '88%',
        background: isUser ? 'var(--accent)' : 'var(--surface2)',
        border: `1px solid ${isUser ? 'var(--accent)' : 'var(--border)'}`,
        borderRadius: isUser ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
        padding: '10px 14px',
        fontSize: 13, lineHeight: 1.6, color: isUser ? '#fff' : 'var(--text)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}>
        {msg.content}
        {msg.streaming && <span style={{ opacity: 0.5, animation: 'blink 1s infinite' }}>▌</span>}
      </div>
      {/* Sources */}
      {msg.sources && msg.sources.length > 0 && !msg.streaming && (
        <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 5, maxWidth: '88%' }}>
          {msg.sources.slice(0, 4).map((s, i) => <SourcePill key={i} source={s} />)}
        </div>
      )}
    </div>
  );
};

// ── Suggested questions ────────────────────────────────────────

const SUGGESTIONS = [
  'Why did Video A get more engagement than Video B?',
  'What\'s the engagement rate of each video?',
  'Compare the hooks in the first 5 seconds.',
  'Who\'s the creator of Video B and what\'s their follower count?',
  'Suggest improvements for B based on what worked in A.',
];

// ── Main App ───────────────────────────────────────────────────

export default function App() {
  const [urlA, setUrlA] = useState('');
  const [urlB, setUrlB] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState('');
  const [error, setError] = useState('');

  const [videoA, setVideoA] = useState<VideoCard | null>(null);
  const [videoB, setVideoB] = useState<VideoCard | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);

  const sessionId = useRef(uid());
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const ingest = async () => {
    if (!urlA.trim() || !urlB.trim()) {
      setError('Please enter both video URLs.');
      return;
    }
    setError('');
    setLoading(true);
    setLoadingMsg('Fetching metadata & transcripts…');
    try {
      const res = await fetch(`${API}/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url_a: urlA.trim(), url_b: urlB.trim() }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Ingestion failed');
      }
      const data = await res.json();
      setVideoA(data.video_a);
      setVideoB(data.video_b);
      setMessages([{
        id: uid(), role: 'assistant',
        content: `✅ Both videos ingested and embedded!\n\n` +
          `• Video A (${data.video_a.platform}): "${data.video_a.title}" by @${data.video_a.creator} — ${data.video_a.engagement_rate.toFixed(2)}% engagement\n` +
          `• Video B (${data.video_b.platform}): "${data.video_b.title}" by @${data.video_b.creator} — ${data.video_b.engagement_rate.toFixed(2)}% engagement\n\n` +
          `Ask me anything about these videos!`,
      }]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
      setLoadingMsg('');
    }
  };

  const sendMessage = useCallback(async (text?: string) => {
    const msg = (text || input).trim();
    if (!msg || chatLoading || !videoA || !videoB) return;
    setInput('');

    const userMsg: ChatMessage = { id: uid(), role: 'user', content: msg };
    const assistantId = uid();
    const assistantMsg: ChatMessage = { id: assistantId, role: 'assistant', content: '', streaming: true };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setChatLoading(true);

    try {
      const res = await fetch(`${API}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId.current,
          message: msg,
          video_a_id: 'A',
          video_b_id: 'B',
        }),
      });

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const payload = JSON.parse(line.slice(6));

          if (payload.type === 'token') {
            setMessages(prev => prev.map(m =>
              m.id === assistantId
                ? { ...m, content: m.content + payload.content }
                : m
            ));
          } else if (payload.type === 'sources') {
            setMessages(prev => prev.map(m =>
              m.id === assistantId
                ? { ...m, sources: payload.content }
                : m
            ));
          } else if (payload.type === 'done') {
            setMessages(prev => prev.map(m =>
              m.id === assistantId ? { ...m, streaming: false } : m
            ));
          } else if (payload.type === 'error') {
            setMessages(prev => prev.map(m =>
              m.id === assistantId
                ? { ...m, content: `Error: ${payload.content}`, streaming: false }
                : m
            ));
          }
        }
      }
    } catch (e: any) {
      setMessages(prev => prev.map(m =>
        m.id === assistantId
          ? { ...m, content: `Network error: ${e.message}`, streaming: false }
          : m
      ));
    } finally {
      setChatLoading(false);
    }
  }, [input, chatLoading, videoA, videoB]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // ── Render ────────────────────────────────────────────────────

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', fontFamily: 'var(--sans)' }}>
      <style>{`
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes spin { to { transform: rotate(360deg) } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
        .fade-in { animation: fadeIn 0.25s ease; }
        .suggestion-btn:hover { background: var(--surface2) !important; color: var(--text) !important; }
        .send-btn:hover { opacity: 0.85; }
        .ingest-btn:hover { opacity: 0.88; }
        textarea:focus { outline: none; border-color: var(--accent) !important; }
        input:focus { outline: none; border-color: var(--accent) !important; }
      `}</style>

      {/* Header */}
      <header style={{
        borderBottom: '1px solid var(--border)',
        padding: '12px 24px',
        display: 'flex', alignItems: 'center', gap: 12,
        background: 'var(--surface)',
      }}>
        <div style={{
          width: 28, height: 28, borderRadius: 6,
          background: 'linear-gradient(135deg, var(--accent), var(--accent2))',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 14,
        }}>⚡</div>
        <span style={{ fontWeight: 700, fontSize: 15, letterSpacing: 0.5 }}>VideoRAG</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--mono)', marginLeft: 4 }}>
          LangChain + ChromaDB
        </span>
        {videoA && videoB && (
          <span style={{
            marginLeft: 'auto', fontSize: 11, fontFamily: 'var(--mono)',
            color: 'var(--green)', background: '#00d68f15',
            border: '1px solid #00d68f30', borderRadius: 4, padding: '2px 8px',
          }}>● READY</span>
        )}
      </header>

      {/* URL Input */}
      <div style={{
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
        padding: '12px 24px',
      }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <label style={{ fontSize: 10, fontFamily: 'var(--mono)', color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
              VIDEO A — YouTube URL
            </label>
            <input
              value={urlA}
              onChange={e => setUrlA(e.target.value)}
              placeholder="https://youtube.com/watch?v=..."
              style={{
                width: '100%', background: 'var(--bg)', border: '1px solid var(--border)',
                borderRadius: 6, padding: '8px 12px', color: 'var(--text)', fontSize: 12,
                fontFamily: 'var(--mono)',
              }}
            />
          </div>
          <div style={{ flex: 1, minWidth: 200 }}>
            <label style={{ fontSize: 10, fontFamily: 'var(--mono)', color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
              VIDEO B — Instagram Reel URL
            </label>
            <input
              value={urlB}
              onChange={e => setUrlB(e.target.value)}
              placeholder="https://instagram.com/reel/..."
              style={{
                width: '100%', background: 'var(--bg)', border: '1px solid var(--border)',
                borderRadius: 6, padding: '8px 12px', color: 'var(--text)', fontSize: 12,
                fontFamily: 'var(--mono)',
              }}
            />
          </div>
          <button
            onClick={ingest}
            disabled={loading}
            className="ingest-btn"
            style={{
              background: 'linear-gradient(135deg, var(--accent), #8b7cf8)',
              color: '#fff', border: 'none', borderRadius: 6,
              padding: '9px 22px', fontWeight: 700, fontSize: 13,
              cursor: loading ? 'not-allowed' : 'pointer',
              fontFamily: 'var(--sans)', whiteSpace: 'nowrap',
              opacity: loading ? 0.7 : 1,
              display: 'flex', alignItems: 'center', gap: 8,
            }}
          >
            {loading ? (
              <><span style={{ display: 'inline-block', width: 12, height: 12, border: '2px solid #ffffff40', borderTopColor: '#fff', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />{loadingMsg || 'Processing…'}</>
            ) : '⚡ Ingest & Embed'}
          </button>
        </div>
        {error && (
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--accent2)', fontFamily: 'var(--mono)' }}>
            ⚠ {error}
          </div>
        )}
      </div>

      {/* Main content */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>

        {/* Video Cards panel */}
        {(videoA || videoB) && (
          <div style={{
            width: 420, flexShrink: 0,
            borderRight: '1px solid var(--border)',
            overflowY: 'auto', padding: 14,
            display: 'flex', flexDirection: 'column', gap: 14,
          }}>
            {videoA && <VideoCardUI card={videoA} label="A" />}
            {videoB && <VideoCardUI card={videoB} label="B" />}
          </div>
        )}

        {/* Chat panel */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>

          {/* Messages */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
            {messages.length === 0 && (
              <div style={{ textAlign: 'center', marginTop: 60, color: 'var(--text-muted)' }}>
                <div style={{ fontSize: 36, marginBottom: 12 }}>⚡</div>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>VideoRAG Chat</div>
                <div style={{ fontSize: 12, fontFamily: 'var(--mono)' }}>
                  Enter two video URLs above and click Ingest to start.
                </div>
              </div>
            )}
            {messages.map(msg => (
              <div key={msg.id} className="fade-in">
                <MessageBubble msg={msg} />
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>

          {/* Suggestions */}
          {videoA && videoB && messages.length > 0 && (
            <div style={{
              padding: '6px 20px 0',
              display: 'flex', flexWrap: 'wrap', gap: 5,
              borderTop: '1px solid var(--border)',
            }}>
              {SUGGESTIONS.map((s, i) => (
                <button
                  key={i}
                  onClick={() => sendMessage(s)}
                  disabled={chatLoading}
                  className="suggestion-btn"
                  style={{
                    fontSize: 10, fontFamily: 'var(--mono)',
                    background: 'var(--surface)',
                    border: '1px solid var(--border)',
                    borderRadius: 4, padding: '4px 9px',
                    color: 'var(--text-muted)', cursor: 'pointer',
                    transition: 'all 0.15s',
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div style={{
            padding: '10px 20px 16px',
            borderTop: messages.length > 0 ? 'none' : '1px solid var(--border)',
            display: 'flex', gap: 8, alignItems: 'flex-end',
          }}>
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={videoA && videoB ? 'Ask about the videos… (Enter to send)' : 'Ingest videos first to chat'}
              disabled={!videoA || !videoB || chatLoading}
              rows={2}
              style={{
                flex: 1, background: 'var(--surface2)', border: '1px solid var(--border)',
                borderRadius: 8, padding: '9px 13px', color: 'var(--text)', fontSize: 13,
                fontFamily: 'var(--sans)', resize: 'none', lineHeight: 1.5,
              }}
            />
            <button
              onClick={() => sendMessage()}
              disabled={!input.trim() || chatLoading || !videoA || !videoB}
              className="send-btn"
              style={{
                background: 'var(--accent)', color: '#fff', border: 'none',
                borderRadius: 8, padding: '9px 16px', fontWeight: 700, fontSize: 16,
                cursor: 'pointer', transition: 'opacity 0.15s',
                opacity: (!input.trim() || chatLoading || !videoA || !videoB) ? 0.4 : 1,
                height: 40,
              }}
            >
              {chatLoading ? '…' : '↑'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
