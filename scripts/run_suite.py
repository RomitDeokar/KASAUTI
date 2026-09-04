#!/usr/bin/env python3
"""KASAUTI runner: build corpus, judge it, emit scorecard + metrics.

  python scripts/run_suite.py            # offline, deterministic
  python scripts/run_suite.py --live 10  # + live Gemini adversary
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from corpus.builder import build_corpus  # noqa: E402
from corpus.history import HISTORIES  # noqa: E402
from kasauti.adversary import mutate_offline  # noqa: E402
from kasauti.crossepisode import ALL_CROSS_CHECKERS, judge_history  # noqa: E402
from kasauti.engine import assert_no_llm, judge, score_corpus, verdict_hash  # noqa: E402

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adversary", type=int, default=60)
    ap.add_argument("--live", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20260905)
    args = ap.parse_args()

    print("=" * 72)
    print("KASAUTI - conformance suite for money-moving AI agents")
    print("=" * 72)

    # The load-bearing guarantee, checked before anything is judged.
    assert_no_llm()
    print("[ok] purity guard: no checker can reach an LLM, network, clock or RNG")

    transcripts = build_corpus()
    print(f"[ok] handwritten + hard negatives: {len(transcripts)}")

    adv = mutate_offline(args.adversary, seed=args.seed)
    transcripts += adv
    print(f"[ok] offline adversary (seed={args.seed}): {len(adv)}")

    if args.live:
        from kasauti.adversary import generate_live
        try:
            live = generate_live(args.live)
            transcripts += live
            print(f"[ok] live Gemini adversary: {len(live)}")
        except RuntimeError as e:
            print(f"[skip] live adversary: {e}")

    metrics = score_corpus(transcripts)

    os.makedirs(ART, exist_ok=True)
    rows = []
    for t in transcripts:
        v = judge(t)
        rows.append({
            "transcript_id": t.transcript_id,
            "origin": t.origin,
            "expected": sorted(t.expected_violations),
            "fired": v.rules_fired,
            "passed": v.passed,
            "exact": sorted(t.expected_violations) == v.rules_fired,
            "verdict_hash": verdict_hash(v),
            "findings": [
                {"rule": f.rule_id, "turn": f.turn_idx,
                 "citation": f.citation, "evidence": f.evidence}
                for f in v.findings
            ],
            "notes": t.notes,
        })

    with open(os.path.join(ART, "verdicts.json"), "w") as fh:
        json.dump(rows, fh, indent=2)
    with open(os.path.join(ART, "metrics.json"), "w") as fh:
        json.dump(metrics, fh, indent=2)

    # ---- report ----
    m = metrics["micro"]
    print("\n" + "-" * 72)
    print("METRICS (per-origin breakdown kept separate on purpose)")
    print("-" * 72)
    print(f"transcripts        : {metrics['n_transcripts']}")
    print(f"origins            : {metrics['origins']}")
    print(f"micro precision    : {m['precision']:.3f}" if m["precision"] is not None else "n/a")
    print(f"micro recall       : {m['recall']:.3f}" if m["recall"] is not None else "n/a")
    print(f"exact-match rate   : {metrics['exact_match_rate']:.3f}")
    print(f"clean transcripts  : {metrics['clean_transcripts']}")
    print(f"  wrongly blocked  : {metrics['clean_wrongly_blocked']}"
          f"  (FP rate on clean: {metrics['false_positive_rate_on_clean']:.3f})")

    print("\nper-rule:")
    print(f"  {'rule':22s} {'tp':>4s} {'fp':>4s} {'fn':>4s} {'prec':>7s} {'rec':>7s}")
    for rid, r in metrics["per_rule"].items():
        p = f"{r['precision']:.3f}" if r["precision"] is not None else "  -  "
        rc = f"{r['recall']:.3f}" if r["recall"] is not None else "  -  "
        print(f"  {rid:22s} {r['tp']:4d} {r['fp']:4d} {r['fn']:4d} {p:>7s} {rc:>7s}")

    # -----------------------------------------------------------------------
    # ZERO-SUPPORT GUARD  (FAILURES.md #7)
    # -----------------------------------------------------------------------
    # INJECTED_INSTRUCTION once shipped with tp=fp=fn=0 -- a rule advertised in
    # the README, demoed in failure_lab.py, and never actually scored, because
    # no corpus transcript exercised it. A dash in a metrics table is not a
    # measurement, and it looked identical to a real result at a glance.
    #
    # An unmeasured rule is now a BUILD FAILURE rather than a formatting
    # artifact. This is the same defect class as FAILURES.md #6 (a component
    # that appeared to work because another component was doing the work), so
    # it gets a machine check rather than a promise to be careful.
    unsupported = [
        rid for rid, r in metrics["per_rule"].items()
        if (r["tp"] + r["fn"]) == 0
    ]
    if unsupported:
        print(f"\n[FAIL] rules with zero corpus support: {unsupported}")
        print("       A rule with no labelled positives is untested, not "
              "perfect. Add corpus coverage or delete the rule.")
        return 1
    print(f"\n[ok] zero-support guard: all {len(metrics['per_rule'])} rules "
          f"have labelled positives")

    # ---- the exhibit ----
    exhibit = next(r for r in rows if r["transcript_id"] == "MEDIANAMA_DEMO")
    print("\n" + "=" * 72)
    print("EXHIBIT A - Razorpay's own Agent Studio launch demo")
    print("  (behaviour as reported by MediaNama, 18 Mar 2026, ss3-4)")
    print("=" * 72)
    print(f"verdict: {'PASS' if exhibit['passed'] else 'BLOCKED'}   "
          f"hash={exhibit['verdict_hash']}")
    for f in exhibit["findings"]:
        print(f"\n  [{f['rule']}] turn {f['turn']}")
        print(f"     {f['citation']}")
        print(f"     {f['evidence']}")

    # -----------------------------------------------------------------------
    # CROSS-EPISODE LAYER
    # -----------------------------------------------------------------------
    # This block is here because it was NOT here (FAILURES.md #7). run_suite.py
    # imported judge_history and HISTORIES and used neither -- while
    # corpus/history.py stated in its module docstring that "scripts/
    # run_suite.py asserts exactly that property ... so the claim is
    # machine-checked rather than a comment."
    #
    # It was a comment. In a project whose entire argument is that claims must
    # be executable, a docstring asserting a check that does not run is the
    # worst possible bug to ship. The imports were the only evidence it was
    # ever intended.
    print("\n" + "-" * 72)
    print("CROSS-EPISODE LAYER (the shapes one transcript cannot show)")
    print("-" * 72)

    cross_exact = 0
    for name, (episodes, expected) in HISTORIES.items():
        # THE LOAD-BEARING ASSERTION: every episode must be individually
        # clean. If any single episode were dirty, the per-episode engine
        # would already have caught it and the cross-episode rule would be
        # decoration claiming an insight it had not earned.
        for ep in episodes:
            ev = judge(ep.transcript)
            assert ev.passed and not ev.rules_fired, (
                f"{name}: episode {ep.transcript.transcript_id} is dirty "
                f"per-episode ({ev.rules_fired}) -- the cross-episode finding "
                f"would be redundant, not novel"
            )

        cv = judge_history(episodes)
        got = tuple(cv.rules_fired)
        ok = got == tuple(sorted(expected))
        cross_exact += ok
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {name:24s} {len(episodes)} clean episodes "
              f"-> {list(got) or 'CLEAN'}")
        if not ok:
            print(f"         expected {sorted(expected)}")

    print("\n  every episode above passes the per-episode engine "
          "individually (asserted, not claimed)")
    print(f"  cross-episode exact match: {cross_exact}/{len(HISTORIES)}")
    print(f"  cross-episode rules: {len(ALL_CROSS_CHECKERS)} checkers, "
          f"aggregate scope")

    if cross_exact != len(HISTORIES):
        print("\n[FAIL] cross-episode layer disagrees with its labels")
        return 1

    metrics["cross_episode"] = {
        "n_histories": len(HISTORIES),
        "exact_match": cross_exact,
        "every_episode_individually_clean": True,
        "n_checkers": len(ALL_CROSS_CHECKERS),
    }
    with open(os.path.join(ART, "metrics.json"), "w") as fh:
        json.dump(metrics, fh, indent=2)

    mismatches = [r for r in rows if not r["exact"]]
    if mismatches:
        print("\n" + "-" * 72)
        print(f"LABEL MISMATCHES ({len(mismatches)}) - reported, not hidden")
        print("-" * 72)
        for r in mismatches[:15]:
            print(f"  {r['transcript_id']:44s} want={r['expected']} got={r['fired']}")
        if len(mismatches) > 15:
            print(f"  ... and {len(mismatches)-15} more (see artifacts/verdicts.json)")

    print("\nwrote artifacts/verdicts.json and artifacts/metrics.json")
    by_origin = Counter(r["origin"] for r in rows if not r["exact"])
    print(f"mismatches by origin: {dict(by_origin) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
