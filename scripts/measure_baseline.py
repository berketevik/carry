#!/usr/bin/env python3
"""Baseline measurements on the frozen synthetic corpus.

Reports cold and warm numbers per provider so retrieval and latency budgets can
be set from data instead of from expectation. Cold means a fresh process and a
freshly built index; warm means the same process querying a published index.

Usage: python3 scripts/measure_baseline.py [--provider ollama|hashing] [--repeat 5]
"""
import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carry import index as index_module          # noqa: E402
from carry.config import EmbeddingConfig, SourceConfig, Workspace  # noqa: E402
from carry.fixtures import install_fixture       # noqa: E402
from carry.recall import recall                  # noqa: E402

QUERIES = [
    "When is the Cedar pilot delivery date?",
    "What does Cedar keep canonical?",
    "How does a Cedar correction handle history?",
    "What is the Cedar pilot budget?",
]


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[position]


def measure(provider, repeat):
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        notes = install_fixture(base / "corpus", exist_ok=True, include_readme=False)
        workspace = Workspace.create(base / "state",
                                     sources=[SourceConfig("cedar", notes)],
                                     embedding=EmbeddingConfig(provider=provider))
        started = time.monotonic()
        build = index_module.build(workspace)
        build_seconds = time.monotonic() - started
        if build["status"] != "built":
            return {"provider": provider, "error": build}

        cold, cold_wall = [], []
        for query in QUERIES:
            launched = time.monotonic()
            process = subprocess.run(
                [sys.executable, "-m", "carry.cli", "--workspace", str(workspace.state_dir),
                 "--json", "recall", query],
                capture_output=True, text=True,
                env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                     "PATH": "/usr/bin:/bin"})
            # Wall time includes interpreter startup and imports, which is what a
            # client waiting on a one-shot process actually experiences.
            cold_wall.append(round((time.monotonic() - launched) * 1000))
            payload = json.loads(process.stdout or "{}")
            cold.append(payload.get("diagnostics", {}).get("elapsed_ms"))

        warm = []
        for _ in range(repeat):
            for query in QUERIES:
                result = recall(workspace, query)
                warm.append(result["diagnostics"]["elapsed_ms"])
        return {
            "provider": provider,
            "corpus_files": build["files"],
            "chunks": build["chunks"],
            "semantic": build["semantic"],
            "build_seconds": round(build_seconds, 3),
            "incremental_rebuild_seconds": round(_timed_rebuild(workspace), 3),
            "cold_query_ms": {"p50": percentile(cold, 0.5), "p95": percentile(cold, 0.95),
                              "samples": cold},
            "cold_process_wall_ms": {"p50": percentile(cold_wall, 0.5),
                                     "p95": percentile(cold_wall, 0.95),
                                     "samples": cold_wall},
            "warm_query_ms": {"p50": percentile(warm, 0.5), "p95": percentile(warm, 0.95),
                              "mean": round(statistics.mean(warm), 1), "n": len(warm)},
        }


def _timed_rebuild(workspace):
    started = time.monotonic()
    index_module.build(workspace)
    return time.monotonic() - started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", action="append",
                        choices=("ollama", "hashing"), default=None)
    parser.add_argument("--repeat", type=int, default=5)
    arguments = parser.parse_args()
    providers = arguments.provider or ["hashing", "ollama"]
    report = {"python": sys.version.split()[0],
              "measurements": [measure(p, arguments.repeat) for p in providers]}
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
