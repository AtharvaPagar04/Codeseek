"""RAGAS calibration runner for CodeSeek."""

import os
import sys
import json
import argparse
from pathlib import Path
from unittest.mock import patch
import yaml

# Ensure backend directory is in path
sys.path.append(str(Path(__file__).resolve().parent.parent))

def load_calibration_queries(yaml_path: str | Path) -> list[dict]:
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"Calibration queries file not found: {yaml_path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "queries" not in data:
        raise ValueError("Calibration YAML must contain a top-level 'queries' key.")
    queries = data["queries"]
    if not isinstance(queries, list):
        raise ValueError("Calibration 'queries' key must map to a list.")
    return queries

def compute_calibration_diagnostics(query: dict, trace: dict) -> dict:
    answer = trace.get("answer") or ""
    contexts = trace.get("retrieved_contexts") or []
    
    top_context_files = [c["relative_path"] for c in contexts if c.get("relative_path")]
    expected_files = query.get("expected_files") or []
    
    # expected_file_found_in_contexts
    expected_file_found_in_contexts = False
    for ef in expected_files:
        if ef in top_context_files:
            expected_file_found_in_contexts = True
            break
            
    # expected_answer_terms_found
    expected_terms = query.get("expected_answer_contains") or []
    expected_answer_terms_found = {}
    for term in expected_terms:
        expected_answer_terms_found[term] = term.lower() in answer.lower()
        
    # answer_mentions_expected_file
    answer_mentions_expected_file = False
    for ef in expected_files:
        basename = Path(ef).name
        if ef in answer or basename in answer:
            answer_mentions_expected_file = True
            break
            
    # answer_mentions_any_top_context_file
    answer_mentions_any_top_context_file = False
    for cf in top_context_files:
        basename = Path(cf).name
        if cf in answer or basename in answer:
            answer_mentions_any_top_context_file = True
            break

    return {
        "answer_length_chars": len(answer),
        "context_count": len(contexts),
        "total_context_chars": sum(len(c.get("content") or "") for c in contexts),
        "top_context_files": top_context_files,
        "expected_file_found_in_contexts": expected_file_found_in_contexts,
        "expected_answer_terms_found": expected_answer_terms_found,
        "answer_mentions_expected_file": answer_mentions_expected_file,
        "answer_mentions_any_top_context_file": answer_mentions_any_top_context_file
    }

def interpret_result(query: dict, diags: dict, ragas_scores: dict, ragas_failed: bool) -> str:
    if ragas_failed:
        return "ragas_execution_failed"
    if diags["answer_length_chars"] < 200:
        return "answer_too_short_for_ragas"
        
    expected_files = query.get("expected_files") or []
    has_expected_files = len(expected_files) > 0
    
    if has_expected_files and not diags["expected_file_found_in_contexts"]:
        return "retrieval_context_missing_expected_file"
        
    faithfulness = ragas_scores.get("faithfulness")
    relevancy = ragas_scores.get("answer_relevancy")
    precision = ragas_scores.get("context_precision")
    
    # Check if all scores are 0.0 or None
    all_zero = True
    for val in (faithfulness, relevancy, precision):
        if val is not None and val > 0.0:
            all_zero = False
            break
            
    if all_zero:
        if (not has_expected_files or (diags["expected_file_found_in_contexts"] and diags["answer_mentions_expected_file"])):
            return "retrieval_context_good_but_local_judge_low_score"
            
    # Check for actual grounding problems (low average score but not all zero)
    avg_score = None
    vals = [v for v in (faithfulness, relevancy, precision) if isinstance(v, (int, float))]
    if vals:
        avg_score = sum(vals) / len(vals)
    if avg_score is not None and avg_score < 0.5:
        return "actual_answer_grounding_problems"
        
    return "calibrated_pass"

