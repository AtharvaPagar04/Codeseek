import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { exchangeGithubCode } from '../utils/api';
import { setGithubToken, setGithubUser } from '../utils/storage';

export default function AuthCallback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState(null);

  useEffect(() => {
    const code = searchParams.get('code');

    if (!code) {
      setError('No authorization code received from GitHub. Please try connecting again.');
      return;
    }

    let cancelled = false;

    exchangeGithubCode(code)
      .then(({ access_token, username }) => {
        if (cancelled) return;
        setGithubToken(access_token);
        if (username) setGithubUser(username);
        navigate('/', { replace: true });
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[AuthCallback] Exchange failed:', err);
        setError(err.message || 'GitHub connection failed. Please try again.');
      });

    return () => {
      cancelled = true;
    };
  }, []); // Run once on mount

  if (error) {
    const is404 = error.includes('404');
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-base text-center px-6 gap-5">
        <div className="font-mono text-xs text-text-muted uppercase tracking-widest mb-1">Codeseek</div>
        
        <div className="max-w-md border border-border bg-surface-2 p-6 rounded flex flex-col gap-4">
          <p className="text-offline text-sm font-medium">⚠ {error}</p>
          
          {is404 && (
            <p className="text-text-secondary text-xs leading-relaxed">
              This typically means your Codeseek backend does not have a <code className="text-accent bg-surface-3 px-1 py-0.5 rounded font-mono">/auth/github</code> endpoint configured. 
              You can connect using a GitHub Personal Access Token (PAT) instead.
            </p>
          )}

          {is404 ? (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const tokenInput = e.target.elements.pat.value.trim();
                if (!tokenInput) return;
                try {
                  const { fetchGithubUser } = await import('../utils/github');
                  const user = await fetchGithubUser(tokenInput);
                  setGithubToken(tokenInput);
                  setGithubUser(user.login);
                  navigate('/', { replace: true });
                } catch (err) {
                  alert(err.message || 'Verification failed. Please try a different token.');
                }
              }}
              className="flex flex-col gap-3 text-left mt-2"
            >
              <div className="flex flex-col gap-1.5">
                <label htmlFor="pat-callback" className="text-[10px] font-mono text-text-secondary uppercase tracking-wider">
                  Personal Access Token (PAT)
                </label>
                <input
                  id="pat-callback"
                  name="pat"
                  type="password"
                  placeholder="ghp_..."
                  required
                  className="bg-surface-3 border border-border rounded px-3 py-1.5 text-sm text-text-primary placeholder-text-muted font-mono focus:outline-none focus:border-accent/60 transition-colors"
                />
              </div>
              <button
                type="submit"
                className="w-full py-2 bg-accent hover:bg-accent-dim text-base rounded text-sm font-semibold transition-colors"
                style={{ color: '#0d0f11' }}
              >
                Verify &amp; Connect
              </button>
            </form>
          ) : null}
        </div>

        <a
          href="/"
          className="text-xs text-text-secondary hover:text-text-primary hover:underline transition-colors font-mono"
        >
          &lt; Back to home
        </a>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center h-screen bg-base text-center gap-3">
      <div className="font-mono text-xs text-text-muted uppercase tracking-widest">Codeseek</div>
      <div className="flex items-center gap-2 text-text-secondary text-sm">
        <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
        Connecting to GitHub…
      </div>
    </div>
  );
}
