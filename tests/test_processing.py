"""Activity aggregation, explicit zeros, merge exclusion and reproducibility."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from coevolution_skills.process import build_activity_counts, execute
from coevolution_skills.validation import SelectedRepository


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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
