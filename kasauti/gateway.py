"""
The inline enforcement point -- KASAUTI's rules applied *before* an action runs.

WHY THIS MODULE EXISTS
----------------------
Read Razorpay's guardrails blog closely and there are two separate promises:

  s8  CERTIFICATION. Every agent published to the marketplace is screened,
      including "automated screening for communication patterns that could
      constitute dark patterns". This happens once, at publish time.

  s4  RUNTIME VALIDATION. "Every action passes through Razorpay's
      platform-level validation layer before execution" -- compliance
      boundaries, amount validation, scope checks.

Both are real and both are sensible. But they are *different systems asked
different questions at different times*, and nothing in the post establishes
that they agree with each other.

That gap has a name in every other regulated industry: **certified-but-drifted.**
An agent passes certification on a corpus, ships, and then behaves differently
in production -- not because anyone was malicious, but because the offline
screen and the inline gate were built by different people from different
specs, and the divergence is invisible until it costs money.

The gap is structural, not hypothetical:
  - the offline screen sees whole conversations, after the fact
  - the inline gate sees one action at a time, before the fact
  - a rule that needs conversational history (ESCALATING_PRESSURE needs to
    know a refusal happened) is *easy* offline and *easy to get wrong* inline

So KASAUTI does not implement a second rule set. There is exactly one set of
predicates -- `kasauti.rules.checkers.ALL_CHECKERS` -- and this module is a
different *evaluation strategy* over the same functions. Certification and
enforcement cannot drift because they are the same code.

`tests/test_equivalence.py` proves that claim on every transcript in the
corpus rather than asserting it here.

WHAT THIS IS NOT
----------------
This is not a claim to have reimplemented Razorpay's validation layer, and it
is not a "policy firewall" product. It is the missing *equivalence proof*
between two things Razorpay already ships separately. The novel artifact is
the proof, not the gate.

Honest limitation, stated up front rather than buried: in ENFORCE mode the
gate changes history. If it blocks turn 3, turn 3 never happened, so turns
4..n are counterfactual and no longer comparable to the offline verdict on the
original transcript. Equivalence is therefore proven in SHADOW mode, where
every turn is replayed and decisions are recorded but not applied. That is a
real caveat about what the proof covers, and it is why the two modes exist.
See NOT_CHECKED.md.

PURITY
------
Same contract as everything else in this package: no LLM, no network, no
clock, no RNG. `tests/test_purity.py` runs the guard over this module's
decision path too, so the gate is held to the standard the checkers are.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .rules.checkers import ALL_CHECKERS
from .schema import Finding, Severity, Transcript, Turn


class Mode(str, Enum):
    """How the gate treats its own decisions.

    SHADOW   evaluate every turn, record the decision, apply nothing. This is
             the mode the equivalence proof runs in, because it preserves the
             transcript exactly as the offline engine sees it.
    ENFORCE  deny the offending action. The action does not execute, so the
             conversation genuinely diverges from the recorded one.
    """
    SHADOW = "shadow"
    ENFORCE = "enforce"


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class GateDecision:
    """One admission decision about one attempted action."""
    turn_idx: int
    decision: Decision
    findings: tuple[Finding, ...] = ()

    @property
    def denied(self) -> bool:
        return self.decision is Decision.DENY

    def reason(self) -> str:
        """A denial reason a merchant can read, not a stack trace.

        The rubric line is "every money action explainable". An enforcement
        point that says DENIED without saying why is operationally useless --
        the merchant cannot tell a bug from a correct block, so they turn the
        gate off.
        """
        if not self.findings:
            return "allowed"
        return " | ".join(
            f"[{f.rule_id}] {f.evidence} (cite: {f.citation})"
            for f in self.findings
        )


@dataclass
class GateLog:
    """The audit trail. Ordered, hashable, and the same shape as the offline
    verdict so the two can be compared field by field."""
    mode: Mode
    decisions: list[GateDecision] = field(default_factory=list)

    @property
    def denied_turns(self) -> list[int]:
        return [d.turn_idx for d in self.decisions if d.denied]

    @property
    def rules_fired(self) -> list[str]:
        return sorted({
            f.rule_id for d in self.decisions for f in d.findings
        })

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "denied_turns": self.denied_turns,
            "rules_fired": self.rules_fired,
            "decisions": [
                {
                    "turn_idx": d.turn_idx,
                    "decision": d.decision.value,
                    "reason": d.reason(),
                }
                for d in self.decisions
            ],
        }


def _findings_on_prefix(t: Transcript, upto_idx: int) -> list[Finding]:
    """Run every checker on the conversation *as it existed* through upto_idx.

    This is the whole trick, and it is deliberately boring: rather than
    writing incremental versions of eight stateful rules (which is exactly
    where certification and enforcement drift apart in practice), the gate
    re-runs the unmodified offline checkers on a truncated transcript.

    It costs O(n^2) on an n-turn conversation. For agent conversations -- a
    handful of turns, occasionally dozens -- that is free, and it buys a
    property worth far more than the cycles: there is no second
    implementation of any rule, so there is no second implementation to be
    wrong. Correct and provably identical beats clever.
    """
    prefix = Transcript(
        transcript_id=t.transcript_id,
        merchant=t.merchant,
        catalog=t.catalog,
        consent=t.consent,
        turns=[x for x in t.turns if x.idx <= upto_idx],
        origin=t.origin,
        expected_violations=t.expected_violations,
        notes=t.notes,
        meta=t.meta,
    )
    out: list[Finding] = []
    for fn in ALL_CHECKERS:
        out.extend(fn(prefix))
    return out


def evaluate_action(t: Transcript, turn: Turn) -> GateDecision:
    """Would this action be admitted, given everything before it?

    A turn is denied when it *introduces* a finding at its own index. A
    finding attributed to an earlier turn is already-spilt milk: it must not
    deny the current action, or one early violation would block the rest of
    the conversation forever and the gate's precision would collapse.
    """
    new = [f for f in _findings_on_prefix(t, turn.idx) if f.turn_idx == turn.idx]
    blocking = [f for f in new if f.severity is Severity.BLOCK]
    if blocking:
        return GateDecision(
            turn_idx=turn.idx,
            decision=Decision.DENY,
            findings=tuple(sorted(blocking, key=lambda f: f.rule_id)),
        )
    return GateDecision(
        turn_idx=turn.idx,
        decision=Decision.ALLOW,
        findings=tuple(sorted(new, key=lambda f: f.rule_id)),  # WARNs
    )


def run_gate(t: Transcript, mode: Mode = Mode.SHADOW) -> GateLog:
    """Replay a transcript through the inline gate, turn by turn.

    In SHADOW mode every turn is evaluated regardless of earlier denials, so
    the log is directly comparable with the offline verdict on the same
    transcript. In ENFORCE mode a denied action is dropped from the history
    the gate carries forward -- because in production it never happened.
    """
    log = GateLog(mode=mode)
    admitted: list[Turn] = []

    for turn in sorted(t.turns, key=lambda x: x.idx):
        if mode is Mode.ENFORCE:
            # Judge against the history that actually occurred: admitted
            # turns only, plus the one being attempted.
            view = Transcript(
                transcript_id=t.transcript_id,
                merchant=t.merchant,
                catalog=t.catalog,
                consent=t.consent,
                turns=admitted + [turn],
                origin=t.origin,
                expected_violations=t.expected_violations,
                notes=t.notes,
                meta=t.meta,
            )
            d = evaluate_action(view, turn)
        else:
            d = evaluate_action(t, turn)

        log.decisions.append(d)
        if not d.denied:
            admitted.append(turn)

    return log
