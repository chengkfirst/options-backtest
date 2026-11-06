"""The per-agent path-restriction hook, driven with real hook input.

`engine-dev` owns `backtest/` (except `backtest/research/`) and the tests for what it
changes; `interface-dev` owns `app/`. Those are the two configurations exercised here,
because they are the two that exist -- a hook verified against invented arguments is
verified against nothing.

Same shape as `test_git_add_hook.py`, and for the same reason: the forms a guard must
refuse are a list, and a list belongs where it is re-run. The allowed half matters more
than the refused half, because a hook that refuses what it should not is a hook whose
agent cannot work, and the way out of that is to delete the hook.
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
HOOK = ROOT / "tools" / "hooks" / "restrict_paths.py"

#: Exactly what `.claude/agents/engine-dev.md` and `interface-dev.md` register.
ENGINE = ["--allow", "backtest", "--allow", "tests", "--block", "backtest/research"]
INTERFACE = ["--allow", "app"]


def _run(args: list[str], payload: object) -> subprocess.CompletedProcess[str]:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK), *args], input=raw,
                          capture_output=True, text=True, cwd=ROOT, timeout=60)


def _edit(args: list[str], path: str, tool: str = "Edit"):
    return _run(args, {"tool_name": tool, "tool_input": {"file_path": path},
                       "cwd": str(ROOT), "hook_event_name": "PreToolUse"})


class TestEngineDev:
    @pytest.mark.parametrize("path", [
        "backtest/engine/loop.py",
        "backtest/core/slots.py",
        "backtest/CLAUDE.md",
        "tests/test_components.py",
        "tests/kinds.py",
        # An absolute path inside an owned directory is the same edit.
        str(ROOT / "backtest" / "api" / "sdk.py"),
    ])
    def test_it_may_edit_what_it_owns(self, path):
        r = _edit(ENGINE, path)
        assert r.returncode == 0, f"{path} refused: {r.stderr.strip()[:200]}"

    @pytest.mark.parametrize("path", [
        "app/static/js/views/bench.js",          # the other builder's area
        "app/runner.py",
        "app/CLAUDE.md",
        "docs/conventions.md",                    # the rulebook is nobody's to edit
        "README.md",
        "tools/check_deps.py",                    # the guards are not a builder's
        ".claude/agents/engine-dev.md",           # not its own definition
        ".github/workflows/check.yml",
        "docs/architecture.md",
    ])
    def test_it_may_not_edit_what_it_does_not_own(self, path):
        r = _edit(ENGINE, path)
        assert r.returncode == 2, f"{path} allowed"
        assert "Refused:" in r.stderr

    @pytest.mark.parametrize("path", [
        "backtest/research/metrics.py",
        "backtest/research/validation/bootstrap.py",
    ])
    def test_the_carve_out_inside_an_owned_directory_holds(self, path):
        """`--block backtest/research` is the harder half: the path is inside a directory
        the agent does own, so a prefix check that stopped at the first `--allow` match
        would let it through."""
        r = _edit(ENGINE, path)
        assert r.returncode == 2, f"{path} allowed despite --block"
        assert "even though it owns the directory around it" in r.stderr


class TestInterfaceDev:
    @pytest.mark.parametrize("path", [
        "app/static/js/views/bench.js",
        "app/static/bench.css",
        "app/static/index.html",
        "app/runner.py",
        "app/server.py",
    ])
    def test_it_may_edit_the_interface(self, path):
        assert _edit(INTERFACE, path).returncode == 0

    @pytest.mark.parametrize("path", [
        "backtest/engine/loop.py",
        "backtest/core/thresholds.py",
        "tests/test_components.py",
        "docs/conventions.md",
    ])
    def test_it_may_not_edit_the_engine_or_its_tests(self, path):
        assert _edit(INTERFACE, path).returncode == 2


class TestComponentsNotStringPrefixes:
    """`app` must not allow `application/`.

    A `str.startswith` check would, and the mistake is invisible until something has been
    written to the wrong tree -- which is the shape of failure this whole project is
    organised against.
    """

    @pytest.mark.parametrize("path", [
        "application/x.py",
        "apps/x.py",
        "backtesting/x.py",
        "backtest_extra/x.py",
        "tests_old/x.py",
    ])
    def test_a_lookalike_directory_is_not_the_owned_one(self, path):
        assert _edit(INTERFACE, path).returncode == 2, f"{path} allowed by --allow app"
        assert _edit(ENGINE, path).returncode == 2, f"{path} allowed by the engine's rules"

    def test_a_lookalike_inside_a_blocked_path_is_not_blocked_by_accident(self):
        """`backtest/research_notes.py` is not inside `backtest/research/`. It is owned."""
        assert _edit(ENGINE, "backtest/research_notes.py").returncode == 0


class TestOutsideTheRepository:
    @pytest.mark.parametrize("path", [
        "/etc/hosts",
        "/tmp/scratch.py",
        "../elsewhere/x.py",
        "~/.claude/settings.json",
    ])
    def test_an_edit_outside_the_project_directory_is_refused(self, path):
        r = _edit(ENGINE, path)
        assert r.returncode == 2, f"{path} allowed"


class TestStandsAsideRatherThanBlocking:
    """A hook that fails closed on its own bug stops its agent working entirely."""

    def test_with_no_allow_arguments_it_checks_nothing(self):
        """An agent that declares no ownership is not one this is meant to constrain, and
        a hook that refused everything in that case would be a trap for the next agent
        definition that registers it without arguments."""
        assert _edit([], "anything/at/all.py").returncode == 0

    @pytest.mark.parametrize("raw", [
        "",
        "not json",
        "[]",
        '{"tool_name": "Edit"}',
        '{"tool_name": "Edit", "tool_input": {}}',
        '{"tool_name": "Edit", "tool_input": {"file_path": ""}}',
    ])
    def test_unreadable_input_does_not_block(self, raw):
        assert _run(ENGINE, raw).returncode == 0

    @pytest.mark.parametrize("tool", ["Bash", "Read", "Grep", "Glob", "WebFetch"])
    def test_a_tool_that_does_not_write_a_file_is_not_checked(self, tool):
        """The matcher in the frontmatter names the writing tools, but a matcher widened
        later must not silently start refusing reads."""
        r = _run(ENGINE, {"tool_name": tool,
                          "tool_input": {"file_path": "conventions.md"},
                          "cwd": str(ROOT)})
        assert r.returncode == 0

    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_every_writing_tool_is_checked(self, tool):
        assert _edit(ENGINE, "conventions.md", tool=tool).returncode == 2


class TestTheAgentsDeclareWhatTheyOwn:
    """Read the arguments out of the agent definitions, rather than restating them here.

    A test that restated the allow-lists would pass while an agent definition said
    something else, which is the "one fact defined in two places" failure applied to the
    thing meant to be guarding against it. These cases read
    ``.claude/agents/<name>.md``'s own frontmatter and drive the hook with it, so editing
    an agent's ownership is what changes the behaviour under test.

    The boundaries asserted are the ones that make the pairing mean anything: neither
    builder can reach the other's side, the analyst cannot touch the platform, and the
    document keeper cannot touch code or a generated transcription.
    """

    AGENTS = Path(__file__).resolve().parent.parent / ".claude" / "agents"

    @staticmethod
    def _argv(agent: str) -> list[str]:
        import re

        import yaml

        text = (TestTheAgentsDeclareWhatTheyOwn.AGENTS / f"{agent}.md").read_text(
            encoding="utf-8")
        front = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        assert front, f"{agent}.md has no frontmatter"
        meta = yaml.safe_load(front.group(1))
        assert meta["name"] == agent, f"{agent}.md declares name {meta['name']!r}"
        entries = meta["hooks"]["PreToolUse"]
        assert len(entries) == 1, f"{agent}.md registers {len(entries)} PreToolUse entries"
        command = entries[0]["hooks"][0]["command"]
        import shlex
        argv = shlex.split(command.replace("$CLAUDE_PROJECT_DIR", str(ROOT)))
        assert Path(argv[0]).name == "restrict_paths.py", argv[0]
        return argv[1:]

    #: (agent, path, must it be allowed). Each line is a boundary that the division of
    #: labour rests on, so each is stated as a case rather than left to the prose.
    BOUNDARIES = [
        ("engine-dev", "backtest/engine/loop.py", True),
        ("engine-dev", "tests/test_components.py", True),
        ("engine-dev", "app/runner.py", False),
        ("engine-dev", "app/static/js/views/bench.js", False),
        ("engine-dev", "backtest/research/metrics.py", False),
        ("engine-dev", "docs/conventions.md", False),
        ("engine-dev", "tools/check.py", False),
        ("interface-dev", "app/static/index.html", True),
        ("interface-dev", "app/runner.py", True),
        ("interface-dev", "backtest/core/slots.py", False),
        ("interface-dev", "tests/test_components.py", False),
        ("interface-dev", "tools/check_wiring.py", False),
        ("research-analyst", "docs/findings/whatever.md", True),
        ("research-analyst", "docs/specs/needs-intraday-quotes.md", True),
        ("research-analyst", "backtest/engine/loop.py", False),
        ("research-analyst", "backtest/research/metrics.py", False),
        ("research-analyst", "tests/test_x.py", False),
        ("research-analyst", "docs/research/plan.md", False),
        ("doc-keeper", "README.md", True),
        ("doc-keeper", "docs/requirements.md", True),
        ("doc-keeper", "backtest/CLAUDE.md", True),
        ("doc-keeper", "backtest/engine/loop.py", False),
        ("doc-keeper", "tools/check_docs.py", False),
        ("doc-keeper", "docs/architecture.md", False),
        # The PDFs the three transcriptions come from. They live under `docs/`,
        # which doc-keeper owns, so without `--block docs/originals` an agent
        # told never to edit a transcription could edit the original it is made
        # from -- and nothing else in this suite would have mentioned them.
        ("doc-keeper", "docs/originals/backtest-system-architecture.pdf", False),
        ("doc-keeper", "docs/originals/frontend-web-plan.pdf", False),
        ("doc-keeper", "docs/originals/ui-ux-design-plan-addendum.pdf", False),
        ("doc-keeper", "docs/conventions.md", True),
        ("doc-keeper", "docs/research-questions.md", True),
        ("doc-keeper", "docs/feedback/2026-11-06-anything.md", False),
    ]

    @pytest.mark.parametrize("agent,path,allowed", BOUNDARIES)
    def test_the_boundary_holds(self, agent, path, allowed):
        code = _edit(self._argv(agent), path).returncode
        assert code == (0 if allowed else 2), (
            f"{agent} {'cannot edit' if allowed else 'can edit'} {path}, "
            f"which its definition says the opposite of")

    @pytest.mark.parametrize("agent", ["reviewer", "quant-auditor"])
    def test_a_read_only_agent_has_no_writing_tool_at_all(self, agent):
        """These two need no path hook because they are given no tool that writes a file.

        Asserted rather than assumed: adding Write to one of them later would make it an
        agent that fixes what it finds, and then nobody reviews the fix.
        """
        import re

        import yaml

        text = (self.AGENTS / f"{agent}.md").read_text(encoding="utf-8")
        meta = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1))
        tools = meta["tools"]
        names = {t.strip() for t in (tools.split(",") if isinstance(tools, str) else tools)}
        assert not (names & WRITING), f"{agent} has {names & WRITING}"


#: Tools that write a file, as the hook defines them.
WRITING = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
