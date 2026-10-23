"""Inferential RQ1/RQ2 analysis frozen in ``docs/analysis-plan.md`` (v1.1).

The module reads the preserved processed data and the sealed adoption review,
recomputes the analysis sample, and writes every test, effect size, confidence
interval and sensitivity required by the pre-registered plan.  It never edits
``data/processed/`` or the descriptive ``results/`` directory: all outputs go to
a new directory.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import duckdb
import numpy as np
import scipy
from scipy.stats import binomtest, friedmanchisquare, rankdata, wilcoxon

from .analyze import (
    ADOPTION_DECISIONS,
    ALL_CATEGORIES,
    PERIODS,
    RQ1_CATEGORIES,
    RQ2_CATEGORIES,
    load_counts,
    quantile,
)
from .extract import (
    Config,
    SKILL_ROOTS,
    file_sha256,
    git_version,
    huggingface_url,
    load_config,
    parse_datetime,
    serializable_config,
    sql_list,
    utc_now,
    write_csv_atomic,
)


INFERENCE_PROTOCOL_VERSION = "1"
ALPHA = 0.05
DEFAULT_BOOTSTRAP_RESAMPLES = 10_000
DEFAULT_BOOTSTRAP_SEED = 20261020
BOOTSTRAP_CONFIDENCE = 0.95
BOOTSTRAP_PERCENTILES = (2.5, 97.5)
HOLM_DEFINITION = (
    "Holm step-down adjusted p-values within RQ1 (3 pairwise tests) and "
    "within RQ2 (2 tests), computed separately"
)
SIGN_DEFINITION = (
    "Two-sided exact binomial test on non-zero paired differences; "
    "Clopper-Pearson 95% interval for the proportion of positive differences"
)
WILCOXON_DEFINITION = (
    "Two-sided Wilcoxon signed-rank test; zero differences discarded; SciPy "
    "method='auto' (exact when n_nonzero <= 25 without ties, otherwise normal "
    "approximation with tie correction); no continuity correction"
)
EFFECT_DEFINITION = (
    "Matched rank-biserial correlation (positive rank sum minus negative rank "
    "sum over total rank sum) and Hodges-Lehmann shift (median of Walsh averages)"
)
CI_DEFINITION = (
    "Percentile bootstrap: 2.5 and 97.5 percentiles; repositories are the "
    "resampling unit and RQ1/RQ2 keep their paired structure"
)

RQ1_PAIRS = (
    ("skill", "production"),
    ("skill", "test"),
    ("production", "test"),
)

ANALYSIS_SAMPLE_FIELDS = (
    "repo_full_name",
    "adoption_decision",
    "included_in_primary",
    "ambiguous",
)
TEST_FIELDS = (
    "sensitivity",
    "rq",
    "test",
    "comparison",
    "n_blocks",
    "n_nonzero",
    "df",
    "statistic",
    "p_value",
    "p_adjusted",
    "adjusted_family",
    "significant",
    "note",
)
EFFECT_FIELDS = (
    "sensitivity",
    "rq",
    "comparison",
    "first",
    "second",
    "n_nonzero",
    "median_first",
    "median_second",
    "rank_biserial",
    "hodges_lehmann",
    "direction",
    "note",
)
BOOTSTRAP_FIELDS = (
    "sensitivity",
    "rq",
    "quantity",
    "comparison",
    "category",
    "point_estimate",
    "ci_low",
    "ci_high",
    "degenerate",
    "resamples",
    "seed",
)
SIGN_FIELDS = (
    "sensitivity",
    "rq",
    "comparison",
    "n_nonzero",
    "positive",
    "negative",
    "positive_proportion",
    "ci_low",
    "ci_high",
    "p_value",
    "p_adjusted",
    "significant",
    "wilcoxon_significant",
    "concordant_with_wilcoxon",
    "robust",
    "note",
)
SENSITIVITY_STATUS_FIELDS = (
    "sensitivity",
    "status",
    "repositories",
    "detail",
)
CHURN_FIELDS = (
    "repo_full_name",
    "period",
    "category",
    "commits",
    "adoption_files",
    "commits_per_file",
    "lines_changed",
    "churn_per_file",
    "binary_changes",
    "repository_commits_in_period",
    "commit_share",
)
CLUSTER_FIELDS = (
    "repo_full_name",
    "skill_signature",
    "cluster_size",
    "cluster_representative",
    "included_in_sensitivity",
)
BOT_FIELDS = (
    "repo_full_name",
    "commit",
    "period",
    "categories",
    "author_name",
    "author_email",
    "committer_name",
    "committer_email",
    "human_review_decision",
    "human_notes",
)
REPRESENTATIVENESS_FIELDS = (
    "group",
    "variable",
    "n_repositories",
    "n_available",
    "n_missing",
    "median",
    "q1",
    "q3",
    "iqr",
    "minimum",
    "maximum",
    "unit",
    "source",
    "notes",
)
METADATA_FIELDS = (
    "repo_full_name",
    "stars",
    "forks",
    "created_at",
    "language",
    "source",
    "fetched_at_utc",
)
FRAME_COPY_FIELDS = (
    "repo_full_name",
    "skill_count_snapshot",
    "copied_skill_count_snapshot",
)


@dataclass(frozen=True)
class BootstrapEstimate:
    point: float
    low: float
    high: float
    degenerate: bool


@dataclass(frozen=True)
class BlockResults:
    tests: list[dict[str, Any]]
    effects: list[dict[str, Any]]
    bootstrap: list[dict[str, Any]]
    signs: list[dict[str, Any]]


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, preserving the input order."""
    if not p_values:
        return []
    indexed = sorted(
        (
            (
                1.0
                if not math.isfinite(float(value))
                else max(0.0, min(1.0, float(value)))
            ),
            index,
        )
        for index, value in enumerate(p_values)
    )
    adjusted = [0.0] * len(p_values)
    running = 0.0
    count = len(p_values)
    for rank, (value, index) in enumerate(indexed, start=1):
        candidate = min(1.0, (count - rank + 1) * value)
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


def rank_biserial(differences: Iterable[float]) -> float:
    """Matched rank-biserial correlation for paired differences."""
    values = np.asarray([float(value) for value in differences if value != 0], dtype=float)
    if values.size == 0:
        return 0.0
    ranks = rankdata(np.abs(values), method="average")
    positive = float(ranks[values > 0].sum())
    negative = float(ranks[values < 0].sum())
    total = positive + negative
    return 0.0 if total == 0 else (positive - negative) / total


def hodges_lehmann(differences: Iterable[float]) -> float:
    """Hodges-Lehmann shift: median of all Walsh averages (d_i+d_j)/2."""
    values = np.asarray([float(value) for value in differences], dtype=float)
    if values.size == 0:
        return math.nan
    if values.size == 1:
        return float(values[0])
    indices = np.triu_indices(values.size)
    walsh = (values[:, None] + values[None, :])[indices] / 2.0
    return float(np.median(walsh))


def paired_wilcoxon(differences: Iterable[float]) -> dict[str, Any]:
    values = np.asarray([float(value) for value in differences], dtype=float)
    nonzero = values[values != 0]
    result: dict[str, Any] = {
        "n_total": int(values.size),
        "n_nonzero": int(nonzero.size),
        "statistic": math.nan,
        "p_value": math.nan,
        "note": "",
    }
    if nonzero.size == 0:
        result.update(
            statistic=0.0,
            p_value=1.0,
            note="no_nonzero_differences; test not identifiable, p=1 reported",
        )
        return result
    try:
        test = wilcoxon(
            nonzero,
            zero_method="wilcox",
            correction=False,
            alternative="two-sided",
            method="auto",
        )
        result["statistic"] = float(test.statistic)
        result["p_value"] = float(test.pvalue)
        if not math.isfinite(result["p_value"]):
            result["p_value"] = 1.0
            result["note"] = "scipy_returned_nonfinite_p; p=1 reported"
    except ValueError as error:  # pragma: no cover - defensive
        result["p_value"] = 1.0
        result["note"] = f"wilcoxon_failed: {error}"
    return result


def friedman_test(columns: Sequence[Sequence[float]]) -> dict[str, Any]:
    matrix = np.asarray(columns, dtype=float).T
    if matrix.ndim != 2:
        raise ValueError("Friedman input must be a sequence of equal-length columns")
    blocks, treatments = matrix.shape
    result: dict[str, Any] = {
        "n_blocks": int(blocks),
        "treatments": int(treatments),
        "statistic": math.nan,
        "df": int(treatments - 1),
        "p_value": math.nan,
        "kendall_w": math.nan,
        "note": "",
    }
    if blocks < 2 or treatments < 3:
        result["note"] = "insufficient_blocks_or_treatments"
        result["p_value"] = 1.0
        result["statistic"] = 0.0
        result["kendall_w"] = 0.0
        return result
    # A completely flat block (all three categories equal) still carries a tie
    # correction; Friedman remains defined as long as the rank sums vary.
    if np.allclose(matrix, matrix[0, 0]):
        result["note"] = "all_observations_equal; Friedman undefined, p=1 reported"
        result["statistic"] = 0.0
        result["p_value"] = 1.0
        result["kendall_w"] = 0.0
        return result
    try:
        test = friedmanchisquare(*[matrix[:, index] for index in range(treatments)])
        statistic = float(test.statistic)
        p_value = float(test.pvalue)
        if not math.isfinite(statistic) or not math.isfinite(p_value):
            result["note"] = "scipy_returned_nonfinite_result; p=1 reported"
            statistic, p_value = 0.0, 1.0
        result["statistic"] = statistic
        result["p_value"] = p_value
        result["kendall_w"] = statistic / (blocks * (treatments - 1))
    except ValueError as error:  # pragma: no cover - defensive
        result["statistic"] = 0.0
        result["p_value"] = 1.0
        result["kendall_w"] = 0.0
        result["note"] = f"friedman_failed: {error}"
    return result


