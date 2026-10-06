"""Process auditable commit activity in fixed pre/post-adoption windows."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from .extract import (
    Config,
    file_sha256,
    git_bytes_output,
    git_output,
    git_version,
    load_config,
    parse_datetime,
    serializable_config,
    utc_now,
    write_csv_atomic,
)
from .reviewer import (
    ReviewValidationError,
    validate_human_review_fields,
    validate_review_evidence,
)
from .validation import (
    REVIEW_CATEGORIES,
    SelectedRepository,
    classify_path,
    load_selected,
    repository_path,
)


PROCESSING_PROTOCOL_VERSION = "1"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
PERIODS = ("pre", "post")

COMMIT_ACTIVITY_FIELDS = (
    "repo_full_name",
    "period",
    "commit",
    "committed_at_utc",
    "category",
    "path_count",
    "excluded",
    "exclusion_reason",
)
FILE_CHANGE_FIELDS = (
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
)
ACTIVITY_COUNT_FIELDS = (
    "repo_full_name",
    "period",
    "category",
    "unique_commit_count",
    "data_status",
)


@dataclass(frozen=True)
class Commit:
    commit: str
    committed_at_utc: str
    parents: tuple[str, ...]
    period: str


@dataclass(frozen=True)
class GitChange:
    status: str
    similarity: str
    old_path: str
    new_path: str


def configure_logging(output_directory: Path) -> logging.Logger:
    output_directory.mkdir(parents=True, exist_ok=False)
    logger = logging.getLogger("process-activity")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime
    file_handler = logging.FileHandler(output_directory / "processing.log")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def verify_human_review_checksums(
    validation_directory: Path,
    project_directory: Path,
    *,
    require_human_review: bool = True,
) -> bool:
    """Verify the seal over the two human-review forms.

    A present seal is always verified in full. A missing seal is only an error
    when the human review is required; otherwise it is reported as absent so
    the run can continue and record that the review is still pending.
    """
    checksum_path = validation_directory / "human_review.sha256"
    if not checksum_path.exists():
        if require_human_review:
            raise RuntimeError(
                f"Human-review checksum is required but missing: {checksum_path}"
            )
        return False
    recorded: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        try:
            digest, relative_path = line.split("  ", 1)
        except ValueError as error:
            raise RuntimeError(f"Malformed checksum line in {checksum_path}: {line!r}") from error
        recorded[relative_path] = digest
    expected = {
        "data/validation/adoption_review.csv",
        "data/validation/path_review.csv",
    }
    if set(recorded) != expected:
        raise RuntimeError(
            f"{checksum_path} must contain exactly the two human-review forms"
        )
    for relative_path, digest in recorded.items():
        path = project_directory / relative_path
        if file_sha256(path) != digest:
            raise RuntimeError(f"Human-review checksum failed: {relative_path}")
    return True


def commit_exists(repository: Path, commit: str, config: Config) -> None:
    resolved = git_output(repository, config, "rev-parse", f"{commit}^{{commit}}").strip()
    if resolved != commit:
        raise RuntimeError(f"Commit {commit} is unavailable in {repository}")


def preserved_branch_ref(repository: Path, branch: str, config: Config) -> str:
    for reference in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        try:
            git_output(repository, config, "show-ref", "--verify", reference)
        except Exception:
            continue
        return reference
    raise RuntimeError(f"Default branch {branch!r} is unavailable in {repository}")


def verify_repository(
    repository: Path,
    default_branch: str,
    cutoff_commit: str,
    adoption_commit: str,
    config: Config,
) -> str:
    if not (repository / ".git").is_dir():
        raise RuntimeError(f"Selected clone is missing: {repository}")
    branch_ref = preserved_branch_ref(repository, default_branch, config)
    commit_exists(repository, cutoff_commit, config)
    commit_exists(repository, adoption_commit, config)
    git_output(
        repository,
        config,
        "merge-base",
        "--is-ancestor",
        adoption_commit,
        cutoff_commit,
    )
    git_output(
        repository,
        config,
        "merge-base",
        "--is-ancestor",
        cutoff_commit,
        branch_ref,
    )
    return branch_ref


def commits_in_windows(
    repository: Path,
    cutoff_commit: str,
    adoption_commit: str,
    adoption_at_utc: str,
    config: Config,
) -> tuple[list[Commit], int]:
    adoption_at = parse_datetime(adoption_at_utc)
    pre_start = adoption_at - timedelta(days=config.window_days)
    post_end = adoption_at + timedelta(days=config.window_days)
    # Widen Git's coarse date filter by one second and enforce every boundary
    # below using parsed committer timestamps.
    output = git_output(
        repository,
        config,
        "log",
        "--format=%H%x09%cI%x09%P",
        f"--since-as-filter={(pre_start - timedelta(seconds=1)).isoformat()}",
        f"--until={(post_end + timedelta(seconds=1)).isoformat()}",
        cutoff_commit,
    )
    commits: list[Commit] = []
    boundary_exclusions = 0
    for line in output.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise RuntimeError(f"Unexpected git log record in {repository}: {line!r}")
        commit_hash, timestamp, parent_text = fields
        committed_at = parse_datetime(timestamp)
        period = ""
        if pre_start <= committed_at < adoption_at:
            period = "pre"
        elif adoption_at < committed_at <= post_end:
            period = "post"
        else:
            boundary_exclusions += 1
            continue
        if commit_hash == adoption_commit:
            boundary_exclusions += 1
            continue
        commits.append(
            Commit(
                commit=commit_hash,
                committed_at_utc=committed_at.isoformat().replace("+00:00", "Z"),
                parents=tuple(parent_text.split()) if parent_text else (),
                period=period,
            )
        )
    commits.sort(key=lambda item: (item.committed_at_utc, item.commit))
    return commits, boundary_exclusions


def parse_name_status(payload: bytes) -> list[GitChange]:
    fields = payload.decode("utf-8").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: list[GitChange] = []
    index = 0
    while index < len(fields):
        status_token = fields[index]
        index += 1
        status = status_token[:1]
        if status in {"R", "C"}:
            if index + 1 >= len(fields):
                raise RuntimeError("Truncated rename/copy record from git diff")
            old_path, new_path = fields[index], fields[index + 1]
            index += 2
            similarity = status_token[1:]
        else:
            if index >= len(fields):
                raise RuntimeError("Truncated path record from git diff")
            path = fields[index]
            index += 1
            old_path = path if status != "A" else ""
            new_path = path if status != "D" else ""
            similarity = ""
        changes.append(
            GitChange(
                status=status,
                similarity=similarity,
                old_path=old_path,
                new_path=new_path,
            )
        )
    return changes


def changes_for_commit(
    repository: Path, commit: Commit, config: Config
) -> list[GitChange]:
    parent = commit.parents[0] if commit.parents else EMPTY_TREE
    payload = git_bytes_output(
        repository,
        config,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        parent,
        commit.commit,
        "--",
    )
    return parse_name_status(payload)


def classify_change(
    repository: Path, commit: Commit, change: GitChange, config: Config
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    parent = commit.parents[0] if commit.parents else EMPTY_TREE
    old_category = ""
    new_category = ""
    old_rule = ""
    new_rule = ""
    if change.old_path:
        old_category, old_rule = classify_path(
            repository, parent, change.old_path, config
        )
    if change.new_path:
        new_category, new_rule = classify_path(
            repository, commit.commit, change.new_path, config
        )

    affected: list[tuple[str, str]] = []
    if change.status == "D":
        affected.append((old_category, change.old_path))
    elif change.status in {"R", "C"}:
        affected.append((old_category, change.old_path))
        affected.append((new_category, change.new_path))
    else:
        affected.append((new_category, change.new_path))

    rules = []
    if old_rule:
        rules.append(f"old={old_rule}")
    if new_rule:
        rules.append(f"new={new_rule}")
    row = {
        "repo_full_name": "",
        "period": commit.period,
        "commit": commit.commit,
        "status": change.status,
        "similarity": change.similarity,
        "old_path": change.old_path,
        "new_path": change.new_path,
        "old_category": old_category,
        "new_category": new_category,
        "classification_rule": " | ".join(rules),
    }
    return row, affected


def process_repository(
    selected: SelectedRepository,
    config: Config,
    logger: logging.Logger,
    commit_rows: list[dict[str, Any]],
    file_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    repository = repository_path(config, selected.repo_full_name)
    branch_ref = verify_repository(
        repository,
        selected.default_branch,
        selected.cutoff_commit,
        selected.adoption_commit,
        config,
    )
    commits, boundary_exclusions = commits_in_windows(
        repository,
        selected.cutoff_commit,
        selected.adoption_commit,
        selected.adoption_at_utc,
        config,
    )
    merge_count = 0
    for commit in commits:
        if len(commit.parents) > 1:
            merge_count += 1
            commit_rows.append(
                {
                    "repo_full_name": selected.repo_full_name,
                    "period": commit.period,
                    "commit": commit.commit,
                    "committed_at_utc": commit.committed_at_utc,
                    "category": "",
                    "path_count": 0,
                    "excluded": "true",
                    "exclusion_reason": "merge_commit",
                }
            )
            continue

        category_paths: dict[str, set[str]] = defaultdict(set)
        for change in changes_for_commit(repository, commit, config):
            row, affected = classify_change(repository, commit, change, config)
            row["repo_full_name"] = selected.repo_full_name
            file_rows.append(row)
            for category, path in affected:
                if category and path:
                    category_paths[category].add(path)
        for category in REVIEW_CATEGORIES:
            if category not in category_paths:
                continue
            commit_rows.append(
                {
                    "repo_full_name": selected.repo_full_name,
                    "period": commit.period,
                    "commit": commit.commit,
                    "committed_at_utc": commit.committed_at_utc,
                    "category": category,
                    "path_count": len(category_paths[category]),
                    "excluded": "false",
                    "exclusion_reason": "",
                }
            )
    logger.info(
        "%s: %d commits in windows, %d merges excluded",
        selected.repo_full_name,
        len(commits),
        merge_count,
    )
    return {
        "repo_full_name": selected.repo_full_name,
        "default_branch_ref": branch_ref,
        "commits_in_windows": len(commits),
        "merge_commits_excluded": merge_count,
        "boundary_or_adoption_commits_excluded": boundary_exclusions,
    }


def build_activity_counts(
    selected_repositories: list[SelectedRepository],
    commit_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    observed: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in commit_rows:
        if row["excluded"] == "true" or not row["category"]:
            continue
        key = (row["repo_full_name"], row["period"], row["category"])
        observed[key].add(row["commit"])
    rows = []
    for selected in selected_repositories:
        for period in PERIODS:
            for category in REVIEW_CATEGORIES:
                key = (selected.repo_full_name, period, category)
                rows.append(
                    {
                        "repo_full_name": selected.repo_full_name,
                        "period": period,
                        "category": category,
                        "unique_commit_count": len(observed[key]),
                        "data_status": "observed",
                    }
                )
    return rows


def validate_outputs(
    selected_repositories: list[SelectedRepository],
    commit_rows: list[dict[str, Any]],
    file_rows: list[dict[str, Any]],
    count_rows: list[dict[str, Any]],
    config: Config,
) -> None:
    expected_count_rows = len(selected_repositories) * len(PERIODS) * len(
        REVIEW_CATEGORIES
    )
    if len(count_rows) != expected_count_rows:
        raise RuntimeError(
            f"Expected {expected_count_rows} activity count rows, got {len(count_rows)}"
        )
    count_keys = {
        (row["repo_full_name"], row["period"], row["category"])
        for row in count_rows
    }
    if len(count_keys) != expected_count_rows:
        raise RuntimeError("Duplicate or missing repository/period/category count rows")

    seen_commit_categories: set[tuple[str, str, str, str]] = set()
    counted_commits: set[tuple[str, str]] = set()
    excluded_commits: set[tuple[str, str]] = set()
    adoption_by_repo = {
        item.repo_full_name: (item.adoption_commit, parse_datetime(item.adoption_at_utc))
        for item in selected_repositories
    }
    for row in commit_rows:
        adoption_commit, adoption_at = adoption_by_repo[row["repo_full_name"]]
        if row["commit"] == adoption_commit:
            raise RuntimeError("Adoption commit was included in commit_activity.csv")
        committed_at = parse_datetime(row["committed_at_utc"])
        if row["period"] == "pre":
            valid_date = adoption_at - timedelta(days=config.window_days) <= committed_at < adoption_at
        else:
            valid_date = adoption_at < committed_at <= adoption_at + timedelta(days=config.window_days)
        if not valid_date:
            raise RuntimeError(f"Commit outside its recorded window: {row['commit']}")
        if row["excluded"] == "false":
            counted_commits.add((row["repo_full_name"], row["commit"]))
            key = (
                row["repo_full_name"],
                row["period"],
                row["commit"],
                row["category"],
            )
            if key in seen_commit_categories:
                raise RuntimeError(f"Duplicate commit/category row: {key}")
            seen_commit_categories.add(key)
        else:
            if row["exclusion_reason"] != "merge_commit":
                raise RuntimeError("An excluded commit lacks the merge exclusion reason")
            excluded_commits.add((row["repo_full_name"], row["commit"]))
    if counted_commits & excluded_commits:
        raise RuntimeError("A merge commit was also included in activity counts")

    rebuilt = build_activity_counts(selected_repositories, commit_rows)
    if rebuilt != count_rows:
        raise RuntimeError("Counts cannot be reproduced from commit activity rows")

    category_paths: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    seen_file_changes: set[tuple[str, ...]] = set()
    for row in file_rows:
        file_key = (
            row["repo_full_name"],
            row["period"],
            row["commit"],
            row["status"],
            row["old_path"],
            row["new_path"],
        )
        if file_key in seen_file_changes:
            raise RuntimeError(f"Duplicate file-change row: {file_key}")
        seen_file_changes.add(file_key)
        if row["status"] == "D":
            affected = ((row["old_category"], row["old_path"]),)
        elif row["status"] in {"R", "C"}:
            affected = (
                (row["old_category"], row["old_path"]),
                (row["new_category"], row["new_path"]),
            )
        else:
            affected = ((row["new_category"], row["new_path"]),)
        for category, path in affected:
            if category not in REVIEW_CATEGORIES or not path:
                raise RuntimeError(f"Unauditable category/path in file change: {file_key}")
            category_paths[
                (row["repo_full_name"], row["period"], row["commit"], category)
            ].add(path)

    recorded_activity = {
        (row["repo_full_name"], row["period"], row["commit"], row["category"]): int(
            row["path_count"]
        )
        for row in commit_rows
        if row["excluded"] == "false"
    }
    audited_activity = {key: len(paths) for key, paths in category_paths.items()}
    if recorded_activity != audited_activity:
        raise RuntimeError("Commit categories cannot be reproduced from file changes")

    file_commits = {
        (row["repo_full_name"], row["period"], row["commit"]) for row in file_rows
    }
    active_commits = {
        (row["repo_full_name"], row["period"], row["commit"])
        for row in commit_rows
        if row["excluded"] == "false"
    }
    if not active_commits.issubset(file_commits):
        raise RuntimeError("A counted commit has no auditable file-change rows")


def execute(
    config_path: Path, output_directory: Path, *, require_human_review: bool = True
) -> None:
    config = load_config(config_path)
    logger = configure_logging(output_directory)
    logger.info("Validating preserved extraction and human-review inputs")
    project_directory = config_path.resolve().parent
    validation_directory = project_directory / "data/validation"
    adoption_rows, path_rows = validate_review_evidence(validation_directory, config)
    pending_adoptions, pending_paths = validate_human_review_fields(
        adoption_rows,
        path_rows,
        validation_directory / "adoption_review.csv",
        validation_directory / "path_review.csv",
    )
    if require_human_review and (pending_adoptions or pending_paths):
        raise ReviewValidationError(
            "Human review is incomplete: "
            f"pending adoption positions {pending_adoptions}; "
            f"pending path positions {pending_paths}. "
            "Re-run with --allow-pending-human-review to continue."
        )
    checksum_present = verify_human_review_checksums(
        validation_directory,
        project_directory,
        require_human_review=require_human_review,
    )
    human_screening_review = {
        "required": require_human_review,
        "status": "pending" if (pending_adoptions or pending_paths) else "complete",
        "pending_adoptions": pending_adoptions,
        "pending_paths": pending_paths,
        "checksum_file_present": checksum_present,
    }
    if human_screening_review["status"] == "pending":
        logger.warning(
            "Human screening review is pending (%d adoptions, %d paths); "
            "continuing because --allow-pending-human-review was set",
            len(pending_adoptions),
            len(pending_paths),
        )
    selected_path = config.output_directory / "selected_repositories.csv"
    selected_repositories = load_selected(selected_path)
    if len(selected_repositories) != config.target_count:
        raise RuntimeError(
            f"Expected {config.target_count} selected repositories, "
            f"found {len(selected_repositories)}"
        )

    commit_rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    repository_summaries = []
    for selected in selected_repositories:
        logger.info(
            "Processing repository %d: %s",
            selected.sample_position,
            selected.repo_full_name,
        )
        repository_summaries.append(
            process_repository(selected, config, logger, commit_rows, file_rows)
        )

    commit_rows.sort(
        key=lambda row: (
            row["repo_full_name"],
            PERIODS.index(row["period"]),
            row["committed_at_utc"],
            row["commit"],
            row["category"],
        )
    )
    file_rows.sort(
        key=lambda row: (
            row["repo_full_name"],
            PERIODS.index(row["period"]),
            row["commit"],
            row["status"],
            row["old_path"],
            row["new_path"],
        )
    )
    count_rows = build_activity_counts(selected_repositories, commit_rows)
    validate_outputs(
        selected_repositories, commit_rows, file_rows, count_rows, config
    )

    commit_path = output_directory / "commit_activity.csv"
    changes_path = output_directory / "file_changes.csv"
    counts_path = output_directory / "activity_counts.csv"
    write_csv_atomic(commit_path, list(COMMIT_ACTIVITY_FIELDS), commit_rows)
    write_csv_atomic(changes_path, list(FILE_CHANGE_FIELDS), file_rows)
    write_csv_atomic(counts_path, list(ACTIVITY_COUNT_FIELDS), count_rows)

    input_paths = {
        "configuration": config_path.resolve(),
        "extraction_manifest": config.output_directory / "run_manifest.json",
        "selected_repositories": selected_path,
        "adoption_review": validation_directory / "adoption_review.csv",
        "path_review": validation_directory / "path_review.csv",
        "validation_manifest": validation_directory / "validation_manifest.json",
        "automated_checks": validation_directory / "automated_checks.csv",
    }
    checksum_path = validation_directory / "human_review.sha256"
    if checksum_path.exists():
        input_paths["human_review_checksums"] = checksum_path
    manifest = {
        "created_at_utc": utc_now(),
        "processing_protocol_version": PROCESSING_PROTOCOL_VERSION,
        "configuration": {
            "extraction": serializable_config(config),
            "config_path": str(config_path.resolve()),
            "output_directory": str(output_directory.resolve()),
            "periods": list(PERIODS),
            "categories": list(REVIEW_CATEGORIES),
            "merge_policy": "exclude commits with more than one parent",
            "adoption_policy": "exclude t0 and use committer timestamps normalized to UTC",
        },
        "tool_versions": {"git": git_version(config), "python": sys.version},
        "input_sha256": {
            name: file_sha256(path) for name, path in input_paths.items()
        },
        "output_sha256": {
            "commit_activity": file_sha256(commit_path),
            "file_changes": file_sha256(changes_path),
            "activity_counts": file_sha256(counts_path),
        },
        "row_counts": {
            "commit_activity": len(commit_rows),
            "file_changes": len(file_rows),
            "activity_counts": len(count_rows),
        },
        "human_screening_review": human_screening_review,
        "repositories": repository_summaries,
    }
    (output_directory / "processing_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    logger.info(
        "Processing complete: %d commit-category rows, %d file changes, %d counts",
        len(commit_rows),
        len(file_rows),
        len(count_rows),
    )


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process commit activity in fixed pre/post-adoption windows."
    )
    parser.add_argument(
        "--config", type=Path, default=Path("extraction.toml"), help="Extraction TOML"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed"),
        help="New output directory; it must not already exist",
    )
    parser.add_argument(
        "--allow-pending-human-review",
        action="store_true",
        help=(
            "Continue when the screening review forms have blank human fields "
            "(preserved evidence and automated checks are still enforced)"
        ),
    )
    return parser


def main() -> None:
    args = argument_parser().parse_args()
    execute(
        args.config.resolve(),
        args.output.resolve(),
        require_human_review=not args.allow_pending_human_review,
    )


if __name__ == "__main__":
    main()
