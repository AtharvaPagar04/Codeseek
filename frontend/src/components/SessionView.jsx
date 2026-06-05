import { useState, useEffect, useRef } from 'react';
import MessageBubble from './MessageBubble';
import EmptyState from './EmptyState';
import ConfirmDialog from './ConfirmDialog';
import { useChat } from '../hooks/useChat';
import { listProviderCredentials } from '../utils/api';

function getProviderFallbackModel(provider) {
  if (provider === 'groq') return 'llama-3.3-70b-versatile';
  if (provider === 'openai') return 'gpt-4o-mini';
  if (provider === 'openrouter') return 'openai/gpt-4o-mini';
  if (provider === 'aicredits') return 'gpt-5.4-mini';
  return 'gemini-2.0-flash';
}

export default function SessionView({
  session,
  appendMessage,
  onClearMessages,
  onRetryIndexing,
}) {
  const [input, setInput] = useState('');
  const [confirmClear, setConfirmClear] = useState(false);
  const [copiedSession, setCopiedSession] = useState(false);
  const [activeProvider, setActiveProvider] = useState(null);
  const [selectedModel, setSelectedModel] = useState('');
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  const fetchActiveProvider = async () => {
    try {
      const creds = await listProviderCredentials();
      const active = creds.find((c) => c.isActive) || null;
      setActiveProvider(active);
    } catch (err) {
      console.warn('Failed to fetch active provider:', err);
    }
  };

  useEffect(() => {
    fetchActiveProvider();
    window.addEventListener('CODESEEK_PROVIDER_CHANGED', fetchActiveProvider);
    return () => {
      window.removeEventListener('CODESEEK_PROVIDER_CHANGED', fetchActiveProvider);
    };
  }, [session.id]);

  useEffect(() => {
    if (!activeProvider) {
      localStorage.removeItem('CODESEEK_ACTIVE_MODEL_OVERRIDE');
      setSelectedModel('');
      return;
    }
    const provider = activeProvider.provider;
    const providerOverride = localStorage.getItem(`CODESEEK_MODEL_OVERRIDE_${provider}`);
    const credentialDefault = activeProvider.model;
    const fallbackDefault = getProviderFallbackModel(provider);

    const resolved = providerOverride || credentialDefault || fallbackDefault;
    setSelectedModel(resolved);
    localStorage.setItem('CODESEEK_ACTIVE_MODEL_OVERRIDE', resolved);
  }, [activeProvider]);

  const handleModelChange = (model) => {
    setSelectedModel(model);
    localStorage.setItem('CODESEEK_ACTIVE_MODEL_OVERRIDE', model);
    if (activeProvider) {
      localStorage.setItem(`CODESEEK_MODEL_OVERRIDE_${activeProvider.provider}`, model);
    }
  };

  const handleCopySession = () => {
    const messages = activeThread?.messages || [];
    if (messages.length === 0) return;

    const formattedMessages = messages
      .map((msg) => {
        const role = msg.role === 'user' ? 'User' : 'CodeSeek';
        const content = typeof msg.content === 'string' ? msg.content.trim() : '';
        
        let meta = '';
        if (msg.role !== 'user') {
          const modelInfo = selectedModel ? `Model: ${selectedModel}` : '';
          const tokenInfo = msg.context_tokens ? `${msg.context_tokens} tokens` : '';
          const parts = [modelInfo, tokenInfo].filter(Boolean);
          if (parts.length > 0) {
            meta = ` (${parts.join(', ')})`;
          }
        }
        
        let text = `### **${role}**${meta}\n\n${content}`;
        
        if (msg.role !== 'user' && msg.sources && msg.sources.length > 0) {
          const sourceLines = msg.sources
            .map((src) => {
              const file = src.file || src.relative_path || '';
              const symbol = src.symbol || src.symbol_name || '';
              
              let lines = src.lines;
              if (!lines && src.start_line) {
                const start = Number(src.start_line);
                const end = Number(src.end_line);
                if (Number.isFinite(start) && start > 0) {
                  if (Number.isFinite(end) && end > 0 && end !== start) {
                    lines = `${start}-${end}`;
                  } else {
                    lines = String(start);
                  }
                }
              }
              
              return `- ${file}${symbol ? ` :: ${symbol}` : ''}${lines ? ` (lines ${lines})` : ''}`;
            })
            .filter(Boolean);
          if (sourceLines.length > 0) {
            text += `\n\n**Sources:**\n${sourceLines.join('\n')}`;
          }
        }
        
        return text;
      })
      .join('\n\n---\n\n');

    const header = `# CodeSeek Session - ${session.repo_id}\n\n`;
    const fullText = header + formattedMessages;

    navigator.clipboard.writeText(fullText).then(() => {
      setCopiedSession(true);
      setTimeout(() => setCopiedSession(false), 2000);
    });
  };

  const { isLoading, sendMessage } = useChat({ appendMessage });
  const isReady = session.status === 'ready';
  const activeThread =
    session.threads?.find((thread) => thread.id === session.active_thread_id) ||
    session.threads?.[0] ||
    null;
  const canChat = isReady && !!activeThread;
  const statusMessage = statusCopy(session);

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
      {/* Floating copy-session and clear-chat buttons — top-right corner */}
      {(activeThread?.messages || []).length > 0 && (
        <>
          <button
            onClick={handleCopySession}
            title={copiedSession ? "Copied!" : "Copy whole session"}
            className="absolute top-3 right-14 z-10 w-8 h-8 flex items-center justify-center rounded-full bg-surface-2 border border-border text-text-muted hover:text-text-primary hover:border-text-muted transition-all duration-150"
            aria-label="Copy whole session"
          >
            {copiedSession ? <CheckIcon /> : <CopyIcon />}
          </button>
          <button
            onClick={() => setConfirmClear(true)}
            title="Clear chat"
            className="absolute top-3 right-4 z-10 w-8 h-8 flex items-center justify-center rounded-full bg-surface-2 border border-border text-text-muted hover:text-warning hover:border-warning/40 transition-all duration-150"
            aria-label="Clear chat"
          >
            <ClearIcon />
          </button>
        </>
      )}

      {/* Message list or empty state */}
      {!hasMessages ? (
        <div className="flex-1 flex flex-col items-center justify-center px-5 min-h-0">
          {statusMessage && (
            <StatusNotice
              tone={session.status === 'failed' ? 'error' : 'info'}
              message={statusMessage}
              actionLabel={session.status === 'failed' ? 'Retry indexing' : ''}
              onAction={session.status === 'failed' ? () => onRetryIndexing?.(session.id) : undefined}
            />
          )}
          <EmptyState
            repoName={session.repo_id}
          />
          {/* Input bar inline below empty state */}
          <div className="w-full max-w-xl mt-8">
            <div
              className="flex items-center gap-2 px-4 py-1.5 rounded-2xl border border-border bg-surface-2 shadow-lg transition-colors focus-within:border-text-muted"
              style={{ boxShadow: '0 0 20px rgba(0, 0, 0, 0.5), 0 0 2px rgba(255, 255, 255, 0.03)' }}
            >
              <ModelSelector activeModel={selectedModel} onChange={handleModelChange} activeProvider={activeProvider} />
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
            {statusMessage && (
              <StatusNotice
                tone={session.status === 'failed' ? 'error' : 'info'}
                message={statusMessage}
                actionLabel={session.status === 'failed' ? 'Retry indexing' : ''}
                onAction={session.status === 'failed' ? () => onRetryIndexing?.(session.id) : undefined}
              />
            )}
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
                <ModelSelector activeModel={selectedModel} onChange={handleModelChange} activeProvider={activeProvider} />
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

function statusCopy(session) {
  if (session.status === 'failed') {
    return session.error
      ? `Indexing failed: ${session.error}. Retry indexing after checking GitHub access and backend logs.`
      : 'Indexing failed. Retry after checking GitHub access and backend logs.';
  }
  if (session.status && session.status !== 'ready') {
    return 'Repository indexing is still running. Questions will be enabled when the session becomes ready.';
  }
  return '';
}

function StatusNotice({ tone, message, actionLabel = '', onAction = null }) {
  const toneClass =
    tone === 'error'
      ? 'border-offline/40 bg-offline/10 text-offline'
      : 'border-warning/40 bg-warning/10 text-warning';
  return (
    <div className={`w-full max-w-xl mb-4 rounded-xl border px-4 py-3 text-xs font-mono leading-relaxed ${toneClass}`}>
      <div className="flex items-start justify-between gap-3">
        <div>{message}</div>
        {actionLabel && onAction && (
          <button
            onClick={onAction}
            className="shrink-0 rounded-full border border-current/30 px-2.5 py-1 text-[10px] uppercase tracking-wide transition-colors hover:bg-black/10"
          >
            {actionLabel}
          </button>
        )}
      </div>
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

const PROVIDER_MODEL_PRESETS = {
  gemini: [
    {
      value: 'gemini-2.0-flash',
      name: 'Gemini 2.0 Flash',
      label: 'Default / Fast load',
      short: '⚡ Flash',
      tooltip: 'Free tier limits: 15 Requests Per Minute (RPM) & 1,000,000 Tokens Per Minute (TPM). High capacity, extremely fast.',
    },
    {
      value: 'gemini-1.5-pro',
      name: 'Gemini 1.5 Pro',
      label: 'Complex queries',
      short: '💎 Pro',
      tooltip: 'Free tier limits: 2 Requests Per Minute (RPM) & 32,000 Tokens Per Minute (TPM). Highly rate-limited; easily triggered on large repositories.',
    },
    {
      value: 'gemini-1.5-flash',
      name: 'Gemini 1.5 Flash',
      label: 'Flash 1.5',
      short: '⚡ Flash (1.5)',
      tooltip: 'Free tier limits: 15 Requests Per Minute (RPM) & 1,000,000 Tokens Per Minute (TPM). Fast and reliable.',
    }
  ],
  groq: [
    {
      value: 'llama-3.3-70b-versatile',
      name: 'Llama 3.3 70B',
      label: 'Default / High quality',
      short: '🦙 Llama 3.3',
      tooltip: 'Versatile 70B model with high rate limits and speed.',
    },
    {
      value: 'llama-3.1-8b-instant',
      name: 'Llama 3.1 8B',
      label: 'Instant replies',
      short: '🦙 Llama 8B',
      tooltip: 'Super fast lightweight model.',
    },
    {
      value: 'mixtral-8x7b-32768',
      name: 'Mixtral 8x7B',
      label: 'Mixtral Mixture of Experts',
      short: '🌀 Mixtral',
      tooltip: 'Good general reasoning model.',
    }
  ],
  openai: [
    {
      value: 'gpt-4o-mini',
      name: 'GPT-4o Mini',
      label: 'Default / Fast & cheap',
      short: '✨ 4o Mini',
      tooltip: 'Very fast, cost-effective model.',
    },
    {
      value: 'gpt-4o',
      name: 'GPT-4o',
      label: 'Advanced intelligence',
      short: '🧠 GPT-4o',
      tooltip: 'High intelligence, premium general model.',
    },
    {
      value: 'gpt-3.5-turbo',
      name: 'GPT-3.5 Turbo',
      label: 'Legacy Fast',
      short: '⚡ GPT-3.5',
      tooltip: 'Standard fast model.',
    }
  ],
  openrouter: [
    {
      value: 'google/gemini-2.0-flash',
      name: 'Gemini 2.0 Flash',
      label: 'Gemini 2.0 Flash via OpenRouter',
      short: '⚡ Gemini',
      tooltip: 'Fast and efficient Gemini 2.0 Flash.',
    },
    {
      value: 'openai/gpt-4o-mini',
      name: 'GPT-4o Mini',
      label: 'GPT-4o Mini via OpenRouter',
      short: '✨ 4o Mini',
      tooltip: 'High-speed, low-cost intelligence.',
    },
    {
      value: 'meta-llama/llama-3-8b-instruct',
      name: 'Llama 3 8B',
      label: 'Llama 3 8B Instruct via OpenRouter',
      short: '🦙 Llama 3',
      tooltip: 'Open-source instruction-tuned model.',
    }
  ],
  aicredits: [
    {
      value: 'gpt-5.4-mini',
      name: 'GPT-5.4 Mini',
      label: 'Default / AI Credits',
      short: '🪙 GPT-5.4',
      tooltip: 'GPT-5.4 Mini via AI Credits. Fast and cost-effective.',
    },
  ],
};

function ModelSelector({ activeModel, onChange, activeProvider }) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);

  useEffect(() => {
    const handleOutsideClick = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setIsOpen(false);
      }
    };
    window.addEventListener('click', handleOutsideClick);
    return () => window.removeEventListener('click', handleOutsideClick);
  }, []);

  if (!activeProvider) {
    return (
      <div className="relative shrink-0 flex items-center">
        <button
          type="button"
          title="No active LLM provider configured. Click to configure API tokens."
          onClick={() => {
            window.dispatchEvent(new Event('CODESEEK_OPEN_API_MODAL'));
          }}
          className="flex items-center gap-1.5 rounded-lg border border-warning/40 bg-warning/10 px-2 py-1 text-2xs font-mono font-medium text-warning hover:bg-warning/20 transition-colors select-none animate-pulse"
        >
          <span>⚠️ Setup API</span>
        </button>
      </div>
    );
  }

  const provider = activeProvider.provider;
  const presets = PROVIDER_MODEL_PRESETS[provider] || [];

  const isPreset = presets.some((p) => p.value === activeModel);
  const current = isPreset
    ? presets.find((p) => p.value === activeModel)
    : {
        value: activeModel,
        name: activeModel || 'Default Model',
        label: 'Active Model',
        short: activeModel
          ? activeModel.length > 12
            ? activeModel.substring(0, 10) + '…'
            : activeModel
          : 'Default',
        tooltip: `Active model: ${activeModel || 'Default model'}`,
      };

  return (
    <div className="relative shrink-0 flex items-center" ref={dropdownRef}>
      <button
        type="button"
        title={`Active model: ${current.name}. Click to switch.`}
        onClick={(e) => {
          e.stopPropagation();
          setIsOpen(!isOpen);
        }}
        className="flex items-center gap-1.5 rounded-lg border border-border bg-surface-3 px-2 py-1 text-2xs font-mono font-medium text-text-secondary hover:text-text-primary hover:border-text-muted transition-colors select-none"
      >
        <span>{current.short}</span>
        <svg
          className={`h-2.5 w-2.5 transform text-text-muted transition-transform duration-150 ${
            isOpen ? 'rotate-180' : ''
          }`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={3}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <div
          className="absolute bottom-full left-0 mb-2 w-52 rounded-xl border border-border bg-surface-2 p-1 shadow-xl animate-fadeIn z-30 flex flex-col"
          style={{ boxShadow: '0 4px 20px rgba(0, 0, 0, 0.4)' }}
        >
          <div className="max-h-48 overflow-y-auto">
            {presets.map((opt) => (
              <button
                key={opt.value}
                type="button"
                title={opt.tooltip}
                onClick={() => {
                  onChange(opt.value);
                  setIsOpen(false);
                }}
                className={`w-full text-left rounded-lg px-2.5 py-1.5 hover:bg-surface-3 transition-colors flex flex-col ${
                  opt.value === activeModel ? 'bg-surface-3/50' : ''
                }`}
              >
                <span className="text-2xs font-medium text-text-primary">{opt.name}</span>
                <span className="text-[10px] text-text-muted">{opt.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CopyIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M4 1.5H3a2 2 0 0 0-2 2V14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V3.5a2 2 0 0 0-2-2h-1v1h1a1 1 0 0 1 1 1V14a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3.5a1 1 0 0 1 1-1h1v-1z" />
      <path d="M9.5 1a.5.5 0 0 1 .5.5v1a.5.5 0 0 1-.5.5h-3a.5.5 0 0 1-.5-.5v-1a.5.5 0 0 1 .5-.5h3zm-3-1A1.5 1.5 0 0 0 5 1.5v1A1.5 1.5 0 0 0 6.5 4h3A1.5 1.5 0 0 0 11 2.5v-1A1.5 1.5 0 0 0 9.5 0h-3z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor" className="text-online" aria-hidden="true">
      <path d="M13.854 3.646a.5.5 0 0 1 0 .708l-7 7a.5.5 0 0 1-.708 0l-3.5-3.5a.5.5 0 1 1 .708-.708L6.5 10.293l6.646-6.647a.5.5 0 0 1 .708 0z" />
    </svg>
  );
}
