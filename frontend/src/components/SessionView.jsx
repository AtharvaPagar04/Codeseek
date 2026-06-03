import { useState, useEffect, useRef } from 'react';
import MessageBubble from './MessageBubble';
import EmptyState from './EmptyState';
import ConfirmDialog from './ConfirmDialog';
import { useChat } from '../hooks/useChat';

export default function SessionView({
  session,
  appendMessage,
  onClearMessages,
}) {
  const [input, setInput] = useState('');
  const [confirmClear, setConfirmClear] = useState(false);
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  const { isLoading, sendMessage } = useChat({ appendMessage });
  const isReady = session.status === 'ready';
  const activeThread =
    session.threads?.find((thread) => thread.id === session.active_thread_id) ||
    session.threads?.[0] ||
    null;
  const canChat = isReady && !!activeThread;

  // Auto-scroll when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activeThread?.messages]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || isLoading || !canChat) return;
    setInput('');
    sendMessage(session, text);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Auto-resize textarea up to ~3 lines
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 84) + 'px';
  }, [input]);

  const hasMessages = (activeThread?.messages || []).length > 0;

  return (
    <div className="flex flex-col h-full min-w-0 relative">
      {/* Floating clear-chat button — top-right corner */}
      {(activeThread?.messages || []).length > 0 && (
        <button
          onClick={() => setConfirmClear(true)}
          title="Clear chat"
          className="absolute top-3 right-4 z-10 w-8 h-8 flex items-center justify-center rounded-full bg-surface-2 border border-border text-text-muted hover:text-warning hover:border-warning/40 transition-all duration-150"
          aria-label="Clear chat"
        >
          <ClearIcon />
        </button>
      )}

      {/* Message list or empty state */}
      {!hasMessages ? (
        <div className="flex-1 flex flex-col items-center justify-center px-5 min-h-0">
          <EmptyState
            repoName={session.repo_id}
          />
          {/* Input bar inline below empty state */}
          <div className="w-full max-w-xl mt-8">
            <div
              className="flex items-center gap-2 px-4 py-1.5 rounded-2xl border border-border bg-surface-2 shadow-lg transition-colors focus-within:border-text-muted"
              style={{ boxShadow: '0 0 20px rgba(0, 0, 0, 0.5), 0 0 2px rgba(255, 255, 255, 0.03)' }}
            >
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isLoading || !canChat}
                placeholder={`Ask about ${session.repo_id}…`}
                rows={1}
                className="flex-1 resize-none bg-transparent border-none text-sm text-text-primary placeholder-text-muted font-sans focus:outline-none disabled:opacity-50 leading-normal"
                style={{ minHeight: '24px', maxHeight: '84px' }}
              />
              <button
                onClick={handleSend}
                disabled={isLoading || !input.trim() || !canChat}
                title="Send (Enter)"
                className="shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-text-primary text-base hover:bg-text-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150"
                style={{ color: '#0a0a0a' }}
              >
                {isLoading ? <SpinnerIcon /> : <SendIcon />}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4 min-h-0" style={{ paddingBottom: '100px' }}>
            {(activeThread?.messages || []).map((msg) => (
              <MessageBubble key={msg.id} message={msg} />
            ))}
            <div ref={bottomRef} />
          </div>

          {/* Floating input bar — only when messages exist */}
          <div className="absolute bottom-0 left-0 right-0 px-4 pb-2 pt-6 pointer-events-none"
               style={{ background: 'linear-gradient(to top, #0a0a0a 50%, transparent)' }}>
            <div className="pointer-events-auto max-w-xl mx-auto">
              <div
                className="flex items-center gap-2 px-4 py-1.5 rounded-2xl border border-border bg-surface-2 shadow-lg transition-colors focus-within:border-text-muted"
                style={{ boxShadow: '0 0 20px rgba(0, 0, 0, 0.5), 0 0 2px rgba(255, 255, 255, 0.03)' }}
              >
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={isLoading || !canChat}
                  placeholder={`Ask about ${session.repo_id}…`}
                  rows={1}
                  className="flex-1 resize-none bg-transparent border-none text-sm text-text-primary placeholder-text-muted font-sans focus:outline-none disabled:opacity-50 leading-normal"
                  style={{ minHeight: '24px', maxHeight: '84px' }}
                />
                <button
                  onClick={handleSend}
                  disabled={isLoading || !input.trim() || !canChat}
                  title="Send (Enter)"
                  className="shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-text-primary text-base hover:bg-text-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150"
                  style={{ color: '#0a0a0a' }}
                >
                  {isLoading ? <SpinnerIcon /> : <SendIcon />}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {confirmClear && (
        <ConfirmDialog
          message="Clear this chat? The repo session will remain available."
          confirmLabel="Clear Chat"
          danger={false}
          onConfirm={() => {
            setConfirmClear(false);
            onClearMessages(session.id);
          }}
          onCancel={() => setConfirmClear(false)}
        />
      )}
    </div>
  );
}

function ClearIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M6.5 1a.5.5 0 0 1 .5.5V2h2v-.5a.5.5 0 0 1 1 0V2h1.5a.5.5 0 0 1 0 1H4.707l6.147 6.146a.5.5 0 0 1 0 .708l-2 2a.5.5 0 0 1-.708 0L2 5.707V11.5A2.5 2.5 0 0 0 4.5 14h5a2.5 2.5 0 0 0 2.5-2.5V8.207a.5.5 0 0 1 1 0V11.5A3.5 3.5 0 0 1 9.5 15h-5A3.5 3.5 0 0 1 1 11.5V4.5a.5.5 0 0 1 .854-.354L8.5 10.793l1.293-1.293L3.146 2.854A.5.5 0 0 1 3.5 2H6v-.5a.5.5 0 0 1 .5-.5z" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M15.854.146a.5.5 0 0 1 .11.54l-5.819 14.547a.75.75 0 0 1-1.329.124l-3.178-4.995L.643 7.184a.75.75 0 0 1 .124-1.33L15.314.037a.5.5 0 0 1 .54.11z" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      className="animate-spin"
      aria-hidden="true"
    >
      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
    </svg>
  );
}
