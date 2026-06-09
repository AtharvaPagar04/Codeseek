import React from 'react';

export default function EvaluationPanel({
  report,
  loading,
  error,
  onRefresh,
  sessionId,
  repoRoot,
  collection
}) {
  if (loading) {
    return (
      <div className="shrink-0 bg-surface-2 border-b border-border px-6 py-8 flex flex-col items-center justify-center gap-3">
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

  if (!report) return null;

  // Handle missing report state
  if (report.available === false) {
    const defaultRepoRoot = repoRoot || '/home/arch/DEV/CodeSeek';
    const defaultCollection = collection || 'repository_chunks__local__codeseek';
    
    return (
      <div className="shrink-0 bg-surface-2 border-b border-border px-6 py-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <h4 className="text-xs uppercase tracking-wider text-text-muted font-bold">Evaluation Health</h4>
            <div className="flex items-center gap-2 text-text-secondary text-xs">
              <span className="w-2 h-2 rounded-full bg-text-muted animate-pulse" />
              <span>{report.message || 'No evaluation report found.'}</span>
            </div>
          </div>
          <button
            onClick={onRefresh}
            className="py-1.5 px-3 text-2xs font-semibold rounded-lg bg-surface-3 border border-border hover:border-text-muted text-text-primary transition-colors flex items-center gap-1.5"
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
      <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-2xs font-mono uppercase font-semibold tracking-wide ${classes}`}>
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
      <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-2xs font-mono uppercase font-semibold tracking-wide ${classes}`}>
        {status || 'ERROR'}
      </span>
    );
  };

  return (
    <div className="shrink-0 bg-surface-2 border-b border-border px-6 py-5 animate-fadeIn relative z-10 space-y-5">
      {/* Header Info */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-border-subtle">
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wider text-text-muted font-bold font-mono">Evaluation Health Dashboard</div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-xs text-text-secondary font-mono">Overall Status:</span>
              {getStatusBadge(report.status)}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-text-secondary font-mono">Hard Gate Status:</span>
              {getHardGateBadge(report.hard_gate_status)}
            </div>
            {report.loaded_at && (
              <span className="text-[10px] text-text-muted font-mono" title={`Report file: ${report.report_path}`}>
                Loaded at: {new Date(report.loaded_at).toLocaleString()}
              </span>
            )}
          </div>
        </div>

        <button
          onClick={onRefresh}
          className="self-start md:self-center py-1.5 px-3 text-2xs font-semibold rounded-lg bg-surface-3 border border-border hover:border-text-muted text-text-primary transition-colors flex items-center gap-1.5"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1 1 21.306 7M7 9a5 5 0 0 1 10 0" />
          </svg>
          <span>Refresh Report</span>
        </button>
      </div>

      {/* Grid Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left Column: Recommendation & Steps */}
        <div className="space-y-4">
          {report.recommendation && (
            <div className="rounded-xl border border-border bg-surface-3 p-4 space-y-1">
              <div className="text-[10px] uppercase tracking-wider text-text-muted font-bold font-mono">Recommendation</div>
              <p className="text-xs text-text-primary leading-relaxed">{report.recommendation}</p>
            </div>
          )}

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
                  {report.steps && report.steps.map((step) => (
                    <tr key={step.name} className="hover:bg-surface-2/40 transition-colors">
                      <td className="py-2.5 font-semibold text-text-secondary select-all">{step.name}</td>
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
                  ))}
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
    </div>
  );
}
