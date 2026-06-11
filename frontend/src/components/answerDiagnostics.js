const MAX_SOURCE_ITEMS = 6;

function safeString(value) {
  return typeof value === 'string' ? value.trim() : `${value ?? ''}`.trim();
}

function formatLineRange(source) {
  const start = Number(source?.start_line);
  const end = Number(source?.end_line);
  if (!Number.isFinite(start) || start <= 0) return '';
  if (!Number.isFinite(end) || end <= 0 || end === start) return `L${start}`;
  return `L${start}–${end}`;
}

export function summarizeDiagnosticSource(source) {
  if (!source || typeof source !== 'object') return '';

  const relativePath = safeString(source.relative_path || source.file);
  if (!relativePath) return '';

  const symbolName = safeString(source.symbol_name || source.symbol);
  const lines = formatLineRange(source);
  const parts = [relativePath];
  if (symbolName) parts.push(`:: ${symbolName}`);
  if (lines) parts.push(`(${lines})`);
  return parts.join(' ');
}

function summarizeEvidenceConfidence(confidence) {
  if (!confidence || typeof confidence !== 'object') return '';
  const parts = [];
  const level = safeString(confidence.level);
  const count = Number(confidence.count);
  const reason = safeString(confidence.reason);
  if (level) parts.push(level);
  if (Number.isFinite(count) && count >= 0) parts.push(`${count} hit${count === 1 ? '' : 's'}`);
  if (reason) parts.push(reason);
  return parts.join(' · ');
}

function summarizeValidation(validation) {
  if (!validation || typeof validation !== 'object') return '';
  const parts = [];
  if (typeof validation.valid === 'boolean') {
    parts.push(validation.valid ? 'valid' : 'repaired');
  }
  const reasons = Array.isArray(validation.reasons) ? validation.reasons.filter(Boolean) : [];
  if (reasons.length > 0) {
    parts.push(reasons.join(', '));
  }
  return parts.join(' · ');
}

function summarizeSourceFilter(sourceFilter) {
  if (!sourceFilter || typeof sourceFilter !== 'object') return '';
  const parts = [];
  const selected = Number(sourceFilter.selected_primary);
  const expanded = Number(sourceFilter.selected_expanded);
  const display = Number(sourceFilter.display_count);
  const reasoning = Number(sourceFilter.reasoning_count);
  if (Number.isFinite(selected)) parts.push(`primary ${selected}`);
  if (Number.isFinite(expanded)) parts.push(`expanded ${expanded}`);
  if (Number.isFinite(display)) parts.push(`display ${display}`);
  if (Number.isFinite(reasoning)) parts.push(`reasoning ${reasoning}`);
  return parts.join(' · ');
}

function compactSourceList(items) {
  if (!Array.isArray(items) || items.length === 0) return [];
  const seen = new Set();
  const compacted = [];
  for (const item of items) {
    const summary = summarizeDiagnosticSource(item);
    if (!summary || seen.has(summary)) continue;
    seen.add(summary);
    compacted.push(summary);
    if (compacted.length >= MAX_SOURCE_ITEMS) break;
  }
  return compacted;
}

export function buildAnswerDiagnosticsRows(diagnostics) {
  if (!diagnostics || typeof diagnostics !== 'object') return [];

  const rows = [];
  const addTextRow = (label, value) => {
    const text = safeString(value);
    if (!text) return;
    rows.push({ label, kind: 'text', value: text });
  };
  const addListRow = (label, items) => {
    const values = compactSourceList(items);
    if (values.length === 0) return;
    rows.push({ label, kind: 'list', value: values });
  };

  addTextRow('Intent', diagnostics.intent);
  addTextRow('Primary intent', diagnostics.primary_intent);
  addTextRow('Response mode', diagnostics.response_mode);
  addTextRow(
    'Model',
    [diagnostics.provider, diagnostics.model].filter(Boolean).join(' / ')
  );
  addTextRow('Routing mode', diagnostics.routing_mode);
  addTextRow('Context tokens', diagnostics.context_tokens);
  addTextRow('Evidence confidence', summarizeEvidenceConfidence(diagnostics.evidence_confidence));
  addTextRow('Source filter', summarizeSourceFilter(diagnostics.source_filter));
  addTextRow('Session status', diagnostics.session_status);
  addTextRow('Session error', diagnostics.session_error);
  addTextRow('Validation', summarizeValidation(diagnostics.validation));
  addListRow('Selected sources', diagnostics.selected_sources);
  addListRow('Reasoning sources', diagnostics.reasoning_sources);
  addListRow('Rendered sources', diagnostics.rendered_sources);

  return rows;
}
