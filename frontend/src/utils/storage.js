const SESSIONS_KEY = 'codeseek_sessions';
const GH_TOKEN_KEY = 'gh_token';
const GH_USER_KEY = 'gh_user';

export const getSessions = () => {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    console.warn('[storage] Failed to parse sessions from localStorage — resetting to []');
    return [];
  }
};

export const saveSessions = (sessions) => {
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
  } catch (e) {
    console.error('[storage] Failed to save sessions:', e);
  }
};

export const getGithubToken = () => {
  try {
    return localStorage.getItem(GH_TOKEN_KEY) || null;
  } catch {
    return null;
  }
};

export const setGithubToken = (token) => {
  try {
    localStorage.setItem(GH_TOKEN_KEY, token);
  } catch (e) {
    console.error('[storage] Failed to set gh_token:', e);
  }
};

export const getGithubUser = () => {
  try {
    return localStorage.getItem(GH_USER_KEY) || null;
  } catch {
    return null;
  }
};

export const setGithubUser = (username) => {
  try {
    localStorage.setItem(GH_USER_KEY, username);
  } catch (e) {
    console.error('[storage] Failed to set gh_user:', e);
  }
};

export const clearGithubAuth = () => {
  try {
    localStorage.removeItem(GH_TOKEN_KEY);
    localStorage.removeItem(GH_USER_KEY);
  } catch (e) {
    console.error('[storage] Failed to clear GitHub auth:', e);
  }
};

const API_TOKENS_KEY = 'codeseek_api_tokens';
const PROVIDER_CONFIG_IDS_KEY = 'codeseek_provider_config_ids';
const DEFAULT_PROVIDER = 'groq';

const normalizeApiToken = (token, index = 0) => {
  if (!token || typeof token !== 'object') return null;
  const value =
    (typeof token.key === 'string' && token.key) ||
    (typeof token.token === 'string' && token.token) ||
    '';
  if (!value.trim()) return null;

  const label =
    (typeof token.label === 'string' && token.label.trim()) ||
    (typeof token.description === 'string' && token.description.trim()) ||
    `API Config ${index + 1}`;

  const provider =
    (typeof token.provider === 'string' && token.provider.trim().toLowerCase()) ||
    DEFAULT_PROVIDER;

  return {
    id:
      (typeof token.id === 'string' && token.id.trim()) ||
      `${provider}-${index}-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`,
    key: value,
    label,
    provider,
    isActive: Boolean(token.isActive),
    created_at:
      (typeof token.created_at === 'string' && token.created_at) ||
      new Date().toISOString(),
  };
};

export const getApiTokens = () => {
  try {
    const raw = localStorage.getItem(API_TOKENS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((token, index) => normalizeApiToken(token, index))
      .filter(Boolean);
  } catch {
    console.warn('[storage] Failed to parse API tokens — resetting to []');
    return [];
  }
};

export const saveApiTokens = (tokens) => {
  try {
    const normalized = Array.isArray(tokens)
      ? tokens
          .map((token, index) => normalizeApiToken(token, index))
          .filter(Boolean)
      : [];
    localStorage.setItem(API_TOKENS_KEY, JSON.stringify(normalized));
  } catch (e) {
    console.error('[storage] Failed to save API tokens:', e);
  }
};

export const getActiveApiConfig = () => {
  const tokens = getApiTokens();
  return tokens.find((t) => t.isActive) || null;
};

export const getBackendApiKey = () => import.meta.env.VITE_API_KEY || '';

const getProviderConfigIdsMap = () => {
  try {
    const raw = sessionStorage.getItem(PROVIDER_CONFIG_IDS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
};

const saveProviderConfigIdsMap = (value) => {
  try {
    sessionStorage.setItem(PROVIDER_CONFIG_IDS_KEY, JSON.stringify(value));
  } catch (e) {
    console.error('[storage] Failed to save provider config ids:', e);
  }
};

export const getRegisteredProviderConfigId = (localConfigId) => {
  const map = getProviderConfigIdsMap();
  return typeof map[localConfigId] === 'string' ? map[localConfigId] : '';
};

export const setRegisteredProviderConfigId = (localConfigId, providerConfigId) => {
  const map = getProviderConfigIdsMap();
  map[localConfigId] = providerConfigId;
  saveProviderConfigIdsMap(map);
};

export const clearRegisteredProviderConfigId = (localConfigId) => {
  const map = getProviderConfigIdsMap();
  delete map[localConfigId];
  saveProviderConfigIdsMap(map);
};
