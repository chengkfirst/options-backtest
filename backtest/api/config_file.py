"""A run as a file: write it, read it, prove it reproduces.

## Why a file

A settled strategy is the object the second of this platform's two uses revolves around --
something kept, re-run on new data, and compared against itself a year later. Until now it
had no form. It lived in the arguments of whatever call produced it, and after the run
only as a JSON blob in the store that **nothing could run again**.

A file can be diffed, versioned, handed over, and re-run. The architecture document asks
for YAML validated against a schema at load time, with unknown fields raising. The
validation half was already true -- the config objects are frozen dataclasses that reject
an unknown keyword and check their own ranges -- so what is added here is the half that was
missing: the carrier.

## The property that makes it real

**A config file and the run it describes must share a fingerprint.** `run_id` is the hash
of what determines a result; a file that writes out something less than that would be a
description of a run rather than the run itself, and the difference only shows up a year
later when the replay quietly differs. So :func:`check_round_trip` runs the file and
compares, and a test holds it.

## What cannot go in a file, and why it raises

An entry condition written as an expression is text and travels. An entry written as a
**Python function**, a plan, a review, or a custom contract selector does not: the file
would have to carry code, and loading one would mean executing whatever a file on disk
said. Those raise on export, naming what blocked it, rather than writing a file that
silently drops the part that did the deciding.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping

import yaml

from backtest.core.errors import StartupError

#: The top-level keys a config file may hold. Anything else raises, as the document asks:
#: a misspelled key that was ignored would run something other than what the file says,
#: and the file is the whole point.
KEYS: tuple[str, ...] = (
    "symbol", "start", "end", "entry", "select", "exit", "components", "sizing",
    "quantity", "engine", "max_concurrent", "capital", "execution_lag_days",
    "shadow_days", "name", "chain",
)

#: Keys that carry a Python object rather than data, and so cannot be written to a file.
#: The value is the reason, shown to whoever tried.
NOT_WRITABLE: Mapping[str, str] = {
    "plan": "a plan is a Python function that decides what to buy, how much and how to "
            "get out. Writing it to a file would mean the file carried code, and loading "
            "one would mean running whatever a file on disk said",
    "review": "a daily review is a Python function, as above",
}


def _plain(value: Any) -> Any:
    """Dates as ISO strings, tuples as lists, everything else untouched.

    Kept narrow on purpose. A general-purpose converter would quietly turn an object it
    did not understand into its ``repr``, and the file would then look complete and
    reproduce nothing.
    """
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise StartupError(
        f"a run config cannot hold {type(value).__name__} ({value!r}). Only data goes in "
        f"a file; a Python object would be written as its text and read back as nothing"
    )


def to_mapping(result) -> dict[str, Any]:
    """The config that reproduces this run, as plain data.

    Built from the manifest rather than from whatever the caller passed, so a run loaded
    from a file and a run written in Python export identically.
    """
    manifest = result.manifest()
    strategy, config = manifest["strategy"], manifest["config"]
    execution = manifest["execution"]

    blocked = [k for k in NOT_WRITABLE if strategy.get(k)]
    if blocked:
        raise StartupError(
            "this run cannot be written to a config file: "
            + "; ".join(f"{k} -- {NOT_WRITABLE[k]}" for k in blocked)
        )
    if not strategy.get("entry_is_expression"):
        raise StartupError(
            f"the entry condition is {strategy['entry']!r}, which is a Python function "
            f"rather than an expression. A file would carry code, and loading one would "
            f"mean running it. Write the entry as an expression to put this run in a file"
        )
    if strategy["select"] == "<callable>":
        raise StartupError(
            "contract selection is a custom function rather than a rule, so it cannot go "
            "in a file for the same reason as a plan"
        )

    select = dict(strategy["select"])
    out: dict[str, Any] = {
        "symbol": config["symbol"],
        "start": config["start"],
        "end": config["end"],
        "entry": strategy["entry"],
        "select": {
            # The word, not the enum's letter. A file is read by a person before it is
            # read by the parser, and "C" tells them nothing.
            "right": "call" if str(select["right"]).upper().startswith("C") else "put",
            "dte_window": list(select["dte_window"]),
            **({"delta": select["target_delta"]} if select.get("target_delta") is not None
               else {}),
            **({"moneyness": select["moneyness"]} if select.get("moneyness") is not None
               else {}),
            **({"delta_band": list(select["delta_band"])}
               if select.get("delta_band") else {}),
            **({"iv_band": list(select["iv_band"])} if select.get("iv_band") else {}),
            **{k: select[k] for k in ("min_volume", "min_open_interest",
                                      "max_relative_spread") if select.get(k) is not None},
        },
        "exit": {k: v for k, v in strategy["exit"].items() if v is not None},
        "sizing": {k: v for k, v in strategy["sizing"].items() if v is not None},
        "quantity": strategy["quantity"],
        "engine": execution["engine"],
        "capital": config["notional_capital"],
        "execution_lag_days": config["execution_lag_days"],
        "name": strategy["name"],
    }
    if strategy.get("components"):
        # The version is written, and loading checks it. Without it a file would replay
        # against whatever version of the form happens to be registered later -- the file
        # would look unchanged and the run would not be the same run, which is the exact
        # drift this file exists to prevent.
        out["components"] = {
            slot: {"variant": c["ref"].split("/")[1].split("@")[0],
                   "version": int(c["ref"].split("@")[1]),
                   "params": dict(c["params"])}
            for slot, c in strategy["components"].items()
        }
    if execution.get("max_concurrent") is not None:
        out["max_concurrent"] = execution["max_concurrent"]
    if execution.get("shadow_days"):
        out["shadow_days"] = execution["shadow_days"]
    return _plain(out)


#: What goes at the top of a written file. The fingerprint is a comment rather than a
#: field: it is **derived** from everything below it, and a field would invite editing the
#: hash instead of re-running to get a new one.
HEADER = """\
# A backtest run, as data.
#
# Re-run it with:  from backtest import run_from; run_from("{path}")
#
# This file is the complete input: the same file, the same code and the same data give
# the same result. Its fingerprint below is the hash of exactly that, and is derived --
# editing it changes nothing. If a replay produces a different one, the two are not the
# same run and the difference is in this file.
#
# fingerprint: {fingerprint}
# written:     {written}
"""


def save(result, path: str | Path) -> Path:
    """Write the config that reproduces this run."""
    from datetime import datetime

    target = Path(path)
    # default_flow_style=None keeps short lists inline ([21, 45]) and blocks out the
    # nested mappings, which is how a person would write this file by hand.
    body = yaml.safe_dump(to_mapping(result), sort_keys=False, allow_unicode=False,
                          default_flow_style=None)
    head = HEADER.format(path=target.name,
                         fingerprint=result.manifest()["run_id"],
                         written=datetime.now().strftime("%Y-%m-%d %H:%M"))
    target.write_text(head + "\n" + body, encoding="utf-8")
    return target


def load(path: str | Path) -> dict[str, Any]:
    """Read a config file into keyword arguments for :func:`backtest.run`.

    Unknown keys raise, as the document asks. The alternative is a misspelled key that is
    ignored, which runs something other than what the file says while the file looks right.
    """
    target = Path(path)
    if not target.exists():
        raise StartupError(f"there is no config file at {target}")
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StartupError(f"{target} is not valid YAML: {exc}") from None
    if not isinstance(data, Mapping):
        raise StartupError(
            f"{target} should hold a mapping of settings, got {type(data).__name__}"
        )
    unknown = sorted(set(data) - set(KEYS))
    if unknown:
        raise StartupError(
            f"{target} has keys this does not understand: {', '.join(unknown)}. "
            f"Known: {', '.join(KEYS)}"
        )
    return dict(data)


def fingerprint_of(path: str | Path) -> str | None:
    """The fingerprint recorded in a written file, or ``None`` if it carries none."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("# fingerprint:"):
            return line.split(":", 1)[1].strip()
        if not line.startswith("#"):
            break
    return None


def check_round_trip(result, path: str | Path) -> str:
    """Write this run, run the file, and confirm the two are the same run.

    **This is what makes the file a carrier rather than a description.** A config that
    writes out less than the fingerprint covers still looks complete, and the gap only
    appears a year later when the replay quietly differs. Returns the shared fingerprint,
    or raises saying which one drifted.
    """
    from backtest.api.sdk import run

    target = save(result, path)
    before = result.manifest()["run_id"]
    after = run(**load(target)).manifest()["run_id"]
    if before != after:
        raise StartupError(
            f"the config file does not reproduce the run: the run fingerprints as "
            f"{before} and the file replays as {after}. Something that changes the result "
            f"is not being written -- compare manifest()['strategy'] and ['execution'] "
            f"against the keys this module writes"
        )
    return before
