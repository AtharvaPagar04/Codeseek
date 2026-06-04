import { useState } from 'react';

/**
 * Compact badge showing a cited source file.
 * Clicking the file path copies it to clipboard.
 */
export default function SourceCard({ source }) {
  const [copied, setCopied] = useState(false);
  const file = source.file || source.relative_path || '';
  const symbol = source.symbol || source.symbol_name || '';
  const lines = source.lines || formatLines(source.start_line, source.end_line);
  const expansionType = source.expansion_type || '';
  const copyValue = [file, symbol ? `:: ${symbol}` : '', lines ? ` (lines ${lines})` : ''].join('');

  const handleCopy = () => {
    if (!file) return;
    navigator.clipboard.writeText(copyValue).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="inline-flex items-center gap-2 bg-surface-3 border border-border rounded-full px-3 py-1 text-2xs font-mono">
      <button
        onClick={handleCopy}
        title="Copy path"
        className="text-text-primary hover:text-text-secondary transition-colors truncate max-w-[240px]"
      >
        {copied ? '✓ copied' : file || 'unknown source'}
      </button>

      {lines && (
        <span className="text-text-muted shrink-0">
          L{lines.replace('-', '–')}
        </span>
      )}

      {symbol && (
        <span className="bg-surface-2 text-text-secondary px-1.5 py-0.5 rounded-full text-2xs shrink-0">
          {symbol}
        </span>
      )}

      {expansionType && expansionType !== 'primary' && (
        <span className="text-text-muted shrink-0 uppercase tracking-wide">
          {expansionType.replace(/_/g, ' ')}
        </span>
      )}
    </div>
  );
}

function formatLines(startLine, endLine) {
  const start = Number(startLine);
  const end = Number(endLine);
  if (!Number.isFinite(start) || start <= 0) return '';
  if (!Number.isFinite(end) || end <= 0 || end === start) return String(start);
  return `${start}-${end}`;
}
