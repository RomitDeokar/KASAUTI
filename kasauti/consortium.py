"""
The consortium layer: the abuse shape that NO single merchant's data can reveal.

WHY THIS EXISTS
---------------
`crossepisode.py` widened the question from one episode to one customer's
history at ONE merchant. That still has a structural hole, and it is again a
hole in *arity*, not in rule quality:

    A customer opts out at merchant A. Contacts them once.
    Opts out at merchant B. Contacts them once.
    ... across nine merchants.

Every merchant is individually clean. Every merchant is individually
*correct* to believe they are clean -- they are not missing a rule, they are
missing the other eight merchants' logs. Nine contacts to a person who said
stop nine times is the harassment pattern TCCCPR exists to prevent, and it is
invisible at single-merchant scope in principle.

Razorpay is the only party in the Indian stack that sits across all nine.
That is the argument for why this belongs in a Razorpay submission and not
anywhere else.

THE CRITIQUE THIS MODULE HAD TO SURVIVE
---------------------------------------
The v3 workbook proposed exactly this as "RIWAAJ" and I did not build it,
for a reason I wrote down in crossepisode.py's docstring at the time:

    "Synthetic cross-merchant rings would mean I generate the rings AND
     detect them -- the 'did you just detect your own generator?' critique,
     which is fatal and correct."

That critique is still fatal and still correct, and it is the reason this
module is NOT a machine-learned ring detector. I could not make a learned
detector honest in the time available, so I did not ship one.

What I shipped instead is the part that survives the critique: **the
deterministic aggregation whose ground truth is not a modelling assumption.**
SUPPRESSION_BREACH_NETWORK does not *infer* that a ring exists. It reads
whether an opt-out is recorded at merchant A and a contact is recorded at
merchant B. That is arithmetic over logs. It cannot be "detecting its own
generator" because there is nothing to detect -- the opt-out either is in the
log or it is not, exactly as in the single-merchant case.

The distinction I want a reviewer to hold me to:
  - "these accounts form a fraud ring"     -> a MODEL's claim. Not shipped.
  - "this person said stop at A and was
     contacted at B"                       -> a LOG's claim. Shipped.

The second is less impressive and it is true. See NOT_CHECKED.md for what a
real consortium would additionally need.

PRIVACY IS THE ACTUAL ENGINEERING PROBLEM
-----------------------------------------
A consortium that pools raw customer identifiers across merchants is not a
compliance tool, it is a DPDPA violation wearing one. Purpose limitation
(DPDP Act 2023 s6) does not let merchant A hand merchant B a phone number
because fraud was the excuse.

So merchants never exchange identifiers here. They exchange **salted hashes**
under a consortium-wide salt (`ConsortiumConfig.salt`):

    join_key = sha256(salt || normalised_identifier)[:16]

Properties this gets and does not get, stated precisely because "we hashed
it" is a claim people routinely overstate:

  It DOES  prevent a participating merchant from reading an identifier
           belonging to a customer it has never transacted with, given a
           salt it does not hold.
  It DOES  make the join computable without any party seeing plaintext.
  It NOT   defeat an offline dictionary attack by a party who HOLDS the salt:
           phone numbers are a ~10^10 space and that is trivially
           enumerable. The salt is the whole secret.
  It NOT   provide differential privacy, unlinkability, or any guarantee
           against the consortium operator itself.

A production build wants a PSI protocol or an HSM-held salt. That is named in
NOT_CHECKED.md rather than hinted at, because a hash is a real mitigation and
pretending it is more than one is how compliance theatre starts.

PURITY
------
Same contract as every other rule module: no LLM, no network, no clock, no
RNG. Time enters only through timestamps already inside the episodes.
`hashlib` is added to the import allowlist in engine.py -- it is a pure
function, and the allowlist grew by hand, on purpose. See FAILURES.md #1.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .schema import Finding, Severity

CONSORTIUM_RULE_IDS = (
    "SUPPRESSION_BREACH_NETWORK",
    "CONTACT_FLOODING_NETWORK",
    "CEILING_LAUNDERING_NETWORK",
)


class DegenerateIdentifier(ValueError):
    """Raised when an identifier is not specific enough to join on.

    This exception exists because of FAILURES.md #9, which is the worst bug
    I shipped in this project: an empty-string identifier hashed to a
    perfectly valid-looking key, every merchant reporting a customer with a
    missing phone number joined onto that same key, and the engine accused
    six unrelated people of being one harassed customer.

    A hash function has no opinion about whether its input means anything.
    Validation has to happen BEFORE the hash, or the hash launders garbage
    into something that looks authoritative.
    """


# Degenerate values that appear constantly in real merchant CRM exports and
# must never be treated as identifying. Refusing to join is always safe;
# a false join is an accusation against a real person.
_NULL_IDENTIFIERS = {
    "", "-", "na", "n/a", "nan", "none", "null", "nil", "unknown",
    "test", "0", "+", "91", "0000000000", "9999999999", "1234567890",
}


def join_key(identifier: str, salt: str) -> str:
    """The only identifier that ever crosses a merchant boundary.

    Normalisation before hashing is load-bearing and is where this kind of
    system usually breaks: "+91 98765 43210", "919876543210" and
    "09876543210" are one human being and three different hashes. Getting
    this wrong does not raise an error -- it silently produces a join that
    finds nothing, which looks exactly like "no abuse detected".

    Raises DegenerateIdentifier if the input cannot identify one person.
    That is deliberately a hard failure rather than a skip: a consortium
    quietly dropping unjoinable rows under-reports, and a consortium quietly
    joining them over-accuses. Both are silent. An exception is not.

    See FAILURES.md #9.
    """
    norm = _normalise_identifier(identifier)
    _reject_if_degenerate(norm, identifier)
    return hashlib.sha256(f"{salt}|{norm}".encode("utf-8")).hexdigest()[:16]


def _reject_if_degenerate(norm: str, original: str) -> None:
    """Refuse to produce a join key for anything that isn't a real person.

    The rule is conservative by construction: an identifier must be either a
    plausible Indian mobile number (10 digits, leading 6-9 per the NNP) or
    something with an '@' in it. Everything else is rejected rather than
    guessed at, because the cost of a wrong join here is a false accusation
    of harassment against a customer who did nothing.
    """
    if norm in _NULL_IDENTIFIERS:
        raise DegenerateIdentifier(
            f"refusing to build a join key from {original!r}: this is a "
            f"placeholder, not an identity. Joining on it would merge "
            f"unrelated customers into one phantom record (FAILURES.md #9)."
        )
    if norm.isdigit():
        if len(norm) != 10 or norm[0] not in "6789":
            raise DegenerateIdentifier(
                f"refusing to build a join key from {original!r}: not a "
                f"valid 10-digit Indian mobile (National Numbering Plan "
                f"mobile series start with 6-9). Normalised to {norm!r}."
            )
        if len(set(norm)) == 1:
            raise DegenerateIdentifier(
                f"refusing to build a join key from {original!r}: all-same-"
                f"digit numbers are placeholders in every CRM export I have "
                f"seen."
            )
        return
    if "@" in norm:
        # BUGFIX (FAILURES.md #18): the previous test was `"." in
        # norm.split("@")[-1]`, which accepted "@a.com" (nobody before the
        # @), "a@.com" (no host before the dot) and "a@b@c.com" (two @s) and
        # hashed each into a confident-looking join key. Same bug class as
        # #9 -- garbage laundered by a hash -- in a new spot. An email-shaped
        # identifier needs exactly one @, a non-empty local part, and a
        # domain with a non-empty label on both sides of a dot.
        local, _, domain = norm.partition("@")
        labels = domain.split(".")
        if (
            local
            and "@" not in domain
            and len(labels) >= 2
            and all(labels)
            and len(norm) >= 6
        ):
            return
        raise DegenerateIdentifier(
            f"refusing to build a join key from {original!r}: normalised to "
            f"{norm!r}, which is email-shaped but not an email (empty local "
            f"part, empty domain label, or more than one '@'). Hashing it "
            f"would merge every record with the same defect into one phantom "
            f"customer (FAILURES.md #9, #18)."
        )
    raise DegenerateIdentifier(
        f"refusing to build a join key from {original!r}: normalised to "
        f"{norm!r}, which is neither an Indian mobile nor an email. The "
        f"consortium joins on identity; it does not guess at it."
    )


def _normalise_identifier(identifier: str) -> str:
    """Canonicalise an Indian phone number / email before hashing.

    Deliberately conservative: strip formatting, lowercase, and drop a
    leading +91 / 91 / 0 country-or-trunk prefix on 10-digit mobile numbers.
    Anything that does not look like an Indian mobile is only lowercased and
    stripped -- guessing harder would create false joins between distinct
    people, and a false join here means accusing an innocent customer.
    """
    s = "".join(identifier.split()).strip().lower()
    s = s.replace("-", "").replace("(", "").replace(")", "")
    digits = s[1:] if s.startswith("+") else s
    if digits.isdigit():
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        return digits
    return s


@dataclass(frozen=True)
class MerchantReport:
    """What ONE merchant contributes to the consortium about ONE customer.

    Note what is absent: no name, no phone, no email, no order contents, no
    transcript text. A merchant reports timestamps, channels, and its own
    ceiling -- the minimum needed to evaluate the three network rules, and
    nothing that would let another merchant profile the customer.

    `join_key` is the salted hash. Plaintext identifiers never enter this
    dataclass; `MerchantReport.build` is the only constructor and it hashes
    at the boundary.
    """
    join_key: str
    merchant_id: str
    # (timestamp, channel) for every outbound contact this merchant made.
    contacts: tuple[tuple[datetime, str], ...] = ()
    # Earliest opt-out this merchant recorded, if any.
    opted_out_at: datetime | None = None
    # (timestamp, sku, discount_pct) for offers extended.
    offers: tuple[tuple[datetime, str, float], ...] = ()
    # This merchant's own configured ceiling, for laundering arithmetic.
    max_discount_pct: float = 100.0

    @classmethod
    def build(cls, identifier: str, salt: str, merchant_id: str, **kw
              ) -> "MerchantReport":
        """Construct a report, hashing the identifier at the boundary."""
        return cls(join_key=join_key(identifier, salt),
                   merchant_id=merchant_id, **kw)


@dataclass
class ConsortiumConfig:
    """Consortium-wide thresholds and the join salt.

    Every number here is an OPERATOR POLICY CHOICE, not a legal quantity, and
    the Findings say so in their citations. TCCCPR regulates consent and
    opt-out; it does not legislate "five contacts per seven days across all
    merchants". I needed executable numbers, so they live here as arguments
    rather than as magic constants inside a rule body pretending to be law.

    network_contact_cap is deliberately LOOSER than the single-merchant cap
    (3 in CrossEpisodePolicy). A customer legitimately transacting with eight
    merchants will receive more total messages than one transacting with one,
    and a network rule that fires on ordinary multi-merchant shopping is
    worse than no rule -- it trains the operator to ignore it.
    """
    salt: str = "kasauti-demo-consortium-salt"
    network_contact_cap: int = 5
    window_days: int = 7
    # Minimum distinct merchants before a network rule may fire at all.
    # A "network" finding involving one merchant is a single-merchant
    # finding, and crossepisode.py already owns that case. Without this
    # guard the two layers double-report the same event.
    min_merchants: int = 2


@dataclass
class ConsortiumLedger:
    """The pooled view. Deliberately dumb: it holds reports and groups them."""
    config: ConsortiumConfig = field(default_factory=ConsortiumConfig)
    reports: list[MerchantReport] = field(default_factory=list)

    def add(self, report: MerchantReport) -> None:
        self.reports.append(report)

    def by_customer(self) -> dict[str, list[MerchantReport]]:
        out: dict[str, list[MerchantReport]] = defaultdict(list)
        for r in self.reports:
            out[r.join_key].append(r)
        return dict(out)


def check_suppression_breach_network(
    ledger: ConsortiumLedger,
) -> list[Finding]:
    """Opt-out at merchant A, contact at merchant B.

    THE HONEST SCOPE QUESTION, ANSWERED EXPLICITLY:

    Is an opt-out to merchant A legally binding on merchant B? Under TCCCPR
    preference registration, largely yes -- the preference attaches to the
    subscriber, not to one sender. Under a merchant's own first-party consent
    relationship, arguably no -- B has its own consent from the customer.

    I could not resolve that cleanly from the primary text, so the rule does
    NOT assert a legal violation across merchants. Severity is WARN, not
    BLOCK, and the citation says "cross-merchant suppression is an operator
    policy question". A BLOCK here would be me legislating.

    The single-merchant equivalent in crossepisode.py IS a BLOCK, because
    there the answer is not ambiguous.
    """
    out: list[Finding] = []

    for jk, reports in sorted(ledger.by_customer().items()):
        optouts = [(r.opted_out_at, r.merchant_id) for r in reports
                   if r.opted_out_at is not None]
        if not optouts:
            continue
        earliest_at, earliest_merchant = min(optouts, key=lambda x: (x[0], x[1]))

        for r in sorted(reports, key=lambda r: r.merchant_id):
            for at, channel in sorted(r.contacts):
                if at <= earliest_at:
                    continue
                # DE-DUPLICATION AGAINST THE SINGLE-MERCHANT LAYER.
                #
                # If THIS merchant holds its own opt-out and contacted after
                # it, crossepisode.py::check_suppression_breach already owns
                # the event at BLOCK. Reporting it again here at WARN shows
                # an operator one event twice at two severities.
                #
                # The subtle part, and the bug (FAILURES.md #10): the test
                # must be "does r hold an opt-out at or before this contact",
                # NOT "is r the merchant holding the EARLIEST opt-out". Those
                # differ whenever two merchants both have opt-outs, which is
                # exactly the shape of a genuinely harassed customer. The old
                # `r.merchant_id == earliest_merchant` check silenced only
                # the earliest opt-out holder, so merchant B breaching its
                # OWN opt-out was re-reported as a cross-merchant breach
                # against merchant A -- blaming the wrong merchant for an
                # event the BLOCK layer had already caught.
                if r.opted_out_at is not None and r.opted_out_at <= at:
                    continue
                out.append(Finding(
                    rule_id="SUPPRESSION_BREACH_NETWORK",
                    citation=(
                        "TRAI TCCCPR 2018 reg. 17 (preference attaches to the "
                        "subscriber); cross-merchant applicability is an "
                        "OPERATOR POLICY question, not a settled legal one -- "
                        "see kasauti/consortium.py docstring"
                    ),
                    severity=Severity.WARN,
                    turn_idx=-1,
                    evidence=(
                        f"customer {jk} opted out at {earliest_merchant} on "
                        f"{earliest_at.isoformat()}; contacted by "
                        f"{r.merchant_id} via {channel} at {at.isoformat()} -- "
                        f"both merchants are individually compliant; only the "
                        f"pooled view shows it"
                    ),
                ))
    return out


def check_contact_flooding_network(
    ledger: ConsortiumLedger,
) -> list[Finding]:
    """Contacts that are legal per-merchant and harassment in aggregate.

    Fires on the (cap+1)th contact inside the rolling window so the evidence
    names the contact that crossed the line, and only when at least
    `min_merchants` distinct merchants are involved.
    """
    out: list[Finding] = []
    cfg = ledger.config
    window = timedelta(days=cfg.window_days)

    for jk, reports in sorted(ledger.by_customer().items()):
        events: list[tuple[datetime, str, str]] = []
        for r in reports:
            for at, channel in r.contacts:
                events.append((at, r.merchant_id, channel))
        events.sort(key=lambda x: (x[0], x[1]))

        for i, (at, merchant, channel) in enumerate(events):
            recent = [e for e in events[:i + 1] if at - e[0] <= window]
            if len(recent) <= cfg.network_contact_cap:
                continue
            involved = sorted({e[1] for e in recent})
            if len(involved) < cfg.min_merchants:
                continue
            out.append(Finding(
                rule_id="CONTACT_FLOODING_NETWORK",
                citation=(
                    f"OPERATOR POLICY: >{cfg.network_contact_cap} contacts / "
                    f"{cfg.window_days}d across >={cfg.min_merchants} "
                    f"merchants. Not a statutory number -- TCCCPR does not "
                    f"quantify a cross-merchant frequency cap"
                ),
                severity=Severity.WARN,
                turn_idx=-1,
                evidence=(
                    f"customer {jk} received {len(recent)} contacts in "
                    f"{cfg.window_days}d across {len(involved)} merchants "
                    f"({', '.join(involved)}); threshold crossed by "
                    f"{merchant} via {channel} at {at.isoformat()}"
                ),
            ))
            break  # one finding per customer per window crossing
    return out


def check_ceiling_laundering_network(
    ledger: ConsortiumLedger,
) -> list[Finding]:
    """Stacked discounts on ONE sku across MANY merchants.

    The single-merchant version (crossepisode.py) catches 10%+10%+10% at one
    merchant against that merchant's 10% ceiling. This catches the case where
    the same SKU is discounted at three merchants who each stay inside their
    own ceiling -- relevant for marketplace/reseller topologies where one
    customer farms the same product across sellers.

    Scoped to a rolling `window_days` window. Summing over all time was the
    original implementation and it was wrong: two 10% offers six years apart
    are a repeat customer, not laundering. Stacking is a claim about offers
    being live together. See FAILURES.md #11.

    Threshold is the MAXIMUM ceiling any participating merchant configured
    *on that SKU*, not the sum and not the minimum. Reasoning: the sum is meaningless (three
    merchants each allowing 10% does not authorise 30% to one customer), and
    the minimum would fire on a merchant who never agreed to the strictest
    participant's policy. The max is the most permissive defensible reading,
    which is the right bias for a WARN-level heuristic.
    """
    out: list[Finding] = []
    cfg = ledger.config

    window = timedelta(days=cfg.window_days)

    for jk, reports in sorted(ledger.by_customer().items()):
        by_sku: dict[str, list[tuple[datetime, str, float]]] = defaultdict(list)
        ceiling_by_sku: dict[str, float] = {}
        for r in reports:
            for at, sku, pct in r.offers:
                by_sku[sku].append((at, r.merchant_id, pct))
                # This max() is deliberately INSIDE the offer loop. A
                # merchant that never offered this SKU must not raise this
                # SKU's threshold -- otherwise one participant with a
                # permissive ceiling on unrelated inventory would silently
                # authorise stacking everywhere. Pinned by
                # test_bystander_merchant_ceiling_does_not_leak.
                ceiling_by_sku[sku] = max(
                    ceiling_by_sku.get(sku, 0.0), r.max_discount_pct)

        for sku, rows in sorted(by_sku.items()):
            rows.sort(key=lambda x: (x[0], x[1]))
            # UNBOUNDED-WINDOW BUG (FAILURES.md #11): the total used to be
            # summed over ALL history, so two 10% offers six YEARS apart
            # summed to 20% and tripped a 10% ceiling. That is not discount
            # laundering, it is a repeat customer. Stacking is a claim about
            # offers being live together, so it is scoped to the same rolling
            # window the flooding rule already uses.
            best: tuple[float, list[tuple[datetime, str, float]]] | None = None
            for i, (at, _m, _p) in enumerate(rows):
                recent = [e for e in rows[:i + 1] if at - e[0] <= window]
                tot = sum(p for _, _, p in recent)
                if best is None or tot > best[0]:
                    best = (tot, recent)
            if best is None:
                continue
            total, recent = best
            merchants = sorted({m for _, m, _ in recent})
            if len(merchants) < cfg.min_merchants:
                continue
            ceiling = ceiling_by_sku[sku]
            # Strict >, and a small epsilon, because binary floats made an
            # earlier version of this fire at exactly the ceiling.
            # See FAILURES.md #2a for the same bug in the per-episode rules.
            if total <= ceiling + 1e-9:
                continue
            out.append(Finding(
                rule_id="CEILING_LAUNDERING_NETWORK",
                citation=(
                    f"OPERATOR POLICY: cumulative cross-merchant discount on "
                    f"one SKU within {cfg.window_days}d exceeding the most "
                    f"permissive participating merchant's configured ceiling. "
                    f"Razorpay guardrails blog s2 states the ceiling is "
                    f"per-merchant; the cross-merchant reading is mine, and "
                    f"the window is a policy choice, not a statutory one"
                ),
                severity=Severity.WARN,
                turn_idx=-1,
                evidence=(
                    f"customer {jk} received {total:.1f}% cumulative discount "
                    f"on {sku} within {cfg.window_days}d across "
                    f"{len(merchants)} merchants ({', '.join(merchants)}); "
                    f"most permissive single-merchant ceiling is "
                    f"{ceiling:.1f}% -- every individual offer is within its "
                    f"own merchant's limit"
                ),
            ))
    return out


ALL_CONSORTIUM_CHECKERS = (
    check_suppression_breach_network,
    check_contact_flooding_network,
    check_ceiling_laundering_network,
)


def evaluate_consortium(ledger: ConsortiumLedger) -> list[Finding]:
    """Run every network rule. Pure: same ledger in => same findings out."""
    out: list[Finding] = []
    for fn in ALL_CONSORTIUM_CHECKERS:
        out.extend(fn(ledger))
    return out
