"""Extraction stopping rules: sample target and screening cap."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import write_csv
from coevolution_skills.extract import (
    ScreeningResult,
    candidate_cap_reached,
    execute,
    load_config,
    protocol_hash,
    record_run_summary,
    serializable_config,
)


CONFIG_TEMPLATE = """\
[dataset]
repository = "mvaccargiu/gitskills"
revision = "{revision}"
artifact_parts = 1

[selection]
target_count = {target_count}
{max_candidates}
seed = "20261004"
history_cutoff_utc = "2026-10-04T23:59:59Z"
window_days = 60

[git]
clone_timeout_seconds = 30
command_timeout_seconds = 30
retry_count = 1
checkout_selected = true

[paths]
output_directory = "data/extraction"
repository_directory = "repositories"
"""


def write_config(
    directory: Path,
    *,
    target_count: int,
    max_candidates: int | None = None,
) -> Path:
    path = directory / "extraction.toml"
    path.write_text(
        CONFIG_TEMPLATE.format(
            revision="0" * 40,
            target_count=target_count,
            max_candidates=(
                "" if max_candidates is None else f"max_candidates = {max_candidates}"
            ),
        ),
        encoding="utf-8",
    )
    return path


def test_protocol_hash_ignores_stopping_rules(config_factory) -> None:
    config = config_factory(target_count=10)
    widened = replace(config, target_count=120, max_candidates=1500)
    assert protocol_hash(config) == protocol_hash(widened)


def test_candidate_cap_reached(config_factory) -> None:
    capped = config_factory(target_count=120, max_candidates=1500)
    assert not candidate_cap_reached(capped, 1499)
    assert candidate_cap_reached(capped, 1500)
    assert candidate_cap_reached(capped, 1501)
    uncapped = config_factory(target_count=120)
    assert not candidate_cap_reached(uncapped, 10**9)


def test_load_config_reads_max_candidates(tmp_path: Path) -> None:
    config = load_config(
        write_config(tmp_path, target_count=120, max_candidates=1500)
    )
    assert config.target_count == 120
    assert config.max_candidates == 1500
    assert serializable_config(config)["max_candidates"] == 1500


def test_load_config_allows_missing_max_candidates(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, target_count=10))
    assert config.max_candidates is None


def test_load_config_rejects_cap_below_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_candidates"):
        load_config(write_config(tmp_path, target_count=120, max_candidates=100))


def test_record_run_summary_marks_cap_reached(config_factory) -> None:
    config = config_factory(target_count=120, max_candidates=1500)
    config.output_directory.mkdir(parents=True)
    manifest_path = config.output_directory / "run_manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")

    record_run_summary(
        config,
        examined_count=1500,
        eligible_count=90,
        completion_status="cap_reached",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["completion_status"] == "cap_reached"
    assert manifest["cap_reached"] is True
    assert manifest["candidates_examined"] == 1500
    assert manifest["eligible_count"] == 90
    assert manifest["target_count"] == 120
    assert manifest["max_candidates"] == 1500


def test_execute_stops_at_candidate_cap(
    config_factory, tmp_path: Path, monkeypatch
) -> None:
    config = config_factory(target_count=3, max_candidates=2)
    config.output_directory.mkdir(parents=True)
    candidates = [
        {
            "candidate_position": position,
            "repo_full_name": f"owner/repo-{position}",
            "eligible_skill_count": 1,
            "ordering_hash": f"{position:064d}",
        }
        for position in (1, 2, 3)
    ]
    write_csv(
        config.output_directory / "candidate_frame.csv",
        ["repo_full_name", "eligible_skill_count"],
        (
            {
                "repo_full_name": row["repo_full_name"],
                "eligible_skill_count": row["eligible_skill_count"],
            }
            for row in candidates
        ),
    )
    write_csv(
        config.output_directory / "candidate_order.csv",
        ["candidate_position", "repo_full_name", "eligible_skill_count", "ordering_hash"],
        candidates,
    )
    screened: list[int] = []

    def fake_screen(candidate, config, temporary_directory, logger):
        screened.append(candidate.candidate_position)
        return (
            ScreeningResult(
                candidate_position=candidate.candidate_position,
                repo_full_name=candidate.repo_full_name,
                ordering_hash=candidate.ordering_hash,
                status="excluded",
                exclusion_reason="test_exclusion",
                detail="hermetic test",
                repository_url="",
            ),
            None,
        )

    monkeypatch.setattr(
        "coevolution_skills.extract.screen_repository", fake_screen
    )

    execute(config, tmp_path / "extraction.toml")

    manifest = json.loads(
        (config.output_directory / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert screened == [1, 2]
    assert manifest["completion_status"] == "cap_reached"
    assert manifest["cap_reached"] is True
    assert manifest["candidates_examined"] == 2
    assert manifest["eligible_count"] == 0
    assert manifest["target_count"] == 3