def main() -> None:
    parser = argparse.ArgumentParser(description="Run RAGAS calibration pipeline.")
    parser.add_argument(
        "--queries",
        type=str,
        default="evals/ragas_calibration_queries.yaml",
        help="Path to calibration queries YAML.",
    )
    parser.add_argument(
        "--trace-output",
        type=str,
        default="evals/reports/ragas_calibration_traces.jsonl",
        help="Path to write calibration traces.",
    )
    parser.add_argument(
        "--ragas-output",
        type=str,
        default="evals/reports/ragas_calibration_latest.json",
        help="Path to write RAGAS report.",
    )
    parser.add_argument(
        "--summary-output",
        type=str,
        default="evals/reports/ragas_calibration_summary.json",
        help="Path to write calibration summary JSON.",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID to retrieve repository status/collection.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="ollama",
        help="RAGAS evaluator provider: 'openai' or 'ollama'.",
    )
    parser.add_argument(
        "--evaluator-model",
        type=str,
        default="qwen2.5-coder:3b",
        help="RAGAS evaluator model.",
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default="nomic-embed-text",
        help="RAGAS embedding model.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of queries evaluated.",
    )
    parser.add_argument(
        "--skip-ragas",
        action="store_true",
        help="Skip RAGAS evaluation execution.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing trace file instead of backup/deleting.",
    )
    parser.add_argument(
        "--ragas-timeout",
        type=int,
        default=None,
        help="Timeout in seconds for RAGAS evaluation."
    )
    parser.add_argument(
        "--ragas-max-workers",
        type=int,
        default=None,
        help="Maximum concurrent workers for RAGAS."
    )
    parser.add_argument(
        "--ragas-max-retries",
        type=int,
        default=None,
        help="Maximum retries for RAGAS evaluation."
    )
    parser.add_argument(
        "--metrics",
        type=str,
        default=None,
        help="Comma-separated list of metrics to run."
    )
    parser.add_argument(
        "--expected-repo-root",
        type=str,
        default=None,
        help="Expected repository root directory.",
    )
    parser.add_argument(
        "--expected-collection",
        type=str,
        default=None,
        help="Expected Qdrant collection name.",
    )
    args = parser.parse_args()

    queries_path = Path(args.queries)
    trace_output_path = Path(args.trace_output)
    ragas_output_path = Path(args.ragas_output)
    summary_output_path = Path(args.summary_output)

    # 1. Resolve session from DB if provided
    if args.session_id:
        try:
            from retrieval.db import db_cursor
            with db_cursor() as (conn, cursor):
                cursor.execute(
                    "SELECT collection, repo_root FROM repo_sessions WHERE id = ?",
                    (args.session_id,)
                )
                row = cursor.fetchone()
                if row:
                    db_session = dict(row)
                    os.environ["QDRANT_COLLECTION_NAME"] = db_session["collection"]
                    os.environ["RETRIEVAL_REPO_ROOT"] = db_session["repo_root"]
                    print(f"Bound to session {args.session_id}: collection={db_session['collection']}, repo_root={db_session['repo_root']}")
        except Exception as e:
            print(f"Database session query failed: {e}. Using env/default binding.")

    # 2. Validate session binding
    import retrieval.config
    actual_repo_root = retrieval.config.get_repo_root()
    actual_collection = retrieval.config.get_collection_name()

    mismatches = []
    if args.expected_repo_root:
        expected_root_abs = str(Path(args.expected_repo_root).resolve())
        actual_root_abs = str(Path(actual_repo_root).resolve())
        if expected_root_abs != actual_root_abs:
            mismatches.append(
                f"Session binding mismatch: expected repo_root {args.expected_repo_root} but got {actual_repo_root}"
            )
            
    if args.expected_collection:
        if args.expected_collection != actual_collection:
            mismatches.append(
                f"Session binding mismatch: expected collection {args.expected_collection} but got {actual_collection}"
            )

    if mismatches:
        for err_msg in mismatches:
            print(err_msg, file=sys.stderr)
            
        if args.summary_output:
            err_dict = {
                "type": "SESSION_BINDING_MISMATCH",
                "message": "; ".join(mismatches),
                "expected_repo_root": args.expected_repo_root or "",
                "actual_repo_root": actual_repo_root or "",
                "expected_collection": args.expected_collection or "",
                "actual_collection": actual_collection or ""
            }
            summary_err_report = {
                "status": "ERROR",
                "errors": [err_dict]
            }
            try:
                summary_output_path.parent.mkdir(parents=True, exist_ok=True)
                with summary_output_path.open("w", encoding="utf-8") as out_f:
                    json.dump(summary_err_report, out_f, indent=2)
                print(f"Calibration error summary written to: {summary_output_path}")
            except Exception as e:
                print(f"Failed to write error summary to {summary_output_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Create directories and handle old trace file deletion/backup
    trace_output_path.parent.mkdir(parents=True, exist_ok=True)
    ragas_output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_output_path.parent.mkdir(parents=True, exist_ok=True)

    if trace_output_path.exists() and not args.append:
        try:
            trace_output_path.unlink()
        except Exception as e:
            print(f"Failed to delete old trace file: {e}")

    # 4. Load queries
    try:
        queries = load_calibration_queries(queries_path)
    except Exception as e:
        print(f"Error loading calibration queries: {e}", file=sys.stderr)
        sys.exit(1)

    if args.limit:
        queries = queries[:args.limit]

    # 4. Enable answer tracing configuration
    os.environ["ENABLE_ANSWER_TRACE_LOGGING"] = "1"
    os.environ["ANSWER_TRACE_OUTPUT_PATH"] = str(trace_output_path)

    import retrieval.config
    retrieval.config.ENABLE_ANSWER_TRACE_LOGGING = True
    retrieval.config.ANSWER_TRACE_OUTPUT_PATH = str(trace_output_path)

    from retrieval.main import run_query
    from retrieval.memory import ConversationMemory

    # Setup write interception to inject query_id into trace extras
    import evals.answer_trace_writer
    original_write = evals.answer_trace_writer.write_answer_trace

    current_query_id = None
    generated_traces = []

    def custom_write(trace: dict, output_path: str | None = None):
        if current_query_id:
            trace.setdefault("extra", {})["query_id"] = current_query_id
        generated_traces.append(trace)
        return original_write(trace, output_path)

    evals.answer_trace_writer.write_answer_trace = custom_write

    # 5. Run answer generation for each query
    print(f"Generating answers for {len(queries)} queries...")
    for idx, q in enumerate(queries, start=1):
        qid = q["id"]
        query_text = q["query"]
        print(f"[{idx}/{len(queries)}] Running: {query_text} (ID: {qid})")
        
        current_query_id = qid
        memory = ConversationMemory(max_turns=5)
        memory.session_id = args.session_id
        
        try:
            run_query(query_text, memory)
        except Exception as e:
            print(f"Failed to generate answer for {qid}: {e}", file=sys.stderr)

    # 6. Run RAGAS if not skipped
    ragas_failed = False
    ragas_report = {}
    if not args.skip_ragas:
        print("Invoking RAGAS evaluation...")
        import evals.ragas_eval
        ragas_args = [
            "ragas_eval.py",
            "--input", str(trace_output_path),
            "--output", str(ragas_output_path),
            "--allow-no-ground-truth",
            "--evaluator-provider", args.provider,
            "--evaluator-model", args.evaluator_model,
            "--embedding-model", args.embedding_model,
            "--check-evaluator-health"
        ]
        if args.ragas_timeout is not None:
            ragas_args.extend(["--ragas-timeout", str(args.ragas_timeout)])
        if args.ragas_max_workers is not None:
            ragas_args.extend(["--ragas-max-workers", str(args.ragas_max_workers)])
        if args.ragas_max_retries is not None:
            ragas_args.extend(["--ragas-max-retries", str(args.ragas_max_retries)])
        if args.metrics is not None:
            ragas_args.extend(["--metrics", str(args.metrics)])
        
        with patch("sys.argv", ragas_args):
            try:
                evals.ragas_eval.main()
            except SystemExit as se:
                if se.code != 0:
                    print(f"RAGAS evaluation exited with code {se.code}")
                    ragas_failed = True
            except Exception as e:
                print(f"RAGAS evaluation raised exception: {e}")
                ragas_failed = True

        if not ragas_failed and ragas_output_path.exists():
            try:
                with ragas_output_path.open("r", encoding="utf-8") as f:
                    ragas_report = json.load(f)
            except Exception as e:
                print(f"Failed to read RAGAS output: {e}")
                ragas_failed = True
    else:
        print("Skipped RAGAS evaluation.")
        ragas_failed = True

    # 7. Generate calibration summary
    # Map generated traces by query_id
    traces_by_id = {t.get("extra", {}).get("query_id"): t for t in generated_traces if t.get("extra", {}).get("query_id")}
    
    # Also fallback map by question text if needed
    if not traces_by_id:
        traces_by_id = {t.get("question"): t for t in generated_traces}

    # Map RAGAS scores from ragas_report if available
    ragas_scores_by_id = {}
    if not ragas_failed and "traces" in ragas_report:
        for rt in ragas_report["traces"]:
            tid = rt.get("extra", {}).get("query_id")
            if not tid:
                q_text = rt.get("question")
                for q in queries:
                    if q["query"] == q_text:
                        tid = q["id"]
                        break
            if tid:
                ragas_scores_by_id[tid] = rt.get("scores") or {}

    query_details = []
    for q in queries:
        qid = q["id"]
        question = q["query"]
        trace = traces_by_id.get(qid) or traces_by_id.get(question)
        
        if not trace:
            continue
            
        diags = compute_calibration_diagnostics(q, trace)
        r_scores = ragas_scores_by_id.get(qid) or {}
        
        scores_dict = {
            "faithfulness": r_scores.get("faithfulness"),
            "answer_relevancy": r_scores.get("answer_relevancy"),
            "context_precision": r_scores.get("context_precision"),
        }
        
        interpretation = interpret_result(q, diags, scores_dict, ragas_failed)
        
        query_details.append({
            "query_id": qid,
            "question": question,
            "deterministic_diagnostics": diags,
            "ragas_scores": scores_dict,
            "interpretation": interpretation
        })

    # Calculate overall averages
    avg_answer_length = 0
    avg_context_count = 0
    expected_hit_count = 0
    has_expected_files_count = 0
    mentions_expected_count = 0
    
    faithfulness_vals = []
    relevancy_vals = []
    precision_vals = []
    
    for qd in query_details:
        diags = qd["deterministic_diagnostics"]
        avg_answer_length += diags["answer_length_chars"]
        avg_context_count += diags["context_count"]
        
        q_obj = next((x for x in queries if x["id"] == qd["query_id"]), {})
        has_exp = len(q_obj.get("expected_files", [])) > 0
        if has_exp:
            has_expected_files_count += 1
            if diags["expected_file_found_in_contexts"]:
                expected_hit_count += 1
            if diags["answer_mentions_expected_file"]:
                mentions_expected_count += 1
                
        scores = qd["ragas_scores"]
        if isinstance(scores.get("faithfulness"), (int, float)):
            faithfulness_vals.append(scores["faithfulness"])
        if isinstance(scores.get("answer_relevancy"), (int, float)):
            relevancy_vals.append(scores["answer_relevancy"])
        if isinstance(scores.get("context_precision"), (int, float)):
            precision_vals.append(scores["context_precision"])

    num_queries = len(query_details)
    avg_answer_length = round(avg_answer_length / num_queries, 2) if num_queries else 0
    avg_context_count = round(avg_context_count / num_queries, 2) if num_queries else 0
    expected_hit_rate = round(expected_hit_count / has_expected_files_count, 2) if has_expected_files_count else 0.0
    mentions_expected_rate = round(mentions_expected_count / has_expected_files_count, 2) if has_expected_files_count else 0.0

    score_summary = {
        "faithfulness_avg": round(sum(faithfulness_vals) / len(faithfulness_vals), 4) if faithfulness_vals else None,
        "answer_relevancy_avg": round(sum(relevancy_vals) / len(relevancy_vals), 4) if relevancy_vals else None,
        "context_precision_avg": round(sum(precision_vals) / len(precision_vals), 4) if precision_vals else None,
    }

    diagnostic_summary = {
        "avg_answer_length_chars": avg_answer_length,
        "avg_context_count": avg_context_count,
        "expected_file_context_hit_rate": expected_hit_rate,
        "answer_mentions_expected_file_rate": mentions_expected_rate
    }

    status = "PASS"
    if ragas_failed and not args.skip_ragas:
        status = "ERROR"

    summary_report = {
        "status": status,
        "schema_version": "ragas_calibration.v1",
        "total_queries": len(queries),
        "traces_generated": len(generated_traces),
        "ragas_status": ragas_report.get("status", "ERROR" if ragas_failed else "SKIPPED"),
        "score_summary": score_summary,
        "diagnostic_summary": diagnostic_summary,
        "queries": query_details
    }

    if "ragas_runtime" in ragas_report:
        summary_report["ragas_runtime"] = ragas_report["ragas_runtime"]

    # Write JSON summary
    with summary_output_path.open("w", encoding="utf-8") as out_f:
        json.dump(summary_report, out_f, indent=2)
    print(f"Calibration summary written to: {summary_output_path}")

    # Write Markdown summary report
    md_output_path = summary_output_path.with_suffix(".md")
    md_lines = [
        "# RAGAS Calibration Summary Report",
        "",
        f"- **Status**: {status}",
        f"- **Total Queries**: {len(queries)}",
        f"- **Traces Generated**: {len(generated_traces)}",
        f"- **RAGAS Evaluator**: {args.provider} ({args.evaluator_model})",
        "",
        "## Summary Metrics",
        "",
        "### RAGAS Averages",
        f"- **Faithfulness**: {score_summary['faithfulness_avg']}",
        f"- **Answer Relevancy**: {score_summary['answer_relevancy_avg']}",
        f"- **Context Precision**: {score_summary['context_precision_avg']}",
        "",
        "### Deterministic Diagnostics Averages",
        f"- **Avg Answer Length (chars)**: {avg_answer_length}",
        f"- **Avg Context Count**: {avg_context_count}",
        f"- **Expected File Hit Rate**: {expected_hit_rate * 100}%",
        f"- **Answer Mentions Expected File Rate**: {mentions_expected_rate * 100}%",
        "",
        "## Query Details",
        ""
    ]

    for qd in query_details:
        diags = qd["deterministic_diagnostics"]
        scores = qd["ragas_scores"]
        
        md_lines.extend([
            f"### {qd['query_id']}: {qd['question']}",
            "",
            "- **Top Context Files**:",
        ])
        if diags["top_context_files"]:
            for f in diags["top_context_files"][:3]:
                md_lines.append(f"  - `{f}`")
            if len(diags["top_context_files"]) > 3:
                md_lines.append(f"  - ... and {len(diags['top_context_files']) - 3} more")
        else:
            md_lines.append("  - *None retrieved*")
            
        md_lines.extend([
            "",
            "- **Diagnostics**:",
            f"  - Answer Length: {diags['answer_length_chars']} chars",
            f"  - Context Count: {diags['context_count']}",
            f"  - Expected File Found in Contexts: `{diags['expected_file_found_in_contexts']}`",
            f"  - Answer Mentions Expected File: `{diags['answer_mentions_expected_file']}`",
            f"  - Answer Mentions Any Top Context File: `{diags['answer_mentions_any_top_context_file']}`",
            "",
            "- **RAGAS Scores**:",
            f"  - Faithfulness: {scores['faithfulness']}",
            f"  - Answer Relevancy: {scores['answer_relevancy']}",
            f"  - Context Precision: {scores['context_precision']}",
            "",
            f"- **Interpretation**: `{qd['interpretation']}`",
            ""
        ])

    with md_output_path.open("w", encoding="utf-8") as md_f:
        md_f.write("\n".join(md_lines))
    print(f"Calibration Markdown report written to: {md_output_path}")

if __name__ == "__main__":
    main()
