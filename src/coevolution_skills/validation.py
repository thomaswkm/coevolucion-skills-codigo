"""Prepare reproducible evidence forms for human validation of screening."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .extract import (
    EXCLUDED_PRODUCTION_PARTS,
    SKILL_PATH_RE,
    SKILL_ROOT_PARTS,
    SKILL_ROOTS,
    Config,
    file_sha256,
    git_bytes_output,
    git_output,
    load_config,
    parse_datetime,
    tree_paths,
    utc_now,
    valid_skill_document,
    valid_skills_at,
    write_csv_atomic,
)


VALIDATION_PROTOCOL_VERSION = "2"
REVIEW_CATEGORIES = ("skill", "production", "test", "other")


@dataclass(frozen=True)
class SelectedRepository:
    sample_position: int
    repo_full_name: str
    default_branch: str
    cutoff_commit: str
    adoption_commit: str
    adoption_at_utc: str
    reported_skill_paths: tuple[str, ...]
    reported_production_count: int
    reported_test_count: int


@dataclass(frozen=True)
class PathCandidate:
    repo_full_name: str
    repository_directory: str
    commit: str
    path: str
    automatic_category: str
    classification_rule: str
    ordering_hash: str


def configure_logging(output_directory: Path) -> logging.Logger:
    output_directory.mkdir(parents=True, exist_ok=False)
    logger = logging.getLogger("prepare-validation")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime
    file_handler = logging.FileHandler(output_directory / "validation.log")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def load_selected(path: Path) -> list[SelectedRepository]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    selected = [
        SelectedRepository(
            sample_position=int(row["sample_position"]),
            repo_full_name=row["repo_full_name"],
            default_branch=row["default_branch"],
            cutoff_commit=row["cutoff_commit"],
            adoption_commit=row["adoption_commit"],
            adoption_at_utc=row["adoption_at_utc"],
            reported_skill_paths=tuple(
                path for path in row["valid_skill_paths"].split(";") if path
            ),
            reported_production_count=int(row["production_python_count"]),
            reported_test_count=int(row["test_python_count"]),
        )
        for row in rows
    ]
    selected.sort(key=lambda item: item.sample_position)
    if not selected:
        raise ValueError(f"No selected repositories found in {path}")
    expected = list(range(1, len(selected) + 1))
    actual = [item.sample_position for item in selected]
    if actual != expected:
        raise ValueError(f"sample_position is not contiguous: {actual}")
    return selected


def repository_path(config: Config, repo_full_name: str) -> Path:
    return config.repository_directory / repo_full_name.replace("/", "__")


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path.resolve())


def classify_path(
    repository: Path, commit: str, path_text: str, config: Config
) -> tuple[str, str]:
    skill_match = SKILL_PATH_RE.fullmatch(path_text)
    if skill_match:
        content = git_bytes_output(repository, config, "show", f"{commit}:{path_text}")
        if valid_skill_document(content, skill_match.group(1)):
            return "skill", "valid SKILL.md in an accepted root-anchored location"

    path = PurePosixPath(path_text)
    if path.suffix != ".py":
        return "other", "not a .py file and not a valid accepted SKILL.md"
    parts = path.parts
    if (
        len(parts) >= 4
        and tuple(parts[:2]) in SKILL_ROOT_PARTS
    ):
        return "other", "Python file inside a skill directory"
    excluded = next(
        (part for part in parts[:-1] if part in EXCLUDED_PRODUCTION_PARTS), None
    )
    if excluded:
        return "other", f"Python file under excluded directory component: {excluded}"
    filename = path.name
    if "test" in parts[:-1] or "tests" in parts[:-1]:
        return "test", "Python file under test/ or tests/"
    if filename.startswith("test_"):
        return "test", "filename matches test_*.py"
    if filename.endswith("_test.py"):
        return "test", "filename matches *_test.py"
    if filename == "conftest.py":
        return "test", "filename is conftest.py"
    return "production", "other non-excluded .py file"


def git_check(repository: Path, config: Config, *args: str) -> bool:
    try:
        git_output(repository, config, *args)
    except Exception:
        return False
    return True


def add_check(
    rows: list[dict[str, Any]],
    selected: SelectedRepository,
    check: str,
    passed: bool,
    observed: Any,
    expected: Any,
) -> None:
    rows.append(
        {
            "sample_position": selected.sample_position,
            "repo_full_name": selected.repo_full_name,
            "check": check,
            "passed": str(bool(passed)).lower(),
            "observed": observed,
            "expected": expected,
        }
    )


def diff_name_status(
    repository: Path, parent: str, commit: str, config: Config
) -> str:
    output = git_output(
        repository,
        config,
        "diff",
        "--name-status",
        "--find-renames",
        "--find-copies-harder",
        parent,
        commit,
        "--",
        *SKILL_ROOTS,
    )
    return " | ".join(line for line in output.splitlines() if line)


def prepare_repository_evidence(
    selected: SelectedRepository,
    config: Config,
    validation_seed: str,
    checks: list[dict[str, Any]],
    adoption_rows: list[dict[str, Any]],
    path_candidates: list[PathCandidate],
) -> None:
    repository = repository_path(config, selected.repo_full_name)
    if not (repository / ".git").is_dir():
        raise RuntimeError(f"Selected clone is missing: {repository}")

    cutoff_exists = git_check(
        repository, config, "cat-file", "-e", f"{selected.cutoff_commit}^{{commit}}"
    )
    adoption_exists = git_check(
        repository, config, "cat-file", "-e", f"{selected.adoption_commit}^{{commit}}"
    )
    add_check(checks, selected, "cutoff_commit_exists", cutoff_exists, cutoff_exists, True)
    add_check(checks, selected, "adoption_commit_exists", adoption_exists, adoption_exists, True)
    if not cutoff_exists or not adoption_exists:
        raise RuntimeError(f"Required commits are missing in {selected.repo_full_name}")

    ancestry = git_check(
        repository,
        config,
        "merge-base",
        "--is-ancestor",
        selected.adoption_commit,
        selected.cutoff_commit,
    )
    add_check(checks, selected, "adoption_reaches_cutoff", ancestry, ancestry, True)

    parent_line = git_output(
        repository,
        config,
        "rev-list",
        "--parents",
        "-n",
        "1",
        selected.adoption_commit,
    ).strip()
    parent_fields = parent_line.split()
    first_parent = parent_fields[1] if len(parent_fields) > 1 else ""
    add_check(checks, selected, "first_parent_exists", bool(first_parent), first_parent, "non-empty")
    if not first_parent:
        raise RuntimeError(f"Adoption commit has no first parent: {selected.repo_full_name}")

    skills_before = valid_skills_at(repository, first_parent, config)
    skills_after = valid_skills_at(repository, selected.adoption_commit, config)
    add_check(checks, selected, "valid_skills_before_is_zero", not skills_before, len(skills_before), 0)
    add_check(checks, selected, "valid_skills_after_positive", bool(skills_after), len(skills_after), "> 0")
    reported_skills = sorted(selected.reported_skill_paths)
    add_check(
        checks,
        selected,
        "reported_skill_paths_match",
        skills_after == reported_skills,
        ";".join(skills_after),
        ";".join(reported_skills),
    )

    actual_timestamp = git_output(
        repository, config, "show", "-s", "--format=%cI", selected.adoption_commit
    ).strip()
    timestamp_matches = parse_datetime(actual_timestamp) == parse_datetime(
        selected.adoption_at_utc
    )
    add_check(
        checks,
        selected,
        "adoption_committer_timestamp_matches",
        timestamp_matches,
        actual_timestamp,
        selected.adoption_at_utc,
    )

    adoption_at = parse_datetime(actual_timestamp)
    pre_boundary = adoption_at - timedelta(days=config.window_days)
    pre_commit = git_output(
        repository,
        config,
        "rev-list",
        "--first-parent",
        "-1",
        f"--before={pre_boundary.isoformat()}",
        selected.cutoff_commit,
    ).strip()
    post_boundary = adoption_at + timedelta(days=config.window_days)
    add_check(checks, selected, "pre_window_observable", bool(pre_commit), pre_commit, "non-empty")
    add_check(
        checks,
        selected,
        "post_window_before_cutoff",
        post_boundary <= config.cutoff,
        post_boundary.isoformat(),
        f"<= {config.cutoff.isoformat()}",
    )

    status = diff_name_status(repository, first_parent, selected.adoption_commit, config)
    adoption_rows.append(
        {
            "sample_position": selected.sample_position,
            "repo_full_name": selected.repo_full_name,
            "repository_directory": display_path(repository),
            "cutoff_commit": selected.cutoff_commit,
            "adoption_commit": selected.adoption_commit,
            "first_parent": first_parent,
            "adoption_at_utc": selected.adoption_at_utc,
            "pre_window_boundary_utc": pre_boundary.isoformat().replace("+00:00", "Z"),
            "post_window_boundary_utc": post_boundary.isoformat().replace("+00:00", "Z"),
            "valid_skills_before": ";".join(skills_before),
            "valid_skills_after": ";".join(skills_after),
            "skill_location_diff": status,
            "human_adoption_decision": "",
            "human_rename_only": "",
            "human_frontmatter_and_paths_valid": "",
            "human_notes": "",
            "reviewer": "",
            "reviewed_at_utc": "",
        }
    )

    automatic_counts = {category: 0 for category in REVIEW_CATEGORIES}
    for path in tree_paths(repository, selected.adoption_commit, config):
        category, rule = classify_path(repository, selected.adoption_commit, path, config)
        automatic_counts[category] += 1
        digest = hashlib.sha256(
            f"{validation_seed}:{category}:{selected.repo_full_name}:{path}".encode()
        ).hexdigest()
        path_candidates.append(
            PathCandidate(
                repo_full_name=selected.repo_full_name,
                repository_directory=display_path(repository),
                commit=selected.adoption_commit,
                path=path,
                automatic_category=category,
                classification_rule=rule,
                ordering_hash=digest,
            )
        )
    add_check(
        checks,
        selected,
        "production_path_count_matches",
        automatic_counts["production"] == selected.reported_production_count,
        automatic_counts["production"],
        selected.reported_production_count,
    )
    add_check(
        checks,
        selected,
        "test_path_count_matches",
        automatic_counts["test"] == selected.reported_test_count,
        automatic_counts["test"],
        selected.reported_test_count,
    )


def stratified_sample(
    candidates: Iterable[PathCandidate], category: str, maximum: int
) -> list[PathCandidate]:
    eligible = sorted(
        (item for item in candidates if item.automatic_category == category),
        key=lambda item: (item.ordering_hash, item.repo_full_name, item.path),
    )
    selected: list[PathCandidate] = []
    represented: set[str] = set()
    for item in eligible:
        if item.repo_full_name not in represented:
            selected.append(item)
            represented.add(item.repo_full_name)
            if len(selected) == maximum:
                return selected
    selected_keys = {(item.repo_full_name, item.path) for item in selected}
    for item in eligible:
        if (item.repo_full_name, item.path) in selected_keys:
            continue
        selected.append(item)
        if len(selected) == maximum:
            break
    return selected


def write_review_instructions(
    path: Path, selected_count: int, sample_counts: dict[str, int]
) -> None:
    counts = ", ".join(f"{category}={sample_counts[category]}" for category in REVIEW_CATEGORIES)
    content = f"""# Revisión manual del screening

