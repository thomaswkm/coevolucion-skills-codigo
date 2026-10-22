"""Activity aggregation, explicit zeros, merge exclusion and reproducibility."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from conftest import write_csv
from coevolution_skills.analyze import execute as analyze_execute
from coevolution_skills.process import (
    BOT_PATTERN,
    build_activity_counts,
    execute,
    is_bot_identity,
)
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


def test_bot_pattern_and_identity() -> None:
    assert BOT_PATTERN.pattern.startswith("(?i)")
    assert is_bot_identity("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com", "", "")
    assert is_bot_identity("github-actions[bot]", "", "", "")
    assert not is_bot_identity("Alice", "alice@example.org", "Alice", "alice@example.org")
    assert not is_bot_identity("Abbot", "abbot@example.org", "", "")


def test_activity_outputs_record_identity_line_stats_and_skill_hashes(
    synthetic_project,
) -> None:
    output = synthetic_project.project / "out" / "enriched"
    execute(synthetic_project.config_path, output)

    commits = read_csv(output / "commit_activity.csv")
    counted = next(row for row in commits if row["excluded"] == "false")
    assert counted["author_name"] == "Test Reviewer"
    assert counted["author_email"] == "reviewer@example.com"
    assert counted["committer_name"] == "Test Reviewer"
    assert counted["committer_email"] == "reviewer@example.com"
    assert counted["is_bot"] == "false"
    assert "author_name" in commits[0]
    assert "is_bot" in commits[0]

    changes = read_csv(output / "file_changes.csv")
    assert all("lines_added" in row and "lines_deleted" in row for row in changes)
    assert all(row["is_binary"] in {"true", "false"} for row in changes)
    modified = next(
        row
        for row in changes
        if row["new_path"] == "pkg/app.py" and row["status"] == "M"
    )
    assert (modified["lines_added"], modified["lines_deleted"]) == ("1", "1")

    skills = read_csv(output / "skill_contents.csv")
    assert len(skills) == 1
    expected = hashlib.sha256(
        (
            "---\nname: demo\ndescription: Demo skill for hermetic pipeline tests.\n"
            "---\n# Demo\n"
        ).encode()
    ).hexdigest()
    assert skills[0]["path"] == ".claude/skills/demo/SKILL.md"
    assert skills[0]["content_sha256"] == expected

    manifest = json.loads((output / "processing_manifest.json").read_text())
    assert manifest["row_counts"]["skill_contents"] == 1
    assert manifest["configuration"]["activity_window_days"] == 5
    assert manifest["configuration"]["activity_window_source"] == "config"


def test_window_override_changes_activity_window_only(synthetic_project) -> None:
    output = synthetic_project.project / "out" / "short-window"
    execute(synthetic_project.config_path, output, window_days=3)

    counts = {
        (row["period"], row["category"]): int(row["unique_commit_count"])
        for row in read_csv(output / "activity_counts.csv")
    }
    assert counts[("post", "production")] == 2
    assert counts[("pre", "production")] == 1

    manifest = json.loads((output / "processing_manifest.json").read_text())
    assert manifest["configuration"]["activity_window_days"] == 3
    assert manifest["configuration"]["activity_window_source"] == "command-line override"
    summary = manifest["repositories"][0]
    assert summary["pre_window_complete"] is True
    assert summary["post_window_complete"] is True


def test_process_then_analyze_integration(synthetic_project) -> None:
    processed = synthetic_project.project / "out" / "processed"
    results = synthetic_project.project / "out" / "results"
    execute(synthetic_project.config_path, processed)
    analyze_execute(
        synthetic_project.config,
        processed,
        results,
        synthetic_project.validation,
    )

    assert len(read_csv(results / "analysis_sample.csv")) == 1
    assert len(read_csv(results / "rq1_repository_counts.csv")) == 1
    assert len(read_csv(results / "rq2_repository_differences.csv")) == 1
    manifest = json.loads((results / "analysis_manifest.json").read_text())
    assert manifest["repository_count"] == 1
    assert manifest["excluded_invalid_count"] == 0
    assert "MISMATCH" not in (results / "consistency_checks.txt").read_text()


def test_shortfall_is_accepted_only_for_cap_or_frame_exhaustion(
    synthetic_project,
) -> None:
    config_text = synthetic_project.config_path.read_text().replace(
        "target_count = 1", "target_count = 2"
    )
    target_two = synthetic_project.project / "extraction-target-two.toml"
    target_two.write_text(config_text, encoding="utf-8")
    manifest_path = synthetic_project.extraction / "run_manifest.json"
    validation_manifest_path = (
        synthetic_project.validation / "validation_manifest.json"
    )

    def write_extraction_manifest(status: str, cap: bool) -> None:
        manifest_path.write_text(
            json.dumps(
                {"completion_status": status, "cap_reached": cap}
            )
            + "\n",
            encoding="utf-8",
        )
        validation_manifest = json.loads(validation_manifest_path.read_text())
        validation_manifest["extraction_manifest_sha256"] = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        validation_manifest_path.write_text(
            json.dumps(validation_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    write_extraction_manifest("target_reached", False)
    with pytest.raises(ValueError, match="shortfall"):
        execute(target_two, synthetic_project.project / "out" / "shortfall-rejected")

    write_extraction_manifest("cap_reached", True)
    output = synthetic_project.project / "out" / "shortfall-accepted"
    execute(target_two, output)
    manifest = json.loads((output / "processing_manifest.json").read_text())
    assert manifest["selected_repository_count"] == 1
    assert manifest["target_count"] == 2
    assert manifest["extraction_completion_status"] == "cap_reached"
