"""Run retrieval eval across multiple datasets/repos and aggregate metrics."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from retrieval.isolation import expected_collection_name

HIT_RE = re.compile(r"^hit@\d+:\s*([0-9.]+)\s*$")
MRR_RE = re.compile(r"^mrr@\d+:\s*([0-9.]+)\s*$")
COV_RE = re.compile(r"^citation_coverage:\s*([0-9.]+)\s*$")
CASES_RE = re.compile(r"^Cases:\s*(\d+)\s*$")


def _parse_eval_output(text: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if match := HIT_RE.match(line):
            metrics["hit"] = float(match.group(1))
        elif match := MRR_RE.match(line):
            metrics["mrr"] = float(match.group(1))
        elif match := COV_RE.match(line):
            metrics["cov"] = float(match.group(1))
        elif match := CASES_RE.match(line):
            metrics["cases"] = float(match.group(1))
    required = {"hit", "mrr", "cov", "cases"}
    missing = required - set(metrics)
    if missing:
        raise RuntimeError(f"Missing metrics in eval output: {sorted(missing)}")
    return metrics


def _run_dataset(project_root: Path, dataset: dict) -> dict:
    eval_file = str((project_root / dataset["eval_file"]).resolve())
    repo_root = str(Path(dataset["repo_root"]).resolve())
    k = int(dataset.get("k", 10))

    env = dict(os.environ)
    env["PYTHONPATH"] = str(project_root)
    env["QDRANT_COLLECTION_NAME"] = str(
        dataset.get("collection_name", expected_collection_name(repo_root))
    )
    env["RETRIEVAL_REPO_ROOT"] = repo_root

    if dataset.get("ingest_before_eval", False):
        ingest_cmd = [
            str(project_root / ".venv" / "bin" / "python"),
            "-m",
            "rag_ingestion.main",
            repo_root,
        ]
        ingest_proc = subprocess.run(
            ingest_cmd,
            cwd=project_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if ingest_proc.returncode != 0:
            raise RuntimeError(
                f"Ingestion failed for dataset {dataset['id']}\n"
                f"stdout:\n{ingest_proc.stdout}\n\nstderr:\n{ingest_proc.stderr}"
            )

    cmd = [
        str(project_root / ".venv" / "bin" / "python"),
        str(project_root / "scripts" / "retrieval_eval.py"),
        "--eval-file",
        eval_file,
        "--k",
        str(k),
    ]
    proc = subprocess.run(
        cmd,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Eval failed for dataset {dataset['id']}\n"
            f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
        )
    metrics = _parse_eval_output(proc.stdout)
    metrics["id"] = dataset["id"]
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Run multi-repo retrieval eval suite.")
    parser.add_argument(
        "--suite-file",
        default="docs/retrieval_docs/eval_suite_multi_repo.json",
        help="Suite JSON with datasets list",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional path to write suite metrics as JSON.",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    suite = json.loads((project_root / args.suite_file).read_text(encoding="utf-8"))
    datasets = suite.get("datasets", [])
    if not datasets:
        raise SystemExit("No datasets found in suite file.")

    results = []
    for dataset in datasets:
        metrics = _run_dataset(project_root, dataset)
        results.append(metrics)
        print(
            f"{dataset['id']}: cases={int(metrics['cases'])} "
            f"hit@k={metrics['hit']:.3f} mrr@k={metrics['mrr']:.3f} "
            f"citation_coverage={metrics['cov']:.3f}"
        )

    total_cases = sum(r["cases"] for r in results)
    agg_hit = sum(r["hit"] * r["cases"] for r in results) / total_cases
    agg_mrr = sum(r["mrr"] * r["cases"] for r in results) / total_cases
    agg_cov = sum(r["cov"] * r["cases"] for r in results) / total_cases

    print("\nAggregate")
    print("=========")
    print(f"datasets: {len(results)}")
    print(f"cases: {int(total_cases)}")
    print(f"weighted_hit@k: {agg_hit:.3f}")
    print(f"weighted_mrr@k: {agg_mrr:.3f}")
    print(f"weighted_citation_coverage: {agg_cov:.3f}")

    if args.json_out:
        payload = {
            "datasets": results,
            "aggregate": {
                "datasets": len(results),
                "cases": int(total_cases),
                "weighted_hit@k": round(agg_hit, 6),
                "weighted_mrr@k": round(agg_mrr, 6),
                "weighted_citation_coverage": round(agg_cov, 6),
            },
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
