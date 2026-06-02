import { useState, useCallback } from 'react';
import {
  getGithubToken,
  setGithubToken,
  getGithubUser,
  setGithubUser,
  clearGithubAuth,
} from '../utils/storage';
import { fetchUserRepos, fetchGithubUser } from '../utils/github';

const CLIENT_ID = import.meta.env.VITE_GITHUB_CLIENT_ID || '';
const REDIRECT_URI = import.meta.env.VITE_REDIRECT_URI || `${window.location.origin}/auth/callback`;

export function useGitHub() {
  const [token, setToken] = useState(() => getGithubToken());
  const [username, setUsername] = useState(() => getGithubUser());
  const [avatarUrl, setAvatarUrl] = useState(null);
  const [repos, setRepos] = useState([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [reposError, setReposError] = useState(null);

  const isConnected = !!token;

  const initiateOAuth = useCallback(() => {
    const params = new URLSearchParams({
      client_id: CLIENT_ID,
      redirect_uri: REDIRECT_URI,
      scope: 'repo',
    });
    const url = `https://github.com/login/oauth/authorize?${params}`;
    window.open(url, '_blank', 'noopener,noreferrer');
  }, []);

  /**
   * Called from AuthCallback after receiving token from backend.
   * Persists token + fetches user profile.
   */
  const storeAuth = useCallback(async (accessToken, usernameOverride) => {
    setGithubToken(accessToken);
    setToken(accessToken);

    if (usernameOverride) {
      setGithubUser(usernameOverride);
      setUsername(usernameOverride);
      return;
    }

    // Fetch user from GitHub API
    try {
      const user = await fetchGithubUser(accessToken);
      setGithubUser(user.login);
      setUsername(user.login);
      setAvatarUrl(user.avatar_url);
    } catch (err) {
      console.error('[useGitHub] Failed to fetch user profile:', err);
    }
  }, []);

  const fetchRepos = useCallback(async () => {
    if (!token) return;
    setReposLoading(true);
    setReposError(null);
    try {
      const data = await fetchUserRepos(token);
      setRepos(data);
    } catch (err) {
      console.error('[useGitHub] fetchRepos error:', err);
      setReposError(err.message || 'Could not load repositories. Check your GitHub connection.');
    } finally {
      setReposLoading(false);
    }
  }, [token]);

  const disconnect = useCallback(() => {
    clearGithubAuth();
    setToken(null);
    setUsername(null);
    setAvatarUrl(null);
    setRepos([]);
  }, []);

  return {
    isConnected,
    token,
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
