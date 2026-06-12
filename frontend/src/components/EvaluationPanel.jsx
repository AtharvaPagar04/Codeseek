import React, { useState } from 'react';

const REGRESSION_QUERIES = [
  {
    id: 1,
    query: "What is this project about?",
    category: "Overview",
    intent: "general_context",
    files: ["README.md"]
  },
  {
    id: 2,
    query: "How is this codebase structured?",
    category: "Overview",
    intent: "technical_explanation (architecture)",
    files: ["README.md", "backend/retrieval/main.py"]
  },
  {
    id: 3,
    query: "What are the main backend modules?",
    category: "Overview",
    intent: "general_context",
    files: ["backend/retrieval/session_indexer.py", "backend/retrieval/code_answers.py"]
  },
  {
    id: 4,
    query: "show me _require_auth code",
    category: "Code Snippet",
    intent: "code_snippet",
    files: ["backend/retrieval/api_service.py"]
  },
  {
    id: 5,
    query: "provide me the auth function code",
    category: "Code Snippet",
    intent: "code_snippet",
    files: ["backend/retrieval/api_service.py", "backend/retrieval/auth_store.py"]
  },
  {
    id: 6,
    query: "show me the safe eval runner code",
    category: "Code Snippet",
    intent: "code_snippet",
    files: ["backend/evals/run_safe_evals.py"]
  },
  {
    id: 7,
    query: "show me the evaluation report API endpoint code",
    category: "Code Snippet",
    intent: "code_snippet",
    files: ["backend/retrieval/api_service.py", "backend/retrieval/eval_reports.py"]
  },
  {
    id: 8,
    query: "show me the Qdrant upsert code",
    category: "Code Snippet",
    intent: "code_snippet",
    files: ["backend/rag_ingestion/stages/storage.py"]
  },
  {
    id: 9,
    query: "Where is safe eval implemented?",
    category: "Source Location",
    intent: "code_location",
    files: ["backend/evals/run_safe_evals.py"]
  },
  {
    id: 10,
    query: "Where is evaluation report API implemented?",
    category: "Source Location",
    intent: "code_location",
    files: ["backend/retrieval/api_service.py"]
  },
  {
    id: 11,
    query: "show me safe eval docs",
    category: "Code Snippet",
    intent: "general_context",
    files: ["docs/product/diagnostics_panel.md", "backend/docs/retrieval_docs/evaluation_policy.md"]
  },
  {
    id: 12,
    query: "How does the retrieval pipeline work?",
    category: "Technical Explanation",
    intent: "technical_explanation",
    files: ["backend/docs/retrieval_docs/retrieval_pipeline_docs.md", "backend/retrieval/searcher.py"]
  },
  {
    id: 13,
    query: "Where is reranking handled in searcher.py?",
    category: "Source Location",
    intent: "code_location",
    files: ["backend/retrieval/searcher.py"]
  },
  {
    id: 14,
    query: "show me the Qdrant upsert code -> explain that",
    category: "Multi-turn / Follow-up",
    intent: "technical_explanation",
    files: ["backend/rag_ingestion/stages/storage.py"]
  },
  {
    id: 15,
    query: "show me the safe eval runner code -> explain that",
    category: "Multi-turn / Follow-up",
    intent: "technical_explanation",
    files: ["backend/evals/run_safe_evals.py"]
  }
];

