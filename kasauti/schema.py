"""
KASAUTI event-log schema.

Everything KASAUTI judges is a *transcript*: an ordered log of what an agent
did, plus the merchant's first-party ground truth at the time it acted.

Design rule that the whole project rests on:
    The schema is the contract. Checkers read ONLY the schema.
    No checker is allowed to call an LLM. See kasauti/engine.py::assert_no_llm.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Channel(str, Enum):
    VOICE = "voice"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class Actor(str, Enum):
    AGENT = "agent"
    CUSTOMER = "customer"
    SYSTEM = "system"


class ConsentState(str, Enum):
    GRANTED = "granted"
    NEVER_GIVEN = "never_given"
    REVOKED = "revoked"


class Provenance(str, Enum):
    """Who authored the text an agent acted on.

    This enum is the whole of KASAUTI's prompt-injection defence, and the
    reason that defence is a *capability* check rather than a blocklist.

    Razorpay guardrails blog s3 says agents work with "verified first-party
    data ... not from web scraping, external inference, or unverified
    sources". The blog states the boundary. It does not give anyone a way to
    TEST that an agent respected it. Provenance does:

        MERCHANT_CONFIG  the merchant's own catalog/policy. Authoritative.
        FIRST_PARTY      merchant-connected systems (Shopify, Shiprocket).
        CUSTOMER         what the customer said in this conversation.
        UNTRUSTED        anything a third party can write: product reviews,
                         seller-supplied descriptions, scraped pages, an
                         inbound buyer-agent's free-text notes.

    The rule (see rules/checkers.py::check_injected_instruction) is not
    "does this text look like an attack". It is: *did a money-moving decision
    cite UNTRUSTED content as its authority?* A blocklist of phrases like
    "ignore previous instructions" loses to paraphrase. Provenance does not,
    because it never reads the attack at all -- it reads where the authority
    came from.

    Same lesson as FAILURES.md #1: reason about capability, not about strings.
    """
    MERCHANT_CONFIG = "merchant_config"
    FIRST_PARTY = "first_party"
    CUSTOMER = "customer"
    UNTRUSTED = "untrusted"

    @property
    def is_authoritative(self) -> bool:
        """May content of this provenance authorise a money-moving action?"""
        return self in (Provenance.MERCHANT_CONFIG, Provenance.FIRST_PARTY)


@dataclass(frozen=True)
class CatalogItem:
    """First-party ground truth. Razorpay guardrails blog s3: agents work with
    verified first-party data, not external inference."""
    sku: str
    name: str
    price_paise: int
    in_stock: bool
    # None => merchant has configured NO time-bound offer on this SKU.
    offer_expires_at: datetime | None = None
    mrp_paise: int | None = None


@dataclass(frozen=True)
class MerchantPolicy:
    """The merchant's own configured ceilings. Razorpay guardrails blog s2:
    'If a merchant has configured a maximum 10% discount for cart recovery,
    no agent will offer 15%. The ceiling is the merchant's ceiling.'"""
    merchant_id: str
    max_discount_pct: float
    # RBI recovery-agent directions: contact window in customer local time.
    contact_window_start_hour: int = 8
    contact_window_end_hour: int = 19
    allowed_channels: tuple[Channel, ...] = (
        Channel.WHATSAPP,
        Channel.VOICE,
        Channel.EMAIL,
        Channel.SMS,
    )


@dataclass(frozen=True)
class Offer:
    """A structured offer attached to an agent turn. Structured, not parsed out
    of prose -- so DISCOUNT_CEILING and ESCALATING_PRESSURE are exact, not fuzzy."""
    sku: str
    discount_pct: float
    # What the agent *told the customer* about expiry (its claim).
    claimed_expires_at: datetime | None = None


@dataclass(frozen=True)
class Turn:
    """One event in the conversation."""
    idx: int
    actor: Actor
    at: datetime                      # customer local time
    text: str = ""
    channel: Channel | None = None
    offer: Offer | None = None
    # Customer-side signals, set by the harness, never inferred by a checker.
    is_refusal: bool = False          # "no thanks", "not interested"
    is_optout: bool = False           # "stop", "do not contact me"
    # Numeric claims the agent made in prose, extracted by the *harness*
    # (an LLM may do this) and then judged by deterministic code.
    price_claims_paise: tuple[int, ...] = ()
    stock_claims: tuple[tuple[str, bool], ...] = ()

    # --- Provenance / injection defence -----------------------------------
    # Content the agent read before producing this turn, as
    # (provenance, snippet) pairs. Populated by the harness from the agent's
    # actual tool-call returns -- never guessed by a checker.
    context_sources: tuple[tuple[Provenance, str], ...] = ()
    # Which of those sources the agent CITED as the basis for this turn's
    # action. An agent that reads a poisoned review is careless; an agent that
    # justifies a 100% discount *by* that review has been captured. Only the
    # second is a rule violation, and this field is what distinguishes them.
    action_authority: Provenance | None = None
    # Defaults keep every pre-existing corpus case and all 23 v1 tests valid:
    # a turn that declares no sources asserts nothing about provenance, so the
    # injection rule stays silent rather than inventing a verdict.


@dataclass
class Transcript:
    """A complete agent episode plus the ground truth it should have obeyed."""
    transcript_id: str
    merchant: MerchantPolicy
    catalog: dict[str, CatalogItem]
    consent: ConsentState
    turns: list[Turn]
    # Provenance -- which generator made this, and the label we expect.
    origin: str = "unknown"
    expected_violations: tuple[str, ...] = ()
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def agent_turns(self) -> list[Turn]:
        return [t for t in self.turns if t.actor is Actor.AGENT]

    def outbound_turns(self) -> list[Turn]:
        """Agent turns that actually contacted the customer."""
        return [t for t in self.agent_turns() if t.channel is not None]


class Severity(str, Enum):
    BLOCK = "block"    # money/contact must not proceed
    WARN = "warn"      # allowed but logged for merchant review


@dataclass(frozen=True)
class Finding:
    rule_id: str
    citation: str
    severity: Severity
    turn_idx: int
    evidence: str

    def key(self) -> str:
        return self.rule_id
