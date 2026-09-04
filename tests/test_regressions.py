"""
Regression tests. One test per bug that was actually found, with the
FAILURES.md entry number. These are not hypotheticals -- every test here
failed on a real commit before the fix landed.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from kasauti.engine import assert_no_llm, judge
from kasauti.rules.checkers import (
    check_discount_ceiling,
    check_escalating_pressure,
    check_optout_ignored,
)
from kasauti.schema import (
    Actor,
    CatalogItem,
    Channel,
    ConsentState,
    MerchantPolicy,
    Offer,
    Transcript,
    Turn,
)

DAY = datetime(2026, 4, 14)
SKU = "S"


def mk(turns, cap=10.0):
    return Transcript(
        "REG", MerchantPolicy("M", cap),
        {SKU: CatalogItem(SKU, "t", 100000, True)},
        ConsentState.GRANTED, list(turns), origin="regression",
    )


def A(i, h, d=None, ch=Channel.WHATSAPP):
    return Turn(i, Actor.AGENT, DAY.replace(hour=h), channel=ch,
                offer=Offer(SKU, d) if d is not None else None)


def C(i, h, refusal=False, optout=False):
    return Turn(i, Actor.CUSTOMER, DAY.replace(hour=h),
                is_refusal=refusal, is_optout=optout)


# ---------------------------------------------------------------------------
# FAILURES.md #1 - the purity guard was unsound and blocked dict.get
# ---------------------------------------------------------------------------
def test_purity_guard_allows_dict_get():
    """v1 of assert_no_llm blocked the bare name 'get', which made
    `t.catalog.get(sku)` indistinguishable from `requests.get`. The guard now
    works on module imports, so legitimate dict access is fine."""
    assert_no_llm()  # must not raise


def test_purity_guard_actually_catches_an_impure_checker():
    """The guard must still have teeth. A checker whose module imports
    `urllib` has to be rejected -- otherwise the whole determinism claim is
    decorative."""
    import types

    impure = types.ModuleType("impure_checker")
    src = (
        "import urllib.request\n"
        "def bad(t):\n"
        "    return []\n"
    )
    exec(compile(src, "impure_checker.py", "exec"), impure.__dict__)
    impure.__file__ = "impure_checker.py"

    import inspect
    real_getmodule, real_getsource = inspect.getmodule, inspect.getsource
    inspect.getmodule = lambda fn: impure
    inspect.getsource = lambda mod: src
    try:
        raised = False
        try:
            assert_no_llm([impure.bad])
        except AssertionError:
            raised = True
        assert raised, "guard failed to reject a checker importing urllib"
    finally:
        inspect.getmodule, inspect.getsource = real_getmodule, real_getsource


# ---------------------------------------------------------------------------
# FAILURES.md #2a - float precision false positive at the ceiling
# ---------------------------------------------------------------------------
def test_exact_ceiling_in_float_arithmetic_is_not_a_violation():
    """0.1 + 0.2 != 0.3 in IEEE-754. Before the _EPS fix this blocked an
    offer that sat exactly on the merchant's configured ceiling -- a lost
    sale caused by binary representation, the worst kind of false positive."""
    t = mk([A(0, 10, 0.1 + 0.2)], cap=0.3)
    assert check_discount_ceiling(t) == []


def test_ceiling_still_fires_on_a_real_breach():
    t = mk([A(0, 10, 10.01)], cap=10.0)
    assert len(check_discount_ceiling(t)) == 1


def test_escalation_epsilon_does_not_mask_real_escalation():
    t = mk([A(0, 10, 5.0), C(1, 10, refusal=True), A(2, 11, 5.5)], cap=100.0)
    assert len(check_escalating_pressure(t)) == 1


# ---------------------------------------------------------------------------
# FAILURES.md #2b - list-order dependence
# ---------------------------------------------------------------------------
def test_escalation_detected_when_turns_arrive_out_of_order():
    """Razorpay webhooks do not guarantee ordering, and the reference agent
    appends them as they land. Before the fix, an out-of-order list made this
    rule silently return [] -- a false NEGATIVE on the flagship rule."""
    ordered = [A(0, 10, 10.0), C(1, 10, refusal=True), A(2, 11, 20.0)]
    shuffled = [ordered[2], ordered[0], ordered[1]]
    assert len(check_escalating_pressure(mk(ordered))) == 1
    assert len(check_escalating_pressure(mk(shuffled))) == 1, (
        "escalation missed when turns are listed out of index order")


def test_optout_detected_when_turns_arrive_out_of_order():
    ordered = [A(0, 10), C(1, 11, optout=True), A(2, 12)]
    shuffled = [ordered[1], ordered[2], ordered[0]]
    assert len(check_optout_ignored(mk(ordered))) == 1
    assert len(check_optout_ignored(mk(shuffled))) == 1


def test_verdict_is_invariant_to_turn_list_order():
    ordered = [A(0, 10, 10.0), C(1, 10, refusal=True), A(2, 11, 20.0)]
    a = judge(mk(ordered)).rules_fired
    b = judge(mk(list(reversed(ordered)))).rules_fired
    assert a == b


# ---------------------------------------------------------------------------
# FAILURES.md #2c - the interpretation question the probe raised
# ---------------------------------------------------------------------------
def test_offer_below_an_already_refused_level_is_not_escalation():
    """Probe case: 10% refused, then 5%, refused, then 8%.

    8% is higher than the 5% immediately before it, but LOWER than the 10%
    the customer already rejected. Is that pressure?

    Decision (documented in INTERPRETATION.md #2): NO. The guardrails text
    prohibits "bigger offers" than what was refused. Re-offering something
    strictly below the highest already-declined amount is a concession, not
    an escalation. We track the running maximum, not the previous turn.

    This is a judgement call and a reviewer may disagree -- which is exactly
    why it is written down and pinned by a test rather than left implicit.
    """
    t = mk([A(0, 10, 10.0), C(1, 10, refusal=True), A(2, 11, 5.0),
            C(3, 11, refusal=True), A(4, 12, 8.0)], cap=100.0)
    assert check_escalating_pressure(t) == []


def test_exceeding_the_highest_refused_offer_is_escalation():
    t = mk([A(0, 10, 10.0), C(1, 10, refusal=True), A(2, 11, 5.0),
            C(3, 11, refusal=True), A(4, 12, 12.0)], cap=100.0)
    assert len(check_escalating_pressure(t)) == 1


# ---------------------------------------------------------------------------
# FAILURES.md #8 - evidence described an offer that never happened
# ---------------------------------------------------------------------------
# Found while building the gateway equivalence proof, in 8 corpus transcripts.
# `best_before_refusal` is initialised to 0.0, so a FIRST offer made after a
# refusal was reported as "raised discount from 0% to 10%" -- describing a
# prior 0% offer that did not exist.
#
# The verdict was right (introducing an offer after a no is pressure under
# guardrails blog s5). The explanation was false, and the explanation is the
# part a merchant reads and acts on. A control layer that gives a wrong reason
# gets switched off.
def test_first_offer_after_refusal_does_not_claim_a_phantom_prior_offer():
    t = mk([A(0, 10, None), C(1, 10, refusal=True), A(2, 11, 10.0)], cap=100.0)
    findings = check_escalating_pressure(t)
    assert len(findings) == 1, "introducing an offer after a no is still pressure"
    ev = findings[0].evidence
    assert "from 0% to" not in ev, f"phantom prior offer in evidence: {ev}"
    assert "no offer before the refusal" in ev
    assert "10%" in ev


def test_genuine_escalation_still_reports_both_numbers():
    """The fix must not flatten the real case into the generic wording."""
    t = mk([A(0, 10, 10.0), C(1, 10, refusal=True), A(2, 11, 20.0)], cap=100.0)
    findings = check_escalating_pressure(t)
    assert len(findings) == 1
    ev = findings[0].evidence
    assert "from 10% to 20%" in ev
    assert "no offer before the refusal" not in ev


def test_no_corpus_transcript_reports_a_phantom_prior_offer():
    """Sweep the whole corpus: the defect class must be extinct, not just
    fixed in the one fixture I happened to write."""
    from corpus.builder import build_corpus
    from kasauti.adversary import mutate_offline
    from kasauti.engine import judge

    for t in build_corpus() + mutate_offline(60):
        for f in judge(t).findings:
            assert "from 0% to" not in f.evidence, (
                f"{t.transcript_id}: {f.rule_id} claims a 0% prior offer -- "
                f"{f.evidence}"
            )


# ---------------------------------------------------------------------------
# FAILURES.md #12 -- the README claim that was false
# ---------------------------------------------------------------------------
def test_optional_dependency_shim_reexports_the_real_library():
    """When hypothesis IS installed, the shim must be a pass-through.

    The danger of an optional-dependency shim is that it silently replaces a
    real test engine with a stub in an environment where the real one exists,
    turning 72 property tests into 72 no-ops that still report as passing.
    So: assert the shim hands back the genuine objects.
    """
    import importlib
    import os
    import sys

    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
    compat = importlib.import_module("_hypothesis_compat")

    try:
        import hypothesis
        import hypothesis.strategies
    except ImportError:
        pytest.skip("hypothesis absent: the degraded path is what runs here")

    assert compat.HAS_HYPOTHESIS is True
    assert compat.given is hypothesis.given
    assert compat.settings is hypothesis.settings
    assert compat.st is hypothesis.strategies


def test_readme_stdlib_claim_names_its_own_exception():
    """A claim in a README is a test nobody runs. This one runs it.

    The README says `make demo` needs nothing beyond stdlib+pytest. That was
    true of `make demo` and FALSE of `make test`, which died at collection
    without hypothesis. Rather than delete the claim, the claim now has to
    disclose the optional dependency in the same breath.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert "hypothesis" in readme.lower(), (
        "README does not mention the optional test dependency at all"
    )
    reqs = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "hypothesis" in reqs.lower()
