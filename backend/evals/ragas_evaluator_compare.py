#!/usr/bin/env python3
"""RAGAS Evaluator Comparison tool."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ensure backend directory is in path
sys.path.append(str(Path(__file__).resolve().parent.parent))

def parse_evaluator_config(config_str: str) -> tuple[str, str, str]:
    parts = config_str.split(":")
    if len(parts) < 3:
        raise ValueError(
            f"Invalid evaluator configuration format: '{config_str}'. "
            "Expected format: 'provider:model:embedding_model'"
        )
    provider = parts[0]
    embedding_model = parts[-1]
    model = ":".join(parts[1:-1])
    if provider not in ("openai", "ollama"):
        raise ValueError(
            f"Unsupported provider: '{provider}' in evaluator configuration '{config_str}'"
        )
    return provider, model, embedding_model

def get_tail(text: str, max_lines: int = 20) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

def main():
    parser = argparse.ArgumentParser(description="Compare RAGAS results across multiple evaluator configurations.")
    parser.add_argument(
        "--input-traces",
        type=str,
        required=True,
        help="Path to the frozen input traces JSONL file.",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        required=True,
        help="Path to write the comparison JSON report.",
    )
    parser.add_argument(
        "--output-md",
        type=str,
        required=True,
        help="Path to write the comparison Markdown report.",
    )
    parser.add_argument(
        "--metrics",
        type=str,
        default=None,
        help="Comma-separated list of metrics to evaluate.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of traces to evaluate.",
    )
    parser.add_argument(
        "--evaluator",
        type=str,
        action="append",
        required=True,
        help="Evaluator configuration in the format 'provider:model:embedding_model'. Can be repeated.",
    )
    parser.add_argument(
        "--subprocess-timeout",
        type=int,
        default=3600,
        help="Subprocess timeout in seconds per evaluator run.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose progress and command construction output.",
    )

    args = parser.parse_args()

    input_traces_path = Path(args.input_traces)
    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)

    # Validate input traces exists
    if not input_traces_path.exists():
        print(f"Error: Input traces file not found at '{input_traces_path}'", file=sys.stderr)
        sys.exit(1)

    # Parse and validate all evaluator configs
    evaluator_configs = []
    for eval_str in args.evaluator:
        try:
            provider, model, embedding_model = parse_evaluator_config(eval_str)
            evaluator_configs.append({
                "raw": eval_str,
                "provider": provider,
                "model": model,
                "embedding_model": embedding_model
            })
        except ValueError as e:
            print(f"Error parsing evaluator configuration '{eval_str}': {e}", file=sys.stderr)
            sys.exit(1)

    # Parse requested metrics
    metrics_requested = []
    if args.metrics:
        metrics_requested = [m.strip() for m in args.metrics.split(",") if m.strip()]
    else:
        metrics_requested = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]

    results = []
    evaluator_statuses = []
    faithfulness_null_counts = {}

    # Create temporary directory inside the backend workspace to store reports
    backend_dir = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(dir=str(backend_dir)) as tmp_dir:
        tmp_dir_path = Path(tmp_dir)

        for idx, eval_cfg in enumerate(evaluator_configs):
            raw_cfg = eval_cfg["raw"]
            provider = eval_cfg["provider"]
            model = eval_cfg["model"]
            embedding_model = eval_cfg["embedding_model"]
            evaluator_id = raw_cfg.replace(":", "_").replace("-", "_").replace(".", "_")

            temp_report_path = tmp_dir_path / f"ragas_report_{evaluator_id}.json"

            print(f"\n[{idx + 1}/{len(evaluator_configs)}] Running evaluator: {raw_cfg} (ID: {evaluator_id})")
            
            # Construct subprocess command to invoke evals/ragas_eval.py
            cmd = [
                sys.executable,
                "evals/ragas_eval.py",
                "--input", str(input_traces_path),
                "--output", str(temp_report_path),
                "--evaluator-provider", provider,
                "--evaluator-model", model,
                "--embedding-model", embedding_model,
                "--ragas-timeout", "600",
                "--ragas-max-workers", "1",
                "--ragas-max-retries", "1",
                "--allow-no-ground-truth"
            ]
            if args.metrics:
                cmd.extend(["--metrics", args.metrics])
            if args.limit is not None:
                cmd.extend(["--limit", str(args.limit)])

            if args.verbose:
                print(f"Command: {' '.join(cmd)}")

            # Run as subprocess with heartbeat/progress tracking
            start_time = time.perf_counter()
            timed_out = False
            stdout_str = ""
            stderr_str = ""
            return_code = None

            try:
                env = os.environ.copy()
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env
                )

                poll_interval = 5.0
                while True:
                    ret = process.poll()
                    if ret is not None:
                        return_code = ret
                        break
                    
                    elapsed = time.perf_counter() - start_time
                    if elapsed >= args.subprocess_timeout:
                        timed_out = True
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        break
                    
                    print(f"  ... {evaluator_id} still running: {elapsed:.0f}s elapsed", flush=True)
                    time.sleep(poll_interval)

                try:
                    stdout_str, stderr_str = process.communicate(timeout=5)
                except Exception as ce:
                    stderr_str += f"\nError communicating with subprocess: {ce}"

            except Exception as e:
                return_code = -1
                stdout_str = ""
                stderr_str = f"Subprocess invocation failed: {e}"

            duration = time.perf_counter() - start_time

            print(f"Completed in {duration:.2f} seconds. Exit code: {return_code}")

            # Initialize result details
            status = "ERROR"
            score_health = {
                "numeric_score_count": 0,
                "null_score_count": 0,
                "metrics_with_numeric_scores": [],
                "metrics_with_null_scores": []
            }
            metrics_run = []
            metrics_skipped = {}
            errors = []
            metric_averages = {}
            ragas_runtime = {}

            # Read report if created and exited successfully
            report_read_ok = False
            if return_code == 0 and temp_report_path.exists():
                try:
                    with temp_report_path.open("r", encoding="utf-8") as rf:
                        report_data = json.load(rf)
                    
                    status = report_data.get("status", "ERROR")
                    score_health = report_data.get("score_health", score_health)
                    metrics_run = report_data.get("metrics_run", [])
                    metrics_skipped = report_data.get("metrics_skipped", {})
                    errors = report_data.get("errors", [])
                    ragas_runtime = report_data.get("ragas_runtime", {})
                    
                    # Extract averages
                    if "summary" in report_data:
                        for k, v in report_data["summary"].items():
                            if k != "score_health":
                                metric_averages[k] = v

                    # Calculate faithfulness nulls count
                    f_null = 0
                    if "traces" in report_data:
                        for tr in report_data["traces"]:
                            score = tr.get("scores", {}).get("faithfulness")
                            if "faithfulness" in metrics_requested and not _is_number(score):
                                f_null += 1
                    faithfulness_null_counts[evaluator_id] = f_null

                    report_read_ok = True
                except Exception as e:
                    errors.append({
                        "type": "REPORT_READ_FAILED",
                        "message": f"Failed to parse generated report JSON: {e}"
                    })

            if not report_read_ok:
                status = "ERROR"
                if timed_out:
                    errors.append({
                        "type": "SUBPROCESS_TIMEOUT",
                        "message": f"Subprocess timed out after {args.subprocess_timeout} seconds.",
                        "timeout_seconds": args.subprocess_timeout
                    })
                else:
                    err_msg = get_tail(stderr_str) or f"Subprocess exited with code {return_code} without generating report."
                    errors.append({
                        "type": "SUBPROCESS_ERROR",
                        "message": err_msg
                    })
                # If faithfulness was requested, count all as null since the run failed
                try:
                    import evals.ragas_eval
                    valid_traces, _, _ = evals.ragas_eval.load_answer_traces(input_traces_path, limit=args.limit)
                    total_traces = len(valid_traces)
                except Exception:
                    total_traces = args.limit or 1
                faithfulness_null_counts[evaluator_id] = total_traces

            evaluator_statuses.append(status)

            results.append({
                "evaluator_id": evaluator_id,
                "provider": provider,
                "model": model,
                "embedding_model": embedding_model,
                "command": cmd,
                "return_code": return_code,
                "duration_seconds": round(duration, 2),
                "report_path": str(temp_report_path) if report_read_ok else "",
                "status": status,
                "score_health": score_health,
                "metrics_run": metrics_run,
                "metrics_skipped": metrics_skipped,
                "errors": errors,
                "metric_averages": metric_averages,
                "ragas_runtime": ragas_runtime,
                "timeout_seconds": args.subprocess_timeout,
                "timed_out": timed_out,
                "stdout_tail": get_tail(stdout_str),
                "stderr_tail": get_tail(stderr_str)
            })

    # Resolve top-level status
    has_usable = any(s in ("PASS", "PARTIAL", "DRY_RUN_PASS") for s in evaluator_statuses)
    if not has_usable:
        top_status = "ERROR"
    elif all(s in ("PASS", "DRY_RUN_PASS") for s in evaluator_statuses):
        top_status = "PASS"
    else:
        top_status = "PARTIAL"

    # Compute summaries and recommendations
    usable_results = [res for res in results if res["status"] in ("PASS", "PARTIAL", "DRY_RUN_PASS")]
    
    best_numeric_score_health = None
    lowest_null_score_count = None
    if usable_results:
        # Sort for best numeric score count descending, then null count ascending
        sorted_by_numeric = sorted(
            usable_results,
            key=lambda x: (-x["score_health"].get("numeric_score_count", 0), x["score_health"].get("null_score_count", 0))
        )
        best_numeric_score_health = sorted_by_numeric[0]["evaluator_id"]

        # Sort for lowest null score count ascending, then numeric score count descending
        sorted_by_null = sorted(
            usable_results,
            key=lambda x: (x["score_health"].get("null_score_count", 0), -x["score_health"].get("numeric_score_count", 0))
        )
        lowest_null_score_count = sorted_by_null[0]["evaluator_id"]

    context_precision_values = {
        res["evaluator_id"]: res["metric_averages"].get("context_precision")
        for res in results if "context_precision" in res["metric_averages"]
    }
    answer_relevancy_values = {
        res["evaluator_id"]: res["metric_averages"].get("answer_relevancy")
        for res in results if "answer_relevancy" in res["metric_averages"]
    }

    # Heuristic recommendation
    rec_parts = []
    if usable_results:
        best_stable = sorted_by_null[0]
        rec_parts.append(
            f"Evaluator '{best_stable['evaluator_id']}' has the fewest null scores ({best_stable['score_health'].get('null_score_count', 0)}) "
            f"and is recommended as the most stable configuration."
        )

    # Check if context_precision is consistently 0.0
    cp_vals = [v for v in context_precision_values.values() if v is not None]
    if cp_vals and all(v == 0.0 for v in cp_vals):
        rec_parts.append(
            "context_precision remains 0.0 across all evaluators; this suggests a metric/reference mismatch rather than retrieval failure."
        )

    # Check if faithfulness nulls occur on smaller models
    if "faithfulness" in metrics_requested:
        smaller_nulls = []
        stronger_nulls = []
        for res in results:
            model_name = res["model"].lower()
            provider_name = res["provider"].lower()
            is_smaller = "3b" in model_name or "7b" in model_name or "8b" in model_name or provider_name == "ollama"
            null_count = faithfulness_null_counts.get(res["evaluator_id"], 0)
            if is_smaller:
                smaller_nulls.append(null_count)
            else:
                stronger_nulls.append(null_count)

        if any(n > 0 for n in smaller_nulls) and (not stronger_nulls or all(n == 0 for n in stronger_nulls)):
            rec_parts.append(
                "Faithfulness null scores occurred on smaller local models; faithfulness likely needs a stronger evaluator model "
                "(e.g., gpt-4o-mini, gpt-4o, or a 32b+ parameter local model)."
            )

    recommendation = " ".join(rec_parts) if rec_parts else "No clear recommendation could be determined based on the comparison results."

    summary = {
        "best_numeric_score_health": best_numeric_score_health,
        "lowest_null_score_count": lowest_null_score_count,
        "faithfulness_null_counts": faithfulness_null_counts,
        "context_precision_values": context_precision_values,
        "answer_relevancy_values": answer_relevancy_values,
        "recommendation": recommendation
    }

    # Construct overall JSON report
    comparison_report = {
        "status": top_status,
        "input_traces": str(input_traces_path),
        "metrics_requested": metrics_requested,
        "evaluators": [cfg["raw"] for cfg in evaluator_configs],
        "summary": summary,
        "results": results
    }

    # Create directories for output paths
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)

    # Save JSON report
    with output_json_path.open("w", encoding="utf-8") as jf:
        json.dump(comparison_report, jf, indent=2)
    print(f"\nComparison JSON report saved to {output_json_path}")

    # Generate suggested next command
    suggested_cmd = ""
    if usable_results:
        best_stable_res = sorted_by_null[0]
        orig_cfg = next((c for c in evaluator_configs if c["raw"].replace(":", "_").replace("-", "_").replace(".", "_") == best_stable_res["evaluator_id"]), None)
        if orig_cfg:
            suggested_cmd = (
                f".venv/bin/python evals/ragas_calibration.py \\\n"
                f"  --provider {orig_cfg['provider']} \\\n"
                f"  --evaluator-model {orig_cfg['model']} \\\n"
                f"  --embedding-model {orig_cfg['embedding_model']}"
            )

    # Generate Markdown report
    md_lines = [
        "# RAGAS Evaluator Comparison Report",
        "",
        f"- **Status**: {top_status}",
        f"- **Input Traces Path**: `{input_traces_path}`",
        f"- **Metrics Requested**: {', '.join(metrics_requested)}",
        "",
        "## Evaluator Comparison Table",
        "",
        "| Provider | Model | Status | Numeric Count | Null Count | Answer Relevancy | Context Precision | Faithfulness | Duration (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    ]

    for res in results:
        prov = res["provider"]
        mod = res["model"]
        stat = res["status"]
        num_c = res["score_health"].get("numeric_score_count", 0)
        null_c = res["score_health"].get("null_score_count", 0)
        
        ar = res["metric_averages"].get("answer_relevancy")
        ar_str = f"{ar:.4f}" if _is_number(ar) else "-"
        
        cp = res["metric_averages"].get("context_precision")
        cp_str = f"{cp:.4f}" if _is_number(cp) else "-"
        
        fth = res["metric_averages"].get("faithfulness")
        fth_str = f"{fth:.4f}" if _is_number(fth) else "-"
        
        dur = f"{res['duration_seconds']:.2f}"
        
        md_lines.append(
            f"| {prov} | {mod} | {stat} | {num_c} | {null_c} | {ar_str} | {cp_str} | {fth_str} | {dur} |"
        )

    md_lines.extend([
        "",
        "## Errors Section",
        ""
    ])

    has_errors = False
    for res in results:
        if res["errors"]:
            has_errors = True
            md_lines.append(f"### Errors for `{res['provider']}:{res['model']}`:")
            for err in res["errors"]:
                if isinstance(err, dict):
                    md_lines.append(f"- **[{err.get('type')}]**: {err.get('message')}")
                else:
                    md_lines.append(f"- {err}")
            md_lines.append("")

    if not has_errors:
        md_lines.append("No errors encountered during evaluation runs.")
        md_lines.append("")

    md_lines.extend([
        "## Recommendation Section",
        "",
        recommendation,
        "",
        "## Suggested Next Command",
        ""
    ])

    if suggested_cmd:
        md_lines.extend([
            "To run the calibration pipeline with the recommended stable configuration, execute:",
            "```bash",
            suggested_cmd,
            "```"
        ])
    else:
        md_lines.append("No recommended command available.")

    with output_md_path.open("w", encoding="utf-8") as mf:
        mf.write("\n".join(md_lines) + "\n")
    print(f"Comparison Markdown report saved to {output_md_path}")

if __name__ == "__main__":
    main()
