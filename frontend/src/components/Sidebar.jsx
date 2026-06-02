import SessionItem from './SessionItem';

export default function Sidebar({ sessions, activeSessionId, onSelectSession, onDeleteSession, onNewSession }) {
  return (
    <aside className="flex flex-col h-full bg-surface border-r border-border w-full">
      {/* Session list — scrollable */}
      <div className="flex-1 overflow-y-auto py-2 px-1 space-y-0.5 scrollbar-thin">
        {sessions.length === 0 ? (
          <div className="px-3 py-8 text-center text-text-muted text-xs">
            No sessions yet.
            <br />
            Create one below.
          </div>
        ) : (
          sessions.map((session) => (
            <SessionItem
              key={session.id}
              session={session}
              isActive={session.id === activeSessionId}
              onSelect={() => onSelectSession(session.id)}
              onDelete={onDeleteSession}
            />
          ))
        )}
      </div>

      {/* New session button — pinned at bottom */}
      <div className="p-3 border-t border-border">
        <button
          onClick={onNewSession}
          className="w-full py-2 text-sm font-medium text-accent border border-accent/40 rounded hover:bg-accent-glow hover:border-accent transition-colors"
        >
          + New Session
        </button>
      </div>
    </aside>
  );
}
