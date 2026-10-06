"""Activity aggregation, explicit zeros, merge exclusion and reproducibility."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from conftest import write_csv
from coevolution_skills.process import build_activity_counts, execute
from coevolution_skills.reviewer import AUTOMATED_CHECK_FIELDS, ReviewValidationError
from coevolution_skills.validation import (
    ADOPTION_REVIEW_FIELDS,
    PATH_REVIEW_FIELDS,
    SelectedRepository,
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def clear_human_fields(project) -> None:
    adoption = read_csv(project.adoption_path)
    for row in adoption:
        for field in (
            "human_adoption_decision",
            "human_rename_only",
            "human_frontmatter_and_paths_valid",
            "human_notes",
            "reviewer",
            "reviewed_at_utc",
        ):
            row[field] = ""
    write_csv(project.adoption_path, ADOPTION_REVIEW_FIELDS, adoption)

    paths = read_csv(project.path_review_path)
    for row in paths:
        for field in (
            "human_category",
            "agreement",
            "human_notes",
            "reviewer",
            "reviewed_at_utc",
        ):
            row[field] = ""
    write_csv(project.path_review_path, PATH_REVIEW_FIELDS, paths)
    (project.validation / "human_review.sha256").unlink()


def selected_repository(position: int, name: str) -> SelectedRepository:
    return SelectedRepository(
        sample_position=position,
        repo_full_name=name,
        default_branch="main",
        cutoff_commit="a" * 40,
        adoption_commit="b" * 40,
        adoption_at_utc="2026-01-01T00:00:00Z",
        reported_skill_paths=(".claude/skills/demo/SKILL.md",),
        reported_production_count=1,
        reported_test_count=1,
    )


def commit_row(name: str, period: str, commit: str, category: str) -> dict[str, str]:
    return {
        "repo_full_name": name,
        "period": period,
        "commit": commit,
        "excluded": "false",
        "category": category,
    }


def test_build_activity_counts_emits_explicit_zeros() -> None:
    repository = selected_repository(1, "test/repo")
    rows = build_activity_counts(
        [repository],
        [
            commit_row("test/repo", "post", "c1", "production"),
            commit_row("test/repo", "post", "c1", "test"),
            commit_row("test/repo", "post", "c2", "production"),
        ],
    )
    assert len(rows) == 8
    counts = {
        (row["period"], row["category"]): row["unique_commit_count"] for row in rows
    }
    assert counts[("post", "production")] == 2
    assert counts[("post", "test")] == 1
    assert counts[("post", "skill")] == 0
    assert counts[("pre", "production")] == 0
    assert all(row["data_status"] == "observed" for row in rows)


def test_processing_excludes_merges_and_counts_multifile_once(synthetic_project) -> None:
    output = synthetic_project.project / "out" / "run"
    execute(synthetic_project.config_path, output)

    counts = read_csv(output / "activity_counts.csv")
    assert len(counts) == 8
    observed = {
        (row["period"], row["category"]): int(row["unique_commit_count"])
        for row in counts
    }
    assert observed[("pre", "production")] == 1
    assert observed[("post", "production")] == 3
    assert observed[("post", "test")] == 1
    assert observed[("post", "skill")] == 0
    assert observed[("pre", "skill")] == 0

    commits = read_csv(output / "commit_activity.csv")
    merges = [row for row in commits if row["excluded"] == "true"]
    assert len(merges) == 1
    assert merges[0]["exclusion_reason"] == "merge_commit"
    assert merges[0]["commit"] == synthetic_project.commits["cutoff"]

    counted = {
        (row["commit"], row["category"]): int(row["path_count"])
        for row in commits
        if row["excluded"] == "false"
    }
    # One commit touching two production files counts once with both paths.
    assert counted[(synthetic_project.commits["side_one"], "production")] == 2
    # A commit touching production and test appears in both categories.
    assert (synthetic_project.commits["side_two"], "production") in counted
    assert (synthetic_project.commits["side_two"], "test") in counted


def test_two_runs_produce_identical_csv_and_checksums(synthetic_project) -> None:
    first = synthetic_project.project / "out" / "first"
    second = synthetic_project.project / "out" / "second"
    execute(synthetic_project.config_path, first)
    execute(synthetic_project.config_path, second)

    for name in ("commit_activity.csv", "file_changes.csv", "activity_counts.csv"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name

    manifest_one = json.loads((first / "processing_manifest.json").read_text())
    manifest_two = json.loads((second / "processing_manifest.json").read_text())
    assert manifest_one["output_sha256"] == manifest_two["output_sha256"]
    assert manifest_one["input_sha256"] == manifest_two["input_sha256"]
    assert manifest_one["row_counts"] == manifest_two["row_counts"]


def test_pending_human_review_is_blocking_by_default(synthetic_project) -> None:
    clear_human_fields(synthetic_project)
    output = synthetic_project.project / "out" / "blocked"
    with pytest.raises(ReviewValidationError):
        execute(synthetic_project.config_path, output)


def test_pending_human_review_can_be_allowed(synthetic_project) -> None:
    clear_human_fields(synthetic_project)
    output = synthetic_project.project / "out" / "allowed"
    execute(synthetic_project.config_path, output, require_human_review=False)

    manifest = json.loads((output / "processing_manifest.json").read_text())
    review = manifest["human_screening_review"]
    assert review["required"] is False
    assert review["status"] == "pending"
    assert review["pending_adoptions"] == [1]
    assert review["pending_paths"] == [1, 2, 3, 4]
    assert review["checksum_file_present"] is False
    assert len(read_csv(output / "activity_counts.csv")) == 8


def test_mechanical_evidence_blocks_when_pending_review_is_allowed(
    synthetic_project,
) -> None:
    clear_human_fields(synthetic_project)
    checks = read_csv(synthetic_project.validation / "automated_checks.csv")
    checks[0]["passed"] = "false"
    write_csv(
        synthetic_project.validation / "automated_checks.csv",
        AUTOMATED_CHECK_FIELDS,
        checks,
    )
    manifest_path = synthetic_project.validation / "validation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["automatic_checks_passed"] = len(checks) - 1
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    output = synthetic_project.project / "out" / "blocked-evidence"
    with pytest.raises(ReviewValidationError):
        execute(synthetic_project.config_path, output, require_human_review=False)
