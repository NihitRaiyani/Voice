"""Step 7 replay harness (docs/10): does a scripted call still do what docs/03, docs/11 and
docs/04 say it must?

Four assertions, straight from the build order — phase hits, word caps, slot extracted,
**filter never leaks**. `scripts/run_eval.py` is the CLI.

Pipecat-free, like `roma.postcall`: the harness drives the controller directly rather than
standing up a media pipeline, so it runs anywhere, needs no websocket, and costs nothing by
default. Live mode is opt-in and metered against `Settings.openai_budget_inr` (₹100).
"""

from roma.eval.checks import (
    ALLOWED_LINES,
    CANARIES,
    Finding,
    ScriptResult,
    TurnResult,
    check_canaries,
    run_checks,
)
from roma.eval.cost import BudgetExceeded, Meter, Usage, inr_for
from roma.eval.runner import FROZEN_NOW, run_script
from roma.eval.script import Script, ScriptError, Turn, load, load_dir, loads

__all__ = [
    "Script",
    "Turn",
    "ScriptError",
    "load",
    "load_dir",
    "loads",
    "run_script",
    "FROZEN_NOW",
    "Finding",
    "TurnResult",
    "ScriptResult",
    "run_checks",
    "check_canaries",
    "CANARIES",
    "ALLOWED_LINES",
    "Meter",
    "Usage",
    "BudgetExceeded",
    "inr_for",
]
