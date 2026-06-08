#!/usr/bin/env python3
"""RAGAS judge calibration analysis tool."""

import argparse
import json
import os
import sys
from pathlib import Path
import yaml

# Ensure backend directory is in path
sys.path.append(str(Path(__file__).resolve().parent.parent))

def load_queries(queries_path: Path) -> list[dict]:
    if not queries_path.exists():
        raise FileNotFoundError(f"Queries file not found: {queries_path}")
    with open(queries_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "queries" not in data:
        raise ValueError("Queries YAML must contain a top-level 'queries' key.")
    queries = data["queries"]
    if not isinstance(queries, list):
        raise ValueError("Queries 'queries' key must map to a list.")
    return queries

def load_traces(trace_file_path: Path) -> list[dict]:
    if not trace_file_path.exists():
        raise FileNotFoundError(f"Trace file not found: {trace_file_path}")
    traces = []
    with open(trace_file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    traces.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Warning: skipped invalid JSON line in trace file: {e}", file=sys.stderr)
    return traces

def load_ragas_report(ragas_report_path: Path) -> dict:
    if not ragas_report_path.exists():
        raise FileNotFoundError(f"RAGAS report not found: {ragas_report_path}")
    with open(ragas_report_path, "r", encoding="utf-8") as f:
        return json.load(f)

def normalize_path(p: str) -> str:
    p = p.replace("\\", "/").strip()
    if p.startswith("./"):
        p = p[2:]
    p = p.strip("/")
    if p.startswith("backend/"):
        p = p[8:]
    return p

def main() -> None:
    parser = argparse.ArgumentParser(description="RAGAS Judge Calibration Analysis")
    parser.add_argument(
        "--ragas-report",
        required=True,
        help="Path to RAGAS report JSON"
    )
    parser.add_argument(
        "--trace-file",
        required=True,
        help="Path to trace file JSONL"
    )
    parser.add_argument(
        "--queries",
        required=True,
        help="Path to queries YAML"
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Path to output JSON report"
    )
    parser.add_argument(
        "--output-md",
        required=True,
        help="Path to output Markdown report"
    )
    args = parser.parse_args()

    ragas_report_path = Path(args.ragas_report)
    trace_file_path = Path(args.trace_file)
    queries_path = Path(args.queries)
    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)

    # Ensure parent directories exist
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        queries_data = load_queries(queries_path)
        traces_data = load_traces(trace_file_path)
        ragas_report = load_ragas_report(ragas_report_path)
    except Exception as e:
        print(f"Error loading inputs: {e}", file=sys.stderr)
        # Write error status report
        error_report = {
            "status": "ERROR",
            "overall_status": "ERROR",
            "error_message": str(e),
            "total_traces": 0,
            "metrics_found": [],
            "numeric_score_counts": {},
            "null_score_counts": {},
            "queries": [],
            "per_query": [],
            "retrieval_tuning_recommended": False,
            "deterministic_context_file_diagnostics": {
                "queries_with_expected_files": 0,
                "expected_file_hit_count": 0,
                "expected_file_hit_rate": 0.0,
                "mean_expected_file_rank": None,
                "mean_expected_file_reciprocal_rank": None,
                "mean_expected_context_file_precision": None
            }
        }
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(error_report, f, indent=2)
        sys.exit(1)

    # Index traces
    trace_by_query_id = {}
    trace_by_question = {}
    for trace in traces_data:
        qid = trace.get("extra", {}).get("query_id")
        if qid:
            trace_by_query_id[qid] = trace
        question = trace.get("question")
        if question:
            trace_by_question[question.strip().lower()] = trace

    # Index RAGAS report traces
    ragas_by_trace_id = {}
    ragas_by_question = {}
    for r_trace in ragas_report.get("traces", []):
        tid = r_trace.get("trace_id")
        if tid:
            ragas_by_trace_id[tid] = r_trace
        question = r_trace.get("question")
        if question:
            ragas_by_question[question.strip().lower()] = r_trace

    # Identify metrics run/requested
    metrics_found = ragas_report.get("metrics_run", [])
    if not metrics_found:
        # Extract from first trace scores if not explicit in report
        for r_trace in ragas_report.get("traces", []):
            scores = r_trace.get("scores", {})
            if scores:
                metrics_found = list(scores.keys())
                break

    per_query_rows = []
    for query in queries_data:
        qid = query.get("id")
        query_text = query.get("query", "")
        category = query.get("category", "")
        expected_files = query.get("expected_files") or []
        expected_answer_contains = query.get("expected_answer_contains") or []

        # Match trace
        trace = None
        if qid:
            trace = trace_by_query_id.get(qid)
        if not trace and query_text:
            trace = trace_by_question.get(query_text.strip().lower())

        if not trace:
            print(f"Warning: No trace found for query {qid} ('{query_text}')", file=sys.stderr)
            continue

        # Match RAGAS trace
        r_trace = None
        tid = trace.get("trace_id")
        if tid:
            r_trace = ragas_by_trace_id.get(tid)
        if not r_trace and query_text:
            r_trace = ragas_by_question.get(query_text.strip().lower())

        if not r_trace:
            r_trace = {}

        # Retrieved files and unique normalized list
        retrieved_files = []
        for ctx in trace.get("retrieved_contexts", []):
            rel_path = ctx.get("relative_path")
            if rel_path:
                retrieved_files.append(rel_path)

        norm_retrieved_unique = list(dict.fromkeys(normalize_path(f) for f in retrieved_files if f))

        # Expected context diagnostics
        expected_context_files_found = []
        expected_context_files_missing = []
        expected_context_file_rank = None
        expected_context_file_precision = None
        expected_context_file_reciprocal_rank = None

        if expected_files:
            norm_expected = {normalize_path(ef) for ef in expected_files}
            expected_context_file_hit = any(normalize_path(ef) in norm_retrieved_unique for ef in expected_files)
            
            # Rank (1-based index)
            for i, nf in enumerate(norm_retrieved_unique, start=1):
                if nf in norm_expected:
                    expected_context_file_rank = i
                    break
            
            # Reciprocal rank
            if expected_context_file_rank is not None:
                expected_context_file_reciprocal_rank = 1.0 / expected_context_file_rank
            
            # Precision
            if norm_retrieved_unique:
                found_count = sum(1 for f in norm_retrieved_unique if f in norm_expected)
                expected_context_file_precision = found_count / len(norm_retrieved_unique)
            else:
                expected_context_file_precision = 0.0

            # Found / missing original paths
            expected_context_files_found = [ef for ef in expected_files if normalize_path(ef) in norm_retrieved_unique]
            expected_context_files_missing = [ef for ef in expected_files if normalize_path(ef) not in norm_retrieved_unique]
        else:
            expected_context_file_hit = True

        expected_file_context_hit = expected_context_file_hit

        # Expected answer mentions
        answer = trace.get("answer") or ""
        answer_mentions_expected_terms = {}
        for term in expected_answer_contains:
            answer_mentions_expected_terms[term] = term.lower() in answer.lower()

        # Score parsing
        metric_scores = {}
        null_metrics = []
        r_scores = r_trace.get("scores", {})
        for metric_name in metrics_found:
            score = r_scores.get(metric_name)
            if score is not None and isinstance(score, (int, float)) and not isinstance(score, bool):
                metric_scores[metric_name] = score
            else:
                null_metrics.append(metric_name)

        # Errors for trace
        errors_for_trace = []
        for err in ragas_report.get("errors", []):
            if isinstance(err, dict):
                msg = err.get("message", "")
                if tid and (tid in msg or err.get("trace_id") == tid):
                    errors_for_trace.append(err)
            elif isinstance(err, str):
                if tid and tid in err:
                    errors_for_trace.append(err)

        # Interpretation rules
        interpretations = []

        # Check parser instability
        is_parser_instability = False
        if "faithfulness" in null_metrics:
            for err in errors_for_trace:
                err_type = ""
                err_msg = ""
                if isinstance(err, dict):
                    err_type = err.get("type", "")
                    err_msg = err.get("message", "")
                elif isinstance(err, str):
                    err_msg = err
                
                if err_type == "METRIC_EVALUATION_NaN" or "nan" in err_msg.lower() or "parser" in err_msg.lower() or "outputparser" in err_msg.lower():
                    is_parser_instability = True
                    break

        if metric_scores.get("context_precision") == 0.0 and expected_context_file_hit:
            interpretations.append("RAGAS context_precision disagrees with deterministic expected-file hit")
            interpretations.append("possible RAGAS context_precision/code-location mismatch")
        
        if expected_context_file_rank == 1:
            interpretations.append("expected file ranked first")
            
        if expected_context_file_rank is not None and expected_context_file_rank > 1:
            interpretations.append("expected file retrieved below rank 1")
            
        if expected_files and not expected_context_file_hit:
            interpretations.append("expected file missing from retrieved contexts")

        if is_parser_instability:
            interpretations.append("local judge parser instability")
            
        if isinstance(metric_scores.get("answer_relevancy"), (int, float)) and metric_scores.get("answer_relevancy") >= 0.8:
            interpretations.append("semantically relevant")

        if not interpretations:
            interpretation = "calibrated pass"
        else:
            interpretation = "; ".join(interpretations)

        per_query_rows.append({
            "query_id": qid,
            "query": query_text,
            "category": category,
            "expected_files": expected_files,
            "retrieved_files": retrieved_files,
            "expected_file_context_hit": expected_file_context_hit,
            "expected_answer_contains": expected_answer_contains,
            "answer_mentions_expected_terms": answer_mentions_expected_terms,
            "metric_scores": metric_scores,
            "null_metrics": null_metrics,
            "errors_for_trace": errors_for_trace,
            "interpretation": interpretation,
            "expected_context_file_hit": expected_context_file_hit,
            "expected_context_file_rank": expected_context_file_rank,
            "expected_context_file_precision": expected_context_file_precision,
            "expected_context_file_reciprocal_rank": expected_context_file_reciprocal_rank,
            "expected_context_files_found": expected_context_files_found,
            "expected_context_files_missing": expected_context_files_missing
        })

    # Check if retrieval tuning is recommended
    # Do not recommend retrieval tuning unless expected_file_context_hit is false (for a query that has expected files).
    tuning_recommended_queries = []
    for row in per_query_rows:
        if row["expected_files"] and not row["expected_file_context_hit"]:
            tuning_recommended_queries.append(row["query_id"])
    
    retrieval_tuning_recommended = len(tuning_recommended_queries) > 0

    # Numeric / null score counts
    numeric_score_counts = {m: 0 for m in metrics_found}
    null_score_counts = {m: 0 for m in metrics_found}
    for row in per_query_rows:
        for m in metrics_found:
            if m in row["metric_scores"]:
                numeric_score_counts[m] += 1
            else:
                null_score_counts[m] += 1

    total_numeric = sum(numeric_score_counts.values())
    total_null = sum(null_score_counts.values())

    # Calculate deterministic aggregate diagnostics
    queries_with_expected_files = sum(1 for q in per_query_rows if q["expected_files"])
    expected_file_hit_count = sum(1 for q in per_query_rows if q["expected_files"] and q["expected_context_file_hit"])
    
    expected_file_hit_rate = (
        expected_file_hit_count / queries_with_expected_files
        if queries_with_expected_files > 0
        else 0.0
    )
    
    ranks = [
        q["expected_context_file_rank"]
        for q in per_query_rows
        if q["expected_context_file_rank"] is not None
    ]
    mean_expected_file_rank = (
        sum(ranks) / len(ranks)
        if ranks
        else None
    )
    
    rr_list = [
        q["expected_context_file_reciprocal_rank"]
        for q in per_query_rows
        if q["expected_context_file_reciprocal_rank"] is not None
    ]
    mean_expected_file_reciprocal_rank = (
        sum(rr_list) / len(rr_list)
        if rr_list
        else None
    )
    
    precisions = [
        q["expected_context_file_precision"]
        for q in per_query_rows
        if q["expected_context_file_precision"] is not None
    ]
    mean_expected_context_file_precision = (
        sum(precisions) / len(precisions)
        if precisions
        else None
    )
    
    deterministic_context_file_diagnostics = {
        "queries_with_expected_files": queries_with_expected_files,
        "expected_file_hit_count": expected_file_hit_count,
        "expected_file_hit_rate": expected_file_hit_rate,
        "mean_expected_file_rank": mean_expected_file_rank,
        "mean_expected_file_reciprocal_rank": mean_expected_file_reciprocal_rank,
        "mean_expected_context_file_precision": mean_expected_context_file_precision
    }

    # Overall status (Do not fail report just because faithfulness is null)
    # We set status to "PASS" if we ran and loaded everything successfully.
    overall_status = "PASS"

    output_json = {
        "status": overall_status,
        "overall_status": overall_status,
        "total_traces": len(traces_data),
        "metrics_found": metrics_found,
        "numeric_score_counts": numeric_score_counts,
        "null_score_counts": null_score_counts,
        "total_numeric_scores": total_numeric,
        "total_null_scores": total_null,
        "numeric_score_count": total_numeric,
        "null_score_count": total_null,
        "queries": per_query_rows,
        "per_query": per_query_rows,
        "retrieval_tuning_recommended": retrieval_tuning_recommended,
        "deterministic_context_file_diagnostics": deterministic_context_file_diagnostics
    }

    # Write JSON
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output_json, f, indent=2)
    print(f"Calibration JSON analysis saved to {output_json_path}")

    # Generate Markdown Report
    md_lines = []
    md_lines.append("# RAGAS Judge Calibration Analysis Report v1")
    md_lines.append("")
    md_lines.append(f"- **Overall Status**: `{overall_status}`")
    md_lines.append(f"- **Total Traces Analyzed**: {len(traces_data)}")
    md_lines.append(f"- **Metrics Evaluated**: {', '.join(metrics_found) if metrics_found else 'None'}")
    md_lines.append("")

    # Summary table
    md_lines.append("## Summary Table")
    md_lines.append("")
    md_lines.append("| Metric | Numeric Scores | Null/NaN Scores | Total Evaluated |")
    md_lines.append("| --- | --- | --- | --- |")
    for m in metrics_found:
        num_cnt = numeric_score_counts[m]
        null_cnt = null_score_counts[m]
        md_lines.append(f"| `{m}` | {num_cnt} | {null_cnt} | {num_cnt + null_cnt} |")
    md_lines.append("")

    # Deterministic Context-File Diagnostics
    md_lines.append("## Deterministic Context-File Diagnostics")
    md_lines.append("")
    md_lines.append("These deterministic metrics are not RAGAS scores. They are local diagnostics used to explain whether RAGAS `context_precision` aligns with expected file retrieval.")
    md_lines.append("")
    md_lines.append(f"- **Queries with Expected Files**: {queries_with_expected_files}")
    md_lines.append(f"- **Expected File Hit Rate**: {expected_file_hit_rate:.2%} ({expected_file_hit_count}/{queries_with_expected_files})")
    mean_rank_str = f"{mean_expected_file_rank:.2f}" if mean_expected_file_rank is not None else "N/A"
    md_lines.append(f"- **Mean Expected File Rank**: {mean_rank_str}")
    mean_rr_str = f"{mean_expected_file_reciprocal_rank:.4f}" if mean_expected_file_reciprocal_rank is not None else "N/A"
    md_lines.append(f"- **Mean Reciprocal Rank (MRR)**: {mean_rr_str}")
    mean_prec_str = f"{mean_expected_context_file_precision:.4f}" if mean_expected_context_file_precision is not None else "N/A"
    md_lines.append(f"- **Mean Deterministic Expected-File Precision**: {mean_prec_str}")
    md_lines.append("")

    md_lines.append("### Retrieval Tuning Recommendation")
    if retrieval_tuning_recommended:
        md_lines.append("> [!WARNING]")
        md_lines.append(f"> **Retrieval tuning is RECOMMENDED** because the following queries failed to retrieve expected context files: {', '.join(tuning_recommended_queries)}.")
    else:
        md_lines.append("> [!NOTE]")
        md_lines.append("> **Retrieval tuning is NOT recommended** because all query runs successfully retrieved the expected context files (100% expected file context hit rate).")
    md_lines.append("")

    md_lines.append("## Per-Query Details")
    md_lines.append("")
    for row in per_query_rows:
        md_lines.append(f"### Query `{row['query_id']}`: \"{row['query']}\"")
        md_lines.append(f"- **Category**: `{row['category']}`")
        md_lines.append(f"- **Expected Files**: {', '.join([f'`{f}`' for f in row['expected_files']]) if row['expected_files'] else '*None*'}")
        md_lines.append(f"- **Expected File Context Hit**: `{row['expected_file_context_hit']}`")
        md_lines.append(f"- **Expected Context File Hit**: `{row['expected_context_file_hit']}`")
        
        rank_val = row['expected_context_file_rank']
        rank_str = str(rank_val) if rank_val is not None else 'N/A'
        md_lines.append(f"- **Expected Context File Rank**: `{rank_str}`")
        
        prec_val = row['expected_context_file_precision']
        prec_str = f"{prec_val:.4f}" if prec_val is not None else 'N/A'
        md_lines.append(f"- **Expected Context File Precision**: `{prec_str}`")
        
        rr_val = row['expected_context_file_reciprocal_rank']
        rr_str = f"{rr_val:.4f}" if rr_val is not None else 'N/A'
        md_lines.append(f"- **Expected Context File Reciprocal Rank**: `{rr_str}`")
        
        md_lines.append(f"- **Found Expected Files**: {', '.join([f'`{f}`' for f in row['expected_context_files_found']]) if row['expected_context_files_found'] else '*None*'}")
        md_lines.append(f"- **Missing Expected Files**: {', '.join([f'`{f}`' for f in row['expected_context_files_missing']]) if row['expected_context_files_missing'] else '*None*'}")
        
        # retrieved files list (show top 3)
        ret_files = row["retrieved_files"]
        if ret_files:
            ret_display = ", ".join([f"`{f}`" for f in ret_files[:3]])
            if len(ret_files) > 3:
                ret_display += f" (+ {len(ret_files) - 3} more)"
            md_lines.append(f"- **Retrieved Files**: {ret_display}")
        else:
            md_lines.append("- **Retrieved Files**: *None*")

        # Answer expected terms matches
        term_matches = []
        for term, matched in row["answer_mentions_expected_terms"].items():
            status_symbol = "✓" if matched else "✗"
            term_matches.append(f"`{term}` ({status_symbol})")
        if term_matches:
            md_lines.append(f"- **Expected Answer Terms**: {', '.join(term_matches)}")

        # Scores table
        score_parts = []
        for m in metrics_found:
            val = row["metric_scores"].get(m)
            val_str = f"{val:.4f}" if val is not None else "null/NaN"
            score_parts.append(f"{m}: `{val_str}`")
        md_lines.append(f"- **Scores**: {', '.join(score_parts)}")

        # Interpretation
        md_lines.append(f"- **Interpretation**: **{row['interpretation']}**")
        
        if row["errors_for_trace"]:
            md_lines.append("- **Errors**:")
            for err in row["errors_for_trace"]:
                if isinstance(err, dict):
                    md_lines.append(f"  - `[{err.get('type')}]` {err.get('message')}")
                else:
                    md_lines.append(f"  - {err}")
        md_lines.append("")

    # Known limitations
    md_lines.append("## Known Local Evaluator Limitations")
    md_lines.append("")
    md_lines.append("When running RAGAS evaluation locally with Ollama using smaller models like `qwen2.5-coder:3b`:")
    md_lines.append("1. **Faithfulness Instability**: Faithfulness scoring is highly unstable and frequently fails due to `RagasOutputParserException` or output formats that do not comply with RAGAS JSON expectations, resulting in `NaN` scores.")
    md_lines.append("2. **Context Precision 0.0**: `context_precision` often defaults to `0.0` even when deterministic expected files are retrieved. This happens because the small model struggles to correctly rank or parse the code snippets' exact relevance mapping within the context layout requested by the RAGAS template.")
    md_lines.append("3. **Context Length Constraints**: Local 3B models have constrained context windows and processing speeds, which can cause timeout issues under parallel load.")
    md_lines.append("")

    # Recommended stable local smoke metrics
    md_lines.append("## Recommended Stable Local Smoke Metrics")
    md_lines.append("")
    md_lines.append("- **Recommended stable local smoke metrics command**:")
    md_lines.append("  ```bash")
    md_lines.append("  --metrics answer_relevancy,context_precision")
    md_lines.append("  ```")
    md_lines.append("- **Recommendation on Faithfulness**:")
    md_lines.append("  > [!IMPORTANT]")
    md_lines.append("  > Faithfulness should be run separately or with a stronger judge (e.g. `qwen2.5-coder:32b`, `llama3:70b`, or commercial APIs like OpenAI GPT-4o). Running faithfulness on `qwen2.5-coder:3b` is not recommended for stable CI pipeline gates.")
    md_lines.append("")

    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"Calibration Markdown analysis saved to {output_md_path}")

if __name__ == "__main__":
    main()
