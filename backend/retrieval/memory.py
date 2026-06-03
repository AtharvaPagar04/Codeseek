"""Conversation memory helpers."""

from retrieval.chat_store import (
    list_session_messages,
    list_session_turns,
    list_thread_messages,
    list_thread_turns,
)
from retrieval.memory_store import (
    get_session_memory,
    get_thread_memory,
    save_session_memory,
    save_thread_memory,
)


class ConversationMemory:
    """Store bounded query/answer turns for prompt continuity."""

    def __init__(self, max_turns: int):
        self.max_turns = max_turns
        self.turns: list[dict[str, str]] = []

    def add(self, query: str, answer: str, resolved_query: str | None = None) -> None:
        self.turns.append(
            {
                "query": query,
                "answer": answer,
                "resolved_query": resolved_query or query,
            }
        )
        if len(self.turns) > self.max_turns:
            self.turns.pop(0)

    def latest_query(self) -> str:
        if not self.turns:
            return ""
        return self.turns[-1].get("query", "")

    def latest_resolved_query(self) -> str:
        if not self.turns:
            return ""
        return self.turns[-1].get("resolved_query", "") or self.turns[-1].get("query", "")

    def get_history_block(self) -> str:
        if not self.turns:
            return ""
        lines = ["--- CONVERSATION HISTORY ---"]
        for index, turn in enumerate(self.turns, start=1):
            lines.append(f"Q{index}: {turn['query']}")
            lines.append(f"A{index}: {turn['answer']}")
        lines.append("--- END HISTORY ---")
        return "\n".join(lines)


class SessionConversationMemory:
    """DB-backed session memory using rolling summaries + recent turns."""

    def __init__(self, session_id: str, max_turns: int):
        self.session_id = session_id
        self.max_turns = max_turns

    @property
    def turns(self) -> list[dict[str, str]]:
        return list_session_turns(self.session_id)

    def add(self, query: str, answer: str, resolved_query: str | None = None) -> None:
        turns = self.turns + [
            {
                "query": query,
                "answer": answer,
                "resolved_query": resolved_query or query,
            }
        ]
        rolling_summary = ""
        if len(turns) > self.max_turns:
            older_turns = turns[:-self.max_turns]
            rolling_summary = _summarize_turns(older_turns)
        save_session_memory(
            self.session_id,
            rolling_summary=rolling_summary,
            last_resolved_query=(resolved_query or query).strip(),
        )

    def latest_query(self) -> str:
        messages = list_session_messages(self.session_id)
        for message in reversed(messages):
            if message.get("role") == "user":
                return str(message.get("content", ""))
        return ""

    def latest_resolved_query(self) -> str:
        state = get_session_memory(self.session_id)
        if state["last_resolved_query"]:
            return state["last_resolved_query"]
        return self.latest_query()

    def get_history_block(self) -> str:
        state = get_session_memory(self.session_id)
        recent_turns = self.turns[-self.max_turns :]
        if not state["rolling_summary"] and not recent_turns:
            return ""

        lines = []
        if state["rolling_summary"]:
            lines.append("--- CONVERSATION SUMMARY ---")
            lines.append(state["rolling_summary"])
            lines.append("--- END SUMMARY ---")
        if recent_turns:
            lines.append("--- CONVERSATION HISTORY ---")
            for index, turn in enumerate(recent_turns, start=1):
                lines.append(f"Q{index}: {turn['query']}")
                lines.append(f"A{index}: {turn['answer']}")
            lines.append("--- END HISTORY ---")
        return "\n".join(lines)


class ThreadConversationMemory:
    """DB-backed thread memory using rolling summaries + recent turns."""

    def __init__(self, thread_id: str, session_id: str, max_turns: int):
        self.thread_id = thread_id
        self.session_id = session_id
        self.max_turns = max_turns

    @property
    def turns(self) -> list[dict[str, str]]:
        return list_thread_turns(self.thread_id)

    def add(self, query: str, answer: str, resolved_query: str | None = None) -> None:
        turns = self.turns + [
            {
                "query": query,
                "answer": answer,
                "resolved_query": resolved_query or query,
            }
        ]
        rolling_summary = ""
        if len(turns) > self.max_turns:
            older_turns = turns[:-self.max_turns]
            rolling_summary = _summarize_turns(older_turns)
        save_thread_memory(
            self.thread_id,
            rolling_summary=rolling_summary,
            last_resolved_query=(resolved_query or query).strip(),
        )

    def latest_query(self) -> str:
        messages = list_thread_messages(self.thread_id)
        for message in reversed(messages):
            if message.get("role") == "user":
                return str(message.get("content", ""))
        return ""

    def latest_resolved_query(self) -> str:
        state = get_thread_memory(self.thread_id)
        if state["last_resolved_query"]:
            return state["last_resolved_query"]
        return self.latest_query()

    def get_history_block(self) -> str:
        state = get_thread_memory(self.thread_id)
        recent_turns = self.turns[-self.max_turns :]
        if not state["rolling_summary"] and not recent_turns:
            return ""

        lines = []
        if state["rolling_summary"]:
            lines.append("--- CONVERSATION SUMMARY ---")
            lines.append(state["rolling_summary"])
            lines.append("--- END SUMMARY ---")
        if recent_turns:
            lines.append("--- CONVERSATION HISTORY ---")
            for index, turn in enumerate(recent_turns, start=1):
                lines.append(f"Q{index}: {turn['query']}")
                lines.append(f"A{index}: {turn['answer']}")
            lines.append("--- END HISTORY ---")
        return "\n".join(lines)


def _summarize_turns(turns: list[dict[str, str]]) -> str:
    summary_lines = []
    for turn in turns[-12:]:
        query = " ".join(str(turn.get("query", "")).split())
        answer = " ".join(str(turn.get("answer", "")).split())
        if len(answer) > 220:
            answer = answer[:217].rstrip() + "..."
        summary_lines.append(f"- Q: {query}\n  A: {answer}")
    return "\n".join(summary_lines)
