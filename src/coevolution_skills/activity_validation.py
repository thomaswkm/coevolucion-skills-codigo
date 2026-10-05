"""Prepare and review the human validation of changed paths in the windows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import shlex
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .extract import (
    Config,
    file_sha256,
    git_bytes_output,
    git_output,
    git_version,
    load_config,
    parse_datetime,
    utc_now,
    write_csv_atomic,
)
from .reviewer import (
    ReviewValidationError,
    content_preview,
    load_rows,
    repository_path,
)
from .validation import REVIEW_CATEGORIES, display_path, load_selected, repository_path as _repo_dir_path


ACTIVITY_VALIDATION_PROTOCOL_VERSION = "1"
GUARANTEED_STATUSES = ("D", "R")
ACTIVITY_REVIEW_FIELDS = (
    "review_position",
    "repo_full_name",
    "repository_directory",
    "period",
    "commit",
    "parent_commit",
    "status",
    "similarity",
    "old_path",
    "old_category",
    "new_path",
    "new_category",
    "automatic_category",
    "classification_rule",
    "ordering_hash",
    "human_category",
    "agreement",
    "human_notes",
    "reviewer",
    "reviewed_at_utc",
)
YES_NO = {"yes", "no"}


def configure_logging(output_directory: Path) -> logging.Logger:
    output_directory.mkdir(parents=True, exist_ok=False)
    logger = logging.getLogger("prepare-activity-validation")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime
    file_handler = logging.FileHandler(output_directory / "activity_validation.log")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def effective_category(row: dict[str, str]) -> str:
    return row["new_category"] or row["old_category"]


def activity_seed(config: Config) -> str:
    return f"{config.seed}:activity-validation:{ACTIVITY_VALIDATION_PROTOCOL_VERSION}"


def ordering_hash(seed: str, category: str, row: dict[str, str]) -> str:
    payload = ":".join(
        [
            seed,
            category,
            row["repo_full_name"],
            row["commit"],
            row["status"],
            row["old_path"],
            row["new_path"],
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def load_changes(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ReviewValidationError(f"No file changes found in {path}")
    return rows


def first_parent(repository: Path, commit: str, config: Config) -> str:
    line = git_output(repository, config, "rev-list", "--parents", "-n", "1", commit).strip()
    fields = line.split()
    return fields[1] if len(fields) > 1 else ""


def identity(row: dict[str, str]) -> tuple[str, ...]:
    return (
        row["repo_full_name"],
        row["commit"],
        row["status"],
        row["old_path"],
        row["new_path"],
    )


def select_changes(
    rows: Iterable[dict[str, str]], category: str, maximum: int, seed: str
) -> list[dict[str, str]]:
    eligible: list[dict[str, str]] = []
    for row in rows:
        if effective_category(row) != category:
            continue
        enriched = dict(row)
        enriched["automatic_category"] = category
        enriched["ordering_hash"] = ordering_hash(seed, category, row)
        eligible.append(enriched)
    eligible.sort(
        key=lambda row: (
            row["ordering_hash"],
            row["repo_full_name"],
            row["commit"],
            row["status"],
            row["old_path"],
            row["new_path"],
        )
    )
    selected: list[dict[str, str]] = []
    chosen: set[tuple[str, ...]] = set()

    def add(row: dict[str, str]) -> bool:
        key = identity(row)
        if key in chosen:
            return False
        chosen.add(key)
        selected.append(row)
        return True

    for status in GUARANTEED_STATUSES:
        if len(selected) >= maximum:
            break
        for row in eligible:
            if row["status"] == status:
                add(row)
                break
    represented = {row["repo_full_name"] for row in selected}
    for row in eligible:
        if len(selected) >= maximum:
            break
        if row["repo_full_name"] in represented:
            continue
        if add(row):
            represented.add(row["repo_full_name"])
    for row in eligible:
        if len(selected) >= maximum:
            break
        add(row)
    return selected[:maximum]


def write_review_instructions(path: Path, sample_counts: dict[str, int]) -> None:
    counts = ", ".join(f"{category}={sample_counts[category]}" for category in REVIEW_CATEGORIES)
    content = f"""# Revisión manual de rutas modificadas

