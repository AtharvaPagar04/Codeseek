import { useState, useEffect, useRef } from 'react';
import {
  activateProviderCredential,
  createProviderCredential,
  deleteProviderCredential,
  listProviderCredentials,
} from '../utils/api';

const PROVIDER_OPTIONS = [
  { value: 'groq', label: 'Groq' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'aicredits', label: 'AI Credits' },
  { value: 'local', label: 'Local LLM' },
];

const PROVIDER_MODELS = {
  local: [
    { value: 'auto', label: 'Auto (3B warmup, 7B on demand)' },
    { value: 'qwen2.5-coder:3b-8k', label: 'Qwen2.5 Coder 3B 8K' },
    { value: 'qwen-coder-7b-8192', label: 'Qwen Coder 7B 8192' },
  ],
  gemini: [
    { value: 'default', label: 'Default (gemini-2.0-flash)' },
    { value: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash' },
    { value: 'gemini-1.5-flash', label: 'Gemini 1.5 Flash' },
    { value: 'gemini-1.5-pro', label: 'Gemini 1.5 Pro' },
  ],
  groq: [
    { value: 'default', label: 'Default (llama-3.3-70b-versatile)' },
    { value: 'llama-3.3-70b-versatile', label: 'Llama 3.3 70B' },
    { value: 'llama-3.1-8b-instant', label: 'Llama 3.1 8B' },
    { value: 'mixtral-8x7b-32768', label: 'Mixtral 8x7B' },
  ],
  openai: [
    { value: 'default', label: 'Default (gpt-4o-mini)' },
    { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
    { value: 'gpt-4o', label: 'GPT-4o' },
    { value: 'gpt-3.5-turbo', label: 'GPT-3.5 Turbo' },
  ],
  openrouter: [
    { value: 'default', label: 'Default (openai/gpt-4o-mini)' },
    { value: 'google/gemini-2.0-flash', label: 'Gemini 2.0 Flash' },
    { value: 'openai/gpt-4o-mini', label: 'GPT-4o Mini' },
    { value: 'meta-llama/llama-3-8b-instruct', label: 'Llama 3 8B' },
  ],
  aicredits: [
    { value: 'default', label: 'Default (gpt-5.4-mini)' },
    { value: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
  ],
};

export default function ApiTokensModal({ onClose }) {
  const [tokens, setTokens] = useState([]);
  const [tokenInput, setTokenInput] = useState('');
  const [labelInput, setLabelInput] = useState('');
  const [providerInput, setProviderInput] = useState(PROVIDER_OPTIONS[0].value);
  const [modelSelect, setModelSelect] = useState('default');
  const [error, setError] = useState(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const overlayRef = useRef(null);
  const formRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    const loadTokens = async () => {
      try {
        const data = await listProviderCredentials();
        if (!cancelled) setTokens(data);
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to load provider configurations.');
      }
    };

    loadTokens();
    return () => {
      cancelled = true;
    };
  }, []);

  // Handle Escape key
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const handleAdd = async (e) => {
    e.preventDefault();
    setError(null);

    const key = tokenInput.trim();
    const label = labelInput.trim() || `${providerLabel(providerInput)} Config`;
    const provider = providerInput;
    const isLocalProvider = provider === 'local';

    if (!key && !isLocalProvider) {
      setError('Token value cannot be empty.');
      return;
    }

    const finalModel = modelSelect === 'default' ? '' : modelSelect;

    const isDuplicate = tokens.some((t) => t.label === label && t.provider === provider);
    if (isDuplicate) {
      setError('A configuration with this label and provider already exists.');
      return;
    }

    const shouldBeActive = tokens.length === 0 || !tokens.some((t) => t.isActive);
    try {
      const created = await createProviderCredential({
        provider,
        label,
        apiKey: key,
        model: finalModel,
        isActive: shouldBeActive,
      });
      setTokens((prev) => {
        const next = shouldBeActive
          ? prev.map((t) => ({ ...t, isActive: false }))
          : [...prev];
        return [...next, created];
      });
      setTokenInput('');
      setLabelInput('');
      setProviderInput(PROVIDER_OPTIONS[0].value);
      setModelSelect('default');
      setShowAddForm(false);
      window.dispatchEvent(new Event('CODESEEK_PROVIDER_CHANGED'));
    } catch (err) {
      setError(err.message || 'Failed to save configuration.');
    }
  };

  const handleSelect = async (id) => {
    try {
      await activateProviderCredential(id);
      setTokens((prev) =>
        prev.map((t) => ({
          ...t,
          isActive: t.id === id,
        }))
      );
      window.dispatchEvent(new Event('CODESEEK_PROVIDER_CHANGED'));
    } catch (err) {
      setError(err.message || 'Failed to activate configuration.');
    }
  };

  const handleDelete = async (id) => {
    const target = tokens.find((t) => t.id === id);
    try {
      await deleteProviderCredential(id);
      setTokens((prev) => {
        const next = prev.filter((t) => t.id !== id);
        if (target?.isActive && next.length > 0) {
          next[0] = { ...next[0], isActive: true };
        }
        return next;
      });
      window.dispatchEvent(new Event('CODESEEK_PROVIDER_CHANGED'));
    } catch (err) {
      setError(err.message || 'Failed to delete configuration.');
    }
  };

  const activeToken = tokens.find((t) => t.isActive);

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center pt-[10vh]"
      onClick={(e) => e.target === overlayRef.current && onClose()}
    >
      <div className="bg-surface-2 border border-border rounded-2xl w-full max-w-lg mx-4 shadow-xl animate-fadeIn flex flex-col max-h-[75vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <span className="text-sm font-medium text-text-primary flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-online" />
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
              <div className="bg-surface-3 border border-border rounded-xl p-3 text-xs text-text-secondary font-mono leading-relaxed">
                <span className="text-warning">⚠️ No custom configurations added.</span>
                <p className="mt-1 text-text-muted text-[11px]">
                  Query requests will use the currently selected provider key from this list.
                </p>
                <p className="mt-2 text-text-muted text-[11px]">
                  Add a provider key before sending queries. If responses later fail with auth or rate-limit errors,
                  return here and update or switch the active credential.
                </p>
              </div>
            ) : (
              <div className="bg-surface-3 border border-border rounded-xl p-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-medium text-sm text-text-primary truncate">
                    {activeToken?.label}
                  </div>
                  <div className="text-2xs text-text-muted mt-0.5 uppercase tracking-wide flex items-center gap-1.5 flex-wrap">
                    <span>{providerLabel(activeToken?.provider)}</span>
                    {activeToken?.model && (
                      <>
                        <span className="w-1 h-1 rounded-full bg-border" />
                        <span className="normal-case font-mono">{activeToken.model}</span>
                      </>
                    )}
                  </div>
                  {activeToken?.provider === 'local' && (
                    <div className="mt-1 flex items-center gap-2 flex-wrap">
                    <span className={`text-2xs font-mono px-2 py-0.5 rounded-full border ${
                        localStatusClass(activeToken)
                      }`}>
                        {localStatusLabel(activeToken)}
                      </span>
                      {activeToken.runtime_detail && (
                        <span className="text-2xs text-text-muted font-mono">
                          {activeToken.runtime_detail}
                        </span>
                      )}
                    </div>
                  )}
                </div>
                <span className="shrink-0 text-2xs bg-online/15 text-online border border-online/30 px-1.5 py-0.5 rounded-full font-mono font-medium">
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
              <div className="border border-border rounded-xl divide-y divide-border/60 overflow-hidden bg-surface-3">
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
                        className="accent-white scale-105 shrink-0"
                      />
                      <div className="min-w-0">
                        <div className="font-medium text-sm text-text-primary truncate">
                          {t.label}
                        </div>
                        <div className="text-2xs text-text-muted mt-0.5 uppercase tracking-wide flex items-center gap-1.5 flex-wrap">
                          <span>{providerLabel(t.provider)}</span>
                          {t.model && (
                            <>
                              <span className="w-1 h-1 rounded-full bg-border" />
                              <span className="normal-case font-mono">{t.model}</span>
                            </>
                          )}
                        </div>
                        {t.provider === 'local' && (
                          <div className="mt-1 flex items-center gap-2 flex-wrap">
                            <span className={`text-2xs font-mono px-2 py-0.5 rounded-full border ${
                              localStatusClass(t)
                            }`}>
                              {localStatusLabel(t)}
                            </span>
                            {t.runtime_detail && (
                              <span className="text-2xs text-text-muted font-mono">
                                {t.runtime_detail}
                              </span>
                            )}
                          </div>
                        )}
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

        {/* Error notification banner */}
        {error && !showAddForm && (
          <div className="bg-offline/10 border-t border-b border-offline/20 px-4 py-2.5 flex items-center justify-between gap-3 animate-fadeIn">
            <p className="text-2xs text-offline/90 font-mono leading-relaxed flex-1">
              ⚠ {error}
            </p>
            <button
              type="button"
              onClick={() => setError(null)}
              className="text-text-muted hover:text-text-primary transition-colors text-sm font-bold leading-none shrink-0"
              title="Dismiss error"
            >
              ×
            </button>
          </div>
        )}

        {/* Add API toggle button / Add new token form */}
        <div className="border-t border-border shrink-0">
          {!showAddForm ? (
            <button
              type="button"
              onClick={() => { setShowAddForm(true); setError(null); }}
              className="w-full py-3 px-4 bg-surface-3/80 hover:bg-surface-2 text-text-primary text-xs font-semibold font-mono tracking-wider transition-colors flex items-center justify-center gap-2 rounded-b-2xl"
            >
              <span className="text-base leading-none">+</span> ADD API
            </button>
          ) : (
            <form
              ref={formRef}
              onSubmit={handleAdd}
              className="bg-surface-3/80 p-4 flex flex-col gap-3 animate-slideDown"
            >
              <div className="flex items-center justify-between">
                <h3 className="text-2xs font-mono text-text-secondary uppercase tracking-wider">
                  Add Configuration
                </h3>
                <button
                  type="button"
                  onClick={() => {
                    setShowAddForm(false);
                    setError(null);
                    setTokenInput('');
                    setLabelInput('');
                    setProviderInput(PROVIDER_OPTIONS[0].value);
                    setModelSelect('default');
                  }}
                  className="text-text-muted hover:text-text-primary transition-colors text-sm leading-none"
                  title="Cancel"
                >
                  ×
                </button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <label htmlFor="token-value" className="text-2xs font-mono text-text-muted uppercase">
                    {providerInput === 'local' ? 'Local Access Token' : 'API Key'}
                  </label>
                  <input
                    id="token-value"
                    type="password"
                    placeholder={providerInput === 'local' ? 'Optional local token' : 'Provider API key'}
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                    required={providerInput !== 'local'}
                    autoFocus
                    className="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-xs text-text-primary placeholder-text-muted font-mono focus:outline-none focus:border-text-muted transition-colors"
                  />
                  {providerInput === 'local' && (
                    <p className="text-[11px] leading-relaxed text-text-muted">
                      Leave empty if your local server does not require an auth token.
                    </p>
                  )}
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
                    className="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-xs text-text-primary placeholder-text-muted focus:outline-none focus:border-text-muted transition-colors"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <label htmlFor="token-provider" className="text-2xs font-mono text-text-muted uppercase">
                    Provider
                  </label>
                  <select
                    id="token-provider"
                    value={providerInput}
                    onChange={(e) => {
                      setProviderInput(e.target.value);
                      setModelSelect(e.target.value === 'local' ? 'auto' : 'default');
                    }}
                    className="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:border-text-muted transition-colors"
                  >
                    {PROVIDER_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex flex-col gap-1">
                  <label htmlFor="token-model" className="text-2xs font-mono text-text-muted uppercase">
                    Model
                  </label>
                  <select
                    id="token-model"
                    value={modelSelect}
                    onChange={(e) => setModelSelect(e.target.value)}
                    className="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:border-text-muted transition-colors"
                  >
                    {(PROVIDER_MODELS[providerInput] || []).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  {providerInput === 'local' && (
                    <p className="text-[11px] leading-relaxed text-text-muted">
                      Auto routes regular queries to Qwen Coder 3B and escalates complex queries to Qwen Coder 7B.
                    </p>
                  )}
                </div>
              </div>



              {error && <p className="text-2xs text-offline/90 font-mono">⚠ {error}</p>}

              <button
                type="submit"
                className="py-2 bg-text-primary hover:bg-text-secondary text-base rounded-lg text-xs font-semibold font-mono tracking-wider transition-colors"
                style={{ color: '#0a0a0a' }}
              >
                + ADD CONFIGURATION
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

function providerLabel(provider) {
  return PROVIDER_OPTIONS.find((option) => option.value === provider)?.label || 'Provider';
}

function localStatusLabel(token) {
  const runtimeStatus = `${token?.runtime_status || ''}`.toLowerCase();
  const selectedStatus = `${token?.runtime_selected_status || ''}`.toLowerCase();
  const primaryStatus = `${token?.runtime_primary_status || ''}`.toLowerCase();
  const selected = token?.runtime_selected_model || token?.model || '';
  if (selected === 'qwen-coder-7b-8192') {
    if (selectedStatus === 'ready') return '7B ready';
    if (selectedStatus === 'loading') return 'Loading 7B';
    if (primaryStatus === 'loading') return 'Warming 3B';
    if (primaryStatus === 'ready') return '3B ready';
    return '7B idle';
  }
  if (runtimeStatus === 'ready') {
    return '3B ready';
  }
  if (runtimeStatus === 'loading' || primaryStatus === 'loading') {
    return 'Warming 3B';
  }
  if (runtimeStatus === 'error' || primaryStatus === 'error') return 'Load failed';
  if (runtimeStatus === 'idle') return 'Idle';
  return 'Unknown';
}

function localStatusClass(token) {
  const runtimeStatus = `${token?.runtime_status || ''}`.toLowerCase();
  const selectedStatus = `${token?.runtime_selected_status || ''}`.toLowerCase();
  const primaryStatus = `${token?.runtime_primary_status || ''}`.toLowerCase();
  const status = primaryStatus === 'loading' ? 'loading' : runtimeStatus;
  if (status === 'ready' || selectedStatus === 'ready') return 'bg-online/15 text-online border-online/30';
  if (status === 'loading' || selectedStatus === 'loading') return 'bg-warning/15 text-warning border-warning/30';
  if (status === 'error' || selectedStatus === 'error' || primaryStatus === 'error') return 'bg-offline/15 text-offline border-offline/30';
  return 'bg-surface-2 text-text-muted border-border';
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
