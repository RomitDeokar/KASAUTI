#!/usr/bin/env python3
"""Score KASAUTI against the approaches it claims to beat, same corpus, same labels.

  python scripts/compare_baselines.py         # lexical baseline (offline)
  python scripts/compare_baselines.py --llm   # + LLM-as-judge (needs GEMINI_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from corpus.builder import build_corpus  # noqa: E402
from kasauti.adversary import mutate_offline  # noqa: E402
from kasauti.baselines import lexical_judge, score_judge  # noqa: E402
from kasauti.engine import judge  # noqa: E402

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts")


def _fmt(x):
    return f"{x:.3f}" if isinstance(x, float) else " n/a "


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--adversary", type=int, default=60)
    ap.add_argument("--llm-sample", type=int, default=25,
                    help="cap live LLM calls to stay inside the free tier")
    args = ap.parse_args()

    corpus = build_corpus() + mutate_offline(args.adversary)
    results = {}

    results["kasauti"] = score_judge(corpus, lambda t: judge(t).rules_fired)
    results["lexical"] = score_judge(corpus, lexical_judge)

    if args.llm:
        from kasauti.baselines import llm_judge
        sample = corpus[: args.llm_sample]
        try:
            results["llm_judge"] = score_judge(sample, llm_judge)
            results["llm_judge"]["n_sampled"] = len(sample)
        except Exception as e:
            print(f"[skip] llm_judge unavailable: {e}")
    else:
        print("[info] LLM-as-judge baseline skipped (pass --llm with GEMINI_API_KEY)")

    print("\n" + "=" * 78)
    print("BASELINE COMPARISON - identical corpus, identical labels")
    print("=" * 78)
    print(f"corpus: {len(corpus)} transcripts "
          f"({sum(1 for t in corpus if not t.expected_violations)} clean)\n")
    hdr = f"{'judge':14s} {'prec':>7s} {'rec':>7s} {'exact':>7s} {'FP on clean':>13s}"
    print(hdr)
    print("-" * len(hdr))
    for name, r in results.items():
        print(f"{name:14s} {_fmt(r['precision']):>7s} {_fmt(r['recall']):>7s} "
              f"{_fmt(r['exact_match_rate']):>7s} "
              f"{r['clean_wrongly_blocked']:>3d}/{r['clean_transcripts']:<3d}"
              f" {_fmt(r['false_positive_rate_on_clean']):>6s}")

    print("\n" + "-" * 78)
    print("WHERE THE LEXICAL BASELINE BREAKS (this is the argument for KASAUTI)")
    print("-" * 78)
    shown = 0
    for d in results["lexical"]["disagreements"]:
        if d.get("spurious") and shown < 6:
            print(f"\n  {d['transcript_id']}  [{d.get('origin')}]")
            print(f"    baseline wrongly fired : {d['spurious']}")
            print(f"    truth                  : {d['expected'] or 'CLEAN'}")
            print(f"    why                    : {d['notes']}")
            shown += 1
    missed = [d for d in results["lexical"]["disagreements"] if d.get("missed")]
    if missed:
        print(f"\n  ...and {len(missed)} transcripts where it MISSED a real violation, e.g.:")
        for d in missed[:3]:
            print(f"    {d['transcript_id']:40s} missed={d['missed']}")

    os.makedirs(ART, exist_ok=True)
    with open(os.path.join(ART, "baselines.json"), "w") as fh:
        json.dump(results, fh, indent=2)
    print("\nwrote artifacts/baselines.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
