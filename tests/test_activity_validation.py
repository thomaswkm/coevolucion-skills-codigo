"""Deterministic sampling and strict validation of changed-path review forms."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import GitRepo, write_csv
from coevolution_skills.activity_validation import (
    ACTIVITY_REVIEW_FIELDS,
    effective_category,
    prepare,
    select_changes,
    validate_activity_review,
)
from coevolution_skills.extract import load_config
from coevolution_skills.reviewer import ReviewValidationError


CHANGES_FIELDS = [
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
]


def change(
    commit: str,
    status: str,
    old_path: str,
    new_path: str,
    old_category: str,
    new_category: str,
    similarity: str = "",
) -> dict[str, str]:
    return {
        "repo_full_name": "test/repo",
        "period": "post",
        "commit": commit,
        "status": status,
        "similarity": similarity,
        "old_path": old_path,
        "new_path": new_path,
        "old_category": old_category,
        "new_category": new_category,
        "classification_rule": "fixture",
    }


@pytest.fixture
def activity_project(tmp_path: Path) -> SimpleNamespace:
    project = tmp_path / "project"
    repositories = project / "repositories"
    extraction = project / "data" / "extraction"
    processed = project / "data" / "processed"
    extraction.mkdir(parents=True)
    processed.mkdir(parents=True)

    repo = GitRepo(repositories / "test__repo")
    repo.write("pkg/app.py", "VALUE = 1\n")
    repo.write("pkg/other.py", "OTHER = 1\n")
    repo.write("README.md", "# Demo\n")
    c1 = repo.commit("base", "2026-05-01T12:00:00Z")
    repo.write("pkg/app.py", "VALUE = 2\n")
    c2 = repo.commit("modify", "2026-05-02T12:00:00Z")
    repo.remove("pkg/app.py")
    c3 = repo.commit("delete", "2026-05-03T12:00:00Z")
    (repo.path / "tests").mkdir(parents=True, exist_ok=True)
    repo.git("mv", "pkg/other.py", "tests/test_other.py")
    c4 = repo.commit("rename", "2026-05-04T12:00:00Z")
    repo.write(
        ".claude/skills/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo skill.\n---\n",
    )
    c5 = repo.commit("skill", "2026-05-05T12:00:00Z")

    changes = [
        change(c1, "A", "", "pkg/app.py", "", "production"),
        change(c1, "A", "", "pkg/other.py", "", "production"),
        change(c1, "A", "", "README.md", "", "other"),
        change(c2, "M", "pkg/app.py", "pkg/app.py", "production", "production"),
        change(c3, "D", "pkg/app.py", "", "production", ""),
        change(c4, "R", "pkg/other.py", "tests/test_other.py", "production", "test", "100"),
        change(c5, "A", "", ".claude/skills/demo/SKILL.md", "", "skill"),
    ]
    changes_path = processed / "file_changes.csv"
    write_csv(changes_path, CHANGES_FIELDS, changes)
    (processed / "processing_manifest.json").write_text(
        json.dumps(
            {
                "processing_protocol_version": "1",
                "output_sha256": {
                    "file_changes": hashlib.sha256(changes_path.read_bytes()).hexdigest()
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    selected_path = extraction / "selected_repositories.csv"
    write_csv(
        selected_path,
        [
            "sample_position",
            "candidate_position",
            "repo_full_name",
            "ordering_hash",
            "status",
            "exclusion_reason",
            "detail",
            "repository_url",
            "default_branch",
            "cutoff_commit",
            "adoption_commit",
            "adoption_at_utc",
            "valid_skill_paths",
            "production_python_count",
            "test_python_count",
            "retrieved_at_utc",
        ],
        [
            {
                "sample_position": 1,
                "candidate_position": 1,
                "repo_full_name": "test/repo",
                "ordering_hash": "0" * 64,
                "status": "eligible",
                "exclusion_reason": "",
                "detail": "fixture",
                "repository_url": "https://example.com/test/repo.git",
                "default_branch": "main",
                "cutoff_commit": c5,
                "adoption_commit": c5,
                "adoption_at_utc": "2026-05-05T12:00:00Z",
                "valid_skill_paths": ".claude/skills/demo/SKILL.md",
                "production_python_count": 1,
                "test_python_count": 1,
                "retrieved_at_utc": "2026-05-05T12:00:00Z",
            }
        ],
    )

    config_path = project / "extraction.toml"
    config_path.write_text(
        "\n".join(
            [
                "[dataset]",
                'repository = "mvaccargiu/gitskills"',
                f'revision = "{"0" * 40}"',
                "artifact_parts = 1",
                "",
                "[selection]",
                "target_count = 1",
                'seed = "test-seed"',
                'history_cutoff_utc = "2026-12-31T23:59:59Z"',
                "window_days = 5",
                "",
                "[git]",
                "clone_timeout_seconds = 30",
                "command_timeout_seconds = 30",
                "retry_count = 1",
                "checkout_selected = true",
                "",
                "[paths]",
                f'output_directory = "{extraction}"',
                f'repository_directory = "{repositories}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    return SimpleNamespace(
        project=project,
        config_path=config_path,
        config=config,
        changes_path=changes_path,
        output=project / "data" / "activity-validation",
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def fill_human(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    for row in rows:
        row["human_category"] = row["automatic_category"]
        row["agreement"] = "yes"
        row["reviewer"] = "Test Reviewer"
        row["reviewed_at_utc"] = "2026-10-05T00:00:00Z"
    return rows


def rewrite(path: Path, rows: list[dict[str, str]]) -> None:
    write_csv(path, ACTIVITY_REVIEW_FIELDS, rows)


def test_effective_category_prefers_new() -> None:
    assert effective_category({"new_category": "test", "old_category": "production"}) == "test"
    assert effective_category({"new_category": "", "old_category": "production"}) == "production"


def test_selection_guarantees_delete_and_rename() -> None:
    rows = [
        {"repo_full_name": "a/b", "commit": "c1", "status": "A", "old_path": "", "new_path": "x.py", "old_category": "", "new_category": "production"},
        {"repo_full_name": "a/b", "commit": "c2", "status": "M", "old_path": "x.py", "new_path": "x.py", "old_category": "production", "new_category": "production"},
        {"repo_full_name": "a/b", "commit": "c3", "status": "D", "old_path": "x.py", "new_path": "", "old_category": "production", "new_category": ""},
        {"repo_full_name": "a/b", "commit": "c4", "status": "R", "old_path": "y.py", "new_path": "tests/test_y.py", "old_category": "production", "new_category": "test"},
    ]
    production = select_changes(rows, "production", 10, "seed")
    statuses = {row["status"] for row in production}
    assert "D" in statuses
    # The rename resolves to the test stratum, not production.
    assert {row["status"] for row in select_changes(rows, "test", 10, "seed")} == {"R"}
    assert all(row["automatic_category"] == "production" for row in production)


def test_prepare_samples_and_validates(activity_project) -> None:
    prepare(activity_project.config, activity_project.output, activity_project.changes_path, 10)

    rows = read_rows(activity_project.output / "activity_review.csv")
    assert len(rows) == 7
    assert [int(row["review_position"]) for row in rows] == list(range(1, 8))
    by_category: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_category.setdefault(row["automatic_category"], []).append(row)
    assert {category: len(items) for category, items in by_category.items()} == {
        "skill": 1,
        "production": 4,
        "test": 1,
        "other": 1,
    }
    rename = by_category["test"][0]
    assert (rename["old_path"], rename["new_path"]) == ("pkg/other.py", "tests/test_other.py")
    assert rename["parent_commit"]  # resolved for renames
    deletion = [row for row in by_category["production"] if row["status"] == "D"][0]
    assert deletion["parent_commit"]

    manifest = json.loads(
        (activity_project.output / "activity_validation_manifest.json").read_text()
    )
    assert manifest["sample_counts"] == {"skill": 1, "production": 4, "test": 1, "other": 1}
    assert manifest["status_distribution"]["D"] == 1
    assert manifest["status_distribution"]["R"] == 1


def test_validate_requires_human_fields(activity_project) -> None:
    prepare(activity_project.config, activity_project.output, activity_project.changes_path, 10)
    with pytest.raises(ReviewValidationError):
        validate_activity_review(activity_project.output, activity_project.changes_path)


def test_validate_and_seal(activity_project) -> None:
    prepare(activity_project.config, activity_project.output, activity_project.changes_path, 10)
    form_path = activity_project.output / "activity_review.csv"
    rewrite(form_path, fill_human(read_rows(form_path)))

    rows = validate_activity_review(activity_project.output, activity_project.changes_path, seal=True)
    assert len(rows) == 7
    checksum = activity_project.output / "human_activity_review.sha256"
    assert checksum.exists()
    # Second seal must refuse to overwrite.
    with pytest.raises(ReviewValidationError):
        validate_activity_review(activity_project.output, activity_project.changes_path, seal=True)


def test_validate_rejects_inconsistent_agreement(activity_project) -> None:
    prepare(activity_project.config, activity_project.output, activity_project.changes_path, 10)
    form_path = activity_project.output / "activity_review.csv"
    rows = fill_human(read_rows(form_path))
    rows[0]["human_category"] = "other" if rows[0]["automatic_category"] != "other" else "test"
    rows[0]["agreement"] = "yes"
    rewrite(form_path, rows)

    with pytest.raises(ReviewValidationError):
        validate_activity_review(activity_project.output, activity_project.changes_path)
