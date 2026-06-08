import argparse
import json
import os
import re
import sys
from pathlib import Path

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Try defensive imports for Ragas
try:
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
    RAGAS_AVAILABLE = True
except ImportError:
    RAGAS_AVAILABLE = False


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _count_numeric_scores(trace_results: list[dict]) -> int:
    count = 0
    for trace in trace_results:
        for value in (trace.get("scores") or {}).values():
            if _is_number(value):
                count += 1
    return count


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_EVALUATOR_MODEL = "qwen2.5-coder:3b"
DEFAULT_OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"


def _model_matches(requested: str, available: str) -> bool:
    requested = requested.lower()
    available = available.lower()
    if requested == available:
        return True
    req_norm = requested.replace(":latest", "")
    av_norm = available.replace(":latest", "")
    if req_norm == av_norm:
        return True
    if ":" in requested and ":" in available:
        req_base, req_tag = requested.split(":", 1)
        av_base, av_tag = available.split(":", 1)
        if req_base == av_base:
            if req_tag in av_tag or av_tag in req_tag:
                return True
    return False


def check_ollama_health(base_url: str, model: str, embedding_model: str) -> tuple[bool, list[str]]:
    import urllib.request
    import json
    
    url = f"{base_url.rstrip('/')}/api/tags"
    errors = []
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status != 200:
                return False, [f"Ollama returned status code {response.status}"]
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return False, [f"Ollama not reachable at {base_url}: {e}"]
        
    models = data.get("models", [])
    available_names = [m.get("name") for m in models if m.get("name")]
    
    eval_ok = False
    for av in available_names:
        if _model_matches(model, av):
            eval_ok = True
            break
            
    embed_ok = False
    for av in available_names:
        if _model_matches(embedding_model, av):
            embed_ok = True
            break
            
    if not eval_ok:
        errors.append(f"Missing evaluator model '{model}' in Ollama. Pull hint: `ollama pull {model}`")
    if not embed_ok:
        errors.append(f"Missing embedding model '{embedding_model}' in Ollama. Pull hint: `ollama pull {embedding_model}`")
        
    if errors:
        return False, errors
    return True, []


def build_ragas_evaluator(provider: str, model: str | None, embedding_model: str | None, base_url: str | None):
    if provider == "openai":
        return None, None
        
    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama, OllamaEmbeddings
        except ImportError as e:
            raise ImportError(f"Required langchain-ollama package is not installed: {e}")
            
        try:
            from ragas.llms import LangchainLLMWrapper
        except ImportError:
            LangchainLLMWrapper = None

        try:
            from ragas.embeddings import LangchainEmbeddingsWrapper
        except ImportError:
            LangchainEmbeddingsWrapper = None
            
        try:
            chat_model = ChatOllama(
                model=model or DEFAULT_OLLAMA_EVALUATOR_MODEL,
                base_url=base_url or DEFAULT_OLLAMA_BASE_URL,
                temperature=0,
            )

            embedding_model_obj = OllamaEmbeddings(
                model=embedding_model or DEFAULT_OLLAMA_EMBEDDING_MODEL,
                base_url=base_url or DEFAULT_OLLAMA_BASE_URL,
            )

            if LangchainLLMWrapper:
                llm = LangchainLLMWrapper(chat_model)
            else:
                llm = chat_model

            if LangchainEmbeddingsWrapper:
                embeddings = LangchainEmbeddingsWrapper(embedding_model_obj)
            else:
                embeddings = embedding_model_obj

            return llm, embeddings
        except Exception as e:
            raise RuntimeError(f"Failed to build Ollama evaluator/embeddings wrapper: {e}")
            
    raise ValueError(f"Unsupported provider: {provider}")


