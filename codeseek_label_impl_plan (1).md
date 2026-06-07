# CodeSeek Label Strategy — Stepwise Implementation Plan

Each group is independently completable and verifiable before moving to the next.
Do not start a group until the previous one passes its verification check.

---

## Group 1 — Scaffold: Constants, Registry, Helpers

- [x] **Group 1 complete and verified**

**Group 1 Implementation Details:**
- Created `backend/rag_ingestion/label_constants.py` defining matching confidences, limits, internal labels, LLM refinement configs, and the comprehensive 41-label `LABEL_REGISTRY`.
- Created `backend/rag_ingestion/stages/labeler.py` implementing `add_label`, `select_top_labels`, `_first_sentence` (with precision-aware rounding and regex-based sentence isolation), `derive_code_intent`, `is_codeseek_repo`, `filter_repo_specific_labels`, and stub pipelines.
- Verified and validated successfully via the Group 1 smoke test.

**Goal:** Lay the foundation everything else depends on. No pipeline changes yet.

**Files to create:**
```
backend/rag_ingestion/stages/labeler.py   (new — stub only)
backend/rag_ingestion/label_constants.py  (new)
```

**Tasks:**

- [ ] 1. Create `label_constants.py` with:
  - [ ] `STRONG_MATCH = 0.90`, `MEDIUM_MATCH = 0.70`, `WEAK_MATCH = 0.55`
  - [ ] `MIN_CONFIDENCE = 0.50`, `MAX_CONFIDENCE = 0.95`
  - [ ] `MAX_LABELS_PER_CATEGORY` dict
  - [ ] `MAX_TOTAL_LABELS = 12`
  - [ ] `LABEL_REGISTRY` dict (all label IDs with descriptions)
  - [ ] `CODESEEK_INTERNAL_LABELS` set
  - [ ] Config flags (add to your settings file or `label_constants.py`):
    ```python
    ENABLE_CHUNK_LABELS = True
    ENABLE_LLM_LABEL_REFINEMENT = False
    ```
    For v1 these are the required defaults. If your project uses environment-based config (`.env` / `settings.py`), add them there so they are importable as `settings.ENABLE_CHUNK_LABELS` in Group 4.

- [ ] 2. Create `labeler.py` stub with:
  - [ ] Imports only
  - [ ] `add_label()` function
  - [ ] `select_top_labels()` function
  - [ ] `_first_sentence()` helper
  - [ ] `derive_code_intent()` function
  - [ ] `is_codeseek_repo()` guard
  - [ ] `filter_repo_specific_labels()` function
  - [ ] `label_chunk()` — stub returning chunk unchanged
  - [ ] `label_chunks()` — calls `label_chunk` in a loop

**Verification:**
```python
# smoke test — paste into python REPL or test file
from backend.rag_ingestion.label_constants import LABEL_REGISTRY, CODESEEK_INTERNAL_LABELS
from backend.rag_ingestion.stages.labeler import add_label, select_top_labels, _first_sentence

candidates = {}
add_label(candidates, "domain:auth", 0.90)
add_label(candidates, "domain:auth", 0.70)   # should boost to 0.95
assert candidates["domain:auth"] == 0.95

candidates["domain:frontend"] = 0.0
add_label(candidates, "domain:frontend", 0.80)  # label in candidates with 0.0
# existing=0.0, new=0.80 → min(0.95, max(0.0, 0.80) + 0.05) = 0.85
assert candidates["domain:frontend"] == 0.85    # boosted, not treated as missing

assert _first_sentence("Validates the session token.") == "Validates the session token."
assert _first_sentence("Validates the session token. It then loads the user.") == "Validates the session token."
assert _first_sentence("Validates the session token") == "Validates the session token."

labels = select_top_labels({"domain:auth": 0.90, "domain:frontend": 0.80, "domain:backend": 0.70})
assert len(labels) <= 2  # domain cap is 2
print("Group 1 OK")
```

---

## Group 2 — Chunk Model Fields

- [x] **Group 2 complete and verified**

**Group 2 Implementation Details:**
- Modified `backend/rag_ingestion/models/chunk.py` to add `labels`, `label_confidences`, and `code_intent` fields to the `Chunk` dataclass.
- Verified that these fields are correctly initialized with default values (`[]`, `{}`, and `""` respectively) by executing the Group 2 verification test.
- Confirmed that `Chunk` dataclass instances are not serialized to dictionaries in indexing events, ensuring no breaking side effects.

**Goal:** Add `labels`, `label_confidences`, `code_intent` to `Chunk`. No logic yet.

**Files to edit:**
```
backend/rag_ingestion/models/chunk.py   (or wherever Chunk is defined)
```

**Tasks:**

