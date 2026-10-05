"""Strict structural validation of the human-review CSV forms."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

import pytest

from coevolution_skills.reviewer import ReviewValidationError, load_rows, validate_review_package
from coevolution_skills.validation import ADOPTION_REVIEW_FIELDS, PATH_REVIEW_FIELDS


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def rewrite(path: Path, fields: Iterable[str], rows: Iterable[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_valid_package_passes(synthetic_project) -> None:
    adoptions, paths = validate_review_package(
        synthetic_project.validation, synthetic_project.config
    )
    assert len(adoptions) == 1
    assert len(paths) == 4


def test_extra_column_is_rejected(synthetic_project) -> None:
    path = synthetic_project.adoption_path
    rows = read_rows(path)
    fields = [*ADOPTION_REVIEW_FIELDS, "unexpected"]
    for row in rows:
        row["unexpected"] = "x"
    rewrite(path, fields, rows)

    with pytest.raises(ReviewValidationError):
        load_rows(path, ADOPTION_REVIEW_FIELDS)


def test_missing_column_is_rejected(synthetic_project) -> None:
    path = synthetic_project.adoption_path
    rows = read_rows(path)
    fields = list(ADOPTION_REVIEW_FIELDS[:-1])
    trimmed = [{key: row[key] for key in fields} for row in rows]
    rewrite(path, fields, trimmed)

    with pytest.raises(ReviewValidationError):
        load_rows(path, ADOPTION_REVIEW_FIELDS)


def test_extra_data_row_is_rejected(synthetic_project) -> None:
    path = synthetic_project.adoption_path
    rows = read_rows(path)
    rewrite(path, ADOPTION_REVIEW_FIELDS, [*rows, dict(rows[0])])

    with pytest.raises(ReviewValidationError):
        validate_review_package(synthetic_project.validation, synthetic_project.config)


def test_incomplete_human_fields_are_rejected(synthetic_project) -> None:
    path = synthetic_project.adoption_path
    rows = read_rows(path)
    rows[0]["reviewer"] = ""
    rewrite(path, ADOPTION_REVIEW_FIELDS, rows)

    with pytest.raises(ReviewValidationError):
        validate_review_package(synthetic_project.validation, synthetic_project.config)


def test_inconsistent_agreement_is_rejected(synthetic_project) -> None:
    path = synthetic_project.path_review_path
    rows = read_rows(path)
    rows[0]["human_category"] = "production" if rows[0]["automatic_category"] != "production" else "test"
    rows[0]["agreement"] = "yes"
    rewrite(path, PATH_REVIEW_FIELDS, rows)

    with pytest.raises(ReviewValidationError):
        validate_review_package(synthetic_project.validation, synthetic_project.config)