def load_answer_traces(path: str | Path, limit: int | None = None) -> tuple[list[dict], list[str], list[dict]]:
    """
    Loads answer traces from a JSONL file.
    Validates that each trace contains required fields (question, answer, contexts).
    Filters out invalid/blank lines and collects validation errors.
    Returns:
        tuple: (valid_traces, errors, skipped_traces)
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    valid_traces = []
    errors = []
    skipped_traces = []

    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                trace = json.loads(line)
            except Exception as e:
                errors.append(f"Line {line_num}: Invalid JSON: {e}")
                continue

            # Check required fields
            ragas_data = trace.get("ragas", {})
            q = ragas_data.get("question") or trace.get("question")
            a = ragas_data.get("answer") or trace.get("answer")

            # Check contexts
            contexts = ragas_data.get("contexts")
            if not contexts and "retrieved_contexts" in trace:
                contexts = [ctx.get("content", "") for ctx in trace["retrieved_contexts"] if ctx.get("content")]

            if not q or not a or not contexts:
                reasons = []
                if not q:
                    reasons.append("missing question")
                if not a:
                    reasons.append("missing answer")
                if not contexts:
                    reasons.append("empty contexts")
                
                skipped_traces.append({
                    "trace_id": trace.get("trace_id", f"line_{line_num}"),
                    "reason": ", ".join(reasons),
                    "trace": trace
                })
                continue

            valid_traces.append(trace)

    # Limit to latest N traces (last N items in append-only JSONL)
    if limit is not None and limit > 0:
        valid_traces = valid_traces[-limit:]

    return valid_traces, errors, skipped_traces


def trace_to_ragas_sample(trace: dict) -> dict:
    """
    Converts a raw trace into a normalized RAGAS-ready sample format.
    """
    ragas_data = trace.get("ragas", {})
    q = ragas_data.get("question") or trace.get("question") or ""
    a = ragas_data.get("answer") or trace.get("answer") or ""

    contexts = ragas_data.get("contexts")
    if not contexts and "retrieved_contexts" in trace:
        contexts = [ctx.get("content", "") for ctx in trace["retrieved_contexts"] if ctx.get("content")]
    if contexts is None:
        contexts = []

    gt = ragas_data.get("ground_truth")

    return {
        "trace_id": trace.get("trace_id"),
        "question": q,
        "answer": a,
        "contexts": contexts,
        "ground_truth": gt,
    }


def compute_diagnostics(sample: dict, trace: dict) -> dict:
    """
    Computes deterministic fallback/diagnostic metrics on a sample.
    """
    answer = sample["answer"]
    contexts = sample["contexts"]

    # Check for file path/citation pattern like backend/foo.py or foo.md
    has_citation = bool(re.search(r'(\w+/)+\w+\.\w+', answer)) or ".py" in answer or "backend/" in answer

    first_path = None
    if "retrieved_contexts" in trace and trace["retrieved_contexts"]:
        first_path = trace["retrieved_contexts"][0].get("relative_path")

    mentions_top = False
    if first_path:
        basename = Path(first_path).name
        mentions_top = (first_path in answer) or (basename in answer)

    return {
        "answer_length_chars": len(answer),
        "context_count": len(contexts),
        "total_context_chars": sum(len(c) for c in contexts),
        "answer_has_citation_like_path": has_citation,
        "answer_mentions_top_context_file": mentions_top,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation on answer traces.")
    parser.add_argument(
        "--input",
        type=str,
        default="evals/reports/answer_traces.jsonl",
        help="Path to the JSONL traces file.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="evals/reports/ragas_latest.json",
        help="Path to write the output JSON report.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the latest N traces.",
    )
    parser.add_argument(
        "--require-ground-truth",
        action="store_true",
        help="Skip traces without ground truth.",
    )
    parser.add_argument(
        "--allow-no-ground-truth",
        action="store_true",
        help="Run only metrics that do not require ground truth.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate trace loading and RAGAS readiness without calling RAGAS evaluator.",
    )
    parser.add_argument(
        "--evaluator-provider",
        type=str,
        default=None,
        help="Evaluator provider: 'openai' or 'ollama'."
    )
    parser.add_argument(
        "--evaluator-model",
        type=str,
        default=None,
        help="Evaluator model name (e.g. qwen2.5-coder:3b)."
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default=None,
        help="Embedding model name (e.g. nomic-embed-text)."
    )
    parser.add_argument(
        "--ollama-base-url",
        type=str,
        default=None,
        help="Ollama base URL (e.g. http://localhost:11434)."
    )
    parser.add_argument(
        "--check-evaluator-health",
        action="store_true",
        help="Perform health check on evaluator/models (required for dry-run validation)."
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    errors = []
    valid_traces = []
    skipped_traces = []

    # Resolve values with priority: args -> env -> defaults
    evaluator_provider = (
        args.evaluator_provider
        or os.environ.get("RAGAS_EVALUATOR_PROVIDER")
        or "openai"
    ).lower()

    default_eval_model = "qwen2.5-coder:3b" if evaluator_provider == "ollama" else None
    default_embed_model = "nomic-embed-text" if evaluator_provider == "ollama" else None

    evaluator_model = (
        args.evaluator_model
        or os.environ.get("RAGAS_EVALUATOR_MODEL")
        or default_eval_model
    )

    embedding_model = (
        args.embedding_model
        or os.environ.get("RAGAS_EMBEDDING_MODEL")
        or default_embed_model
    )

    ollama_base_url = (
        args.ollama_base_url
        or os.environ.get("OLLAMA_BASE_URL")
        or "http://localhost:11434"
    )

    evaluator_config = {
        "provider": evaluator_provider,
        "model": evaluator_model,
        "embedding_model": embedding_model,
        "base_url": ollama_base_url
    }

    import datasets
    import ragas
    try:
        import langchain_ollama
        langchain_ollama_available = True
    except ImportError:
        langchain_ollama_available = False

    runtime_info = {
        "ragas_version": getattr(ragas, "__version__", "unknown") if RAGAS_AVAILABLE else "unknown",
        "datasets_version": getattr(datasets, "__version__", "unknown") if RAGAS_AVAILABLE else "unknown",
        "langchain_ollama_available": langchain_ollama_available,
    }

    # Validate provider name
    if evaluator_provider not in ("openai", "ollama"):
        report = {
            "status": "ERROR",
            "schema_version": "ragas_eval.v1",
            "input_path": str(input_path),
            "output_path": str(output_path),
            "total_traces_loaded": 0,
            "total_traces_evaluated": 0,
            "total_traces_skipped": 0,
            "metrics_requested": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
            "metrics_run": [],
            "metrics_skipped": {},
            "evaluator": evaluator_config,
            "runtime": runtime_info,
            "score_health": {
                "numeric_score_count": 0,
                "null_score_count": 0,
                "metrics_with_numeric_scores": [],
                "metrics_with_null_scores": []
            },
            "summary": {},
            "traces": [],
            "errors": [f"Invalid evaluator provider: {evaluator_provider}. Supported providers: openai, ollama"],
        }
        with output_path.open("w", encoding="utf-8") as out_f:
            json.dump(report, out_f, indent=2)
        print(f"Error: Invalid evaluator provider: {evaluator_provider}", file=sys.stderr)
        sys.exit(1)

    # 1. Load Traces
    try:
        valid_traces, load_errors, load_skipped = load_answer_traces(input_path, limit=args.limit)
        errors.extend(load_errors)
        skipped_traces.extend(load_skipped)
    except Exception as e:
        # Generate initial/fallback error report if we can't read files at all
        report = {
            "status": "ERROR",
            "schema_version": "ragas_eval.v1",
            "input_path": str(input_path),
            "output_path": str(output_path),
            "total_traces_loaded": 0,
            "total_traces_evaluated": 0,
            "total_traces_skipped": 0,
            "metrics_requested": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
            "metrics_run": [],
            "metrics_skipped": {},
            "evaluator": evaluator_config,
            "runtime": runtime_info,
            "score_health": {
                "numeric_score_count": 0,
                "null_score_count": 0,
                "metrics_with_numeric_scores": [],
                "metrics_with_null_scores": []
            },
            "summary": {},
            "traces": [],
            "errors": [f"Failed to read/load trace file: {e}"],
        }
        with output_path.open("w", encoding="utf-8") as out_f:
            json.dump(report, out_f, indent=2)
        print(f"Error loading trace file: {e}", file=sys.stderr)
        sys.exit(1)

    total_traces_loaded = len(valid_traces) + len(skipped_traces)
    total_traces_skipped = len(skipped_traces)

    # 2. Convert to RAGAS samples & calculate diagnostics
    samples = []
    trace_diagnostics = {}
    
    for trace in valid_traces:
        sample = trace_to_ragas_sample(trace)
        trace_id = sample["trace_id"]
        
        # Apply filter: --require-ground-truth
        if args.require_ground_truth and not sample.get("ground_truth"):
            skipped_traces.append({
                "trace_id": trace_id,
                "reason": "ground_truth_missing_required",
                "trace": trace
            })
            total_traces_skipped += 1
            continue

        samples.append(sample)
        trace_diagnostics[trace_id] = compute_diagnostics(sample, trace)

    # 3. Determine metrics to run per sample
    requested_metrics = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    metrics_run_set = set()
    metrics_skipped = {}

    traces_report_data = []

    # Check dependencies if not dry run
    if not args.dry_run and not RAGAS_AVAILABLE:
        report = {
            "status": "ERROR",
            "schema_version": "ragas_eval.v1",
            "input_path": str(input_path),
            "output_path": str(output_path),
            "total_traces_loaded": total_traces_loaded,
            "total_traces_evaluated": 0,
            "total_traces_skipped": total_traces_skipped,
            "metrics_requested": requested_metrics,
            "metrics_run": [],
            "metrics_skipped": {"all": "ragas_package_missing"},
            "evaluator": evaluator_config,
            "runtime": runtime_info,
            "score_health": {
                "numeric_score_count": 0,
                "null_score_count": 0,
                "metrics_with_numeric_scores": [],
                "metrics_with_null_scores": []
            },
            "summary": {},
            "traces": [],
            "errors": ["Ragas or datasets packages are not installed. Install hint: pip install ragas datasets"],
        }
        with output_path.open("w", encoding="utf-8") as out_f:
            json.dump(report, out_f, indent=2)
        print("Error: ragas or datasets package is missing.", file=sys.stderr)
        print("Install using: pip install ragas datasets", file=sys.stderr)
        sys.exit(1)

    # Evaluate each trace's metric needs
    for sample in samples:
        trace_id = sample["trace_id"]
        scores = {}
        sample_skipped_metrics = {}

        for m in requested_metrics:
            if m == "context_recall":
                if args.allow_no_ground_truth:
                    sample_skipped_metrics[m] = "ground_truth_missing"
                    metrics_skipped[m] = "ground_truth_missing"
                    scores[m] = None
                elif not sample.get("ground_truth"):
                    sample_skipped_metrics[m] = "ground_truth_missing"
                    metrics_skipped[m] = "ground_truth_missing"
                    scores[m] = None
                else:
                    metrics_run_set.add(m)
                    scores[m] = 0.0 # Will be populated if ran
            else:
                metrics_run_set.add(m)
                scores[m] = 0.0

        traces_report_data.append({
            "trace_id": trace_id,
            "question": sample["question"],
            "answer_preview": sample["answer"][:100] + "..." if len(sample["answer"]) > 100 else sample["answer"],
            "context_count": len(sample["contexts"]),
            "ground_truth_present": bool(sample.get("ground_truth")),
            "scores": scores,
            "skipped_metrics": sample_skipped_metrics,
            "diagnostics": trace_diagnostics[trace_id]
        })

    metrics_run = sorted(list(metrics_run_set))

    # 4. Perform actual RAGAS evaluation or Dry Run
    status = "PASS"
    summary_scores = {m: None for m in requested_metrics}

    # Check health first
    health_ok = True
    if evaluator_provider == "ollama":
        if not args.dry_run or args.check_evaluator_health:
            ok, health_errors = check_ollama_health(
                base_url=ollama_base_url,
                model=evaluator_model,
                embedding_model=embedding_model
            )
            if not ok:
                health_ok = False
                status = "ERROR"
                for he in health_errors:
                    errors.append({
                        "type": "OLLAMA_HEALTH_CHECK_FAILED",
                        "message": he
                    })
    elif evaluator_provider == "openai":
        if not args.dry_run or args.check_evaluator_health:
            if not os.getenv("OPENAI_API_KEY"):
                health_ok = False
                status = "ERROR"
                errors.append({
                    "type": "OPENAI_API_KEY_MISSING",
                    "message": "No OPENAI_API_KEY found in the environment. OpenAI evaluator requires a configured API key."
                })

    if not health_ok:
        # Bypassed due to health check failure
        for tr in traces_report_data:
            for m in requested_metrics:
                tr["scores"][m] = None
    elif args.dry_run:
        status = "DRY_RUN_PASS"
        # In dry run, scores are set to null
        for tr in traces_report_data:
            for m in requested_metrics:
                tr["scores"][m] = None
    else:
        # Run real evaluation
        try:
            llm, embeddings = build_ragas_evaluator(
                provider=evaluator_provider,
                model=evaluator_model,
                embedding_model=embedding_model,
                base_url=ollama_base_url
            )

            # Build dataset and evaluate
            data_dict = {
                "question": [s["question"] for s in samples],
                "answer": [s["answer"] for s in samples],
                "contexts": [s["contexts"] for s in samples],
                "ground_truth": [s["ground_truth"] or "" for s in samples],
            }
            dataset = Dataset.from_dict(data_dict)

            # Map metrics
            from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
            metric_map = {
                "faithfulness": faithfulness,
                "answer_relevancy": answer_relevancy,
                "context_precision": context_precision,
                "context_recall": context_recall,
            }
            ragas_metrics = [metric_map[m] for m in metrics_run if m in metric_map]

            try:
                result = evaluate(
                    dataset=dataset,
                    metrics=ragas_metrics,
                    llm=llm,
                    embeddings=embeddings
                )
            except TypeError as te:
                if evaluator_provider == "ollama":
                    raise RuntimeError(f"RAGAS_VERSION_DOES_NOT_SUPPORT_CUSTOM_EVALUATOR_ARGS: {te}")
                else:
                    result = evaluate(dataset=dataset, metrics=ragas_metrics)

            df = result.to_pandas()

            # Update scores in report traces
            for i, tr in enumerate(traces_report_data):
                for m in requested_metrics:
                    if m in metrics_run and m in df.columns:
                        val = df.iloc[i][m]
                        import numpy as np
                        if val is not None and not (isinstance(val, float) and np.isnan(val)):
                            tr["scores"][m] = round(float(val), 4)
                        else:
                            tr["scores"][m] = None
                            errors.append({
                                "type": "METRIC_EVALUATION_NaN",
                                "message": f"Metric '{m}' returned NaN or null for trace '{tr['trace_id']}'"
                            })
                    else:
                        tr["scores"][m] = None

            # Calculate summary/average scores
            for m in metrics_run:
                vals = [tr["scores"][m] for tr in traces_report_data if _is_number(tr["scores"][m])]
                if vals:
                    summary_scores[m] = round(sum(vals) / len(vals), 4)

            # Classify status
            numeric_score_count = _count_numeric_scores(traces_report_data)
            total_traces_evaluated = len(samples)

            has_metric_failures = False
            for tr in traces_report_data:
                for m in metrics_run:
                    if not _is_number(tr["scores"].get(m)):
                        has_metric_failures = True
                        break

            if total_traces_evaluated == 0:
                status = "ERROR"
            elif numeric_score_count == 0:
                status = "ERROR"
                errors.append({
                    "type": "NO_NUMERIC_RAGAS_SCORES",
                    "message": "RAGAS execution completed but returned no numeric metric scores. Check evaluator API key, provider config, or runtime errors."
                })
            elif has_metric_failures:
                status = "PARTIAL"
            else:
                status = "PASS"

        except Exception as e:
            status = "ERROR"
            err_msg = str(e)
            # Make sure scores are null when error happens
            for tr in traces_report_data:
                for m in requested_metrics:
                    tr["scores"][m] = None

            if "RAGAS_VERSION_DOES_NOT_SUPPORT_CUSTOM_EVALUATOR_ARGS" in err_msg:
                errors.append({
                    "type": "RAGAS_VERSION_DOES_NOT_SUPPORT_CUSTOM_EVALUATOR_ARGS",
                    "message": err_msg
                })
            else:
                errors.append({
                    "type": "RAGAS_RUNTIME_EXCEPTION",
                    "message": f"Ragas execution error: {e}"
                })

    # Calculate diagnostics summary/average
    diag_summary = {}
    if traces_report_data:
        lengths = [tr["diagnostics"]["answer_length_chars"] for tr in traces_report_data]
        counts = [tr["diagnostics"]["context_count"] for tr in traces_report_data]
        chars = [tr["diagnostics"]["total_context_chars"] for tr in traces_report_data]
        has_cit = [1 if tr["diagnostics"]["answer_has_citation_like_path"] else 0 for tr in traces_report_data]
        mentions = [1 if tr["diagnostics"]["answer_mentions_top_context_file"] else 0 for tr in traces_report_data]

        diag_summary = {
            "answer_length_chars_avg": round(sum(lengths) / len(lengths), 2) if lengths else 0,
            "context_count_avg": round(sum(counts) / len(counts), 2) if counts else 0,
            "total_context_chars_avg": round(sum(chars) / len(chars), 2) if chars else 0,
            "answer_has_citation_like_path_rate": round(sum(has_cit) / len(has_cit), 2) if has_cit else 0,
            "answer_mentions_top_context_file_rate": round(sum(mentions) / len(mentions), 2) if mentions else 0,
        }

    # Calculate score_health
    numeric_score_count = _count_numeric_scores(traces_report_data)
    null_score_count = 0
    metrics_with_numeric_scores = []
    metrics_with_null_scores = []
    for m in requested_metrics:
        m_num = sum(1 for tr in traces_report_data if _is_number(tr["scores"].get(m)))
        m_null = sum(1 for tr in traces_report_data if not _is_number(tr["scores"].get(m)) and m not in tr.get("skipped_metrics", {}))
        null_score_count += m_null
        if m_num > 0:
            metrics_with_numeric_scores.append(m)
        if m_null > 0:
            metrics_with_null_scores.append(m)

    score_health = {
        "numeric_score_count": numeric_score_count,
        "null_score_count": null_score_count,
        "metrics_with_numeric_scores": sorted(metrics_with_numeric_scores),
        "metrics_with_null_scores": sorted(metrics_with_null_scores),
    }
    summary_scores["score_health"] = score_health

    # 5. Write JSON report
    report = {
        "status": status,
        "schema_version": "ragas_eval.v1",
        "input_path": str(input_path),
        "output_path": str(output_path),
        "total_traces_loaded": total_traces_loaded,
        "total_traces_evaluated": len(samples),
        "total_traces_skipped": total_traces_skipped,
        "metrics_requested": requested_metrics,
        "metrics_run": metrics_run,
        "metrics_skipped": metrics_skipped,
        "evaluator": evaluator_config,
        "runtime": runtime_info,
        "score_health": score_health,
        "summary": summary_scores,
        "diagnostics": diag_summary,
        "traces": traces_report_data,
        "errors": errors,
    }

    with output_path.open("w", encoding="utf-8") as out_f:
        json.dump(report, out_f, indent=2)

    # Print summary to stdout
    print("========================================")
    print("         RAGAS EVALUATION REPORT")
    print("========================================")
    print(f"Status:                      {status}")
    print(f"Total Traces Loaded:         {total_traces_loaded}")
    print(f"Total Traces Evaluated:      {len(samples)}")
    print(f"Total Traces Skipped:        {total_traces_skipped}")
    print(f"Metrics Planned/Run:         {', '.join(metrics_run)}")
    if errors:
        print(f"Errors encountered:          {len(errors)}")
        for err in errors[:3]:
            if isinstance(err, dict):
                print(f"  - [{err.get('type')}] {err.get('message')}")
            else:
                print(f"  - {err}")
    print("========================================")
    if status == "DRY_RUN_PASS":
        print("Dry run completed successfully.")
    elif status == "PASS":
        print("Evaluation summary:")
        for m, score in summary_scores.items():
            if score is not None:
                print(f"  - {m}: {score}")
    print(f"Report written to: {output_path}")


if __name__ == "__main__":
    main()
