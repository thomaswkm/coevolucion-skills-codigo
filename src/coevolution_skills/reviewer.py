"""Read-only command helper for reviewing generated validation CSV rows."""

from __future__ import annotations

import argparse
import csv
import json
import shlex
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from .extract import (
    SKILL_ROOTS,
    Config,
    file_sha256,
    git_bytes_output,
    git_output,
    load_config,
    parse_datetime,
    tree_paths,
)
from .validation import (
    ADOPTION_REVIEW_FIELDS,
    PATH_REVIEW_FIELDS,
    REVIEW_CATEGORIES,
    display_path,
)


ADOPTION_DECISIONS = {"valid", "invalid", "ambiguous"}
YES_NO = {"yes", "no"}
AUTOMATED_CHECK_FIELDS = (
    "sample_position",
    "repo_full_name",
    "check",
    "passed",
    "observed",
    "expected",
)


class ReviewValidationError(ValueError):
    """A human-review form is malformed, inconsistent, or incomplete."""


def load_rows(
    path: Path, expected_fields: tuple[str, ...] | None = None
) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, strict=True)
        if expected_fields is not None and tuple(reader.fieldnames or ()) != expected_fields:
            raise ReviewValidationError(
                f"{path}: expected exactly {len(expected_fields)} columns in this order: "
                f"{', '.join(expected_fields)}"
            )
        rows = list(reader)
    if any(None in row for row in rows):
        raise ReviewValidationError(f"{path}: at least one row has extra or shifted fields")
    return rows


def _require_choice(
    row: dict[str, str], field: str, choices: set[str], location: str
) -> None:
    if row[field] not in choices:
        allowed = ", ".join(sorted(choices))
        raise ReviewValidationError(
            f"{location}: {field} must be one of {allowed}; got {row[field]!r}"
        )


def _require_text(row: dict[str, str], field: str, location: str) -> None:
    if not row[field].strip():
        raise ReviewValidationError(f"{location}: {field} is required")


def _require_utc(row: dict[str, str], field: str, location: str) -> None:
    _require_text(row, field, location)
    try:
        parsed = parse_datetime(row[field])
    except ValueError as error:
        raise ReviewValidationError(f"{location}: invalid {field}: {error}") from error
    if parsed.isoformat().replace("+00:00", "Z") != row[field]:
        raise ReviewValidationError(
            f"{location}: {field} must use normalized UTC form ending in Z"
        )


def _contiguous_positions(
    rows: list[dict[str, str]], field: str, expected_count: int, path: Path
) -> None:
    if len(rows) != expected_count:
        raise ReviewValidationError(
            f"{path}: expected {expected_count} data rows, found {len(rows)}"
        )
    try:
        positions = [int(row[field]) for row in rows]
    except ValueError as error:
        raise ReviewValidationError(f"{path}: {field} must contain integers") from error
    expected = list(range(1, expected_count + 1))
    if positions != expected:
        raise ReviewValidationError(
            f"{path}: {field} must be unique and contiguous; got {positions}"
        )


def _selected_rows(config: Config) -> list[dict[str, str]]:
    path = config.output_directory / "selected_repositories.csv"
    rows = load_rows(path)
    if len(rows) != config.target_count:
        raise ReviewValidationError(
            f"{path}: expected {config.target_count} selected repositories, found {len(rows)}"
        )
    return rows


