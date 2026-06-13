from retrieval.api_service import _build_query_diagnostics


def test_build_query_diagnostics_compacts_safe_fields():
    meta = {
        "query_intent": "CODE_REQUEST",
        "primary_intent": "CODE_REQUEST",
        "response_mode": "code_snippet",
        "memory_diagnostics": {
            "memory": {
                "is_followup": False,
                "topic_shift_detected": True,
                "followup_confidence": 0.125,
                "query_similarity": 0.0,
                "keyword_overlap": 0.0,
                "similarity_method": "keyword_overlap",
                "has_valid_referent": False,
                "history_injected": False,
                "history_turns_used": 0,
            },
            "rewrite": {
                "query_rewritten": False,
                "rewrite_anchor": None,
                "rewrite_mode": "none",
            },
            "retrieval": {
                "previous_candidates_injected": 0,
                "strong_new_entities": ["backend/evals/run_safe_evals.py", "main"],
                "exact_hit": True,
                "multi_layer_hit": True,
                "top_score": 0.97,
                "candidate_count": 4,
                "retrieval_confidence": "high",
            },
        },
        "llm_selection": {
            "provider": "local",
            "model": "qwen2.5-coder:3b-8k",
            "routing_mode": "local",
        },
        "evidence_confidence": {"level": "strong", "reason": "matched route", "count": 2},
        "source_filter": {"selected_primary": 1, "selected_expanded": 0, "display_count": 1, "reasoning_count": 2},
        "display_sources": [
            {
                "relative_path": "backend/evals/run_safe_evals.py",
                "symbol_name": "main",
                "start_line": 10,
                "end_line": 48,
                "api_key": "secret",
            }
        ],
        "reasoning_sources": [
            {
                "relative_path": "backend/evals/run_safe_evals.py",
                "symbol_name": "get_tail",
                "start_line": 50,
                "end_line": 66,
                "raw_prompt": "hidden",
            }
        ],
        "validation": {"valid": False, "reasons": ["rebuilt_code_snippet"], "repaired_answer": "kept"},
    }

    diagnostics = _build_query_diagnostics(
        meta=meta,
        sources=[
            {
                "relative_path": "backend/evals/run_safe_evals.py",
                "symbol_name": "main",
                "start_line": 10,
                "end_line": 48,
                "payload": "do-not-expose",
            }
        ],
        token_count=512,
        session={"status": "ready", "error": ""},
        provider_config={"provider": "local", "model": "qwen2.5-coder:3b-8k"},
    )

    assert diagnostics["intent"] == "CODE_REQUEST"
    assert diagnostics["primary_intent"] == "CODE_REQUEST"
    assert diagnostics["response_mode"] == "code_snippet"
    assert diagnostics["provider"] == "local"
    assert diagnostics["model"] == "qwen2.5-coder:3b-8k"
    assert diagnostics["context_tokens"] == 512
    assert diagnostics["session_status"] == "ready"
    assert diagnostics["selected_source_count"] == 1
    assert diagnostics["reasoning_source_count"] == 1
    assert diagnostics["rendered_source_count"] == 1
    assert diagnostics["memory"]["topic_shift_detected"] is True
    assert diagnostics["memory"]["similarity_method"] == "keyword_overlap"
    assert diagnostics["rewrite"]["rewrite_mode"] == "none"
    assert diagnostics["retrieval"]["top_score"] == 0.97
    assert diagnostics["rendered_sources"][0] == {
        "relative_path": "backend/evals/run_safe_evals.py",
        "symbol_name": "main",
        "start_line": 10,
        "end_line": 48,
    }
    assert diagnostics["selected_sources"][0]["relative_path"] == "backend/evals/run_safe_evals.py"
    assert "api_key" not in diagnostics["selected_sources"][0]
    assert diagnostics["reasoning_sources"][0]["symbol_name"] == "get_tail"
    assert diagnostics["validation"]["repaired"] is True
    assert diagnostics["validation"]["reasons"] == ["rebuilt_code_snippet"]
