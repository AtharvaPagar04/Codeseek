import { useEffect, useRef, useState } from 'react';
import SourceCard from './SourceCard';
import { fetchRagasValidationBundle } from '../utils/api';

const METRICS = [
  'context_precision',
  'context_recall',
  'faithfulness',
  'answer_relevancy',
  'answer_correctness',
];

const METRIC_LABELS = {
  context_precision: 'Context precision',
  context_recall: 'Context recall',
  faithfulness: 'Faithfulness',
  answer_relevancy: 'Answer relevancy',
  answer_correctness: 'Answer correctness',
};

export default function RagasValidationModal({ onClose }) {
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);
  const [selectedCaseId, setSelectedCaseId] = useState('');
  const [filterText, setFilterText] = useState('');
  const overlayRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    fetchRagasValidationBundle()
      .then((data) => {
        if (!cancelled) {
          setBundle(data);
          const firstId = data?.report?.responses?.[0]?.case_id || '';
          setSelectedCaseId(firstId);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Failed to load RAGAS report.');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handler = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const report = bundle?.report || null;
  const responses = report?.responses || [];
  const familyTrend = bundle?.family_baseline_trend || null;
  const benchmark = bundle?.human_review_benchmark || null;
  const benchmarkMap = new Map((benchmark?.cases || []).map((item) => [item.case_id, item]));

  const visibleResponses = responses.filter((item) => {
    if (!filterText.trim()) return true;
    const haystack = [
      item.case_id,
      item.query,
      item.response_mode,
      item.primary_intent,
      item.failure_stage_hint,
    ]
      .join(' ')
      .toLowerCase();
    return haystack.includes(filterText.trim().toLowerCase());
  });

  const selectedResponse =
    responses.find((item) => item.case_id === selectedCaseId) ||
    visibleResponses[0] ||
    responses[0] ||
    null;

  useEffect(() => {
    if (!selectedResponse) return;
    const visibleIds = new Set(visibleResponses.map((item) => item.case_id));
    if (visibleResponses.length > 0 && !visibleIds.has(selectedResponse.case_id)) {
      setSelectedCaseId(visibleResponses[0].case_id);
    }
  }, [visibleResponses, selectedResponse]);

  const selectedBenchmark = selectedResponse ? benchmarkMap.get(selectedResponse.case_id) || null : null;
  const selectedIntentTrend = selectedResponse
    ? familyTrend?.families?.primary_intent?.[selectedResponse.primary_intent] || null
    : null;
  const selectedModeTrend = selectedResponse
    ? familyTrend?.families?.response_mode?.[selectedResponse.response_mode] || null
    : null;

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-start justify-center px-3 py-[4vh]"
      onClick={(event) => event.target === overlayRef.current && onClose()}
    >
      <div className="w-full max-w-7xl bg-surface-2 border border-border rounded-3xl shadow-2xl overflow-hidden max-h-[92vh] flex flex-col">
        <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-border bg-gradient-to-r from-surface-2 via-surface-2 to-surface-3">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-[0.3em] text-text-muted font-mono">Validation</div>
            <div className="text-lg font-semibold text-text-primary truncate">RAGAS scorecards</div>
            <div className="text-xs text-text-muted font-mono truncate">
              {report?.run_meta?.dataset_name || 'No report loaded'}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="w-9 h-9 rounded-full border border-border bg-surface-3 text-text-muted hover:text-text-primary hover:border-text-muted transition-colors"
              aria-label="Close RAGAS report"
            >
              ×
            </button>
          </div>
        </div>

        {error ? (
          <div className="p-6 text-sm text-offline font-mono">
            {error}
          </div>
        ) : !report ? (
          <div className="p-6 text-sm text-text-muted font-mono">
            Loading RAGAS validation report...
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)] min-h-0 flex-1">
            <aside className="border-r border-border bg-surface/70 flex flex-col min-h-0">
              <div className="p-4 border-b border-border space-y-3">
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <InfoChip label="Cases" value={String(report?.run_meta?.case_count ?? responses.length)} />
                  <InfoChip label="Generated" value={shortDate(report?.run_meta?.generated_at_utc)} />
                </div>
                <input
                  value={filterText}
                  onChange={(e) => setFilterText(e.target.value)}
                  placeholder="Filter case id, query, mode..."
                  className="w-full rounded-xl border border-border bg-surface-3 px-3 py-2 text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-text-muted"
                />
                <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
                  <MiniStat label="Baseline" value={bundle?.family_baseline ? 'loaded' : 'none'} />
                  <MiniStat label="Benchmark" value={benchmark ? 'loaded' : 'none'} />
                </div>
              </div>

              <div className="flex-1 overflow-y-auto p-3 space-y-2">
                {visibleResponses.length === 0 ? (
                  <div className="rounded-xl border border-border bg-surface-3 p-4 text-xs text-text-muted font-mono">
                    No responses match the current filter.
                  </div>
                ) : (
                  visibleResponses.map((response) => (
                    <button
                      key={response.case_id}
                      onClick={() => setSelectedCaseId(response.case_id)}
                      className={`w-full text-left rounded-2xl border px-3 py-3 transition-colors ${
                        response.case_id === selectedResponse?.case_id
                          ? 'border-text-muted bg-surface-3'
                          : 'border-border bg-surface-3/50 hover:bg-surface-3 hover:border-text-muted'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-text-primary truncate">
                            {response.case_id}
                          </div>
                          <div className="text-xs text-text-muted line-clamp-2 mt-1">
                            {response.query}
                          </div>
                        </div>
                        <div className="shrink-0 text-right">
                          <ModeBadge value={response.response_mode} />
                          <div className="mt-1 text-[10px] text-text-muted font-mono uppercase tracking-wide">
                            {response.failure_stage_hint || 'none'}
                          </div>
                        </div>
                      </div>
                    </button>
                  ))
                )}
              </div>
            </aside>

            <section className="min-h-0 overflow-y-auto p-5 space-y-5">
              {selectedResponse ? (
                <>
                  <div className="grid gap-3 md:grid-cols-4">
                    <CardPanel title="Response mode" value={selectedResponse.response_mode} />
                    <CardPanel title="Primary intent" value={selectedResponse.primary_intent || '-'} />
                    <CardPanel title="Failure hint" value={selectedResponse.failure_stage_hint || 'none'} />
                    <CardPanel title="Context tokens" value={String(selectedResponse.context_token_count ?? 0)} />
                  </div>

                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
                    <section className="rounded-2xl border border-border bg-surface-3/80 p-4">
                      <SectionTitle title="Final Answer" subtitle="What the model or deterministic path returned" />
                      <TextBlock text={selectedResponse.final_answer || 'No answer recorded.'} />
                    </section>

                    <section className="rounded-2xl border border-border bg-surface-3/80 p-4 space-y-4">
                      <SectionTitle title="Ground Truth" subtitle="Reference answer and evidence anchors" />
                      <TextBlock text={selectedResponse.ground_truth || 'No ground truth recorded.'} />
                      <div className="space-y-2">
                        <div className="text-2xs uppercase tracking-[0.24em] text-text-muted font-mono">Ground-truth sources</div>
                        <SourceList sources={selectedResponse.ground_truth_sources || []} emptyLabel="No ground-truth sources" />
                      </div>
                    </section>
                  </div>

                  <section className="rounded-2xl border border-border bg-surface-3/80 p-4">
                    <SectionTitle title="Metric Scorecard" subtitle="Per-response RAGAS-compatible values" />
                    <div className="grid gap-3 md:grid-cols-5">
                      {METRICS.map((metric) => (
                        <MetricCard key={metric} metric={metric} cell={selectedResponse.ragas?.[metric]} />
                      ))}
                    </div>
                  </section>

                  <div className="grid gap-4 xl:grid-cols-2">
                    <section className="rounded-2xl border border-border bg-surface-3/80 p-4">
                      <SectionTitle title="Pipeline Metadata" subtitle="What happened before the answer was produced" />
                      <KeyValueGrid
                        items={[
                          ['Resolved query', selectedResponse.resolved_query || '-'],
                          ['Expected mode', selectedResponse.expected_response_mode || '-'],
                          ['Expected intent', selectedResponse.expected_intent || '-'],
                          ['Context capture', selectedResponse.context_capture_status || '-'],
                          ['Latency', `${selectedResponse.total_latency_ms ?? 0} ms`],
                          ['Backend latency', `${selectedResponse.backend_latency_ms ?? 0} ms`],
                          ['Provider latency', `${selectedResponse.provider_latency_ms ?? 0} ms`],
                          ['Reasoning tokens', String(selectedResponse.reasoning_context_token_count ?? 0)],
                        ]}
                      />

                      <div className="mt-4 space-y-3">
                        <div className="text-2xs uppercase tracking-[0.24em] text-text-muted font-mono">Stage latency</div>
                        <KeyValueGrid
                          items={Object.entries(selectedResponse.stage_latency_ms || {}).map(([key, value]) => [
                            key,
                            `${value} ms`,
                          ])}
                        />
                      </div>
                    </section>

                    <section className="rounded-2xl border border-border bg-surface-3/80 p-4 space-y-4">
                      <SectionTitle title="Baseline Comparison" subtitle="Family-level drift for this case family" />
                      {selectedIntentTrend ? (
                        <TrendPanel title={`Intent: ${selectedResponse.primary_intent || '-'}`} payload={selectedIntentTrend} />
                      ) : (
                        <EmptyPanel label="No primary-intent baseline available." />
                      )}
                      {selectedModeTrend ? (
                        <TrendPanel title={`Mode: ${selectedResponse.response_mode || '-'}`} payload={selectedModeTrend} />
                      ) : (
                        <EmptyPanel label="No response-mode baseline available." />
                      )}
                      {selectedBenchmark ? (
                        <BenchmarkPanel response={selectedResponse} benchmark={selectedBenchmark} />
                      ) : (
                        <EmptyPanel label="No human-reviewed benchmark entry for this case." />
                      )}
                    </section>
                  </div>

                  <div className="grid gap-4 xl:grid-cols-2">
                    <section className="rounded-2xl border border-border bg-surface-3/80 p-4">
                      <SectionTitle title="Contexts" subtitle="The exact blocks shown to the answer path" />
                      <ContextList contexts={selectedResponse.contexts || []} />
                    </section>

                    <section className="rounded-2xl border border-border bg-surface-3/80 p-4 space-y-4">
                      <SectionTitle title="Evidence Sets" subtitle="What the pipeline retrieved, expanded, and retained" />
                      <EvidenceGroup label="Search candidates" sources={selectedResponse.search_candidates || []} />
                      <EvidenceGroup label="Expanded candidates" sources={selectedResponse.expanded_candidates || []} />
                      <EvidenceGroup label="Assembled sources" sources={selectedResponse.assembled_sources || []} />
                      <EvidenceGroup label="Display sources" sources={selectedResponse.display_sources || []} />
                      <EvidenceGroup label="Reasoning sources" sources={selectedResponse.reasoning_sources || []} />
                    </section>
                  </div>
                </>
              ) : (
                <EmptyPanel label="No RAGAS response selected." />
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}

function SectionTitle({ title, subtitle }) {
  return (
    <div className="mb-3">
      <div className="text-sm font-semibold text-text-primary">{title}</div>
      <div className="text-xs text-text-muted mt-0.5">{subtitle}</div>
    </div>
  );
}

function CardPanel({ title, value }) {
  return (
    <div className="rounded-2xl border border-border bg-surface-3/80 px-4 py-3">
      <div className="text-2xs uppercase tracking-[0.24em] text-text-muted font-mono">{title}</div>
      <div className="mt-1 text-sm text-text-primary break-words">{value || '-'}</div>
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div className="rounded-xl border border-border bg-surface-3 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.24em] text-text-muted font-mono">{label}</div>
      <div className="text-xs text-text-primary font-medium mt-1">{value}</div>
    </div>
  );
}

function InfoChip({ label, value }) {
  return (
    <div className="rounded-xl border border-border bg-surface-3 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.24em] text-text-muted font-mono">{label}</div>
      <div className="text-sm text-text-primary font-medium mt-1">{value}</div>
    </div>
  );
}

function ModeBadge({ value }) {
  return (
    <span className="inline-flex items-center rounded-full border border-border bg-surface-2 px-2 py-0.5 text-[10px] font-mono uppercase tracking-wide text-text-secondary">
      {value || '-'}
    </span>
  );
}

function MetricCard({ metric, cell }) {
  const state = cell?.state || 'missing';
  const value = typeof cell?.value === 'number' ? cell.value : null;
  const tone = metricTone(state, value);
  return (
    <div className={`rounded-2xl border px-4 py-3 ${tone.border} ${tone.bg}`}>
      <div className="text-2xs uppercase tracking-[0.24em] font-mono text-text-muted">{METRIC_LABELS[metric] || metric}</div>
      <div className="mt-1 flex items-baseline justify-between gap-2">
        <div className="text-lg font-semibold text-text-primary">{value === null ? state : value.toFixed(4)}</div>
        <div className="text-[10px] font-mono uppercase tracking-wide text-text-muted">{state}</div>
      </div>
      {cell?.detail && <div className="mt-2 text-xs text-text-muted leading-relaxed">{cell.detail}</div>}
    </div>
  );
}

function metricTone(state, value) {
  if (state === 'error') return { border: 'border-offline/30', bg: 'bg-offline/10' };
  if (state === 'not_applicable') return { border: 'border-warning/30', bg: 'bg-warning/10' };
  if (typeof value === 'number' && value >= 0.85) return { border: 'border-online/30', bg: 'bg-online/10' };
  if (typeof value === 'number' && value >= 0.7) return { border: 'border-border', bg: 'bg-surface-3/60' };
  return { border: 'border-warning/20', bg: 'bg-warning/5' };
}

function KeyValueGrid({ items }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
      {items.map(([label, value]) => (
        <div key={label} className="rounded-xl border border-border bg-surface-2/60 px-3 py-2">
          <div className="text-[10px] uppercase tracking-[0.24em] text-text-muted font-mono">{label}</div>
          <div className="mt-1 text-sm text-text-primary break-words">{value || '-'}</div>
        </div>
      ))}
    </div>
  );
}

function TrendPanel({ title, payload }) {
  const deltas = payload?.metric_deltas || {};
  return (
    <div className="rounded-2xl border border-border bg-surface-2/60 p-3">
      <div className="text-xs font-semibold text-text-primary">{title}</div>
      <div className="mt-1 text-[11px] text-text-muted font-mono">
        {payload?.current_count ?? 0} current / {payload?.previous_count ?? 0} baseline
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        {METRICS.map((metric) => (
          <div key={metric} className="rounded-lg border border-border bg-surface-3 px-2 py-2">
            <div className="text-[10px] uppercase tracking-wide text-text-muted font-mono">
              {METRIC_LABELS[metric] || metric}
            </div>
            <div className="mt-1 font-mono text-text-primary">{formatSigned(deltas[metric])}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function BenchmarkPanel({ response, benchmark }) {
  const minimums = benchmark.minimums || {};
  const metrics = Object.entries(minimums);
  const row = metrics.length > 0 ? metrics : [['status', 'review-only']];
  return (
    <div className="rounded-2xl border border-border bg-surface-2/60 p-3">
      <div className="text-xs font-semibold text-text-primary">Human-reviewed benchmark</div>
      <div className="mt-1 text-[11px] text-text-muted font-mono">
        {benchmark.review_status || '-'} / priority {benchmark.priority || '-'}
      </div>
      <div className="mt-3 space-y-2">
        {row.map(([metric, threshold]) => {
          if (metric === 'status') {
            return (
              <div key={metric} className="rounded-lg border border-border bg-surface-3 px-2 py-2 text-xs text-text-muted">
                Review-only case: {threshold}
              </div>
            );
          }
          const cell = response.ragas?.[metric];
          const value = typeof cell?.value === 'number' ? cell.value : null;
          const pass = value !== null && value >= Number(threshold);
          return (
            <div key={metric} className="rounded-lg border border-border bg-surface-3 px-2 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-text-primary">{METRIC_LABELS[metric] || metric}</span>
                <span className={`text-[10px] font-mono uppercase ${pass ? 'text-online' : 'text-warning'}`}>
                  {pass ? 'pass' : 'review'}
                </span>
              </div>
              <div className="mt-1 text-[11px] text-text-muted font-mono">
                current {value === null ? '-' : value.toFixed(4)} / min {Number(threshold).toFixed(4)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SourceList({ sources, emptyLabel }) {
  if (!sources || sources.length === 0) {
    return <EmptyPanel label={emptyLabel} compact />;
  }
  return <div className="flex flex-wrap gap-2">{sources.map((source, index) => <SourceCard key={`${source.relative_path || source.file || 'source'}-${index}`} source={source} />)}</div>;
}

function EvidenceGroup({ label, sources }) {
  return (
    <details className="rounded-xl border border-border bg-surface-2/60 overflow-hidden">
      <summary className="cursor-pointer px-3 py-2 text-sm text-text-primary flex items-center justify-between gap-3">
        <span>{label}</span>
        <span className="text-xs text-text-muted font-mono">{sources?.length || 0}</span>
      </summary>
      <div className="px-3 pb-3">
        <SourceList sources={sources} emptyLabel={`No ${label.toLowerCase()}.`} />
      </div>
    </details>
  );
}

function ContextList({ contexts }) {
  if (!contexts || contexts.length === 0) {
    return <EmptyPanel label="No captured contexts available." compact />;
  }
  return (
    <div className="space-y-3">
      {contexts.map((context, index) => (
        <details key={`${context.relative_path || 'context'}-${index}`} className="rounded-xl border border-border bg-surface-2/60 overflow-hidden">
          <summary className="cursor-pointer px-3 py-2 text-sm text-text-primary flex items-center justify-between gap-3">
            <span className="truncate">
              {context.relative_path || 'context'}{context.symbol_name ? ` :: ${context.symbol_name}` : ''}
            </span>
            <span className="text-xs text-text-muted font-mono">{context.expansion_type || 'context'}</span>
          </summary>
          <div className="px-3 pb-3 space-y-2">
            <div className="text-xs text-text-muted font-mono">
              {lineLabel(context.start_line, context.end_line)}
            </div>
            <pre className="whitespace-pre-wrap rounded-xl border border-border bg-surface-3 p-3 text-xs text-text-secondary overflow-x-auto">
              {context.text || context.summary || ''}
            </pre>
          </div>
        </details>
      ))}
    </div>
  );
}

function EmptyPanel({ label, compact = false }) {
  return (
    <div className={`rounded-xl border border-border bg-surface-2/60 ${compact ? 'px-3 py-2 text-xs' : 'p-4 text-sm'} text-text-muted font-mono`}>
      {label}
    </div>
  );
}

function TextBlock({ text }) {
  return (
    <pre className="whitespace-pre-wrap rounded-xl border border-border bg-surface-2/70 p-3 text-sm text-text-secondary leading-relaxed overflow-x-auto">
      {text}
    </pre>
  );
}

function shortDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function lineLabel(startLine, endLine) {
  const start = Number(startLine);
  const end = Number(endLine);
  if (!Number.isFinite(start) || start <= 0) return 'lines -';
  if (!Number.isFinite(end) || end <= 0 || end === start) {
    return `line ${start}`;
  }
  return `lines ${start}-${end}`;
}

function formatSigned(value) {
  const number = Number(value || 0);
  return `${number >= 0 ? '+' : ''}${number.toFixed(4)}`;
}
