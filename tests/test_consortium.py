"""
Consortium-layer tests.

Two things are being defended here, and they pull in opposite directions:

  1. The network rules must FIRE on abuse that no single merchant can see.
  2. The network rules must STAY SILENT on ordinary multi-merchant shopping,
     and on events the single-merchant layer already owns.

(2) is the harder half and it is where the value is. A cross-merchant rule
that flags every customer who shops at five stores is not a compliance tool,
it is a way to make an operator disable the compliance tool.

The regression tests at the bottom pin FAILURES.md #9 -- the phantom-join bug
that accused six unrelated people of being one harassed customer.
"""
from __future__ import annotations

import pytest
from _hypothesis_compat import given, settings, st

from corpus.consortium_fixtures import ALL_FIXTURES, _c
from kasauti.consortium import (
    ALL_CONSORTIUM_CHECKERS,
    CONSORTIUM_RULE_IDS,
    ConsortiumConfig,
    ConsortiumLedger,
    DegenerateIdentifier,
    MerchantReport,
    evaluate_consortium,
    join_key,
)
from kasauti.engine import assert_no_llm

SALT = "test-salt"


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_fixture_matches_expected_rules(name):
    led, expected, why = ALL_FIXTURES[name]()
    got = sorted({f.rule_id for f in evaluate_consortium(led)})
    assert got == sorted(expected), f"{name}: {why}"


def test_every_rule_id_is_exercised_by_the_corpus():
    """A rule with no fixture is an untested rule.

    This test is the reason the corpus cannot rot: adding a rule id without
    adding a case that fires it fails the build.
    """
    fired = set()
    for fn in ALL_FIXTURES.values():
        led, _, _ = fn()
        fired |= {f.rule_id for f in evaluate_consortium(led)}
    assert fired == set(CONSORTIUM_RULE_IDS)


# ---------------------------------------------------------------------------
# Purity -- the network layer is held to the same standard as the rest
# ---------------------------------------------------------------------------
def test_consortium_checkers_are_pure():
    assert_no_llm(ALL_CONSORTIUM_CHECKERS)


def test_determinism_same_ledger_same_findings():
    led, _, _ = ALL_FIXTURES["N2_FLOODING_NETWORK"]()
    a = [(f.rule_id, f.evidence) for f in evaluate_consortium(led)]
    b = [(f.rule_id, f.evidence) for f in evaluate_consortium(led)]
    assert a == b


def test_findings_are_order_independent():
    """Merchant report order must not change the verdict.

    Reports arrive from independent merchants with no global ordering, so a
    verdict that depends on arrival order would be non-reproducible in
    exactly the situation this layer exists for.
    """
    led, expected, _ = ALL_FIXTURES["N1_SUPPRESSION_NETWORK"]()
    forward = sorted({f.rule_id for f in evaluate_consortium(led)})
    led.reports.reverse()
    backward = sorted({f.rule_id for f in evaluate_consortium(led)})
    assert forward == backward == sorted(expected)


# ---------------------------------------------------------------------------
# The privacy-preserving join
# ---------------------------------------------------------------------------
def test_same_person_different_formats_joins():
    """The whole layer is worthless if this fails.

    Three merchant databases store one human three ways. If normalisation
    misses, the join silently finds nothing and the output is a confident
    "no abuse detected".
    """
    keys = {join_key(p, SALT) for p in
            ("+91 98765 43210", "9876543210", "09876543210",
             "+919876543210", "98765-43210")}
    assert len(keys) == 1


def test_distinct_people_do_not_join():
    keys = {join_key(p, SALT) for p in
            ("9876543210", "9876543211", "8123456789")}
    assert len(keys) == 3


def test_plaintext_identifier_never_stored_on_the_report():
    """No merchant boundary may be crossed by a raw identifier.

    Asserted over the dataclass's actual field values rather than by reading
    the constructor, so adding a field that leaks PII fails this test.
    """
    phone = "9876543210"
    r = MerchantReport.build(phone, SALT, "MERCH_A",
                             contacts=((_c(0), "whatsapp"),))
    blob = repr(r)
    assert phone not in blob
    assert "98765" not in blob
    assert r.join_key != phone


