"""Shared hermetic fixtures for the phase 3 test suite."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import pytest

from coevolution_skills.extract import Config, load_config
from coevolution_skills.reviewer import AUTOMATED_CHECK_FIELDS
from coevolution_skills.validation import ADOPTION_REVIEW_FIELDS, PATH_REVIEW_FIELDS


T0 = "2026-05-10T12:00:00Z"
PRE_BOUNDARY = "2026-05-05T12:00:00Z"
POST_BOUNDARY = "2026-05-15T12:00:00Z"
WINDOW_DAYS = 5
ADOPTION_COMMIT_DATE = T0


class GitRepo:
    """A throwaway Git repository with deterministic committer dates."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self._run("init", "-b", "main")
        self._run("config", "user.name", "Test Reviewer")
        self._run("config", "user.email", "reviewer@example.com")

    def _run(self, *args: str, date: str | None = None) -> str:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        if date is not None:
            environment["GIT_AUTHOR_DATE"] = date
            environment["GIT_COMMITTER_DATE"] = date
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=True,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout

    def git(self, *args: str, date: str | None = None) -> str:
        return self._run(*args, date=date)

    def write(self, relative: str, content: str) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def remove(self, relative: str) -> None:
        (self.path / relative).unlink()

    def commit(self, message: str, date: str) -> str:
        self._run("add", "-A")
        self._run("commit", "-m", message, date=date)
        return self.rev_parse("HEAD")

    def rev_parse(self, reference: str) -> str:
        return self._run("rev-parse", reference).strip()


def build_config(
    repository_directory: Path,
    output_directory: Path,
    *,
    window_days: int = WINDOW_DAYS,
    target_count: int = 1,
) -> Config:
    return Config(
        dataset_repository="mvaccargiu/gitskills",
        dataset_revision="0" * 40,
        artifact_parts=1,
        target_count=target_count,
        seed="test-seed",
        history_cutoff_utc="2026-12-31T23:59:59Z",
        window_days=window_days,
        clone_timeout_seconds=30,
        command_timeout_seconds=30,
        retry_count=1,
        checkout_selected=True,
        output_directory=output_directory,
        repository_directory=repository_directory,
    )


def write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def config_factory(tmp_path: Path):
    def factory(
        repository_directory: Path | None = None,
        output_directory: Path | None = None,
        *,
        window_days: int = WINDOW_DAYS,
        target_count: int = 1,
    ) -> Config:
        return build_config(
            repository_directory or tmp_path / "repositories",
            output_directory or tmp_path / "extraction",
            window_days=window_days,
            target_count=target_count,
        )

    return factory


@pytest.fixture
def git_repo_factory(tmp_path: Path):
    def factory(name: str = "repo") -> GitRepo:
        return GitRepo(tmp_path / "repositories" / name)

    return factory


def _build_repository(repositories: Path) -> dict[str, str]:
    repo = GitRepo(repositories / "test__repo")
    repo.write("pkg/app.py", "VALUE = 1\n")
    repo.write("tests/test_app.py", "def test_app():\n    assert True\n")
    repo.write("README.md", "# Demo\n")
    base = repo.commit("base", "2026-04-30T12:00:00Z")

    repo.write("pkg/app.py", "VALUE = 2\n")
    pre = repo.commit("pre: production change", "2026-05-07T12:00:00Z")

    repo.write(
        ".claude/skills/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo skill for hermetic pipeline tests.\n---\n# Demo\n",
    )
    adoption = repo.commit("adopt skill", ADOPTION_COMMIT_DATE)

    repo.git("checkout", "-b", "feature")
    repo.write("pkg/one.py", "ONE = 1\n")
    repo.write("pkg/two.py", "TWO = 2\n")
    side_one = repo.commit("feature: two production files", "2026-05-11T12:00:00Z")
    repo.write("pkg/one.py", "ONE = 11\n")
    repo.write("tests/test_app.py", "def test_app():\n    assert 2\n")
    side_two = repo.commit("feature: production and test", "2026-05-12T12:00:00Z")

    repo.git("checkout", "main")
    repo.write("pkg/main_extra.py", "EXTRA = 1\n")
    main_tip = repo.commit("main: production", "2026-05-14T12:00:00Z")
    repo.git("merge", "--no-ff", "-m", "merge feature", "feature", date="2026-05-14T13:00:00Z")
    cutoff = repo.rev_parse("HEAD")
    return {
        "base": base,
        "pre": pre,
        "adoption": adoption,
        "side_one": side_one,
        "side_two": side_two,
        "main_tip": main_tip,
        "cutoff": cutoff,
        "first_parent": pre,
    }


