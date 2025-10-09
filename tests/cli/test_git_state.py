import subprocess
from pathlib import Path

import pytest

from nexus.cli.utils import save_working_state, restore_working_state


def run(cmd: str, cwd: Path) -> str:
    return subprocess.run(cmd.split(), cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def status(cwd: Path) -> str:
    return run("git status --porcelain", cwd)


def stash_count(cwd: Path) -> int:
    result = run("git stash list", cwd)
    return len(result.splitlines()) if result else 0


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    run("git init", repo)
    run("git config user.email test@test.com", repo)
    run("git config user.name Test", repo)
    (repo / "initial.txt").write_text("initial")
    run("git add .", repo)
    run("git commit -m initial", repo)

    yield repo


def test_clean_tree_unchanged(git_repo: Path):
    before = status(git_repo)
    assert before == ""


def test_untracked_files_restored(git_repo: Path):
    (git_repo / "new.txt").write_text("new")
    before_status, before_count = status(git_repo), stash_count(git_repo)

    orig, temp, sha, created = save_working_state()
    restore_working_state(orig, temp, created)

    assert status(git_repo) == before_status
    assert stash_count(git_repo) == before_count


def test_modified_files_restored(git_repo: Path):
    (git_repo / "initial.txt").write_text("modified")
    before_status, before_count = status(git_repo), stash_count(git_repo)

    orig, temp, sha, created = save_working_state()
    restore_working_state(orig, temp, created)

    assert status(git_repo) == before_status
    assert stash_count(git_repo) == before_count


def test_staged_changes_restored(git_repo: Path):
    (git_repo / "staged.txt").write_text("staged")
    run("git add .", git_repo)
    before_status, before_count = status(git_repo), stash_count(git_repo)

    orig, temp, sha, created = save_working_state()
    restore_working_state(orig, temp, created)

    assert status(git_repo) == before_status
    assert stash_count(git_repo) == before_count


def test_mixed_changes_restored(git_repo: Path):
    (git_repo / "initial.txt").write_text("changed")
    (git_repo / "new.txt").write_text("new")
    run("git add initial.txt", git_repo)
    before_status, before_count = status(git_repo), stash_count(git_repo)

    orig, temp, sha, created = save_working_state()
    restore_working_state(orig, temp, created)

    after_status = status(git_repo)
    assert "initial.txt" in after_status and "new.txt" in after_status, (
        f"Before: {before_status}\nAfter: {after_status}"
    )
    assert stash_count(git_repo) == before_count


def test_deleted_files_restored(git_repo: Path):
    (git_repo / "initial.txt").unlink()
    before_status, before_count = status(git_repo), stash_count(git_repo)

    orig, temp, sha, created = save_working_state()
    restore_working_state(orig, temp, created)

    assert status(git_repo) == before_status
    assert stash_count(git_repo) == before_count


def test_existing_stash_preserved(git_repo: Path):
    (git_repo / "stashed.txt").write_text("stashed")
    run("git stash -u", git_repo)
    before_count = stash_count(git_repo)

    (git_repo / "new.txt").write_text("new")
    orig, temp, sha, created = save_working_state()
    restore_working_state(orig, temp, created)

    assert stash_count(git_repo) == before_count