def sign_test(differences: Iterable[float]) -> dict[str, Any]:
    values = np.asarray([float(value) for value in differences], dtype=float)
    nonzero = values[values != 0]
    positive = int(np.count_nonzero(nonzero > 0))
    negative = int(np.count_nonzero(nonzero < 0))
    result: dict[str, Any] = {
        "n_nonzero": int(nonzero.size),
        "positive": positive,
        "negative": negative,
        "positive_proportion": math.nan,
        "ci_low": math.nan,
        "ci_high": math.nan,
        "p_value": math.nan,
        "note": "",
    }
    if nonzero.size == 0:
        result.update(
            positive_proportion=0.0,
            ci_low=0.0,
            ci_high=0.0,
            p_value=1.0,
            note="no_nonzero_differences; sign test not identifiable, p=1 reported",
        )
        return result
    test = binomtest(positive, nonzero.size, 0.5, alternative="two-sided")
    interval = test.proportion_ci(confidence_level=0.95, method="exact")
    result.update(
        positive_proportion=positive / nonzero.size,
        ci_low=float(interval.low),
        ci_high=float(interval.high),
        p_value=float(test.pvalue),
    )
    return result


def bootstrap_indices(n: int, resamples: int, seed: int) -> np.ndarray:
    if n < 1:
        raise ValueError("bootstrap requires at least one repository")
    if resamples < 1:
        raise ValueError("bootstrap resamples must be positive")
    generator = np.random.default_rng(seed)
    return generator.integers(0, n, size=(resamples, n), dtype=np.int64)


def percentile_interval(samples: np.ndarray) -> BootstrapEstimate:
    finite = samples[np.isfinite(samples)]
    if finite.size == 0:
        return BootstrapEstimate(math.nan, math.nan, math.nan, True)
    low, high = np.percentile(finite, BOOTSTRAP_PERCENTILES)
    degenerate = bool(np.allclose(finite, finite[0], rtol=0.0, atol=1e-12))
    if degenerate:
        return BootstrapEstimate(float(finite[0]), float(low), float(high), True)
    return BootstrapEstimate(float(np.median(finite)), float(low), float(high), False)


def bootstrap_block(
    medians: dict[str, np.ndarray],
    shifts: dict[str, np.ndarray],
    *,
    points: dict[str, float] | None = None,
    resamples: int,
    seed: int,
) -> tuple[dict[str, BootstrapEstimate], int]:
    """Bootstrap medians and Hodges-Lehmann shifts over repository resamples."""
    if not medians and not shifts:
        return {}, 0
    lengths = {len(values) for values in list(medians.values()) + list(shifts.values())}
    if len(lengths) != 1:
        raise ValueError("bootstrap inputs must share the same repository dimension")
    n = lengths.pop()
    indices = bootstrap_indices(n, resamples, seed)
    estimates: dict[str, BootstrapEstimate] = {}
    for name, values in medians.items():
        array = np.asarray(values, dtype=float)
        samples = np.median(array[indices], axis=1)
        estimate = percentile_interval(samples)
        point = points.get(name, float(np.median(array))) if points else float(np.median(array))
        estimates[name] = BootstrapEstimate(
            float(point), estimate.low, estimate.high, estimate.degenerate
        )
    for name, values in shifts.items():
        array = np.asarray(values, dtype=float)
        samples = np.empty(resamples, dtype=float)
        for replicate, resample in enumerate(array[indices]):
            samples[replicate] = hodges_lehmann(resample)
        estimate = percentile_interval(samples)
        point = points.get(name, hodges_lehmann(array)) if points else hodges_lehmann(array)
        estimates[name] = BootstrapEstimate(
            float(point), estimate.low, estimate.high, estimate.degenerate
        )
    return estimates, resamples


