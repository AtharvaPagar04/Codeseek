import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import SourceCard from './SourceCard';

/**
 * Three bouncing dots for the loading state.
 */
function LoadingDots() {
  return (
    <div className="flex items-end gap-1 py-1 px-0.5 h-6">
      <span className="w-1.5 h-1.5 rounded-full bg-text-muted animate-dot-1" />
      <span className="w-1.5 h-1.5 rounded-full bg-text-muted animate-dot-2" />
      <span className="w-1.5 h-1.5 rounded-full bg-text-muted animate-dot-3" />
    </div>
  );
}

/**
 * Custom renderers for react-markdown — enforces our theme inside code blocks.
 */
const markdownComponents = {
  code({ inline, className, children, ...props }) {
    return inline ? (
      <code
        className="font-mono text-text-primary bg-surface-3 px-1.5 py-0.5 rounded-md text-[0.82em] border border-border"
        {...props}
      >
        {children}
      </code>
    ) : (
      <pre className="bg-surface-3 border border-border rounded-xl p-3 my-3 overflow-x-auto">
        <code className="font-mono text-text-primary text-xs leading-relaxed" {...props}>
          {children}
        </code>
      </pre>
    );
  },
  a({ href, children }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="text-text-primary underline decoration-text-muted underline-offset-4 hover:decoration-text-secondary"
      >
        {children}
      </a>
    );
  },
  p({ children }) {
    return <p className="mb-3 last:mb-0 leading-7 text-[0.96rem] text-text-primary/95">{children}</p>;
  },
  h1({ children }) {
    return <h1 className="text-base font-semibold tracking-tight text-text-primary mb-3">{children}</h1>;
  },
  h2({ children }) {
    return <h2 className="text-sm font-semibold tracking-tight text-text-primary mt-5 mb-2">{children}</h2>;
  },
  h3({ children }) {
    return <h3 className="text-sm font-medium text-text-primary mt-4 mb-2">{children}</h3>;
  },
  ul({ children }) {
    return <ul className="mb-3 space-y-2 pl-0">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="mb-3 space-y-2 pl-0">{children}</ol>;
  },
  li({ children, ordered }) {
    return (
      <li className="flex items-start gap-2.5 text-[0.94rem] leading-7 text-text-primary/92">
        <span className="mt-[0.72rem] h-1.5 w-1.5 shrink-0 rounded-full bg-text-muted" />
        <span className="min-w-0">{children}</span>
      </li>
    );
  },
  strong({ children }) {
    return <strong className="font-semibold text-text-primary">{children}</strong>;
  },
  blockquote({ children }) {
    return (
      <blockquote className="my-3 rounded-r-xl border-l-2 border-text-muted bg-surface-3/60 px-3 py-2 text-text-secondary">
        {children}
      </blockquote>
    );
  },
  hr() {
    return <hr className="my-4 border-0 border-t border-border" />;
  },
  table({ children }) {
    return (
      <div className="my-3 overflow-x-auto rounded-xl border border-border">
        <table className="w-full border-collapse text-left text-sm">{children}</table>
      </div>
    );
  },
  thead({ children }) {
    return <thead className="bg-surface-3 text-text-primary">{children}</thead>;
  },
  th({ children }) {
    return <th className="px-3 py-2 font-medium border-b border-border">{children}</th>;
  },
  td({ children }) {
    return <td className="px-3 py-2 align-top border-b border-border last:border-b-0">{children}</td>;
  },
};

