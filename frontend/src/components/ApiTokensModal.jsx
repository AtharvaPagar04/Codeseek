import { useState, useEffect, useRef } from 'react';
import { clearRegisteredProviderConfigId, getApiTokens, saveApiTokens } from '../utils/storage';
import { v4 as uuidv4 } from 'uuid';

const PROVIDER_OPTIONS = [
  { value: 'groq', label: 'Groq' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'openrouter', label: 'OpenRouter' },
];

export default function ApiTokensModal({ onClose }) {
  const [tokens, setTokens] = useState([]);
  const [tokenInput, setTokenInput] = useState('');
  const [labelInput, setLabelInput] = useState('');
  const [providerInput, setProviderInput] = useState(PROVIDER_OPTIONS[0].value);
  const [error, setError] = useState(null);
  const overlayRef = useRef(null);

  // Load tokens on mount
  useEffect(() => {
    setTokens(getApiTokens());
  }, []);

  // Handle Escape key
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const handleAdd = (e) => {
    e.preventDefault();
    setError(null);

    const key = tokenInput.trim();
    const label = labelInput.trim() || `${providerLabel(providerInput)} Config`;
    const provider = providerInput;

    if (!key) {
      setError('Token value cannot be empty.');
      return;
    }

    const isDuplicate = tokens.some((t) => t.key === key && t.provider === provider);
    if (isDuplicate) {
      setError('This provider key is already added.');
      return;
    }

    // If it's the first token added, make it active
    const shouldBeActive = tokens.length === 0 || !tokens.some((t) => t.isActive);

    const newToken = {
      id: uuidv4(),
      key,
      label,
      provider,
      isActive: shouldBeActive,
      created_at: new Date().toISOString(),
    };

    const next = [...tokens, newToken];
    setTokens(next);
    saveApiTokens(next);

    setTokenInput('');
    setLabelInput('');
    setProviderInput(PROVIDER_OPTIONS[0].value);
  };

  const handleSelect = (id) => {
    const next = tokens.map((t) => ({
      ...t,
      isActive: t.id === id,
    }));
    setTokens(next);
    saveApiTokens(next);
  };

  const handleDelete = (id) => {
    const target = tokens.find((t) => t.id === id);
    const next = tokens.filter((t) => t.id !== id);

    // If we deleted the active one, make the first remaining token active (if any)
    if (target?.isActive && next.length > 0) {
      next[0].isActive = true;
    }

    setTokens(next);
    saveApiTokens(next);
    clearRegisteredProviderConfigId(id);
  };

  const activeToken = tokens.find((t) => t.isActive);

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center pt-[10vh]"
      onClick={(e) => e.target === overlayRef.current && onClose()}
    >
      <div className="bg-surface-2 border border-border rounded w-full max-w-lg mx-4 shadow-xl animate-fadeIn flex flex-col max-h-[75vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <span className="text-sm font-medium text-text-primary flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-accent" />
            LLM Provider Configurations
          </span>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>

        {/* Content list */}
        <div className="overflow-y-auto flex-1 p-4 space-y-4">
          <div>
            <h3 className="text-2xs font-mono text-text-secondary uppercase tracking-wider mb-2">
              Active Configuration
            </h3>
            {tokens.length === 0 ? (
              <div className="bg-surface-3 border border-border rounded p-3 text-xs text-text-secondary font-mono leading-relaxed">
                <span className="text-warning">⚠️ No custom configurations added.</span>
                <p className="mt-1 text-text-muted text-[11px]">
                  Query requests will use the currently selected provider key from this list.
                </p>
              </div>
            ) : (
              <div className="bg-accent-glow/20 border border-accent/30 rounded p-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-medium text-sm text-text-primary truncate">
                    {activeToken?.label}
                  </div>
                  <div className="text-2xs text-text-muted mt-0.5 uppercase tracking-wide">
                    {providerLabel(activeToken?.provider)}
                  </div>
                  <div className="font-mono text-xs text-accent mt-0.5 truncate">
                    Key {activeToken?.key.slice(0, 12)}...{activeToken?.key.slice(-4)}
                  </div>
                </div>
                <span className="shrink-0 text-2xs bg-accent/15 text-accent border border-accent/30 px-1.5 py-0.5 rounded font-mono font-medium">
                  Active
                </span>
              </div>
            )}
          </div>

          {tokens.length > 0 && (
            <div>
              <h3 className="text-2xs font-mono text-text-secondary uppercase tracking-wider mb-2">
                Saved Configurations
              </h3>
              <div className="border border-border rounded divide-y divide-border/60 overflow-hidden bg-surface-3">
                {tokens.map((t) => (
                  <div
                    key={t.id}
                    onClick={() => handleSelect(t.id)}
                    className={`p-3 flex items-center justify-between gap-3 cursor-pointer hover:bg-surface-2 transition-colors ${
                      t.isActive ? 'bg-surface-2/40' : ''
                    }`}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <input
                        type="radio"
                        checked={t.isActive}
                        onChange={() => handleSelect(t.id)}
                        className="accent-accent scale-105 shrink-0"
                      />
                      <div className="min-w-0">
                        <div className="font-medium text-sm text-text-primary truncate">
                          {t.label}
                        </div>
                        <div className="text-2xs text-text-muted mt-0.5 uppercase tracking-wide">
                          {providerLabel(t.provider)}
                        </div>
                        <div className="font-mono text-2xs text-text-muted mt-0.5 truncate">
                          {t.key.slice(0, 8)}...{t.key.slice(-4)}
                        </div>
                      </div>
                    </div>

                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(t.id);
                      }}
                      className="shrink-0 text-text-muted hover:text-offline p-1 transition-colors"
                      title="Delete configuration"
                    >
                      <TrashIcon />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Add new token form */}
        <form
          onSubmit={handleAdd}
          className="border-t border-border bg-surface-3/80 p-4 shrink-0 flex flex-col gap-3"
        >
          <h3 className="text-2xs font-mono text-text-secondary uppercase tracking-wider">
            Add Configuration
          </h3>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label htmlFor="token-value" className="text-2xs font-mono text-text-muted uppercase">
                API Key
              </label>
              <input
                id="token-value"
                type="password"
                placeholder="Provider API key"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                required
                className="bg-surface-2 border border-border rounded px-3 py-1.5 text-xs text-text-primary placeholder-text-muted font-mono focus:outline-none focus:border-accent/60 transition-colors"
              />
            </div>

            <div className="flex flex-col gap-1">
              <label htmlFor="token-label" className="text-2xs font-mono text-text-muted uppercase">
                Label
              </label>
              <input
                id="token-label"
                type="text"
                placeholder="e.g. Personal Groq, Personal Gemini"
                value={labelInput}
                onChange={(e) => setLabelInput(e.target.value)}
                className="bg-surface-2 border border-border rounded px-3 py-1.5 text-xs text-text-primary placeholder-text-muted focus:outline-none focus:border-accent/60 transition-colors"
              />
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="token-provider" className="text-2xs font-mono text-text-muted uppercase">
              Provider
            </label>
            <select
              id="token-provider"
              value={providerInput}
              onChange={(e) => setProviderInput(e.target.value)}
              className="bg-surface-2 border border-border rounded px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:border-accent/60 transition-colors"
            >
              {PROVIDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          {error && <p className="text-2xs text-offline/90 font-mono">⚠ {error}</p>}

          <button
            type="submit"
            className="py-2 bg-accent hover:bg-accent-dim text-base rounded text-xs font-semibold font-mono tracking-wider transition-colors"
            style={{ color: '#0d0f11' }}
          >
            + ADD CONFIGURATION
          </button>
        </form>
      </div>
    </div>
  );
}

function providerLabel(provider) {
  return PROVIDER_OPTIONS.find((option) => option.value === provider)?.label || 'Provider';
}

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
      <path d="M5.5 5.5A.5.5 0 0 1 6 6v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm2.5 0a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V6a.5.5 0 0 1 .5-.5zm3 .5a.5.5 0 0 0-1 0v6a.5.5 0 0 0 1 0V6z" />
      <path
        fillRule="evenodd"
        d="M14.5 3a1 1 0 0 1-1 1H13v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V4h-.5a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1H6a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1h3.5a1 1 0 0 1 1 1v1zM4.118 4 4 4.059V13a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4.059L11.882 4H4.118zM2.5 3V2h11v1h-11z"
      />
    </svg>
  );
}
