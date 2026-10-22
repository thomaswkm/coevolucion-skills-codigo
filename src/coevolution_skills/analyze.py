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
ADOPTION_DECISIONS = {"valid", "invalid", "ambiguous"}
ANALYSIS_SAMPLE_FIELDS = (
    "repo_full_name",
    "adoption_decision",
    "included_in_primary",
    "ambiguous",
)
SAMPLE_ESTIMATE_FIELDS = (
    "frame_size",
    "candidates_examined",
    "target_count",
    "eligible_found",
    "yield",
    "yield_ci_low",
    "yield_ci_high",
    "estimated_population",
    "estimated_population_low",
    "estimated_population_high",
    "sampling_fraction",
    "finite_population_correction",
    "completion_status",
    "cap_reached",
    "method",
)
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


def load_adoption_decisions(
    path: Path, order: list[str], *, allow_pending: bool = False
) -> dict[str, str] | None:
    """Read the sealed human adoption review, when it is available.

    A pending exploratory review is reported as ``None`` so the descriptive
    analysis can continue and record that the decisions were not applied.
    """
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "repo_full_name" not in (reader.fieldnames or ()) or (
            "human_adoption_decision" not in (reader.fieldnames or ())
        ):
            raise ValueError(f"{path}: missing adoption-review columns")
        rows = list(reader)
    decisions: dict[str, str] = {}
    for row in rows:
        repo = row["repo_full_name"]
        if repo in decisions:
            raise ValueError(f"{path}: duplicate adoption decision for {repo}")
        decision = row["human_adoption_decision"].strip()
        if not decision:
            if allow_pending:
                return None
            raise ValueError(f"{path}: blank adoption decision for {repo}")
        if decision not in ADOPTION_DECISIONS:
            raise ValueError(
                f"{path}: invalid adoption decision {decision!r} for {repo}"
            )
        decisions[repo] = decision
    missing = [repo for repo in order if repo not in decisions]
    if missing:
        raise ValueError(
            f"{path}: missing adoption decisions for {len(missing)} repositories"
        )
    return decisions


def apply_adoption_decisions(
    order: list[str], decisions: dict[str, str] | None
) -> tuple[list[str], list[str], list[str]]:
    if decisions is None:
        return list(order), [], []
    invalid = [repo for repo in order if decisions[repo] == "invalid"]
    ambiguous = [repo for repo in order if decisions[repo] == "ambiguous"]
    included = [
        repo for repo in order if decisions[repo] not in {"invalid"}
    ]
    return included, invalid, ambiguous


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - half), min(1.0, center + half)


def estimate_population(config: Config) -> dict[str, Any] | None:
    """Estimate the eligible population and the screening yield."""
    order_path = config.output_directory / "candidate_order.csv"
    log_path = config.output_directory / "screening_log.csv"
    manifest_path = config.output_directory / "run_manifest.json"
    if not order_path.exists() or not log_path.exists():
        return None
    with order_path.open(newline="", encoding="utf-8") as handle:
        frame_size = sum(1 for _ in csv.DictReader(handle))
    with log_path.open(newline="", encoding="utf-8") as handle:
        screening = list(csv.DictReader(handle))
    examined = len(screening)
    eligible = sum(1 for row in screening if row.get("status") == "eligible")
    if frame_size <= 0 or examined <= 0:
        return None
    yield_rate = eligible / examined
    low, high = wilson_interval(eligible, examined)
    estimated = frame_size * yield_rate
    sampling_fraction = eligible / estimated if estimated > 0 else 0.0
    correction = (
        math.sqrt(max(0.0, 1.0 - eligible / estimated))
        if estimated > 0
        else 0.0
    )
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "frame_size": frame_size,
        "candidates_examined": examined,
        "target_count": manifest.get("target_count") or config.target_count,
        "eligible_found": eligible,
        "yield": yield_rate,
        "yield_ci_low": low,
        "yield_ci_high": high,
        "estimated_population": estimated,
        "estimated_population_low": frame_size * low,
        "estimated_population_high": frame_size * high,
        "sampling_fraction": sampling_fraction,
        "finite_population_correction": correction,
        "completion_status": manifest.get("completion_status", ""),
        "cap_reached": str(manifest.get("cap_reached", "")).lower(),
        "method": "Wilson 95% interval on eligible/examined; N = frame * yield",
    }


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
    *,
    decisions: dict[str, str] | None = None,
    invalid: list[str] | None = None,
    ambiguous: list[str] | None = None,
) -> str:
    invalid = invalid or []
    ambiguous = ambiguous or []
    lines = [
        "Consistency checks for the descriptive RQ1/RQ2 analysis",
        "",
        f"repositories in analyzed sample: {len(order)}",
        f"repositories with a tie for the largest post category: {tie_count}",
    ]
    if decisions is not None:
        lines.extend(
            [
                f"adoption decisions applied: {len(decisions)} reviewed",
                f"invalid adoptions excluded: {len(invalid)}",
                f"ambiguous adoptions kept and flagged: {len(ambiguous)}",
            ]
        )
    lines.extend(
        [
            "",
            "RQ1 reconstructed from activity_counts.csv (post period):",
        ]
    )
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


