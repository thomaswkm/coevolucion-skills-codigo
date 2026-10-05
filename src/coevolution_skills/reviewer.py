"""Read-only command helper for reviewing generated validation CSV rows."""

from __future__ import annotations

import argparse
import csv
import shlex
from pathlib import Path, PurePosixPath
from typing import Any

from .extract import (
    SKILL_ROOTS,
    Config,
    git_bytes_output,
    git_output,
    load_config,
    tree_paths,
)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def select_row(rows: list[dict[str, str]], field: str, position: int) -> dict[str, str]:
    for row in rows:
        if int(row[field]) == position:
            return row
    available = ", ".join(row[field] for row in rows)
    raise SystemExit(f"Position {position} not found; available positions: {available}")


def repository_path(row: dict[str, str]) -> Path:
    path = Path(row["repository_directory"])
    path = path if path.is_absolute() else Path.cwd() / path
    if not (path / ".git").is_dir():
        raise SystemExit(f"Repository clone not found: {path}")
    return path


def show_command(repository: Path, *args: str) -> None:
    display_repository = str(repository)
    try:
        display_repository = str(repository.relative_to(Path.cwd()))
    except ValueError:
        pass
    print("$", shlex.join(["git", "-C", display_repository, *args]))


def run_text(repository: Path, config: Config, *args: str) -> str:
    show_command(repository, *args)
    return git_output(repository, config, *args)


