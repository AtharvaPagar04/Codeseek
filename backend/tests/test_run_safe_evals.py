import os
import sys
import json
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure backend directory is in path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from evals.run_safe_evals import main as run_safe_evals_main

@pytest.fixture
def temp_output_dir(tmp_path):
    d = tmp_path / "safe_eval_out"
    d.mkdir()
    return d

def test_command_building_and_safe_workflow(temp_output_dir):
    argv = [
        "run_safe_evals.py",
        "--session-id", "test-session-123",
        "--expected-repo-root", "/home/arch/DEV/CodeSeek",
        "--expected-collection", "repository_chunks__local__codeseek",
        "--output-dir", str(temp_output_dir),
        "--verbose"
    ]

    captured_cmds = []

    def mock_run(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        
        # Write mock files as side effects of running
        if any("retrieval_eval.py" in arg for arg in cmd):
            ret_path = Path(cmd[cmd.index("--output") + 1])
            ret_path.write_text(json.dumps({"status": "PASS", "summary": {}}))
        elif any("conversation_eval.py" in arg for arg in cmd):
            conv_path = Path(cmd[cmd.index("--output") + 1])
            conv_path.write_text(json.dumps({"status": "PASS"}))
        elif any("eval_policy_summary.py" in arg for arg in cmd):
            out_json_path = Path(cmd[cmd.index("--output-json") + 1])
            out_json_path.write_text(json.dumps({
                "status": "PASS",
                "hard_gate_status": "PASS",
                "hard_gate_failures": [],
                "warnings": [],
                "diagnostics": [],
                "recommendation": "Passed policy tests."
            }))
            out_md_path = Path(cmd[cmd.index("--output-md") + 1])
            out_md_path.write_text("# Policy Report MD")

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Mock Success Out"
        mock_process.stderr = ""
        return mock_process

    with patch("sys.argv", argv), patch("subprocess.run", side_effect=mock_run):
        run_safe_evals_main()

    # 1. Builds retrieval command correctly
    ret_cmd = next((c for c in captured_cmds if any("retrieval_eval.py" in arg for arg in c)), None)
    assert ret_cmd is not None
    assert "--session-id" in ret_cmd
    assert "test-session-123" in ret_cmd
    assert "--golden" in ret_cmd

    # 2. Builds conversation command correctly
    conv_cmd = next((c for c in captured_cmds if any("conversation_eval.py" in arg for arg in c)), None)
    assert conv_cmd is not None
    assert "--session-id" in conv_cmd
    assert "test-session-123" in conv_cmd
    assert "--trees" in conv_cmd

    # 3. Runs required steps and writes summary JSON/Markdown
    summary_json = temp_output_dir / "safe_eval_summary.json"
    summary_md = temp_output_dir / "safe_eval_summary.md"
    assert summary_json.exists()
    assert summary_md.exists()

    with open(summary_json, "r") as f:
        data = json.load(f)
    
    # 4. PASS when all subprocesses return 0 and policy summary is PASS
    assert data["status"] == "PASS"
    assert data["hard_gate_status"] == "PASS"
    
    # 8. Optional RAGAS steps are skipped by default
    assert not any("ragas_calibration.py" in c[1] for c in captured_cmds)
    assert not any("ragas_judge_calibration.py" in c[1] for c in captured_cmds)
    assert not any("ragas_evaluator_compare.py" in c[1] for c in captured_cmds)


def test_include_optional_ragas_and_compare(temp_output_dir):
    argv = [
        "run_safe_evals.py",
        "--session-id", "test-session-ragas",
        "--expected-repo-root", "/home/arch/DEV/CodeSeek",
        "--expected-collection", "repository_chunks__local__codeseek",
        "--output-dir", str(temp_output_dir),
        "--include-ragas",
        "--include-evaluator-compare"
    ]

    captured_cmds = []

    def mock_run(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        
        # Write mock files as side effects of running
        if any("retrieval_eval.py" in arg for arg in cmd):
            ret_path = Path(cmd[cmd.index("--output") + 1])
            ret_path.write_text(json.dumps({"status": "PASS", "summary": {}}))
        elif any("conversation_eval.py" in arg for arg in cmd):
            conv_path = Path(cmd[cmd.index("--output") + 1])
            conv_path.write_text(json.dumps({"status": "PASS"}))
        elif any("ragas_calibration.py" in arg for arg in cmd):
            summary_path = Path(cmd[cmd.index("--summary-output") + 1])
            summary_path.write_text(json.dumps({"status": "PASS", "summary": {}}))
            latest_path = Path(cmd[cmd.index("--ragas-output") + 1])
            latest_path.write_text(json.dumps({}))
            traces_path = Path(cmd[cmd.index("--trace-output") + 1])
            traces_path.write_text("{}")
        elif any("ragas_judge_calibration.py" in arg for arg in cmd):
            json_path = Path(cmd[cmd.index("--output-json") + 1])
            json_path.write_text(json.dumps({}))
            md_path = Path(cmd[cmd.index("--output-md") + 1])
            md_path.write_text("# Judge Report")
        elif any("ragas_evaluator_compare.py" in arg for arg in cmd):
            json_path = Path(cmd[cmd.index("--output-json") + 1])
            json_path.write_text(json.dumps({}))
        elif any("eval_policy_summary.py" in arg for arg in cmd):
            out_json_path = Path(cmd[cmd.index("--output-json") + 1])
            out_json_path.write_text(json.dumps({
                "status": "PASS",
                "hard_gate_status": "PASS",
                "hard_gate_failures": [],
                "warnings": [],
                "diagnostics": [],
                "recommendation": "Passed RAGAS policy."
            }))

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Mock Success Out"
        mock_process.stderr = ""
        return mock_process

    with patch("sys.argv", argv), patch("subprocess.run", side_effect=mock_run):
        run_safe_evals_main()

    # Verify optional steps were run
    assert any("ragas_calibration.py" in c[1] for c in captured_cmds)
    assert any("ragas_judge_calibration.py" in c[1] for c in captured_cmds)
    assert any("ragas_evaluator_compare.py" in c[1] for c in captured_cmds)


def test_status_warn_handling(temp_output_dir):
    argv = [
        "run_safe_evals.py",
        "--session-id", "test-session-warn",
        "--expected-repo-root", "/home/arch/DEV/CodeSeek",
        "--expected-collection", "coll",
        "--output-dir", str(temp_output_dir)
    ]

    def mock_run(cmd, *args, **kwargs):
        if any("retrieval_eval.py" in arg for arg in cmd):
            ret_path = Path(cmd[cmd.index("--output") + 1])
            ret_path.write_text(json.dumps({"status": "PASS", "summary": {}}))
        elif any("conversation_eval.py" in arg for arg in cmd):
            conv_path = Path(cmd[cmd.index("--output") + 1])
            conv_path.write_text(json.dumps({"status": "PASS"}))
        elif any("eval_policy_summary.py" in arg for arg in cmd):
            out_json_path = Path(cmd[cmd.index("--output-json") + 1])
            out_json_path.write_text(json.dumps({
                "status": "WARN",
                "hard_gate_status": "PASS",
                "hard_gate_failures": [],
                "warnings": ["Warning details"],
                "diagnostics": [],
                "recommendation": "Warnings present."
            }))

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Success"
        mock_process.stderr = ""
        return mock_process

    with patch("sys.argv", argv), patch("subprocess.run", side_effect=mock_run):
        run_safe_evals_main()

    summary_json = temp_output_dir / "safe_eval_summary.json"
    with open(summary_json, "r") as f:
        data = json.load(f)
    assert data["status"] == "WARN"


def test_required_step_failure(temp_output_dir):
    argv = [
        "run_safe_evals.py",
        "--session-id", "test-session-fail",
        "--expected-repo-root", "/home/arch/DEV/CodeSeek",
        "--expected-collection", "coll",
        "--output-dir", str(temp_output_dir)
    ]

    def mock_run(cmd, *args, **kwargs):
        mock_process = MagicMock()
        if any("retrieval_eval.py" in arg for arg in cmd):
            mock_process.returncode = 1
            mock_process.stdout = ""
            mock_process.stderr = "Retrieval failed error details."
        else:
            mock_process.returncode = 0
            mock_process.stdout = "Success"
            mock_process.stderr = ""
        return mock_process

    with patch("sys.argv", argv), patch("subprocess.run", side_effect=mock_run):
        run_safe_evals_main()

    summary_json = temp_output_dir / "safe_eval_summary.json"
    with open(summary_json, "r") as f:
        data = json.load(f)
    assert data["status"] == "ERROR"
    # Dependent step eval_policy_summary should have failed/skipped
    policy_step = next(s for s in data["steps"] if s["name"] == "eval_policy_summary")
    assert policy_step["return_code"] == -1


def test_policy_summary_status_error(temp_output_dir):
    argv = [
        "run_safe_evals.py",
        "--session-id", "test-session-policy-error",
        "--expected-repo-root", "/home/arch/DEV/CodeSeek",
        "--expected-collection", "coll",
        "--output-dir", str(temp_output_dir)
    ]

    def mock_run(cmd, *args, **kwargs):
        if any("retrieval_eval.py" in arg for arg in cmd):
            ret_path = Path(cmd[cmd.index("--output") + 1])
            ret_path.write_text(json.dumps({"status": "PASS", "summary": {}}))
        elif any("conversation_eval.py" in arg for arg in cmd):
            conv_path = Path(cmd[cmd.index("--output") + 1])
            conv_path.write_text(json.dumps({"status": "PASS"}))
        elif any("eval_policy_summary.py" in arg for arg in cmd):
            out_json_path = Path(cmd[cmd.index("--output-json") + 1])
            out_json_path.write_text(json.dumps({
                "status": "ERROR",
                "hard_gate_status": "ERROR",
                "hard_gate_failures": ["Hard gate broken!"],
                "warnings": [],
                "diagnostics": [],
                "recommendation": "Error recommendation."
            }))

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Success"
        mock_process.stderr = ""
        return mock_process

    with patch("sys.argv", argv), patch("subprocess.run", side_effect=mock_run):
        run_safe_evals_main()

    summary_json = temp_output_dir / "safe_eval_summary.json"
    with open(summary_json, "r") as f:
        data = json.load(f)
    assert data["status"] == "ERROR"
    assert data["hard_gate_status"] == "ERROR"


def test_timeout_marks_step_error(temp_output_dir):
    argv = [
        "run_safe_evals.py",
        "--session-id", "test-session-timeout",
        "--expected-repo-root", "/home/arch/DEV/CodeSeek",
        "--expected-collection", "coll",
        "--output-dir", str(temp_output_dir)
    ]

    def mock_run(cmd, *args, **kwargs):
        if any("retrieval_eval.py" in arg for arg in cmd):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1800, output="Partial std", stderr="Timeout expired")
        
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Success"
        mock_process.stderr = ""
        return mock_process

    with patch("sys.argv", argv), patch("subprocess.run", side_effect=mock_run):
        run_safe_evals_main()

    summary_json = temp_output_dir / "safe_eval_summary.json"
    with open(summary_json, "r") as f:
        data = json.load(f)
    assert data["status"] == "ERROR"
    ret_step = next(s for s in data["steps"] if s["name"] == "retrieval_eval")
    assert ret_step["status"] == "ERROR"
    assert ret_step["return_code"] == -2


def test_missing_optional_reports_do_not_fail_runner(temp_output_dir):
    argv = [
        "run_safe_evals.py",
        "--session-id", "test-session-missing-opts",
        "--expected-repo-root", "/home/arch/DEV/CodeSeek",
        "--expected-collection", "coll",
        "--output-dir", str(temp_output_dir)
    ]

    def mock_run(cmd, *args, **kwargs):
        if any("retrieval_eval.py" in arg for arg in cmd):
            ret_path = Path(cmd[cmd.index("--output") + 1])
            ret_path.write_text(json.dumps({"status": "PASS", "summary": {}}))
        elif any("conversation_eval.py" in arg for arg in cmd):
            conv_path = Path(cmd[cmd.index("--output") + 1])
            conv_path.write_text(json.dumps({"status": "PASS"}))
        elif any("eval_policy_summary.py" in arg for arg in cmd):
            out_json_path = Path(cmd[cmd.index("--output-json") + 1])
            out_json_path.write_text(json.dumps({
                "status": "PASS",
                "hard_gate_status": "PASS",
                "hard_gate_failures": [],
                "warnings": [],
                "diagnostics": [],
                "recommendation": "Passed policy tests."
            }))

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "Success"
        mock_process.stderr = ""
        return mock_process

    with patch("sys.argv", argv), patch("subprocess.run", side_effect=mock_run):
        run_safe_evals_main()

    summary_json = temp_output_dir / "safe_eval_summary.json"
    with open(summary_json, "r") as f:
        data = json.load(f)
    assert data["status"] == "PASS"
