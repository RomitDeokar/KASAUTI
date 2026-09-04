# FAILURES.md

Kept from commit 1. Nothing here is reverse-engineered to sound good — each
entry is a bug that shipped into a real commit, was found by a specific
mechanism, and is now pinned by a named regression test.

The pattern worth noticing: **every single bug below was found by tooling I
built to attack my own work**, not by reading the code again. That is the
argument for the whole project.

---

## #1 — My own purity guard was unsound, and it fired on my first run

**What broke.** `kasauti/engine.py::assert_no_llm` is the load-bearing claim
of this project: it proves no rule checker can reach an LLM, a network, a
clock, or an RNG. v1 implemented it as a **blocklist of bare identifiers**,
including `get`, `post`, `now`, and `random`.

The very first `python scripts/run_suite.py` died:

```
AssertionError: checker check_false_urgency references forbidden
name(s) ['get'] -- checkers must be pure and deterministic
```

**Why.** `t.catalog.get(sku)` and `requests.get(url)` compile to the *same*
identifier in CPython bytecode (`co_names` contains `'get'`). A name blocklist
cannot tell a dict lookup from an HTTP call. It was unsound in both
directions: it rejected pure code, and it would have happily passed
`import requests as r; r.request(...)`.

**Fix.** Replaced the blocklist with an **AST import allowlist** on the
defining module (`_ALLOWED_IMPORTS` = stdlib types + `kasauti.schema`), plus a
much narrower bytecode check for attributes that have no legitimate pure
spelling (`urlopen`, `utcnow`, `randint`, …). A pure function cannot reach a
network it never imported.

**Tests.** `test_purity_guard_allows_dict_get` and — more importantly —
`test_purity_guard_actually_catches_an_impure_checker`, which injects a fake
checker whose module imports `urllib` and asserts the guard still rejects it.
Without that second test, the fix could have degenerated into a guard that
passes everything.

**What I'd take from it.** My first instinct for "prove this code is pure" was
pattern-matching on names. The sound version reasons about *capability*: what
can this module reach at all? I now distrust any security check that operates
on strings rather than on structure.

---

## #2 — Three bugs found by deliberately probing my own rules

After the corpus hit 100% precision/recall I did not trust it, so I wrote a
throwaway probe script feeding the checkers cases I had *not* designed for.
It found three defects in about ninety seconds.

### #2a — Float precision caused a false positive at the exact ceiling

**What broke.** A merchant cap of `0.3` with an offer of `0.1 + 0.2`:

```
PROBE2 (0.1+0.2 vs cap 0.3): ['offered 0.3% on S; merchant ceiling is 0.3%']
```

The evidence string is self-refuting — it reports a violation while printing
two identical numbers. In IEEE-754, `0.1 + 0.2 == 0.30000000000000004 > 0.3`.

**Why it matters more than it looks.** This is the *worst* class of bug for
this product. A false positive on `DISCOUNT_CEILING` blocks a legitimate,
merchant-authorised sale. The suite would have been rejecting real revenue
because of binary representation error, and the merchant-facing explanation
would have been gibberish.

**Fix.** Introduced `_EPS = 1e-9` and routed every percentage comparison
through it (`> cap + _EPS`, `> best + _EPS`). 1e-9 is orders of magnitude
below any real merchant configuration (percentages are quoted to 2 decimals)
while eliminating representation error.

**Tests.** `test_exact_ceiling_in_float_arithmetic_is_not_a_violation`, plus
`test_ceiling_still_fires_on_a_real_breach` and
`test_ceiling_tolerance_band_is_negligible` (a 0.01pp breach must still fire)
so the tolerance cannot silently widen into a hole.

### #2b — Turn-order dependence caused a **false negative on the flagship rule**

**What broke.** `check_escalating_pressure` iterated `t.turns` in *list* order.
Given the same three turns listed out of index order, it returned `[]`:

```
PROBE3 (turns listed out of idx order): []
```

**Why it matters.** This is a miss, not a false alarm — the rule that
reproduces Razorpay's own launch-demo behaviour would silently pass a guilty
agent. And it is not hypothetical: **payment webhooks do not guarantee
ordering**. Any real integration that appends events as they arrive would hit
this. The 100% recall I was admiring was an artifact of my corpus builder
happening to append turns in order.

