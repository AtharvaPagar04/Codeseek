import json
import subprocess
import sys
from pathlib import Path
import pytest

@pytest.fixture
def tmp_reports_dir(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    return d

def run_policy_summary(args_list):
    script_path = str(Path(__file__).resolve().parent.parent / "evals" / "eval_policy_summary.py")
    cmd = [sys.executable, script_path] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result

def test_pass_clean_reports(tmp_reports_dir):
    # 1. PASS when all reports pass and only diagnostics exist.
    ret_path = tmp_reports_dir / "retrieval.json"
    conv_path = tmp_reports_dir / "conversation.json"
    cal_path = tmp_reports_dir / "calibration.json"
    ragas_path = tmp_reports_dir / "ragas.json"
    comp_path = tmp_reports_dir / "compare.json"
    out_json = tmp_reports_dir / "output.json"
    out_md = tmp_reports_dir / "output.md"

    # Write clean reports
    ret_path.write_text(json.dumps({
        "status": "PASS",
        "summary": {
            "exact_hit_regression_count": 0,
            "protected_hits_total": 5,
            "protected_exact_hit_preserved@5": 100.0,
            "empty_result_rate": 0.0
        }
    }))
    conv_path.write_text(json.dumps({
        "status": "PASS"
    }))
    cal_path.write_text(json.dumps({
        "queries": [
            {
                "id": "q1",
                "expected_files": ["foo.py"],
                "expected_context_file_hit": True,
                "expected_context_file_rank": 1,
                "answer_mentions_expected_terms": {"term1": True},
                "metric_scores": {
                    "answer_relevancy": 0.8,
                    "context_precision": 0.0,
                    "faithfulness": None
                }
            }
        ],
        "score_summary": {
            "answer_relevancy_avg": 0.8
        }
    }))
    ragas_path.write_text(json.dumps({
        "summary": {
            "answer_relevancy": 0.8
        },
        "traces": [
            {
                "trace_id": "tr1",
                "scores": {
                    "answer_relevancy": 0.8,
                    "context_precision": 0.0,
                    "faithfulness": None
                }
            }
        ]
    }))
    comp_path.write_text(json.dumps({
        "results": [
            {
                "evaluator_id": "eval_3b",
                "model": "qwen2.5-coder:3b",
                "status": "PASS",
                "score_health": {
                    "null_score_count": 0
                }
            },
            {
                "evaluator_id": "eval_7b",
                "model": "qwen-coder-7b-16k",
                "status": "PASS",
                "score_health": {
                    "null_score_count": 0
                }
            }
        ],
        "summary": {
            "context_precision_values": {
                "eval_3b": 0.0,
                "eval_7b": 0.0
            }
        }
    }))

    args = [
        "--retrieval-report", str(ret_path),
        "--conversation-report", str(conv_path),
        "--judge-calibration-report", str(cal_path),
        "--ragas-report", str(ragas_path),
        "--evaluator-compare-report", str(comp_path),
        "--output-json", str(out_json),
        "--output-md", str(out_md)
    ]

    res = run_policy_summary(args)
    assert res.returncode == 0

    # Read output
    with open(out_json, "r") as f:
        data = json.load(f)

    assert data["status"] == "PASS"
    assert data["hard_gate_status"] == "PASS"
    assert len(data["warnings"]) == 0
    assert len(data["hard_gate_failures"]) == 0
    assert len(data["diagnostics"]) > 0
    
    # Ensure diagnostics contain context_precision and faithfulness details
    assert any("context_precision" in d for d in data["diagnostics"])
    assert any("faithfulness" in d for d in data["diagnostics"])
    assert any("disagreement" in d for d in data["diagnostics"])

def test_error_retrieval_fail(tmp_reports_dir):
    # 2. ERROR when retrieval status is FAIL.
    ret_path = tmp_reports_dir / "retrieval.json"
    out_json = tmp_reports_dir / "output.json"

    ret_path.write_text(json.dumps({
        "status": "FAIL",
        "summary": {}
    }))

    res = run_policy_summary([
        "--retrieval-report", str(ret_path),
        "--output-json", str(out_json)
    ])
    assert res.returncode == 1

    with open(out_json, "r") as f:
        data = json.load(f)

    assert data["status"] == "ERROR"
    assert data["hard_gate_status"] == "ERROR"
    assert "retrieval eval report status is FAIL or ERROR" in data["hard_gate_failures"]

def test_error_conversation_error(tmp_reports_dir):
    # 3. ERROR when conversation status is ERROR.
    conv_path = tmp_reports_dir / "conversation.json"
    out_json = tmp_reports_dir / "output.json"

    conv_path.write_text(json.dumps({
        "status": "ERROR"
    }))

    res = run_policy_summary([
        "--conversation-report", str(conv_path),
        "--output-json", str(out_json)
    ])
    assert res.returncode == 1

    with open(out_json, "r") as f:
        data = json.load(f)

    assert data["status"] == "ERROR"
    assert data["hard_gate_status"] == "ERROR"
    assert "conversation eval report status is FAIL or ERROR" in data["hard_gate_failures"]

def test_error_judge_calibration_file_miss(tmp_reports_dir):
    # 4. ERROR when judge calibration has a query with expected_files and expected_context_file_hit false.
    cal_path = tmp_reports_dir / "calibration.json"
    out_json = tmp_reports_dir / "output.json"

    cal_path.write_text(json.dumps({
        "queries": [
            {
                "id": "q_test",
                "expected_files": ["missing.py"],
                "expected_context_file_hit": False
            }
        ]
    }))

    res = run_policy_summary([
        "--judge-calibration-report", str(cal_path),
        "--output-json", str(out_json)
    ])
    assert res.returncode == 1

    with open(out_json, "r") as f:
        data = json.load(f)

    assert data["status"] == "ERROR"
    assert "expected context file missing for query q_test" in data["hard_gate_failures"]

def test_warn_answer_relevancy_low(tmp_reports_dir):
    # 5. WARN when answer_relevancy is below threshold.
    ragas_path = tmp_reports_dir / "ragas.json"
    out_json = tmp_reports_dir / "output.json"

    ragas_path.write_text(json.dumps({
        "summary": {
            "answer_relevancy": 0.5  # Below default 0.6
        }
    }))

    res = run_policy_summary([
        "--ragas-report", str(ragas_path),
        "--output-json", str(out_json)
    ])
    assert res.returncode == 0

    with open(out_json, "r") as f:
        data = json.load(f)

    assert data["status"] == "WARN"
    assert data["hard_gate_status"] == "PASS"
    assert any("Average answer_relevancy" in w for w in data["warnings"])

def test_diagnostic_only_context_precision_zero(tmp_reports_dir):
    # 6. Diagnostic-only context_precision=0.0 does not cause WARN/ERROR.
    ragas_path = tmp_reports_dir / "ragas.json"
    out_json = tmp_reports_dir / "output.json"

    ragas_path.write_text(json.dumps({
        "summary": {
            "answer_relevancy": 0.8
        },
        "traces": [
            {
                "trace_id": "tr1",
                "scores": {
                    "answer_relevancy": 0.8,
                    "context_precision": 0.0,
                    "faithfulness": 0.9
                }
            }
        ]
    }))

    res = run_policy_summary([
        "--ragas-report", str(ragas_path),
        "--output-json", str(out_json)
    ])
    assert res.returncode == 0

    with open(out_json, "r") as f:
        data = json.load(f)

    assert data["status"] == "PASS"
    assert data["hard_gate_status"] == "PASS"
    assert len(data["warnings"]) == 0
    assert any("context_precision is 0.0" in d for d in data["diagnostics"])

def test_diagnostic_only_compare_precision_zero(tmp_reports_dir):
    # 7. Evaluator comparison with context_precision=0.0 across evaluators creates diagnostic only.
    comp_path = tmp_reports_dir / "compare.json"
    out_json = tmp_reports_dir / "output.json"

    comp_path.write_text(json.dumps({
        "results": [
            {
                "evaluator_id": "eval_1",
                "status": "PASS"
            }
        ],
        "summary": {
            "context_precision_values": {
                "eval_1": 0.0
            }
        }
    }))

    res = run_policy_summary([
        "--evaluator-compare-report", str(comp_path),
        "--output-json", str(out_json)
    ])
    assert res.returncode == 0

    with open(out_json, "r") as f:
        data = json.load(f)

    assert data["status"] == "PASS"
    assert len(data["warnings"]) == 0
    assert any("context_precision remains 0.0 across all evaluators" in d for d in data["diagnostics"])

def test_json_and_markdown_written(tmp_reports_dir):
    # 8. JSON and Markdown outputs are written.
    ret_path = tmp_reports_dir / "retrieval.json"
    out_json = tmp_reports_dir / "output.json"
    out_md = tmp_reports_dir / "output.md"

    ret_path.write_text(json.dumps({
        "status": "PASS"
    }))

    res = run_policy_summary([
        "--retrieval-report", str(ret_path),
        "--output-json", str(out_json),
        "--output-md", str(out_md)
    ])
    assert res.returncode == 0

    assert out_json.exists()
    assert out_md.exists()
    
    # Check Markdown content contains Policy Notes
    md_content = out_md.read_text()
    assert "# CodeSeek Evaluation Policy and Gating Report" in md_content
    assert "Policy Notes" in md_content

def test_missing_optional_reports_gracefully(tmp_reports_dir):
    # 9. Missing optional reports are handled gracefully.
    out_json = tmp_reports_dir / "output.json"

    res = run_policy_summary([
        "--retrieval-report", "nonexistent_retrieval.json",
        "--conversation-report", "nonexistent_conversation.json",
        "--output-json", str(out_json)
    ])
    assert res.returncode == 0

    with open(out_json, "r") as f:
        data = json.load(f)

    assert data["status"] == "PASS"
    assert data["reports_loaded"]["retrieval_report"] is False
    assert data["reports_loaded"]["conversation_report"] is False
