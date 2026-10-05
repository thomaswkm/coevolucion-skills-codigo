"""Descriptive RQ1/RQ2 analysis from the processed activity counts."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .extract import (
    Config,
    file_sha256,
    git_version,
    load_config,
    serializable_config,
    utc_now,
    write_csv_atomic,
)


ANALYSIS_PROTOCOL_VERSION = "1"
QUANTILE_METHOD = "linear interpolation: rank = q * (n - 1)"
PERIODS = ("pre", "post")
RQ1_CATEGORIES = ("skill", "production", "test")
RQ2_CATEGORIES = ("production", "test")
ALL_CATEGORIES = ("skill", "production", "test", "other")

COUNT_FIELDS = ("repo_full_name", "period", "category", "unique_commit_count", "data_status")
RQ1_COUNT_FIELDS = (
    "repo_full_name",
    "skill_commits",
    "production_commits",
    "test_commits",
    "largest_count",
    "largest_categories",
    "tie",
)
RQ1_SUMMARY_FIELDS = (
    "category",
    "n_repositories",
    "zero_count",
    "median",
    "q1",
    "q3",
    "iqr",
    "minimum",
    "maximum",
    "mean",
    "total",
    "repos_with_largest",
)
RQ2_DIFFERENCE_FIELDS = (
    "repo_full_name",
    "production_pre",
    "production_post",
    "production_difference",
    "test_pre",
    "test_post",
    "test_difference",
)
RQ2_SUMMARY_FIELDS = (
    "category",
    "pre_median",
    "pre_q1",
    "pre_q3",
    "pre_iqr",
    "pre_minimum",
    "pre_maximum",
    "pre_mean",
    "pre_total",
    "post_median",
    "post_q1",
    "post_q3",
    "post_iqr",
    "post_minimum",
    "post_maximum",
    "post_mean",
    "post_total",
    "difference_median",
    "difference_q1",
    "difference_q3",
    "difference_iqr",
    "difference_minimum",
    "difference_maximum",
    "difference_mean",
    "difference_total",
    "positive",
    "negative",
    "zero",
)


@dataclass(frozen=True)
class Statistics:
    n: int
    median: float
    q1: float
    q3: float
    iqr: float
    minimum: int
    maximum: int
    mean: float
    total: int

    def as_row(self) -> dict[str, int | float]:
        return {
            "median": self.median,
            "q1": self.q1,
            "q3": self.q3,
            "iqr": self.iqr,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "mean": self.mean,
            "total": self.total,
        }


def quantile(sorted_values: list[int], q: float) -> float:
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_values[0])
    rank = q * (n - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = rank - lower
    return sorted_values[lower] + fraction * (sorted_values[upper] - sorted_values[lower])


def summarize(values: Iterable[int]) -> Statistics:
    ordered = sorted(values)
    q1 = quantile(ordered, 0.25)
    q3 = quantile(ordered, 0.75)
    total = sum(ordered)
    return Statistics(
        n=len(ordered),
        median=quantile(ordered, 0.5),
        q1=q1,
        q3=q3,
        iqr=q3 - q1,
        minimum=ordered[0] if ordered else 0,
        maximum=ordered[-1] if ordered else 0,
        mean=total / len(ordered) if ordered else 0.0,
        total=total,
    )


def load_counts(path: Path) -> tuple[list[str], dict[tuple[str, str, str], int]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != COUNT_FIELDS:
            raise ValueError(f"{path}: expected columns {COUNT_FIELDS}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path}: no activity counts found")
    order: list[str] = []
    values: dict[tuple[str, str, str], int] = {}
    for row in rows:
        if row["data_status"] != "observed":
            raise ValueError(
                f"{path}: repository {row['repo_full_name']} has data_status "
                f"{row['data_status']!r}; cannot analyze incomplete data"
            )
        repo = row["repo_full_name"]
        if repo not in order:
            order.append(repo)
        key = (repo, row["period"], row["category"])
        if key in values:
            raise ValueError(f"{path}: duplicate count row {key}")
        values[key] = int(row["unique_commit_count"])

    for repo in order:
        for period in PERIODS:
            for category in ALL_CATEGORIES:
                if (repo, period, category) not in values:
                    raise ValueError(f"{path}: missing {repo}/{period}/{category}")
    return order, values


def build_rq1(
    order: list[str], values: dict[tuple[str, str, str], int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    rows = []
    largest_counter: dict[str, int] = defaultdict(int)
    tie_count = 0
    for repo in order:
        counts = {category: values[(repo, "post", category)] for category in RQ1_CATEGORIES}
        largest = max(counts.values())
        largest_categories = [
            category for category in RQ1_CATEGORIES if counts[category] == largest
        ]
        tie = len(largest_categories) > 1
        if tie:
            tie_count += 1
        for category in largest_categories:
            largest_counter[category] += 1
        rows.append(
            {
                "repo_full_name": repo,
                "skill_commits": counts["skill"],
                "production_commits": counts["production"],
                "test_commits": counts["test"],
                "largest_count": largest,
                "largest_categories": ";".join(largest_categories),
                "tie": str(tie).lower(),
            }
        )
    summary = []
    for category in RQ1_CATEGORIES:
        series = [values[(repo, "post", category)] for repo in order]
        stats = summarize(series)
        summary.append(
            {
                "category": category,
                "n_repositories": stats.n,
                "zero_count": sum(1 for value in series if value == 0),
                **stats.as_row(),
                "repos_with_largest": largest_counter.get(category, 0),
            }
        )
    return rows, summary, tie_count


def build_rq2(
    order: list[str], values: dict[tuple[str, str, str], int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    for repo in order:
        row: dict[str, Any] = {"repo_full_name": repo}
        for category in RQ2_CATEGORIES:
            pre = values[(repo, "pre", category)]
            post = values[(repo, "post", category)]
            row[f"{category}_pre"] = pre
            row[f"{category}_post"] = post
            row[f"{category}_difference"] = post - pre
        rows.append(row)
    summary = []
    for category in RQ2_CATEGORIES:
        pre_series = [values[(repo, "pre", category)] for repo in order]
        post_series = [values[(repo, "post", category)] for repo in order]
        differences = [post - pre for pre, post in zip(pre_series, post_series)]
        pre_stats = summarize(pre_series)
        post_stats = summarize(post_series)
        difference_stats = summarize(differences)
        summary.append(
            {
                "category": category,
                **{f"pre_{key}": value for key, value in pre_stats.as_row().items()},
                **{f"post_{key}": value for key, value in post_stats.as_row().items()},
                **{
                    f"difference_{key}": value
                    for key, value in difference_stats.as_row().items()
                },
                "positive": sum(1 for value in differences if value > 0),
                "negative": sum(1 for value in differences if value < 0),
                "zero": sum(1 for value in differences if value == 0),
            }
        )
    return rows, summary


def render_consistency(
    order: list[str],
    values: dict[tuple[str, str, str], int],
    rq1_rows: list[dict[str, Any]],
    rq2_rows: list[dict[str, Any]],
    tie_count: int,
) -> str:
    lines = [
        "Consistency checks for the descriptive RQ1/RQ2 analysis",
        "",
        f"repositories: {len(order)}",
        f"repositories with a tie for the largest post category: {tie_count}",
        "",
        "RQ1 reconstructed from activity_counts.csv (post period):",
    ]
    for row in rq1_rows:
        repo = row["repo_full_name"]
        expected = {
            category: values[(repo, "post", category)] for category in RQ1_CATEGORIES
        }
        observed = {
            "skill": row["skill_commits"],
            "production": row["production_commits"],
            "test": row["test_commits"],
        }
        status = "OK" if expected == observed else "MISMATCH"
        lines.append(
            f"  {status}  {repo}: {observed} (expected {expected})"
        )
    lines.extend(["", "RQ2 differences reconstructed from activity_counts.csv:"])
    for row in rq2_rows:
        repo = row["repo_full_name"]
        for category in RQ2_CATEGORIES:
            expected = values[(repo, "post", category)] - values[(repo, "pre", category)]
            observed = row[f"{category}_difference"]
            status = "OK" if expected == observed else "MISMATCH"
            lines.append(f"  {status}  {repo} {category} difference {observed}")
    lines.extend(
        [
            "",
            "No inferential test, confidence interval or population generalization",
            "was computed in this stage.",
        ]
    )
    return "\n".join(lines) + "\n"


def execute(config: Config, processed_directory: Path, output_directory: Path) -> None:
    if output_directory.exists():
        raise FileExistsError(
            f"Output directory already exists: {output_directory}. Use a new directory."
        )
    output_directory.mkdir(parents=True, exist_ok=False)

    counts_path = processed_directory / "activity_counts.csv"
    processing_manifest_path = processed_directory / "processing_manifest.json"
    order, values = load_counts(counts_path)

    rq1_rows, rq1_summary, tie_count = build_rq1(order, values)
    rq2_rows, rq2_summary = build_rq2(order, values)
    consistency = render_consistency(order, values, rq1_rows, rq2_rows, tie_count)

    write_csv_atomic(output_directory / "rq1_repository_counts.csv", list(RQ1_COUNT_FIELDS), rq1_rows)
    write_csv_atomic(output_directory / "rq1_summary.csv", list(RQ1_SUMMARY_FIELDS), rq1_summary)
    write_csv_atomic(
        output_directory / "rq2_repository_differences.csv",
        list(RQ2_DIFFERENCE_FIELDS),
        rq2_rows,
    )
    write_csv_atomic(output_directory / "rq2_summary.csv", list(RQ2_SUMMARY_FIELDS), rq2_summary)
    (output_directory / "consistency_checks.txt").write_text(consistency, encoding="utf-8")

    inputs = {"activity_counts": counts_path, "processing_manifest": processing_manifest_path}
    review_checksum = processed_directory.parent / "activity-validation" / "human_activity_review.sha256"
    if review_checksum.exists():
        inputs["human_activity_review_checksums"] = review_checksum
    manifest = {
        "created_at_utc": utc_now(),
        "analysis_protocol_version": ANALYSIS_PROTOCOL_VERSION,
        "quantile_method": QUANTILE_METHOD,
        "configuration": {
            "extraction": serializable_config(config),
            "processed_directory": str(processed_directory.resolve()),
            "output_directory": str(output_directory.resolve()),
            "rq1_categories": list(RQ1_CATEGORIES),
            "rq2_categories": list(RQ2_CATEGORIES),
            "rq1_period": "post",
            "rq2_difference_definition": "post - pre",
        },
        "tool_versions": {"git": git_version(config), "python": sys.version},
        "input_sha256": {name: file_sha256(path) for name, path in inputs.items()},
        "repository_count": len(order),
        "tie_repository_count": tie_count,
        "row_counts": {
            "rq1_repository_counts": len(rq1_rows),
            "rq1_summary": len(rq1_summary),
            "rq2_repository_differences": len(rq2_rows),
            "rq2_summary": len(rq2_summary),
        },
        "inferential_tests": False,
    }
    (output_directory / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute descriptive RQ1/RQ2 statistics from activity counts."
    )
    parser.add_argument("--config", type=Path, default=Path("extraction.toml"))
    parser.add_argument(
        "--processed-directory", type=Path, default=Path("data/processed")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results"),
        help="New output directory; it must not already exist",
    )
    return parser


def main() -> None:
    args = argument_parser().parse_args()
    config = load_config(args.config)
    execute(config, args.processed_directory.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
