"""Skill-location history: hash-only output and non-UTF-8 tolerance."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from conftest import GitRepo
from coevolution_skills.extract import (
    commits_touching_skill_locations,
    git_output,
)


def build_repository_with_merge(repo: GitRepo) -> dict[str, str]:
    """Repository whose merge carries a cp1252 blob through a skill directory."""
    repo.write("README.md", "# Demo\n")
    repo.commit("base", "2026-01-01T00:00:00Z")

    repo.write(
        ".claude/skills/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo skill for history tests.\n---\n# Demo\n",
    )
    skills = repo.commit("add skill", "2026-01-02T00:00:00Z")

    repo.git("checkout", "-b", "feature")
    report = repo.path / ".claude/skills/demo/benchmark.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    # Latin-1/cp1252 "±" (0xb1) is not valid UTF-8.
    report.write_bytes(b"| Metric | Delta |\n|--------|-------|\n| Pass | 0% \xb1 0% |\n")
    repo.commit("feature: benchmark report", "2026-01-03T00:00:00Z")

    repo.git("checkout", "main")
    repo.write("pkg/app.py", "VALUE = 1\n")
    repo.commit("main: production change", "2026-01-04T00:00:00Z")
    repo.git(
        "merge",
        "--no-ff",
        "-m",
        "merge feature",
        "feature",
        date="2026-01-05T00:00:00Z",
    )
    return {"skills": skills, "cutoff": repo.rev_parse("HEAD")}


def test_skill_history_tolerates_non_utf8_patch_bytes(
    git_repo_factory, config_factory
) -> None:
    config = config_factory()
    repo: GitRepo = git_repo_factory("non_utf8_history")
    commits = build_repository_with_merge(repo)

    # The merge is exactly the case that used to dump a patch for a latin-1
    # blob, which made strict locale decoding abort the whole screening run.
    patch_view = git_output(
        repo.path, config, "log", "--format=%H", "--patch", "--root", commits["cutoff"]
    )
    assert "\ufffd" in patch_view

    history = commits_touching_skill_locations(repo.path, commits["cutoff"], config)
    assert all(re.fullmatch(r"[0-9a-f]{40,64}", commit) for commit in history)
    assert set(history) == {commits["skills"], commits["cutoff"]}


def test_skill_history_requests_hash_only_log(
    monkeypatch, tmp_path: Path, config_factory
) -> None:
    config = config_factory()
    recorded: dict[str, list[str]] = {}

    def fake_run_command(args, *, cwd=None, timeout, check=True):
        recorded["args"] = list(args)
        return subprocess.CompletedProcess(args, 0, stdout="a" * 40 + "\n", stderr="")

    monkeypatch.setattr(
        "coevolution_skills.extract.run_command", fake_run_command
    )

    history = commits_touching_skill_locations(tmp_path, "b" * 40, config)

    assert history == ["a" * 40]
    assert "--no-patch" in recorded["args"]
    assert not any(option.startswith("--diff-merges") for option in recorded["args"])