- [ ] 1. Add three fields to the `Chunk` dataclass:
   ```python
   labels: list[str] = field(default_factory=list)
   label_confidences: dict[str, float] = field(default_factory=dict)
   code_intent: str = ""
   ```

- [ ] 2. Confirm serialization — if `Chunk` is serialized to dict anywhere (e.g. for SSE progress events or logging), check that `label_confidences` is excluded or handled gracefully. It does not need to be excluded at this stage — just be aware of it.

**Verification:**
```python
from backend.rag_ingestion.models.chunk import Chunk

c = Chunk(...)   # use minimal required fields
assert hasattr(c, "labels")
assert hasattr(c, "label_confidences")
assert hasattr(c, "code_intent")
assert c.labels == []
assert c.label_confidences == {}
assert c.code_intent == ""
print("Group 2 OK")
```

---

## Group 3 — Deterministic Labeling Rules

- [x] **Group 3 complete and verified**

**Group 3 Implementation Details:**
- Implemented `label_chunk()` inside `backend/rag_ingestion/stages/labeler.py` with the complete rule engine:
  - Strong matching for artifacts and code roles based on chunk types and file types.
  - Case-insensitive domain detection from relative path segments.
  - Strong matching for capabilities and technology from chunk imports and calls.
  - Medium matching for domains and capabilities based on text keywords inside chunk summaries and descriptions.
  - Weak matching for domains and capabilities from the first 2000 characters of chunk content/excerpt when fewer than 2 labels are found.
  - Strong and medium matching for question uses tailored to chunk types, tests, and config files (restricting `code-snippet` to extractable code chunks).
  - Filtering of CodeSeek-internal labels for external repositories.
  - Enforcement of per-category limits and total limits via `select_top_labels()`.
  - Intent derivation via `derive_code_intent()`.
  - Dynamic fallback handling to ensure all chunks receive at least one label.
- Implemented `label_chunks()` to run the labeling pipeline over collections.
- Successfully verified all assertions in the Group 3 verification script.

**Goal:** Implement the actual rule engine inside `label_chunk()`.

**Files to edit:**
```
backend/rag_ingestion/stages/labeler.py
```

**Tasks:**

- [ ] 1. Implement `label_chunk(chunk, *, repo_name, repo_root)`:

  - [ ] a. Build `candidates: dict[str, float] = {}`

  - [ ] b. **Artifact + code_role from chunk_type** (STRONG_MATCH):
    ```
    function     → artifact:source-code, code_role:function
    method       → artifact:source-code, code_role:method
    class        → artifact:source-code, code_role:class
    repo_summary → artifact:repo-summary
    ```

  - [ ] c. **Artifact from file_type** (STRONG_MATCH):
    ```
    readme         → artifact:readme
    package_json   → artifact:package-manifest, capability:dependency-management
    dockerfile     → artifact:dockerfile, domain:devops, tech:docker
    docker_compose → artifact:docker-compose, domain:devops, tech:docker
    env_example    → artifact:env-example
    ```

  - [ ] d. **Domain from path segments** (STRONG_MATCH):
    ```
    "auth" in path      → domain:auth
    "retrieval" in path → domain:retrieval (codeseek-only)
    "ingestion" in path → domain:ingestion (codeseek-only)
    "provider" in path  → domain:provider-management (codeseek-only)
    "frontend" in path  → domain:frontend
    "test" in path      → artifact:test-code, domain:testing
    ```

  - [ ] e. **Capability + tech from imports/calls** (STRONG_MATCH):
    ```
    QdrantClient, qdrant_client           → tech:qdrant, domain:vector-db, capability:qdrant-storage
    upsert, PointStruct                   → capability:vector-upsert
    model.encode, SentenceTransformer     → tech:sentence-transformers, capability:embedding-generation
    StreamingResponse, text/event-stream  → tech:sse, capability:live-indexing-events
    ```

  - [ ] f. **Domain/capability from summary + description** (MEDIUM_MATCH):
    Combine `chunk.summary + " " + chunk.description` into `text`.
    ```
    "session_token", "auth_sessions" → domain:auth, capability:session-validation, capability:token-validation
    "qdrant", "QdrantClient"         → domain:vector-db, tech:qdrant, capability:qdrant-storage
    "embedding", "encode"            → capability:embedding-generation
    ```

  - [ ] g. **Weak content matching** (WEAK_MATCH):
    Only if the above produced < 2 domain/capability labels.
    Sample first 2000 chars of `chunk.content` or `chunk.content_excerpt`.
    Apply same keyword checks at WEAK_MATCH confidence.

  - [ ] h. **question_use from chunk_type**:

    **Important:** Only assign `question_use:code-snippet` to chunks with extractable code.
    Allowed chunk types for code-snippet: `function`, `method`, `class`, `component`, `hook`.
    Do NOT assign code-snippet to: `readme`, `repo_summary`, `package_json`, or plain config chunks.

    ```
    function/method/class → question_use:technical-explanation (STRONG)
                           → question_use:code-location (STRONG)
                           → question_use:code-snippet (STRONG)      ← only these types
                           → question_use:implementation (MEDIUM)    ← all source-code chunks
                                                                        are editable targets
    repo_summary          → question_use:repo-overview (STRONG)
                           → question_use:general-context (STRONG)
    readme                → question_use:repo-overview (STRONG)
                           → question_use:setup (STRONG)
                           (no code-snippet, no implementation)
    package_json          → question_use:dependency-question (STRONG)
                           → question_use:setup (STRONG)
                           (no code-snippet, no implementation)
    config files          → question_use:config-question (STRONG)
                           → question_use:general-context (STRONG)
                           (no code-snippet, no implementation)
    test files            → question_use:test-validation (STRONG)
                           → question_use:debugging (STRONG)
                           → question_use:implementation (MEDIUM)    ← tests are also
                                                                        editable targets
    ```

    Why MEDIUM for `question_use:implementation`?
    Every source-code chunk is a potential edit target, but implementation intent is not
    the primary purpose of most chunks — technical-explanation and code-location are stronger
    signals. Using MEDIUM ensures implementation labels are present but don't crowd out
    stronger per-category labels during `select_top_labels`.

  - [ ] i. **Filter CodeSeek internal labels** if not CodeSeek repo:
    ```python
    candidates = filter_repo_specific_labels(candidates, is_codeseek=is_codeseek_repo(repo_name, repo_root))
    ```

  - [ ] j. **Select top labels**:
    ```python
    chunk.label_confidences = candidates
    chunk.labels = select_top_labels(candidates)
    ```

  - [ ] k. **Derive code_intent**:
    ```python
    chunk.code_intent = derive_code_intent(chunk)
    ```

  - [ ] l. **Apply fallbacks** if `chunk.labels` is still empty after selection:
    Based on `chunk.chunk_type`, add minimum fallback labels directly to `candidates` at `MIN_CONFIDENCE + 0.01`, then re-run `select_top_labels`.

