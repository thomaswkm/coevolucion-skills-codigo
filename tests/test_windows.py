"""Exact pre/post window boundaries and adoption exclusion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import GitRepo
from coevolution_skills.process import commits_in_windows


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_window_boundaries_and_adoption_exclusion(git_repo_factory, config_factory) -> None:
    config = config_factory(window_days=5)
    repo: GitRepo = git_repo_factory("windows")

    t0 = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    dates = [
        t0 - timedelta(days=6),
        t0 - timedelta(days=5),
        t0 - timedelta(seconds=1),
        t0,
        t0 + timedelta(seconds=1),
        t0 + timedelta(days=5),
        t0 + timedelta(days=5, seconds=1),
    ]
    commits: list[str] = []
    for index, moment in enumerate(dates):
        repo.write("f.txt", "\n".join(str(value) for value in range(index + 1)) + "\n")
        commits.append(repo.commit(f"commit {index}", iso(moment)))

    adoption = commits[3]
    cutoff = commits[-1]
    found, _ = commits_in_windows(repo.path, cutoff, adoption, iso(t0), config)
    observed = {commit.committed_at_utc: commit.period for commit in found}

    assert iso(t0 - timedelta(days=6)) not in observed
    assert observed[iso(t0 - timedelta(days=5))] == "pre"
    assert observed[iso(t0 - timedelta(seconds=1))] == "pre"
    assert iso(t0) not in observed  # adoption commit is always excluded
    assert observed[iso(t0 + timedelta(seconds=1))] == "post"
    assert observed[iso(t0 + timedelta(days=5))] == "post"
    assert iso(t0 + timedelta(days=5, seconds=1)) not in observed


def test_windows_partition_commits_by_period(git_repo_factory, config_factory) -> None:
    config = config_factory(window_days=5)
    repo: GitRepo = git_repo_factory("windows-partition")

    t0 = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    repo.write("f.txt", "base\n")
    commits = [repo.commit("base", iso(t0 - timedelta(days=3)))]
    repo.write("f.txt", "base\npre\n")
    commits.append(repo.commit("pre", iso(t0 - timedelta(days=1))))
    repo.write(".claude/skills/demo/SKILL.md", "---\nname: demo\ndescription: d\n---\n")
    commits.append(repo.commit("adopt", iso(t0)))
    repo.write("f.txt", "base\npre\npost\n")
    commits.append(repo.commit("post", iso(t0 + timedelta(days=2))))

    found, excluded = commits_in_windows(repo.path, commits[-1], commits[2], iso(t0), config)
    assert [commit.period for commit in found] == ["pre", "pre", "post"]
    assert excluded == 1  # the adoption commit t0