def test_salt_changes_the_key():
    """Without this, the 'merchant cannot read a stranger's identifier'
    claim in the module docstring is false."""
    assert join_key("9876543210", "salt-a") != join_key("9876543210", "salt-b")


# ---------------------------------------------------------------------------
# REGRESSION: FAILURES.md #9 -- the phantom join
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    "", " ", "-", "na", "N/A", "nan", "none", "null", "unknown", "test",
    "0", "+", "91", "+91", "0000000000", "9999999999", "1234567890",
    "5876543210",   # 10 digits but not a valid mobile series (must be 6-9)
    "98765",        # too short
    "98765432101",  # too long
    "abc",
])
def test_degenerate_identifiers_are_refused(bad):
    """Every one of these used to produce a valid-looking join key.

    Blank was the killer: all six merchants reporting a customer with a
    missing phone number joined onto ONE key and the engine reported
    CONTACT_FLOODING_NETWORK against a person who does not exist.
    """
    with pytest.raises(DegenerateIdentifier):
        join_key(bad, SALT)


def test_phantom_join_cannot_recur_end_to_end():
    """The exact scenario from FAILURES.md #9, as an executable regression.

    Six merchants, six unrelated customers, all with missing identifiers.
    The old code produced 7 findings. The correct behaviour is to refuse to
    build the ledger at all -- loudly.
    """
    cfg = ConsortiumConfig()
    with pytest.raises(DegenerateIdentifier):
        MerchantReport.build("", cfg.salt, "MERCH_A",
                             contacts=((_c(0), "whatsapp"),),
                             opted_out_at=_c(0))


def test_valid_identifiers_still_accepted():
    """The fix must not degenerate into refusing everything.

    Without this test, `_reject_if_degenerate` could raise unconditionally
    and every test above would still pass.
    """
    for good in ("9876543210", "6123456789", "7000000001",
                 "A@B.com", "user.name@shop.co.in"):
        assert len(join_key(good, SALT)) == 16


# ---------------------------------------------------------------------------
# De-duplication against the single-merchant layer
# ---------------------------------------------------------------------------
def test_single_merchant_breach_is_not_claimed_by_the_network_layer():
    """One event must not appear twice at two severities.

    crossepisode.py reports a merchant breaching its own opt-out at BLOCK.
    If the network layer also reported it at WARN, an operator would see the
    same event twice with conflicting severity and lose trust in the queue.
    """
    led, expected, why = ALL_FIXTURES["N5_SINGLE_MERCHANT_DEDUP"]()
    assert evaluate_consortium(led) == [], why


def test_min_merchants_guard_holds():
    """A 'network' finding from one merchant is a contradiction in terms."""
    cfg = ConsortiumConfig(min_merchants=2)
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build(
        "9876543210", cfg.salt, "MERCH_A",
        contacts=tuple((_c(i * 0.2), "sms") for i in range(20)),
    ))
    assert not [f for f in evaluate_consortium(led)
                if f.rule_id == "CONTACT_FLOODING_NETWORK"]


# ---------------------------------------------------------------------------
# The false-positive floor
# ---------------------------------------------------------------------------
def test_ordinary_multi_merchant_shopping_is_silent():
    led, _, why = ALL_FIXTURES["N4_CLEAN_MULTI_MERCHANT"]()
    assert evaluate_consortium(led) == [], why