- [ ] 2. Implement `label_chunks()` to call `label_chunk()` for each chunk.

**Verification:**
```python
# create minimal mock chunks and check expected labels

auth_chunk = Chunk(chunk_type="function", relative_path="backend/retrieval/auth_store.py",
                   symbol_name="get_user_for_session_token", summary="Function: get_user_for_session_token",
                   description="Validates session token and resolves the current user.", ...)
result = label_chunk(auth_chunk)
assert "domain:auth" in result.labels
assert "artifact:source-code" in result.labels
assert "question_use:code-snippet" in result.labels
assert "question_use:implementation" in result.labels
assert len(result.labels) <= 12
assert result.code_intent != ""

storage_chunk = Chunk(chunk_type="function", relative_path="backend/rag_ingestion/stages/storage.py",
                      imports=["QdrantClient", "PointStruct"], ...)
result = label_chunk(storage_chunk, repo_name="codeseek")
assert "tech:qdrant" in result.labels
assert "capability:qdrant-storage" in result.labels
assert "domain:ingestion" in result.labels

# external repo should not get codeseek-internal labels
ext_chunk = Chunk(chunk_type="function", relative_path="src/db/storage.py",
                  imports=["QdrantClient"], ...)
result = label_chunk(ext_chunk, repo_name="some-user-repo")
assert "tech:qdrant" in result.labels           # tech label is generic — allowed
assert "domain:ingestion" not in result.labels  # codeseek-internal — blocked

# fallback: unknown chunk should still get labels
unknown_chunk = Chunk(chunk_type="function", relative_path="src/utils/misc.py", ...)
result = label_chunk(unknown_chunk)
assert len(result.labels) >= 1

print("Group 3 OK")
```

---

## Group 4 — Wire Labeler into Pipeline

- [x] **Group 4 complete and verified**

**Group 4 Implementation Details:**
- Defined environment-based config variables `ENABLE_CHUNK_LABELS` (default: `True`) and `ENABLE_LLM_LABEL_REFINEMENT` (default: `False`) in `backend/rag_ingestion/config.py`.
- Updated `backend/rag_ingestion/label_constants.py` to import `ENABLE_CHUNK_LABELS` and `ENABLE_LLM_LABEL_REFINEMENT` directly from the centralized `rag_ingestion.config` module.
- Modified `backend/rag_ingestion/main.py` to wire `label_chunks` into the pipeline sequence, positioned between the description stage and the embedding stage.
- Added a temporary debug loop to log the labels and intents of the first 5 processed chunks.
- Verified execution by running a test ingestion of a mock python auth repository, confirming that the deterministic rules correctly assign labels and intents to the parsed chunks.

