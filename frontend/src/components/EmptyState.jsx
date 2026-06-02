/**
 * Shown in SessionView when there are no messages yet.
 * onChipClick: (text: string) => void — populate input without sending
 */
export default function EmptyState({ repoName, onChipClick }) {
  const examples = [
    'Where is the authentication logic?',
    'How is the database connection managed?',
    'What does the main entry point do?',
  ];

  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8 select-none">
      <div className="text-text-muted font-mono text-xs mb-2 uppercase tracking-widest">
        {repoName}
      </div>
      <h2 className="text-text-primary text-lg font-medium mb-1">
        Ask anything about{' '}
        <span className="text-accent font-mono">{repoName}</span>
      </h2>
      <p className="text-text-secondary text-sm mb-8">
        Answers are grounded in cited source files from this repository.
      </p>

      <div className="flex flex-col gap-2 w-full max-w-sm">
        {examples.map((ex) => (
          <button
            key={ex}
            onClick={() => onChipClick(ex)}
            className="text-left text-sm text-text-secondary border border-border rounded px-3.5 py-2.5 hover:border-accent/50 hover:text-text-primary hover:bg-accent-glow transition-colors font-sans"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}
