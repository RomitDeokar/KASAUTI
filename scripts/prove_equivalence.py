#!/usr/bin/env python3
"""
Show the certified-but-drifted gap, and prove KASAUTI does not have it.

    python scripts/prove_equivalence.py

Runs offline. No API key, no network, no LLM.

WHAT THIS DEMONSTRATES
----------------------
Razorpay's guardrails blog makes two separate promises:

  s8  agents are CERTIFIED before publication, including "automated screening
      for communication patterns that could constitute dark patterns"
  s4  every agent action is VALIDATED inline, "before execution"

Both are real. Nothing published shows that the two agree with each other,
and in every other regulated industry the gap between an offline certification
and an online control has a name: certified-but-drifted. An agent passes the
screen, ships, and behaves differently in production -- not maliciously, but
because two systems were built from two readings of one spec.

KASAUTI closes that gap structurally rather than by testing harder: there is
exactly ONE set of predicates, and the offline engine and the inline gate are
two evaluation strategies over the same functions. This script replays the
corpus through both and reports every disagreement. tests/test_equivalence.py
asserts the same property so it fails CI rather than just printing badly.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from corpus.builder import build_corpus  # noqa: E402
from kasauti.adversary import mutate_offline  # noqa: E402
from kasauti.engine import judge  # noqa: E402
from kasauti.gateway import Mode, run_gate  # noqa: E402
from kasauti.schema import Severity  # noqa: E402

RESET, BOLD, RED, GREEN, YELLOW, DIM = (
    "\033[0m", "\033[1m", "\033[31m", "\033[32m", "\033[33m", "\033[2m"
)


def main() -> int:
    corpus = build_corpus() + mutate_offline(60)

    print()
    print(f"{BOLD}EQUIVALENCE PROOF{RESET} -- offline certification vs inline enforcement")
    print(f"{DIM}guardrails blog s8 (publish-time screen) vs s4 (runtime validation){RESET}")
    print("=" * 76)

    rule_mismatch = []
    turn_mismatch = []

    for t in corpus:
        offline = judge(t)
        inline = run_gate(t, mode=Mode.SHADOW)

        if offline.rules_fired != inline.rules_fired:
            rule_mismatch.append((t, offline.rules_fired, inline.rules_fired))
            continue

        off_blocking = {
            (f.turn_idx, f.rule_id) for f in offline.findings
            if f.severity is Severity.BLOCK
        }
        inl_blocking = {
            (d.turn_idx, f.rule_id) for d in inline.decisions
            for f in d.findings if f.severity is Severity.BLOCK
        }
        if off_blocking != inl_blocking:
            turn_mismatch.append((t, sorted(off_blocking), sorted(inl_blocking)))

    n = len(corpus)
    print(f"\ntranscripts replayed through both paths : {n}")
    print(f"  rule-set disagreements                : "
          f"{RED if rule_mismatch else GREEN}{len(rule_mismatch)}{RESET}")
    print(f"  same-rules-different-turn-blamed      : "
          f"{RED if turn_mismatch else GREEN}{len(turn_mismatch)}{RESET}")

    for t, off, inl in rule_mismatch[:5]:
        print(f"\n  {RED}DRIFT{RESET} {t.transcript_id}")
        print(f"    offline screen fired : {off}")
        print(f"    inline gate fired    : {inl}")
    for t, off, inl in turn_mismatch[:5]:
        print(f"\n  {RED}MISATTRIBUTED{RESET} {t.transcript_id}")
        print(f"    offline blamed : {off}")
        print(f"    inline blamed  : {inl}")

    # ---- the case that makes the property non-trivial ----
    print("\n" + "-" * 76)
    print("WHY THIS IS NOT TRIVIALLY TRUE")
    print("-" * 76)
    print(f"{DIM}The gate does not re-run the offline engine on the whole{RESET}")
    print(f"{DIM}transcript. It judges each turn against only the prefix that{RESET}")
    print(f"{DIM}preceded it -- which is genuinely harder for every stateful rule.{RESET}")
    print()

    demo = next(t for t in corpus if t.transcript_id == "MEDIANAMA_DEMO")
    log = run_gate(demo, mode=Mode.SHADOW)
    print(f"{BOLD}Exhibit: Razorpay's own launch demo, judged turn by turn{RESET}")
    print(f"{DIM}(behaviour as reported by MediaNama, 18 Mar 2026){RESET}\n")
    for d in log.decisions:
        turn = next(x for x in demo.turns if x.idx == d.turn_idx)
        who = turn.actor.value
        mark = f"{RED}DENY {RESET}" if d.denied else f"{GREEN}ALLOW{RESET}"
        offer = f" [{turn.offer.discount_pct:g}% off]" if turn.offer else ""
        print(f"  turn {d.turn_idx}  {mark}  {who:8s}{offer}")
        if d.denied:
            for f in d.findings:
                print(f"           {YELLOW}[{f.rule_id}]{RESET} {f.evidence[:96]}")

    print(f"\n{DIM}ESCALATING_PRESSURE at turn 2 is the load-bearing case: 20% off{RESET}")
    print(f"{DIM}is perfectly legal until you know the customer refused 10% at{RESET}")
    print(f"{DIM}turn 1. An inline gate that inspects only the action in front{RESET}")
    print(f"{DIM}of it -- the obvious implementation -- cannot catch it at all.{RESET}")

    ok = not rule_mismatch and not turn_mismatch
    print("\n" + "=" * 76)
    if ok:
        print(f"{GREEN}{BOLD}PROVEN{RESET} on {n} transcripts: an agent cannot pass "
              f"certification and\n       fail enforcement, or vice versa. Same "
              f"predicates, two strategies.")
    else:
        print(f"{RED}{BOLD}DRIFT DETECTED{RESET} -- see above.")

    print()
    print(f"{DIM}SCOPE: proven in SHADOW mode. In ENFORCE mode a blocked action{RESET}")
    print(f"{DIM}never executes, so later turns are counterfactual and there is{RESET}")
    print(f"{DIM}no offline verdict to compare against. That limit is asserted in{RESET}")
    print(f"{DIM}test_enforce_mode_diverges_by_design and stated in NOT_CHECKED.md.{RESET}")
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
