"""Tests for the Stage 3 plotting utility."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from conftest import write_csv


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "plot_results.py"
RQ1_FIELDS = (
    "repo_full_name",
    "skill_commits",
    "production_commits",
    "test_commits",
    "largest_count",
    "largest_categories",
    "tie",
)
RQ2_FIELDS = (
    "repo_full_name",
    "production_pre",
    "production_post",
    "production_difference",
    "test_pre",
    "test_post",
    "test_difference",
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
STATUS_FIELDS = ("sensitivity", "status", "repositories", "detail")


def load_plotting():
    spec = importlib.util.spec_from_file_location("plot_results", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plotting = load_plotting()


def bootstrap_row(
    quantity: str,
    rq: str,
    comparison: str,
    category: str,
    estimate: float,
    low: float,
    high: float,
    sensitivity: str = "primary",
) -> dict[str, object]:
    return {
        "sensitivity": sensitivity,
        "rq": rq,
        "quantity": quantity,
        "comparison": comparison,
        "category": category,
        "point_estimate": estimate,
        "ci_low": low,
        "ci_high": high,
        "degenerate": "false",
        "resamples": 10000,
        "seed": 20261020,
    }


def effect_row(
    sensitivity: str,
    rq: str,
    comparison: str,
    first: str,
    second: str,
    median_first: float,
    median_second: float,
    hl: float,
    direction: str,
) -> dict[str, object]:
    return {
        "sensitivity": sensitivity,
        "rq": rq,
        "comparison": comparison,
        "first": first,
        "second": second,
        "n_nonzero": 3,
        "median_first": median_first,
        "median_second": median_second,
        "rank_biserial": 0.5,
        "hodges_lehmann": hl,
        "direction": direction,
        "note": "",
    }


def wilcoxon_row(
    sensitivity: str,
    rq: str,
    comparison: str,
    p_adjusted: float,
    significant: bool,
) -> dict[str, object]:
    return {
        "sensitivity": sensitivity,
        "rq": rq,
        "test": "wilcoxon",
        "comparison": comparison,
        "n_blocks": 3,
        "n_nonzero": 3,
        "df": "",
        "statistic": 1.0,
        "p_value": p_adjusted,
        "p_adjusted": p_adjusted,
        "adjusted_family": rq.lower(),
        "significant": str(significant).lower(),
        "note": "",
    }


def sign_row(
    rq: str,
    comparison: str,
    positive: int,
    n_nonzero: int,
    p_adjusted: float,
    significant: bool,
    robust: bool,
    sensitivity: str = "primary",
) -> dict[str, object]:
    proportion = positive / n_nonzero
    return {
        "sensitivity": sensitivity,
        "rq": rq,
        "comparison": comparison,
        "n_nonzero": n_nonzero,
        "positive": positive,
        "negative": n_nonzero - positive,
        "positive_proportion": proportion,
        "ci_low": max(0.0, proportion - 0.1),
        "ci_high": min(1.0, proportion + 0.1),
        "p_value": p_adjusted,
        "p_adjusted": p_adjusted,
        "significant": str(significant).lower(),
        "wilcoxon_significant": str(significant).lower(),
        "concordant_with_wilcoxon": str(robust).lower(),
        "robust": str(robust).lower(),
        "note": "",
    }


@pytest.fixture
def stage3(tmp_path: Path) -> tuple[Path, Path]:
    results = tmp_path / "results"
    inference = tmp_path / "results-inference"
    results.mkdir()
    inference.mkdir()

    write_csv(
        results / "rq1_repository_counts.csv",
        RQ1_FIELDS,
        [
            {
                "repo_full_name": "a/b",
                "skill_commits": 0,
                "production_commits": 5,
                "test_commits": 2,
                "largest_count": 5,
                "largest_categories": "production",
                "tie": "false",
            },
            {
                "repo_full_name": "c/d",
                "skill_commits": 2,
                "production_commits": 0,
                "test_commits": 1,
                "largest_count": 2,
                "largest_categories": "skill",
                "tie": "false",
            },
            {
                "repo_full_name": "e/f",
                "skill_commits": 1,
                "production_commits": 3,
                "test_commits": 0,
                "largest_count": 3,
                "largest_categories": "production",
                "tie": "false",
            },
        ],
    )
    write_csv(
        results / "rq2_repository_differences.csv",
        RQ2_FIELDS,
        [
            {
                "repo_full_name": "a/b",
                "production_pre": 1,
                "production_post": 5,
                "production_difference": 4,
                "test_pre": 1,
                "test_post": 2,
                "test_difference": 1,
            },
            {
                "repo_full_name": "c/d",
                "production_pre": 4,
                "production_post": 0,
                "production_difference": -4,
                "test_pre": 0,
                "test_post": 1,
                "test_difference": 1,
            },
            {
                "repo_full_name": "e/f",
                "production_pre": 2,
                "production_post": 3,
                "production_difference": 1,
                "test_pre": 2,
                "test_post": 0,
                "test_difference": -2,
            },
        ],
    )

    write_csv(
        inference / "bootstrap_ci.csv",
        BOOTSTRAP_FIELDS,
        [
            bootstrap_row("median", "RQ1", "", "skill", 0, 0, 1),
            bootstrap_row("median", "RQ1", "", "production", 1.5, 0.5, 3),
            bootstrap_row("median", "RQ1", "", "test", 1, 0, 2),
            bootstrap_row("median_difference", "RQ2", "", "production", 1, 0, 2),
            bootstrap_row("median_difference", "RQ2", "", "test", 0, -1, 1),
            bootstrap_row("hodges_lehmann", "RQ1", "skill-production", "", 2, 1, 3),
            bootstrap_row("hodges_lehmann", "RQ1", "skill-test", "", 1, 0, 2),
            bootstrap_row("hodges_lehmann", "RQ1", "production-test", "", -1, -2, 0),
            bootstrap_row("hodges_lehmann", "RQ2", "production", "", 1, 0, 2),
            bootstrap_row("hodges_lehmann", "RQ2", "test", "", 0, -1, 1),
        ],
    )
    write_csv(
        inference / "effect_sizes.csv",
        EFFECT_FIELDS,
        [
            effect_row(
                "primary", "RQ1", "skill-production", "skill", "production", 0, 1.5, 2,
                "production_greater_than_skill",
            ),
            effect_row(
                "primary", "RQ1", "skill-test", "skill", "test", 0, 1, 1,
                "test_greater_than_skill",
            ),
            effect_row(
                "primary", "RQ1", "production-test", "production", "test", 1.5, 1, -1,
                "production_greater_than_test",
            ),
            effect_row(
                "primary", "RQ2", "production_post_minus_pre", "pre", "post", 1, 3, 1,
                "post_greater_than_pre",
            ),
            effect_row(
                "primary", "RQ2", "test_post_minus_pre", "pre", "post", 1, 2, 0,
                "no_difference",
            ),
        ],
    )
    write_csv(
        inference / "rq1_tests.csv",
        TEST_FIELDS,
        [
            wilcoxon_row("primary", "RQ1", "skill-production", 0.003, True),
            wilcoxon_row("primary", "RQ1", "skill-test", 0.02, True),
            wilcoxon_row("primary", "RQ1", "production-test", 0.02, True),
        ],
    )
    write_csv(
        inference / "rq2_tests.csv",
        TEST_FIELDS,
        [
            wilcoxon_row("primary", "RQ2", "production_post_minus_pre", 0.5, False),
            wilcoxon_row("primary", "RQ2", "test_post_minus_pre", 0.6, False),
        ],
    )

    sensitivity_effects = []
    sensitivity_ci = []
    sensitivity_tests = []
    sensitivity_names = (
        "churn_commits_per_file",
        "churn_churn_per_file",
        "churn_commit_share",
        "skill_content_clusters",
        "bots_excluded",
    )
    for sensitivity, values in zip(
        sensitivity_names,
        (
            (-1.0, 0.0, 0.5),
            (1.0, 2.0, 3.0),
            (0.0, 0.5, 1.0),
            (-1.0, 0.0, 0.5),
            (1.0, 2.0, 3.0),
        ),
    ):
        for comparison, first, second, hl in zip(
            ("skill-production", "skill-test", "production-test"),
            ("skill", "skill", "production"),
            ("production", "test", "test"),
            values,
        ):
            sensitivity_effects.append(
                effect_row(
                    sensitivity,
                    "RQ1",
                    comparison,
                    first,
                    second,
                    0,
                    1,
                    hl,
                    "no_difference",
                )
            )
        sensitivity_ci.extend(
            [
                bootstrap_row(
                    "hodges_lehmann",
                    "RQ1",
                    "skill-production",
                    "",
                    values[0],
                    values[0] - 0.5,
                    values[0] + 0.5,
                    sensitivity,
                ),
                bootstrap_row(
                    "hodges_lehmann",
                    "RQ1",
                    "skill-test",
                    "",
                    values[1],
                    values[1] - 0.5,
                    values[1] + 0.5,
                    sensitivity,
                ),
                bootstrap_row(
                    "hodges_lehmann",
                    "RQ1",
                    "production-test",
                    "",
                    values[2],
                    values[2] - 0.5,
                    values[2] + 0.5,
                    sensitivity,
                ),
            ]
        )
        for comparison, significant in (
            ("skill-production", True),
            ("skill-test", False),
            ("production-test", True),
        ):
            sensitivity_tests.append(
                wilcoxon_row(
                    sensitivity,
                    "RQ1",
                    comparison,
                    0.01 if significant else 0.5,
                    significant,
                )
            )

    write_csv(inference / "sensitivity_effects.csv", EFFECT_FIELDS, sensitivity_effects)
    write_csv(inference / "sensitivity_ci.csv", BOOTSTRAP_FIELDS, sensitivity_ci)
    write_csv(inference / "sensitivity_tests.csv", TEST_FIELDS, sensitivity_tests)
    write_csv(
        inference / "sensitivity_status.csv",
        STATUS_FIELDS,
        [
            {
                "sensitivity": name,
                "status": "computed",
                "repositories": 3,
                "detail": "",
            }
            for name in sensitivity_names
        ]
        + [
            {
                "sensitivity": "ambiguous_excluded",
                "status": "not_applicable",
                "repositories": 3,
                "detail": "no ambiguous adoptions",
            },
            {
                "sensitivity": "window_30_days",
                "status": "not_available",
                "repositories": "",
                "detail": "not run",
            },
        ],
    )
    write_csv(
        inference / "sign_tests.csv",
        SIGN_FIELDS,
        [
            sign_row("RQ1", "skill-production", 3, 3, 0.003, True, True),
            sign_row("RQ1", "skill-test", 3, 3, 0.02, True, True),
            sign_row("RQ1", "production-test", 1, 3, 0.02, True, True),
            sign_row(
                "RQ2", "production_post_minus_pre", 2, 3, 0.5, False, False
            ),
            sign_row("RQ2", "test_post_minus_pre", 2, 3, 0.6, False, False),
        ],
    )
    return results, inference


def test_run_writes_figures_readme_and_manifest(
    stage3: tuple[Path, Path], tmp_path: Path
) -> None:
    results, inference = stage3
    output = tmp_path / "figures"
    manifest = plotting.run(
        results, inference, output, forest=True, sensitivity=True
    )

    expected = (
        "fig_rq1_boxplot",
        "fig_rq2_boxplot",
        "fig_effects",
        "fig_rq1_sensitivity",
    )
    assert manifest["configuration"]["sample_size"] == 3
    assert tuple(manifest["figures"]) == expected
    for name in expected:
        assert (output / f"{name}.png").is_file()
        assert (output / f"{name}.pdf").is_file()
    readme = (output / "README.md").read_text(encoding="utf-8")
    assert "n=3" in readme
    assert "fig_effects" in readme
    assert "manifest.json" in readme
    assert (output / "manifest.json").is_file()
    assert manifest["tool_versions"]["matplotlib"]


def test_run_refuses_non_empty_output_without_force(
    stage3: tuple[Path, Path], tmp_path: Path
) -> None:
    results, inference = stage3
    output = tmp_path / "figures"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(SystemExit, match="no está vacío"):
        plotting.run(results, inference, output, force=False)

    plotting.run(results, inference, output, force=True)
    assert sentinel.is_file()
    assert (output / "fig_rq1_boxplot.png").is_file()


def test_without_inference_writes_descriptive_figures_only(
    stage3: tuple[Path, Path], tmp_path: Path
) -> None:
    results, _ = stage3
    output = tmp_path / "figures"
    manifest = plotting.run(results, tmp_path / "missing-inference", output)

    assert tuple(manifest["figures"]) == (
        "fig_rq1_boxplot",
        "fig_rq2_boxplot",
    )
    assert (output / "fig_rq1_boxplot.png").is_file()
    assert (output / "fig_rq2_boxplot.png").is_file()
    assert not (output / "fig_effects.png").exists()
    readme = (output / "README.md").read_text(encoding="utf-8")
    assert "No se encontró `results-inference/`" in readme


def test_forest_requires_inference(
    stage3: tuple[Path, Path], tmp_path: Path
) -> None:
    results, _ = stage3
    with pytest.raises(SystemExit, match="bootstrap_ci"):
        plotting.run(
            results,
            tmp_path / "missing-inference",
            tmp_path / "figures",
            forest=True,
        )


def test_sign_and_dynamic_sensitivity_figures(
    stage3: tuple[Path, Path], tmp_path: Path
) -> None:
    results, inference = stage3
    output = tmp_path / "figures"
    manifest = plotting.run(
        results, inference, output, forest=True, sign=True, sensitivity=True
    )

    assert tuple(manifest["figures"]) == (
        "fig_rq1_boxplot",
        "fig_rq2_boxplot",
        "fig_effects",
        "fig_sign_tests",
        "fig_rq1_sensitivity",
    )
    assert manifest["configuration"]["sensitivity_panels"] == [
        "churn_commits_per_file",
        "churn_churn_per_file",
        "churn_commit_share",
        "skill_content_clusters",
        "bots_excluded",
    ]
    assert "ambiguous_excluded" not in manifest["configuration"]["sensitivity_panels"]
    assert (output / "fig_sign_tests.png").is_file()
    readme = (output / "README.md").read_text(encoding="utf-8")
    assert "fig_sign_tests" in readme
    assert "churn_commit_share" in readme
    assert "window_30_days" in readme


def test_sign_requires_sign_tests(
    stage3: tuple[Path, Path], tmp_path: Path
) -> None:
    results, inference = stage3
    (inference / "sign_tests.csv").unlink()
    with pytest.raises(SystemExit, match="sign_tests"):
        plotting.run(results, inference, tmp_path / "figures", sign=True)


def test_formatting_helpers() -> None:
    assert plotting.fmt_number(0.5) == "0,5"
    assert plotting.fmt_number(32) == "32"
    assert plotting.fmt_p(1e-9) == "p < 0,001"
    assert plotting.fmt_p(0.759) == "p = 0,759"


def test_two_runs_produce_identical_figures(
    stage3: tuple[Path, Path], tmp_path: Path
) -> None:
    import hashlib

    results, inference = stage3
    first = tmp_path / "figures-a"
    second = tmp_path / "figures-b"
    plotting.run(results, inference, first, forest=True, sign=True, sensitivity=True)
    plotting.run(results, inference, second, forest=True, sign=True, sensitivity=True)

    for suffix in ("png", "pdf"):
        for path in sorted(first.glob(f"*.{suffix}")):
            digest_a = hashlib.sha256(path.read_bytes()).hexdigest()
            digest_b = hashlib.sha256(
                (second / path.name).read_bytes()
            ).hexdigest()
            assert digest_a == digest_b, path.name
