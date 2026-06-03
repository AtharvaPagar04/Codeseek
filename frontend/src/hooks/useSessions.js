import { useState, useCallback } from 'react';

const sortByLastActive = (sessions) =>
  [...sessions].sort(
    (a, b) =>
      new Date(b.last_active || b.created_at) - new Date(a.last_active || a.created_at)
  );

export function useSessions() {
  const [sessions, setSessions] = useState([]);

  const normalizeThreads = (threads) =>
    Array.isArray(threads) ? threads.map((thread) => ({ ...thread, messages: thread.messages || [] })) : [];

  const addSession = useCallback((sessionData) => {
    const now = new Date().toISOString();
    const repoFullName = sessionData.repo_full_name || '';
    const repoId = repoFullName.split('/').pop() || sessionData.repo_id || 'repository';
    const newSession = {
      id: sessionData.id,
      repo_id: repoId,
      repo_full_name: repoFullName,
      repo_description: sessionData.repo_description || '',
      repo_private: sessionData.repo_private ?? false,
      status: sessionData.status || 'indexing',
      error: sessionData.error || '',
      created_at: sessionData.created_at || now,
      last_active: now,
      threads: [],
      active_thread_id: null,
    };
    setSessions((prev) =>
      sortByLastActive([newSession, ...prev.filter((s) => s.id !== newSession.id)])
    );
    return newSession;
  }, []);

  const deleteSession = useCallback((sessionId) => {
    setSessions((prev) => sortByLastActive(prev.filter((s) => s.id !== sessionId)));
  }, []);

  const clearSessionMessages = useCallback((sessionId) => {
    const now = new Date().toISOString();
    setSessions((prev) => {
      const next = prev.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              threads: session.threads.map((thread) =>
                thread.id === session.active_thread_id ? { ...thread, messages: [] } : thread
              ),
              last_active: now,
            }
          : session
      );
      return sortByLastActive(next);
    });
  }, []);

  const setThreadMessages = useCallback((sessionId, threadId, messages) => {
    setSessions((prev) => {
      const next = prev.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              threads: session.threads.map((thread) =>
                thread.id === threadId
                  ? { ...thread, messages: Array.isArray(messages) ? messages : [] }
                  : thread
              ),
            }
          : session
      );
      return sortByLastActive(next);
    });
  }, []);

  /**
   * appendMessage supports two modes:
   *  1. Normal: append message to the session's messages array.
   *  2. Replace: if message has __replaceId, replace the message with that id instead of appending.
   *     This is used to swap the loading placeholder with the real assistant response.
   */
  const appendMessage = useCallback((sessionId, threadId, message) => {
    const now = new Date().toISOString();
    setSessions((prev) => {
      const next = prev.map((s) => {
        if (s.id !== sessionId) return s;

        const threads = s.threads.map((thread) => {
          if (thread.id !== threadId) return thread;
          let messages;
          if (message.__replaceId) {
            const { __replaceId, ...realMessage } = message;
            messages = (thread.messages || []).map((m) => (m.id === __replaceId ? realMessage : m));
          } else {
            messages = [...(thread.messages || []), message];
          }
          return { ...thread, messages };
        });

        return { ...s, last_active: now, threads };
      });
      return sortByLastActive(next);
    });
  }, []);

  const setSessionThreads = useCallback((sessionId, threads) => {
    setSessions((prev) => {
      const next = prev.map((session) =>
        session.id === sessionId
          ? (() => {
              const normalizedThreads = normalizeThreads(threads);
              const activeThreadId = normalizedThreads.some((thread) => thread.id === session.active_thread_id)
                ? session.active_thread_id
                : normalizedThreads[0]?.id || null;
              return {
                ...session,
                threads: normalizedThreads,
                active_thread_id: activeThreadId,
              };
            })()
          : session
      );
      return sortByLastActive(next);
    });
  }, []);

  const setActiveThread = useCallback((sessionId, threadId) => {
    setSessions((prev) =>
      sortByLastActive(
        prev.map((session) =>
          session.id === sessionId ? { ...session, active_thread_id: threadId } : session
        )
      )
    );
  }, []);

  const addThread = useCallback((sessionId, thread) => {
    setSessions((prev) =>
      sortByLastActive(
        prev.map((session) =>
          session.id === sessionId
            ? {
                ...session,
                threads: [...session.threads, { ...thread, messages: [] }],
                active_thread_id: thread.id,
              }
            : session
        )
      )
    );
  }, []);

  const mergeBackendSessions = useCallback((backendSessions) => {
    setSessions((prev) => {
      const byId = new Map(prev.map((s) => [s.id, s]));
      for (const b of backendSessions) {
        const current = byId.get(b.id);
        const repoFullName = b.repo_full_name || current?.repo_full_name || '';
        const repoId = repoFullName.split('/').pop() || current?.repo_id || 'repository';
        byId.set(b.id, {
          ...current,
          id: b.id,
          repo_id: repoId,
          repo_full_name: repoFullName,
          status: b.status || current?.status || 'indexing',
          error: b.error || '',
          created_at: b.created_at || current?.created_at,
          last_active: current?.last_active || b.updated_at || b.created_at,
          threads: current?.threads || [],
          active_thread_id: current?.active_thread_id || null,
        });
      }
      const backendIds = new Set(backendSessions.map((s) => s.id));
      const merged = [...byId.values()].filter((s) => backendIds.has(s.id));
      return sortByLastActive(merged);
    });
  }, []);

  return {
    sessions,
    addSession,
    deleteSession,
    clearSessionMessages,
    setThreadMessages,
    appendMessage,
    mergeBackendSessions,
    setSessionThreads,
    setActiveThread,
    addThread,
  };
}
