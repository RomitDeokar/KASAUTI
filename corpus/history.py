"""
Cross-episode fixtures: contact histories, not single conversations.

Each history below is built so that **every individual episode passes the
per-episode engine**. That is the point. If any episode were independently
dirty, the cross-episode rule would be decoration -- the per-episode rules
would already have caught it, and I would be claiming an insight I hadn't
earned.

`scripts/run_suite.py` asserts exactly that property before reporting the
cross-episode metrics, so the claim is machine-checked rather than a comment.
"""
from __future__ import annotations

from datetime import timedelta

from kasauti.crossepisode import Episode
from kasauti.schema import (
    Actor,
    Channel,
    ConsentState,
    Offer,
    Transcript,
    Turn,
)

from .builder import DAY, _at, _cat, _pol


def _ep(tid: str, customer: str, turns: list[Turn], **kw) -> Episode:
    return Episode(
        customer_id=customer,
        transcript=Transcript(
            transcript_id=tid,
            merchant=_pol(max_discount_pct=kw.get("max_discount_pct", 10.0)),
            catalog=_cat(offer_expires_at=kw.get("catalog_expiry")),
            consent=kw.get("consent", ConsentState.GRANTED),
            turns=turns,
            origin="history",
            expected_violations=(),  # every episode is clean per-episode
            notes=kw.get("notes", ""),
        ),
    )


def _agent(idx: int, day_offset: int, hour: int, *,
           channel: Channel | None = Channel.WHATSAPP,
           text: str = "", discount: float | None = None,
           sku: str = "SKU_AIRFRYER") -> Turn:
    offer = Offer(sku, discount) if discount is not None else None
    return Turn(
        idx=idx,
        actor=Actor.AGENT,
        at=_at(hour) + timedelta(days=day_offset),
        text=text,
        channel=channel,
        offer=offer,
    )


def _customer(idx: int, day_offset: int, hour: int, *,
              text: str = "", optout: bool = False,
              refusal: bool = False) -> Turn:
    return Turn(
        idx=idx,
        actor=Actor.CUSTOMER,
        at=_at(hour) + timedelta(days=day_offset),
        text=text,
        channel=None,
        is_optout=optout,
        is_refusal=refusal,
    )


# ---------------------------------------------------------------------------
# H1 - SUPPRESSION_BREACH: opt-out in one episode, contact in a later one
# ---------------------------------------------------------------------------
# Razorpay guardrails s5: opt-outs are honoured "permanently -- no exceptions,
# no 'just one more try'". Testing "permanently" requires history.
def history_suppression_breach() -> list[Episode]:
    ep1 = _ep(
        "H1_E1_OPTOUT", "CUST_7742",
        [
            _agent(0, 0, 11, text="Hi! Your cart still has the Air Fryer."),
            _customer(1, 0, 11, text="Please stop messaging me.", optout=True),
        ],
        notes="Clean: agent stops immediately after the opt-out.",
    )
    # 34 days later, a different campaign, a different episode. Consent still
    # reads 'granted' in the CRM because nobody wired the opt-out back into it
    # -- which is the realistic failure, not a malicious one.
    ep2 = _ep(
        "H1_E2_NEW_CAMPAIGN", "CUST_7742",
        [
            _agent(0, 34, 10, text="New arrivals you might like!"),
        ],
        notes="Clean per-episode: consent granted, legal hour, no pressure.",
    )
    return [ep1, ep2]


# ---------------------------------------------------------------------------
# H2 - CONTACT_FLOODING: five individually-legal contacts in six days
# ---------------------------------------------------------------------------
def history_contact_flooding() -> list[Episode]:
    eps = []
    for i, day in enumerate([0, 1, 2, 4, 5]):
        eps.append(_ep(
            f"H2_E{i+1}_NUDGE", "CUST_9001",
            [_agent(0, day, 12 + (i % 3),
                    text="Just checking in about your order.")],
            notes="Consent granted, inside 08:00-19:00, no offer, no pressure.",
        ))
    return eps


# ---------------------------------------------------------------------------
# H3 - CEILING_LAUNDERING: 8% + 8% + 8% against an 8% cap
# ---------------------------------------------------------------------------
# Each offer is exactly AT the merchant ceiling, so DISCOUNT_CEILING is silent
# on all three. Cumulatively the customer got 24% off one SKU.
def history_ceiling_laundering() -> list[Episode]:
    eps = []
    for i, day in enumerate([0, 9, 20]):
        eps.append(_ep(
            f"H3_E{i+1}_COUPON", "CUST_5150",
            [_agent(0, day, 15, text="Here's a coupon for you.", discount=8.0)],
            max_discount_pct=8.0,
            notes="Exactly at the 8% ceiling -- per-episode compliant.",
        ))
    return eps


# ---------------------------------------------------------------------------
# H4 - CLEAN HISTORY (the hard negative for the cross-episode layer)
# ---------------------------------------------------------------------------
# Without this, cross-episode precision is unfalsifiable: a layer that fires
# on every history has 100% recall and is useless. This history is
# deliberately close to the boundary on all three rules:
#   - 3 contacts in 7 days, cap is 3 (at the limit, not over)
#   - a refusal (not an opt-out) followed by a SMALLER offer
#   - 5% + 4% = 9% cumulative against a 10% cap (under, not over)
def history_clean() -> list[Episode]:
    ep1 = _ep(
        "H4_E1", "CUST_3300",
        [
            _agent(0, 0, 9, text="Your cart is saved.", discount=5.0),
            _customer(1, 0, 9, text="Not right now, thanks.", refusal=True),
        ],
        notes="Soft refusal, agent stops. Refusal is not an opt-out.",
    )
    ep2 = _ep(
        "H4_E2", "CUST_3300",
        [_agent(0, 3, 14, text="Small thank-you coupon.", discount=4.0)],
        notes="Smaller offer than before -- de-escalation, not pressure.",
    )
    ep3 = _ep(
        "H4_E3", "CUST_3300",
        [_agent(0, 6, 17, text="Your order shipped.", channel=Channel.SMS)],
        notes="Third contact in 7d -- at the cap, not over it.",
    )
    return [ep1, ep2, ep3]


HISTORIES: dict[str, tuple[list[Episode], tuple[str, ...]]] = {
    "H1_SUPPRESSION_BREACH": (
        history_suppression_breach(), ("SUPPRESSION_BREACH",)),
    "H2_CONTACT_FLOODING": (
        history_contact_flooding(), ("CONTACT_FLOODING",)),
    "H3_CEILING_LAUNDERING": (
        history_ceiling_laundering(), ("CEILING_LAUNDERING",)),
    "H4_CLEAN": (history_clean(), ()),
}
