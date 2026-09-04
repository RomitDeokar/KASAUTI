"""
Consortium fixtures: pooled multi-merchant histories.

The invariant here is the same one `corpus/history.py` holds, pushed one
level out, and it is what makes this layer worth shipping:

    Every merchant's OWN report is individually compliant.

Not "mostly clean". Individually compliant, asserted by
`scripts/run_consortium.py` before any metric is printed. If a merchant were
independently dirty, the single-merchant engine would already catch it and
the network rule would be taking credit for someone else's work.

Each fixture also deliberately includes the identifier in a DIFFERENT
FORMAT per merchant ("+91 98765 43210" vs "9876543210" vs "09876543210"),
because that is exactly how the same human appears across real merchant
databases, and because normalisation failure is the silent way this whole
layer stops working. See FAILURES.md #9.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from kasauti.consortium import (
    ConsortiumConfig,
    ConsortiumLedger,
    MerchantReport,
)

BASE = datetime(2026, 5, 4, 10, 0)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)


def _c(day: float, hour: int = 10) -> datetime:
    return BASE + timedelta(days=day) + timedelta(hours=hour - 10)


# The same person, as three merchant databases would actually store them.
PHONE_FORMATS = ("+91 98765 43210", "9876543210", "09876543210")


def suppression_network() -> tuple[ConsortiumLedger, list[str], str]:
    """Opted out at ONE merchant, contacted by TWO others afterwards.

    Every merchant is clean on its own log: A honoured its own opt-out
    (it never contacts again), B and C never received one.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build(
        PHONE_FORMATS[0], cfg.salt, "MERCH_A",
        contacts=((_c(0), "whatsapp"),),
        opted_out_at=_c(0, 11),
    ))
    led.add(MerchantReport.build(
        PHONE_FORMATS[1], cfg.salt, "MERCH_B",
        contacts=((_c(2), "sms"),),
    ))
    led.add(MerchantReport.build(
        PHONE_FORMATS[2], cfg.salt, "MERCH_C",
        contacts=((_c(4), "voice"),),
    ))
    return led, ["SUPPRESSION_BREACH_NETWORK"], (
        "opt-out recorded at MERCH_A; MERCH_B and MERCH_C each contact once "
        "afterwards. No single merchant's log shows a breach."
    )


def flooding_network() -> tuple[ConsortiumLedger, list[str], str]:
    """Eight contacts in six days, from four merchants, two each.

    Two contacts per merchant is below the single-merchant cap of 3, so
    crossepisode.py stays silent on every one of them -- correctly.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    for i, m in enumerate(["MERCH_A", "MERCH_B", "MERCH_C", "MERCH_D"]):
        led.add(MerchantReport.build(
            PHONE_FORMATS[i % 3], cfg.salt, m,
            contacts=((_c(i * 1.5), "whatsapp"), (_c(i * 1.5 + 0.5), "sms")),
        ))
    return led, ["CONTACT_FLOODING_NETWORK"], (
        "8 contacts / 5 days across 4 merchants, 2 each -- every merchant "
        "inside its own per-merchant cap of 3. The rule fires at the 6th "
        "contact (3 merchants in window), not the 8th: it names the contact "
        "that crossed the line, not the whole burst."
    )


def laundering_network() -> tuple[ConsortiumLedger, list[str], str]:
    """Same SKU discounted at three merchants, each inside its own ceiling."""
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    for i, m in enumerate(["MERCH_A", "MERCH_B", "MERCH_C"]):
        led.add(MerchantReport.build(
            PHONE_FORMATS[i], cfg.salt, m,
            contacts=((_c(i), "whatsapp"),),
            offers=((_c(i), "SKU_AIRFRYER", 10.0),),
            max_discount_pct=10.0,
        ))
    return led, ["CEILING_LAUNDERING_NETWORK"], (
        "30% cumulative on one SKU across 3 merchants; every single offer is "
        "exactly at -- not above -- its own merchant's 10% ceiling."
    )


def clean_multi_merchant() -> tuple[ConsortiumLedger, list[str], str]:
    """The hard negative: a normal person who shops at five merchants.

    This is the fixture that decides whether the network layer is usable.
    Five merchants, one contact each, no opt-out, different SKUs. A naive
    'many merchants + many contacts = suspicious' heuristic fires here and
    would flag ordinary shopping as abuse. KASAUTI must stay silent.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    skus = ["SKU_AIRFRYER", "SKU_KETTLE", "SKU_TOASTER", "SKU_BLENDER",
            "SKU_MIXER"]
    for i, m in enumerate(["MERCH_A", "MERCH_B", "MERCH_C", "MERCH_D",
                           "MERCH_E"]):
        led.add(MerchantReport.build(
            PHONE_FORMATS[i % 3], cfg.salt, m,
            contacts=((_c(i * 3), "whatsapp"),),
            offers=((_c(i * 3), skus[i], 8.0),),
            max_discount_pct=10.0,
        ))
    return led, [], (
        "5 merchants, 1 contact each, distinct SKUs, no opt-out. Ordinary "
        "multi-merchant shopping. Must NOT fire."
    )


def clean_single_merchant_owns_it() -> tuple[ConsortiumLedger, list[str], str]:
    """One merchant breaches its own opt-out -- network layer must NOT claim it.

    This is a de-duplication test, not an abuse test. crossepisode.py already
    reports this at BLOCK severity. If the network layer also reported it at
    WARN, an operator would see one event twice with two different severities
    and stop trusting the queue.
    """
    cfg = ConsortiumConfig()
    led = ConsortiumLedger(config=cfg)
    led.add(MerchantReport.build(
        PHONE_FORMATS[0], cfg.salt, "MERCH_A",
        contacts=((_c(0), "whatsapp"), (_c(3), "sms")),
        opted_out_at=_c(1),
    ))
    return led, [], (
        "MERCH_A contacts after its OWN opt-out. Single-merchant layer owns "
        "this at BLOCK; the network layer must stay silent to avoid "
        "double-reporting the same event at two severities."
    )


ALL_FIXTURES = {
    "N1_SUPPRESSION_NETWORK": suppression_network,
    "N2_FLOODING_NETWORK": flooding_network,
    "N3_LAUNDERING_NETWORK": laundering_network,
    "N4_CLEAN_MULTI_MERCHANT": clean_multi_merchant,
    "N5_SINGLE_MERCHANT_DEDUP": clean_single_merchant_owns_it,
}
