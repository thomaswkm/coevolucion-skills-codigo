"""Git diff parsing and path-level change classification."""

from __future__ import annotations

from conftest import GitRepo
from coevolution_skills.process import (
    Commit,
    changes_for_commit,
    classify_change,
    parse_name_status,
)


def make_commit(commit_hash: str, parent: str | None) -> Commit:
    return Commit(
        commit=commit_hash,
        committed_at_utc="2026-01-01T00:00:00Z",
        parents=(parent,) if parent else (),
        period="post",
    )


def test_parse_name_status_add_modify_delete() -> None:
    payload = b"A\x00new.py\x00M\x00same.py\x00D\x00old.py\x00"
    changes = parse_name_status(payload)
    assert [(c.status, c.old_path, c.new_path) for c in changes] == [
        ("A", "", "new.py"),
        ("M", "same.py", "same.py"),
        ("D", "old.py", ""),
    ]


def test_parse_name_status_rename_and_copy() -> None:
    payload = b"R087\x00before.py\x00after.py\x00C100\x00src.py\x00copy.py\x00"
    changes = parse_name_status(payload)
    rename, copy = changes
    assert (rename.status, rename.similarity) == ("R", "087")
    assert (rename.old_path, rename.new_path) == ("before.py", "after.py")
    assert (copy.status, copy.similarity) == ("C", "100")
    assert (copy.old_path, copy.new_path) == ("src.py", "copy.py")


def test_add_modify_delete_classification(git_repo_factory, config_factory) -> None:
    config = config_factory()
    repo: GitRepo = git_repo_factory("changes")

    repo.write("pkg/app.py", "VALUE = 1\n")
    add = repo.commit("add", "2026-01-01T00:00:00Z")

    repo.write("pkg/app.py", "VALUE = 2\n")
    modify = repo.commit("modify", "2026-01-02T00:00:00Z")

    repo.remove("pkg/app.py")
    delete = repo.commit("delete", "2026-01-03T00:00:00Z")

    added = changes_for_commit(repo.path, make_commit(add, None), config)
    assert [(c.status, c.new_path, c.old_path) for c in added] == [("A", "pkg/app.py", "")]
    added_row, added_affected = classify_change(
        repo.path, make_commit(add, None), added[0], config
    )
    assert added_row["new_category"] == "production"
    assert added_affected == [("production", "pkg/app.py")]

    modified = changes_for_commit(repo.path, make_commit(modify, add), config)
    assert modified[0].status == "M"
    modify_row, _ = classify_change(repo.path, make_commit(modify, add), modified[0], config)
    assert (modify_row["old_category"], modify_row["new_category"]) == (
        "production",
        "production",
    )

    deleted = changes_for_commit(repo.path, make_commit(delete, modify), config)
    assert deleted[0].status == "D"
    delete_row, delete_affected = classify_change(
        repo.path, make_commit(delete, modify), deleted[0], config
    )
    assert delete_row["old_category"] == "production"
    assert delete_row["new_category"] == ""
    assert delete_affected == [("production", "pkg/app.py")]


def test_rename_within_category_affects_one_category(git_repo_factory, config_factory) -> None:
    config = config_factory()
    repo: GitRepo = git_repo_factory("rename-same")

    repo.write("pkg/app.py", "VALUE = 1\n")
    add = repo.commit("add", "2026-01-01T00:00:00Z")
    repo.git("mv", "pkg/app.py", "pkg/moved.py")
    rename = repo.commit("rename", "2026-01-02T00:00:00Z")

    change = changes_for_commit(repo.path, make_commit(rename, add), config)[0]
    row, affected = classify_change(repo.path, make_commit(rename, add), change, config)
    assert change.status == "R"
    assert change.old_path == "pkg/app.py"
    assert change.new_path == "pkg/moved.py"
    assert (row["old_category"], row["new_category"]) == ("production", "production")
    assert {category for category, _ in affected} == {"production"}


def test_rename_across_categories_affects_both(git_repo_factory, config_factory) -> None:
    config = config_factory()
    repo: GitRepo = git_repo_factory("rename-cross")

    repo.write("pkg/app.py", "VALUE = 1\n")
    add = repo.commit("add", "2026-01-01T00:00:00Z")
    (repo.path / "tests").mkdir(parents=True, exist_ok=True)
    repo.git("mv", "pkg/app.py", "tests/test_app.py")
    rename = repo.commit("rename across", "2026-01-02T00:00:00Z")

    change = changes_for_commit(repo.path, make_commit(rename, add), config)[0]
    row, affected = classify_change(repo.path, make_commit(rename, add), change, config)
    assert change.status == "R"
    assert (row["old_category"], row["new_category"]) == ("production", "test")
    assert {category for category, _ in affected} == {"production", "test"}
