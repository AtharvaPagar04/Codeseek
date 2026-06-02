import { useState, useCallback } from 'react';
import { getSessions, saveSessions } from '../utils/storage';

const sortByLastActive = (sessions) =>
  [...sessions].sort(
    (a, b) =>
      new Date(b.last_active || b.created_at) - new Date(a.last_active || a.created_at)
  );

export function useSessions() {
  const [sessions, setSessions] = useState(() => sortByLastActive(getSessions()));

  const persist = (next) => {
    saveSessions(next);
    return sortByLastActive(next);
  };

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
      messages: [],
    };
    setSessions((prev) => persist([newSession, ...prev.filter((s) => s.id !== newSession.id)]));
    return newSession;
  }, []);

  const deleteSession = useCallback((sessionId) => {
    setSessions((prev) => persist(prev.filter((s) => s.id !== sessionId)));
  }, []);

  const clearSessionMessages = useCallback((sessionId) => {
    const now = new Date().toISOString();
    setSessions((prev) => {
      const next = prev.map((session) =>
        session.id === sessionId
          ? { ...session, messages: [], last_active: now }
          : session
      );
      return persist(next);
    });
  }, []);

  const setSessionMessages = useCallback((sessionId, messages) => {
    setSessions((prev) => {
      const next = prev.map((session) =>
        session.id === sessionId
          ? { ...session, messages: Array.isArray(messages) ? messages : [] }
          : session
      );
      return persist(next);
    });
  }, []);

  /**
   * appendMessage supports two modes:
   *  1. Normal: append message to the session's messages array.
   *  2. Replace: if message has __replaceId, replace the message with that id instead of appending.
   *     This is used to swap the loading placeholder with the real assistant response.
   */
  const appendMessage = useCallback((sessionId, message) => {
    const now = new Date().toISOString();
    setSessions((prev) => {
      const next = prev.map((s) => {
        if (s.id !== sessionId) return s;

        let messages;
        if (message.__replaceId) {
          // Replace loading placeholder in-place
          const { __replaceId, ...realMessage } = message;
          messages = s.messages.map((m) => (m.id === __replaceId ? realMessage : m));
        } else {
          messages = [...s.messages, message];
        }

        return { ...s, last_active: now, messages };
      });
      return persist(next);
    });
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
          messages: current?.messages || [],
        });
      }
      const backendIds = new Set(backendSessions.map((s) => s.id));
      const merged = [...byId.values()].filter((s) => backendIds.has(s.id));
      return persist(merged);
    });
  }, []);

  return {
    sessions,
    addSession,
    deleteSession,
    clearSessionMessages,
    setSessionMessages,
    appendMessage,
    mergeBackendSessions,
  };
}