Se muestrearon cambios efectivamente ocurridos en las ventanas previa y
posterior: {counts}.

## 1. Completar la revisión

Completa `human_category`, `agreement`, `human_notes`, `reviewer` y
`reviewed_at_utc` en `activity_review.csv`. Categorías permitidas: `skill`,
`production`, `test` y `other`; `agreement` debe ser `yes` o `no`. Para
eliminaciones y renombrados el formulario incluye la ruta anterior y la nueva.

Para inspeccionar un cambio:

```bash
uv run review-activity-validation show <review_position>
```

## 2. Cierre

Valida y sella los formularios:

```bash
uv run review-activity-validation validate --seal
```

## 3. Si aparece un error evidente

No edites `data/processed/` en sitio. Corrige la regla de clasificación,
incrementa la versión del protocolo de procesamiento en
`src/coevolution_skills/process.py`, reprocesa en un directorio nuevo y repite
esta revisión desde el inicio. Documenta el ajuste en el manifiesto y en el
`README.md` del proyecto.
"""
    path.write_text(content, encoding="utf-8")


def prepare(
    config: Config,
    output_directory: Path,
    changes_path: Path,
    maximum_per_category: int,
) -> None:
    if maximum_per_category < 1:
        raise ValueError("maximum_per_category must be positive")
    logger = configure_logging(output_directory)
    selected = load_selected(config.output_directory / "selected_repositories.csv")
    selected_names = {item.repo_full_name for item in selected}
    changes = load_changes(changes_path)

    processing_manifest_path = changes_path.parent / "processing_manifest.json"
    processing_manifest = json.loads(processing_manifest_path.read_text(encoding="utf-8"))
    recorded = processing_manifest.get("output_sha256", {}).get("file_changes")
    if recorded != file_sha256(changes_path):
        raise ReviewValidationError(
            f"{changes_path} does not match processing_manifest.json"
        )

    unknown = {row["repo_full_name"] for row in changes} - selected_names
    if unknown:
        raise ReviewValidationError(f"Changes reference unselected repositories: {sorted(unknown)}")

    seed = activity_seed(config)
    sampled: list[dict[str, str]] = []
    sample_counts: dict[str, int] = {}
    for category in REVIEW_CATEGORIES:
        category_sample = select_changes(changes, category, maximum_per_category, seed)
        sampled.extend(category_sample)
        sample_counts[category] = len(category_sample)

    form_rows = []
    for position, row in enumerate(sampled, start=1):
        repository = _repo_dir_path(config, row["repo_full_name"])
        parent = first_parent(repository, row["commit"], config) if row["old_path"] else ""
        form_rows.append(
            {
                "review_position": position,
                "repo_full_name": row["repo_full_name"],
                "repository_directory": display_path(repository),
                "period": row["period"],
                "commit": row["commit"],
                "parent_commit": parent,
                "status": row["status"],
                "similarity": row["similarity"],
                "old_path": row["old_path"],
                "old_category": row["old_category"],
                "new_path": row["new_path"],
                "new_category": row["new_category"],
                "automatic_category": row["automatic_category"],
                "classification_rule": row["classification_rule"],
                "ordering_hash": row["ordering_hash"],
                "human_category": "",
                "agreement": "",
                "human_notes": "",
                "reviewer": "",
                "reviewed_at_utc": "",
            }
        )

    write_csv_atomic(output_directory / "activity_review.csv", list(ACTIVITY_REVIEW_FIELDS), form_rows)
    write_review_instructions(output_directory / "README.md", sample_counts)

    manifest = {
        "created_at_utc": utc_now(),
        "activity_validation_protocol_version": ACTIVITY_VALIDATION_PROTOCOL_VERSION,
        "processing_protocol_version": processing_manifest.get("processing_protocol_version"),
        "activity_validation_seed": seed,
        "maximum_per_category": maximum_per_category,
        "sampling_method": (
            "SHA-256 ordering within effective category; guarantee one deletion and "
            "one rename per category when present; cover each repository once; then fill"
        ),
        "sample_counts": sample_counts,
        "status_distribution": dict(Counter(row["status"] for row in sampled)),
        "category_distribution": dict(Counter(row["automatic_category"] for row in sampled)),
        "selected_repository_count": len(selected),
        "input_sha256": {
            "file_changes": file_sha256(changes_path),
            "processing_manifest": file_sha256(processing_manifest_path),
            "selected_repositories": file_sha256(
                config.output_directory / "selected_repositories.csv"
            ),
        },
        "tool_versions": {"git": git_version(config), "python": sys.version},
    }
    (output_directory / "activity_validation_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    logger.info("Prepared %d sampled changes for manual review", len(form_rows))


def load_changes_index(changes_path: Path) -> dict[tuple[str, ...], str]:
    index: dict[tuple[str, ...], str] = {}
    with changes_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            index[
                (
                    row["repo_full_name"],
                    row["commit"],
                    row["status"],
                    row["old_path"],
                    row["new_path"],
                )
            ] = effective_category(row)
    return index


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
        raise ReviewValidationError(f"{location}: {field} must use normalized UTC form ending in Z")


def validate_activity_review(
    validation_directory: Path, changes_path: Path, *, seal: bool = False
) -> list[dict[str, str]]:
    form_path = validation_directory / "activity_review.csv"
    rows = load_rows(form_path, ACTIVITY_REVIEW_FIELDS)
    manifest = json.loads(
        (validation_directory / "activity_validation_manifest.json").read_text(encoding="utf-8")
    )
    expected = sum(int(value) for value in manifest["sample_counts"].values())
    if len(rows) != expected:
        raise ReviewValidationError(
            f"{form_path}: expected {expected} rows, found {len(rows)}"
        )
    positions = [int(row["review_position"]) for row in rows]
    if positions != list(range(1, expected + 1)):
        raise ReviewValidationError(f"{form_path}: review_position must be contiguous from 1")

    index = load_changes_index(changes_path)
    for row in rows:
        location = f"{form_path}: row {row['review_position']}"
        key = (
            row["repo_full_name"],
            row["commit"],
            row["status"],
            row["old_path"],
            row["new_path"],
        )
        if key not in index:
            raise ReviewValidationError(f"{location}: change is not in file_changes.csv")
        if row["automatic_category"] != index[key]:
            raise ReviewValidationError(
                f"{location}: automatic_category does not match file_changes.csv"
            )
        if row["human_category"] not in REVIEW_CATEGORIES:
            raise ReviewValidationError(
                f"{location}: human_category must be one of {', '.join(REVIEW_CATEGORIES)}"
            )
        if row["agreement"] not in YES_NO:
            raise ReviewValidationError(f"{location}: agreement must be yes or no")
        agrees = row["human_category"] == row["automatic_category"]
        if (row["agreement"] == "yes") != agrees:
            raise ReviewValidationError(
                f"{location}: agreement conflicts with automatic and human categories"
            )
        if row["agreement"] == "no":
            _require_text(row, "human_notes", location)
        _require_text(row, "reviewer", location)
        _require_utc(row, "reviewed_at_utc", location)
    if seal:
        checksum_path = validation_directory / "human_activity_review.sha256"
        if checksum_path.exists():
            raise ReviewValidationError(f"{checksum_path} already exists; refusing to reseal")
        checksum_path.write_text(
            f"{file_sha256(form_path)}  {display_path(form_path)}\n", encoding="utf-8"
        )
    return rows


def list_progress(rows: list[dict[str, str]]) -> None:
    print("Activity reviews:")
    for row in rows:
        agreement = row["agreement"] or "pending"
        print(
            f"  {row['review_position']:>2}  {agreement:<7}  {row['status']}  "
            f"{row['automatic_category']:<10}  {row['repo_full_name']}:{row['new_path'] or row['old_path']}"
        )


def show_change(row: dict[str, str], config: Config, commands_only: bool) -> None:
    repository = repository_path(row)
    print(f"\n{'=' * 78}\nActivity review #{row['review_position']}\n{'=' * 78}")
    print("Repository:", row["repo_full_name"])
    print("Period:", row["period"])
    print("Commit:", row["commit"])
    print("Parent:", row["parent_commit"] or "(none)")
    print("Status:", row["status"], "similarity:", row["similarity"] or "-")
    print("Old path:", row["old_path"] or "(none)", "|", row["old_category"] or "-")
    print("New path:", row["new_path"] or "(none)", "|", row["new_category"] or "-")
    print("Automatic category:", row["automatic_category"])
    print("Rule:", row["classification_rule"])

    old_spec = f"{row['parent_commit']}:{row['old_path']}" if row["old_path"] and row["parent_commit"] else ""
    new_spec = f"{row['commit']}:{row['new_path']}" if row["new_path"] else ""
    paths = [path for path in (row["old_path"], row["new_path"]) if path]
    commands = []
    if old_spec:
        commands.append(["git", "-C", str(repository), "show", old_spec])
    if new_spec:
        commands.append(["git", "-C", str(repository), "show", new_spec])
    if row["parent_commit"]:
        commands.append(
            [
                "git",
                "-C",
                str(repository),
                "diff",
                "--name-status",
                "--find-renames",
                row["parent_commit"],
                row["commit"],
                "--",
                *paths,
            ]
        )
    if commands_only:
        for command in commands:
            print("$", shlex.join(command))
        return

    if old_spec:
        print(f"\n-- Previous content: {row['old_path']} --")
        print(content_preview(git_bytes_output(repository, config, "show", old_spec)))
    if new_spec:
        print(f"\n-- New content: {row['new_path']} --")
        print(content_preview(git_bytes_output(repository, config, "show", new_spec)))
    if row["parent_commit"]:
        print("\n-- Git status --")
        print(
            git_output(
                repository,
                config,
                "diff",
                "--name-status",
                "--find-renames",
                row["parent_commit"],
                row["commit"],
                "--",
                *paths,
            ).strip()
            or "(none)"
        )
    print("\nComplete the human fields in data/activity-validation/activity_review.csv.")


def select_row(rows: list[dict[str, str]], position: int) -> dict[str, str]:
    for row in rows:
        if int(row["review_position"]) == position:
            return row
    raise SystemExit(f"Position {position} not found")


def prepare_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare deterministic evidence for changed-path classification review."
    )
    parser.add_argument("--config", type=Path, default=Path("extraction.toml"))
    parser.add_argument("--changes", type=Path, default=Path("data/processed/file_changes.csv"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/activity-validation"),
        help="New output directory; it must not already exist",
    )
    parser.add_argument("--maximum-per-category", type=int, default=10)
    return parser


def prepare_main() -> None:
    args = prepare_argument_parser().parse_args()
    config = load_config(args.config)
    prepare(config, args.output.resolve(), args.changes.resolve(), args.maximum_per_category)


def review_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only helper for the changed-path validation forms."
    )
    parser.add_argument("--config", type=Path, default=Path("extraction.toml"))
    parser.add_argument(
        "--activity-validation-directory",
        type=Path,
        default=Path("data/activity-validation"),
    )
    parser.add_argument("--changes", type=Path, default=Path("data/processed/file_changes.csv"))
    parser.add_argument("--commands-only", action="store_true")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="List sampled changes and completion status")
    show = subparsers.add_parser("show", help="Show evidence for one sampled change")
    show.add_argument("position", type=int)
    validate = subparsers.add_parser("validate", help="Validate (and optionally seal) the forms")
    validate.add_argument("--seal", action="store_true")
    return parser


def review_main() -> None:
    args = review_argument_parser().parse_args()
    directory = args.activity_validation_directory.resolve()
    form_path = directory / "activity_review.csv"
    if args.action == "list":
        rows = load_rows(form_path, ACTIVITY_REVIEW_FIELDS)
        list_progress(rows)
        return
    if args.action == "show":
        config = load_config(args.config)
        rows = load_rows(form_path, ACTIVITY_REVIEW_FIELDS)
        show_change(select_row(rows, args.position), config, args.commands_only)
        return
    if args.action == "validate":
        try:
            validate_activity_review(directory, args.changes.resolve(), seal=args.seal)
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f"Invalid activity review: {error}") from error
        message = "Activity review is complete."
        if args.seal:
            message += " Sealed human_activity_review.sha256."
        print(message)
