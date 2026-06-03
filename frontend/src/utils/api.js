import { getBackendApiKey } from './storage';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const authHeaders = () => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${getBackendApiKey()}`,
});

let submissionKeyPromise = null;

const pemToArrayBuffer = (pem) => {
  const base64 = pem
    .replace('-----BEGIN PUBLIC KEY-----', '')
    .replace('-----END PUBLIC KEY-----', '')
    .replace(/\s+/g, '');
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
};

const fetchSubmissionPublicKey = async () => {
  if (!submissionKeyPromise) {
    submissionKeyPromise = withNetworkError(
      async () => {
        const res = await fetch(`${API_BASE}/api/v1/crypto/submission-key`, {
          credentials: 'include',
        });
        if (!res.ok) {
          throw new Error(`Submission key fetch failed (${res.status})`);
        }
        return res.json();
      },
      'Submission key fetch'
    ).catch((err) => {
      submissionKeyPromise = null;
      throw err;
    });
  }
  return submissionKeyPromise;
};

const encryptSecretForSubmission = async (secret) => {
  const value = `${secret || ''}`.trim();
  if (!value) {
    throw new Error('Secret value cannot be empty.');
  }
  if (!window.crypto?.subtle) {
    throw new Error('Browser crypto support is unavailable for secure submission.');
  }
  const keyPayload = await fetchSubmissionPublicKey();
  const importedKey = await window.crypto.subtle.importKey(
    'spki',
    pemToArrayBuffer(keyPayload.public_key_pem),
    { name: 'RSA-OAEP', hash: 'SHA-256' },
    false,
    ['encrypt']
  );
  const ciphertext = await window.crypto.subtle.encrypt(
    { name: 'RSA-OAEP' },
    importedKey,
    new TextEncoder().encode(value)
  );
  const bytes = new Uint8Array(ciphertext);
  let binary = '';
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return {
    key_id: keyPayload.key_id,
    ciphertext: btoa(binary),
  };
};

const sendQuery = async (body) => {
  const res = await fetch(`${API_BASE}/api/v1/query`, {
    method: 'POST',
    credentials: 'include',
    headers: authHeaders(),
    body: JSON.stringify(body),
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

export const querySession = async ({ question, session_id, thread_id = '' }) => {
  return sendQuery({ question, session_id, thread_id: thread_id || undefined });
};

export const createSession = async ({ repoFullName, repoUrl, tenantId = 'local', githubToken = '' }) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/sessions`, {
        method: 'POST',
        credentials: 'include',
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
        credentials: 'include',
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
    credentials: 'include',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Delete session failed (${res.status})`);
  return res.json();
};

export const fetchSessionMessages = async (sessionId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/sessions/${sessionId}/messages`, {
        credentials: 'include',
        headers: authHeaders(),
      }),
    'Fetch session messages'
  );
  if (!res.ok) throw new Error(`Fetch session messages failed (${res.status})`);
  const data = await res.json();
  return data.messages || [];
};

export const listSessionThreads = async (sessionId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/sessions/${sessionId}/threads`, {
        credentials: 'include',
        headers: authHeaders(),
      }),
    'List session threads'
  );
  if (!res.ok) throw new Error(`List session threads failed (${res.status})`);
  const data = await res.json();
  return data.threads || [];
};

export const createSessionThread = async (sessionId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/sessions/${sessionId}/threads`, {
        method: 'POST',
        credentials: 'include',
        headers: authHeaders(),
      }),
    'Create session thread'
  );
  if (!res.ok) throw new Error(`Create session thread failed (${res.status})`);
  const data = await res.json();
  return data.thread;
};

export const fetchThreadMessages = async (threadId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/threads/${threadId}/messages`, {
        credentials: 'include',
        headers: authHeaders(),
      }),
    'Fetch thread messages'
  );
  if (!res.ok) throw new Error(`Fetch thread messages failed (${res.status})`);
  const data = await res.json();
  return data.messages || [];
};

export const clearThreadMessagesApi = async (threadId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/threads/${threadId}/messages`, {
        method: 'DELETE',
        credentials: 'include',
        headers: authHeaders(),
      }),
    'Clear thread messages'
  );
  if (!res.ok) throw new Error(`Clear thread messages failed (${res.status})`);
  return res.json();
};