@settings(max_examples=200, deadline=None)
@given(
    n_merchants=st.integers(min_value=2, max_value=9),
    day_gap=st.floats(min_value=2.5, max_value=30.0),
)
def test_no_optout_and_sparse_contact_never_fires(n_merchants, day_gap):
    """Property: widely-spaced single contacts with no opt-out and distinct
    SKUs must never produce a finding, however many merchants participate.

    This is the property a naive 'many merchants = suspicious' detector
    violates, and the one that decides whether an operator keeps the tool on.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    for i in range(n_merchants):
        led.add(MerchantReport.build(
            "9876543210", cfg.salt, f"MERCH_{i}",
            contacts=((_c(i * day_gap), "whatsapp"),),
            offers=((_c(i * day_gap), f"SKU_{i}", 5.0),),
            max_discount_pct=10.0,
        ))
    assert evaluate_consortium(led) == []


@settings(max_examples=100, deadline=None)
@given(pct=st.floats(min_value=0.1, max_value=4.9))
def test_cumulative_discount_under_ceiling_never_fires(pct):
    """Two merchants, same SKU, total strictly under the ceiling."""
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    for i in range(2):
        led.add(MerchantReport.build(
            "9876543210", cfg.salt, f"MERCH_{i}",
            offers=((_c(i), "SKU_X", pct),),
            max_discount_pct=10.0,
        ))
    assert not [f for f in evaluate_consortium(led)
                if f.rule_id == "CEILING_LAUNDERING_NETWORK"]


def test_exact_ceiling_does_not_fire():
    """Binary floats already caused this exact false positive once, in the
    per-episode rules (FAILURES.md #2a). 0.1+0.2 != 0.3 in IEEE 754, so a
    naive `>` comparison fires at exactly the ceiling."""
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_A",
                                 offers=((_c(0), "SKU_X", 0.1),),
                                 max_discount_pct=0.3))
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_B",
                                 offers=((_c(1), "SKU_X", 0.2),),
                                 max_discount_pct=0.3))
    assert not [f for f in evaluate_consortium(led)
                if f.rule_id == "CEILING_LAUNDERING_NETWORK"]


# ---------------------------------------------------------------------------
# Regressions: FAILURES.md #10, #11, #12
#
# Each of these three failed against the code as it shipped in the previous
# commit. They are the reason this file grew, and they were all found by
# probing the layer's own de-duplication and windowing assumptions rather than
# by re-reading the module.
# ---------------------------------------------------------------------------
def test_merchant_breaching_its_own_optout_is_not_a_network_finding():
    """FAILURES.md #10 -- the double-reporting bug, and the wrong-blame bug.

    MERCH_B holds its own opt-out and contacts after it. That is
    crossepisode.py's SUPPRESSION_BREACH at BLOCK severity, and the network
    layer must stay silent so an operator does not see one event twice at two
    severities.

    N5_SINGLE_MERCHANT_DEDUP already tested this shape -- but with only ONE
    merchant in the ledger, where `min_merchants`-style reasoning made it pass
    for the wrong reason. The bug needed TWO merchants that BOTH hold
    opt-outs, because the old guard silenced only the holder of the EARLIEST
    opt-out. With A opting out first, B's breach of B's own suppression got
    reported as a cross-merchant breach *against A* -- double-reported AND
    attributed to the wrong merchant.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build(
        "9876543210", cfg.salt, "MERCH_A",
        opted_out_at=_c(1), contacts=((_c(0), "sms"),),
    ))
    led.add(MerchantReport.build(
        "9876543210", cfg.salt, "MERCH_B",
        opted_out_at=_c(2), contacts=((_c(5), "sms"),),
    ))
    breaches = [f for f in evaluate_consortium(led)
                if f.rule_id == "SUPPRESSION_BREACH_NETWORK"]
    assert breaches == [], (
        "network layer re-reported an event the single-merchant BLOCK layer "
        f"owns: {[f.evidence for f in breaches]}"
    )


def test_true_cross_merchant_suppression_breach_still_fires():
    """The dedup fix must not silence the shape the layer exists for.

    A guard that suppresses too much is the failure mode of every dedup fix,
    so this is asserted immediately next to the test above rather than
    somewhere else in the file.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_A",
                                 opted_out_at=_c(1)))
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_B",
                                 contacts=((_c(5), "sms"),)))
    ids = [f.rule_id for f in evaluate_consortium(led)]
    assert "SUPPRESSION_BREACH_NETWORK" in ids


def test_merchant_contacting_before_its_own_optout_still_counts():
    """Boundary: B's opt-out arriving AFTER the contact must not excuse it.

    The dedup guard keys on whether the merchant's own opt-out predates the
    contact. If it keyed merely on "this merchant has an opt-out somewhere",
    a merchant could contact a suppressed customer and then record its own
    opt-out afterwards to launder the finding away.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_A",
                                 opted_out_at=_c(1)))
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_B",
                                 contacts=((_c(3), "sms"),),
                                 opted_out_at=_c(9)))
    ids = [f.rule_id for f in evaluate_consortium(led)]
    assert "SUPPRESSION_BREACH_NETWORK" in ids, (
        "a later self-recorded opt-out must not retroactively excuse a "
        "contact that breached another merchant's suppression"
    )


