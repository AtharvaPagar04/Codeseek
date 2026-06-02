# Codeseek Frontend — Project Summary

Welcome to the **Codeseek Frontend**, a developer-focused, high-density, terminal-native web application designed as the user interface for the Codeseek RAG-based code assistant.

Below is the definitive summary of everything that has been implemented, validated, and is fully functional.

---

## 🛠 Tech Stack & Architecture

- **Core:** React 18 (functional components, custom hooks) + Vite + React Router v6.
- **Styling:** Tailwind CSS configured with a dark-native developer theme (`#0d0f11` background, electric cyan `#00F0FF` accents, high contrast elements).
- **Markdown & Code:** Custom `react-markdown` + `remark-gfm` integration to render structured answers, markdown tables, and syntax-highlighted block code.
- **Persistence:** High-safety `localStorage` wrappers with JSON-parsing try/catch recovery blocks to ensure robust state preservation across page reloads.

---

## 🚀 Fully Functional Features

### 1. Unified Dashboard Shell (`App.jsx`)
* **Responsive Layout:** Dynamic fixed sidebar for session lists that collapses into an off-screen drawer on mobile screens.
* **Persistent States:** Track active sessions, connection states, and settings in real-time across the app shell.

### 2. Rich Session Management (`useSessions.js` & `SessionView.jsx`)
* **Full CRUD Operations:** Instantly create, select, search, and delete chat sessions.
* **Excerpts & History:** The sidebar dynamically displays truncated previews of the last chat message along with relative dates formatted via `date-fns`.
* **Clean Deletion Safety:** A custom modal confirmation (`ConfirmDialog.jsx`) with keyboard bindings (`Escape` key listeners) protects against accidental session deletions.

### 3. Reactive Chat Interface (`useChat.js` & `MessageBubble.jsx`)
* **Dual-state Message Rendering:** Differentiates user questions (right-aligned, compact) and AI answers (left-aligned, rich markdown).
* **Smooth Auto-Scroll:** Chat windows automatically lock-scroll to the bottom on new message arrivals or streaming outputs.
* **Smart Textarea Resizing:** Auto-expands input blocks as you type multi-line code queries, with instant `Enter` key bindings to submit and `Shift+Enter` for line breaks.
* **Immediate Response Injection:** Appends a live placeholder dot animation immediately upon query execution, replacing it seamlessly with the actual LLM response upon return.

### 4. Advanced Source Citation (`SourceCard.jsx`)
* **Grounding References:** Generates interactive inline reference tags for each source file utilized by the LLM response.
* **Rich Metadata:** Displays the cited file path, symbol, line-range, and repository context.
* **One-Click Clipboard:** Built-in copy-to-clipboard functionality for easily extracting file references.

### 5. Multi-API Token Management (`ApiTokensModal.jsx`)
* **"API Config" Dashboard:** Access a secure panel straight from the status bar key icon.
* **Multiple Token Storage:** Add, manage, and describe multiple different API keys (e.g. `"Local Dev RAG"`, `"Prod GPU Server"`, `"Backup Server"`).
* **On-the-fly Toggling:** Select any key instantly via radio selectors to switch active backends without editing configuration files.

### 6. GitHub Integration & Fallback Auth (`RepoPickerModal.jsx` & `AuthCallback.jsx`)
* **Tab-Safe OAuth:** "Connect GitHub" opens OAuth workflows in a new tab securely.
* **404 Resilient Callback:** Handles non-configured backend callback errors gracefully. Instead of displaying a crash screen, it detects `/auth/github` missing routes and displays a sleek, secure **Personal Access Token (PAT) input form**.
* **Direct Token Verification:** Allows developers to input a GitHub PAT directly, bypassing OAuth completely. Validates the token immediately against the GitHub API and unlocks repository ingestion.

---

## 📶 Health & Status Monitoring (`useHealth.js` & `StatusBar.jsx`)
* **Live Health Polling:** Background task polls `/api/v1/health` every 60 seconds.
* **Aesthetic Status Indicator:** Displays API connection state via color-coded dots (Green = Online, Red = Degraded/Unreachable) next to the active GitHub username/avatar profile.

---

## 📂 Project Directory Structure

```bash
Codeseek-Frontend/
├── .env                  # active configurations
├── index.html            # HTML mount point
├── package.json          # Vite + React packages & scripts
├── postcss.config.js     # ESM compatible PostCSS configs
├── tailwind.config.js    # Developer-dark theme configuration
├── vite.config.js        # Vite compiler configurations
└── src/
    ├── App.jsx           # Main routing & layout controller
    ├── main.jsx          # Bootstrap entry point
    ├── index.css         # Monospace font loads & custom scrollbars
    ├── components/
    │   ├── ApiTokensModal.jsx   # Multi-token settings manager
    │   ├── ConfirmDialog.jsx    # Destruction verification overlays
    │   ├── EmptyState.jsx       # Interactive query chip suggestions
    │   ├── MessageBubble.jsx    # Code & markdown rendering bubbles
    │   ├── RepoPickerModal.jsx  # OAuth & PAT repository browser
    │   ├── SessionItem.jsx      # Sidebar list nodes
    │   ├── SessionView.jsx      # Scrollable dialogue board
    │   ├── Sidebar.jsx          # Left-pane navigation drawer
    │   ├── SourceCard.jsx       # Citation card drawer
    │   └── StatusBar.jsx        # Status ticker & account links
    ├── hooks/
    │   ├── useChat.js           # LLM submission state trackers
    │   ├── useGitHub.js         # Token & listing aggregators
    │   ├── useHealth.js         # 60s FastAPI ping monitors
    │   └── useSessions.js       # localStorage CRUD synchronizers
    ├── pages/
    │   └── AuthCallback.jsx     # OAuth redirect & verification page
    └── utils/
        ├── api.js               # Centralized backend client
        ├── github.js            # GitHub API endpoint wrappers
        └── storage.js           # Protected localStorage access
```

---

## ⚡ Setup & Launch

Run the following commands in the project folder to launch:

```bash
# 1. Prepare environment config
cp .env.example .env

# 2. Start developer sandbox
npm run dev

# 3. Compile optimized production release
npm run build
```
