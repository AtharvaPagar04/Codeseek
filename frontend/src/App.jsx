import { useState, useEffect, useRef } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import StatusBar from './components/StatusBar';
import Sidebar from './components/Sidebar';
import SessionView from './components/SessionView';
import RepoPickerModal from './components/RepoPickerModal';
import ApiTokensModal from './components/ApiTokensModal';
import LiveBackground from './components/LiveBackground';
import { useSessions } from './hooks/useSessions';
import { useGitHub } from './hooks/useGitHub';
import {
  clearSessionMessagesApi,
  clearThreadMessagesApi,
  createSession,
  deleteSessionApi,
  fetchThreadMessages,
  listSessions,
  listSessionThreads,
  retrySessionIndexing,
} from './utils/api';

function Shell() {
  const {
    sessions,
    addSession,
    deleteSession,
    clearSessionMessages,
    setThreadMessages,
    appendMessage,
    mergeBackendSessions,
    setSessionThreads,
  } = useSessions();
  const {
    isConnected,
    username,
    avatarUrl,
    repos,
    reposLoading,
    reposError,
    oauthLoading,
    oauthError,
    authStateMessage,
    initiateOAuth,
    storeAuth,
    fetchRepos,
    disconnect,
  } = useGitHub();

  const [activeSessionId, setActiveSessionId] = useState(() => sessions[0]?.id ?? null);
  const [modalOpen, setModalOpen] = useState(false);
  const [apiModalOpen, setApiModalOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => typeof window !== 'undefined' && window.innerWidth >= 768);
  const [uiNotice, setUiNotice] = useState(null);
  const pollingErrorShownRef = useRef(false);

  // Keep active session in sync when sessions change
  useEffect(() => {
    if (activeSessionId && sessions.find((s) => s.id === activeSessionId)) return;
    // Active session was deleted or doesn't exist — default to first
    setActiveSessionId(sessions[0]?.id ?? null);
  }, [sessions, activeSessionId]);

  const activeSession = sessions.find((s) => s.id === activeSessionId) ?? null;

  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      try {
        const remote = await listSessions();
        if (!stopped) mergeBackendSessions(remote);
        pollingErrorShownRef.current = false;
      } catch (err) {
        if (!pollingErrorShownRef.current) {
          console.warn('[sessions] polling failed:', err.message);
          pollingErrorShownRef.current = true;
        }
      }
    };
    tick();
    const id = setInterval(tick, 3000);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [mergeBackendSessions]);

  useEffect(() => {
    if (!activeSessionId) return;
    let cancelled = false;
    const loadThreads = async () => {
      try {
        const threads = await listSessionThreads(activeSessionId);
        if (!cancelled) {
          setSessionThreads(activeSessionId, threads);
        }
      } catch (err) {
        console.warn('[sessions] fetch threads failed:', err.message);
      }
    };
    loadThreads();
    return () => {
      cancelled = true;
    };
  }, [activeSessionId, setSessionThreads]);

  useEffect(() => {
    const activeThreadId = activeSession?.active_thread_id;
    if (!activeSessionId || !activeThreadId) return;
    let cancelled = false;
    const loadMessages = async () => {
      try {
        const messages = await fetchThreadMessages(activeThreadId);
        if (!cancelled) {
          setThreadMessages(activeSessionId, activeThreadId, messages);
        }
      } catch (err) {
        console.warn('[sessions] fetch thread messages failed:', err.message);
      }
    };
    loadMessages();
    return () => {
      cancelled = true;
    };
  }, [activeSessionId, activeSession?.active_thread_id, setThreadMessages]);

  const handleSelectRepo = async (repo) => {
    try {
      const existing = sessions.find((session) => session.repo_full_name === repo.full_name);
      const created = await createSession({
        repoFullName: repo.full_name,
        repoUrl: repo.clone_url || `https://github.com/${repo.full_name}.git`,
      });
      const newSession = addSession(created);
      setActiveSessionId(newSession.id);
      setUiNotice(
        existing
          ? {
              tone: 'info',
              message: `Opened existing session for ${repo.full_name}.`,
            }
          : {
              tone: 'success',
              message: `Created session for ${repo.full_name}. Indexing will continue in the background.`,
            }
      );
      setModalOpen(false);
      setSidebarOpen(false);
    } catch (err) {
      setUiNotice({ tone: 'error', message: err.message || 'Failed to create session.' });
    }
  };

  const handleDeleteSession = async (sessionId) => {
    try {
      await deleteSessionApi(sessionId);
    } catch (err) {
      console.warn('[sessions] delete api failed:', err.message);
      setUiNotice({ tone: 'error', message: err.message || 'Failed to delete session.' });
      return;
    }
    deleteSession(sessionId);
    if (sessionId === activeSessionId) {
      const remaining = sessions.filter((s) => s.id !== sessionId);
      setActiveSessionId(remaining[0]?.id ?? null);
    }
    setUiNotice({ tone: 'info', message: 'Session deleted.' });
  };

  const handleRetryIndexing = async (sessionId) => {
    try {
      const session = await retrySessionIndexing(sessionId);
      addSession(session);
      setUiNotice({
        tone: 'info',
        message: `Retrying indexing for ${session.repo_full_name}.`,
      });
    } catch (err) {
      setUiNotice({ tone: 'error', message: err.message || 'Failed to retry indexing.' });
    }
  };

  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768;

  return (
    <div className="flex flex-col h-screen bg-base text-text-primary overflow-hidden relative">
      <LiveBackground />
      <StatusBar
        ghUser={username}
        ghAvatarUrl={avatarUrl}
        onConnectGitHub={() => setModalOpen(true)}
        onDisconnectGitHub={disconnect}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
        isMobile={isMobile}
        onOpenApiTokens={() => setApiModalOpen(true)}
        activeSession={activeSession}
        githubNotice={authStateMessage}
      />
      {uiNotice && (
        <div className="px-4 pt-3">
          <div
            className={`mx-auto max-w-4xl rounded-xl border px-4 py-3 text-xs font-mono ${
              uiNotice.tone === 'error'
                ? 'border-offline/40 bg-offline/10 text-offline'
                : uiNotice.tone === 'success'
                  ? 'border-online/40 bg-online/10 text-online'
                  : 'border-border bg-surface-3/80 text-text-secondary'
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <span>{uiNotice.message}</span>
              <button
                onClick={() => setUiNotice(null)}
                className="shrink-0 text-text-muted hover:text-text-primary"
                aria-label="Dismiss notice"
              >
                ×
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-1 min-h-0 overflow-hidden relative">
        {/* Sidebar — desktop: toggleable, mobile: overlay drawer */}
        <div
          className={`
            shrink-0 overflow-hidden transition-all duration-200
            ${isMobile
              ? `absolute inset-y-0 left-0 z-30 w-64 ${sidebarOpen ? 'translate-x-0 shadow-2xl' : '-translate-x-full'}`
              : `${sidebarOpen ? 'w-64' : 'w-0'}`
            }
          `}
          style={{ borderRight: (isMobile || sidebarOpen) ? '1px solid #262626' : 'none' }}
        >
          <div className="w-64 h-full flex flex-col">
            <Sidebar
              sessions={sessions}
              activeSessionId={activeSessionId}
              onSelectSession={(id) => {
                setActiveSessionId(id);
                if (isMobile) setSidebarOpen(false);
              }}
              onDeleteSession={handleDeleteSession}
              onNewSession={() => setModalOpen(true)}
            />
          </div>
        </div>

        {/* Mobile sidebar backdrop */}
        {isMobile && sidebarOpen && (
          <div
            className="absolute inset-0 z-20 bg-black/50"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Main content */}
        <main className="flex-1 min-w-0 overflow-hidden">
          {activeSession ? (
            <SessionView
              key={activeSession.id}
              session={activeSession}
              appendMessage={appendMessage}
              onRetryIndexing={handleRetryIndexing}
              onClearMessages={async (sessionId) => {
                try {
                  const activeThreadId = activeSession.active_thread_id;
                  if (activeThreadId) {
                    await clearThreadMessagesApi(activeThreadId);
                  } else {
                    await clearSessionMessagesApi(sessionId);
                  }
                } catch (err) {
                  console.warn('[sessions] clear messages api failed:', err.message);
                  setUiNotice({ tone: 'error', message: err.message || 'Failed to clear messages.' });
                }
                clearSessionMessages(sessionId);
              }}
            />
          ) : (
            <NoSessionPlaceholder onNewSession={() => setModalOpen(true)} />
          )}
        </main>
      </div>

      {modalOpen && (
        <RepoPickerModal
          isConnected={isConnected}
          repos={repos}
          reposLoading={reposLoading}
          reposError={reposError}
          onSelect={handleSelectRepo}
          onClose={() => setModalOpen(false)}
          onConnectGitHub={initiateOAuth}
          onLoadRepos={fetchRepos}
          onSaveToken={storeAuth}
          oauthLoading={oauthLoading}
          oauthError={oauthError}
        />
      )}

      {apiModalOpen && (
        <ApiTokensModal onClose={() => setApiModalOpen(false)} />
      )}
    </div>
  );
}

function NoSessionPlaceholder({ onNewSession }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center gap-4 px-8">
      <div className="font-mono text-text-muted text-xs uppercase tracking-widest mb-1">Codeseek</div>
      <p className="text-text-secondary text-sm max-w-xs">
        No sessions yet. Create one to start asking questions about your code.
      </p>
      <button
        onClick={onNewSession}
        className="px-4 py-2 text-sm text-text-primary bg-surface-3 border border-border rounded-xl hover:bg-surface-2 hover:border-text-muted transition-colors"
      >
        + New Session
      </button>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/" element={<Shell />} />
      </Routes>
    </BrowserRouter>
  );
}
