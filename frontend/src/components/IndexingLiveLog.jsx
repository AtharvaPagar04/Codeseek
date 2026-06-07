import { useState, useEffect, useRef } from 'react';

const API_BASE = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000';

/**
 * IndexingLiveLog — shows a real-time activity log during session indexing.
 *
 * Props:
 *   sessionId  — the session being indexed
 *   isIndexing — whether the session is currently indexing
 */
export default function IndexingLiveLog({ sessionId, isIndexing }) {
  const [events, setEvents] = useState([]);
  const [sseStatus, setSseStatus] = useState('idle'); // idle | connected | disconnected
  const bottomRef = useRef(null);
  const sseRef = useRef(null);
  const retryTimer = useRef(null);

  // Fetch existing events on mount / when session changes.
  useEffect(() => {
    if (!sessionId) return;
    fetch(`${API_BASE}/api/v1/sessions/${sessionId}/indexing-events`, {
      credentials: 'include',
    })
      .then((res) => (res.ok ? res.json() : { events: [] }))
      .then((data) => {
        if (data.events?.length) {
          setEvents((prev) => dedup([...prev, ...data.events]));
        }
      })
      .catch(() => {});
  }, [sessionId]);

  // SSE subscription while indexing.
  useEffect(() => {
    if (!sessionId || !isIndexing) {
      closeSse();
      return;
    }
    openSse();
    return () => closeSse();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, isIndexing]);

  function openSse() {
    closeSse();
    // EventSource doesn't send cookies by default in all browsers.
    // Use fetch-based streaming as a more reliable cross-origin approach.
    const ctrl = new AbortController();
    sseRef.current = ctrl;
    setSseStatus('connected');

    (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/sessions/${sessionId}/indexing-events/stream`,
          { credentials: 'include', signal: ctrl.signal },
        );
        if (!res.ok || !res.body) {
          setSseStatus('disconnected');
          scheduleRetry();
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const evt = JSON.parse(line.slice(6));
                setEvents((prev) => dedup([...prev, evt]));
              } catch {
                // ignore malformed
              }
            }
          }
        }
        // Stream ended naturally (complete/failed).
        setSseStatus('idle');
      } catch (err) {
        if (err.name === 'AbortError') return;
        setSseStatus('disconnected');
        scheduleRetry();
      }
    })();
  }

  function closeSse() {
    sseRef.current?.abort();
    sseRef.current = null;
    clearTimeout(retryTimer.current);
  }

  function scheduleRetry() {
    clearTimeout(retryTimer.current);
    retryTimer.current = setTimeout(() => {
      if (isIndexing) openSse();
    }, 2000);
  }

  // Auto-scroll on new events.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length]);

  if (events.length === 0 && !isIndexing) return null;

  const latest = events[events.length - 1];
  const terminalStage = latest?.stage === 'complete' || latest?.stage === 'failed';

  return (
    <div
      className="w-full max-w-xl mb-4 rounded-xl border border-border bg-surface-2/60 overflow-hidden"
      style={{ backdropFilter: 'blur(6px)' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border/50">
        <span className="text-2xs font-mono font-medium text-text-secondary tracking-wide uppercase">
          {terminalStage ? 'Indexing Log' : 'Indexing…'}
        </span>
        <StatusDot status={latest?.level || 'info'} />
      </div>

      {/* Event log */}
      <div className="max-h-32 overflow-y-auto px-4 py-2 space-y-1 scrollbar-thin">
        {events.map((evt) => (
          <EventLine key={evt.id} event={evt} />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Progress bar for latest progress */}
      {latest?.progress != null && latest?.total > 0 && !terminalStage && (
        <div className="px-4 pb-2">
          <div className="w-full h-1 rounded-full bg-surface-3 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{
                width: `${Math.min(100, Math.round((latest.progress / latest.total) * 100))}%`,
                background: 'linear-gradient(90deg, #22c55e, #3b82f6)',
              }}
            />
          </div>
          <div className="text-right text-[10px] text-text-muted mt-0.5 font-mono">
            {latest.progress}/{latest.total}
          </div>
        </div>
      )}

      {/* SSE disconnected notice */}
      {sseStatus === 'disconnected' && isIndexing && (
        <div className="px-4 pb-2 text-[10px] text-warning font-mono">
          Live updates disconnected. Retrying…
        </div>
      )}
    </div>
  );
}

function EventLine({ event }) {
  const icon = levelIcon(event.level);
  const color = levelColor(event.level);
  return (
    <div className="flex items-start gap-2 text-xs leading-relaxed font-mono">
      <span className={`shrink-0 mt-0.5 ${color}`}>{icon}</span>
      <span className="text-text-secondary">{event.message}</span>
    </div>
  );
}

function StatusDot({ status }) {
  const bg =
    status === 'success'
      ? 'bg-online'
      : status === 'error'
        ? 'bg-offline'
        : status === 'warning'
          ? 'bg-warning'
          : 'bg-text-muted';
  const pulse = status === 'info' ? 'animate-pulse' : '';
  return <span className={`inline-block w-2 h-2 rounded-full ${bg} ${pulse}`} />;
}

function levelIcon(level) {
  if (level === 'success') return '✓';
  if (level === 'error') return '✗';
  if (level === 'warning') return '⚠';
  return '•';
}

function levelColor(level) {
  if (level === 'success') return 'text-online';
  if (level === 'error') return 'text-offline';
  if (level === 'warning') return 'text-warning';
  return 'text-text-muted';
}

function dedup(events) {
  const seen = new Set();
  const out = [];
  for (const e of events) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    out.push(e);
  }
  return out.sort((a, b) => a.id - b.id);
}
