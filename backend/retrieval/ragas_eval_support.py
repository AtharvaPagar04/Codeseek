"""Support helpers for CodeSeek RAGAS-style validation reports.

The runtime environment used for this task does not ship the `ragas` package,
so this module implements a deterministic compatibility layer that produces the
same per-response report shape and metric names. If `ragas` becomes available
later, the CLI can swap the scoring backend without changing the report schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from statistics import mean


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
    "why",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
LOW_CONTEXT_RE = re.compile(
    r"(insufficient context|not enough context|no relevant code|not found in the retrieved context)",
    re.IGNORECASE,
)

METRICS = (
    "context_precision",
    "context_recall",
    "faithfulness",
    "answer_relevancy",
    "answer_correctness",
)


@dataclass(slots=True)
class MetricCell:
    state: str
    value: float | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        payload = {"state": self.state}
        if self.value is not None:
            payload["value"] = round(float(self.value), 4)
        if self.detail:
            payload["detail"] = self.detail
        return payload


def load_fixture(path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("Invalid eval fixture: `cases` must be a list")
    return data, cases


def resolve_repo_root_hint(fixture: dict, default: str) -> str:
    hint = str(fixture.get("repo_root_hint", "")).strip()
    return hint or default


def serialize_source_item(item: dict) -> dict:
    payload = {
        "relative_path": item.get("relative_path", ""),
        "symbol_name": item.get("symbol_name", ""),
        "qualified_symbol": item.get("qualified_symbol", ""),
        "chunk_type": item.get("chunk_type", ""),
        "start_line": int(item.get("start_line", 0)),
        "end_line": int(item.get("end_line", 0)),
        "expansion_type": item.get("expansion_type", ""),
        "score": float(item.get("score", item.get("retrieval_score", 0.0)) or 0.0),
    }
    if item.get("signature"):
        payload["signature"] = item["signature"]
    if item.get("summary"):
        payload["summary"] = item["summary"]
    if item.get("chunk_id"):
        payload["chunk_id"] = item["chunk_id"]
    if item.get("support_kind"):
        payload["support_kind"] = item["support_kind"]
    return payload


def serialize_context_block(item: dict | str) -> dict:
    if isinstance(item, str):
        return {"text": item}
    payload = {
        "text": item.get("text") or item.get("content") or item.get("summary") or "",
        "relative_path": item.get("relative_path", ""),
        "symbol_name": item.get("symbol_name", ""),
        "chunk_type": item.get("chunk_type", ""),
        "start_line": int(item.get("start_line", 0)),
        "end_line": int(item.get("end_line", 0)),
        "expansion_type": item.get("expansion_type", ""),
    }
    if item.get("signature"):
        payload["signature"] = item["signature"]
    if item.get("summary"):
        payload["summary"] = item["summary"]
    if item.get("block_type"):
        payload["block_type"] = item["block_type"]
    if item.get("support_kind"):
        payload["support_kind"] = item["support_kind"]
    return payload


def serialize_metric_bundle(bundle: dict[str, MetricCell]) -> dict:
    return {key: value.to_dict() for key, value in bundle.items()}


def _metric_names() -> tuple[str, ...]:
    return METRICS


def _bucket_entries(entries: list[dict], group_field: str) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}
    for entry in entries:
        bucket = str(entry.get(group_field, "") or "-")
        buckets.setdefault(bucket, []).append(entry)
    return buckets


def _metric_averages(entries: list[dict]) -> dict[str, float]:
    return {metric: average_numeric_metric(entries, metric) for metric in _metric_names()}


def _metric_state_counts(entries: list[dict]) -> dict[str, dict[str, int]]:
    states = ("numeric", "not_applicable", "error")
    counts: dict[str, dict[str, int]] = {}
    for metric in _metric_names():
        counts[metric] = {
            state: sum(1 for entry in entries if entry.get("ragas", {}).get(metric, {}).get("state") == state)
            for state in states
        }
    return counts


def build_report_meta(
    *,
    dataset_name: str,
    repo_root: str,
    collection_name: str,
    case_count: int,
    previous_report: dict | None = None,
) -> dict:
    meta = {
        "dataset_name": dataset_name,
        "repo_root": repo_root,
        "collection_name": collection_name,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "case_count": case_count,
    }
    if previous_report:
        meta["compared_to"] = {
            "dataset_name": previous_report.get("run_meta", {}).get("dataset_name", ""),
            "generated_at_utc": previous_report.get("run_meta", {}).get("generated_at_utc", ""),
        }
    return meta


def tokenize(text: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall(text.lower()):
        parts = [part for part in re.split(r"[/_.:-]+", token) if part]
        if not parts:
            parts = [token]
        for part in parts:
            if len(part) <= 2 or part in STOPWORDS:
                continue
            tokens.append(part)
    return tokens


def token_set(text: str) -> set[str]:
    return set(tokenize(text))


def coverage(haystack: str, needle: str) -> float:
    needle_tokens = token_set(needle)
    if not needle_tokens:
        return 0.0
    haystack_tokens = token_set(haystack)
    return len(needle_tokens & haystack_tokens) / len(needle_tokens)


def similarity(a: str, b: str) -> float:
    a_tokens = token_set(a)
    b_tokens = token_set(b)
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = len(a_tokens & b_tokens)
    precision = intersection / len(a_tokens)
    recall = intersection / len(b_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def source_anchor_matches(block: dict, expected_source: dict) -> bool:
    block_path = str(block.get("relative_path", "")).lower()
    block_symbol = str(block.get("symbol_name", "")).lower()
    expected_path = str(expected_source.get("relative_path", "")).lower()
    expected_symbol = str(expected_source.get("symbol_name", "")).lower()
    if expected_path and expected_path not in block_path and not block_path.endswith(expected_path):
        return False
    if expected_symbol and expected_symbol != block_symbol and expected_symbol not in block_symbol:
        return False
    return True


def extract_block_text(blocks: list[dict | str]) -> str:
    parts = []
    for block in blocks:
        if isinstance(block, str):
            text = block.strip()
        else:
            text = str(block.get("text") or block.get("content") or block.get("summary") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def split_claims(answer: str) -> list[str]:
    if not answer.strip():
        return []
    sentences = []
    for chunk in re.split(r"[\n•]+", answer):
        chunk = chunk.strip(" -\t")
        if not chunk:
            continue
        sentences.extend(part.strip() for part in SENTENCE_RE.split(chunk) if part.strip())
    return [sentence for sentence in sentences if len(sentence) > 8]


def score_context_precision(
    question: str,
    answer_context_blocks: list[dict | str],
    ground_truth: str,
    ground_truth_sources: list[dict],
) -> MetricCell:
    if not answer_context_blocks:
        return MetricCell("numeric", 0.0, "no context blocks")
    scores = []
    for block in answer_context_blocks:
        block_dict = block if isinstance(block, dict) else {"text": str(block)}
        text = str(block_dict.get("text") or block_dict.get("content") or block_dict.get("summary") or "")
        if not text.strip():
            continue
        query_score = coverage(text, question)
        gt_score = coverage(text, ground_truth) if ground_truth else 0.0
        anchor_bonus = 0.0
        if ground_truth_sources:
            for expected in ground_truth_sources:
                if source_anchor_matches(block_dict, expected):
                    anchor_bonus = 0.25
                    break
        score = min(1.0, 0.55 * query_score + 0.35 * gt_score + anchor_bonus)
        scores.append(score)
    if not scores:
        return MetricCell("numeric", 0.0, "empty block texts")
    return MetricCell("numeric", mean(scores), "average block relevance")


def score_context_recall(
    answer_context_blocks: list[dict | str],
    ground_truth: str,
    ground_truth_sources: list[dict],
) -> MetricCell:
    if not answer_context_blocks:
        return MetricCell("numeric", 0.0, "no context blocks")
    block_dicts = [b if isinstance(b, dict) else {"text": str(b)} for b in answer_context_blocks]
    if ground_truth_sources:
        matched = 0
        for expected in ground_truth_sources:
            if any(source_anchor_matches(block, expected) for block in block_dicts):
                matched += 1
        return MetricCell(
            "numeric",
            matched / max(1, len(ground_truth_sources)),
            "ground-truth source anchor coverage",
        )
    if ground_truth:
        context_text = extract_block_text(answer_context_blocks)
        return MetricCell("numeric", coverage(context_text, ground_truth), "ground-truth text coverage")
    return MetricCell("not_applicable", None, "missing ground truth")


def score_faithfulness(
    answer: str,
    answer_context_blocks: list[dict | str],
    response_mode: str,
) -> MetricCell:
    if not answer.strip():
        return MetricCell("numeric", 0.0, "empty answer")
    if response_mode == "low_context" and LOW_CONTEXT_RE.search(answer):
        return MetricCell("numeric", 1.0, "low-context fallback")
    context_text = extract_block_text(answer_context_blocks)
    if not context_text.strip():
        return MetricCell("numeric", 0.0, "no answer context")
    claims = split_claims(answer)
    if not claims:
        return MetricCell("numeric", 0.0, "no claims extracted")
    scores = []
    for claim in claims:
        claim_score = similarity(claim, context_text)
        if claim_score == 0.0:
            claim_score = coverage(context_text, claim) * 0.85
        scores.append(min(1.0, max(0.0, claim_score)))
    return MetricCell("numeric", mean(scores), "claim coverage against context")


def score_answer_relevancy(question: str, answer: str) -> MetricCell:
    if not answer.strip():
        return MetricCell("numeric", 0.0, "empty answer")
    q_to_a = coverage(answer, question)
    a_to_q = coverage(question, answer)
    score = 0.45 * q_to_a + 0.55 * a_to_q
    if LOW_CONTEXT_RE.search(answer):
        score = max(score, 0.7)
    return MetricCell("numeric", min(1.0, score), "question-answer overlap")


def score_answer_correctness(answer: str, ground_truth: str) -> MetricCell:
    if not ground_truth.strip():
        return MetricCell("not_applicable", None, "missing ground truth")
    if not answer.strip():
        return MetricCell("numeric", 0.0, "empty answer")
    return MetricCell("numeric", similarity(answer, ground_truth), "answer-ground-truth similarity")


def compute_metric_bundle(
    *,
    question: str,
    answer: str,
    answer_context_blocks: list[dict | str],
    ground_truth: str,
    ground_truth_sources: list[dict],
    response_mode: str,
) -> dict[str, MetricCell]:
    return {
        "context_precision": score_context_precision(question, answer_context_blocks, ground_truth, ground_truth_sources),
        "context_recall": score_context_recall(answer_context_blocks, ground_truth, ground_truth_sources),
        "faithfulness": score_faithfulness(answer, answer_context_blocks, response_mode),
        "answer_relevancy": score_answer_relevancy(question, answer),
        "answer_correctness": score_answer_correctness(answer, ground_truth),
    }


def expected_source_hit(
    candidates: list[dict],
    expected_sources: list[dict],
) -> float:
    if not expected_sources:
        return 1.0
    matched = 0
    for expected in expected_sources:
        if any(source_anchor_matches(candidate, expected) for candidate in candidates):
            matched += 1
    return matched / max(1, len(expected_sources))


def infer_failure_stage_hint(
    *,
    query: str,
    response_mode: str,
    expected_response_mode: str,
    search_candidates: list[dict],
    expanded_candidates: list[dict],
    assembled_sources: list[dict],
    display_sources: list[dict],
    reasoning_sources: list[dict],
    ground_truth_sources: list[dict],
    metric_bundle: dict[str, MetricCell],
) -> str:
    recall = metric_bundle["context_recall"].value or 0.0
    precision = metric_bundle["context_precision"].value or 0.0
    faithfulness = metric_bundle["faithfulness"].value or 0.0
    relevancy = metric_bundle["answer_relevancy"].value or 0.0
    correctness = metric_bundle["answer_correctness"].value or 0.0

    if expected_response_mode and response_mode != expected_response_mode and relevancy < 0.75:
        return "response_mode_selection"
    if not assembled_sources and not display_sources:
        return "search"
    search_hit = expected_source_hit(search_candidates, ground_truth_sources)
    expanded_hit = expected_source_hit(expanded_candidates, ground_truth_sources)
    assembled_hit = expected_source_hit(assembled_sources, ground_truth_sources)
    display_hit = expected_source_hit(display_sources, ground_truth_sources)
    reasoning_hit = expected_source_hit(reasoning_sources, ground_truth_sources)

    if search_hit < 0.35:
        return "search"
    if search_hit >= 0.35 and assembled_hit < 0.35 and expanded_hit >= 0.35:
        return "assemble"
    if assembled_hit >= 0.35 and display_hit < 0.35 and response_mode != "llm":
        return "source_filter"
    if recall < 0.4 and assembled_hit < 0.5:
        return "search"
    if precision < 0.45 and recall >= 0.6:
        return "expand"
    if recall >= 0.6 and precision >= 0.6 and faithfulness < 0.8:
        return "answer_generation"
    if recall >= 0.6 and precision >= 0.6 and relevancy < 0.75:
        return "response_mode_selection"
    if recall >= 0.7 and precision >= 0.7 and correctness < 0.7 and reasoning_hit >= 0.5:
        return "ground_truth_gap"
    if query.strip().lower() and response_mode == "low_context":
        return "query_understanding"
    return "none"


def average_numeric_metric(entries: list[dict], metric: str) -> float:
    values = []
    for entry in entries:
        cell = entry.get("ragas", {}).get(metric, {})
        if cell.get("state") == "numeric" and isinstance(cell.get("value"), (int, float)):
            values.append(float(cell["value"]))
    return round(mean(values), 4) if values else 0.0


def summarize_entries(entries: list[dict]) -> dict:
    summary = {
        "case_count": len(entries),
        "metric_averages": _metric_averages(entries),
        "warning_counts": {},
        "state_counts": {},
        "by_response_mode": {},
        "by_primary_intent": {},
    }
    warning_threshold = 0.7
    for metric in _metric_names():
        summary["warning_counts"][metric] = sum(
            1
            for entry in entries
            if entry.get("ragas", {}).get(metric, {}).get("state") == "numeric"
            and float(entry.get("ragas", {}).get(metric, {}).get("value", 0.0) or 0.0) < warning_threshold
        )
    summary["state_counts"] = _metric_state_counts(entries)
    for key, group_field in (("by_response_mode", "response_mode"), ("by_primary_intent", "primary_intent")):
        buckets = _bucket_entries(entries, group_field)
        summary[key] = {
            bucket: {
                "count": len(items),
                "metric_averages": _metric_averages(items),
                "warning_counts": {
                    metric: sum(
                        1
                        for entry in items
                        if entry.get("ragas", {}).get(metric, {}).get("state") == "numeric"
                        and float(entry.get("ragas", {}).get(metric, {}).get("value", 0.0) or 0.0) < warning_threshold
                    )
                    for metric in _metric_names()
                },
                "state_counts": _metric_state_counts(items),
            }
            for bucket, items in buckets.items()
        }
    return summary


def build_family_baseline_snapshot(report: dict) -> dict:
    responses = report.get("responses", [])
    summary = summarize_entries(responses)
    return {
        "source_report": {
            "dataset_name": report.get("run_meta", {}).get("dataset_name", ""),
            "generated_at_utc": report.get("run_meta", {}).get("generated_at_utc", ""),
            "case_count": report.get("run_meta", {}).get("case_count", len(responses)),
        },
        "families": {
            "primary_intent": summary.get("by_primary_intent", {}),
            "response_mode": summary.get("by_response_mode", {}),
        },
    }


def compare_family_baselines(current_report: dict, previous_baseline: dict) -> dict:
    current_snapshot = build_family_baseline_snapshot(current_report)
    current_families = current_snapshot.get("families", {})
    previous_families = previous_baseline.get("families", {}) if isinstance(previous_baseline, dict) else {}
    comparison: dict[str, dict[str, dict]] = {}

    for family_field in ("primary_intent", "response_mode"):
        current_groups = current_families.get(family_field, {}) or {}
        previous_groups = previous_families.get(family_field, {}) or {}
        family_delta: dict[str, dict] = {}
        for bucket in sorted(set(current_groups) | set(previous_groups)):
            current_group = current_groups.get(bucket, {})
            previous_group = previous_groups.get(bucket, {})
            current_metrics = current_group.get("metric_averages", {})
            previous_metrics = previous_group.get("metric_averages", {})
            family_delta[bucket] = {
                "current_count": int(current_group.get("count", 0) or 0),
                "previous_count": int(previous_group.get("count", 0) or 0),
                "metric_deltas": {
                    metric: round(float(current_metrics.get(metric, 0.0) or 0.0) - float(previous_metrics.get(metric, 0.0) or 0.0), 4)
                    for metric in _metric_names()
                },
            }
        comparison[family_field] = family_delta

    return {
        "current": current_snapshot.get("source_report", {}),
        "previous": previous_baseline.get("source_report", {}) if isinstance(previous_baseline, dict) else {},
        "families": comparison,
    }


def top_low_scores(entries: list[dict], metric: str, limit: int = 5) -> list[dict]:
    ranked = []
    for entry in entries:
        cell = entry.get("ragas", {}).get(metric, {})
        if cell.get("state") != "numeric":
            continue
        ranked.append(
            {
                "case_id": entry.get("case_id", ""),
                "query": entry.get("query", ""),
                "response_mode": entry.get("response_mode", ""),
                "value": float(cell.get("value", 0.0)),
                "failure_stage_hint": entry.get("failure_stage_hint", "none"),
            }
        )
    ranked.sort(key=lambda item: (item["value"], item["case_id"]))
    return ranked[:limit]


def render_markdown_report(report: dict) -> str:
    run_meta = report.get("run_meta", {})
    summary = report.get("summary", {})
    responses = report.get("responses", [])
    lines = [
        "# CodeSeek RAGAS Validation Report",
        "",
        f"- Dataset: `{run_meta.get('dataset_name', '-')}`",
        f"- Repo root: `{run_meta.get('repo_root', '-')}`",
        f"- Collection: `{run_meta.get('collection_name', '-')}`",
        f"- Generated: `{run_meta.get('generated_at_utc', '-')}`",
        f"- Cases: `{run_meta.get('case_count', len(responses))}`",
        "",
        "## Summary",
        "",
        "| Metric | Average |",
        "|---|---:|",
    ]
    for metric, value in summary.get("metric_averages", {}).items():
        lines.append(f"| `{metric}` | `{float(value):.4f}` |")

    lines.extend(["", "## Lowest Scores", ""])
    for metric in ("context_precision", "context_recall", "faithfulness", "answer_relevancy", "answer_correctness"):
        lines.append(f"### `{metric}`")
        lines.append("")
        lines.append("| Case | Query | Mode | Score | Failure Stage |")
        lines.append("|---|---|---|---:|---|")
        for item in top_low_scores(responses, metric):
            lines.append(
                f"| `{item['case_id']}` | {item['query']} | `{item['response_mode']}` | `{item['value']:.4f}` | `{item['failure_stage_hint']}` |"
            )
        lines.append("")

    lines.append("## Per-Response Details")
    lines.append("")
    for entry in responses:
        lines.append(f"### `{entry.get('case_id', '-')}`")
        lines.append("")
        lines.append(f"- Query: {entry.get('query', '-')}")
        lines.append(f"- Response mode: `{entry.get('response_mode', '-')}`")
        lines.append(f"- Failure stage: `{entry.get('failure_stage_hint', '-')}`")
        lines.append(f"- Ground truth source count: `{len(entry.get('ground_truth_sources', []))}`")
        lines.append("")
        lines.append("| Metric | Value | State |")
        lines.append("|---|---:|---|")
        for metric, cell in entry.get("ragas", {}).items():
            value = cell.get("value", "")
            if isinstance(value, float):
                value_str = f"{value:.4f}"
            elif isinstance(value, int):
                value_str = str(value)
            else:
                value_str = "-"
            lines.append(f"| `{metric}` | `{value_str}` | `{cell.get('state', '-')}` |")
        lines.append("")
    return "\n".join(lines)


def compare_reports(current: dict, previous: dict) -> dict:
    current_summary = current.get("summary", {})
    previous_summary = previous.get("summary", {})
    current_metrics = current_summary.get("metric_averages", {})
    previous_metrics = previous_summary.get("metric_averages", {})
    deltas = {}
    for metric, value in current_metrics.items():
        prev = float(previous_metrics.get(metric, 0.0) or 0.0)
        deltas[metric] = round(float(value) - prev, 4)
    return {
        "current": {
            "dataset_name": current.get("run_meta", {}).get("dataset_name", ""),
            "generated_at_utc": current.get("run_meta", {}).get("generated_at_utc", ""),
        },
        "previous": {
            "dataset_name": previous.get("run_meta", {}).get("dataset_name", ""),
            "generated_at_utc": previous.get("run_meta", {}).get("generated_at_utc", ""),
        },
        "metric_deltas": deltas,
    }
