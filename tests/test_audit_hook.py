"""The Stop hook that refuses to finish on an unaudited engine change.

Same shape as the other two hook suites, and for the same reason: the states a guard
must distinguish are a list, and a list belongs where it is re-run.

This one has a hazard the others do not. A `PreToolUse` hook that misfires refuses one
command and the caller tries another. **A Stop hook that misfires refuses to let the
session end**, and if it does so unconditionally there is no way out from inside. So the
cases below weigh heavily towards "must allow", and the loop guard -- `stop_hook_active`
-- has a test of its own.

Each case drives the real interface: JSON on stdin, an exit code out.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.kinds import labels

pytestmark = labels("guard", "cross")

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "tools" / "hooks" / "require_audit_before_stopping.py"


def _run(payload: object, cwd: Path | None = None):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=raw, capture_output=True,
                          text=True, cwd=str(cwd or ROOT), timeout=60)


def _stop(root: Path, *, active: bool = False):
    return _run({"session_id": "test", "hook_event_name": "Stop",
                 "stop_hook_active": active, "cwd": str(root),
                 "permission_mode": "default"}, cwd=root)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repository shaped like this one, with one commit."""
    run = lambda *a: subprocess.run(a, cwd=tmp_path, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    for d in ("backtest/core", "docs/findings", "app", "tools"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "backtest/core/maybe.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app/server.py").write_text("y = 1\n", encoding="utf-8")
    (tmp_path / "docs/findings/README.md").write_text("# findings\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "first")
    return tmp_path


class TestItBlocksAnUnauditedEngineChange:
    def test_an_uncommitted_change_under_backtest_blocks(self, repo):
        (repo / "backtest/core/maybe.py").write_text("x = 2\n", encoding="utf-8")
        r = _stop(repo)
        assert r.returncode == 2, r.stderr
        assert "backtest/core/maybe.py" in r.stderr, "it has to name what it found"

    def test_an_untracked_file_under_backtest_blocks_too(self, repo):
        (repo / "backtest/core/new_thing.py").write_text("z = 1\n", encoding="utf-8")
        assert _stop(repo).returncode == 2

    def test_the_refusal_says_what_to_do_instead(self, repo):
        """A refusal with no way out gets the hook deleted, which is the worst outcome
        available: then nothing checks an engine change at all."""
        (repo / "backtest/core/maybe.py").write_text("x = 3\n", encoding="utf-8")
        err = _stop(repo).stderr
        assert "quant-auditor" in err
        assert "docs/findings/" in err
        assert "commit the change" in err
        assert "say plainly" in err, (
            "there has to be a way out that is not committing or auditing, or a session "
            "with a deliberately unaudited change cannot end")


class TestItAllows:
    def test_a_clean_tree_does_not_block(self, repo):
        assert _stop(repo).returncode == 0

    def test_a_findings_report_newer_than_the_change_satisfies_it(self, repo):
        (repo / "backtest/core/maybe.py").write_text("x = 4\n", encoding="utf-8")
        assert _stop(repo).returncode == 2
        (repo / "docs/findings/audit-now.md").write_text("# audit\n", encoding="utf-8")
        assert _stop(repo).returncode == 0

    def test_the_directory_readme_is_not_an_audit(self, repo):
        """`docs/findings/README.md` describes the directory. If it counted, the hook
        would be satisfied from the day the directory was created."""
        readme = repo / "docs/findings/README.md"
        (repo / "backtest/core/maybe.py").write_text("x = 5\n", encoding="utf-8")
        readme.touch()                      # newer than the change, and still not an audit
        assert _stop(repo).returncode == 2

    @pytest.mark.parametrize("path", ["app/server.py", "tools/check.py", "docs/x.md"])
    def test_a_change_outside_backtest_does_not_block(self, repo, path):
        """Only `backtest/` -- a change elsewhere can make the interface or a document
        wrong, which is a different problem with a different reviewer."""
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed\n", encoding="utf-8")
        assert _stop(repo).returncode == 0

    def test_a_committed_change_does_not_block(self, repo):
        (repo / "backtest/core/maybe.py").write_text("x = 6\n", encoding="utf-8")
        subprocess.run(["git", "add", "backtest/core/maybe.py"], cwd=repo, check=True,
                       capture_output=True)
        subprocess.run(["git", "commit", "-qm", "audited elsewhere"], cwd=repo, check=True,
                       capture_output=True)
        assert _stop(repo).returncode == 0


class TestTheLoopGuard:
    """`stop_hook_active` is true when a Stop hook has already blocked this stop.

    Blocking again would leave a session with no way to end from inside. This is the one
    case where the hook must stand aside even though its condition holds.
    """

    def test_it_never_blocks_twice(self, repo):
        (repo / "backtest/core/maybe.py").write_text("x = 7\n", encoding="utf-8")
        assert _stop(repo, active=False).returncode == 2, "precondition: it would block"
        assert _stop(repo, active=True).returncode == 0, (
            "it blocked while already blocking, which is a session that cannot end")


class TestItStandsAsideRatherThanTrapping:
    """Every case here runs with `backtest/` **deliberately dirty**.

    That is not decoration. These cases used to run from the repository root with
    whatever state the tree happened to be in, and the hook used to fall back to the
    current directory when it could not read a `cwd` -- so on unreadable input it
    judged the repository it was standing in. With a clean tree it stood aside and the
    test passed; with a dirty one it blocked, which is every engine session mid-work.
    The test was green by accident of state, and `engine-dev` found it by having a
    dirty tree.

    Running them against a repository that *would* block if the hook looked is what
    makes "stands aside" mean something.
    """

    @pytest.mark.parametrize("raw", [
        "", "   ", "not json", "[]", "null",
        '{"hook_event_name": "Stop"}',                      # no cwd
        '{"hook_event_name": "Stop", "cwd": ""}',
        '{"hook_event_name": "Stop", "cwd": "/no/such/dir"}',
        '{"hook_event_name": "Stop", "cwd": 7}',
    ])
    def test_unreadable_input_does_not_block(self, raw, repo):
        (repo / "backtest/core/maybe.py").write_text("dirty = 1\n", encoding="utf-8")
        assert _stop(repo).returncode == 2, (
            "precondition: this repository would block on a readable payload, so "
            "standing aside below is the hook's choice and not the tree's state")
        assert _run(raw, cwd=repo).returncode == 0, f"blocked on {raw!r}"

    def test_a_directory_that_is_not_a_git_repository_does_not_block(self, tmp_path):
        (tmp_path / "backtest").mkdir()
        (tmp_path / "backtest" / "x.py").write_text("x = 1\n", encoding="utf-8")
        assert _run({"hook_event_name": "Stop", "stop_hook_active": False,
                     "cwd": str(tmp_path)}, cwd=tmp_path).returncode == 0
