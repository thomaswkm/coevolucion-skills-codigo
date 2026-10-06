"""Descriptive RQ1/RQ2 statistics and consistency checks."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from conftest import write_csv
from coevolution_skills.analyze import (
    COUNT_FIELDS,
    execute,
    load_counts,
    quantile,
    summarize,
)


def make_counts(path: Path, rows: list[dict[str, object]]) -> None:
    write_csv(path, COUNT_FIELDS, rows)


def repo_rows(name: str, pre: dict[str, int], post: dict[str, int]) -> list[dict[str, object]]:
    rows = []
    for period, values in (("pre", pre), ("post", post)):
        for category, count in values.items():
            rows.append(
                {
                    "repo_full_name": name,
                    "period": period,
                    "category": category,
                    "unique_commit_count": count,
                    "data_status": "observed",
                }
            )
    return rows


@pytest.fixture
def processed(tmp_path: Path) -> Path:
    directory = tmp_path / "data" / "processed"
    directory.mkdir(parents=True)
    rows = []
    rows += repo_rows(
        "a/b",
        {"skill": 0, "production": 1, "test": 0, "other": 5},
        {"skill": 0, "production": 5, "test": 1, "other": 2},
    )
    rows += repo_rows(
        "c/d",
        {"skill": 1, "production": 4, "test": 1, "other": 0},
        {"skill": 2, "production": 2, "test": 2, "other": 1},
    )
    rows += repo_rows(
        "e/f",
        {"skill": 0, "production": 0, "test": 0, "other": 0},
        {"skill": 1, "production": 0, "test": 0, "other": 3},
    )
    make_counts(directory / "activity_counts.csv", rows)
    (directory / "processing_manifest.json").write_text(
        json.dumps({"processing_protocol_version": "1"}) + "\n", encoding="utf-8"
    )
    return directory


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_quantile_linear_interpolation() -> None:
    values = [0, 1, 2]
    assert quantile(values, 0.5) == 1.0
    assert quantile(values, 0.25) == 0.5
    assert quantile(values, 0.75) == 1.5
    stats = summarize(values)
    assert stats.iqr == 1.0
    assert stats.mean == 1.0
    assert stats.total == 3


def test_rq1_counts_and_ties(processed, config_factory, tmp_path) -> None:
    output = tmp_path / "results"
    execute(config_factory(), processed, output)

    rq1 = {row["repo_full_name"]: row for row in read_csv(output / "rq1_repository_counts.csv")}
    assert rq1["a/b"]["largest_categories"] == "production"
    assert rq1["a/b"]["tie"] == "false"
    assert rq1["c/d"]["largest_categories"] == "skill;production;test"
    assert rq1["c/d"]["tie"] == "true"
    assert rq1["e/f"]["largest_categories"] == "skill"

    summary = {row["category"]: row for row in read_csv(output / "rq1_summary.csv")}
    assert summary["skill"]["zero_count"] == "1"
    assert summary["skill"]["median"] == "1.0"
    assert summary["skill"]["q1"] == "0.5"
    assert summary["skill"]["iqr"] == "1.0"
    assert summary["production"]["zero_count"] == "1"
    assert summary["skill"]["repos_with_largest"] == "2"
    assert summary["production"]["repos_with_largest"] == "2"
    assert summary["test"]["repos_with_largest"] == "1"

    manifest = json.loads((output / "analysis_manifest.json").read_text())
    assert manifest["tie_repository_count"] == 1
    assert manifest["repository_count"] == 3
    assert manifest["inferential_tests"] is False


def test_rq2_differences_and_direction(processed, config_factory, tmp_path) -> None:
    output = tmp_path / "results"
    execute(config_factory(), processed, output)

    differences = {
        row["repo_full_name"]: row
        for row in read_csv(output / "rq2_repository_differences.csv")
    }
    assert differences["a/b"]["production_difference"] == "4"
    assert differences["c/d"]["production_difference"] == "-2"
    assert differences["e/f"]["production_difference"] == "0"
    assert differences["a/b"]["test_difference"] == "1"

    summary = {row["category"]: row for row in read_csv(output / "rq2_summary.csv")}
    production = summary["production"]
    assert production["positive"] == "1"
    assert production["negative"] == "1"
    assert production["zero"] == "1"
    assert production["pre_total"] == "5"
    assert production["post_total"] == "7"
    assert summary["test"]["positive"] == "2"
    assert summary["test"]["zero"] == "1"


def test_consistency_checks_reconstruct(processed, config_factory, tmp_path) -> None:
    output = tmp_path / "results"
    execute(config_factory(), processed, output)
    text = (output / "consistency_checks.txt").read_text()
    assert "MISMATCH" not in text
    assert "OK" in text
    assert "No inferential test" in text


def test_incomplete_data_is_rejected(processed, config_factory, tmp_path) -> None:
    path = processed / "activity_counts.csv"
    rows = read_csv(path)
    rows[0]["data_status"] = "missing"
    write_csv(path, COUNT_FIELDS, rows)
    with pytest.raises(ValueError):
        load_counts(path)
    with pytest.raises(ValueError):
        execute(config_factory(), processed, tmp_path / "results")


def test_existing_output_is_rejected(processed, config_factory, tmp_path) -> None:
    output = tmp_path / "results"
    output.mkdir()
    with pytest.raises(FileExistsError):
        execute(config_factory(), processed, output)


def test_screening_review_status_is_propagated(processed, config_factory, tmp_path) -> None:
    manifest_path = processed / "processing_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["human_screening_review"] = {
        "required": False,
        "status": "pending",
        "pending_adoptions": [1],
        "pending_paths": [],
        "checksum_file_present": False,
    }
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    output = tmp_path / "results"
    execute(config_factory(), processed, output)

    result = json.loads((output / "analysis_manifest.json").read_text())
    assert result["human_screening_review"]["status"] == "pending"
    assert result["human_screening_review"]["pending_adoptions"] == [1]