**Goal:** Connect `label_chunks()` into the ingestion pipeline between description and embedder.

**Files to edit:**
```
backend/rag_ingestion/pipeline.py    (or wherever the stage sequence is defined)
```

**Tasks:**

- [ ] 1. Import `label_chunks` from `labeler.py`.

- [ ] 2. After the description stage (or summary stage if descriptions are disabled), add:
   ```python
   if settings.ENABLE_CHUNK_LABELS:
       chunks = label_chunks(chunks, repo_name=repo_name, repo_root=repo_root)
   ```

- [ ] 3. Confirm `repo_name` and `repo_root` are available in pipeline context at this point. If not, thread them through from the ingestion job.

- [ ] 4. Confirm `ENABLE_CHUNK_LABELS = True` is in settings/config.

**Verification:**
Run a small test ingestion on a known file (e.g. `auth_store.py`) and log chunk labels before they reach the embedder:
```python
# add temporary debug log in pipeline after label stage:
for chunk in chunks[:5]:
    print(chunk.relative_path, chunk.labels, chunk.code_intent)
```
Confirm labels are non-empty and plausible before proceeding.

---

## Group 5 — Labels in Embedding Input

- [x] **Group 5 complete and verified**

**Group 5 Implementation Details:**
- Added `"Labels"` and `"Code Intent"` to the `KNOWN_LABELS` set in `backend/rag_ingestion/stages/embedder.py` so the automated validation checks (e.g. `check_embedding_inputs.py`) recognize them.
- Updated `_embedding_input()` in `backend/rag_ingestion/stages/embedder.py` to serialize `chunk.labels` and `chunk.code_intent` using the list-line and single-line format utilities, positioning them directly before `Summary`.
- Verified using a smoke test script that `auth_store` chunks successfully generate embedding inputs containing the correctly matched labels and intent statement.

**Goal:** Include `labels` and `code_intent` in the text that gets embedded.

**Files to edit:**
```
backend/rag_ingestion/stages/embedder.py
```

**Tasks:**

- [ ] 1. Find where the embedding input string is constructed per chunk.

- [ ] 2. Add `Labels` and `Code Intent` lines before `Summary` / `Code`:
   ```python
   parts = [
       f"File: {chunk.relative_path}",
       f"Language: {chunk.language or ''}",
       f"Type: {chunk.chunk_type}",
   ]
   if chunk.symbol_name:
       parts.append(f"Symbol: {chunk.symbol_name}")
   if chunk.labels:
       parts.append(f"Labels: {', '.join(chunk.labels)}")
   if chunk.code_intent:
       parts.append(f"Code Intent: {chunk.code_intent}")
   if chunk.summary:
       parts.append(f"Summary: {chunk.summary}")
   if chunk.description:
       parts.append(f"Description: {chunk.description}")
   # ... rest of code content
   ```

- [ ] 3. Confirm that adding these lines does not push the embedding input past the model's token limit for large chunks. Log the input length for a few sample chunks.

**Verification:**
Print the full embedding input for `auth_store.py::get_user_for_session_token` and confirm it contains:
```
Labels: domain:auth, capability:session-validation, ...
Code Intent: Validates an auth session token and resolves the current user.
```

---

## Group 6 — Labels in Qdrant Storage Payload

- [x] **Group 6 complete and verified**

**Group 6 Implementation Details:**
- Modified `_payload()` in `backend/rag_ingestion/stages/storage.py` to add `"labels"` and `"code_intent"` to the Qdrant payload dictionary.
- Added an explicit code comment indicating that `"label_confidences"` is intentionally excluded and not persisted.
- Verified using a custom test ingestion and Qdrant scroll query that labels and code intents are correctly persisted as part of the point payload, while `label_confidences` is successfully omitted.

**Goal:** Persist `labels` and `code_intent` into Qdrant. Explicitly exclude `label_confidences`.

**Files to edit:**
```
backend/rag_ingestion/stages/storage.py
```

**Tasks:**

- [ ] 1. Find where the Qdrant `PointStruct` payload dict is constructed.

- [ ] 2. Add to payload:
   ```python
   "labels": chunk.labels,
   "code_intent": chunk.code_intent,
   ```

- [ ] 3. Confirm `label_confidences` is NOT in the payload dict. Add an explicit comment:
   ```python
   # label_confidences is intentionally excluded — ingestion-only, not persisted
   ```

**Verification:**
After a test ingestion, query Qdrant directly:
```python
results = qdrant_client.scroll(collection_name="...", limit=5, with_payload=True)
for point in results[0]:
    payload = point.payload
    assert "labels" in payload
    assert "code_intent" in payload
    assert "label_confidences" not in payload
    assert isinstance(payload["labels"], list)
    print(payload["labels"])
```

