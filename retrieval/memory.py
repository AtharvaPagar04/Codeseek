"""In-process conversation memory."""


class ConversationMemory:
    """Store bounded query/answer turns for prompt continuity."""

    def __init__(self, max_turns: int):
        self.max_turns = max_turns
        self.turns: list[dict[str, str]] = []

    def add(self, query: str, answer: str) -> None:
        self.turns.append({"query": query, "answer": answer})
        if len(self.turns) > self.max_turns:
            self.turns.pop(0)

    def get_history_block(self) -> str:
        if not self.turns:
            return ""
        lines = ["--- CONVERSATION HISTORY ---"]
        for index, turn in enumerate(self.turns, start=1):
            lines.append(f"Q{index}: {turn['query']}")
            lines.append(f"A{index}: {turn['answer']}")
        lines.append("--- END HISTORY ---")
        return "\n".join(lines)