export default function EvaluationPanel({
  report,
  loading,
  error,
  onRefresh,
  sessionId,
  repoRoot,
  collection
}) {
  const [activeTab, setActiveTab] = useState('health'); // 'health' | 'regression'
  const [expandedStep, setExpandedStep] = useState(null);

  if (loading) {
    return (
      <div className="shrink-0 bg-surface-2 border-b border-border px-6 py-8 flex flex-col items-center justify-center gap-3 animate-pulse">
        <svg className="w-8 h-8 text-text-secondary animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1 1 21.306 7M7 9a5 5 0 0 1 10 0" />
        </svg>
        <span className="text-xs font-mono text-text-secondary">Loading latest evaluation report...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="shrink-0 bg-surface-2 border-b border-border px-6 py-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-offline">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <span className="text-sm font-semibold font-mono">Error loading evaluation report</span>
          </div>
          <button
            onClick={onRefresh}
            className="py-1 px-3 text-2xs font-semibold rounded-lg bg-surface-3 border border-border hover:border-text-muted text-text-primary transition-colors flex items-center gap-1"
          >
            Retry
          </button>
        </div>
        <pre className="p-3 bg-surface-3 border border-border rounded-xl text-xs font-mono text-text-secondary break-words whitespace-pre-wrap">
          {error}
        </pre>
      </div>
    );
  }

  // Define Badge styling helper
  const getStatusBadge = (status) => {
    let classes = 'border-border text-text-muted bg-surface-3';
    if (status === 'PASS') {
      classes = 'border-online/20 text-online bg-online/5';
    } else if (status === 'WARN') {
      classes = 'border-warning/20 text-warning bg-warning/5';
    } else if (status === 'ERROR') {
      classes = 'border-offline/20 text-offline bg-offline/5';
    }
    return (
      <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-2xs font-mono uppercase font-semibold tracking-wide ${classes}`}>
        {status || 'UNKNOWN'}
      </span>
    );
  };

  const getHardGateBadge = (status) => {
    let classes = 'text-offline bg-offline/5 border-offline/20';
    if (status === 'PASS') {
      classes = 'text-online bg-online/5 border-online/20';
    }
    return (
      <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-2xs font-mono uppercase font-semibold tracking-wide ${classes}`}>
        {status || 'ERROR'}
      </span>
    );
  };

  const getCategoryBadge = (category) => {
    let classes = 'border-border text-text-muted bg-surface-3';
    if (category === 'Overview') {
      classes = 'border-accent-dim/20 text-accent-dim bg-accent-dim/5';
    } else if (category === 'Code Snippet') {
      classes = 'border-online/20 text-online bg-online/5';
    } else if (category === 'Source Location') {
      classes = 'border-warning/20 text-warning bg-warning/5';
    } else if (category === 'Technical Explanation') {
      classes = 'border-text-secondary/20 text-text-secondary bg-text-secondary/5';
    } else if (category === 'Multi-turn / Follow-up') {
      classes = 'border-offline/20 text-offline bg-offline/5';
    }
    return (
      <span className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[9px] font-mono uppercase font-semibold tracking-wide ${classes}`}>
        {category}
      </span>
    );
  };

  return (
    <div className="shrink-0 bg-surface-2 border-b border-border px-6 py-5 animate-fadeIn relative z-10 space-y-5">
      {/* Header Info */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-2 border-b border-border-subtle/40">
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wider text-text-muted font-bold font-mono">Evaluation Health Dashboard</div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-xs text-text-secondary font-mono">Overall Status:</span>
              {getStatusBadge(report?.status || 'UNKNOWN')}
            </div>
            {report?.hard_gate_status && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-text-secondary font-mono">Hard Gate Status:</span>
                {getHardGateBadge(report.hard_gate_status)}
              </div>
            )}
            {report?.loaded_at && (
              <span className="text-[10px] text-text-muted font-mono" title={`Report file: ${report.report_path}`}>
                Loaded: {new Date(report.loaded_at).toLocaleString()}
              </span>
            )}
          </div>
        </div>

        {activeTab === 'health' && (
          <button
            onClick={onRefresh}
            className="self-start md:self-center py-1.5 px-3 text-2xs font-semibold rounded-lg bg-surface-3 border border-border hover:border-text-muted text-text-primary transition-colors flex items-center gap-1.5 font-mono"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1 1 21.306 7M7 9a5 5 0 0 1 10 0" />
            </svg>
            <span>Refresh Report</span>
          </button>
        )}
      </div>

      {/* Tab Selector */}
      <div className="flex border-b border-border-subtle/40 gap-4 text-xs font-mono">
        <button
          onClick={() => setActiveTab('health')}
          className={`pb-2 px-1 font-semibold transition-colors relative ${
            activeTab === 'health' 
              ? 'text-text-primary border-b-2 border-text-primary' 
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Health & Gating Summary
        </button>
        <button
          onClick={() => setActiveTab('regression')}
          className={`pb-2 px-1 font-semibold transition-colors relative ${
            activeTab === 'regression' 
              ? 'text-text-primary border-b-2 border-text-primary' 
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Manual Regression Set ({REGRESSION_QUERIES.length})
        </button>
      </div>

      {/* Tab Contents */}
      {activeTab === 'health' ? (
        // --- Tab 1: Health Summary (Existing Layout) ---
        !report || report.available === false ? (
          // Empty State inside Health Summary
          (() => {
            const defaultRepoRoot = repoRoot || '/home/arch/DEV/CodeSeek';
            const defaultCollection = collection || 'repository_chunks__local__codeseek';
            return (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 text-text-secondary text-xs font-mono">
                      <span className="w-2 h-2 rounded-full bg-text-muted animate-pulse" />
                      <span>{report?.message || 'No evaluation report found.'}</span>
                    </div>
                  </div>
                  <button
                    onClick={onRefresh}
                    className="py-1.5 px-3 text-2xs font-semibold rounded-lg bg-surface-3 border border-border hover:border-text-muted text-text-primary transition-colors flex items-center gap-1.5 font-mono"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1 1 21.306 7M7 9a5 5 0 0 1 10 0" />
                    </svg>
                    <span>Refresh</span>
                  </button>
                </div>

                <div className="rounded-xl border border-border bg-surface-3 p-4 space-y-3 font-mono text-xs text-text-secondary select-text">
                  <p className="text-text-muted text-2xs uppercase tracking-wider font-bold">Suggested Command to generate report</p>
                  <div className="relative">
                    <pre className="p-3 bg-base border border-border/60 rounded-lg overflow-x-auto whitespace-pre text-[11px] leading-relaxed text-accent-dim">
{`cd backend
.venv/bin/python evals/run_safe_evals.py \\
  --session-id ${sessionId || '<session-id>'} \\
  --expected-repo-root ${defaultRepoRoot} \\
  --expected-collection ${defaultCollection} \\
  --output-dir ../evals/reports/safe_eval_latest`}
                    </pre>
                    <button
                      onClick={() => {
                        const text = `cd backend\n.venv/bin/python evals/run_safe_evals.py \\\n  --session-id ${sessionId || '<session-id>'} \\\n  --expected-repo-root ${defaultRepoRoot} \\\n  --expected-collection ${defaultCollection} \\\n  --output-dir ../evals/reports/safe_eval_latest`;
                        navigator.clipboard.writeText(text);
                      }}
                      className="absolute top-2 right-2 p-1.5 bg-surface-3 border border-border rounded text-text-muted hover:text-text-primary hover:border-text-muted transition-colors text-2xs"
                      title="Copy to clipboard"
                    >
                      Copy
                    </button>
                  </div>
                </div>
              </div>
            );
          })()
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Left Column: Recommendation & Steps */}
            <div className="space-y-4">
              {report.recommendation && (
                <div className="rounded-xl border border-border bg-surface-3 p-4 space-y-1">
                  <div className="text-[10px] uppercase tracking-wider text-text-muted font-bold font-mono">Recommendation</div>
                  <p className="text-xs text-text-primary leading-relaxed">{report.recommendation}</p>
                </div>
              )}

              {/* Metadata Card */}
              <div className="rounded-xl border border-border bg-surface-3 p-4 space-y-2.5 font-mono text-[11px] select-text">
                <div className="text-[10px] uppercase tracking-wider text-text-muted font-bold font-mono mb-1">Evaluation Details</div>
                {report.session_id && (
                  <div className="flex flex-col md:flex-row md:justify-between gap-1 border-b border-border-subtle/30 pb-1.5">
                    <span className="text-text-muted">Session ID:</span>
                    <span className="text-text-secondary select-all break-all font-semibold md:text-right">{report.session_id}</span>
                  </div>
                )}
                {report.expected_collection && (
                  <div className="flex flex-col md:flex-row md:justify-between gap-1 border-b border-border-subtle/30 pb-1.5">
                    <span className="text-text-muted">Collection Name:</span>
                    <span className="text-text-secondary select-all break-all font-semibold md:text-right">{report.expected_collection}</span>
                  </div>
                )}
                {report.expected_repo_root && (
                  <div className="flex flex-col md:flex-row md:justify-between gap-1">
                    <span className="text-text-muted">Repo Root:</span>
                    <span className="text-text-secondary select-all break-all font-semibold md:text-right">{report.expected_repo_root}</span>
                  </div>
                )}
              </div>

              <div className="rounded-xl border border-border bg-surface-3 p-4 space-y-3">
                <div className="text-[10px] uppercase tracking-wider text-text-muted font-bold font-mono">Execution Steps</div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs font-mono">
                    <thead>
                      <tr className="text-text-muted border-b border-border-subtle">
                        <th className="pb-2 font-medium">Step</th>
                        <th className="pb-2 font-medium text-center">Status</th>
                        <th className="pb-2 font-medium text-right">Duration</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle/40">
                      {report.steps && report.steps.map((step) => {
                        const isExpanded = expandedStep === step.name;
                        return (
                          <React.Fragment key={step.name}>
                            <tr
                              onClick={() => setExpandedStep(isExpanded ? null : step.name)}
                              className="hover:bg-surface-2/40 cursor-pointer transition-colors"
                            >
                              <td className="py-2.5 font-semibold text-text-secondary select-all flex items-center gap-1.5">
                                <svg className={`w-3.5 h-3.5 text-text-muted transition-transform duration-200 ${isExpanded ? 'rotate-90' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                                </svg>
                                <span>{step.name}</span>
                              </td>
                              <td className="py-2.5 text-center">
                                <span className={`inline-flex items-center text-[10px] font-semibold px-1.5 py-0.5 rounded-md ${
                                  step.status === 'PASS' 
                                    ? 'text-online bg-online/5'
                                    : step.status === 'WARN'
                                    ? 'text-warning bg-warning/5'
                                    : 'text-offline bg-offline/5'
                                }`}>
                                  {step.status}
                                </span>
                              </td>
                              <td className="py-2.5 text-right text-text-primary">
                                {typeof step.duration_seconds === 'number' ? `${step.duration_seconds.toFixed(2)}s` : '-'}
                              </td>
                            </tr>
                            {isExpanded && (
                              <tr>
                                <td colSpan={3} className="px-4 py-3 bg-surface-2/20 border-t border-b border-border-subtle/40">
                                  <div className="space-y-3 text-[11px] font-mono leading-relaxed select-text">
                                    {step.command && step.command.length > 0 && (
                                      <div>
                                        <div className="text-[9px] uppercase tracking-wider text-text-muted font-bold mb-1">Command</div>
                                        <pre className="p-2 bg-base border border-border/60 rounded-md overflow-x-auto text-[10px] leading-relaxed text-text-primary">
                                          {step.command.join(' ')}
                                        </pre>
                                      </div>
                                    )}
                                    {step.stdout_tail && (
                                      <div>
                                        <div className="text-[9px] uppercase tracking-wider text-text-muted font-bold mb-1">Stdout</div>
                                        <pre className="p-2.5 bg-base border border-border/60 rounded-md overflow-x-auto whitespace-pre-wrap text-[10px] leading-relaxed text-text-secondary max-h-48 overflow-y-auto">
                                          {step.stdout_tail}
                                        </pre>
                                      </div>
                                    )}
                                    {step.stderr_tail && (
                                      <div>
                                        <div className="text-[9px] uppercase tracking-wider text-text-muted font-bold mb-1">Stderr</div>
                                        <pre className="p-2.5 bg-base border border-border/40 border-l-2 border-l-offline rounded-md overflow-x-auto whitespace-pre-wrap text-[10px] leading-relaxed text-offline/90 max-h-48 overflow-y-auto">
                                          {step.stderr_tail}
                                        </pre>
                                      </div>
                                    )}
                                    {step.output_path && (
                                      <div className="text-[10px] text-text-muted flex justify-between pt-1">
                                        <span>Output: {step.output_path}</span>
                                        {typeof step.return_code === 'number' && (
                                          <span>Exit Code: {step.return_code}</span>
                                        )}
                                      </div>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })}
                      {(!report.steps || report.steps.length === 0) && (
                        <tr>
                          <td colSpan={3} className="py-3 text-center text-text-muted">No execution steps recorded.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            {/* Right Column: Hard Gate Failures, Warnings, Diagnostics */}
            <div className="space-y-4">
              {/* Hard Gate Failures */}
              <div className="rounded-xl border border-border bg-surface-3 p-4 space-y-2">
                <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-text-muted font-bold font-mono">
                  <span className={`w-1.5 h-1.5 rounded-full ${report.hard_gate_failures?.length > 0 ? 'bg-offline animate-pulse' : 'bg-online'}`} />
                  <span>Hard Gate Failures ({report.hard_gate_failures?.length || 0})</span>
                </div>
                {report.hard_gate_failures && report.hard_gate_failures.length > 0 ? (
                  <ul className="space-y-1.5 pl-3 list-disc text-xs text-text-primary">
                    {report.hard_gate_failures.map((fail, i) => (
                      <li key={i} className="leading-relaxed select-text font-mono text-[11px] text-offline/90">{fail}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-text-muted italic">No hard gate failures</p>
                )}
              </div>

              {/* Warnings */}
              <div className="rounded-xl border border-border bg-surface-3 p-4 space-y-2">
                <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-text-muted font-bold font-mono">
                  <span className={`w-1.5 h-1.5 rounded-full ${report.warnings?.length > 0 ? 'bg-warning animate-pulse' : 'bg-text-muted'}`} />
                  <span>Warnings ({report.warnings?.length || 0})</span>
                </div>
                {report.warnings && report.warnings.length > 0 ? (
                  <ul className="space-y-1.5 pl-3 list-disc text-xs text-text-primary">
                    {report.warnings.map((warn, i) => (
                      <li key={i} className="leading-relaxed select-text font-mono text-[11px] text-warning/90">{warn}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-text-muted italic">No warnings</p>
                )}
              </div>

              {/* Diagnostics */}
              <div className="rounded-xl border border-border bg-surface-3 p-4 space-y-2">
                <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-text-muted font-bold font-mono">
                  <span className="w-1.5 h-1.5 rounded-full bg-text-secondary" />
                  <span>Diagnostics ({report.diagnostics?.length || 0})</span>
                </div>
                {report.diagnostics && report.diagnostics.length > 0 ? (
                  <ul className="space-y-1.5 pl-3 list-disc text-xs text-text-primary">
                    {report.diagnostics.map((diag, i) => (
                      <li key={i} className="leading-relaxed select-text font-mono text-[11px] text-text-secondary">{diag}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-text-muted italic">No diagnostics</p>
                )}
              </div>
            </div>
          </div>
        )
      ) : (
        // --- Tab 2: Manual Regression Panel ---
        <div className="space-y-5">
          {/* Read-Only Informational Header */}
          <div className="rounded-xl border border-border bg-surface-3 p-4 space-y-2">
            <h4 className="text-xs font-semibold text-text-primary font-mono uppercase tracking-wider">Manual Regression Reference Panel</h4>
            <p className="text-xs text-text-secondary leading-relaxed">
              This panel documents the 15-query manual regression suite used to verify query-understanding intent, code retrieval accuracy, and multi-turn response grounding in CodeSeek. 
              To avoid high resource costs, regression runs are not triggered automatically during app sessions.
            </p>
            <div className="text-[10px] text-text-muted font-mono bg-base/50 p-2.5 rounded border border-border/40 select-text leading-relaxed">
              <strong>Execution Policy:</strong> Run regression checks manually via developer commands, for example:
              <br />
              <code className="text-accent-dim select-all">PYTHONPATH=backend .venv/bin/pytest backend/tests/test_code_snippet_answer.py</code>
            </div>
          </div>

          {/* Status bar */}
          <div className="rounded-xl border border-border bg-surface-3 p-4 flex flex-col md:flex-row md:items-center justify-between gap-3 font-mono text-xs">
            <div className="flex items-center gap-2">
              <span className="text-text-secondary">Regression Run Status:</span>
              <span className="inline-flex items-center rounded-full border border-border-subtle/50 px-2 py-0.5 text-[10px] font-semibold text-text-muted bg-surface-2">
                UNKNOWN
              </span>
            </div>
            <span className="text-text-muted italic">
              No manual regression result has been recorded yet.
            </span>
          </div>

          {/* Table list of 15 queries */}
          <div className="rounded-xl border border-border bg-surface-3 p-4">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs font-mono">
                <thead>
                  <tr className="text-text-muted border-b border-border-subtle/60">
                    <th className="pb-2.5 font-medium w-12 text-center">ID</th>
                    <th className="pb-2.5 font-medium">Regression Query Pattern</th>
                    <th className="pb-2.5 font-medium w-44">Category</th>
                    <th className="pb-2.5 font-medium w-48">Expected Intent</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle/30 text-text-secondary">
                  {REGRESSION_QUERIES.map((q) => (
                    <tr key={q.id} className="hover:bg-surface-2/30 transition-colors">
                      <td className="py-2.5 text-center font-bold text-text-muted">#{q.id}</td>
                      <td className="py-2.5 pr-4 select-all text-text-primary leading-relaxed">{q.query}</td>
                      <td className="py-2.5">{getCategoryBadge(q.category)}</td>
                      <td className="py-2.5 select-all text-[11px] text-text-muted">{q.intent}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