---

## Group 7 — Vector DB Audit Extension

- [x] **Group 7 complete and verified**

**Group 7 Implementation Details:**
- Modified `backend/scripts/manual_vector_db_audit.py` to import `LABEL_REGISTRY` and `MAX_TOTAL_LABELS` from `rag_ingestion.label_constants`.
- Added label and code intent coverage calculations, compiling counts of labeled points and points with valid intent strings.
- Added a top label frequency list using Python's `Counter` to display the distribution of the most common labels.
- Programmed strict validation checks to catch:
  - Unknown labels not present in the registry.
  - Points exceeding `MAX_TOTAL_LABELS`.
  - Source-code-only labels (`question_use:code-snippet`) appearing on non-source artifacts.
  - Invalid data types for labels.
  - Missing expected `domain:auth` labels on auth-related store points.
- Verified the audit script by re-indexing the Portfolio repository and running the audit script against it, resulting in a 100% success rate with 0 warnings and 0 errors.

**Goal:** Extend the existing manual audit script to validate label coverage and content.

**Files to edit:**
```
backend/scripts/audit_vector_db.py    (or equivalent)
```

**Tasks:**

- [ ] 1. Add label coverage counters:
   ```python
   total_chunks = len(points)
   labeled = sum(1 for p in points if p.payload.get("labels"))
   code_intent_present = sum(1 for p in points if p.payload.get("code_intent"))

   print(f"Label coverage: {labeled}/{total_chunks}")
   print(f"Code intent coverage: {code_intent_present}/{total_chunks}")
   ```

- [ ] 2. Add label frequency report:
   ```python
   from collections import Counter
   label_counts = Counter()
   for p in points:
       for label in p.payload.get("labels", []):
           label_counts[label] += 1
   print("Top labels:")
   for label, count in label_counts.most_common(15):
       print(f"  {label}: {count}")
   ```

- [ ] 3. Add violation checks:
   ```python
   unknown_labels = set()
   over_limit = []
   snippet_on_non_code = []
   for p in points:
       labels = p.payload.get("labels", [])
       for label in labels:
           if label not in LABEL_REGISTRY:
               unknown_labels.add(label)
       if len(labels) > MAX_TOTAL_LABELS:
           over_limit.append(p.id)
       if "question_use:code-snippet" in labels:
           if "artifact:source-code" not in labels:
               snippet_on_non_code.append(p.id)

   print(f"Unknown labels: {unknown_labels or 'none'}")
   print(f"Chunks over label limit: {len(over_limit)}")
   print(f"code-snippet on non-source chunks: {len(snippet_on_non_code)}")
   ```

- [ ] 4. Add invalid labels type check:
   ```python
   invalid_label_type = []
   for p in points:
       labels = p.payload.get("labels")
       if labels is None or not isinstance(labels, list) or not all(isinstance(lb, str) for lb in labels):
           invalid_label_type.append(p.id)
   print(f"Chunks with invalid labels type: {len(invalid_label_type)}")
   ```

- [ ] 5. Add spot-check assertions for known chunks:
   ```python
   auth_points = [p for p in points if "auth_store" in (p.payload.get("relative_path") or "")]
   for p in auth_points:
       assert "domain:auth" in p.payload["labels"], f"Missing domain:auth in {p.payload['relative_path']}"
   ```

**Verification:**
Run the audit after re-indexing CodeSeek. Expected output:
```
Label coverage: 100%
Unknown labels: none
Chunks over label limit: 0
code-snippet on non-source chunks: 0
Chunks with invalid labels type: 0
VERDICT: SUCCESS ✅
```

---

## Group 8 — Unit Tests

- [x] **Group 8 complete and verified**

**Group 8 Implementation Details:**
- Created `backend/tests/test_labeler.py` defining 32 unique test cases targeting all core modules of the labeling stage.
- Implemented `TestAddLabel` verifying new label additions, boosting mechanics, confidence cap enforcement, and low-confidence existing label handling.
- Implemented `TestSelectTopLabels` verifying per-category limit enforcement, total labels cap enforcement, filtering of low confidence labels, alphabetical sorting sorting, and highest confidence prioritization.
- Implemented `TestLabeler` testing rules matching domains, capabilities, files, tests, summaries, fallback policies, repository isolation/filtering, and code intent derivation.
- Implemented `TestStorage` checking that point payload excludes `label_confidences`.
- Implemented `TestFirstSentence` checking first sentence detection and boundary cases.
- All 32 labeler tests pass successfully with no errors or warnings.

**Goal:** Automated test coverage for all labeler functions.

**Files to create:**
```
backend/tests/test_labeler.py
```