export default function MessageBubble({ message }) {
  const isUser = message.role === 'user';
  const [copied, setCopied] = useState(false);

  const handleCopyResponse = () => {
    const text = typeof message.content === 'string' ? message.content.trim() : '';
    if (!text) return;
    const sourceLines = Array.isArray(message.sources)
      ? message.sources
          .map((src) => {
            const file = src.file || src.relative_path || '';
            const symbol = src.symbol || src.symbol_name || '';
            const lines = src.lines || formatLines(src.start_line, src.end_line);
            return `${file}${symbol ? ` :: ${symbol}` : ''}${lines ? ` (lines ${lines})` : ''}`;
          })
          .filter(Boolean)
      : [];
    const fullText = sourceLines.length > 0 ? `${text}\n\nSources:\n${sourceLines.join('\n')}` : text;
    navigator.clipboard.writeText(fullText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  if (isUser) {
    return (
      <div className="flex justify-end animate-fadeIn">
        <div className="max-w-[75%]">
          <div className="bg-surface-3 border border-border rounded-2xl px-4 py-3 text-text-primary text-sm whitespace-pre-wrap break-words">
            {message.content}
          </div>
          <div className="text-2xs text-text-muted text-right mt-1 pr-0.5">
            {formatTimestamp(message.timestamp)}
          </div>
        </div>
      </div>
    );
  }

  // Assistant — loading state
  if (message.loading) {
    return (
      <div className="flex justify-start animate-fadeIn">
        <div className="max-w-[75%] bg-surface-2 border border-border rounded-2xl px-4 py-3">
          <LoadingDots />
        </div>
      </div>
    );
  }

  // Assistant — error state
  if (message.error) {
    return (
      <div className="flex justify-start animate-fadeIn">
        <div className="max-w-[75%]">
          <div className="bg-surface-2 border border-offline/30 rounded-2xl px-4 py-3 text-offline/80 text-sm">
            ⚠ {message.content}
          </div>
          <div className="text-2xs text-text-muted mt-1 pl-0.5">
            {formatTimestamp(message.timestamp)}
          </div>
        </div>
      </div>
    );
  }

  // Assistant — normal answer
  return (
    <div className="flex justify-start animate-fadeIn">
      <div className="max-w-[80%] min-w-0">
        <div className="overflow-hidden rounded-2xl border border-border bg-surface-2">
          <div className="flex items-center justify-between gap-3 border-b border-border bg-surface-3/50 px-4 py-2.5">
            <div className="flex items-center gap-2 min-w-0">
              <span className="inline-flex h-2 w-2 shrink-0 rounded-full bg-online" />
              <span className="text-2xs font-mono uppercase tracking-[0.24em] text-text-secondary">
                Response
              </span>
            </div>

            <div className="flex items-center gap-2 text-2xs text-text-muted shrink-0">
              <button
                onClick={handleCopyResponse}
                className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-3 px-2 py-0.5 font-mono text-text-secondary transition-colors hover:border-text-muted hover:text-text-primary"
                title="Copy response"
                aria-label="Copy response"
              >
                <CopyIcon />
                {copied ? 'Copied' : 'Copy'}
              </button>
              {message.context_tokens != null && (
                <span
                  className="rounded-full border border-border bg-surface-3 px-2 py-0.5 font-mono"
                  title="Context tokens used"
                >
                  {message.context_tokens} tok
                </span>
              )}
              <span>{formatTimestamp(message.timestamp)}</span>
            </div>
          </div>

          <div className="px-4 py-3.5 text-text-primary text-sm">
            <div className="assistant-response prose-sm max-w-none text-text-primary">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {message.content}
            </ReactMarkdown>
            </div>
          </div>

          {message.sources && message.sources.length > 0 && (
            <div className="border-t border-border bg-surface-3/30 px-4 py-3">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div className="text-2xs text-text-muted uppercase tracking-[0.22em]">
                  Sources
                </div>
                <div className="rounded-full border border-border bg-surface-3 px-2 py-0.5 text-2xs font-mono text-text-muted">
                  {message.sources.length}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {message.sources.map((src, i) => (
                  <SourceCard key={i} source={src} />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatTimestamp(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function CopyIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M4 2.5A1.5 1.5 0 0 1 5.5 1h6A1.5 1.5 0 0 1 13 2.5v8A1.5 1.5 0 0 1 11.5 12h-6A1.5 1.5 0 0 1 4 10.5v-8zm1.5-.5a.5.5 0 0 0-.5.5v8a.5.5 0 0 0 .5.5h6a.5.5 0 0 0 .5-.5v-8a.5.5 0 0 0-.5-.5h-6z" />
      <path d="M2.5 4A1.5 1.5 0 0 0 1 5.5v8A1.5 1.5 0 0 0 2.5 15h6a1.5 1.5 0 0 0 1.415-1H8.5a2.5 2.5 0 0 1-2.5-2.5V4H2.5z" />
    </svg>
  );
}