Se preparó evidencia para {selected_count} repositorios y las siguientes rutas:
{counts}.

## 1. Eventos de adopción

Completa las columnas `human_*`, `reviewer` y `reviewed_at_utc` de
`adoption_review.csv`. Para cada fila comprueba el diff entre `first_parent` y
`adoption_commit`, la ausencia de skills válidos antes, su presencia después y
que no sea solamente un renombrado engañoso. Valores sugeridos para
`human_adoption_decision`: `valid`, `invalid` o `ambiguous`.

## 2. Rutas

Completa `human_category`, `agreement`, `human_notes`, `reviewer` y
`reviewed_at_utc` en `path_review.csv`. Categorías permitidas: `skill`,
`production`, `test` y `other`; `agreement` debe ser `yes` o `no`.

Para inspeccionar un archivo sin cambiar el checkout:

```bash
git -C <repository_directory> show '<commit>:<path>'
```

Para inspeccionar el evento de adopción:

```bash
git -C <repository_directory> diff --find-renames <first_parent> <adoption_commit>
```

## 3. Cierre

No edites `automated_checks.csv` ni `validation_manifest.json`. Si encuentras un
error evidente, registra el caso y corrige el protocolo antes de repetir
extracción y validación. No calcules precisión o recall con esta revisión
acotada.
"""
    path.write_text(content, encoding="utf-8")


def execute(config: Config, output_directory: Path, maximum_per_category: int) -> None:
    if maximum_per_category < 1:
        raise ValueError("maximum_per_category must be positive")
    logger = configure_logging(output_directory)
    selected_path = config.output_directory / "selected_repositories.csv"
    extraction_manifest_path = config.output_directory / "run_manifest.json"
    selected = load_selected(selected_path)
    validation_seed = f"{config.seed}:manual-validation:{VALIDATION_PROTOCOL_VERSION}"
    logger.info("Preparing validation evidence for %d repositories", len(selected))

    checks: list[dict[str, Any]] = []
    adoption_rows: list[dict[str, Any]] = []
    path_candidates: list[PathCandidate] = []
    for item in selected:
        logger.info("Preparing repository %d: %s", item.sample_position, item.repo_full_name)
        prepare_repository_evidence(
            item, config, validation_seed, checks, adoption_rows, path_candidates
        )

    sampled: list[PathCandidate] = []
    sample_counts: dict[str, int] = {}
    for category in REVIEW_CATEGORIES:
        category_sample = stratified_sample(
            path_candidates, category, maximum_per_category
        )
        sampled.extend(category_sample)
        sample_counts[category] = len(category_sample)
    sampled.sort(
        key=lambda item: (
            REVIEW_CATEGORIES.index(item.automatic_category),
            item.ordering_hash,
        )
    )

    check_fields = [
        "sample_position",
        "repo_full_name",
        "check",
        "passed",
        "observed",
        "expected",
    ]
    write_csv_atomic(output_directory / "automated_checks.csv", check_fields, checks)
    adoption_fields = list(adoption_rows[0])
    write_csv_atomic(
        output_directory / "adoption_review.csv", adoption_fields, adoption_rows
    )
    path_rows = []
    for review_position, item in enumerate(sampled, start=1):
        row = {
            "review_position": review_position,
            **item.__dict__,
            "human_category": "",
            "agreement": "",
            "human_notes": "",
            "reviewer": "",
            "reviewed_at_utc": "",
        }
        path_rows.append(row)
    path_fields = [
        "review_position",
        "repo_full_name",
        "repository_directory",
        "commit",
        "path",
        "automatic_category",
        "classification_rule",
        "ordering_hash",
        "human_category",
        "agreement",
        "human_notes",
        "reviewer",
        "reviewed_at_utc",
    ]
    write_csv_atomic(output_directory / "path_review.csv", path_fields, path_rows)
    write_review_instructions(
        output_directory / "README.md", len(selected), sample_counts
    )

    manifest = {
        "created_at_utc": utc_now(),
        "validation_protocol_version": VALIDATION_PROTOCOL_VERSION,
        "validation_seed": validation_seed,
        "maximum_paths_per_category": maximum_per_category,
        "sample_counts": sample_counts,
        "selected_repository_count": len(selected),
        "selected_repositories_sha256": file_sha256(selected_path),
        "extraction_manifest_sha256": file_sha256(extraction_manifest_path),
        "automatic_check_count": len(checks),
        "automatic_checks_passed": sum(row["passed"] == "true" for row in checks),
        "sampling_method": (
            "SHA-256 ordering within category; first cover each repository once, "
            "then fill remaining positions"
        ),
    }
    (output_directory / "validation_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    logger.info("Validation package prepared; human review is still required")


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare deterministic evidence forms for human screening validation."
    )
    parser.add_argument(
        "--config", type=Path, default=Path("extraction.toml"), help="Extraction TOML"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/validation"),
        help="New output directory; it must not already exist",
    )
    parser.add_argument(
        "--maximum-per-category",
        type=int,
        default=10,
        help="Maximum manually reviewed paths for each category",
    )
    return parser


def main() -> None:
    args = argument_parser().parse_args()
    config = load_config(args.config)
    execute(config, args.output.resolve(), args.maximum_per_category)


if __name__ == "__main__":
    main()