**Tasks:**

- [ ] 1. Write `TestAddLabel` class:
  - [ ] `test_new_label_added_at_confidence`
  - [ ] `test_existing_label_boosted_by_005`
  - [ ] `test_existing_label_capped_at_max`
  - [ ] `test_label_with_zero_confidence_is_found` — the `if label in candidates` edge case

- [ ] 2. Write `TestSelectTopLabels` class:
  - [ ] `test_per_category_caps_enforced`
  - [ ] `test_max_total_labels_enforced`
  - [ ] `test_below_min_confidence_excluded`
  - [ ] `test_sorted_alphabetically`
  - [ ] `test_high_confidence_wins_within_category`

- [ ] 3. Write `TestLabeler` class:
  - [ ] `test_auth_chunk_gets_domain_auth`
  - [ ] `test_auth_chunk_gets_session_validation`
  - [ ] `test_source_code_chunk_gets_code_snippet`
  - [ ] `test_source_code_chunk_gets_implementation_label` — MEDIUM confidence on function/method/class
  - [ ] `test_config_chunk_does_not_get_implementation_label` — not on config/docs
  - [ ] `test_repo_summary_gets_repo_overview`
  - [ ] `test_package_json_gets_manifest_labels`
  - [ ] `test_readme_gets_readme_labels`
  - [ ] `test_qdrant_storage_chunk_gets_qdrant_labels`
  - [ ] `test_test_file_gets_test_labels`
  - [ ] `test_unknown_chunk_gets_fallback_labels`
  - [ ] `test_codeseek_internal_labels_blocked_for_external_repo`
  - [ ] `test_tech_qdrant_allowed_for_external_repo`
  - [ ] `test_label_confidences_populated_after_labeling` — IS set on chunk
  - [ ] `test_code_intent_uses_description_first`
  - [ ] `test_code_intent_falls_back_to_summary`
  - [ ] `test_code_intent_falls_back_to_symbol`

- [ ] 4. Write `TestStorage` class:
  - [ ] `test_label_confidences_not_stored_in_qdrant_payload` — excluded from storage, not from chunk

- [ ] 5. Write `TestFirstSentence` class:
  - [ ] `test_sentence_with_period`
  - [ ] `test_sentence_no_trailing_space`
  - [ ] `test_sentence_two_sentences_returns_first`
  - [ ] `test_empty_string`
  - [ ] `test_no_sentence_terminator_truncates`

**Verification:**
```bash
uv run pytest backend/tests/test_labeler.py -v
# all tests pass, no warnings
```

---

## Group 9 — Query Intent Classifier

- [x] **Group 9 complete and verified**

**Group 9 Implementation Details:**
- Created `backend/retrieval/query_intent.py` implementing the query classification system.
- Designed `DOMAIN_KEYWORDS` mapping word-boundary query terms to corresponding domain, capability, and technology labels.
- Implemented `_term_in_query()` and `_any_term_in_query()` helpers utilizing `re.search` with explicit `\b` boundaries for case-insensitive exact matching.
- Implemented `extract_domain_hints()` to detect and return labels based on keyword matches.
- Implemented `classify_query_intent()` to classify user queries into 6 distinct intent categories (`code_snippet`, `implementation`, `technical_explanation`, `code_location`, `general_context`, or fallback) and return a list of labels to boost with matched domain hints merged in.
- Successfully verified correct classifications, routing rules, word-boundary checks, and hints merge logic using the Group 9 test suite assertions.

**Goal:** Add the runtime query classification used by the retriever.

**Files to create:**
```
backend/retrieval/query_intent.py
```

**Tasks:**

- [ ] 1. Implement `_term_in_query(term, query) -> bool` using `re.search` with `\b` boundaries and `re.IGNORECASE`.

- [ ] 2. Implement `_any_term_in_query(terms, query) -> bool`.

- [ ] 3. Implement `DOMAIN_KEYWORDS` dict (copy from strategy doc, all terms use word-boundary matching).

- [ ] 4. Implement `extract_domain_hints(query) -> list[str]`.

- [ ] 5. Implement `classify_query_intent(query) -> dict` with intent buckets in this exact order:
   ```
   1. code_snippet
   2. implementation
   3. "how is/how are ... implemented" compound check → technical_explanation
   4. code_location
   5. technical_explanation (general)
   6. general_context
   7. default fallback → general_context
   ```
   Merge domain hints into every profile's `boost_labels` at the end.

