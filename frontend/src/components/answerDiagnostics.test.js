import test from 'node:test';
import assert from 'node:assert/strict';

import { buildAnswerDiagnosticsRows, summarizeDiagnosticSource } from './answerDiagnostics.js';

test('buildAnswerDiagnosticsRows keeps only safe display fields', () => {
  const rows = buildAnswerDiagnosticsRows({
    intent: 'CODE_REQUEST',
    primary_intent: 'CODE_REQUEST',
    response_mode: 'code_snippet',
    provider: 'local',
    model: 'qwen2.5-coder:3b-8k',
    routing_mode: 'local',
    context_tokens: 512,
    evidence_confidence: { level: 'strong', reason: 'matched route', count: 2 },
    source_filter: { selected_primary: 1, selected_expanded: 0, display_count: 1, reasoning_count: 2 },
    session_status: 'ready',
    session_error: '',
    validation: { valid: false, reasons: ['rebuilt_code_snippet'] },
    selected_sources: [
      {
        relative_path: 'backend/evals/run_safe_evals.py',
        symbol_name: 'main',
        start_line: 10,
        end_line: 48,
        api_key: 'secret',
        raw_prompt: 'hidden',
      },
    ],
    reasoning_sources: [
      {
        relative_path: 'backend/evals/run_safe_evals.py',
        symbol_name: 'get_tail',
        start_line: 50,
        end_line: 66,
      },
    ],
    rendered_sources: [
      {
        relative_path: 'backend/evals/run_safe_evals.py',
        symbol_name: 'main',
        start_line: 10,
        end_line: 48,
      },
    ],
  });

  assert.ok(rows.length > 0);
  assert.equal(rows[0].label, 'Intent');
  assert.ok(rows.some((row) => row.label === 'Validation'));
  assert.ok(rows.some((row) => row.label === 'Rendered sources'));
  assert.ok(rows.some((row) => row.label === 'Selected sources'));
  assert.ok(rows.some((row) => row.label === 'Reasoning sources'));
  const renderedRow = rows.find((row) => row.label === 'Rendered sources');
  assert.equal(renderedRow.value[0], 'backend/evals/run_safe_evals.py :: main (L10–48)');
  const selectedRow = rows.find((row) => row.label === 'Selected sources');
  assert.equal(selectedRow.value[0], 'backend/evals/run_safe_evals.py :: main (L10–48)');
  assert.ok(rows.every((row) => JSON.stringify(row).indexOf('secret') === -1));
  assert.ok(rows.every((row) => JSON.stringify(row).indexOf('hidden') === -1));
});

test('summarizeDiagnosticSource handles missing fields safely', () => {
  assert.equal(summarizeDiagnosticSource(null), '');
  assert.equal(
    summarizeDiagnosticSource({
      relative_path: 'backend/evals/run_safe_evals.py',
      symbol_name: 'main',
      start_line: 10,
      end_line: 48,
    }),
    'backend/evals/run_safe_evals.py :: main (L10–48)'
  );
});
