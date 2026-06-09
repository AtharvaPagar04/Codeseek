#!/usr/bin/env python3
"""Evaluation Policy Summary and Gating tool for CodeSeek."""

import argparse
import json
import sys
from pathlib import Path

def load_json_report(path_str: str | None) -> tuple[dict | None, bool]:
    """Loads a JSON report if path is provided and exists."""
    if not path_str:
        return None, False
    path = Path(path_str)
    if not path.exists():
        return None, False
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), True
    except Exception as e:
        print(f"Warning: Failed to load JSON report from {path_str}: {e}", file=sys.stderr)
        return None, False

def main() -> None:
    parser = argparse.ArgumentParser(description="CodeSeek Evaluation Policy and Gating Runner")
    parser.add_argument("--retrieval-report", help="Path to retrieval evaluation JSON report")
    parser.add_argument("--conversation-report", help="Path to conversation evaluation JSON report")
    parser.add_argument("--judge-calibration-report", help="Path to judge calibration JSON report")
    parser.add_argument("--ragas-report", help="Path to RAGAS evaluation JSON report")
    parser.add_argument("--evaluator-compare-report", help="Path to evaluator comparison JSON report")
    parser.add_argument("--output-json", required=True, help="Path to write the gating JSON report")
    parser.add_argument("--output-md", help="Path to write the gating Markdown report")
    parser.add_argument("--allow-empty-results", action="store_true", default=False, help="Allow empty results in retrieval")
    parser.add_argument("--answer-relevancy-warning-threshold", type=float, default=0.6, help="Warning threshold for answer relevancy")
    args = parser.parse_args()

    hard_gate_failures = []
    warnings = []
    diagnostics = set()

    reports_loaded = {
        "retrieval_report": False,
        "conversation_report": False,
        "judge_calibration_report": False,
        "ragas_report": False,
        "evaluator_compare_report": False,
    }

    # 1. Retrieval Report
    ret_report, loaded = load_json_report(args.retrieval_report)
    if loaded and ret_report:
        reports_loaded["retrieval_report"] = True
        
        status = ret_report.get("status")
        if status in ("FAIL", "ERROR"):
            hard_gate_failures.append("retrieval eval report status is FAIL or ERROR")
            
        summary = ret_report.get("summary", {})
        
        regressions = summary.get("exact_hit_regression_count", 0)
        if regressions > 0:
            hard_gate_failures.append(f"exact hit regressions detected: {regressions}")
            
        protected_total = summary.get("protected_hits_total", 0)
        protected_preservation = summary.get("protected_exact_hit_preserved@5")
        if protected_total > 0 and protected_preservation is not None and protected_preservation < 100.0:
            hard_gate_failures.append(f"protected hit preservation below 100%: {protected_preservation}%")
            
        empty_rate = summary.get("empty_result_rate", 0)
        if empty_rate > 0 and not args.allow_empty_results:
            hard_gate_failures.append(f"empty result rate above 0: {empty_rate}")

    # 2. Conversation Report
    conv_report, loaded = load_json_report(args.conversation_report)
    if loaded and conv_report:
        reports_loaded["conversation_report"] = True
        
        status = conv_report.get("status") or conv_report.get("overall_status")
        if status in ("FAIL", "ERROR"):
            hard_gate_failures.append("conversation eval report status is FAIL or ERROR")

    # 3. Judge Calibration Report
    cal_report, loaded = load_json_report(args.judge_calibration_report)
    if loaded and cal_report:
        reports_loaded["judge_calibration_report"] = True
        
        # Check expected file hits, rank, expected terms, and low answer relevancy
        queries = cal_report.get("queries") or cal_report.get("per_query", [])
        for q in queries:
            qid = q.get("query_id") or q.get("id")
            
            # expected context file hit
            expected_files = q.get("expected_files", [])
            if expected_files:
                hit = q.get("expected_context_file_hit")
                if hit is None:
                    hit = q.get("expected_file_context_hit")
                if hit is False:
                    hard_gate_failures.append(f"expected context file missing for query {qid}")

            # rank warnings
            rank = q.get("expected_context_file_rank")
            if rank is not None and rank > 1:
                warnings.append(f"expected context file rank is {rank} (> 1) for query {qid}")

            # missing terms
            terms_found = q.get("answer_mentions_expected_terms")
            if isinstance(terms_found, dict):
                for term, found in terms_found.items():
                    if not found:
                        warnings.append(f"missing expected answer term '{term}' for query {qid}")

            # answer relevancy query level
            scores = q.get("metric_scores") or q.get("ragas_scores", {})
            relevancy = scores.get("answer_relevancy")
            if relevancy is not None and relevancy < args.answer_relevancy_warning_threshold:
                warnings.append(f"answer_relevancy ({relevancy}) is below warning threshold ({args.answer_relevancy_warning_threshold}) for query {qid}")

            # Diagnostics - context precision = 0.0
            cp = scores.get("context_precision")
            if cp == 0.0:
                diagnostics.add("RAGAS context_precision is 0.0 (diagnostic-only; local code-location traces often default to 0.0 on small models)")

            # Diagnostics - faithfulness null/unstable
            faithfulness = scores.get("faithfulness")
            if faithfulness is None or faithfulness == "null" or qid in cal_report.get("null_score_counts", {}):
                diagnostics.add("RAGAS faithfulness on small local models is unstable/null (diagnostic-only; faithfulness requires larger/stronger model for stable scoring)")

        # answer relevancy average level
        avg_relevancy = cal_report.get("score_summary", {}).get("answer_relevancy_avg")
        if avg_relevancy is not None and avg_relevancy < args.answer_relevancy_warning_threshold:
            warnings.append(f"Average answer_relevancy ({avg_relevancy}) is below warning threshold ({args.answer_relevancy_warning_threshold})")

    # 4. RAGAS Report
    ragas_rep, loaded = load_json_report(args.ragas_report)
    if loaded and ragas_rep:
        reports_loaded["ragas_report"] = True
        
        # Check average relevancy
        avg_relevancy = ragas_rep.get("summary", {}).get("answer_relevancy")
        if avg_relevancy is not None and avg_relevancy < args.answer_relevancy_warning_threshold:
            warnings.append(f"Average answer_relevancy ({avg_relevancy}) is below warning threshold ({args.answer_relevancy_warning_threshold})")

        # Individual trace relevancy
        traces = ragas_rep.get("traces", [])
        for tr in traces:
            qid = tr.get("trace_id") or tr.get("question")
            relevancy = tr.get("scores", {}).get("answer_relevancy")
            if relevancy is not None and relevancy < args.answer_relevancy_warning_threshold:
                warnings.append(f"answer_relevancy ({relevancy}) is below warning threshold ({args.answer_relevancy_warning_threshold}) for query {qid}")

            # Diagnostic context_precision
            cp = tr.get("scores", {}).get("context_precision")
            if cp == 0.0:
                diagnostics.add("RAGAS context_precision is 0.0 (diagnostic-only; local code-location traces often default to 0.0 on small models)")

            # Diagnostic faithfulness
            faithfulness = tr.get("scores", {}).get("faithfulness")
            if faithfulness is None:
                diagnostics.add("RAGAS faithfulness on small local models is unstable/null (diagnostic-only; faithfulness requires larger/stronger model for stable scoring)")

    # 5. Evaluator Compare Report
    comp_report, loaded = load_json_report(args.evaluator_compare_report)
    if loaded and comp_report:
        reports_loaded["evaluator_compare_report"] = True
        
        results = comp_report.get("results", [])
        for res in results:
            eval_id = res.get("evaluator_id")
            null_cnt = res.get("score_health", {}).get("null_score_count", 0)
            if null_cnt > 0 or res.get("status") not in ("PASS", "DRY_RUN_PASS"):
                warnings.append(f"evaluator {eval_id} has {null_cnt} null scores or unstable execution")

        # Evaluator disagreement between 3B and 7B
        models = [res.get("model", "").lower() for res in results]
        has_3b = any("3b" in m for m in models)
        has_7b = any("7b" in m for m in models)
        if has_3b and has_7b:
            diagnostics.add("evaluator disagreement between 3B and 7B models (diagnostic-only)")

        # context_precision remains 0.0 across all evaluators
        cp_values = comp_report.get("summary", {}).get("context_precision_values", {})
        if cp_values and all(v == 0.0 for v in cp_values.values()):
            diagnostics.add("context_precision remains 0.0 across all evaluators (diagnostic-only)")

    # Resolve overall statuses
    hard_gate_status = "ERROR" if hard_gate_failures else "PASS"
    
    if hard_gate_failures:
        status = "ERROR"
    elif warnings:
        status = "WARN"
    else:
        status = "PASS"

    # Dynamic recommendation
    if status == "ERROR":
        recommendation = "Triage and fix the hard gate failures. Check retrieval files/intent accuracy, conversation branches, and expected context files."
    elif status == "WARN":
        recommendation = "Review the soft warnings. Verify if low answer relevancy or missing terms represent a real answer quality regression or acceptable variance."
    else:
        recommendation = "All gates passed successfully. CodeSeek meets the evaluation quality standards for release."

    # Build output dict
    output_data = {
        "status": status,
        "hard_gate_status": hard_gate_status,
        "warnings": sorted(list(set(warnings))),
        "diagnostics": sorted(list(diagnostics)),
        "hard_gate_failures": sorted(hard_gate_failures),
        "reports_loaded": reports_loaded,
        "recommendation": recommendation
    }

    # Write JSON output
    out_json_path = Path(args.output_json)
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    print(f"Evaluation policy summary written to {args.output_json}")

    # Write Markdown output if requested
    if args.output_md:
        out_md_path = Path(args.output_md)
        out_md_path.parent.mkdir(parents=True, exist_ok=True)
        
        md_lines = []
        md_lines.append("# CodeSeek Evaluation Policy and Gating Report")
        md_lines.append("")
        
        # Status Alert Box
        if status == "ERROR":
            md_lines.append("> [!CAUTION]")
            md_lines.append(f"> **Overall Gating Status: {status}**")
            md_lines.append("> One or more hard gates failed. Deployment or release is BLOCKED.")
        elif status == "WARN":
            md_lines.append("> [!WARNING]")
            md_lines.append(f"> **Overall Gating Status: {status}**")
            md_lines.append("> No hard gates failed, but soft warnings were triggered. Review is recommended.")
        else:
            md_lines.append("> [!NOTE]")
            md_lines.append(f"> **Overall Gating Status: {status}**")
            md_lines.append("> All gates passed successfully. Ready for release.")
            
        md_lines.append("")
        md_lines.append(f"- **Hard Gate Status**: `{hard_gate_status}`")
        md_lines.append("")
        
        md_lines.append("## Loaded Reports")
        md_lines.append("")
        md_lines.append("| Report Name | Loaded |")
        md_lines.append("| --- | --- |")
        for rep, load_val in reports_loaded.items():
            loaded_symbol = "✓ Yes" if load_val else "✗ No"
            md_lines.append(f"| `{rep}` | {loaded_symbol} |")
        md_lines.append("")

        md_lines.append("## Hard Gate Failures")
        if hard_gate_failures:
            for fail in sorted(hard_gate_failures):
                md_lines.append(f"- **[FAIL]** {fail}")
        else:
            md_lines.append("*No hard gate failures detected.*")
        md_lines.append("")

        md_lines.append("## Warnings")
        if warnings:
            for warn in sorted(list(set(warnings))):
                md_lines.append(f"- **[WARN]** {warn}")
        else:
            md_lines.append("*No warnings detected.*")
        md_lines.append("")

        md_lines.append("## Diagnostic-only Observations")
        if diagnostics:
            for diag in sorted(list(diagnostics)):
                md_lines.append(f"- **[INFO]** {diag}")
        else:
            md_lines.append("*No diagnostics captured.*")
        md_lines.append("")

        md_lines.append("## Recommendation")
        md_lines.append("")
        md_lines.append(recommendation)
        md_lines.append("")

        md_lines.append("## Policy Notes")
        md_lines.append("")
        md_lines.append("### RAGAS `context_precision` Local Model Mismatch Policy")
        md_lines.append("RAGAS `context_precision` must not fail or warn on current local retrieval quality. Evaluator comparison runs demonstrate that `context_precision` remains `0.0` across both `qwen2.5-coder:3b` and `qwen-coder-7b-16k` for code-location traces, even when local deterministic diagnostics show that the expected files were successfully retrieved at rank 1. This occurs because small local models struggle to correctly interpret and parse the code snippet relevance format requested by the RAGAS template. Therefore, all local `context_precision` findings are diagnostic-only.")
        md_lines.append("")
        md_lines.append("### RAGAS `faithfulness` Local Model Instability Policy")
        md_lines.append("Local 3B and 7B models frequently generate null/NaN scores for `faithfulness` due to output format parser issues. Consequently, null or fluctuating `faithfulness` scores on local evaluators are treated as diagnostic-only observations rather than blocking errors.")
        md_lines.append("")

        with open(out_md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines) + "\n")
        print(f"Evaluation policy Markdown report written to {args.output_md}")

    if status == "ERROR":
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
