import { useState, useEffect, useRef } from 'react';

export default function RepoPickerModal({
  isConnected,
  repos,
  reposLoading,
  reposError,
  onSelect,
  onClose,
  onConnectGitHub,
  onLoadRepos,
  onSaveToken,
}) {
  const [filter, setFilter] = useState('');
  const [patLoading, setPatLoading] = useState(false);
  const [patError, setPatError] = useState(null);
  const inputRef = useRef(null);
  const overlayRef = useRef(null);

  // Fetch repos on open if connected
  useEffect(() => {
    if (!isConnected) return;
    onLoadRepos?.();
  }, [isConnected, onLoadRepos]);

  // Focus search input and handle Escape
  useEffect(() => {
    inputRef.current?.focus();
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const filtered = repos.filter((r) =>
    r.name.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-40 bg-black/60 flex items-start justify-center pt-[10vh]"
      onClick={(e) => e.target === overlayRef.current && onClose()}
    >
      <div className="bg-surface-2 border border-border rounded-2xl w-full max-w-lg mx-4 shadow-xl animate-fadeIn flex flex-col max-h-[75vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
          <span className="text-sm font-medium text-text-primary">New Session</span>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary transition-colors text-lg leading-none">
            ×
          </button>
        </div>

        {!isConnected ? (
          // Not connected state with OAuth button & PAT fallback
          <div className="flex flex-col py-6 px-6 gap-5">
            <div className="flex flex-col items-center text-center gap-3">
              <p className="text-text-secondary text-sm">
                Connect your GitHub account to create sessions.
              </p>
              <button
                onClick={onConnectGitHub}
                className="px-4 py-2 text-sm text-text-primary bg-surface-3 border border-border rounded-xl hover:bg-surface-2 hover:border-text-muted transition-colors font-semibold"
              >
                Connect via GitHub OAuth (New Tab)
              </button>
            </div>

            <div className="flex items-center gap-3 select-none">
              <div className="h-px bg-border flex-1" />
              <span className="text-[10px] text-text-muted font-mono uppercase tracking-wider">or connect via token</span>
              <div className="h-px bg-border flex-1" />
            </div>

            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const tokenInput = e.target.elements.pat.value.trim();
                if (!tokenInput) return;
                setPatLoading(true);
                setPatError(null);
                try {
                  await onSaveToken(tokenInput);
                } catch (err) {
                  setPatError(err.message || 'Invalid GitHub token.');
                } finally {
                  setPatLoading(false);
                }
              }}
              className="flex flex-col gap-3"
            >
              <div className="flex flex-col gap-1.5">
                <label htmlFor="pat" className="text-[10px] font-mono text-text-secondary uppercase tracking-wider">
                  Personal Access Token (PAT)
                </label>
                <input
                  id="pat"
                  name="pat"
                  type="password"
                  placeholder="ghp_..."
                  required
                  className="bg-surface-3 border border-border rounded-lg px-3 py-1.5 text-sm text-text-primary placeholder-text-muted font-mono focus:outline-none focus:border-text-muted transition-colors"
                />
              </div>

              {patError && (
                <p className="text-xs text-offline/90 font-mono">⚠ {patError}</p>
              )}

              <button
                type="submit"
                disabled={patLoading}
                className="w-full py-2 bg-text-primary hover:bg-text-secondary text-base rounded-lg text-sm font-medium disabled:opacity-50 transition-colors font-semibold"
                style={{ color: '#0a0a0a' }}
              >
                {patLoading ? 'Verifying Token...' : 'Connect with Token'}
              </button>
              
              <p className="text-[10px] text-text-muted leading-normal">
                Use either a classic PAT with the <code className="text-text-primary bg-surface-3 px-1 py-0.5 rounded font-mono">repo</code> scope or a fine-grained PAT with repository access granted to the repos you want to index.
              </p>
            </form>
          </div>
        ) : (
          <>
            {/* Search */}
            <div className="px-3 py-2 border-b border-border shrink-0">
              <input
                ref={inputRef}
                type="text"
                placeholder="Filter repositories…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="w-full bg-surface-3 border border-border rounded-lg px-3 py-1.5 text-sm text-text-primary placeholder-text-muted font-mono focus:outline-none focus:border-text-muted transition-colors"
              />
            </div>

            {/* Body */}
            <div className="overflow-y-auto flex-1">
              {reposLoading && (
                <div className="flex flex-col gap-2 p-3">
                  {[...Array(5)].map((_, i) => (
                    <div key={i} className="h-12 bg-surface-3 rounded-xl animate-pulse" />
                  ))}
                </div>
              )}

              {reposError && (
                <div className="p-4 text-sm text-offline/80 text-center">{reposError}</div>
              )}

              {!reposLoading && !reposError && filtered.length === 0 && (
                <div className="p-4 text-sm text-text-muted text-center">
                  {filter ? `No repos matching "${filter}"` : 'No repositories found.'}
                </div>
              )}

              {!reposLoading && !reposError && filtered.map((repo) => (
                <button
                  key={repo.id}
                  onClick={() => onSelect(repo)}
                  className="w-full text-left px-4 py-3 hover:bg-surface-3 transition-colors border-b border-border/40 last:border-0"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-sm text-text-primary font-medium truncate">
                      {repo.name}
                    </span>
                    <span
                      className={`shrink-0 text-2xs px-2 py-0.5 rounded-full border ${
                        repo.private
                          ? 'text-warning border-warning/30 bg-warning/5'
                          : 'text-text-muted border-border'
                      }`}
                    >
                      {repo.private ? 'Private' : 'Public'}
                    </span>
                  </div>
                  {repo.description && (
                    <div className="text-xs text-text-muted mt-0.5 truncate">{repo.description}</div>
                  )}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
