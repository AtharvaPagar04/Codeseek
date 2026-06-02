import {
  clearRegisteredProviderConfigId,
  getActiveApiConfig,
  getBackendApiKey,
  getRegisteredProviderConfigId,
  setRegisteredProviderConfigId,
} from './storage';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const authHeaders = () => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${getBackendApiKey()}`,
});

const activeProviderConfig = () => {
  const active = getActiveApiConfig();
  if (!active?.provider || !active?.key) {
    throw new Error('No active LLM provider configuration selected.');
  }
  return active;
};

const registerProviderConfig = async (config) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/provider-configs`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          provider: config.provider,
          api_key: config.key,
          label: config.label || '',
        }),
      }),
    'Provider config register'
  );

  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body.detail || body.message || '';
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`Provider config register failed (${res.status})${detail ? `: ${detail}` : ''}`);
  }

  const data = await res.json();
  return data.provider_config?.id || '';
};

const ensureProviderConfigId = async () => {
  const active = activeProviderConfig();
  const cached = getRegisteredProviderConfigId(active.id);
  if (cached) return cached;

  const providerConfigId = await registerProviderConfig(active);
  if (!providerConfigId) {
    throw new Error('Provider config registration did not return an id.');
  }
  setRegisteredProviderConfigId(active.id, providerConfigId);
  return providerConfigId;
};

const isExpiredProviderConfigError = (message) =>
  message.includes('provider_config_id is invalid or expired');

const sendQuery = async (body) => {
  const active = activeProviderConfig();
  const doRequest = async (providerConfigId) => {
    const res = await fetch(`${API_BASE}/api/v1/query`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ ...body, provider_config_id: providerConfigId }),
    });

    if (!res.ok) {
      let detail = '';
      try {
        const parsed = await res.json();
        detail = parsed.detail || parsed.message || '';
      } catch {
        detail = await res.text().catch(() => '');
      }
      throw new Error(`Query failed (${res.status})${detail ? `: ${detail}` : ''}`);
    }

    return res.json();
  };

  let providerConfigId = await ensureProviderConfigId();
  try {
    return await doRequest(providerConfigId);
  } catch (err) {
    if (!isExpiredProviderConfigError(err.message || '')) {
      throw err;
    }
    clearRegisteredProviderConfigId(active.id);
    providerConfigId = await ensureProviderConfigId();
    return doRequest(providerConfigId);
  }
};

const withNetworkError = async (fn, label) => {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error(`${label} failed: backend unreachable at ${API_BASE}`);
    }
    throw err;
  }
};

/**
 * POST /api/v1/query
 * Sends a question for a specific repo and returns the answer + sources.
 */
export const queryRepo = async ({ question, repo_id }) => {
  return sendQuery({ question, repo_id, tenant_id: 'default' });
};

export const querySession = async ({ question, session_id }) => {
  return sendQuery({ question, session_id });
};

export const createSession = async ({ repoFullName, repoUrl, tenantId = 'local', githubToken = '' }) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/sessions`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          repo_full_name: repoFullName,
          repo_url: repoUrl,
          tenant_id: tenantId,
          github_token: githubToken,
        }),
      }),
    'Session create'
  );

  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body.detail || body.message || '';
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`Session create failed (${res.status})${detail ? `: ${detail}` : ''}`);
  }

  const data = await res.json();
  return data.session;
};

export const listSessions = async () => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/sessions`, {
        headers: authHeaders(),
      }),
    'List sessions'
  );
  if (!res.ok) throw new Error(`List sessions failed (${res.status})`);
  const data = await res.json();
  return data.sessions || [];
};

export const deleteSessionApi = async (sessionId) => {
  const res = await fetch(`${API_BASE}/api/v1/sessions/${sessionId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Delete session failed (${res.status})`);
  return res.json();
};

export const fetchSessionMessages = async (sessionId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/sessions/${sessionId}/messages`, {
        headers: authHeaders(),
      }),
    'Fetch session messages'
  );
  if (!res.ok) throw new Error(`Fetch session messages failed (${res.status})`);
  const data = await res.json();
  return data.messages || [];
};

export const clearSessionMessagesApi = async (sessionId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/sessions/${sessionId}/messages`, {
        method: 'DELETE',
        headers: authHeaders(),
      }),
    'Clear session messages'
  );
  if (!res.ok) throw new Error(`Clear session messages failed (${res.status})`);
  return res.json();
};

/**
 * GET /api/v1/health
 * Returns true if backend is alive, false otherwise.
 */
export const fetchHealth = async () => {
  try {
    const res = await fetch(`${API_BASE}/api/v1/health`, {
      headers: { Authorization: `Bearer ${getBackendApiKey()}` },
    });
    return res.ok;
  } catch {
    return false;
  }
};

/**
 * POST /auth/github
 * Exchange GitHub OAuth code for an access token via the backend.
 * Returns { access_token, username }.
 */
export const exchangeGithubCode = async (code) => {
  const res = await fetch(`${API_BASE}/auth/github`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });

  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body.detail || body.message || '';
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`GitHub auth failed (${res.status})${detail ? `: ${detail}` : ''}`);
  }

  return res.json();
};
