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

    # Probe for the library the SAME way the shim does. `import hypothesis`
    # alone is not enough: after `pip uninstall`, leftover `__pycache__`
    # directories make `hypothesis` importable as an empty namespace package
    # with no `given` in it. This test then believed the library was present,
    # the shim (correctly) said it was not, and the disagreement failed the
    # bare-stdlib run the README promises works (FAILURES.md #16).
    try:
        from hypothesis import given as real_given  # noqa: F401
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


# ---------------------------------------------------------------------------
# FAILURES.md #13 - a checker CRASHED instead of judging, and the artifact
# the README calls reproducible changed on every run
# ---------------------------------------------------------------------------
def test_false_urgency_survives_mixed_tz_awareness():
    """`Turn.at` is customer-local (naive); a real webhook stamps
    `claimed_expires_at` in UTC (aware). v1 raised TypeError -- the engine
    died, emitted nothing, and a broad `except` upstream read that as CLEAN.
    """
    from datetime import timezone

    from kasauti.rules.checkers import check_false_urgency

    truth = DAY.replace(hour=12)
    claimed_aware = DAY.replace(hour=11, tzinfo=timezone.utc)
    t = Transcript(
        "REG", MerchantPolicy("M", 10.0),
        {SKU: CatalogItem(SKU, "t", 100000, True, offer_expires_at=truth)},
        ConsentState.GRANTED,
        [Turn(0, Actor.AGENT, DAY.replace(hour=10), channel=Channel.WHATSAPP,
              offer=Offer(SKU, 5.0, claimed_expires_at=claimed_aware))],
    )
    found = check_false_urgency(t)  # must not raise
    assert [f.rule_id for f in found] == ["FALSE_URGENCY"]


def test_metrics_artifact_is_byte_stable():
    """`make demo` twice must produce identical metrics.json. A wall-clock
    field in the artifact made every rerun a diff, which is where a real
    regression would have hidden."""
    import json

    from corpus.builder import build_corpus
    from kasauti.engine import score_corpus

    corpus = build_corpus()
    a = json.dumps(score_corpus(corpus), sort_keys=True)
    b = json.dumps(score_corpus(corpus), sort_keys=True)
    assert a == b
    assert "generated_at" not in a
    assert "corpus_digest" in a


@pytest.mark.parametrize("bad", [-0.01, 100.01, 250.0])
def test_offer_rejects_out_of_range_discount(bad):
    """A negative discount is a surcharge; >100% pays the customer. Neither is
    a thing a checker should form an opinion about -- the harness is broken.
    Before this fix a -5% 'offer' was judged CLEAN."""
    with pytest.raises(ValueError):
        Offer(SKU, bad)


def test_transcript_rejects_duplicate_turn_idx():
    """Two turns with one idx have no defined order, which silently defeats
    every ordering fix in FAILURES.md #2 and makes gateway decisions
    ambiguous. Fail where the bug is."""
    with pytest.raises(ValueError, match="duplicate turn idx"):
        mk([A(0, 10), A(0, 11)])


@pytest.mark.parametrize("kw", [
    {"max_discount_pct": -1.0},
    {"max_discount_pct": 101.0},
    {"contact_window_start_hour": 25},
    {"contact_window_end_hour": -1},
])
def test_merchant_policy_rejects_uninterpretable_config(kw):
    base = {"merchant_id": "M", "max_discount_pct": 10.0}
    base.update(kw)
    with pytest.raises(ValueError):
        MerchantPolicy(**base)


# ---------------------------------------------------------------------------
# FAILURES.md #14 - two merchant configurations the engine could not honour:
# a window that wraps midnight, and a channel list nothing enforced
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hour,inside", [
    (20, True), (23, True), (0, True), (5, True),   # inside 20 -> 06
    (6, False), (12, False), (19, False),           # outside
])
def test_contact_window_wraps_midnight(hour, inside):
    """lo=20, hi=6 used to be `20 <= h < 6`, which is False for every hour:
    the merchant could contact nobody, ever, and every contact was BLOCKED."""
    from kasauti.rules.checkers import check_contact_window

    t = Transcript(
        "REG", MerchantPolicy("M", 10.0, contact_window_start_hour=20,
                              contact_window_end_hour=6),
        {SKU: CatalogItem(SKU, "t", 100000, True)}, ConsentState.GRANTED,
        [A(0, hour)],
    )
    assert bool(check_contact_window(t)) is (not inside)


def test_contact_window_equal_bounds_means_no_hours():
    from kasauti.rules.checkers import check_contact_window

    t = Transcript(
        "REG", MerchantPolicy("M", 10.0, contact_window_start_hour=9,
                              contact_window_end_hour=9),
        {SKU: CatalogItem(SKU, "t", 100000, True)}, ConsentState.GRANTED,
        [A(0, 9)],
    )
    assert check_contact_window(t), "lo == hi must permit nothing"


