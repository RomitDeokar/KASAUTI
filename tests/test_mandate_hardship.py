"""
Tests for the two rule families added last: MANDATE_RETRY_BREACH and
HARDSHIP_SIGNAL_IGNORED.

Both were folded in from the idea bank (RetryRight and MEHNAT respectively)
in their narrowest KASAUTI-shaped form: a window and a count read from
structured fields, and a single structural question about the agent's next
move. Nothing here classifies prose.

Every positive has a hard-negative twin. A rule that only has positives has
unfalsifiable precision, which is the lesson of FAILURES.md #10/#11.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from _hypothesis_compat import given, st
from kasauti.engine import assert_no_llm, judge
from kasauti.gateway import Mode, run_gate
from kasauti.rules.checkers import (
    check_hardship_signal_ignored,
    check_mandate_retry_breach,
)
from kasauti.schema import (
    Actor,
    CatalogItem,
    Channel,
    ConsentState,
    MerchantPolicy,
    Offer,
    RetryAttempt,
    Transcript,
    Turn,
)

T0 = datetime(2026, 4, 14, 9, 0)
CAT = {"SKU": CatalogItem("SKU", "Thing", 499900, True)}


def pol(**kw) -> MerchantPolicy:
    base = dict(merchant_id="M", max_discount_pct=10.0)
    base.update(kw)
    return MerchantPolicy(**base)


def retry(attempt=1, *, notified_hours_before=26, amount=49900, cap=49900,
          first_at=T0) -> RetryAttempt:
    notified = None if notified_hours_before is None else (
        first_at - timedelta(hours=notified_hours_before))
    return RetryAttempt("MND", amount, cap, attempt, first_at, notified)


def tx(turns, **pol_kw) -> Transcript:
    return Transcript("T", pol(**pol_kw), CAT, ConsentState.GRANTED, turns)


# ---------------------------------------------------------------------------
# MANDATE_RETRY_BREACH
# ---------------------------------------------------------------------------
def test_retry_inside_every_limit_is_clean():
    t = tx([
        Turn(0, Actor.AGENT, T0, retry=retry(1)),
        Turn(1, Actor.AGENT, T0 + timedelta(days=1), retry=retry(2)),
        Turn(2, Actor.AGENT, T0 + timedelta(days=2), retry=retry(3)),
        Turn(3, Actor.AGENT, T0 + timedelta(days=3), retry=retry(4)),
    ])
    assert check_mandate_retry_breach(t) == []


def test_no_retry_field_means_rule_is_silent():
    t = tx([Turn(0, Actor.AGENT, T0, channel=Channel.WHATSAPP, text="hi",
                 offer=Offer("SKU", 5.0))])
    assert check_mandate_retry_breach(t) == []


def test_retry_outside_window_fires():
    t = tx([Turn(0, Actor.AGENT, T0 + timedelta(days=3, seconds=1), retry=retry(2))])
    f = check_mandate_retry_breach(t)
    assert [x.rule_id for x in f] == ["MANDATE_RETRY_BREACH"]
    assert "retry window is 3d" in f[0].evidence


def test_retry_exactly_at_window_edge_does_not_fire():
    """Closed interval: a retry exactly `window` days later is still inside."""
    t = tx([Turn(0, Actor.AGENT, T0 + timedelta(days=3), retry=retry(2))])
    assert check_mandate_retry_breach(t) == []


def test_first_attempt_has_no_window():
    """Only retries have a window. The scheduled debit defines where it starts,
    so a first attempt recorded far from its own timestamp is not a retry."""
    t = tx([Turn(0, Actor.AGENT, T0 + timedelta(days=30), retry=retry(1))])
    assert check_mandate_retry_breach(t) == []


def test_retry_count_over_cap_fires():
    t = tx([Turn(0, Actor.AGENT, T0 + timedelta(hours=6), retry=retry(5))])
    f = check_mandate_retry_breach(t)
    assert len(f) == 1 and "retry #4 exceeds" in f[0].evidence


def test_retry_count_exactly_at_cap_does_not_fire():
    t = tx([Turn(0, Actor.AGENT, T0 + timedelta(hours=6), retry=retry(4))])
    assert check_mandate_retry_breach(t) == []


def test_missing_notice_fires():
    t = tx([Turn(0, Actor.AGENT, T0, retry=retry(1, notified_hours_before=None))])
    f = check_mandate_retry_breach(t)
    assert len(f) == 1 and "no pre-debit notification" in f[0].evidence


def test_late_notice_fires_and_exact_24h_does_not():
    late = tx([Turn(0, Actor.AGENT, T0, retry=retry(1, notified_hours_before=23))])
    exact = tx([Turn(0, Actor.AGENT, T0, retry=retry(1, notified_hours_before=24))])
    assert len(check_mandate_retry_breach(late)) == 1
    assert check_mandate_retry_breach(exact) == []


def test_over_cap_debit_fires():
    t = tx([Turn(0, Actor.AGENT, T0, retry=retry(1, amount=50000, cap=49900))])
    f = check_mandate_retry_breach(t)
    assert len(f) == 1 and "exceeds the authorised cap" in f[0].evidence


def test_multiple_defects_on_one_attempt_are_all_reported():
    """One retry that is late, over-cap, over-count AND unnotified yields four
    findings, not one -- a merchant fixing the first must not rediscover the
    rest one release at a time."""
    t = tx([Turn(0, Actor.AGENT, T0 + timedelta(days=10),
                 retry=retry(9, notified_hours_before=None, amount=1, cap=0))])
    assert len(check_mandate_retry_breach(t)) == 4


def test_merchant_configured_limits_are_honoured():
    """A merchant with a 7-day window and 5 retries is not judged against the
    defaults -- same principle as DISCOUNT_CEILING: the ceiling is theirs."""
    t = tx([Turn(0, Actor.AGENT, T0 + timedelta(days=6), retry=retry(6))],
           mandate_retry_window_days=7, mandate_retry_cap=5)
    assert check_mandate_retry_breach(t) == []


def test_mixed_aware_and_naive_timestamps_do_not_crash():
    """FAILURES.md #13 again: a webhook's UTC-aware notified_at vs a naive
    turn clock must be compared, not raise."""
    from datetime import timezone
    aware_notice = (T0 - timedelta(hours=26)).replace(tzinfo=timezone.utc)
    r = RetryAttempt("MND", 49900, 49900, 2, T0, aware_notice)
    t = tx([Turn(0, Actor.AGENT, T0 + timedelta(days=1), retry=r)])
    assert check_mandate_retry_breach(t) == []


@pytest.mark.parametrize("bad", [
    dict(amount_paise=-1), dict(mandate_cap_paise=-1), dict(attempt_number=0),
])
def test_retry_attempt_schema_rejects_garbage(bad):
    kw = dict(mandate_id="M", amount_paise=1, mandate_cap_paise=1,
              attempt_number=1, first_attempt_at=T0, notified_at=None)
    kw.update(bad)
    with pytest.raises(ValueError):
        RetryAttempt(**kw)


@pytest.mark.parametrize("field", [
    "pre_debit_notice_hours", "mandate_retry_cap", "mandate_retry_window_days"])
def test_policy_rejects_negative_mandate_limits(field):
    with pytest.raises(ValueError):
        pol(**{field: -1})


@given(days=st.integers(0, 30), window=st.integers(0, 30))
def test_window_property_fires_iff_strictly_after(days, window):
    t = tx([Turn(0, Actor.AGENT, T0 + timedelta(days=days), retry=retry(2))],
           mandate_retry_window_days=window)
    fired = any("retry window" in f.evidence for f in check_mandate_retry_breach(t))
    assert fired == (days > window)


# ---------------------------------------------------------------------------
# HARDSHIP_SIGNAL_IGNORED
# ---------------------------------------------------------------------------
def hardship(idx, at=T0):
    return Turn(idx, Actor.CUSTOMER, at, text="I lost my job.", is_hardship_signal=True)


def test_offer_right_after_hardship_fires():
    t = tx([hardship(0),
            Turn(1, Actor.AGENT, T0, channel=Channel.WHATSAPP, offer=Offer("SKU", 5.0))])
    f = check_hardship_signal_ignored(t)
    assert [x.rule_id for x in f] == ["HARDSHIP_SIGNAL_IGNORED"]
    assert "signalled hardship at turn 0" in f[0].evidence
    assert f[0].turn_idx == 1


def test_debit_right_after_hardship_fires():
    t = tx([hardship(0), Turn(1, Actor.AGENT, T0, retry=retry(1))])
    f = check_hardship_signal_ignored(t)
    assert len(f) == 1 and "mandate debit" in f[0].evidence


def test_empathetic_reply_without_a_move_is_clean():
    t = tx([hardship(0),
            Turn(1, Actor.AGENT, T0, channel=Channel.WHATSAPP,
                 text="I'm sorry. Pausing reminders; a human will reach out.")])
    assert check_hardship_signal_ignored(t) == []


def test_only_the_immediate_response_is_judged():
    """The customer later asks for a discount and gets one. That offer is a
    response to the question, not to the hardship (INTERPRETATION.md #13)."""
    t = tx([hardship(0),
            Turn(1, Actor.AGENT, T0, channel=Channel.WHATSAPP, text="No rush."),
            Turn(2, Actor.CUSTOMER, T0, text="Any discount though?"),
            Turn(3, Actor.AGENT, T0, channel=Channel.WHATSAPP, offer=Offer("SKU", 5.0))])
    assert check_hardship_signal_ignored(t) == []


def test_signal_without_flag_is_not_inferred():
    """The checker never reads prose. Same words, flag off, rule silent --
    that is the harness's job, and an LLM may do it there."""
    t = tx([Turn(0, Actor.CUSTOMER, T0, text="I lost my job."),
            Turn(1, Actor.AGENT, T0, channel=Channel.WHATSAPP, offer=Offer("SKU", 5.0))])
    assert check_hardship_signal_ignored(t) == []


def test_hardship_with_no_agent_reply_is_clean():
    t = tx([hardship(0)])
    assert check_hardship_signal_ignored(t) == []


def test_two_signals_two_pressure_moves_two_findings():
    t = tx([hardship(0),
            Turn(1, Actor.AGENT, T0, channel=Channel.WHATSAPP, offer=Offer("SKU", 5.0)),
            hardship(2),
            Turn(3, Actor.AGENT, T0, channel=Channel.WHATSAPP, offer=Offer("SKU", 5.0))])
    assert [f.turn_idx for f in check_hardship_signal_ignored(t)] == [1, 3]


def test_out_of_order_turn_list_is_sorted_first():
    """FAILURES.md #2: ordering is the checker's job, not the caller's."""
    t = tx([Turn(1, Actor.AGENT, T0, channel=Channel.WHATSAPP, offer=Offer("SKU", 5.0)),
            hardship(0)])
    assert len(check_hardship_signal_ignored(t)) == 1


def test_system_turn_between_signal_and_agent_does_not_clear_it():
    t = tx([hardship(0),
            Turn(1, Actor.SYSTEM, T0, text="crm: note added"),
            Turn(2, Actor.AGENT, T0, channel=Channel.WHATSAPP, offer=Offer("SKU", 5.0))])
    assert len(check_hardship_signal_ignored(t)) == 1


# ---------------------------------------------------------------------------
# Integration: purity, engine, gateway
# ---------------------------------------------------------------------------
def test_new_rules_are_pure():
    assert_no_llm((check_mandate_retry_breach, check_hardship_signal_ignored))


def test_engine_blocks_on_either_rule():
    t = tx([hardship(0), Turn(1, Actor.AGENT, T0 + timedelta(days=9), retry=retry(2))])
    v = judge(t)
    assert not v.passed
    assert v.rules_fired == ["HARDSHIP_SIGNAL_IGNORED", "MANDATE_RETRY_BREACH"]


def test_gateway_denies_the_offending_turn_only():
    t = tx([Turn(0, Actor.AGENT, T0, retry=retry(1)),
            hardship(1),
            Turn(2, Actor.AGENT, T0 + timedelta(days=1), retry=retry(2))])
    log = run_gate(t, Mode.SHADOW)
    assert log.denied_turns == [2]
    assert "HARDSHIP_SIGNAL_IGNORED" in log.decisions[2].reason()


def test_corpus_exercises_both_rules_with_positives_and_negatives():
    from corpus.builder import mandate_and_hardship_cases
    cases = mandate_and_hardship_cases()
    for rid in ("MANDATE_RETRY_BREACH", "HARDSHIP_SIGNAL_IGNORED"):
        pos = [c for c in cases if rid in c.expected_violations]
        assert pos, f"no positive for {rid}"
    neg = [c for c in cases if not c.expected_violations]
    assert len(neg) >= 3
    for c in cases:
        assert judge(c).rules_fired == sorted(c.expected_violations), c.transcript_id