def frontmatter_preview(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()
    result: list[str] = []
    delimiters = 0
    for line in lines:
        result.append(line)
        if line.strip() == "---":
            delimiters += 1
            if delimiters == 2:
                break
        if len(result) >= 80:
            result.append("[front matter preview truncated]")
            break
    return "\n".join(result)


def print_header(title: str, row: dict[str, str]) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    print("Repository:", row["repo_full_name"])


def adoption_commands(row: dict[str, str], repository: Path) -> list[list[str]]:
    parent = row["first_parent"]
    adoption = row["adoption_commit"]
    commands = [
        [
            "git",
            "-C",
            str(repository),
            "diff",
            "--name-status",
            "--find-renames",
            "--find-copies-harder",
            parent,
            adoption,
            "--",
            *SKILL_ROOTS,
        ],
        ["git", "-C", str(repository), "rev-list", "--parents", "-n", "1", adoption],
    ]
    for path in filter(None, row["valid_skills_after"].split(";")):
        commands.extend(
            [
                ["git", "-C", str(repository), "ls-tree", adoption, "--", path],
                ["git", "-C", str(repository), "show", f"{adoption}:{path}"],
            ]
        )
    return commands


def review_adoption(
    row: dict[str, str], config: Config, commands_only: bool
) -> None:
    repository = repository_path(row)
    print_header(f"Adoption review #{row['sample_position']}", row)
    print("First parent:", row["first_parent"])
    print("Adoption commit:", row["adoption_commit"])
    print("Adoption UTC:", row["adoption_at_utc"])
    print("Reported valid skills:", row["valid_skills_after"])

    if commands_only:
        for command in adoption_commands(row, repository):
            print("$", shlex.join(command))
        return

    parent = row["first_parent"]
    adoption = row["adoption_commit"]
    print("\n-- Skill path changes --")
    print(
        run_text(
            repository,
            config,
            "diff",
            "--name-status",
            "--find-renames",
            "--find-copies-harder",
            parent,
            adoption,
            "--",
            *SKILL_ROOTS,
        ).strip()
        or "(none)"
    )

    print("\n-- Commit parents --")
    print(run_text(repository, config, "rev-list", "--parents", "-n", "1", adoption).strip())

    # Show only rename/copy summaries and lines involving SKILL.md so a large
    # adoption commit does not flood the terminal.
    print("\n-- Relevant rename/copy summary --")
    show_command(
        repository,
        "diff",
        "--summary",
        "--find-renames",
        "--find-copies-harder",
        parent,
        adoption,
    )
    summary = git_output(
        repository,
        config,
        "diff",
        "--summary",
        "--find-renames",
        "--find-copies-harder",
        parent,
        adoption,
    )
    relevant_summary = [
        line
        for line in summary.splitlines()
        if "rename " in line or "copy " in line or "SKILL.md" in line
    ]
    print("\n".join(relevant_summary) or "(no relevant rename/copy lines)")

    skill_paths = [path for path in row["valid_skills_after"].split(";") if path]
    expected_names = {PurePosixPath(path).parent.name for path in skill_paths}
    parent_skill_like = [
        path
        for path in tree_paths(repository, parent, config)
        if PurePosixPath(path).name == "SKILL.md"
        and PurePosixPath(path).parent.name in expected_names
    ]
    print("\n-- Same-named SKILL.md paths in first parent --")
    print("\n".join(parent_skill_like) or "(none)")

    for path in skill_paths:
        print(f"\n-- Skill: {path} --")
        print(run_text(repository, config, "ls-tree", adoption, "--", path).strip())
        show_command(repository, "show", f"{adoption}:{path}")
        content = git_bytes_output(repository, config, "show", f"{adoption}:{path}")
        print(frontmatter_preview(content))

    print("\nComplete the human_* fields in data/validation/adoption_review.csv.")


def content_preview(content: bytes, maximum_lines: int = 120, maximum_bytes: int = 24_000) -> str:
    limited = content[:maximum_bytes]
    if b"\x00" in limited:
        return f"[binary content; {len(content)} bytes]"
    text = limited.decode("utf-8", errors="replace")
    lines = text.splitlines()
    preview = "\n".join(lines[:maximum_lines])
    if len(content) > maximum_bytes or len(lines) > maximum_lines:
        preview += "\n[preview truncated]"
    return preview


def review_path(row: dict[str, str], config: Config, commands_only: bool) -> None:
    repository = repository_path(row)
    print_header(f"Path review #{row['review_position']}", row)
    print("Commit:", row["commit"])
    print("Path:", row["path"])
    print("Automatic category:", row["automatic_category"])
    print("Rule:", row["classification_rule"])
    object_spec = f"{row['commit']}:{row['path']}"
    commands = [
        ["git", "-C", str(repository), "ls-tree", row["commit"], "--", row["path"]],
        ["git", "-C", str(repository), "cat-file", "-s", object_spec],
        ["git", "-C", str(repository), "show", object_spec],
    ]
    if commands_only:
        for command in commands:
            print("$", shlex.join(command))
        return

    print("\n-- Tree entry --")
    print(run_text(repository, config, "ls-tree", row["commit"], "--", row["path"]).strip())
    print("\n-- Blob size --")
    print(run_text(repository, config, "cat-file", "-s", object_spec).strip(), "bytes")
    print("\n-- Content preview --")
    show_command(repository, "show", object_spec)
    print(content_preview(git_bytes_output(repository, config, "show", object_spec)))
    print("\nComplete the human_* fields in data/validation/path_review.csv.")


def list_progress(validation_directory: Path) -> None:
    adoption_rows = load_rows(validation_directory / "adoption_review.csv")
    path_rows = load_rows(validation_directory / "path_review.csv")
    print("Adoption reviews:")
    for row in adoption_rows:
        decision = row["human_adoption_decision"] or "pending"
        print(f"  {row['sample_position']:>2}  {decision:<9}  {row['repo_full_name']}")
    print("\nPath reviews:")
    for row in path_rows:
        agreement = row["agreement"] or "pending"
        print(
            f"  {row['review_position']:>2}  {agreement:<7}  "
            f"{row['automatic_category']:<10}  {row['repo_full_name']}:{row['path']}"
        )


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Display read-only evidence for one generated validation CSV row."
    )
    parser.add_argument("--config", type=Path, default=Path("extraction.toml"))
    parser.add_argument(
        "--validation-directory", type=Path, default=Path("data/validation")
    )
    parser.add_argument(
        "--commands-only",
        action="store_true",
        help="Print shell commands without executing the read-only Git queries",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="List review positions and completion status")
    adoption = subparsers.add_parser("adoption", help="Review one adoption event")
    adoption.add_argument("position", type=int)
    path = subparsers.add_parser("path", help="Review one sampled path")
    path.add_argument("position", type=int)
    return parser


def main() -> None:
    args = argument_parser().parse_args()
    validation_directory = args.validation_directory.resolve()
    if args.action == "list":
        list_progress(validation_directory)
        return
    config = load_config(args.config)
    if args.action == "adoption":
        rows = load_rows(validation_directory / "adoption_review.csv")
        review_adoption(
            select_row(rows, "sample_position", args.position),
            config,
            args.commands_only,
        )
    elif args.action == "path":
        rows = load_rows(validation_directory / "path_review.csv")
        review_path(
            select_row(rows, "review_position", args.position),
            config,
            args.commands_only,
        )


if __name__ == "__main__":
    main()