def _as_float(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _format(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return value
    return value


def _row(fields: Sequence[str], **values: Any) -> dict[str, Any]:
    return {field: _format(values.get(field, "")) for field in fields}


def infer_block(
    order: Sequence[str],
    values: dict[tuple[str, str, str], float],
    *,
    sensitivity: str,
    resamples: int,
    seed: int,
) -> BlockResults:
    """Run the pre-registered RQ1 and RQ2 tests on one analysis dataset."""
    n = len(order)
    if n == 0:
        raise ValueError("inference requires at least one repository")

    def series(period: str, category: str) -> np.ndarray:
        return np.asarray(
            [float(values[(repo, period, category)]) for repo in order], dtype=float
        )

    post = {category: series("post", category) for category in RQ1_CATEGORIES}
    pre = {category: series("pre", category) for category in RQ1_CATEGORIES}

    tests: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    signs: list[dict[str, Any]] = []
    bootstrap_medians = {
        f"median_post_{category}": post[category] for category in RQ1_CATEGORIES
    }
    for category in RQ2_CATEGORIES:
        bootstrap_medians[f"median_pre_{category}"] = pre[category]
        bootstrap_medians[f"median_post_{category}"] = post[category]
    bootstrap_shifts: dict[str, np.ndarray] = {}

    # ---- RQ1: Friedman + pairwise Wilcoxon (only if the global test is significant)
    global_test = friedman_test([post[category] for category in RQ1_CATEGORIES])
    friedman_significant = (
        math.isfinite(global_test["p_value"]) and global_test["p_value"] < ALPHA
    )
    tests.append(
        _row(
            TEST_FIELDS,
            sensitivity=sensitivity,
            rq="RQ1",
            test="friedman",
            comparison="skill|production|test",
            n_blocks=global_test["n_blocks"],
            n_nonzero="",
            df=global_test["df"],
            statistic=global_test["statistic"],
            p_value=global_test["p_value"],
            p_adjusted=global_test["p_value"],
            adjusted_family="rq1_global",
            significant=str(friedman_significant).lower(),
            note=(
                f"Kendall W={global_test['kendall_w']:.6f}; "
                f"treatments={global_test['treatments']}; {global_test['note']}"
            ).strip("; "),
        )
    )

    pair_results: list[dict[str, Any]] = []
    pair_p_values: list[float] = []
    for first, second in RQ1_PAIRS:
        differences = post[second] - post[first]
        comparison = f"{first}-{second}"
        bootstrap_shifts[f"hl_post_{comparison}"] = differences
        if not friedman_significant:
            tests.append(
                _row(
                    TEST_FIELDS,
                    sensitivity=sensitivity,
                    rq="RQ1",
                    test="wilcoxon",
                    comparison=comparison,
                    n_blocks=n,
                    n_nonzero=int(np.count_nonzero(differences)),
                    df="",
                    statistic="",
                    p_value="",
                    p_adjusted="",
                    adjusted_family="rq1_pairs",
                    significant="not_run",
                    note="pairwise tests not run because Friedman is not significant",
                )
            )
            continue
        result = paired_wilcoxon(differences)
        pair_p_values.append(float(result["p_value"]))
        pair_results.append(
            {
                "comparison": comparison,
                "first": first,
                "second": second,
                "differences": differences,
                "result": result,
            }
        )
    adjusted_pairs = holm_adjust(pair_p_values)
    for pair, adjusted in zip(pair_results, adjusted_pairs):
        differences = pair["differences"]
        result = pair["result"]
        tests.append(
            _row(
                TEST_FIELDS,
                sensitivity=sensitivity,
                rq="RQ1",
                test="wilcoxon",
                comparison=pair["comparison"],
                n_blocks=n,
                n_nonzero=result["n_nonzero"],
                df="",
                statistic=result["statistic"],
                p_value=result["p_value"],
                p_adjusted=adjusted,
                adjusted_family="rq1_pairs",
                significant=str(adjusted < ALPHA).lower(),
                note=result["note"],
            )
        )
        effects.append(
            _row(
                EFFECT_FIELDS,
                sensitivity=sensitivity,
                rq="RQ1",
                comparison=pair["comparison"],
                first=pair["first"],
                second=pair["second"],
                n_nonzero=result["n_nonzero"],
                median_first=float(np.median(post[pair["first"]])),
                median_second=float(np.median(post[pair["second"]])),
                rank_biserial=rank_biserial(differences),
                hodges_lehmann=hodges_lehmann(differences),
                direction=(
                    f"{pair['second']}_greater_than_{pair['first']}"
                    if hodges_lehmann(differences) > 0
                    else (
                        f"{pair['first']}_greater_than_{pair['second']}"
                        if hodges_lehmann(differences) < 0
                        else "no_difference"
                    )
                ),
                note="positive effect = second category has more post-adoption commits",
            )
        )

    sign_pair_p_values: list[float] = []
    sign_pair_results: list[dict[str, Any]] = []
    if friedman_significant:
        for pair in pair_results:
            sign_result = sign_test(pair["differences"])
            sign_pair_p_values.append(float(sign_result["p_value"]))
            sign_pair_results.append({**pair, "sign": sign_result})
    for pair, adjusted in zip(sign_pair_results, holm_adjust(sign_pair_p_values)):
        sign_result = pair["sign"]
        wilcoxon_adjusted = next(
            (
                row["p_adjusted"]
                for row in tests
                if row["rq"] == "RQ1"
                and row["test"] == "wilcoxon"
                and row["comparison"] == pair["comparison"]
            ),
            math.nan,
        )
        wilcoxon_significant = (
            isinstance(wilcoxon_adjusted, (int, float))
            and math.isfinite(float(wilcoxon_adjusted))
            and float(wilcoxon_adjusted) < ALPHA
        )
        sign_significant = adjusted < ALPHA
        hl = hodges_lehmann(pair["differences"])
        direction = 0 if hl == 0 else (1 if hl > 0 else -1)
        sign_direction = 0 if sign_result["positive_proportion"] == 0.5 else (
            1 if sign_result["positive_proportion"] > 0.5 else -1
        )
        concordant = direction == sign_direction
        signs.append(
            _row(
                SIGN_FIELDS,
                sensitivity=sensitivity,
                rq="RQ1",
                comparison=pair["comparison"],
                n_nonzero=sign_result["n_nonzero"],
                positive=sign_result["positive"],
                negative=sign_result["negative"],
                positive_proportion=sign_result["positive_proportion"],
                ci_low=sign_result["ci_low"],
                ci_high=sign_result["ci_high"],
                p_value=sign_result["p_value"],
                p_adjusted=adjusted,
                significant=str(sign_significant).lower(),
                wilcoxon_significant=str(wilcoxon_significant).lower(),
                concordant_with_wilcoxon=str(concordant).lower(),
                robust=str(
                    wilcoxon_significant and sign_significant and concordant
                ).lower(),
                note=(
                    "sign test is a D6 sensitivity and is only run when Friedman "
                    "is significant; " + sign_result["note"]
                ).strip("; "),
            )
        )

    # ---- RQ2: Wilcoxon and sign tests for post-pre differences
    rq2_results: list[dict[str, Any]] = []
    for category in RQ2_CATEGORIES:
        differences = post[category] - pre[category]
        comparison = f"{category}_post_minus_pre"
        bootstrap_shifts[f"hl_{comparison}"] = differences
        bootstrap_medians[f"difference_{category}"] = differences
        result = paired_wilcoxon(differences)
        rq2_results.append(
            {
                "category": category,
                "comparison": comparison,
                "differences": differences,
                "result": result,
            }
        )
    rq2_adjusted = holm_adjust([float(item["result"]["p_value"]) for item in rq2_results])
    for item, adjusted in zip(rq2_results, rq2_adjusted):
        differences = item["differences"]
        result = item["result"]
        category = item["category"]
        tests.append(
            _row(
                TEST_FIELDS,
                sensitivity=sensitivity,
                rq="RQ2",
                test="wilcoxon",
                comparison=item["comparison"],
                n_blocks=n,
                n_nonzero=result["n_nonzero"],
                df="",
                statistic=result["statistic"],
                p_value=result["p_value"],
                p_adjusted=adjusted,
                adjusted_family="rq2",
                significant=str(adjusted < ALPHA).lower(),
                note=result["note"],
            )
        )
        hl = hodges_lehmann(differences)
        effects.append(
            _row(
                EFFECT_FIELDS,
                sensitivity=sensitivity,
                rq="RQ2",
                comparison=item["comparison"],
                first="pre",
                second="post",
                n_nonzero=result["n_nonzero"],
                median_first=float(np.median(pre[category])),
                median_second=float(np.median(post[category])),
                rank_biserial=rank_biserial(differences),
                hodges_lehmann=hl,
                direction=(
                    "post_greater_than_pre"
                    if hl > 0
                    else ("pre_greater_than_post" if hl < 0 else "no_difference")
                ),
                note="positive effect = activity increases after adoption",
            )
        )

    sign_rq2_p_values: list[float] = []
    sign_rq2_results: list[dict[str, Any]] = []
    for item in rq2_results:
        sign_result = sign_test(item["differences"])
        sign_rq2_p_values.append(float(sign_result["p_value"]))
        sign_rq2_results.append({**item, "sign": sign_result})
    for item, adjusted in zip(sign_rq2_results, holm_adjust(sign_rq2_p_values)):
        sign_result = item["sign"]
        wilcoxon_row = next(
            row
            for row in tests
            if row["rq"] == "RQ2" and row["comparison"] == item["comparison"]
        )
        wilcoxon_significant = (
            isinstance(wilcoxon_row["p_adjusted"], (int, float))
            and math.isfinite(float(wilcoxon_row["p_adjusted"]))
            and float(wilcoxon_row["p_adjusted"]) < ALPHA
        )
        sign_significant = adjusted < ALPHA
        hl = hodges_lehmann(item["differences"])
        direction = 0 if hl == 0 else (1 if hl > 0 else -1)
        sign_direction = 0 if sign_result["positive_proportion"] == 0.5 else (
            1 if sign_result["positive_proportion"] > 0.5 else -1
        )
        concordant = direction == sign_direction
        signs.append(
            _row(
                SIGN_FIELDS,
                sensitivity=sensitivity,
                rq="RQ2",
                comparison=item["comparison"],
                n_nonzero=sign_result["n_nonzero"],
                positive=sign_result["positive"],
                negative=sign_result["negative"],
                positive_proportion=sign_result["positive_proportion"],
                ci_low=sign_result["ci_low"],
                ci_high=sign_result["ci_high"],
                p_value=sign_result["p_value"],
                p_adjusted=adjusted,
                significant=str(sign_significant).lower(),
                wilcoxon_significant=str(wilcoxon_significant).lower(),
                concordant_with_wilcoxon=str(concordant).lower(),
                robust=str(
                    wilcoxon_significant and sign_significant and concordant
                ).lower(),
                note="sign test is a D6 sensitivity; " + sign_result["note"],
            )
        )

    # ---- Bootstrap intervals (D5 + D6)
    bootstrap_points = {
        name: float(np.median(array)) for name, array in bootstrap_medians.items()
    }
    bootstrap_points.update(
        {name: hodges_lehmann(array) for name, array in bootstrap_shifts.items()}
    )
    estimates, used_resamples = bootstrap_block(
        bootstrap_medians,
        bootstrap_shifts,
        points=bootstrap_points,
        resamples=resamples,
        seed=seed,
    )
    bootstrap_rows: list[dict[str, Any]] = []
    for name, estimate in estimates.items():
        if name.startswith("median_"):
            _, period, category = name.split("_", 2)
            quantity, comparison, category_label = "median", "", category
            rq = "RQ1" if period == "post" and category in RQ1_CATEGORIES else "RQ2"
        elif name.startswith("difference_"):
            category = name.removeprefix("difference_")
            quantity, comparison, category_label = "median_difference", "", category
            rq = "RQ2"
        else:
            quantity = "hodges_lehmann"
            category_label = ""
            if name.startswith("hl_post_"):
                rq = "RQ1"
                comparison = name.removeprefix("hl_post_")
            else:
                rq = "RQ2"
                comparison = name.removeprefix("hl_").replace("_post_minus_pre", "")
        bootstrap_rows.append(
            _row(
                BOOTSTRAP_FIELDS,
                sensitivity=sensitivity,
                rq=rq,
                quantity=quantity,
                comparison=comparison,
                category=category_label,
                point_estimate=estimate.point,
                ci_low=estimate.low,
                ci_high=estimate.high,
                degenerate=str(estimate.degenerate).lower(),
                resamples=used_resamples,
                seed=seed,
            )
        )

    return BlockResults(
        tests=tests,
        effects=effects,
        bootstrap=bootstrap_rows,
        signs=signs,
    )


def load_analysis_sample(
    path: Path, counts_order: Sequence[str]
) -> tuple[list[str], set[str], dict[str, str], int]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if fields != ANALYSIS_SAMPLE_FIELDS:
            raise ValueError(f"{path}: expected columns {ANALYSIS_SAMPLE_FIELDS}")
        rows = list(reader)
    included: list[str] = []
    ambiguous: set[str] = set()
    decisions: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        repo = row["repo_full_name"]
        if repo in seen:
            raise ValueError(f"{path}: duplicate repository {repo}")
        seen.add(repo)
        decision = row["adoption_decision"].strip()
        if decision and decision not in ADOPTION_DECISIONS:
            raise ValueError(f"{path}: invalid adoption decision {decision!r} for {repo}")
        decisions[repo] = decision
        if row["included_in_primary"] == "true":
            included.append(repo)
        if row["ambiguous"] == "true":
            ambiguous.add(repo)
    unknown = [repo for repo in included if repo not in counts_order]
    if unknown:
        raise ValueError(f"{path}: included repositories absent from counts: {unknown}")
    if not included:
        raise ValueError(f"{path}: no repository is included in the primary analysis")
    return included, ambiguous, decisions, len(rows)


def load_selected_file_counts(
    path: Path, order: Sequence[str]
) -> dict[str, dict[str, int]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        required = {
            "repo_full_name",
            "valid_skill_paths",
            "production_python_count",
            "test_python_count",
        }
        if not required.issubset(fields):
            raise ValueError(f"{path}: missing selected-repository columns")
        rows = list(reader)
    mapping: dict[str, dict[str, int]] = {}
    for row in rows:
        repo = row["repo_full_name"]
        skills = [
            path
            for path in row["valid_skill_paths"].split(";")
            if path.strip()
        ]
        mapping[repo] = {
            "skill": len(skills),
            "production": int(row["production_python_count"]),
            "test": int(row["test_python_count"]),
        }
    missing = [repo for repo in order if repo not in mapping]
    if missing:
        raise ValueError(f"{path}: missing adoption-tree counts for {missing}")
    for repo in order:
        for category, count in mapping[repo].items():
            if count < 1:
                raise ValueError(
                    f"{path}: adoption tree has no {category} files for {repo}"
                )
    return mapping


def load_churn(
    path: Path, order: Sequence[str]
) -> dict[tuple[str, str, str], dict[str, int]]:
    included = set(order)
    values: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"lines_changed": 0, "binary_changes": 0}
    )
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        required = {
            "repo_full_name",
            "period",
            "status",
            "old_path",
            "new_path",
            "old_category",
            "new_category",
            "lines_added",
            "lines_deleted",
            "is_binary",
        }
        if not required.issubset(fields):
            raise ValueError(f"{path}: missing file-change columns")
        for row in reader:
            repo = row["repo_full_name"]
            if repo not in included:
                continue
            status = row["status"]
            if status == "D":
                category = row["old_category"]
            else:
                category = row["new_category"]
            if category not in RQ1_CATEGORIES:
                continue
            key = (repo, row["period"], category)
            if row["is_binary"] == "true":
                values[key]["binary_changes"] += 1
                continue
            values[key]["lines_changed"] += int(row["lines_added"]) + int(
                row["lines_deleted"]
            )
    return values


def load_commit_index(
    path: Path, order: Sequence[str]
) -> tuple[
    dict[tuple[str, str], set[str]],
    dict[tuple[str, str, str], set[str]],
    dict[tuple[str, str], dict[str, Any]],
]:
    included = set(order)
    totals: dict[tuple[str, str], set[str]] = defaultdict(set)
    categories: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    bots: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        required = {
            "repo_full_name",
            "period",
            "commit",
            "category",
            "excluded",
            "is_bot",
            "author_name",
            "author_email",
            "committer_name",
            "committer_email",
        }
        if not required.issubset(fields):
            raise ValueError(f"{path}: missing commit-activity columns")
        for row in reader:
            repo = row["repo_full_name"]
            if repo not in included or row["excluded"] != "false":
                continue
            key = (repo, row["period"])
            totals[key].add(row["commit"])
            category = row["category"]
            is_bot = row["is_bot"] == "true"
            # The category index is the non-bot index used by the bot
            # sensitivity; bots are tracked separately.
            if category in RQ1_CATEGORIES and not is_bot:
                categories[(repo, row["period"], category)].add(row["commit"])
            if is_bot:
                bot_key = (repo, row["commit"])
                bot = bots.setdefault(
                    bot_key,
                    {
                        "repo_full_name": repo,
                        "commit": row["commit"],
                        "period": row["period"],
                        "categories": set(),
                        "author_name": row["author_name"],
                        "author_email": row["author_email"],
                        "committer_name": row["committer_name"],
                        "committer_email": row["committer_email"],
                    },
                )
                if category:
                    bot["categories"].add(category)
    return totals, categories, bots


def load_skill_signatures(
    path: Path, order: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    included = set(order)
    collected: dict[str, list[tuple[str, str]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"repo_full_name", "path", "content_sha256"}
        if not required.issubset(tuple(reader.fieldnames or ())):
            raise ValueError(f"{path}: missing skill-content columns")
        for row in reader:
            repo = row["repo_full_name"]
            if repo in included:
                collected[repo].append((row["path"], row["content_sha256"]))
    signatures: dict[str, tuple[str, ...]] = {}
    for repo in order:
        values = collected.get(repo)
        if not values:
            signatures[repo] = (f"missing:{repo}",)
        else:
            # The signature is the complete multiset of adopted SKILL.md
            # contents, independent of client-specific directory names.
            signatures[repo] = tuple(sorted(digest for _, digest in values))
    return signatures


def _counts_from_commit_sets(
    order: Sequence[str],
    categories: dict[tuple[str, str, str], set[str]],
) -> dict[tuple[str, str, str], int]:
    return {
        (repo, period, category): len(categories.get((repo, period, category), set()))
        for repo in order
        for period in PERIODS
        for category in RQ1_CATEGORIES
    }


def build_churn_metrics(
    order: Sequence[str],
    counts: dict[tuple[str, str, str], float],
    file_counts: dict[str, dict[str, int]],
    churn: dict[tuple[str, str, str], dict[str, int]],
    totals: dict[tuple[str, str], set[str]],
) -> tuple[list[dict[str, Any]], dict[str, dict[tuple[str, str, str], float]]]:
    rows: list[dict[str, Any]] = []
    metrics: dict[str, dict[tuple[str, str, str], float]] = {
        "commits_per_file": {},
        "churn_per_file": {},
        "commit_share": {},
    }
    for repo in order:
        for period in PERIODS:
            total_commits = len(totals.get((repo, period), set()))
            for category in RQ1_CATEGORIES:
                commits = float(counts[(repo, period, category)])
                files = float(file_counts[repo][category])
                lines = float(churn.get((repo, period, category), {}).get("lines_changed", 0))
                binaries = int(churn.get((repo, period, category), {}).get("binary_changes", 0))
                commits_per_file = commits / files
                churn_per_file = lines / files
                share = commits / total_commits if total_commits else 0.0
                metrics["commits_per_file"][(repo, period, category)] = commits_per_file
                metrics["churn_per_file"][(repo, period, category)] = churn_per_file
                metrics["commit_share"][(repo, period, category)] = share
                rows.append(
                    _row(
                        CHURN_FIELDS,
                        repo_full_name=repo,
                        period=period,
                        category=category,
                        commits=commits,
                        adoption_files=files,
                        commits_per_file=commits_per_file,
                        lines_changed=lines,
                        churn_per_file=churn_per_file,
                        binary_changes=binaries,
                        repository_commits_in_period=total_commits,
                        commit_share=share,
                    )
                )
    return rows, metrics


def _append_block(
    destination: dict[str, list[dict[str, Any]]],
    block: BlockResults,
) -> None:
    destination["tests"].extend(block.tests)
    destination["effects"].extend(block.effects)
    destination["bootstrap"].extend(block.bootstrap)
    destination["signs"].extend(block.signs)


def build_sensitivities(
    order: Sequence[str],
    values: dict[tuple[str, str, str], int],
    *,
    resamples: int,
    seed: int,
    ambiguous: set[str],
    file_counts: dict[str, dict[str, int]] | None,
    churn: dict[tuple[str, str, str], dict[str, int]] | None,
    commit_totals: dict[tuple[str, str], set[str]] | None,
    commit_categories: dict[tuple[str, str, str], set[str]] | None,
    bot_rows: dict[tuple[str, str], dict[str, Any]] | None,
    signatures: dict[str, tuple[str, ...]] | None,
    window_inputs: dict[str, tuple[list[str], dict[tuple[str, str, str], int]]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    combined: dict[str, list[dict[str, Any]]] = {
        "tests": [],
        "effects": [],
        "bootstrap": [],
        "signs": [],
    }
    statuses: list[dict[str, Any]] = []
    churn_rows: list[dict[str, Any]] = []
    cluster_rows: list[dict[str, Any]] = []
    bot_review_rows: list[dict[str, Any]] = []

    # D7: normalized secondary metrics.
    if file_counts is not None and churn is not None and commit_totals is not None:
        churn_rows, churn_metrics = build_churn_metrics(
            order, values, file_counts, churn, commit_totals
        )
        for metric, metric_values in churn_metrics.items():
            block = infer_block(
                order,
                metric_values,
                sensitivity=f"churn_{metric}",
                resamples=resamples,
                seed=seed,
            )
            _append_block(combined, block)
            statuses.append(
                _row(
                    SENSITIVITY_STATUS_FIELDS,
                    sensitivity=f"churn_{metric}",
                    status="computed",
                    repositories=len(order),
                    detail="D7 normalized metric; raw counts remain primary",
                )
            )
    else:
        statuses.append(
            _row(
                SENSITIVITY_STATUS_FIELDS,
                sensitivity="churn",
                status="skipped",
                repositories="",
                detail="file_changes.csv or commit_activity.csv unavailable",
            )
        )

    # D4: ambiguous adoptions.
    ambiguous_included = [repo for repo in order if repo in ambiguous]
    if ambiguous_included:
        reduced = [repo for repo in order if repo not in ambiguous]
        block = infer_block(
            reduced, values, sensitivity="ambiguous_excluded", resamples=resamples, seed=seed
        )
        _append_block(combined, block)
        statuses.append(
            _row(
                SENSITIVITY_STATUS_FIELDS,
                sensitivity="ambiguous_excluded",
                status="computed",
                repositories=len(reduced),
                detail=f"excluded {len(ambiguous_included)} ambiguous adoptions",
            )
        )
    else:
        statuses.append(
            _row(
                SENSITIVITY_STATUS_FIELDS,
                sensitivity="ambiguous_excluded",
                status="not_applicable",
                repositories=len(order),
                detail="no ambiguous adoptions in the analysis sample",
            )
        )

    # D9: one repository per exact SKILL.md content signature.
    if signatures is not None:
        groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for repo in order:
            groups[signatures[repo]].append(repo)
        representatives = {
            signature: min(repositories) for signature, repositories in groups.items()
        }
        reduced = [repo for repo in order if representatives[signatures[repo]] == repo]
        for repo in order:
            signature = signatures[repo]
            cluster_rows.append(
                _row(
                    CLUSTER_FIELDS,
                    repo_full_name=repo,
                    skill_signature=";".join(signature),
                    cluster_size=len(groups[signature]),
                    cluster_representative=representatives[signature],
                    included_in_sensitivity=str(repo in reduced).lower(),
                )
            )
        block = infer_block(
            reduced,
            values,
            sensitivity="skill_content_clusters",
            resamples=resamples,
            seed=seed,
        )
        _append_block(combined, block)
        statuses.append(
            _row(
                SENSITIVITY_STATUS_FIELDS,
                sensitivity="skill_content_clusters",
                status="computed",
                repositories=len(reduced),
                detail=(
                    f"{len(groups)} unique content signatures from {len(order)} "
                    f"repositories; {len(order) - len(reduced)} dropped"
                ),
            )
        )
    else:
        statuses.append(
            _row(
                SENSITIVITY_STATUS_FIELDS,
                sensitivity="skill_content_clusters",
                status="skipped",
                repositories="",
                detail="skill_contents.csv unavailable",
            )
        )

    # Frozen bot pattern: recount unique commits without bot identities.
    if commit_categories is not None and bot_rows is not None:
        bot_values = _counts_from_commit_sets(order, commit_categories)
        block = infer_block(
            order, bot_values, sensitivity="bots_excluded", resamples=resamples, seed=seed
        )
        _append_block(combined, block)
        for bot in sorted(bot_rows.values(), key=lambda row: (row["repo_full_name"], row["commit"])):
            bot_review_rows.append(
                _row(
                    BOT_FIELDS,
                    repo_full_name=bot["repo_full_name"],
                    commit=bot["commit"],
                    period=bot["period"],
                    categories=";".join(sorted(bot["categories"])),
                    author_name=bot["author_name"],
                    author_email=bot["author_email"],
                    committer_name=bot["committer_name"],
                    committer_email=bot["committer_email"],
                    human_review_decision="",
                    human_notes="",
                )
            )
        statuses.append(
            _row(
                SENSITIVITY_STATUS_FIELDS,
                sensitivity="bots_excluded",
                status="computed",
                repositories=len(order),
                detail=(
                    f"{len(bot_rows)} unique commits matched the frozen bot pattern; "
                    "manual confirmation of matches is recorded in bot_commits_review.csv"
                ),
            )
        )
    else:
        statuses.append(
            _row(
                SENSITIVITY_STATUS_FIELDS,
                sensitivity="bots_excluded",
                status="skipped",
                repositories="",
                detail="commit_activity.csv unavailable",
            )
        )

    # Window sensitivities 30 and 90 days.
    for name, (reduced, window_values) in window_inputs.items():
        block = infer_block(
            reduced, window_values, sensitivity=name, resamples=resamples, seed=seed
        )
        _append_block(combined, block)
        statuses.append(
            _row(
                SENSITIVITY_STATUS_FIELDS,
                sensitivity=name,
                status="computed",
                repositories=len(reduced),
                detail=(
                    "same sample and sealed review; only the activity window changes"
                    if reduced
                    else "no repository had complete windows"
                ),
            )
        )
    for name in ("window_30_days", "window_90_days"):
        if name not in window_inputs:
            statuses.append(
                _row(
                    SENSITIVITY_STATUS_FIELDS,
                    sensitivity=name,
                    status="not_available",
                    repositories="",
                    detail="run process-activity with --window-days and pass the directory",
                )
            )

    return combined, statuses, churn_rows, cluster_rows, bot_review_rows


def load_window_inputs(
    directory: Path | None,
    *,
    order: Sequence[str],
    expected_days: int,
    label: str,
) -> tuple[list[str], dict[tuple[str, str, str], int]] | None:
    if directory is None or not directory.exists():
        return None
    counts_path = directory / "activity_counts.csv"
    if not counts_path.exists():
        return None
    counts_order, values = load_counts(counts_path)
    available = set(counts_order)
    selected = [repo for repo in order if repo in available]
    manifest_path = directory / "processing_manifest.json"
    if expected_days == 90 and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        completeness = {
            row["repo_full_name"]: row
            for row in manifest.get("repositories", [])
            if row.get("repo_full_name")
        }
        selected = [
            repo
            for repo in selected
            if completeness.get(repo, {}).get("pre_window_complete", True)
            and completeness.get(repo, {}).get("post_window_complete", True)
        ]
    elif not manifest_path.exists():
        raise ValueError(
            f"{directory}: processing_manifest.json is required for {label}"
        )
    if not selected:
        return [], {}
    values = {
        (repo, period, category): values[(repo, period, category)]
        for repo in selected
        for period in PERIODS
        for category in ALL_CATEGORIES
    }
    return selected, values


def load_repos_metadata(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    rows: dict[str, dict[str, Any]] = {}
    sources: set[str] = set()
    fetched: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"repo_full_name", "stars", "created_at", "source"}
        if not required.issubset(tuple(reader.fieldnames or ())):
            raise ValueError(f"{path}: missing repository-metadata columns")
        for row in reader:
            rows[row["repo_full_name"]] = row
            if row["source"]:
                sources.add(row["source"])
            if row.get("fetched_at_utc"):
                fetched.add(row["fetched_at_utc"])
    source = "; ".join(sorted(sources)) if sources else str(path)
    if fetched:
        source = f"{source}; fetched_at_utc={'; '.join(sorted(fetched))}"
    return rows, source


def fetch_repos_metadata(
    config: Config, frame_path: Path, output_path: Path
) -> str:
    """Export GitSkills repository metadata for the frozen frame (network)."""
    repos_url = huggingface_url(config, "data/repos/part-00000.parquet")
    fetched_at = utc_now()
    connection = duckdb.connect()
    try:
        connection.execute("SET enable_progress_bar = false")
        connection.execute("INSTALL httpfs")
        connection.execute("LOAD httpfs")
        connection.execute(
            "CREATE TABLE frame AS SELECT * FROM read_csv_auto(?)",
            [str(frame_path)],
        )
        rows = connection.execute(
            f"""
            SELECT f.repo_full_name, r.stars, r.forks, r.created_at, r.language
            FROM frame AS f
            LEFT JOIN read_parquet('{repos_url}') AS r
              ON r.full_name = f.repo_full_name
            ORDER BY f.repo_full_name
            """
        ).fetchall()
    finally:
        connection.close()
    source = (
        f"{config.dataset_repository}@{config.dataset_revision} "
        "data/repos/part-00000.parquet"
    )
    write_csv_atomic(
        output_path,
        list(METADATA_FIELDS),
        [
            {
                "repo_full_name": row[0],
                "stars": "" if row[1] is None else row[1],
                "forks": "" if row[2] is None else row[2],
                "created_at": row[3] or "",
                "language": row[4] or "",
                "source": source,
                "fetched_at_utc": fetched_at,
            }
            for row in rows
        ],
    )
    return source


def fetch_frame_skill_copies(
    config: Config, frame_path: Path, output_path: Path
) -> str:
    """Count snapshot-valid SKILL.md copies per frame repository (network)."""
    artifact_urls = [
        huggingface_url(config, f"data/artifacts/part-{index:05d}.parquet")
        for index in range(config.artifact_parts)
    ]
    # Keep the exact roots used by the frozen extraction protocol.
    pattern = (
        "^("
        + "|".join(
            root.replace(".", "\\.") for root in SKILL_ROOTS
        )
        + ")/([^/]+)/SKILL\\.md$"
    )
    connection = duckdb.connect()
    try:
        connection.execute("SET enable_progress_bar = false")
        connection.execute("INSTALL httpfs")
        connection.execute("LOAD httpfs")
        connection.execute(
            "CREATE TABLE frame AS SELECT * FROM read_csv_auto(?)",
            [str(frame_path)],
        )
        connection.execute(
            f"""
            CREATE TABLE artifact_index AS
            SELECT repo_full_name, path, filename, file_sha, dedup_primary,
                   frontmatter_valid, name, description
            FROM read_parquet({sql_list(artifact_urls)}, union_by_name = true)
            """
        )
        rows = connection.execute(
            f"""
            WITH representatives AS (
                SELECT file_sha, frontmatter_valid, name, description
                FROM artifact_index
                WHERE dedup_primary = 1
            ),
            valid_occurrences AS (
                SELECT a.repo_full_name, a.path, a.file_sha
                FROM artifact_index AS a
                JOIN representatives AS p USING (file_sha)
                WHERE a.filename = 'SKILL.md'
                  AND regexp_matches(a.path, '{pattern}')
                  AND p.frontmatter_valid = 1
                  AND p.name IS NOT NULL
                  AND length(p.name) BETWEEN 1 AND 64
                  AND regexp_matches(p.name, '^[a-z0-9]+(-[a-z0-9]+)*$')
                  AND p.name = regexp_extract(a.path, '{pattern}', 2)
                  AND p.description IS NOT NULL
                  AND length(p.description) BETWEEN 1 AND 1024
            ),
            copies AS (
                SELECT v.file_sha, count(DISTINCT v.repo_full_name) AS copies
                FROM valid_occurrences AS v
                JOIN frame AS f ON f.repo_full_name = v.repo_full_name
                GROUP BY v.file_sha
            )
            SELECT f.repo_full_name,
                   count(v.path) AS skill_count,
                   coalesce(sum(CASE WHEN c.copies >= 2 THEN 1 ELSE 0 END), 0)
                       AS copied_count
            FROM frame AS f
            JOIN valid_occurrences AS v ON v.repo_full_name = f.repo_full_name
            JOIN copies AS c USING (file_sha)
            GROUP BY f.repo_full_name
            ORDER BY f.repo_full_name
            """
        ).fetchall()
    finally:
        connection.close()
    source = (
        f"{config.dataset_repository}@{config.dataset_revision} "
        "artifacts (file_sha, snapshot)"
    )
    write_csv_atomic(
        output_path,
        list(FRAME_COPY_FIELDS),
        [
            {
                "repo_full_name": row[0],
                "skill_count_snapshot": row[1],
                "copied_skill_count_snapshot": row[2],
            }
            for row in rows
        ],
    )
    return source


def _distribution_rows(
    group: str,
    variable: str,
    values: list[float],
    *,
    unit: str,
    source: str,
    notes: str = "",
) -> dict[str, Any]:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    n_total = len(values)
    if not finite:
        return _row(
            REPRESENTATIVENESS_FIELDS,
            group=group,
            variable=variable,
            n_repositories=n_total,
            n_available=0,
            n_missing=n_total,
            median="",
            q1="",
            q3="",
            iqr="",
            minimum="",
            maximum="",
            unit=unit,
            source=source,
            notes=notes or "missing; no value imputed",
        )
    q1 = quantile(finite, 0.25)
    q3 = quantile(finite, 0.75)
    return _row(
        REPRESENTATIVENESS_FIELDS,
        group=group,
        variable=variable,
        n_repositories=n_total,
        n_available=len(finite),
        n_missing=n_total - len(finite),
        median=quantile(finite, 0.5),
        q1=q1,
        q3=q3,
        iqr=q3 - q1,
        minimum=finite[0],
        maximum=finite[-1],
        unit=unit,
        source=source,
        notes=notes,
    )


def build_representativeness(
    *,
    config: Config,
    frame_path: Path,
    order: Sequence[str],
    selected_counts: dict[str, dict[str, int]],
    signatures: dict[str, tuple[str, ...]] | None,
    metadata_path: Path | None,
    frame_copies_path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with frame_path.open(newline="", encoding="utf-8") as handle:
        frame_rows = list(csv.DictReader(handle))
    frame_names = [row["repo_full_name"] for row in frame_rows]
    frame_skill = {
        row["repo_full_name"]: float(row["eligible_skill_count"]) for row in frame_rows
    }
    sample_set = set(order)

    metadata: dict[str, dict[str, Any]] = {}
    metadata_source = "not provided; stars and age reported as missing"
    if metadata_path is not None and metadata_path.exists():
        metadata, metadata_source = load_repos_metadata(metadata_path)

    copies: dict[str, tuple[float, float]] = {}
    copies_source = "not provided; verbatim-copy fraction reported as missing"
    if frame_copies_path is not None and frame_copies_path.exists():
        with frame_copies_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                total = float(row["skill_count_snapshot"])
                copied = float(row["copied_skill_count_snapshot"])
                copies[row["repo_full_name"]] = (total, copied)
        copies_source = "GitSkills snapshot file_sha groups (two or more repositories)"

    rows: list[dict[str, Any]] = []
    cutoff = config.cutoff
    for group, names in (("frame", frame_names), ("sample", list(order))):
        stars: list[float] = []
        ages: list[float] = []
        skills: list[float] = []
        copied_fractions: list[float] = []
        for repo in names:
            metadata_row = metadata.get(repo)
            if metadata_row:
                try:
                    stars.append(float(metadata_row["stars"]))
                except (TypeError, ValueError):
                    stars.append(math.nan)
                created = metadata_row.get("created_at", "")
                if created:
                    try:
                        age = (cutoff - parse_datetime(created)).total_seconds() / 86400.0
                    except ValueError:
                        age = math.nan
                else:
                    age = math.nan
                ages.append(age)
            else:
                stars.append(math.nan)
                ages.append(math.nan)
            if repo in frame_skill:
                skills.append(frame_skill[repo])
            elif group == "sample":
                skills.append(float(selected_counts[repo]["skill"]))
            else:
                skills.append(math.nan)
            if repo in copies:
                total, copied = copies[repo]
                copied_fractions.append(copied / total if total else math.nan)
            elif group == "sample" and signatures is not None:
                signature = signatures.get(repo)
                if signature and not signature[0].startswith("missing:"):
                    # At t0, compare exact hashes inside the sample when the
                    # snapshot artifact table is unavailable.
                    hash_repo_count: dict[str, int] = defaultdict(int)
                    for other in order:
                        for digest in set(signatures.get(other, ())):
                            hash_repo_count[digest] += 1
                    distinct = set(signature)
                    copied = sum(
                        1 for digest in distinct if hash_repo_count[digest] >= 2
                    )
                    copied_fractions.append(copied / len(distinct))
                    if copies_source.startswith("not provided"):
                        copies_source = (
                            "t0 SKILL.md content_sha256 within the analysis sample "
                            "(fallback; snapshot frame table unavailable)"
                        )
                else:
                    copied_fractions.append(math.nan)
            else:
                copied_fractions.append(math.nan)

        rows.append(
            _distribution_rows(
                group,
                "stars",
                stars,
                unit="GitHub stars at snapshot",
                source=metadata_source,
                notes="missing values are not imputed",
            )
        )
        rows.append(
            _distribution_rows(
                group,
                "repository_age_days_at_cutoff",
                ages,
                unit="days",
                source=metadata_source,
                notes="cutoff 2026-10-04T23:59:59Z; E is older than the frame by design",
            )
        )
        rows.append(
            _distribution_rows(
                group,
                "eligible_skill_count",
                skills,
                unit="valid SKILL.md",
                source="candidate_frame.csv (GitSkills snapshot)",
                notes="same snapshot definition for both groups",
            )
        )
        rows.append(
            _distribution_rows(
                group,
                "verbatim_skill_fraction",
                copied_fractions,
                unit="fraction of valid SKILL.md",
                source=copies_source,
                notes="0 when no valid skill is copied; no imputation",
            )
        )

    detail = {
        "frame_size": len(frame_names),
        "sample_size": len(order),
        "sample_is_frame_subset": sample_set.issubset(set(frame_names)),
        "metadata_source": metadata_source,
        "copies_source": copies_source,
        "missing_repositories_in_metadata": sum(
            1 for repo in order if repo not in metadata
        ),
        "interpretation": (
            "Descriptive only. No significance tests are computed against the "
            "frame because the sample is drawn from E, not directly from the frame."
        ),
    }
    return rows, detail


def render_consistency(
    order: Sequence[str],
    values: dict[tuple[str, str, str], int],
    *,
    tests: list[dict[str, Any]],
    effects: list[dict[str, Any]],
    bootstrap: list[dict[str, Any]],
    signs: list[dict[str, Any]],
    sensitivity: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    by_repo = {(repo, period, category): values[(repo, period, category)] for repo in order for period in PERIODS for category in RQ1_CATEGORIES}
    lines = [
        "Consistency checks for the inferential RQ1/RQ2 analysis",
        "",
        f"repositories: {len(order)}",
        f"tests reported: {len(tests)}",
        f"effects reported: {len(effects)}",
        f"bootstrap intervals: {len(bootstrap)}",
        f"sign tests: {len(signs)}",
        "",
        "Primary RQ1/RQ2 tests reconstructed from activity_counts.csv:",
    ]

    for test in [row for row in tests if row["sensitivity"] == "primary"]:
        if test["test"] == "friedman":
            columns = [
                np.asarray([by_repo[(repo, "post", category)] for repo in order], dtype=float)
                for category in RQ1_CATEGORIES
            ]
            expected = friedman_test(columns)
            observed = _as_float(test["statistic"])
            status = "OK" if math.isclose(observed, expected["statistic"], rel_tol=1e-9, abs_tol=1e-9) else "MISMATCH"
            lines.append(
                f"  {status}  RQ1 Friedman statistic {observed} (expected {expected['statistic']})"
            )
            p_status = "OK" if math.isclose(_as_float(test["p_value"]), expected["p_value"], rel_tol=1e-9, abs_tol=1e-12) else "MISMATCH"
            lines.append(f"  {p_status}  RQ1 Friedman p {test['p_value']} (expected {expected['p_value']})")
        elif test["test"] == "wilcoxon" and test["rq"] == "RQ1":
            first, second = test["comparison"].split("-")
            differences = [
                by_repo[(repo, "post", second)] - by_repo[(repo, "post", first)]
                for repo in order
            ]
            expected = paired_wilcoxon(differences)
            status = "OK" if math.isclose(_as_float(test["p_value"]), expected["p_value"], rel_tol=1e-9, abs_tol=1e-12) else "MISMATCH"
            lines.append(f"  {status}  RQ1 Wilcoxon {test['comparison']} p {test['p_value']}")
        elif test["test"] == "wilcoxon" and test["rq"] == "RQ2":
            category = test["comparison"].replace("_post_minus_pre", "")
            differences = [
                by_repo[(repo, "post", category)] - by_repo[(repo, "pre", category)]
                for repo in order
            ]
            expected = paired_wilcoxon(differences)
            status = "OK" if math.isclose(_as_float(test["p_value"]), expected["p_value"], rel_tol=1e-9, abs_tol=1e-12) else "MISMATCH"
            lines.append(f"  {status}  RQ2 Wilcoxon {category} p {test['p_value']}")

    lines.extend(["", "Effect sizes reconstructed from activity_counts.csv:"])
    for effect in [row for row in effects if row["sensitivity"] == "primary"]:
        if effect["rq"] == "RQ1":
            first, second = effect["comparison"].split("-")
            differences = [
                by_repo[(repo, "post", second)] - by_repo[(repo, "post", first)]
                for repo in order
            ]
            expected_hl = hodges_lehmann(differences)
            expected_r = rank_biserial(differences)
            hl_ok = math.isclose(_as_float(effect["hodges_lehmann"]), expected_hl, rel_tol=1e-9, abs_tol=1e-12)
            r_ok = math.isclose(_as_float(effect["rank_biserial"]), expected_r, rel_tol=1e-9, abs_tol=1e-12)
            status = "OK" if hl_ok and r_ok else "MISMATCH"
            lines.append(
                f"  {status}  RQ1 {effect['comparison']} HL {effect['hodges_lehmann']} "
                f"(expected {expected_hl}); r_rb {effect['rank_biserial']} (expected {expected_r})"
            )
        else:
            category = effect["comparison"].replace("_post_minus_pre", "")
            differences = [
                by_repo[(repo, "post", category)] - by_repo[(repo, "pre", category)]
                for repo in order
            ]
            expected_hl = hodges_lehmann(differences)
            expected_r = rank_biserial(differences)
            hl_ok = math.isclose(_as_float(effect["hodges_lehmann"]), expected_hl, rel_tol=1e-9, abs_tol=1e-12)
            r_ok = math.isclose(_as_float(effect["rank_biserial"]), expected_r, rel_tol=1e-9, abs_tol=1e-12)
            status = "OK" if hl_ok and r_ok else "MISMATCH"
            lines.append(
                f"  {status}  RQ2 {category} HL {effect['hodges_lehmann']} "
                f"(expected {expected_hl}); r_rb {effect['rank_biserial']} (expected {expected_r})"
            )

    lines.extend(["", "Bootstrap intervals and Holm adjustments:"])
    for row in bootstrap:
        if row["sensitivity"] != "primary":
            continue
        low = _as_float(row["ci_low"])
        high = _as_float(row["ci_high"])
        point = _as_float(row["point_estimate"])
        if math.isfinite(low) and math.isfinite(high) and math.isfinite(point):
            status = "OK" if low <= high else "MISMATCH"
            lines.append(
                f"  {status}  bootstrap {row['rq']} {row['quantity']} "
                f"{row['comparison'] or row['category']}: [{low}, {high}] contains point {point}"
            )
    for rq in ("RQ1", "RQ2"):
        family = [row for row in tests if row["sensitivity"] == "primary" and row["rq"] == rq and row["p_adjusted"] != "" and row["test"] == "wilcoxon"]
        raw = [_as_float(row["p_value"]) for row in family]
        adjusted = [_as_float(row["p_adjusted"]) for row in family]
        valid = all(math.isfinite(value) for value in raw + adjusted)
        monotone = all(a >= b - 1e-12 for a, b in zip(adjusted, raw)) if valid else False
        status = "OK" if valid and monotone else "MISMATCH"
        lines.append(f"  {status}  {rq} Holm adjusted p-values are >= raw p-values")

    if sensitivity:
        lines.extend(["", "Sensitivity output invariants:"])
        for name in sorted({row["sensitivity"] for row in sensitivity.get("tests", [])}):
            family = [
                row
                for row in sensitivity.get("tests", [])
                if row["sensitivity"] == name
                and row["test"] == "wilcoxon"
                and row["p_adjusted"] != ""
            ]
            valid = all(
                math.isfinite(_as_float(row["p_value"]))
                and math.isfinite(_as_float(row["p_adjusted"]))
                for row in family
            )
            monotone = all(
                _as_float(row["p_adjusted"]) >= _as_float(row["p_value"]) - 1e-12
                for row in family
            )
            status = "OK" if valid and monotone else "MISMATCH"
            lines.append(
                f"  {status}  {name}: adjusted p-values are >= raw p-values"
            )
        for row in sensitivity.get("effects", []):
            hl = _as_float(row["hodges_lehmann"])
            direction = row["direction"]
            if not math.isfinite(hl):
                continue
            expected = (
                row["second"] + "_greater_than_" + row["first"]
                if hl > 0
                else (
                    row["first"] + "_greater_than_" + row["second"]
                    if hl < 0
                    else "no_difference"
                )
            )
            status = "OK" if direction == expected else "MISMATCH"
            lines.append(
                f"  {status}  {row['sensitivity']} {row['rq']} {row['comparison']}: "
                f"direction matches Hodges-Lehmann sign"
            )
        for row in sensitivity.get("bootstrap", []):
            low = _as_float(row["ci_low"])
            high = _as_float(row["ci_high"])
            status = "OK" if low <= high else "MISMATCH"
            lines.append(
                f"  {status}  {row['sensitivity']} bootstrap {row['quantity']}: "
                f"CI low <= high"
            )
        for row in sensitivity.get("signs", []):
            raw = _as_float(row["p_value"])
            adjusted = _as_float(row["p_adjusted"])
            status = "OK" if adjusted >= raw - 1e-12 else "MISMATCH"
            lines.append(
                f"  {status}  {row['sensitivity']} sign {row['comparison']}: "
                "adjusted p-value >= raw p-value"
            )

    lines.extend(
        [
            "",
            "All p-values, effect sizes and intervals are reconstructed from the",
            "preserved CSV inputs; no value is copied from the manuscript.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_outputs(
    output_directory: Path,
    *,
    primary: BlockResults,
    combined: dict[str, list[dict[str, Any]]],
    statuses: list[dict[str, Any]],
    churn_rows: list[dict[str, Any]],
    cluster_rows: list[dict[str, Any]],
    bot_review_rows: list[dict[str, Any]],
    representativeness_rows: list[dict[str, Any]],
) -> None:
    write_csv_atomic(output_directory / "rq1_tests.csv", list(TEST_FIELDS), [row for row in primary.tests if row["rq"] == "RQ1"])
    write_csv_atomic(output_directory / "rq2_tests.csv", list(TEST_FIELDS), [row for row in primary.tests if row["rq"] == "RQ2"])
    write_csv_atomic(output_directory / "effect_sizes.csv", list(EFFECT_FIELDS), primary.effects)
    write_csv_atomic(output_directory / "bootstrap_ci.csv", list(BOOTSTRAP_FIELDS), primary.bootstrap)
    write_csv_atomic(output_directory / "sign_tests.csv", list(SIGN_FIELDS), primary.signs)
    write_csv_atomic(
        output_directory / "sensitivity_tests.csv",
        list(TEST_FIELDS),
        combined["tests"],
    )
    write_csv_atomic(
        output_directory / "sensitivity_effects.csv",
        list(EFFECT_FIELDS),
        combined["effects"],
    )
    write_csv_atomic(
        output_directory / "sensitivity_ci.csv",
        list(BOOTSTRAP_FIELDS),
        combined["bootstrap"],
    )
    write_csv_atomic(
        output_directory / "sensitivity_sign_tests.csv",
        list(SIGN_FIELDS),
        combined["signs"],
    )
    write_csv_atomic(
        output_directory / "sensitivity_status.csv",
        list(SENSITIVITY_STATUS_FIELDS),
        statuses,
    )
    write_csv_atomic(output_directory / "sensitivity_churn.csv", list(CHURN_FIELDS), churn_rows)
    write_csv_atomic(
        output_directory / "skill_cluster_assignment.csv",
        list(CLUSTER_FIELDS),
        cluster_rows,
    )
    write_csv_atomic(
        output_directory / "bot_commits_review.csv",
        list(BOT_FIELDS),
        bot_review_rows,
    )
    write_csv_atomic(
        output_directory / "sample_representativeness.csv",
        list(REPRESENTATIVENESS_FIELDS),
        representativeness_rows,
    )


def execute(
    config: Config,
    results_directory: Path,
    processed_directory: Path,
    output_directory: Path,
    *,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    processed_30: Path | None = None,
    processed_90: Path | None = None,
    repos_metadata: Path | None = None,
    fetch_representation: bool = False,
) -> None:
    if output_directory.exists():
        raise FileExistsError(
            f"Output directory already exists: {output_directory}. Use a new directory."
        )
    output_directory.mkdir(parents=True, exist_ok=False)

    counts_path = processed_directory / "activity_counts.csv"
    order_all, values_all = load_counts(counts_path)
    analysis_sample_path = results_directory / "analysis_sample.csv"
    order, ambiguous, decisions, reviewed_count = load_analysis_sample(
        analysis_sample_path, order_all
    )
    values = {
        (repo, period, category): values_all[(repo, period, category)]
        for repo in order
        for period in PERIODS
        for category in ALL_CATEGORIES
    }

    analysis_manifest_path = results_directory / "analysis_manifest.json"
    analysis_manifest: dict[str, Any] = {}
    if analysis_manifest_path.exists():
        analysis_manifest = json.loads(analysis_manifest_path.read_text(encoding="utf-8"))
        recorded = analysis_manifest.get("input_sha256", {}).get("activity_counts")
        if recorded and recorded != file_sha256(counts_path):
            raise ValueError(
                f"{counts_path}: checksum differs from results/analysis_manifest.json"
            )

    selected_path = config.output_directory / "selected_repositories.csv"
    file_counts = load_selected_file_counts(selected_path, order)

    changes_path = processed_directory / "file_changes.csv"
    commit_path = processed_directory / "commit_activity.csv"
    skills_path = processed_directory / "skill_contents.csv"
    churn = load_churn(changes_path, order) if changes_path.exists() else None
    totals, categories, bots = (
        load_commit_index(commit_path, order) if commit_path.exists() else (None, None, None)
    )
    signatures = load_skill_signatures(skills_path, order) if skills_path.exists() else None

    window_inputs: dict[str, tuple[list[str], dict[tuple[str, str, str], int]]] = {}
    for label, expected_days, directory in (
        ("window_30_days", 30, processed_30),
        ("window_90_days", 90, processed_90),
    ):
        loaded = load_window_inputs(
            directory,
            order=order,
            expected_days=expected_days,
            label=label,
        )
        if loaded is not None:
            window_inputs[label] = loaded

    primary = infer_block(order, values, sensitivity="primary", resamples=resamples, seed=seed)
    (
        combined,
        statuses,
        churn_rows,
        cluster_rows,
        bot_review_rows,
    ) = build_sensitivities(
        order,
        values,
        resamples=resamples,
        seed=seed,
        ambiguous=ambiguous,
        file_counts=file_counts,
        churn=churn,
        commit_totals=totals,
        commit_categories=categories,
        bot_rows=bots,
        signatures=signatures,
        window_inputs=window_inputs,
    )

    frame_path = config.output_directory / "candidate_frame.csv"
    metadata_path = repos_metadata
    frame_copies_path: Path | None = None
    representativeness_rows: list[dict[str, Any]] = []
    representativeness_detail: dict[str, Any] = {}
    if fetch_representation:
        if not frame_path.exists():
            raise FileNotFoundError(f"Cannot fetch representativeness without {frame_path}")
        metadata_path = output_directory / "repos_metadata.csv"
        fetch_repos_metadata(config, frame_path, metadata_path)
        frame_copies_path = output_directory / "frame_skill_copies.csv"
        fetch_frame_skill_copies(config, frame_path, frame_copies_path)
    if frame_path.exists():
        representativeness_rows, representativeness_detail = build_representativeness(
            config=config,
            frame_path=frame_path,
            order=order,
            selected_counts=file_counts,
            signatures=signatures,
            metadata_path=metadata_path,
            frame_copies_path=frame_copies_path,
        )
    else:
        representativeness_detail = {"status": "skipped", "detail": "candidate_frame.csv unavailable"}

    _write_outputs(
        output_directory,
        primary=primary,
        combined=combined,
        statuses=statuses,
        churn_rows=churn_rows,
        cluster_rows=cluster_rows,
        bot_review_rows=bot_review_rows,
        representativeness_rows=representativeness_rows,
    )

    consistency = render_consistency(
        order,
        values,
        tests=primary.tests,
        effects=primary.effects,
        bootstrap=primary.bootstrap,
        signs=primary.signs,
        sensitivity=combined,
    )
    (output_directory / "consistency_checks.txt").write_text(consistency, encoding="utf-8")

    input_paths: dict[str, Path] = {
        "activity_counts": counts_path,
        "analysis_sample": analysis_sample_path,
    }
    for optional in (
        changes_path,
        commit_path,
        skills_path,
        selected_path,
        frame_path,
        analysis_manifest_path,
        metadata_path,
        frame_copies_path,
    ):
        if optional is not None and optional.exists():
            input_paths[optional.name] = optional
    if processed_30 is not None and processed_30.exists():
        input_paths["activity_counts_30"] = processed_30 / "activity_counts.csv"
    if processed_90 is not None and processed_90.exists():
        input_paths["activity_counts_90"] = processed_90 / "activity_counts.csv"

    outputs = [
        "rq1_tests.csv",
        "rq2_tests.csv",
        "effect_sizes.csv",
        "bootstrap_ci.csv",
        "sign_tests.csv",
        "sensitivity_tests.csv",
        "sensitivity_effects.csv",
        "sensitivity_ci.csv",
        "sensitivity_sign_tests.csv",
        "sensitivity_status.csv",
        "sensitivity_churn.csv",
        "skill_cluster_assignment.csv",
        "bot_commits_review.csv",
        "sample_representativeness.csv",
        "consistency_checks.txt",
    ]
    for optional in (metadata_path, frame_copies_path):
        if optional is not None and optional.exists() and optional.name not in outputs:
            outputs.append(optional.name)
    manifest = {
        "created_at_utc": utc_now(),
        "inference_protocol_version": INFERENCE_PROTOCOL_VERSION,
        "frozen_plan": "docs/analysis-plan.md v1.1 (2026-10-22)",
        "configuration": {
            "extraction": serializable_config(config),
            "results_directory": str(results_directory.resolve()),
            "processed_directory": str(processed_directory.resolve()),
            "output_directory": str(output_directory.resolve()),
            "alpha": ALPHA,
            "alternative": "two-sided for Wilcoxon and sign tests; Friedman upper tail by construction",
            "bootstrap_resamples": resamples,
            "bootstrap_seed": seed,
            "bootstrap_percentiles": list(BOOTSTRAP_PERCENTILES),
            "rq1_categories": list(RQ1_CATEGORIES),
            "rq2_categories": list(RQ2_CATEGORIES),
            "rq1_pairs": [list(pair) for pair in RQ1_PAIRS],
            "adoption_policy": "exclude invalid; keep ambiguous flagged; no replacement",
            "multiple_comparisons": HOLM_DEFINITION,
            "wilcoxon": WILCOXON_DEFINITION,
            "sign_test": SIGN_DEFINITION,
            "effects": EFFECT_DEFINITION,
            "confidence_intervals": CI_DEFINITION,
            "sensitivity_policy": "report after the primary result; never replace it",
            "placebo": (
                "none (D8); a pre-adoption trend remains a threat to validity and "
                "is not tested"
            ),
            "merges": "excluded before counts by process-activity; no separate sensitivity",
        },
        "tool_versions": {
            "git": git_version(config),
            "python": sys.version,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "duckdb": duckdb.__version__,
        },
        "input_sha256": {name: file_sha256(path) for name, path in input_paths.items()},
        "output_sha256": {
            name: file_sha256(output_directory / name) for name in outputs
        },
        "repository_count": len(order),
        "reviewed_repository_count": reviewed_count,
        "ambiguous_count": len(ambiguous),
        "excluded_invalid_count": sum(
            1 for decision in decisions.values() if decision == "invalid"
        ),
        "analysis_sample_sha256": file_sha256(analysis_sample_path),
        "sample_estimate": analysis_manifest.get("sample_estimate"),
        "tests": {
            "rq1_global_and_pairs": len(primary.tests) - len([row for row in primary.tests if row["rq"] == "RQ2"]),
            "rq2": len([row for row in primary.tests if row["rq"] == "RQ2"]),
            "sign_tests": len(primary.signs),
        },
        "representativeness": representativeness_detail,
        "sensitivity_status": statuses,
        "bot_pattern_matches": {
            "unique_commits": len(bot_review_rows),
            "manual_review_file": "bot_commits_review.csv",
            "manual_review_status": "pending" if bot_review_rows else "not_applicable",
        },
        "software_requirement": "SciPy is pinned in pyproject.toml and uv.lock",
        "generalization": "Only to E; no causal reading is licensed by this design.",
    }
    (output_directory / "inference_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen inferential RQ1/RQ2 analysis."
    )
    parser.add_argument("--config", type=Path, default=Path("extraction.toml"))
    parser.add_argument(
        "--results-directory",
        type=Path,
        default=Path("results"),
        help="Descriptive results directory containing analysis_sample.csv",
    )
    parser.add_argument(
        "--processed-directory",
        type=Path,
        default=Path("data/processed"),
        help="Processed activity directory (60-day primary window)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results-inference"),
        help="New output directory; it must not already exist",
    )
    parser.add_argument(
        "--bootstrap-resamples",
        type=int,
        default=DEFAULT_BOOTSTRAP_RESAMPLES,
    )
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    parser.add_argument(
        "--processed-30",
        type=Path,
        default=None,
        help="Optional 30-day processing directory (not included unless passed)",
    )
    parser.add_argument(
        "--processed-90",
        type=Path,
        default=None,
        help="Optional 90-day processing directory (not included unless passed)",
    )
    parser.add_argument(
        "--repos-metadata",
        type=Path,
        default=None,
        help="Optional CSV with stars, created_at and source for frame repositories",
    )
    parser.add_argument(
        "--fetch-representation",
        action="store_true",
        help="Fetch stars/age and snapshot verbatim-copy counts from GitSkills",
    )
    return parser


def main() -> None:
    args = argument_parser().parse_args()
    config = load_config(args.config)
    execute(
        config,
        args.results_directory.resolve(),
        args.processed_directory.resolve(),
        args.output.resolve(),
        resamples=args.bootstrap_resamples,
        seed=args.bootstrap_seed,
        processed_30=args.processed_30.resolve() if args.processed_30 else None,
        processed_90=args.processed_90.resolve() if args.processed_90 else None,
        repos_metadata=args.repos_metadata.resolve() if args.repos_metadata else None,
        fetch_representation=args.fetch_representation,
    )


if __name__ == "__main__":
    main()