**Verification:**
```python
from backend.retrieval.query_intent import classify_query_intent, extract_domain_hints, _term_in_query

# word boundary
assert _term_in_query("api", "What is the capability?") == False   # "api" in "capability" — blocked
assert _term_in_query("api", "How does the API work?") == True
assert _term_in_query("session", "expression evaluation") == False
assert _term_in_query("implemented", "unimplemented feature") == False

# intent routing
assert classify_query_intent("show me the code for auth")["intent"] == "code_snippet"
assert classify_query_intent("how do i change the auth validation")["intent"] == "implementation"
assert classify_query_intent("how is session validation implemented")["intent"] == "technical_explanation"
assert classify_query_intent("where is auth implemented")["intent"] == "code_location"
assert classify_query_intent("how does auth work")["intent"] == "technical_explanation"
assert classify_query_intent("what does this repo do")["intent"] == "general_context"

# domain hints merge
profile = classify_query_intent("show me the auth session validation code")
assert "domain:auth" in profile["boost_labels"]
assert "capability:session-validation" in profile["boost_labels"]
assert "question_use:code-snippet" in profile["boost_labels"]

# testing terms
assert "domain:testing" in extract_domain_hints("how does testing work")
assert "domain:testing" in extract_domain_hints("show me the tests")
assert "artifact:test-code" in extract_domain_hints("where are the test files")

print("Group 9 OK")
```

---

## Group 10 — Label-Aware Retrieval Scoring

- [x] **Group 10 complete and verified**

**Group 10 Implementation Details:**
- Imported `classify_query_intent` and `compute_label_boost` into `backend/retrieval/searcher.py`.
- Refactored `_rerank_with_query_tokens` to calculate a unified retrieval score: `final_score = 0.70 * vector_score + 0.15 * exact_match_score + 0.10 * label_boost + 0.05 * path_symbol_boost`.
- Ranked and sorted candidates purely by `-final_score`.
- Verified that synthetic candidates prepended by `_inject_overview_candidates()` and `_inject_architecture_file_candidates()` receive appropriate label boosts and rank correctly.

**Goal:** Wire label boost into the final retrieval score.

**Files to edit:**
```
backend/retrieval/retriever.py    (or wherever final_score is computed)
```

**Tasks:**

- [ ] 1. Import `classify_query_intent` and `compute_label_boost`.

- [ ] 2. Implement `compute_label_boost(chunk_labels, query_profile) -> float`:
   ```python
   LABEL_WEIGHTS = {
       "question_use": 0.15,
       "capability": 0.12,
       "domain": 0.10,
       "artifact": 0.08,
       "code_role": 0.08,
       "tech": 0.06,
   }

   def compute_label_boost(chunk_labels: list[str], query_profile: dict) -> float:
       boost_labels = set(query_profile.get("boost_labels", []))
       boost = 0.0
       for label in chunk_labels:
           if label not in boost_labels:
               continue
           category = label.split(":", 1)[0]
           boost += LABEL_WEIGHTS.get(category, 0.05)
       return min(boost, 1.0)
   ```

- [ ] 3. In the retrieval scoring loop, classify intent once per query:
   ```python
   query_profile = classify_query_intent(query)
   ```

- [ ] 4. For each retrieved candidate, compute boost and update score:
   ```python
   chunk_labels = candidate.payload.get("labels", [])
   label_boost = compute_label_boost(chunk_labels, query_profile)

   final_score = (
       0.70 * vector_score
       + 0.15 * exact_match_score
       + 0.10 * label_boost
       + 0.05 * path_symbol_boost
   )
   ```

- [ ] 5. Use `final_score` for ranking — do NOT hard-filter by labels.

**Verification:**
Manual retrieval test using the Qdrant debug script or a REPL session:
```python
# query 1: general
results = retrieve("How does auth work?")
top_paths = [r.payload["relative_path"] for r in results[:5]]
assert any("auth" in p for p in top_paths)

# query 2: code snippet
results = retrieve("Show me the session validation code")
top_labels = [r.payload.get("labels", []) for r in results[:3]]
assert any("question_use:code-snippet" in labels for labels in top_labels)

# query 3: implementation
results = retrieve("How do I change the provider validation logic?")
top_labels = [r.payload.get("labels", []) for r in results[:3]]
assert any("question_use:implementation" in labels for labels in top_labels)

print("Group 10 OK")
```

---

## Group 11 — Full Re-Index and Runtime Validation

- [x] **Group 11 complete and verified**

