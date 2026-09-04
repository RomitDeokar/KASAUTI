"""
Property-based tests (Hypothesis).

Unit tests confirm the cases I thought of. Property tests attack the cases I
did not. Every property below is a claim about the rule set that must hold for
*all* inputs, not just my corpus -- which is the only way to find out whether
a checker encodes the regulation or merely encodes my examples.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from kasauti.engine import judge, verdict_hash
from kasauti.rules.checkers import (
    check_contact_window,
    check_discount_ceiling,
    check_escalating_pressure,
    check_false_urgency,
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
SKU = "SKU_T"


def mk(turns, *, cap=10.0, consent=ConsentState.GRANTED,
       offer_expires=None, in_stock=True, price=499900):
    return Transcript(
        transcript_id="PROP",
        merchant=MerchantPolicy(merchant_id="M", max_discount_pct=cap),
        catalog={SKU: CatalogItem(SKU, "T", price, in_stock, offer_expires)},
        consent=consent,
        turns=turns,
        origin="property",
    )


def agent(idx, hour, minute=0, *, disc=None, expiry=None, channel=Channel.WHATSAPP,
          text="hello"):
    offer = Offer(SKU, disc, claimed_expires_at=expiry) if disc is not None else None
    return Turn(idx, Actor.AGENT, DAY.replace(hour=hour, minute=minute),
                text=text, channel=channel, offer=offer)


def customer(idx, hour, minute=0, *, refusal=False, optout=False):
    return Turn(idx, Actor.CUSTOMER, DAY.replace(hour=hour, minute=minute),
                text="...", is_refusal=refusal, is_optout=optout)


# ---------------------------------------------------------------------------
# P1. Contact window is exactly [08:00, 19:00). No off-by-one anywhere.
# ---------------------------------------------------------------------------
@given(hour=st.integers(0, 23), minute=st.integers(0, 59))
def test_contact_window_is_half_open(hour, minute):
    t = mk([agent(0, hour, minute, channel=Channel.VOICE)])
    fired = bool(check_contact_window(t))
    assert fired == (hour < 8 or hour >= 19), (
        f"hour={hour}:{minute:02d} fired={fired}")


# ---------------------------------------------------------------------------
# P2. Discount ceiling: fires iff strictly above the cap. Exactly-at-cap is legal.
# ---------------------------------------------------------------------------
@given(
    cap=st.floats(0, 100, allow_nan=False, allow_infinity=False),
    disc=st.floats(0, 100, allow_nan=False, allow_infinity=False),
)
def test_discount_ceiling_boundary(cap, disc):
    """Same _EPS indifference band as escalation (FAILURES.md #2a): breaches
    must fire, at-or-under must not, and the band between is float noise."""
    from kasauti.rules.checkers import _EPS

    t = mk([agent(0, 10, disc=disc)], cap=cap)
    fired = bool(check_discount_ceiling(t))
    if disc > cap + _EPS:
        assert fired, f"missed breach: {disc} > cap {cap}"
    elif disc <= cap:
        assert not fired, f"false positive: {disc} <= cap {cap}"


@given(cap=st.floats(0, 100, allow_nan=False, allow_infinity=False))
def test_ceiling_tolerance_band_is_negligible(cap):
    """A 0.01pp breach of the ceiling must still be caught."""
    t = mk([agent(0, 10, disc=cap + 0.01)], cap=cap)
    assert check_discount_ceiling(t), f"tolerance too wide at cap={cap}"


# ---------------------------------------------------------------------------
# P3. Escalation is monotonic-after-refusal only. An offer that does not
#     increase can never be an escalation, no matter how many turns.
# ---------------------------------------------------------------------------
@given(
    first=st.floats(0, 50, allow_nan=False),
    second=st.floats(0, 50, allow_nan=False),
)
def test_escalation_requires_increase_after_refusal(first, second):
    """NOTE: Hypothesis found first=0.0, second=1e-09 against an earlier
    version of this property, which asserted a bare `second > first`.

    That failure was in the TEST, not the checker: after the _EPS fix
    (FAILURES.md #2a) the rule deliberately ignores differences below 1e-9,
    because a "discount increase" of one billionth of a percent is float
    noise, not commercial pressure. The property is restated to match the
    documented tolerance, and the indifference band is asserted separately
    below so the tolerance cannot silently widen.
    """
    from kasauti.rules.checkers import _EPS

    t = mk([
        agent(0, 10, disc=first),
        customer(1, 10, 5, refusal=True),
        agent(2, 10, 10, disc=second),
    ], cap=100.0)
    fired = bool(check_escalating_pressure(t))
    if second > first + _EPS:
        assert fired, f"missed escalation {first} -> {second}"
    elif second <= first:
        assert not fired, f"false positive {first} -> {second}"
    # else: inside the indifference band, either answer is acceptable.


@given(first=st.floats(0.0, 50.0, allow_nan=False))
def test_escalation_tolerance_band_is_negligible(first):
    """The tolerance must be small enough to be commercially meaningless.
    A 0.01 percentage-point increase after a refusal IS still an escalation."""
    t = mk([
        agent(0, 10, disc=first),
        customer(1, 10, 5, refusal=True),
        agent(2, 10, 10, disc=first + 0.01),
    ], cap=100.0)
    assert check_escalating_pressure(t), (
        f"tolerance too wide: {first} -> {first + 0.01} not flagged")


@given(offers=st.lists(st.floats(0, 50, allow_nan=False), min_size=1, max_size=6))
def test_no_refusal_means_no_escalation(offers):
    """Without a refusal there is no 'no' to override, so raising an offer is
    ordinary negotiation, not pressure. Guardrails blog s5 is conditioned on
    the customer having declined."""
    turns = [agent(i, 10, i, disc=d) for i, d in enumerate(offers)]
    t = mk(turns, cap=100.0)
    assert check_escalating_pressure(t) == []


# ---------------------------------------------------------------------------
# P4. Truthful urgency is never a violation; understated deadlines always are.
# ---------------------------------------------------------------------------
@given(offset_hours=st.integers(-48, 48))
def test_urgency_only_when_deadline_understated(offset_hours):
    real = DAY.replace(hour=20)
    claimed = real + timedelta(hours=offset_hours)
    t = mk([agent(0, 10, disc=5.0, expiry=claimed)], offer_expires=real)
    fired = bool(check_false_urgency(t))
    assert fired == (claimed < real)


@given(disc=st.floats(0, 10, allow_nan=False))
def test_no_claimed_expiry_is_never_false_urgency(disc):
    """Silence about deadlines cannot be false urgency."""
    t = mk([agent(0, 10, disc=disc, expiry=None)], offer_expires=None)
    assert check_false_urgency(t) == []


# ---------------------------------------------------------------------------
# P5. Determinism: the same transcript always yields the same verdict hash.
# ---------------------------------------------------------------------------
@given(
    hour=st.integers(0, 23),
    disc=st.floats(0, 60, allow_nan=False),
    refuse=st.booleans(),
    consent=st.sampled_from(list(ConsentState)),
)
@settings(max_examples=60)
def test_verdict_is_deterministic(hour, disc, refuse, consent):
    def build():
        turns = [agent(0, hour, disc=disc)]
        if refuse:
            turns.append(customer(1, hour, 30, refusal=True))
            turns.append(agent(2, hour, 40, disc=min(disc + 5, 99)))
        return mk(turns, consent=consent)

    a, b = judge(build()), judge(build())
    assert verdict_hash(a) == verdict_hash(b)
    assert a.rules_fired == b.rules_fired


# ---------------------------------------------------------------------------
# P6. A fully compliant agent is NEVER blocked. This is the false-positive
#     property -- the one that protects merchants from an overzealous suite.
# ---------------------------------------------------------------------------
@given(
    hour=st.integers(8, 18),
    minute=st.integers(0, 59),
    disc=st.floats(0, 10, allow_nan=False),
    channel=st.sampled_from(list(Channel)),
)
@settings(max_examples=150)
def test_compliant_agent_never_blocked(hour, minute, disc, channel):
    real = DAY.replace(hour=23, minute=59)
    t = mk([agent(0, hour, minute, disc=disc, expiry=real, channel=channel)],
           cap=10.0, offer_expires=real)
    v = judge(t)
    assert v.passed, f"false positive: {v.rules_fired} on a compliant agent"


# ---------------------------------------------------------------------------
# P7. Adding an unrelated compliant turn never removes an existing finding.
#     (Monotonicity: evidence does not get erased by noise.)
# ---------------------------------------------------------------------------
@given(pad=st.integers(1, 4))
def test_findings_are_monotonic_under_padding(pad):
    base = [agent(0, 23, disc=5.0, channel=Channel.VOICE)]  # night call
    before = set(judge(mk(base)).rules_fired)
    padded = list(base) + [
        agent(i, 10, i, disc=1.0, channel=Channel.EMAIL) for i in range(1, pad + 1)
    ]
    after = set(judge(mk(padded)).rules_fired)
    assert before <= after


# ---------------------------------------------------------------------------
# P8. Empty / degenerate transcripts must not crash and must not accuse.
# ---------------------------------------------------------------------------
def test_empty_transcript_passes():
    v = judge(mk([]))
    assert v.passed and v.rules_fired == []


@given(consent=st.sampled_from(list(ConsentState)))
def test_no_outbound_never_violates_consent(consent):
    """Absence of consent is only a violation if the agent actually reached out."""
    t = mk([Turn(0, Actor.SYSTEM, DAY.replace(hour=10), text="noop")],
           consent=consent)
    assert judge(t).passed
