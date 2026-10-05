"""Classification rules shared by screening and activity processing."""

from __future__ import annotations

from pathlib import Path

import pytest

from coevolution_skills.extract import valid_skill_document
from coevolution_skills.validation import classify_path

from conftest import GitRepo


def document(name: str = "demo", description: str = "A demo skill.") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n# Demo\n".encode()


@pytest.mark.parametrize(
    "content, expected_name, expected",
    [
        (document(), "demo", True),
        (document(description=""), "demo", False),
        (document(name="Wrong"), "demo", False),
        (document(name="demo-"), "demo", False),
        (document(name="demo--skill"), "demo", False),
        (b"# no front matter\n", "demo", False),
        (b"---\nname: demo\n---\n", "demo", False),
        (document(description="x" * 1025), "demo", False),
    ],
)
def test_valid_skill_document(content: bytes, expected_name: str, expected: bool) -> None:
    assert valid_skill_document(content, expected_name) is expected


@pytest.mark.parametrize(
    "path, expected",
    [
        ("pkg/app.py", "production"),
        ("src/main.py", "production"),
        ("tests/test_app.py", "test"),
        ("test_root.py", "test"),
        ("pkg/app_test.py", "test"),
        ("conftest.py", "test"),
        ("pkg/tests/helper.py", "test"),
        ("README.md", "other"),
        ("docs/guide.py", "other"),
        ("pkg/docs/guide.py", "other"),
        ("build/generated.py", "other"),
        ("dist/generated.py", "other"),
        ("venv/lib.py", "other"),
        ("__pycache__/cached.py", "other"),
        ("examples/demo.py", "other"),
        (".claude/skills/demo/scripts/run.py", "other"),
        ("tests/fixtures/data.yaml", "other"),
    ],
)
def test_classify_path_without_git(path: str, expected: str, config_factory) -> None:
    config = config_factory()
    category, rule = classify_path(Path("."), "", path, config)
    assert category == expected
    assert rule


def test_skill_takes_precedence_over_test_and_production(git_repo_factory, config_factory) -> None:
    repo: GitRepo = git_repo_factory("precedence")
    config = config_factory()
    repo.write("pkg/app.py", "VALUE = 1\n")
    repo.write("tests/test_app.py", "def test_app():\n    assert True\n")
    repo.write(
        ".claude/skills/demo/SKILL.md",
        "---\nname: demo\ndescription: Demo skill.\n---\n",
    )
    commit = repo.commit("add files", "2026-01-01T00:00:00Z")

    assert classify_path(repo.path, commit, ".claude/skills/demo/SKILL.md", config)[0] == "skill"
    assert classify_path(repo.path, commit, "tests/test_app.py", config)[0] == "test"
    assert classify_path(repo.path, commit, "pkg/app.py", config)[0] == "production"


def test_invalid_skill_document_is_other(git_repo_factory, config_factory) -> None:
    repo: GitRepo = git_repo_factory("invalid-skill")
    config = config_factory()
    repo.write(
        ".claude/skills/demo/SKILL.md",
        "---\nname: not-demo\ndescription: Name does not match the directory.\n---\n",
    )
    commit = repo.commit("invalid skill", "2026-01-01T00:00:00Z")

    category, rule = classify_path(repo.path, commit, ".claude/skills/demo/SKILL.md", config)
    assert category == "other"
    assert "SKILL.md" in rule