**Fix.** Both stateful checkers (`ESCALATING_PRESSURE`, `OPTOUT_IGNORED`) now
sort by `turn.idx` internally. Ordering is the checker's responsibility, not
the caller's.

**Tests.** `test_escalation_detected_when_turns_arrive_out_of_order`,
`test_optout_detected_when_turns_arrive_out_of_order`, and
`test_verdict_is_invariant_to_turn_list_order`.

### #2c — The probe surfaced a question the *regulation* doesn't answer

**Not a code bug — an interpretation gap I had not noticed.**

Probe: agent offers 10%, customer refuses, agent offers 5%, customer refuses,
agent offers 8%. The 8% is *higher than the previous turn* but *lower than the
10% already declined*. Escalation or concession?

I had written the rule to track a running maximum, so it returned "clean" —
but by accident, not by decision. The guardrails text says "bigger offers,"
which does not say bigger *than what*.

**Resolution.** Ruled it a concession, not an escalation, and wrote the
reasoning down as INTERPRETATION.md #2 with the counter-argument stated. Then
pinned *both* directions with tests
(`test_offer_below_an_already_refused_level_is_not_escalation` and
`test_exceeding_the_highest_refused_offer_is_escalation`) so the behaviour is
a decision rather than an implementation detail.

---

## #3 — Hypothesis broke my own fix, twice, and the *tests* were wrong

**What broke.** After the `_EPS` fix, two property tests failed:

```
Falsifying example: test_escalation_requires_increase_after_refusal(
    first=0.0, second=1e-09)
Falsifying example: test_discount_ceiling_boundary(cap=0.0, disc=1e-09)
```

**Why this entry is here.** My first reaction was that the fix was wrong.
It wasn't — the *specifications* were. Both properties asserted a bare
`second > first`, i.e. that a discount increase of one billionth of a
percentage point constitutes commercial pressure. That is not a defensible
claim about the world; it is a claim about floating point.

**Fix.** Restated both properties as three-region assertions: **must fire**
above the tolerance, **must not fire** at or below the cap, and an explicit
indifference band between. Then added two *separate* properties asserting the
band is commercially negligible (a 0.01pp increase must still be caught), so
"tolerance" can never quietly become "loophole."

**What I'd take from it.** Introducing a tolerance silently weakens every
property that depends on it. Hypothesis found the inconsistency between my
implementation and my spec within seconds of the fix landing — faster than I
would have found it by reasoning, and it forced me to say out loud what the
rule actually means.

---

## #4 — The metric I was most proud of is the one I trust least

**Not a crash. A measurement error, and the most important entry here.**

`scripts/run_suite.py` reports precision 1.000 / recall 1.000 / exact-match
1.000 across 79 transcripts. I wrote the checkers **and** the corpus **and**
the labels. That number is substantially circular and I will not present it as
evidence of real-world accuracy.

What I did about it, rather than deleting the number:

1. **Built an adversary I don't control the output of** (`kasauti/adversary.py`)
   and kept `origin` on every transcript so adversary-generated results are
   never blended into handwritten ones in any report.
2. **Built the baselines that could make KASAUTI look pointless**
   (`kasauti/baselines.py`) and scored them on the identical corpus with the
   identical labels. The lexical detector — the thing most people build first
   — scores **precision 0.752 / recall 0.733 / exact-match 0.367**, and
   wrongly blocks **3 of 10 clean agents**. That gap is the actual claim; the
   1.000 alone is not.
3. **Wrote hard negatives designed to break my own rules**: truthful-but-urgent
   copy, discounts that *drop* after a refusal, 08:00/18:59/19:00 boundary
   triples, honest rounding. The lexical baseline fires on four of them.
4. **Stated the residual limitation in NOT_CHECKED.md**, first section: the
   labels are my intent, the transcripts are synthetic, and no number here is
   validated against production traffic.

**What I'd take from it.** A conformance suite that only ever reports its own
score is unfalsifiable. Building the thing that could have made my project
unnecessary is what turned an unfalsifiable 1.000 into a defensible
comparison — and it is the part of this build I'd defend hardest.
