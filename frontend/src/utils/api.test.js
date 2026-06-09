import test from 'node:test';
import assert from 'node:assert/strict';

import { formatApiError } from './api.js';

test('formatApiError maps provider auth failures to actionable copy', () => {
  const message = formatApiError({
    action: 'Query',
    status: 400,
    detail: 'Provider API key rejected or lacks permission.',
  });

  assert.match(message, /provider rejected/i);
  assert.match(message, /update the provider configuration/i);
});

test('formatApiError maps unsupported provider configuration copy', () => {
  const message = formatApiError({
    action: 'Query',
    status: 400,
    detail: 'Unsupported LLM provider configuration: mystery',
  });

  assert.match(message, /provider configuration is invalid/i);
});

test('formatApiError maps rate-limit copy', () => {
  const message = formatApiError({
    action: 'Query',
    status: 429,
    detail: 'Provider rate limit reached. Wait and retry, or switch provider credentials.',
  });

  assert.match(message, /rate limit reached/i);
  assert.match(message, /switch provider credentials/i);
});

test('fetchLatestEvaluationReport invokes the correct endpoint', async () => {
  const originalFetch = globalThis.fetch;
  let calledUrl = null;
  let calledOptions = null;
  
  globalThis.localStorage = {
    getItem: () => null,
    setItem: () => null,
    removeItem: () => null,
  };

  globalThis.fetch = async (url, options) => {
    calledUrl = url;
    calledOptions = options;
    return {
      ok: true,
      json: async () => ({ status: 'PASS', available: true })
    };
  };

  try {
    const { fetchLatestEvaluationReport } = await import('./api.js');
    const report = await fetchLatestEvaluationReport('session-123');
    assert.equal(report.status, 'PASS');
    assert.equal(report.available, true);
    assert.match(calledUrl, /\/api\/v1\/sessions\/session-123\/evaluation\/latest/);
    assert.equal(calledOptions.credentials, 'include');
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.localStorage;
  }
});