def test_offers_outside_the_window_are_not_laundering():
    """FAILURES.md #11 -- the unbounded-window false positive.

    Two 10% offers six YEARS apart, each inside its own merchant's 10%
    ceiling, used to sum to 20% and fire. That is a repeat customer, not
    discount laundering. This is the false positive a merchant would actually
    have complained about, and it was invisible because every fixture in the
    suite happened to be tightly clustered in time.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_A",
                                 offers=((_c(0), "SKU_X", 10.0),),
                                 max_discount_pct=10.0))
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_B",
                                 offers=((_c(365 * 6), "SKU_X", 10.0),),
                                 max_discount_pct=10.0))
    launder = [f for f in evaluate_consortium(led)
               if f.rule_id == "CEILING_LAUNDERING_NETWORK"]
    assert launder == [], (
        f"offers {365 * 6} days apart flagged as stacked: "
        f"{[f.evidence for f in launder]}"
    )


def test_offers_inside_the_window_are_still_laundering():
    """The window fix must not silence genuine stacking."""
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    for i, m in enumerate(("MERCH_A", "MERCH_B")):
        led.add(MerchantReport.build("9876543210", cfg.salt, m,
                                     offers=((_c(i), "SKU_X", 10.0),),
                                     max_discount_pct=10.0))
    ids = [f.rule_id for f in evaluate_consortium(led)]
    assert "CEILING_LAUNDERING_NETWORK" in ids


def test_laundering_window_slides_and_finds_the_worst_burst():
    """A burst inside the window must be caught even when padded by old,
    innocent offers on the same SKU.

    The naive window implementation only checks the LAST offer's trailing
    window, which misses a burst followed by a long quiet period.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_A",
                                 offers=((_c(0), "SKU_X", 10.0),),
                                 max_discount_pct=10.0))
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_B",
                                 offers=((_c(1), "SKU_X", 10.0),),
                                 max_discount_pct=10.0))
    # A much later, lone offer that is innocent on its own.
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_C",
                                 offers=((_c(400), "SKU_X", 5.0),),
                                 max_discount_pct=10.0))
    ids = [f.rule_id for f in evaluate_consortium(led)]
    assert "CEILING_LAUNDERING_NETWORK" in ids, (
        "the early burst was missed because only the final offer's window "
        "was examined"
    )


def test_bystander_merchant_ceiling_does_not_leak():
    """A merchant that never offered this SKU must not raise its threshold.

    This one was NOT a bug -- the max() was already correctly scoped inside
    the offer loop. It is pinned anyway because hoisting that line out of the
    loop is a natural-looking refactor that would silently disable the rule
    for any customer who also shops at one permissive merchant.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_A",
                                 offers=((_c(0), "SKU_X", 10.0),),
                                 max_discount_pct=10.0))
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_B",
                                 offers=((_c(1), "SKU_X", 10.0),),
                                 max_discount_pct=10.0))
    led.add(MerchantReport.build("9876543210", cfg.salt, "MERCH_C",
                                 contacts=((_c(2), "sms"),),
                                 max_discount_pct=90.0))
    ids = [f.rule_id for f in evaluate_consortium(led)]
    assert "CEILING_LAUNDERING_NETWORK" in ids, (
        "an unrelated merchant's permissive ceiling silenced the rule"
    )


def test_evidence_and_citation_disclose_the_window():
    """The window is an operator policy choice, so it must appear in the
    output a reviewer reads, not only in the source.

    FAILURES.md #7 was two README claims the repo could not back. The same
    discipline applies to a Finding: a threshold that shapes the verdict and
    is invisible in the evidence is an unfalsifiable claim.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    for i, m in enumerate(("MERCH_A", "MERCH_B")):
        led.add(MerchantReport.build("9876543210", cfg.salt, m,
                                     offers=((_c(i), "SKU_X", 10.0),),
                                     max_discount_pct=10.0))
    f = next(f for f in evaluate_consortium(led)
             if f.rule_id == "CEILING_LAUNDERING_NETWORK")
    assert f"{cfg.window_days}d" in f.evidence
    assert f"{cfg.window_days}d" in f.citation
    assert "OPERATOR POLICY" in f.citation