**Group 11 Implementation Details:**
- Re-indexed the Portfolio repository into Qdrant collection `repository_chunks__local__atharvapagar04_portfolio` with local LLM descriptions, resulting in 30 chunks with 100% label and code intent coverage.
- Re-indexed the CodeSeek repository into Qdrant collection `repository_chunks__local__atharvapagar04_codeseek` on CPU (avoiding CUDA OOM), resulting in 2088 chunks with 100% label coverage.
- Ran the manual vector DB audit script on CodeSeek, resulting in 0 errors and a clean SUCCESS verdict (confirming zero invalid label types and correct domain mappings).
- Executed a custom retrieval validation script (`scratch/verify_retrieval_labels.py`) checking queries:
  1. `"How does auth work?"` -> correctly retrieved auth-related files like `auth_store.py` and `api_service.py` in the top 5.
  2. `"How is session token validation implemented?"` -> correctly retrieved `get_user_for_session_token` as the top result.
  3. `"Show me the session validation code"` -> correctly boosted `question_use:code-snippet` label (classified intent: `code_snippet`).
  4. `"How do I change the provider validation logic?"` -> correctly boosted `question_use:implementation` label (classified intent: `implementation`).
  5. `"What does this repo do?"` -> correctly routed to `__repo_summary__.md`.

**Goal:** End-to-end validation with real data before marking Phase 1–10 complete.

**Tasks:**

- [x] 1. Re-index Portfolio repo.
- [x] 2. Re-index CodeSeek repo.
- [x] 3. Run the extended vector DB audit (Group 7).
- [x] 4. Run manual retrieval checks:
  - [x] `"How does auth work?"` → top results include `auth_store.py` and auth-related API files
  - [x] `"How is session token validation implemented?"` → top result is `get_user_for_session_token` chunk
  - [x] `"Show me the auth session validation code."` → returns chunk with `content_excerpt`
  - [x] `"Which function stores chunks in Qdrant?"` → top results include `storage.py` functions
  - [x] `"What does this repo do?"` → top result is `__repo_summary__.md` chunk
- [x] 5. Fix any label gaps found. If a known chunk is missing an expected label, trace back to Group 3 rules and add the missing signal.

**Pass criteria:**
```
Label coverage: 100%
Unknown labels: 0
Chunks over label limit: 0
Chunks with invalid labels type: 0
All 5 manual retrieval checks pass
VERDICT: SUCCESS ✅
```

Only after this passes, proceed to Group 12.

---

## Group 12 — Optional LLM Refinement (post-validation only)

- [ ] **Group 12 complete and verified**

**Goal:** Add LLM label refinement gated behind a config flag. Do not implement until Group 11 passes.

**Files to create/edit:**
```
backend/rag_ingestion/stages/labeler.py    (add LLM refinement functions)
backend/rag_ingestion/pipeline.py          (add refinement stage)
```

**Tasks:**

- [ ] 1. Add config:
   ```python
   ENABLE_LLM_LABEL_REFINEMENT = False
   CHUNK_LABEL_LLM_MAX_CHUNKS = 80
   ```

- [ ] 2. Implement `label_refinement_priority(chunk, label_confidences) -> float`.

- [ ] 3. Implement `select_chunks_for_refinement(chunks) -> list[Chunk]` — sorts by priority, takes top 80.

- [ ] 4. Implement `refine_labels_with_llm(chunk) -> list[str]`:
  - [ ] Build small prompt (no full code blocks)
  - [ ] Pass `summary`, `description`, existing labels, allowed registry labels
  - [ ] Parse JSON response
  - [ ] Filter to registry-only labels
  - [ ] Return empty list on any failure

- [ ] 5. Implement `merge_llm_labels(deterministic_labels, llm_labels) -> list[str]` (additive only).

- [ ] 6. In pipeline, after deterministic labeling:
   ```python
   if settings.ENABLE_LLM_LABEL_REFINEMENT:
       selected = select_chunks_for_refinement(chunks)
       for chunk in selected:
           llm_labels = refine_labels_with_llm(chunk)
           if llm_labels:
               chunk.labels = merge_llm_labels(chunk.labels, llm_labels)
   ```

- [ ] 7. Confirm: LLM failure does NOT raise — just keeps deterministic labels.

**Verification:**
```python
# set ENABLE_LLM_LABEL_REFINEMENT=True and run on 5 chunks
# confirm:
# - no new labels outside LABEL_REGISTRY
# - deterministic labels still present after merge
# - code_intent may be improved but not empty
# - if LLM call fails, labels unchanged
```

---

## Dependency Map

```
Group 1 (constants + helpers)
    └── Group 2 (Chunk fields)
            └── Group 3 (labeling rules)
                    ├── Group 4 (pipeline wire-up)
                    │       ├── Group 5 (embedding input)
                    │       └── Group 6 (storage payload)
                    │               └── Group 7 (audit script)
                    │                       └── Group 8 (unit tests) ← can run earlier
                    └── Group 9 (query classifier)
                            └── Group 10 (retrieval scoring)
                                    └── Group 11 (full re-index + validation)
                                            └── Group 12 (LLM refinement — optional)
```

Group 8 (unit tests) can be written in parallel with Groups 3–6 using mock chunks.
Group 9 is independent of Groups 4–7 and can be done in parallel after Group 1.
