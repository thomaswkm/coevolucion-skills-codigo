"""Inferential RQ1/RQ2 analysis and its frozen-plan outputs."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import pytest

from conftest import write_csv
from coevolution_skills.analyze import COUNT_FIELDS
from coevolution_skills.infer import (
    argument_parser,
    bootstrap_block,
    execute,
    friedman_test,
    hodges_lehmann,
    holm_adjust,
    load_analysis_sample,
    load_window_inputs,
    paired_wilcoxon,
    rank_biserial,
    sign_test,
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_project(tmp_path: Path, config_factory) -> dict[str, Path]:
    project = tmp_path / "project"
    processed = project / "data" / "processed"
    results = project / "results"
    extraction = project / "data" / "extraction"
    for directory in (processed, results, extraction):
        directory.mkdir(parents=True)

    counts = []
    repos = [f"owner/repo{index}" for index in range(8)]
    # A range of activity levels, including zeros, ties and an increasing
    # post-adoption pattern.
    post_skill = [0, 0, 1, 1, 2, 2, 0, 1]
    post_production = [3, 5, 8, 10, 12, 20, 0, 14]
    post_test = [1, 2, 4, 5, 9, 10, 0, 6]
    pre_production = [2, 7, 5, 11, 10, 18, 1, 15]
    pre_test = [1, 1, 5, 4, 8, 9, 1, 7]
    for index, repo in enumerate(repos):
        for period, category, value in (
            ("pre", "skill", 0),
            ("pre", "production", pre_production[index]),
            ("pre", "test", pre_test[index]),
            ("pre", "other", 1),
            ("post", "skill", post_skill[index]),
            ("post", "production", post_production[index]),
            ("post", "test", post_test[index]),
            ("post", "other", 0),
        ):
            counts.append(
                {
                    "repo_full_name": repo,
                    "period": period,
                    "category": category,
                    "unique_commit_count": value,
                    "data_status": "observed",
                }
            )
    counts_path = processed / "activity_counts.csv"
    write_csv(counts_path, COUNT_FIELDS, counts)

    analysis_rows = []
    for index, repo in enumerate(repos):
        analysis_rows.append(
            {
                "repo_full_name": repo,
                "adoption_decision": "ambiguous" if index == 1 else "valid",
                "included_in_primary": "true",
                "ambiguous": "true" if index == 1 else "false",
            }
        )
    write_csv(
        results / "analysis_sample.csv",
        ["repo_full_name", "adoption_decision", "included_in_primary", "ambiguous"],
        analysis_rows,
    )
    (results / "analysis_manifest.json").write_text(
        json.dumps(
            {
                "input_sha256": {
                    "activity_counts": __import__("hashlib").sha256(
                        counts_path.read_bytes()
                    ).hexdigest()
                },
                "sample_estimate": {"estimated_population": 100.0},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    write_csv(
        extraction / "candidate_frame.csv",
        ["repo_full_name", "eligible_skill_count"],
        [
            {"repo_full_name": repo, "eligible_skill_count": index + 1}
            for index, repo in enumerate(repos)
        ],
    )
    write_csv(
        extraction / "selected_repositories.csv",
        [
            "repo_full_name",
            "valid_skill_paths",
            "production_python_count",
            "test_python_count",
        ],
        [
            {
                "repo_full_name": repo,
                "valid_skill_paths": ";".join(
                    f".claude/skills/skill{skill}/SKILL.md"
                    for skill in range(2 if index in (0, 1) else index + 1)
                ),
                "production_python_count": index + 2,
                "test_python_count": index + 1,
            }
            for index, repo in enumerate(repos)
        ],
    )

    file_rows = []
    commit_rows = []
    skill_rows = []
    for index, repo in enumerate(repos):
        for period, production_lines, test_lines in (
            ("pre", 10 + index, 5 + index),
            ("post", 20 + index, 10 + index),
        ):
            for category, path, lines in (
                ("production", f"pkg/mod{index}.py", production_lines),
                ("test", f"tests/test_mod{index}.py", test_lines),
            ):
                file_rows.append(
                    {
                        "repo_full_name": repo,
                        "period": period,
                        "commit": f"{period}{index}",
                        "status": "M",
                        "similarity": "",
                        "old_path": path,
                        "new_path": path,
                        "old_category": category,
                        "new_category": category,
                        "classification_rule": "fixture",
                        "lines_added": lines,
                        "lines_deleted": index,
                        "is_binary": "false",
                    }
                )
                commit_rows.append(
                    {
                        "repo_full_name": repo,
                        "period": period,
                        "commit": f"{period}{index}-{category}",
                        "committed_at_utc": "2026-05-01T00:00:00Z",
                        "category": category,
                        "path_count": 1,
                        "excluded": "false",
                        "exclusion_reason": "",
                        "author_name": "Fixture Author",
                        "author_email": "author@example.com",
                        "committer_name": "Fixture Author",
                        "committer_email": "author@example.com",
                        "is_bot": "false",
                    }
                )
        for skill in range(2 if index in (0, 1) else index + 1):
            # The first two repositories share their complete signature.
            digest = "shared" if index in (0, 1) else f"unique-{index}-{skill}"
            skill_rows.append(
                {
                    "repo_full_name": repo,
                    "path": f".claude/skills/skill{skill}/SKILL.md",
                    "content_sha256": digest,
                }
            )
    # A bot commit in the post period of repo0.
    commit_rows.append(
        {
            "repo_full_name": repos[0],
            "period": "post",
            "commit": "bot-commit",
            "committed_at_utc": "2026-05-02T00:00:00Z",
            "category": "production",
            "path_count": 1,
            "excluded": "false",
            "exclusion_reason": "",
            "author_name": "dependabot[bot]",
            "author_email": "bot@example.com",
            "committer_name": "GitHub",
            "committer_email": "noreply@github.com",
            "is_bot": "true",
        }
    )
    write_csv(
        processed / "file_changes.csv",
        [
            "repo_full_name",
            "period",
            "commit",
            "status",
            "similarity",
            "old_path",
            "new_path",
            "old_category",
            "new_category",
            "classification_rule",
            "lines_added",
            "lines_deleted",
            "is_binary",
        ],
        file_rows,
    )
    write_csv(
        processed / "commit_activity.csv",
        [
            "repo_full_name",
            "period",
            "commit",
            "committed_at_utc",
            "category",
            "path_count",
            "excluded",
            "exclusion_reason",
            "author_name",
            "author_email",
            "committer_name",
            "committer_email",
            "is_bot",
        ],
        commit_rows,
    )
    write_csv(
        processed / "skill_contents.csv",
        ["repo_full_name", "path", "content_sha256"],
        skill_rows,
    )

    config = config_factory(output_directory=extraction, target_count=8)
    return {
        "project": project,
        "processed": processed,
        "results": results,
        "extraction": extraction,
        "config": config,
    }


def test_holm_adjust_is_step_down() -> None:
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm_adjust([]) == []
    assert holm_adjust([1.0, 0.5]) == pytest.approx([1.0, 1.0])


def test_rank_biserial_and_hodges_lehmann() -> None:
    assert rank_biserial([1, 2, 3, 0]) == pytest.approx(1.0)
    assert rank_biserial([-1, -2, -3]) == pytest.approx(-1.0)
    assert rank_biserial([0, 0]) == 0.0
    # Walsh averages of 1, 2, 3: 1, 1.5, 2, 2, 2.5, 3.
    assert hodges_lehmann([1, 2, 3]) == pytest.approx(2.0)
    assert math.isnan(hodges_lehmann([]))


def test_wilcoxon_and_sign_handle_zero_differences() -> None:
    result = paired_wilcoxon([0, 0, 1, 2, 3])
    assert result["n_nonzero"] == 3
    assert result["n_total"] == 5
    degenerate = paired_wilcoxon([0, 0, 0])
    assert degenerate["n_nonzero"] == 0
    assert degenerate["p_value"] == 1.0

    sign = sign_test([0, 1, 1, -1])
    assert sign["n_nonzero"] == 3
    assert sign["positive"] == 2
    assert 0 <= sign["ci_low"] <= sign["positive_proportion"] <= sign["ci_high"] <= 1


def test_friedman_uses_repositories_as_blocks() -> None:
    columns = [[1, 2, 3, 4], [2, 3, 4, 5], [4, 5, 6, 7]]
    result = friedman_test(columns)
    assert result["n_blocks"] == 4
    assert result["treatments"] == 3
    assert result["df"] == 2
    assert result["p_value"] < 0.05
    assert 0 < result["kendall_w"] <= 1


def test_bootstrap_block_keeps_full_sample_point_estimates() -> None:
    medians = {"median": np.array([1.0, 2.0, 3.0, 100.0])}
    shifts = {"shift": np.array([1.0, 2.0, 3.0, 4.0])}
    points = {"median": 3.0, "shift": 2.5}
    estimates, resamples = bootstrap_block(
        medians, shifts, points=points, resamples=50, seed=7
    )
    assert resamples == 50
    assert estimates["median"].point == 3.0
    assert estimates["shift"].point == 2.5
    assert estimates["median"].low <= estimates["median"].high


def test_inference_end_to_end(tmp_path: Path, config_factory) -> None:
    project = build_project(tmp_path, config_factory)
    output = project["project"] / "results-inference"
    execute(
        project["config"],
        project["results"],
        project["processed"],
        output,
        resamples=100,
        seed=20261020,
    )

    for name in (
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
        "inference_manifest.json",
    ):
        assert (output / name).exists(), name

    rq1 = {row["comparison"]: row for row in read_csv(output / "rq1_tests.csv")}
    assert rq1["skill|production|test"]["n_blocks"] == "8"
    assert rq1["skill|production|test"]["df"] == "2"
    assert rq1["skill-production"]["p_value"] != ""
    assert rq1["production-test"]["p_value"] != ""

    rq2 = read_csv(output / "rq2_tests.csv")
    assert {row["comparison"] for row in rq2} == {
        "production_post_minus_pre",
        "test_post_minus_pre",
    }
    for row in rq2:
        assert float(row["p_adjusted"]) >= float(row["p_value"])

    effects = read_csv(output / "effect_sizes.csv")
    assert len(effects) == 5
    assert {row["comparison"] for row in effects} >= {"skill-production", "production_post_minus_pre"}

    bootstrap = read_csv(output / "bootstrap_ci.csv")
    assert len(bootstrap) == 12
    assert all(row["resamples"] == "100" for row in bootstrap)
    assert all(row["seed"] == "20261020" for row in bootstrap)
    assert any(row["quantity"] == "hodges_lehmann" for row in bootstrap)

    signs = read_csv(output / "sign_tests.csv")
    assert len(signs) == 5
    assert all(row["ci_low"] != "" for row in signs)

    statuses = {row["sensitivity"]: row for row in read_csv(output / "sensitivity_status.csv")}
    assert statuses["ambiguous_excluded"]["repositories"] == "7"
    assert statuses["skill_content_clusters"]["repositories"] == "7"
    assert statuses["churn_commits_per_file"]["status"] == "computed"
    assert statuses["bots_excluded"]["status"] == "computed"
    assert statuses["window_30_days"]["status"] == "not_available"

    churn = read_csv(output / "sensitivity_churn.csv")
    assert len(churn) == 8 * 2 * 3
    assert {row["category"] for row in churn} == {"skill", "production", "test"}
    assert all(float(row["commits_per_file"]) >= 0 for row in churn)

    clusters = read_csv(output / "skill_cluster_assignment.csv")
    shared = [row for row in clusters if row["cluster_size"] == "2"]
    assert len(shared) == 2
    assert {row["cluster_representative"] for row in shared} == {"owner/repo0"}

    bots = read_csv(output / "bot_commits_review.csv")
    assert len(bots) == 1
    assert bots[0]["commit"] == "bot-commit"

    representativeness = read_csv(output / "sample_representativeness.csv")
    assert {row["group"] for row in representativeness} == {"frame", "sample"}
    assert any(
        row["variable"] == "eligible_skill_count" and row["n_available"] == "8"
        for row in representativeness
    )

    consistency = (output / "consistency_checks.txt").read_text()
    assert "MISMATCH" not in consistency
    assert "OK" in consistency

    manifest = json.loads((output / "inference_manifest.json").read_text())
    assert manifest["repository_count"] == 8
    assert manifest["configuration"]["alpha"] == 0.05
    assert manifest["configuration"]["bootstrap_resamples"] == 100
    assert manifest["configuration"]["bootstrap_seed"] == 20261020
    assert manifest["tool_versions"]["scipy"]
    assert manifest["bot_pattern_matches"]["unique_commits"] == 1
    assert manifest["sensitivity_status"]


def test_invalid_adoptions_are_not_inferred(
    tmp_path: Path, config_factory
) -> None:
    project = build_project(tmp_path, config_factory)
    sample_path = project["results"] / "analysis_sample.csv"
    rows = read_csv(sample_path)
    rows[2]["adoption_decision"] = "invalid"
    rows[2]["included_in_primary"] = "false"
    write_csv(
        sample_path,
        ["repo_full_name", "adoption_decision", "included_in_primary", "ambiguous"],
        rows,
    )
    output = project["project"] / "results-inference"
    execute(
        project["config"],
        project["results"],
        project["processed"],
        output,
        resamples=20,
    )
    manifest = json.loads((output / "inference_manifest.json").read_text())
    assert manifest["repository_count"] == 7
    assert manifest["excluded_invalid_count"] == 1
    rq1 = read_csv(output / "rq1_tests.csv")
    assert rq1[0]["n_blocks"] == "7"


def test_existing_output_is_rejected(tmp_path: Path, config_factory) -> None:
    project = build_project(tmp_path, config_factory)
    output = project["project"] / "results-inference"
    output.mkdir()
    with pytest.raises(FileExistsError):
        execute(project["config"], project["results"], project["processed"], output)


def test_window_90_excludes_incomplete_repositories(tmp_path: Path, config_factory) -> None:
    project = build_project(tmp_path, config_factory)
    directory = project["project"] / "data" / "processed-90"
    directory.mkdir()
    counts = read_csv(project["processed"] / "activity_counts.csv")
    write_csv(directory / "activity_counts.csv", COUNT_FIELDS, counts)
    (directory / "processing_manifest.json").write_text(
        json.dumps(
            {
                "repositories": [
                    {"repo_full_name": f"owner/repo{index}", "pre_window_complete": index != 0, "post_window_complete": True}
                    for index in range(8)
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    order = [row["repo_full_name"] for row in counts]
    order = list(dict.fromkeys(order))
    loaded = load_window_inputs(
        directory,
        order=order,
        expected_days=90,
        label="window_90_days",
    )
    assert loaded is not None
    selected, values = loaded
    assert "owner/repo0" not in selected
    assert len(selected) == 7
    assert values[("owner/repo1", "post", "production")] == 5


def test_window_sensitivities_are_opt_in() -> None:
    args = argument_parser().parse_args([])
    assert args.processed_30 is None
    assert args.processed_90 is None


def test_analysis_sample_requires_expected_schema(tmp_path: Path) -> None:
    path = tmp_path / "analysis_sample.csv"
    write_csv(path, ["repo_full_name", "included_in_primary"], [])
    with pytest.raises(ValueError):
        load_analysis_sample(path, ["a/b"])
