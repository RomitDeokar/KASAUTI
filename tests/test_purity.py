"""
Purity tests for BOTH rule layers.

`assert_no_llm` is the load-bearing claim of this project: it is what makes
"LLM proposes, code decides" a property of the build rather than a sentence in
a README. A guard that only covers the rules I wrote first is not a guard, so
this file holds the cross-episode layer to the identical standard.

See FAILURES.md #1 (the guard was originally unsound) and #5 (adding a pure
stdlib import correctly made it fire on my own code).
"""
from __future__ import annotations

import pytest

from kasauti.crossepisode import ALL_CROSS_CHECKERS, CROSS_RULE_IDS
from kasauti.engine import assert_no_llm
from kasauti.rules.checkers import ALL_CHECKERS, RULE_IDS


def test_per_episode_checkers_are_pure():
    assert_no_llm(ALL_CHECKERS)


def test_cross_episode_checkers_are_pure():
    """The aggregate layer must be as reproducible as the per-episode layer.

    Cross-episode rules are the ones most tempted to reach for a clock ("is
    this contact recent?"). They must not: all time comes from timestamps
    inside the episodes, never from `datetime.now()`. If it did, the same
    history would produce different verdicts on different days.
    """
    assert_no_llm(ALL_CROSS_CHECKERS)


def test_guard_still_catches_an_impure_cross_checker():
    """The guard must be able to FAIL, or it proves nothing.

    Without this, `test_cross_episode_checkers_are_pure` could pass simply
    because the guard silently skips functions it doesn't understand.
    """
    import textwrap
    import types

    mod = types.ModuleType("fake_impure_cross")
    src = textwrap.dedent("""
        import urllib.request
        def check_bad(episodes, policy):
            return []
    """)
    exec(compile(src, "fake_impure_cross.py", "exec"), mod.__dict__)
    mod.__file__ = "fake_impure_cross.py"

    import sys
    sys.modules["fake_impure_cross"] = mod
    try:
        import inspect
        # inspect.getsource needs the source discoverable; patch linecache.
        import linecache
        linecache.cache["fake_impure_cross.py"] = (
            len(src), None, src.splitlines(True), "fake_impure_cross.py"
        )
        with pytest.raises(AssertionError, match="urllib"):
            assert_no_llm([mod.check_bad])
    finally:
        sys.modules.pop("fake_impure_cross", None)


def test_no_rule_id_collisions_between_layers():
    """Per-episode and cross-episode rule ids must not overlap.

    A shared id would make metric attribution ambiguous -- a merchant reading
    a scorecard could not tell which layer objected, and the two layers have
    very different evidential weight (statute vs. operator policy).
    """
    overlap = set(RULE_IDS) & set(CROSS_RULE_IDS)
    assert overlap == set(), f"rule id collision across layers: {overlap}"


def test_verdicts_are_stable_across_runs():
    """Same input, same verdict hash -- twice, in one process."""
    from corpus.builder import build_corpus
    from kasauti.engine import judge, verdict_hash

    corpus = build_corpus()
    first = [verdict_hash(judge(t)) for t in corpus]
    second = [verdict_hash(judge(t)) for t in corpus]
    assert first == second


# ---------------------------------------------------------------------------
# The gateway must be as pure as the rules it enforces
# ---------------------------------------------------------------------------
def test_gateway_decision_path_is_pure():
    """The inline gate is held to the checkers' standard, not a weaker one.

    This matters more for the gate than for the offline engine. An
    enforcement point that consulted a clock or an LLM would make *production
    money decisions* irreproducible: the same action would be allowed at
    11:00 and denied at 11:01, and no audit could ever reconstruct why.

    `kasauti/gateway.py` imports only `dataclasses`, `enum` and its own
    package, so the same import allowlist that governs the checkers governs
    it. Adding a network call to that module makes this test fail.
    """
    from kasauti.gateway import evaluate_action, run_gate

    assert_no_llm((evaluate_action, run_gate))