def test_allowed_channels_is_finally_enforced():
    """`MerchantPolicy.allowed_channels` sat in the schema from commit 1 and
    no rule read it. WhatsApp-only merchant, 11:00 voice call, consent on
    record: CLEAN. Now: CHANNEL_NOT_PERMITTED."""
    t = Transcript(
        "REG", MerchantPolicy("M", 10.0, allowed_channels=(Channel.WHATSAPP,)),
        {SKU: CatalogItem(SKU, "t", 100000, True)}, ConsentState.GRANTED,
        [A(0, 11, ch=Channel.VOICE)],
    )
    assert judge(t).rules_fired == ["CHANNEL_NOT_PERMITTED"]


def test_allowed_channels_silent_on_permitted_channel_and_on_no_contact():
    pol = MerchantPolicy("M", 10.0, allowed_channels=(Channel.WHATSAPP,))
    cat = {SKU: CatalogItem(SKU, "t", 100000, True)}
    ok = Transcript("REG", pol, cat, ConsentState.GRANTED, [A(0, 11)])
    assert "CHANNEL_NOT_PERMITTED" not in judge(ok).rules_fired
    silent = Transcript("REG", pol, cat, ConsentState.GRANTED,
                        [A(0, 11, ch=None)])  # no outbound contact at all
    assert "CHANNEL_NOT_PERMITTED" not in judge(silent).rules_fired


# ---------------------------------------------------------------------------
# FAILURES.md #15 - two rules that were blind to the ordinary case
# ---------------------------------------------------------------------------
def test_fabricated_fact_catches_misquote_without_an_offer():
    """The arithmetic branch was gated on `turn.offer is not None`, so the
    most basic misquote -- wrong list price, no discount -- was never judged."""
    t = Transcript(
        "REG", MerchantPolicy("M", 10.0),
        {SKU: CatalogItem(SKU, "t", 499900, True)}, ConsentState.GRANTED,
        [Turn(0, Actor.AGENT, DAY.replace(hour=11), channel=Channel.WHATSAPP,
              price_claims_paise=(299900,))],
    )
    assert judge(t).rules_fired == ["FABRICATED_FACT"]


def test_fabricated_fact_silent_on_correct_list_price_without_offer():
    t = Transcript(
        "REG", MerchantPolicy("M", 10.0),
        {SKU: CatalogItem(SKU, "t", 499900, True)}, ConsentState.GRANTED,
        [Turn(0, Actor.AGENT, DAY.replace(hour=11), channel=Channel.WHATSAPP,
              price_claims_paise=(499900,))],
    )
    assert judge(t).rules_fired == []


def test_fabricated_fact_does_not_guess_among_several_skus():
    """No offer names a SKU and the catalog has two: the rule must not pick
    one and accuse. Silence over a guess -- docs/INTERPRETATION.md #7."""
    cat = {SKU: CatalogItem(SKU, "t", 499900, True),
           "S2": CatalogItem("S2", "u", 99900, True)}
    t = Transcript(
        "REG", MerchantPolicy("M", 10.0), cat, ConsentState.GRANTED,
        [Turn(0, Actor.AGENT, DAY.replace(hour=11), channel=Channel.WHATSAPP,
              price_claims_paise=(123400,))],
    )
    assert judge(t).rules_fired == []


def test_ceiling_laundering_uses_the_cap_in_force_per_episode():
    """Merchant raised its ceiling 10% -> 20% between episodes. 8% (legal
    under 10) + 15% (legal under 20) = 23%. v1 tested both against the LAST
    cap seen and reported laundering, with an evidence string calling the
    15% 'individually compliant' under a 10% cap that no longer applied."""
    from kasauti.crossepisode import Episode, judge_history

    def ep(tid, cap, disc, day):
        return Episode("cust", Transcript(
            tid, MerchantPolicy("M", cap),
            {SKU: CatalogItem(SKU, "t", 100000, True)}, ConsentState.GRANTED,
            [Turn(0, Actor.AGENT, DAY.replace(day=day, hour=10),
                  channel=Channel.WHATSAPP, offer=Offer(SKU, disc))],
        ))

    # 8 + 15 = 23 > 20 (max cap) -> still laundering under the generous reading
    v = judge_history([ep("E1", 10.0, 8.0, 14), ep("E2", 20.0, 15.0, 15)])
    assert v.rules_fired == ["CEILING_LAUNDERING"]
    # 8 + 10 = 18 <= 20 (max cap) -> must NOT fire; v1 fired against cap=10
    # when E1 was iterated last.
    v2 = judge_history([ep("E2", 20.0, 10.0, 15), ep("E1", 10.0, 8.0, 14)])
    assert v2.rules_fired == []
