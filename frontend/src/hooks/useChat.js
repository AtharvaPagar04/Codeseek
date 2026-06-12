import { useState, useCallback, useRef } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { querySession } from '../utils/api';

export function useChat({ appendMessage }) {
  const [isLoading, setIsLoading] = useState(false);
  // Track the current session so we can pair the placeholder message correctly
  const pendingSessionId = useRef(null);

  const sendMessage = useCallback(
    async (session, questionText) => {
      if (isLoading || !questionText.trim()) return;
      if (session.status && session.status !== 'ready') return;
      if (!session?.id) {
        throw new Error('Cannot query without a session id.');
      }

      const activeThreadId = session.active_thread_id || session.threads?.[0]?.id || '';
      if (!activeThreadId) {
        throw new Error('Conversation thread is still loading. Try again in a moment.');
      }
      const trimmed = questionText.trim();
      setIsLoading(true);
      pendingSessionId.current = session.id;

      // 1. Append user message immediately
      const userMessage = {
        id: uuidv4(),
        role: 'user',
        content: trimmed,
        sources: [],
        timestamp: new Date().toISOString(),
        error: false,
      };
      appendMessage(session.id, activeThreadId, userMessage);

      // 2. Append loading placeholder
      const loadingId = uuidv4();
      const loadingMessage = {
        id: loadingId,
        role: 'assistant',
        content: null,
        sources: [],
        timestamp: new Date().toISOString(),
        loading: true,
        error: false,
      };
      appendMessage(session.id, activeThreadId, loadingMessage);

      try {
        const data = await querySession({
          question: trimmed,
          session_id: session.id,
          thread_id: activeThreadId,
        });

        const assistantMessage = {
          id: loadingId, // reuse same id so we can replace in UI
          role: 'assistant',
          content: data.answer || '(no answer returned)',
          sources: data.sources || [],
          diagnostics: data.diagnostics || null,
          context_tokens: data.context_tokens,
          timestamp: new Date().toISOString(),
          loading: false,
          error: false,
        };

        // Replace loading placeholder — use a dedicated append that patches by id
        appendMessage(session.id, activeThreadId, { __replaceId: loadingId, ...assistantMessage });
      } catch (err) {
        console.error('[useChat] Query failed:', err);

        const errorMessage = {
          id: loadingId,
          role: 'assistant',
          content: err.message || 'Something went wrong. Please try again.',
          sources: [],
          timestamp: new Date().toISOString(),
          loading: false,
          error: true,
        };

        appendMessage(session.id, activeThreadId, { __replaceId: loadingId, ...errorMessage });
      } finally {
        setIsLoading(false);
        pendingSessionId.current = null;
      }
    },
    [isLoading, appendMessage]
  );

  return { isLoading, sendMessage };
}
