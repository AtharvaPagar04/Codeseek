import { useState, useCallback, useEffect } from 'react';
import {
  connectGithubToken,
  fetchGithubSessionMe,
  listGithubRepos,
  logoutGithubSession,
} from '../utils/api';

const CLIENT_ID = import.meta.env.VITE_GITHUB_CLIENT_ID || '';
const REDIRECT_URI = import.meta.env.VITE_REDIRECT_URI || `${window.location.origin}/auth/callback`;

export function useGitHub() {
  const [username, setUsername] = useState(null);
  const [avatarUrl, setAvatarUrl] = useState(null);
  const [repos, setRepos] = useState([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [reposError, setReposError] = useState(null);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchGithubSessionMe()
      .then((data) => {
        if (cancelled) return;
        if (!data?.authenticated || !data.user) {
          setIsConnected(false);
          setUsername(null);
          setAvatarUrl(null);
          return;
        }
        setIsConnected(true);
        setUsername(data.user.username || null);
        setAvatarUrl(data.user.avatar_url || null);
      })
      .catch(() => {
        if (cancelled) return;
        setIsConnected(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const initiateOAuth = useCallback(() => {
    const params = new URLSearchParams({
      client_id: CLIENT_ID,
      redirect_uri: REDIRECT_URI,
      scope: 'repo',
    });
    const url = `https://github.com/login/oauth/authorize?${params}`;
    window.open(url, '_blank', 'noopener,noreferrer');
  }, []);

  const storeAuth = useCallback(async (accessToken) => {
    const data = await connectGithubToken(accessToken);
    const nextUsername = data.username || null;
    setIsConnected(true);
    setUsername(nextUsername);
    setAvatarUrl(data.avatar_url || null);
  }, []);

  const fetchRepos = useCallback(async () => {
    if (!isConnected) return;
    setReposLoading(true);
    setReposError(null);
    try {
      const data = await listGithubRepos();
      setRepos(data);
    } catch (err) {
      console.error('[useGitHub] fetchRepos error:', err);
      setReposError(err.message || 'Could not load repositories. Check your GitHub connection.');
    } finally {
      setReposLoading(false);
    }
  }, [isConnected]);

  const disconnect = useCallback(async () => {
    try {
      await logoutGithubSession();
    } catch (err) {
      console.warn('[useGitHub] backend logout failed:', err?.message || err);
    }
    setIsConnected(false);
    setUsername(null);
    setAvatarUrl(null);
    setRepos([]);
  }, []);

  return {
    isConnected,
    username,
    avatarUrl,
    repos,
    reposLoading,
    reposError,
    initiateOAuth,
    storeAuth,
    fetchRepos,
    disconnect,
  };
}
