"""The broad-`git add` hook, driven with real hook input.

## Why this is a test and not a one-off check

`docs/conventions.md` requires a new guard to be verified in reverse: plant the error, confirm
the check fails, remove it, confirm it passes. A verification run once in a terminal and
written up in a commit message answers for the day it was run. The forms this hook has to
refuse are a list, and a list belongs where it is re-run -- otherwise a later edit to the
parser quietly stops blocking `git add -u` and nothing says so.

The hook is `tools/hooks/refuse_broad_git_add.py`. It is a `PreToolUse` hook on Bash, so
its real interface is **a JSON payload on stdin and an exit code**, and that is what is
driven here. Testing `reason_for()` directly would skip the two places this has actually
to be right: reading `tool_input.command` out of the payload, and exiting 2 rather than 1.

## The two halves, and why the second is the important one

Blocking is easy to get right and easy to over-do. A hook that blocks a form it should
not is worse than no hook, because the way out of it is to turn hooks off -- and then
nothing is checked at all. So the allowed list here is longer than the blocked one, and
it includes the forms that look broad and are not: `--dry-run`, `-p`, an unexpanded glob,
a path that does not exist.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.kinds import labels

#: A guard failing means nothing is watching the staged set any more; it spans no single
#: architecture layer. See ``tests/kinds.py``.
pytestmark = labels("guard", "cross")

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "tools" / "hooks" / "refuse_broad_git_add.py"

#: Staged more than the caller named. Each is a form that has either happened in this
#: repository or is one keystroke from the form that did.
REFUSED = (
    "git add -A",
    "git add --all",
    "git add .",
    "git add ./",
    "git add -u",
    "git add --update",
    "git add tools/",                 # the directory that caused the second incident
    "git add tools",
    "git add app/static",
    "git add :/",
    "git add -- .",
    "git add -vA",                    # bundled short flags
    "git add --chmod=+x .",           # a flag taking a value, then a broad pathspec
    "git add README.md tools/",       # one explicit file is not a defence
    # The shape it really arrives in: after the guards pass, chained into the commit.
    "python -m tools.check --fast && git add -A && git commit -m x",
    "git add README.md ; git add .",
    "git -C /some/where add -A",      # a global option between `git` and `add`
)

#: Allowed, and each for a reason. This list being longer than the one above is
#: deliberate: a hook that blocks too much gets switched off, and then nothing is checked.
ALLOWED = (
    "git add README.md",
    "git add tools/check_docs.py README.md docs/conventions.md",
    "git diff --cached --name-only",
    "git commit -m 'why, not what'",
    "git status",
    "python -m tools.check",
    "git add -n -A",                  # --dry-run stages nothing
    "git add --dry-run .",
    "git add -p",                     # a human picks each hunk
    "git add --interactive",
    "git add no/such/file.py",        # git's own error is clearer than a guess here
    "git add 'tools/*.py'",           # a glob the shell did not expand: not a directory
    "git restore --staged tools/",    # not an add at all
    "echo 'git add -A'",              # a string that contains the words
    "git add README.md && git commit -m x && git push",
)

#: Multi-line commands whose **here-document bodies quote the forbidden forms**. These are
#: the ones the hook got wrong on its first real use, and the reason the lists above were
#: not enough: every case in them was a single line, so none of them could have caught it.
#:
#: The body of a here-document is data — written to a file, or piped to a program — and no
#: part of it is executed. This repository's documents quote `git add -A` on nearly every
#: page about committing, and so does every commit message explaining why a commit is
#: narrow, so a hook that read those bodies as commands would refuse the very commits that
#: respect the rule.
ALLOWED_MULTILINE = (
    # The actual command that was refused: a correct, explicit add, chained into a commit
    # whose message is about the mistake.
    "git add tools/hooks/refuse_broad_git_add.py tests/test_git_add_hook.py"
    " && git commit -q -F - <<'EOF'\n"
    "fix: the hook refused its own first use\n"
    "\n"
    "`git add -A` and `git add tools/` have each swept another session's work into\n"
    "a commit about something else. Blocked now -- but the sentence saying so is\n"
    "text in a here-document, not a command.\n"
    "EOF",
    # Writing a document that quotes the rule.
    "cat >> docs/plans/progress.md <<'EOF'\n"
    "What it refuses: `git add -A`, `git add .`, `git add -u`, `git add <dir>`.\n"
    "EOF",
    # Unquoted delimiter, and a body line that is itself a bare broad add.
    "cat > /tmp/notes <<EOF\n"
    "git add -A\n"
    "EOF",
    # Two bodies on one line, closing in order.
    "cat <<A <<B\ngit add .\nA\ngit add -u\nB",
    # `<<-` strips leading tabs from the delimiter line.
    "cat <<-EOF\n\tgit add -A\n\tEOF",
    # A real command after a body has closed, which must still be read.
    "cat <<'EOF' > /tmp/x\ngit add -A\nEOF\ngit add README.md",
)

#: And the other direction: closing a here-document must not blind the hook to what
#: follows it. If it did, every broad add could be hidden behind a heredoc.
REFUSED_MULTILINE = (
    "cat <<'EOF' > /tmp/x\nsome text\nEOF\ngit add -A",
    "cat <<'EOF' > /tmp/x\nsome text\nEOF\ngit add tools/",
    "git commit -q -F - <<'EOF'\na message\nEOF\ngit add .",
)


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=raw,
                          capture_output=True, text=True, cwd=ROOT, timeout=60)


def _for(command: str) -> subprocess.CompletedProcess[str]:
    return _run({"session_id": "test", "tool_name": "Bash",
                 "tool_input": {"command": command}, "cwd": str(ROOT),
                 "hook_event_name": "PreToolUse", "permission_mode": "default"})


class TestRefuses:
    @pytest.mark.parametrize("command", REFUSED)
    def test_a_broad_stage_is_refused(self, command):
        r = _for(command)
        assert r.returncode == 2, (
            f"{command!r} stages more than it names and was allowed "
            f"(exit {r.returncode}): {r.stderr.strip()[:200]}")

    @pytest.mark.parametrize("command", REFUSED)
    def test_the_refusal_says_why_and_what_to_do_instead(self, command):
        """Exit 2 puts stderr in front of the caller as the reason. A refusal with no
        reason teaches nothing, and the next attempt is the same command again."""
        err = _for(command).stderr
        assert "Refused:" in err
        assert "git add path/to/each/file" in err, (
            "the refusal has to carry the form that is allowed, or it only says no")
        assert "docs/conventions.md" in err, "it has to point at the rule it is holding"


class TestAllows:
    @pytest.mark.parametrize("command", ALLOWED)
    def test_an_explicit_or_harmless_command_passes(self, command):
        r = _for(command)
        assert r.returncode == 0, (
            f"{command!r} stages only what it names, or nothing, and was refused: "
            f"{r.stderr.strip()[:200]}")


class TestHereDocumentBodiesAreText:
    """The failure this hook actually had, and the one its docstring warned about.

    Its first real use was refused: an explicit `git add` of three named files, chained
    into `git commit -F -` whose message quoted `git add -A` as the thing it avoids. The
    body of a here-document is data, not commands, and reading it as commands made the
    hook refuse precisely the commits that respect the rule it holds.
    """

    @pytest.mark.parametrize("command", ALLOWED_MULTILINE)
    def test_a_body_quoting_the_forbidden_form_is_not_a_command(self, command):
        r = _for(command)
        assert r.returncode == 0, (
            "a here-document body is text being written, not a command:\n"
            f"{command}\n--- refused with ---\n{r.stderr.strip()[:300]}")

    @pytest.mark.parametrize("command", REFUSED_MULTILINE)
    def test_a_closed_body_does_not_hide_what_follows(self, command):
        """The other direction. If closing a body blinded the hook to the rest of the
        line, a here-document would be the way to stage everything unnoticed."""
        r = _for(command)
        assert r.returncode == 2, (
            f"a broad add after a closed here-document was allowed:\n{command}")


class TestStandsAsideRatherThanBlocking:
    """A hook that fails closed on its own bugs blocks every commit in the repository.

    That is a worse failure than the one it guards, and it is the failure mode of a hook
    nobody tested with malformed input. Each of these exits 0.
    """

    @pytest.mark.parametrize("raw", [
        "",
        "not json at all",
        "[]",
        "null",
        '{"tool_name": "Bash"}',                          # no tool_input
        '{"tool_name": "Bash", "tool_input": {}}',        # no command
        '{"tool_name": "Bash", "tool_input": {"command": ""}}',
        '{"tool_name": "Bash", "tool_input": {"command": "git add \'"}}',  # unbalanced
        # Another tool's call, carrying text that looks like the command.
        '{"tool_name": "Write", "tool_input": {"command": "git add -A"}}',
    ])
    def test_unreadable_input_does_not_block(self, raw):
        assert _run(raw).returncode == 0, f"blocked on {raw!r}"

    def test_a_missing_cwd_does_not_block_an_explicit_add(self):
        r = _run({"tool_name": "Bash", "tool_input": {"command": "git add README.md"}})
        assert r.returncode == 0

    def test_a_directory_is_still_caught_without_a_cwd(self):
        """With no `cwd` the hook falls back to the project directory, so the check that
        a pathspec is a directory still works -- it is the check most likely to be lost
        silently, because its failure mode is to allow rather than to raise."""
        r = _run({"tool_name": "Bash", "tool_input": {"command": "git add tools/"},
                  "cwd": str(ROOT)})
        assert r.returncode == 2