export const clearSessionMessagesApi = async (sessionId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/sessions/${sessionId}/messages`, {
        method: 'DELETE',
        credentials: 'include',
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
      credentials: 'include',
      headers: { Authorization: `Bearer ${getBackendApiKey()}` },
    });
    return res.ok;
  } catch {
    return false;
  }
};

/**
 * POST /auth/github
 * Exchange GitHub OAuth code via the backend and create a server-side session.
 */
export const exchangeGithubCode = async (code) => {
  const res = await fetch(`${API_BASE}/auth/github`, {
    method: 'POST',
    credentials: 'include',
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

export const connectGithubToken = async (accessToken) => {
  const encryptedSecret = await encryptSecretForSubmission(accessToken);
  const res = await fetch(`${API_BASE}/auth/github/token`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ encrypted_secret: encryptedSecret }),
  });

  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body.detail || body.message || '';
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`GitHub token connect failed (${res.status})${detail ? `: ${detail}` : ''}`);
  }

  return res.json();
};

export const listGithubRepos = async () => {
  const res = await fetch(`${API_BASE}/api/v1/github/repos`, {
    credentials: 'include',
  });
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body.detail || body.message || '';
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`GitHub repo list failed (${res.status})${detail ? `: ${detail}` : ''}`);
  }
  const data = await res.json();
  return data.repos || [];
};

export const listProviderCredentials = async () => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/provider-credentials`, {
        credentials: 'include',
        headers: authHeaders(),
      }),
    'List provider credentials'
  );
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body.detail || body.message || '';
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`List provider credentials failed (${res.status})${detail ? `: ${detail}` : ''}`);
  }
  const data = await res.json();
  return data.provider_credentials || [];
};

export const createProviderCredential = async ({ provider, label, apiKey, model = '', isActive }) => {
  const encryptedSecret = await encryptSecretForSubmission(apiKey);
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/provider-credentials`, {
        method: 'POST',
        credentials: 'include',
        headers: authHeaders(),
        body: JSON.stringify({
          provider,
          label,
          encrypted_secret: encryptedSecret,
          model,
          is_active: isActive,
        }),
      }),
    'Create provider credential'
  );
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body.detail || body.message || '';
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`Create provider credential failed (${res.status})${detail ? `: ${detail}` : ''}`);
  }
  const data = await res.json();
  return data.provider_credential;
};

export const activateProviderCredential = async (credentialId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/provider-credentials/${credentialId}/activate`, {
        method: 'POST',
        credentials: 'include',
        headers: authHeaders(),
      }),
    'Activate provider credential'
  );
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body.detail || body.message || '';
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`Activate provider credential failed (${res.status})${detail ? `: ${detail}` : ''}`);
  }
  const data = await res.json();
  return data.provider_credential;
};

export const deleteProviderCredential = async (credentialId) => {
  const res = await withNetworkError(
    () =>
      fetch(`${API_BASE}/api/v1/provider-credentials/${credentialId}`, {
        method: 'DELETE',
        credentials: 'include',
        headers: authHeaders(),
      }),
    'Delete provider credential'
  );
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body.detail || body.message || '';
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(`Delete provider credential failed (${res.status})${detail ? `: ${detail}` : ''}`);
  }
  return res.json();
};

export const fetchGithubSessionMe = async () => {
  const res = await fetch(`${API_BASE}/auth/me`, {
    credentials: 'include',
  });
  if (!res.ok) {
    throw new Error(`Auth me failed (${res.status})`);
  }
  return res.json();
};

export const logoutGithubSession = async () => {
  const res = await fetch(`${API_BASE}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  });
  if (!res.ok) {
    throw new Error(`Auth logout failed (${res.status})`);
  }
  return res.json();
};