def _write_selected(path: Path, commits: dict[str, str], repo_full_name: str) -> None:
    write_csv(
        path,
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
                "repo_full_name": repo_full_name,
                "ordering_hash": "0" * 64,
                "status": "eligible",
                "exclusion_reason": "",
                "detail": "hermetic fixture",
                "repository_url": "https://example.com/test/repo.git",
                "default_branch": "main",
                "cutoff_commit": commits["cutoff"],
                "adoption_commit": commits["adoption"],
                "adoption_at_utc": T0,
                "valid_skill_paths": ".claude/skills/demo/SKILL.md",
                "production_python_count": 1,
                "test_python_count": 1,
                "retrieved_at_utc": T0,
            }
        ],
    )


@pytest.fixture
def synthetic_project(tmp_path: Path) -> SimpleNamespace:
    project = tmp_path / "project"
    repositories = project / "repositories"
    extraction = project / "data" / "extraction"
    validation = project / "data" / "validation"
    extraction.mkdir(parents=True)
    validation.mkdir(parents=True)

    repo_full_name = "test/repo"
    repository_directory = repositories / repo_full_name.replace("/", "__")
    commits = _build_repository(repositories)

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
                f"window_days = {WINDOW_DAYS}",
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

    selected_path = extraction / "selected_repositories.csv"
    _write_selected(selected_path, commits, repo_full_name)
    run_manifest = extraction / "run_manifest.json"
    run_manifest.write_text(json.dumps({"protocol_hash": "fixture"}) + "\n", encoding="utf-8")

    adoption_path = validation / "adoption_review.csv"
    write_csv(
        adoption_path,
        ADOPTION_REVIEW_FIELDS,
        [
            {
                "sample_position": 1,
                "repo_full_name": repo_full_name,
                "repository_directory": str(repository_directory.resolve()),
                "cutoff_commit": commits["cutoff"],
                "adoption_commit": commits["adoption"],
                "first_parent": commits["first_parent"],
                "adoption_at_utc": T0,
                "pre_window_boundary_utc": PRE_BOUNDARY,
                "post_window_boundary_utc": POST_BOUNDARY,
                "valid_skills_before": "",
                "valid_skills_after": ".claude/skills/demo/SKILL.md",
                "skill_location_diff": "A\t.claude/skills/demo/SKILL.md",
                "human_adoption_decision": "valid",
                "human_rename_only": "no",
                "human_frontmatter_and_paths_valid": "yes",
                "human_notes": "hermetic fixture",
                "reviewer": "Test Reviewer",
                "reviewed_at_utc": "2026-10-05T00:00:00Z",
            }
        ],
    )

    reviewed_paths = [
        (".claude/skills/demo/SKILL.md", "skill"),
        ("pkg/app.py", "production"),
        ("tests/test_app.py", "test"),
        ("README.md", "other"),
    ]
    path_rows = []
    for position, (path, category) in enumerate(reviewed_paths, start=1):
        path_rows.append(
            {
                "review_position": position,
                "repo_full_name": repo_full_name,
                "repository_directory": str(repository_directory.resolve()),
                "commit": commits["adoption"],
                "path": path,
                "automatic_category": category,
                "classification_rule": "fixture",
                "ordering_hash": hashlib.sha256(path.encode()).hexdigest(),
                "human_category": category,
                "agreement": "yes",
                "human_notes": "",
                "reviewer": "Test Reviewer",
                "reviewed_at_utc": "2026-10-05T00:00:00Z",
            }
        )
    path_review_path = validation / "path_review.csv"
    write_csv(path_review_path, PATH_REVIEW_FIELDS, path_rows)

    automated_path = validation / "automated_checks.csv"
    write_csv(
        automated_path,
        AUTOMATED_CHECK_FIELDS,
        [
            {
                "sample_position": 1,
                "repo_full_name": repo_full_name,
                "check": f"fixture_check_{index}",
                "passed": "true",
                "observed": "ok",
                "expected": "ok",
            }
            for index in range(1, 5)
        ],
    )

    validation_manifest = validation / "validation_manifest.json"
    validation_manifest.write_text(
        json.dumps(
            {
                "selected_repository_count": 1,
                "sample_counts": {"skill": 1, "production": 1, "test": 1, "other": 1},
                "automatic_check_count": 4,
                "automatic_checks_passed": 4,
                "selected_repositories_sha256": sha256(selected_path),
                "extraction_manifest_sha256": sha256(run_manifest),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    checksum_path = validation / "human_review.sha256"
    checksum_path.write_text(
        "\n".join(
            [
                f"{sha256(adoption_path)}  data/validation/adoption_review.csv",
                f"{sha256(path_review_path)}  data/validation/path_review.csv",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_config(config_path)
    return SimpleNamespace(
        project=project,
        config_path=config_path,
        config=config,
        repository_directory=repository_directory,
        repo_full_name=repo_full_name,
        commits=commits,
        validation=validation,
        extraction=extraction,
        selected_path=selected_path,
        adoption_path=adoption_path,
        path_review_path=path_review_path,
    )
