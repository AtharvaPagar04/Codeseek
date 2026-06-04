# Retrieval Docs

Current response-quality state:

- scored intent/entity extraction is implemented and enabled by default
- exact entity promotion is implemented for env/config/dependency/API lookup
- lexical retrieval exists behind `RETRIEVAL_ENABLE_LEXICAL`, but remains disabled by default
- structured non-code metadata extraction is implemented for the first supported file set
- synthetic repo-summary generation and overview answer preference are implemented
- repo-summary artifact re-ingestion/eval passed on the backend collection
- multi-repo fixture eval coverage and thresholds are implemented for frontend, backend, infra, and monorepo shapes
- deterministic answer coverage phase 1 is implemented for backend orchestration, auth/session lifecycle, and indexing/session creation flow questions
- phase-1 flow eval coverage and deterministic latency measurement are implemented
- phase-1 flow source gating and answer-term coverage are complete against the current eval gate
- phase-1 flow API source cards now use the deterministic evidence set selected for the answer body
- next future work is phase 2 deterministic coverage only when explicitly resumed

- [Current Retrieval Strategy](./current_retrieval_strategy.md)
- [Response Quality Refinement Plan](./response_quality_refinement_plan.md)
- [Lexical Retrieval Baseline Results](./eval_results_lexical_baseline.md)
- [Latest Multi-Repo Eval Results](./eval_results_multi_repo_latest.json)
- [CodeSeek Flow Phase 1 Eval](./eval_codeseek_flow_phase1.json)
- [Multi-Repo Eval Suite](./eval_suite_multi_repo.json)
- [Multi-Repo Eval Thresholds](./eval_thresholds_multi_repo.json)
- [Retrieval Pipeline Docs](./retrieval_pipeline_docs.md)
- [Retrieval Pipeline Architecture](./retrieval_pipeline_architecture.md)
- [Architecture](./architecture.md)