def validate_review_package(
    validation_directory: Path, config: Config
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Validate exact schemas, preserved evidence, and completed human fields."""
    adoption_path = validation_directory / "adoption_review.csv"
    path_review_path = validation_directory / "path_review.csv"
    adoption_rows = load_rows(adoption_path, ADOPTION_REVIEW_FIELDS)
    path_rows = load_rows(path_review_path, PATH_REVIEW_FIELDS)
    selected = _selected_rows(config)

    manifest_path = validation_directory / "validation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected_path = config.output_directory / "selected_repositories.csv"
    extraction_manifest_path = config.output_directory / "run_manifest.json"
    for field, path in (
        ("selected_repositories_sha256", selected_path),
        ("extraction_manifest_sha256", extraction_manifest_path),
    ):
        if manifest[field] != file_sha256(path):
            raise ReviewValidationError(
                f"{manifest_path}: {field} does not match {path}"
            )

    automated_path = validation_directory / "automated_checks.csv"
    automated_rows = load_rows(automated_path, AUTOMATED_CHECK_FIELDS)
    expected_check_count = int(manifest["automatic_check_count"])
    if len(automated_rows) != expected_check_count:
        raise ReviewValidationError(
            f"{automated_path}: expected {expected_check_count} checks, "
            f"found {len(automated_rows)}"
        )
    passed_count = sum(row["passed"] == "true" for row in automated_rows)
    if passed_count != int(manifest["automatic_checks_passed"]):
        raise ReviewValidationError(
            f"{automated_path}: passed count does not match the manifest"
        )
    if passed_count != len(automated_rows):
        raise ReviewValidationError(f"{automated_path}: not every automatic check passed")

    expected_adoptions = int(manifest["selected_repository_count"])
    expected_paths = sum(int(value) for value in manifest["sample_counts"].values())
    _contiguous_positions(adoption_rows, "sample_position", expected_adoptions, adoption_path)
    _contiguous_positions(path_rows, "review_position", expected_paths, path_review_path)

    selected_by_repo = {row["repo_full_name"]: row for row in selected}
    for row, source in zip(adoption_rows, selected, strict=True):
        location = f"{adoption_path}: row {row['sample_position']}"
        expected_values = {
            "sample_position": source["sample_position"],
            "repo_full_name": source["repo_full_name"],
            "repository_directory": display_path(
                config.repository_directory / source["repo_full_name"].replace("/", "__")
            ),
            "cutoff_commit": source["cutoff_commit"],
            "adoption_commit": source["adoption_commit"],
            "adoption_at_utc": source["adoption_at_utc"],
            "valid_skills_after": source["valid_skill_paths"],
        }
        adoption_at = parse_datetime(source["adoption_at_utc"])
        expected_values["pre_window_boundary_utc"] = (
            adoption_at - timedelta(days=config.window_days)
        ).isoformat().replace("+00:00", "Z")
        expected_values["post_window_boundary_utc"] = (
            adoption_at + timedelta(days=config.window_days)
        ).isoformat().replace("+00:00", "Z")
        for field, expected in expected_values.items():
            if row[field] != expected:
                raise ReviewValidationError(
                    f"{location}: preserved {field} does not match extraction evidence"
                )
        if row["valid_skills_before"]:
            raise ReviewValidationError(
                f"{location}: valid_skills_before must be empty for an adoption"
            )
        for field in ("first_parent", "skill_location_diff"):
            _require_text(row, field, location)
        _require_choice(row, "human_adoption_decision", ADOPTION_DECISIONS, location)
        _require_choice(row, "human_rename_only", YES_NO, location)
        _require_choice(row, "human_frontmatter_and_paths_valid", YES_NO, location)
        _require_text(row, "reviewer", location)
        _require_utc(row, "reviewed_at_utc", location)
        if (
            row["human_adoption_decision"] != "valid"
            or row["human_rename_only"] != "no"
            or row["human_frontmatter_and_paths_valid"] != "yes"
        ):
            _require_text(row, "human_notes", location)

    category_counts = {category: 0 for category in REVIEW_CATEGORIES}
    for row in path_rows:
        location = f"{path_review_path}: row {row['review_position']}"
        source = selected_by_repo.get(row["repo_full_name"])
        if source is None:
            raise ReviewValidationError(f"{location}: repository is not in the selected sample")
        expected_directory = display_path(
            config.repository_directory / row["repo_full_name"].replace("/", "__")
        )
        if row["repository_directory"] != expected_directory:
            raise ReviewValidationError(
                f"{location}: repository_directory does not match extraction evidence"
            )
        if row["commit"] != source["adoption_commit"]:
            raise ReviewValidationError(
                f"{location}: commit does not match the preserved adoption commit"
            )
        _require_choice(row, "automatic_category", set(REVIEW_CATEGORIES), location)
        _require_choice(row, "human_category", set(REVIEW_CATEGORIES), location)
        _require_choice(row, "agreement", YES_NO, location)
        _require_text(row, "classification_rule", location)
        _require_text(row, "ordering_hash", location)
        _require_text(row, "reviewer", location)
        _require_utc(row, "reviewed_at_utc", location)
        agrees = row["human_category"] == row["automatic_category"]
        if (row["agreement"] == "yes") != agrees:
            raise ReviewValidationError(
                f"{location}: agreement conflicts with automatic and human categories"
            )
        if row["agreement"] == "no":
            _require_text(row, "human_notes", location)
        category_counts[row["automatic_category"]] += 1
    expected_counts = {key: int(value) for key, value in manifest["sample_counts"].items()}
    if category_counts != expected_counts:
        raise ReviewValidationError(
            f"{path_review_path}: category counts {category_counts} do not match manifest "
            f"{expected_counts}"
        )
    return adoption_rows, path_rows


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


def list_progress(
    adoption_rows: list[dict[str, str]], path_rows: list[dict[str, str]]
) -> None:
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
    subparsers.add_parser("validate", help="Validate both completed review forms")
    adoption = subparsers.add_parser("adoption", help="Review one adoption event")
    adoption.add_argument("position", type=int)
    path = subparsers.add_parser("path", help="Review one sampled path")
    path.add_argument("position", type=int)
    return parser


def main() -> None:
    args = argument_parser().parse_args()
    validation_directory = args.validation_directory.resolve()
    config = load_config(args.config)
    try:
        adoption_rows, path_rows = validate_review_package(validation_directory, config)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"Invalid validation package: {error}") from error
    if args.action == "list":
        list_progress(adoption_rows, path_rows)
        return
    if args.action == "validate":
        print(
            f"Validation package is complete: {len(adoption_rows)} adoptions, "
            f"{len(path_rows)} paths."
        )
        return
    if args.action == "adoption":
        review_adoption(
            select_row(adoption_rows, "sample_position", args.position),
            config,
            args.commands_only,
        )
    elif args.action == "path":
        review_path(
            select_row(path_rows, "review_position", args.position),
            config,
            args.commands_only,
        )


if __name__ == "__main__":
    main()