def execute(
    config: Config,
    processed_directory: Path,
    output_directory: Path,
    validation_directory: Path | None = None,
) -> None:
    if output_directory.exists():
        raise FileExistsError(
            f"Output directory already exists: {output_directory}. Use a new directory."
        )
    output_directory.mkdir(parents=True, exist_ok=False)

    counts_path = processed_directory / "activity_counts.csv"
    processing_manifest_path = processed_directory / "processing_manifest.json"
    order, values = load_counts(counts_path)
    processing_manifest = json.loads(
        processing_manifest_path.read_text(encoding="utf-8")
    )
    human_screening_review = processing_manifest.get("human_screening_review")

    if validation_directory is None:
        validation_directory = processed_directory.parent / "validation"
    adoption_review_path = validation_directory / "adoption_review.csv"
    review_status = (
        human_screening_review.get("status") if human_screening_review else None
    )
    decisions = load_adoption_decisions(
        adoption_review_path,
        order,
        allow_pending=review_status == "pending",
    )
    if decisions is None and review_status == "complete":
        raise ValueError(
            f"{adoption_review_path}: the processing manifest records a complete "
            "human review but the adoption decisions are missing"
        )
    included_order, invalid, ambiguous = apply_adoption_decisions(order, decisions)

    rq1_rows, rq1_summary, tie_count = build_rq1(included_order, values)
    rq2_rows, rq2_summary = build_rq2(included_order, values)
    consistency = render_consistency(
        included_order,
        values,
        rq1_rows,
        rq2_rows,
        tie_count,
        decisions=decisions,
        invalid=invalid,
        ambiguous=ambiguous,
    )

    sample_rows = [
        {
            "repo_full_name": repo,
            "adoption_decision": decisions[repo] if decisions else "",
            "included_in_primary": str(repo not in invalid).lower(),
            "ambiguous": str(repo in ambiguous).lower(),
        }
        for repo in order
    ]
    write_csv_atomic(
        output_directory / "analysis_sample.csv",
        list(ANALYSIS_SAMPLE_FIELDS),
        sample_rows,
    )
    write_csv_atomic(output_directory / "rq1_repository_counts.csv", list(RQ1_COUNT_FIELDS), rq1_rows)
    write_csv_atomic(output_directory / "rq1_summary.csv", list(RQ1_SUMMARY_FIELDS), rq1_summary)
    write_csv_atomic(
        output_directory / "rq2_repository_differences.csv",
        list(RQ2_DIFFERENCE_FIELDS),
        rq2_rows,
    )
    write_csv_atomic(output_directory / "rq2_summary.csv", list(RQ2_SUMMARY_FIELDS), rq2_summary)
    (output_directory / "consistency_checks.txt").write_text(consistency, encoding="utf-8")

    estimate = estimate_population(config)
    if estimate is not None:
        write_csv_atomic(
            output_directory / "sample_estimate.csv",
            list(SAMPLE_ESTIMATE_FIELDS),
            [estimate],
        )

    inputs = {"activity_counts": counts_path, "processing_manifest": processing_manifest_path}
    if adoption_review_path.exists():
        inputs["adoption_review"] = adoption_review_path
    if estimate is not None:
        inputs["screening_log"] = config.output_directory / "screening_log.csv"
        inputs["candidate_order"] = config.output_directory / "candidate_order.csv"
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
            "validation_directory": str(validation_directory.resolve()),
            "output_directory": str(output_directory.resolve()),
            "rq1_categories": list(RQ1_CATEGORIES),
            "rq2_categories": list(RQ2_CATEGORIES),
            "rq1_period": "post",
            "rq2_difference_definition": "post - pre",
            "adoption_policy": "exclude invalid; keep ambiguous flagged; no replacement",
        },
        "tool_versions": {"git": git_version(config), "python": sys.version},
        "input_sha256": {name: file_sha256(path) for name, path in inputs.items()},
        "repository_count": len(included_order),
        "reviewed_repository_count": len(order),
        "excluded_invalid_count": len(invalid),
        "ambiguous_count": len(ambiguous),
        "adoption_review": {
            "available": decisions is not None,
            "path": str(adoption_review_path.resolve()),
            "invalid_repositories": invalid,
            "ambiguous_repositories": ambiguous,
        },
        "tie_repository_count": tie_count,
        "row_counts": {
            "analysis_sample": len(sample_rows),
            "rq1_repository_counts": len(rq1_rows),
            "rq1_summary": len(rq1_summary),
            "rq2_repository_differences": len(rq2_rows),
            "rq2_summary": len(rq2_summary),
        },
        "sample_estimate": estimate,
        "inferential_tests": False,
        "human_screening_review": human_screening_review,
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
        "--validation-directory",
        type=Path,
        default=Path("data/validation"),
        help="Directory holding the sealed adoption review",
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
    execute(
        config,
        args.processed_directory.resolve(),
        args.output.resolve(),
        args.validation_directory.resolve(),
    )


if __name__ == "__main__":
    main()
