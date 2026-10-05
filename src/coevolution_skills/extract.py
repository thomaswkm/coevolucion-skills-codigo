"""Select and extract eligible repositories without computing RQ metrics."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import duckdb
import yaml


SCREENING_PROTOCOL_VERSION = "2"
# Project-local roots documented by at least one selected client. The primary
# analysis intentionally uses root-anchored paths; nested monorepo and plugin
# locations remain outside the homogeneous population definition.
SKILL_ROOTS = (
    ".agent/skills",      # Antigravity backward compatibility
    ".agents/skills",     # interoperable: Codex, Cursor, Gemini, Antigravity
    ".claude/skills",     # Claude Code; also recognized by Cursor and Copilot
    ".codex/skills",      # Codex compatibility path recognized by Cursor
    ".cursor/skills",     # Cursor
    ".gemini/skills",     # Gemini CLI
    ".github/skills",     # GitHub Copilot
    ".opencode/skills",   # OpenCode
)
SKILL_ROOT_PARTS = {tuple(PurePosixPath(root).parts) for root in SKILL_ROOTS}
SKILL_PATH_RE = re.compile(
    r"^(?:" + "|".join(re.escape(root) for root in SKILL_ROOTS) + r")/([^/]+)/SKILL\.md$"
)
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EXCLUDED_PRODUCTION_PARTS = {
    "venv",
    ".venv",
    "build",
    "dist",
    "__pycache__",
    "docs",
    "doc",
    "examples",
    "example",
}


class ScreeningCode(StrEnum):
    ELIGIBLE = "eligible"
    REPOSITORY_UNAVAILABLE = "repository_unavailable"
    CLONE_FAILED = "clone_failed"
    DEFAULT_BRANCH_MISSING = "default_branch_missing"
    HISTORY_INCOMPLETE = "history_incomplete"
    NO_VALID_SKILL = "no_valid_skill"
    ADOPTION_NOT_FOUND = "adoption_not_found"
    INSUFFICIENT_PRE_WINDOW = "insufficient_pre_window"
    INSUFFICIENT_POST_WINDOW = "insufficient_post_window"
    NO_PRODUCTION_PYTHON = "no_production_python"
    NO_TEST_PYTHON = "no_test_python"
    PYTHON_ONLY_INSIDE_SKILL = "python_only_inside_skill"
    HISTORY_REWRITTEN_OR_CORRUPT = "history_rewritten_or_corrupt"
    UNEXPECTED_ERROR = "unexpected_error"


@dataclass(frozen=True)
class Config:
    dataset_repository: str
    dataset_revision: str
    artifact_parts: int
    target_count: int
    seed: str
    history_cutoff_utc: str
    window_days: int
    clone_timeout_seconds: int
    command_timeout_seconds: int
    retry_count: int
    checkout_selected: bool
    output_directory: Path
    repository_directory: Path

    @property
    def cutoff(self) -> datetime:
        return parse_datetime(self.history_cutoff_utc)


@dataclass
class Candidate:
    candidate_position: int
    repo_full_name: str
    eligible_skill_count: int
    ordering_hash: str


@dataclass
class ScreeningResult:
    candidate_position: int
    repo_full_name: str
    ordering_hash: str
    status: str
    exclusion_reason: str
    detail: str
    repository_url: str
    default_branch: str = ""
    cutoff_commit: str = ""
    adoption_commit: str = ""
    adoption_at_utc: str = ""
    valid_skill_paths: str = ""
    production_python_count: int = 0
    test_python_count: int = 0
    retrieved_at_utc: str = ""


class ScreeningFailure(Exception):
    def __init__(self, code: ScreeningCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp must include a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def load_config(path: Path) -> Config:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    base = path.resolve().parent

    def resolve_path(value: str) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else base / candidate

    config = Config(
        dataset_repository=raw["dataset"]["repository"],
        dataset_revision=raw["dataset"]["revision"],
        artifact_parts=int(raw["dataset"]["artifact_parts"]),
        target_count=int(raw["selection"]["target_count"]),
        seed=str(raw["selection"]["seed"]),
        history_cutoff_utc=raw["selection"]["history_cutoff_utc"],
        window_days=int(raw["selection"]["window_days"]),
        clone_timeout_seconds=int(raw["git"]["clone_timeout_seconds"]),
        command_timeout_seconds=int(raw["git"]["command_timeout_seconds"]),
        retry_count=int(raw["git"]["retry_count"]),
        checkout_selected=bool(raw["git"]["checkout_selected"]),
        output_directory=resolve_path(raw["paths"]["output_directory"]),
        repository_directory=resolve_path(raw["paths"]["repository_directory"]),
    )
    if config.target_count < 1 or config.artifact_parts < 1:
        raise ValueError("target_count and artifact_parts must be positive")
    if config.window_days < 1 or config.retry_count < 1:
        raise ValueError("window_days and retry_count must be positive")
    _ = config.cutoff
    return config


def serializable_config(config: Config) -> dict[str, Any]:
    result = asdict(config)
    result["output_directory"] = str(config.output_directory.resolve())
    result["repository_directory"] = str(config.repository_directory.resolve())
    return result


def protocol_hash(config: Config) -> str:
    # target_count is deliberately excluded: 10 and 485 must be prefixes of
    # the same ordered screening run. Filesystem locations are operational and
    # do not define population membership or ordering.
    frozen_protocol = {
        "screening_protocol_version": SCREENING_PROTOCOL_VERSION,
        "accepted_skill_roots": SKILL_ROOTS,
        "dataset_repository": config.dataset_repository,
        "dataset_revision": config.dataset_revision,
        "artifact_parts": config.artifact_parts,
        "seed": config.seed,
        "history_cutoff_utc": config.history_cutoff_utc,
        "window_days": config.window_days,
        "clone_timeout_seconds": config.clone_timeout_seconds,
        "command_timeout_seconds": config.command_timeout_seconds,
        "retry_count": config.retry_count,
        "checkout_selected": config.checkout_selected,
    }
    payload = json.dumps(frozen_protocol, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def configure_logging(output_directory: Path) -> logging.Logger:
    output_directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("extract-repositories")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime
    file_handler = logging.FileHandler(output_directory / "extraction.log")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update({"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"})
    result = subprocess.run(
        args,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip()[-2000:]
        raise subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout, stderr=stderr
        )
    return result


def git_version(config: Config) -> str:
    return run_command(
        ["git", "--version"], timeout=config.command_timeout_seconds
    ).stdout.strip()


def initialize_manifest(config: Config, config_path: Path) -> None:
    manifest_path = config.output_directory / "run_manifest.json"
    digest = protocol_hash(config)
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        if existing.get("protocol_hash") != digest:
            raise RuntimeError(
                "The output directory belongs to a different protocol. "
                "Use a new output_directory or restore the original configuration."
            )
        previous_repository_directory = existing.get("configuration", {}).get(
            "repository_directory"
        )
        screening_started = any(
            (config.output_directory / "screening").glob("*.json")
        )
        if (
            screening_started
            and previous_repository_directory
            != str(config.repository_directory.resolve())
        ):
            raise RuntimeError(
                "repository_directory cannot change after screening has started"
            )
        existing["last_started_at_utc"] = utc_now()
        existing["last_requested_target_count"] = config.target_count
        existing["configuration"] = serializable_config(config)
        atomic_write_json(manifest_path, existing)
        return
    manifest = {
        "created_at_utc": utc_now(),
        "last_started_at_utc": utc_now(),
        "last_requested_target_count": config.target_count,
        "protocol_hash": digest,
        "config_path": str(config_path.resolve()),
        "configuration": serializable_config(config),
        "git_version": git_version(config),
        "python_version": sys.version,
        "duckdb_version": duckdb.__version__,
        "ordering_definition": 'SHA256(seed + ":" + repo_full_name)',
        "screening_protocol_version": SCREENING_PROTOCOL_VERSION,
        "accepted_skill_roots": list(SKILL_ROOTS),
    }
    atomic_write_json(manifest_path, manifest)


def huggingface_url(config: Config, relative_path: str) -> str:
    return (
        f"https://huggingface.co/datasets/{config.dataset_repository}/resolve/"
        f"{config.dataset_revision}/{relative_path}"
    )


def sql_list(values: Iterable[str]) -> str:
    return "[" + ",".join("'" + value.replace("'", "''") + "'" for value in values) + "]"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_candidate_files(config: Config, candidate_count: int | None = None) -> None:
    manifest_path = config.output_directory / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frame_path = config.output_directory / "candidate_frame.csv"
    order_path = config.output_directory / "candidate_order.csv"
    current = {
        "candidate_frame_sha256": file_sha256(frame_path),
        "candidate_order_sha256": file_sha256(order_path),
    }
    recorded = manifest.get("candidate_files")
    if recorded:
        for key, value in current.items():
            if recorded.get(key) != value:
                raise RuntimeError(
                    f"Frozen candidate file failed its checksum: {key}. "
                    "Use a clean output directory instead of editing generated CSV files."
                )
        return
    if candidate_count is None:
        with order_path.open(newline="", encoding="utf-8") as handle:
            candidate_count = sum(1 for _ in csv.DictReader(handle))
    manifest["candidate_files"] = {**current, "candidate_count": candidate_count}
    atomic_write_json(manifest_path, manifest)


def build_candidate_files(config: Config, logger: logging.Logger) -> None:
    frame_path = config.output_directory / "candidate_frame.csv"
    order_path = config.output_directory / "candidate_order.csv"
    if frame_path.exists() and order_path.exists():
        record_candidate_files(config)
        logger.info("Candidate frame already exists; reusing frozen CSV files")
        return

    logger.info("Building candidate frame from pinned GitSkills Parquet files")
    artifact_urls = [
        huggingface_url(config, f"data/artifacts/part-{index:05d}.parquet")
        for index in range(config.artifact_parts)
    ]
    repos_url = huggingface_url(config, "data/repos/part-00000.parquet")
    skill_path_sql_pattern = (
        "^(" + "|".join(re.escape(root) for root in SKILL_ROOTS) + ")/([^/]+)/SKILL\\.md$"
    )
    database_path = config.output_directory / "candidate_frame.duckdb"
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("SET enable_progress_bar = false")
        connection.execute("INSTALL httpfs")
        connection.execute("LOAD httpfs")
        connection.execute("DROP TABLE IF EXISTS artifact_index")
        connection.execute(
            f"""
            CREATE TABLE artifact_index AS
            SELECT repo_full_name, path, filename, file_sha, dedup_primary,
                   frontmatter_valid, name, description
            FROM read_parquet({sql_list(artifact_urls)}, union_by_name = true)
            """
        )
        rows = connection.execute(
            f"""
            WITH representatives AS (
                SELECT file_sha, frontmatter_valid, name, description
                FROM artifact_index
                WHERE dedup_primary = 1
            ),
            valid_occurrences AS (
                SELECT a.repo_full_name, a.path
                FROM artifact_index AS a
                JOIN representatives AS p USING (file_sha)
                WHERE a.filename = 'SKILL.md'
                  AND regexp_matches(
                      a.path,
                      '{skill_path_sql_pattern}'
                  )
                  AND p.frontmatter_valid = 1
                  AND p.name IS NOT NULL
                  AND length(p.name) BETWEEN 1 AND 64
                  AND regexp_matches(p.name, '^[a-z0-9]+(-[a-z0-9]+)*$')
                  AND p.name = regexp_extract(
                      a.path,
                      '{skill_path_sql_pattern}',
                      2
                  )
                  AND p.description IS NOT NULL
                  AND length(p.description) BETWEEN 1 AND 1024
            )
            SELECT v.repo_full_name, count(*)::BIGINT AS eligible_skill_count
            FROM valid_occurrences AS v
            JOIN read_parquet('{repos_url}') AS r
              ON r.full_name = v.repo_full_name
            WHERE r.language = 'Python'
              AND r.is_fork = 0
              AND r.metadata_fetched = 1
            GROUP BY v.repo_full_name
            ORDER BY v.repo_full_name
            """
        ).fetchall()
    finally:
        connection.close()
        database_path.unlink(missing_ok=True)

    candidates: list[Candidate] = []
    ordered_rows = []
    for repo_full_name, count in rows:
        digest = hashlib.sha256(
            f"{config.seed}:{repo_full_name}".encode("utf-8")
        ).hexdigest()
        ordered_rows.append((digest, repo_full_name, int(count)))
    ordered_rows.sort(key=lambda row: (row[0], row[1]))
    for position, (digest, repo_full_name, count) in enumerate(ordered_rows, start=1):
        candidates.append(Candidate(position, repo_full_name, count, digest))

    write_csv_atomic(
        frame_path,
        ["repo_full_name", "eligible_skill_count"],
        ({"repo_full_name": row[0], "eligible_skill_count": row[1]} for row in rows),
    )
    write_csv_atomic(
        order_path,
        list(Candidate.__dataclass_fields__),
        (asdict(candidate) for candidate in candidates),
    )
    record_candidate_files(config, len(candidates))
    logger.info("Candidate frame frozen with %d repositories", len(candidates))


def load_candidates(path: Path) -> list[Candidate]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            Candidate(
                candidate_position=int(row["candidate_position"]),
                repo_full_name=row["repo_full_name"],
                eligible_skill_count=int(row["eligible_skill_count"]),
                ordering_hash=row["ordering_hash"],
            )
            for row in csv.DictReader(handle)
        ]


def default_branch(
    url: str, config: Config, logger: logging.Logger
) -> tuple[ScreeningCode | None, str, str]:
    last_detail = ""
    for attempt in range(1, config.retry_count + 1):
        try:
            result = run_command(
                ["git", "ls-remote", "--symref", url, "HEAD"],
                timeout=config.command_timeout_seconds,
                check=False,
            )
            if result.returncode == 0:
                match = re.search(r"^ref:\s+refs/heads/(.+)\s+HEAD$", result.stdout, re.M)
                if match:
                    return None, match.group(1), ""
                return ScreeningCode.DEFAULT_BRANCH_MISSING, "", result.stdout.strip()
            last_detail = result.stderr.strip()[-2000:]
        except subprocess.TimeoutExpired:
            last_detail = "git ls-remote timed out"
        logger.warning("Remote lookup failed (attempt %d/%d): %s", attempt, config.retry_count, url)
        if attempt < config.retry_count:
            time.sleep(attempt * 2)
    return ScreeningCode.REPOSITORY_UNAVAILABLE, "", last_detail


def clone_repository(
    url: str,
    branch: str,
    target: Path,
    config: Config,
    logger: logging.Logger,
) -> None:
    last_detail = ""
    for attempt in range(1, config.retry_count + 1):
        if target.exists():
            shutil.rmtree(target)
        try:
            result = run_command(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    "--single-branch",
                    "--branch",
                    branch,
                    url,
                    str(target),
                ],
                timeout=config.clone_timeout_seconds,
                check=False,
            )
            if result.returncode == 0:
                return
            last_detail = result.stderr.strip()[-2000:]
        except subprocess.TimeoutExpired:
            last_detail = "git clone timed out"
        logger.warning("Clone failed (attempt %d/%d): %s", attempt, config.retry_count, url)
        if attempt < config.retry_count:
            time.sleep(attempt * 5)
    raise ScreeningFailure(ScreeningCode.CLONE_FAILED, last_detail)


def git_output(repository: Path, config: Config, *args: str) -> str:
    last_error: Exception | None = None
    for attempt in range(1, config.retry_count + 1):
        try:
            return run_command(
                ["git", *args], cwd=repository, timeout=config.command_timeout_seconds
            ).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            last_error = error
            if attempt < config.retry_count:
                time.sleep(attempt * 2)
    raise ScreeningFailure(
        ScreeningCode.HISTORY_REWRITTEN_OR_CORRUPT, str(last_error)
    ) from last_error


def git_bytes_output(repository: Path, config: Config, *args: str) -> bytes:
    last_detail = ""
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    for attempt in range(1, config.retry_count + 1):
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=repository,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=config.command_timeout_seconds,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout
            last_detail = result.stderr.decode(errors="replace")[-2000:]
        except subprocess.TimeoutExpired:
            last_detail = f"git {' '.join(args)} timed out"
        if attempt < config.retry_count:
            time.sleep(attempt * 2)
    raise ScreeningFailure(ScreeningCode.HISTORY_REWRITTEN_OR_CORRUPT, last_detail)


def valid_skill_document(content: bytes, expected_name: str) -> bool:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not text.startswith("---"):
        return False
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    closing = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        return False
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError:
        return False
    if not isinstance(frontmatter, dict):
        return False
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    return (
        isinstance(name, str)
        and name == expected_name
        and 1 <= len(name) <= 64
        and SKILL_NAME_RE.fullmatch(name) is not None
        and isinstance(description, str)
        and 1 <= len(description) <= 1024
    )


def tree_paths(repository: Path, commit: str, config: Config) -> list[str]:
    output = git_output(repository, config, "ls-tree", "-r", "--name-only", commit)
    return [line for line in output.splitlines() if line]


def valid_skills_at(
    repository: Path, commit: str, config: Config
) -> list[str]:
    result: list[str] = []
    for path in tree_paths(repository, commit, config):
        match = SKILL_PATH_RE.fullmatch(path)
        if not match:
            continue
        content = git_bytes_output(repository, config, "show", f"{commit}:{path}")
        if valid_skill_document(content, match.group(1)):
            result.append(path)
    return sorted(result)


def commits_touching_skill_locations(
    repository: Path, cutoff_commit: str, config: Config
) -> list[str]:
    output = git_output(
        repository,
        config,
        "log",
        "--first-parent",
        "--reverse",
        "--format=%H",
        "--diff-merges=first-parent",
        "--root",
        cutoff_commit,
        "--",
        *SKILL_ROOTS,
    )
    # Path-limited git log can emit path lines in some Git configurations;
    # retaining only object IDs makes parsing independent of those settings.
    return [line for line in output.splitlines() if re.fullmatch(r"[0-9a-f]{40,64}", line)]


def find_adoption(
    repository: Path, cutoff_commit: str, config: Config
) -> tuple[str, datetime, list[str]]:
    commits = commits_touching_skill_locations(repository, cutoff_commit, config)
    if not commits:
        raise ScreeningFailure(
            ScreeningCode.NO_VALID_SKILL, "No commits touch accepted skill locations"
        )
    saw_valid_skill = False
    for commit in commits:
        parent_line = git_output(repository, config, "rev-list", "--parents", "-n", "1", commit).strip()
        fields = parent_line.split()
        first_parent = fields[1] if len(fields) > 1 else ""
        current_skills = valid_skills_at(repository, commit, config)
        if current_skills:
            saw_valid_skill = True
        parent_skills = valid_skills_at(repository, first_parent, config) if first_parent else []
        if not parent_skills and current_skills:
            timestamp = git_output(repository, config, "show", "-s", "--format=%cI", commit).strip()
            return commit, parse_datetime(timestamp), current_skills
    code = ScreeningCode.ADOPTION_NOT_FOUND if saw_valid_skill else ScreeningCode.NO_VALID_SKILL
    raise ScreeningFailure(code, "No zero-to-valid-skill transition was found")


def classify_python_paths(paths: Iterable[str]) -> tuple[int, int, int, int]:
    production = 0
    tests = 0
    inside_skills = 0
    python_total = 0
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        if path.suffix != ".py":
            continue
        python_total += 1
        parts = path.parts
        if len(parts) >= 4 and tuple(parts[:2]) in SKILL_ROOT_PARTS:
            inside_skills += 1
            continue
        if any(part in EXCLUDED_PRODUCTION_PARTS for part in parts[:-1]):
            continue
        filename = path.name
        if (
            "test" in parts[:-1]
            or "tests" in parts[:-1]
            or filename.startswith("test_")
            or filename.endswith("_test.py")
            or filename == "conftest.py"
        ):
            tests += 1
        else:
            production += 1
    return production, tests, inside_skills, python_total


def screen_repository(
    candidate: Candidate,
    config: Config,
    temporary_directory: Path,
    logger: logging.Logger,
) -> tuple[ScreeningResult, Path | None]:
    url = f"https://github.com/{candidate.repo_full_name}.git"
    retrieved_at = utc_now()
    base = ScreeningResult(
        candidate_position=candidate.candidate_position,
        repo_full_name=candidate.repo_full_name,
        ordering_hash=candidate.ordering_hash,
        status="excluded",
        exclusion_reason="",
        detail="",
        repository_url=url,
        retrieved_at_utc=retrieved_at,
    )
    code, branch, detail = default_branch(url, config, logger)
    if code:
        base.exclusion_reason = code.value
        base.detail = detail
        return base, None
    base.default_branch = branch

    clone_path = temporary_directory / candidate.ordering_hash[:16]
    try:
        clone_repository(url, branch, clone_path, config, logger)
        cutoff_commit = git_output(
            clone_path,
            config,
            "rev-list",
            "--first-parent",
            "-1",
            f"--before={config.history_cutoff_utc}",
            f"refs/remotes/origin/{branch}",
        ).strip()
        if not cutoff_commit:
            raise ScreeningFailure(
                ScreeningCode.HISTORY_INCOMPLETE,
                "No default-branch commit exists on or before the cutoff",
            )
        base.cutoff_commit = cutoff_commit
        adoption_commit, adoption_at, skills = find_adoption(clone_path, cutoff_commit, config)
        base.adoption_commit = adoption_commit
        base.adoption_at_utc = adoption_at.isoformat().replace("+00:00", "Z")
        base.valid_skill_paths = ";".join(skills)

        window = timedelta(days=config.window_days)
        if adoption_at + window > config.cutoff:
            raise ScreeningFailure(
                ScreeningCode.INSUFFICIENT_POST_WINDOW,
                f"Adoption plus {config.window_days} days exceeds the fixed cutoff",
            )
        pre_boundary = (adoption_at - window).isoformat()
        pre_commit = git_output(
            clone_path,
            config,
            "rev-list",
            "--first-parent",
            "-1",
            f"--before={pre_boundary}",
            cutoff_commit,
        ).strip()
        if not pre_commit:
            raise ScreeningFailure(
                ScreeningCode.INSUFFICIENT_PRE_WINDOW,
                f"No reachable commit exists at least {config.window_days} days before adoption",
            )

        paths = tree_paths(clone_path, adoption_commit, config)
        production, tests, inside_skills, python_total = classify_python_paths(paths)
        base.production_python_count = production
        base.test_python_count = tests
        if inside_skills > 0 and python_total == inside_skills:
            raise ScreeningFailure(
                ScreeningCode.PYTHON_ONLY_INSIDE_SKILL,
                f"Found {inside_skills} Python files only inside skill directories",
            )
        if production == 0:
            raise ScreeningFailure(
                ScreeningCode.NO_PRODUCTION_PYTHON,
                "No production Python file exists in the adoption tree",
            )
        if tests == 0:
            raise ScreeningFailure(
                ScreeningCode.NO_TEST_PYTHON,
                "No Python test file exists in the adoption tree",
            )

        base.status = ScreeningCode.ELIGIBLE.value
        base.exclusion_reason = ""
        base.detail = "All inclusion criteria passed"
        if config.checkout_selected:
            git_output(clone_path, config, "checkout", "--detach", cutoff_commit)
        return base, clone_path
    except ScreeningFailure as error:
        shutil.rmtree(clone_path, ignore_errors=True)
        base.exclusion_reason = error.code.value
        base.detail = error.detail
        return base, None
    except Exception:
        shutil.rmtree(clone_path, ignore_errors=True)
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_csv_atomic(
    path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def checkpoint_path(config: Config, candidate: Candidate) -> Path:
    return config.output_directory / "screening" / f"{candidate.candidate_position:06d}.json"


def load_results(config: Config) -> list[ScreeningResult]:
    results = []
    screening_directory = config.output_directory / "screening"
    if not screening_directory.exists():
        return results
    for path in sorted(screening_directory.glob("*.json")):
        results.append(ScreeningResult(**json.loads(path.read_text(encoding="utf-8"))))
    return results


def regenerate_result_tables(config: Config) -> list[ScreeningResult]:
    results = load_results(config)
    fields = list(ScreeningResult.__dataclass_fields__)
    write_csv_atomic(
        config.output_directory / "screening_log.csv",
        fields,
        (asdict(result) for result in results),
    )
    selected = [result for result in results if result.status == ScreeningCode.ELIGIBLE.value]
    selected_rows = []
    for sample_position, result in enumerate(selected, start=1):
        row = asdict(result)
        row["sample_position"] = sample_position
        selected_rows.append(row)
    write_csv_atomic(
        config.output_directory / "selected_repositories.csv",
        ["sample_position", *fields],
        selected_rows,
    )
    return results


def selected_target(config: Config, repo_full_name: str) -> Path:
    return config.repository_directory / repo_full_name.replace("/", "__")


def verify_existing_selected(
    destination: Path, expected_url: str, cutoff_commit: str, config: Config
) -> None:
    """Recognize a clone moved just before an interrupted checkpoint write."""
    if not (destination / ".git").is_dir():
        raise RuntimeError(f"Existing selected destination is not a Git clone: {destination}")
    actual_url = run_command(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=destination,
        timeout=config.command_timeout_seconds,
    ).stdout.strip()
    if actual_url != expected_url:
        raise RuntimeError(
            f"Existing selected destination has unexpected origin {actual_url!r}: {destination}"
        )
    run_command(
        ["git", "cat-file", "-e", f"{cutoff_commit}^{{commit}}"],
        cwd=destination,
        timeout=config.command_timeout_seconds,
    )


def execute(config: Config, config_path: Path) -> None:
    logger = configure_logging(config.output_directory)
    initialize_manifest(config, config_path)
    config.repository_directory.mkdir(parents=True, exist_ok=True)
    temporary_directory = config.output_directory / ".work" / "clones"
    temporary_directory.mkdir(parents=True, exist_ok=True)
    build_candidate_files(config, logger)
    candidates = load_candidates(config.output_directory / "candidate_order.csv")
    results = regenerate_result_tables(config)
    completed_positions = {result.candidate_position for result in results}
    eligible_count = sum(result.status == ScreeningCode.ELIGIBLE.value for result in results)
    logger.info(
        "Starting screening with %d completed candidates and %d/%d eligible",
        len(completed_positions),
        eligible_count,
        config.target_count,
    )

    for candidate in candidates:
        if eligible_count >= config.target_count:
            break
        if candidate.candidate_position in completed_positions:
            continue
        logger.info(
            "Screening candidate %d: %s",
            candidate.candidate_position,
            candidate.repo_full_name,
        )
        clone_path: Path | None = None
        try:
            result, clone_path = screen_repository(
                candidate, config, temporary_directory, logger
            )
            if result.status == ScreeningCode.ELIGIBLE.value:
                destination = selected_target(config, candidate.repo_full_name)
                if destination.exists():
                    verify_existing_selected(
                        destination, result.repository_url, result.cutoff_commit, config
                    )
                    if clone_path and clone_path.exists():
                        shutil.rmtree(clone_path)
                    logger.info("Reusing selected clone left before an interrupted checkpoint")
                else:
                    assert clone_path is not None
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(clone_path), destination)
                eligible_count += 1
                logger.info("Included %s as eligible repository %d", candidate.repo_full_name, eligible_count)
            else:
                logger.info("Excluded %s: %s", candidate.repo_full_name, result.exclusion_reason)
        except Exception as error:  # Preserve unexpected failures for audit instead of silently replacing.
            if clone_path and clone_path.exists():
                shutil.rmtree(clone_path, ignore_errors=True)
            logger.exception("Unexpected error while screening %s", candidate.repo_full_name)
            raise RuntimeError(
                f"Screening stopped at {candidate.repo_full_name}; rerun after correcting the failure"
            ) from error
        atomic_write_json(checkpoint_path(config, candidate), asdict(result))
        regenerate_result_tables(config)

    final_results = regenerate_result_tables(config)
    final_eligible = sum(
        result.status == ScreeningCode.ELIGIBLE.value for result in final_results
    )
    if final_eligible < config.target_count:
        raise RuntimeError(
            f"Candidate frame exhausted with only {final_eligible} eligible repositories"
        )
    logger.info("Extraction complete: %d eligible repositories", final_eligible)


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select and clone eligible repositories from pinned GitSkills data."
    )
    parser.add_argument(
        "--config", type=Path, default=Path("extraction.toml"), help="TOML configuration file"
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print resolved configuration without network access or extraction",
    )
    return parser


def main() -> None:
    args = argument_parser().parse_args()
    config = load_config(args.config)
    if args.show_config:
        print(json.dumps(serializable_config(config), indent=2, sort_keys=True))
        return
    execute(config, args.config)


if __name__ == "__main__":
    main()
