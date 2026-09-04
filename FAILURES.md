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

---

## #5 — Adding a pure stdlib import made my own purity guard reject my code

**What broke.** The cross-episode layer (`kasauti/crossepisode.py`) needs to
group episodes by customer, so it imports `collections.defaultdict`. The first
time I ran the guard over the new checkers:

```
AssertionError: module kasauti.crossepisode imports 'collections', which is
not on the pure-checker allowlist ['__future__', 'dataclasses', 'datetime',
'enum', 'kasauti', 'kasauti.schema', 'typing']
```

`collections` is perfectly pure. It reaches no network, no clock, no RNG. The
guard was still right to stop me.

**Why this entry is here even though the fix was one line.** The tempting fix
was to relax the check — swap the allowlist for "anything in the stdlib," or
just delete the import test for the new module. I did neither. I added
`collections` to `_ALLOWED_IMPORTS` **by hand, with a comment explaining
why it is pure.**

That friction is the entire value of an allowlist. Every future import into a
checker module now costs a human decision. A blocklist would have said nothing
and I would have learned nothing — which is precisely the failure recorded in
#1, where a name blocklist both rejected `dict.get` and would have waved
through `import requests as r`.

**Tests.** `test_cross_episode_checkers_are_pure` in `tests/test_purity.py`
runs `assert_no_llm` over `ALL_CROSS_CHECKERS`, so the new layer is held to
the same standard as the original seven rules rather than being exempted from
it by omission.

**What I'd take from it.** A security check that never inconveniences you is
not doing anything. I now read "the guard fired on my own legitimate change"
as evidence the guard works, not as a reason to soften it.

---

## #6 — My new injection rule was redundant, and my own demo proved it

**What broke.** Nothing crashed. The rule was just **useless**, and I only
found out because I printed the per-rule breakdown instead of the summary.

I added `INJECTED_INSTRUCTION` (provenance-based prompt-injection defence) and
wrote four attacks for `scripts/failure_lab.py`: a crude "IGNORE PREVIOUS
INSTRUCTIONS" in a supplier description, an injection in a customer review, a
hostile inbound buyer-agent note, and a subtle paraphrase with no injection
keywords at all. All four blocked. I was pleased.

Then I looked at *which* rules fired:

```
[DISCOUNT_CEILING]      offered 100% on SKU_AIRFRYER; merchant ceiling is 10%
[INJECTED_INSTRUCTION]  offer of 100% ... authorised by 'untrusted' content
```

Every one of my four attacks asked for a discount **above the merchant
ceiling**. So `DISCOUNT_CEILING` — a rule that already existed — blocked all
four on its own. My new rule had not caught a single case that the old rules
missed. I had written a whole schema extension, a checker, and a demo, and
demonstrated **nothing**. Worse, the demo *looked* like a success, which is
how this kind of defect survives.

**Fix.** I added attack #5: a hostile buyer-agent note asking for exactly
**10% — precisely at the ceiling.** Every value-based rule stays silent
because the amount is legal. The verdict is now:

```
Under-the-ceiling capture (the case only provenance catches)
  agent decided: 10% off (within ceiling!)  [COMPROMISED]
  engine verdict: BLOCKED
     [INJECTED_INSTRUCTION]  ... authorised by 'untrusted' content
```

One rule, alone. That is the case that justifies the rule existing: the agent
handed pricing authority to a stranger and the amount it happened to land on
was legal. Next time the note will ask for more.

**Tests.** `test_under_ceiling_capture_is_caught_only_by_provenance` asserts
`rules_fired == ["INJECTED_INSTRUCTION"]` — exactly one rule. If any other
rule ever starts firing on that fixture, the test fails, because the fixture
would no longer isolate the provenance-only case.

**What I'd take from it.** A passing demo is not evidence that the thing you
just built does anything. I had confused "my system blocked the attack" with
"my new component blocked the attack." The diagnostic that mattered was
per-rule attribution, and I nearly shipped without looking at it. I applied
the same reasoning to the cross-episode layer *before* writing it, which is
why `test_cross_episode_layer_is_not_redundant` asserts every episode in every
violating history is individually clean — a test designed to prove that layer
unnecessary. It is the same defect class, caught once by luck and once by
process.

---

## #7 — Two claims this repo could not back, found by grepping my own README

**Not a crash. Two lies, and the worse kind: unintentional ones.**

Before writing the README I went looking for every factual claim the repo
makes about itself, intending to check each one. Two failed immediately.

### #7a — A docstring asserted a machine check that never ran

`corpus/history.py` says, in its module docstring:

> "`scripts/run_suite.py` asserts exactly that property before reporting the
> cross-episode metrics, so the claim is machine-checked rather than a comment."

It was a comment. `run_suite.py` contained:

```python
from corpus.history import HISTORIES              # noqa: E402
from kasauti.crossepisode import ALL_CROSS_CHECKERS, judge_history
```

…and used **neither**. The cross-episode layer — three rules, 295 lines, its
own 295-line test file — never executed in the suite that reports the
project's metrics. The imports were the only surviving evidence I had once
intended to wire it up.

**Why this is the worst possible bug for this project specifically.** The
entire argument of KASAUTI is that compliance claims must be executable
rather than asserted. I shipped a docstring asserting an execution that did
not happen. If a judge greps for `judge_history` before reading the prose —
which is exactly what I would do — the project's own thesis is the first
casualty.

**Fix.** `run_suite.py` now runs all four histories, and asserts the
load-bearing property per episode: every episode in a violating history must
pass the per-episode engine *individually*. If any single episode were dirty,
the cross-episode finding would be redundant rather than novel, and the layer
would be decoration claiming an insight it had not earned.

### #7b — The flagship rule was scored on an empty set

The same pass caught `INJECTED_INSTRUCTION` reporting:

```
INJECTED_INSTRUCTION      0    0    0     -       -
```

`tp=0, fp=0, fn=0`. Not one transcript in the corpus set `action_authority`,
so the provenance rule — the one with the custom schema extension, the
dedicated demo script, and the most README prose — had **no denominator**. Its
row rendered as a dash, which at a glance looks like every other row.

I had 19 tests covering it and zero corpus measurement. Tests prove a function
does what I said; corpus support proves the rule ever meets a case I did not
write while thinking about that rule.

**Fix.** Six new transcripts in `corpus/builder.py::provenance_cases()` —
three positive (crude over-ceiling, under-ceiling capture, keyword-free
paraphrase) and, more importantly, three hard negatives: an agent that *reads*
poisoned content and prices from merchant config anyway; a first-party
authority; and untrusted content cited for an action that moves no money. The
negatives are what stop the rule degenerating into "fires whenever untrusted
text exists," which would block every agent that has ever read a review.

**And a guard so this class cannot recur.** `run_suite.py` now exits non-zero
on *any* rule with zero labelled positives:

```
[ok] zero-support guard: all 8 rules have labelled positives
```

An unmeasured rule is now a build failure rather than a formatting artifact.

**What I'd take from it.** This is the third appearance of one defect class —
#4 (an unfalsifiable metric), #6 (a component that looked functional because a
different component did the work), and now #7 (a claim with no execution
behind it). The pattern is that **my own reports are the least reliable thing
in the repo**, because they are written by the person who wants them to be
true. Every fix has been the same move: convert the claim into something that
can fail. I stopped trusting my summaries and started grepping them.

---

## #8 — The evidence string described an offer that never existed

**Found while building the gateway equivalence proof, in eight transcripts.**

`test_enforce_mode_diverges_by_design` failed with an assertion I had written
expecting the opposite result. Chasing it surfaced a defect in
`ESCALATING_PRESSURE` — the rule this whole project is built around:

```
customer refused at turn 1; agent then raised discount from 0% to 10%
```

There was no 0% offer. `best_before_refusal` is initialised to `0.0`, so when
an agent made its **first** offer *after* a refusal, the rule reported a
phantom prior offer of 0% and described a sweetening that never occurred. A
corpus sweep found it in **8 of 85** transcripts.

**Why I fixed it rather than shrugging.** The *verdict* was correct — dangling
a discount after a customer has said no is pressure under guardrails blog §5,
which is about persisting past a refusal, not about arithmetic. So precision
and recall were completely unaffected, and the headline metrics never moved.

But the evidence string is not decoration. It is the artifact a merchant reads
when deciding whether a block was correct, and it is the thing this repo
offers instead of a confidence score. **A control layer that gives a
demonstrably false reason gets switched off**, and it deserves to be. This is
the same failure as #2a, where a false-positive evidence string reported a
violation while printing two identical numbers: the number was defensible, the
sentence was gibberish, and the sentence is the product.

**Fix.** Track *whether* an offer preceded the refusal separately from its
size, and emit the fact that actually occurred:

```
agent had made no offer before the refusal, then introduced a 40% discount
to reopen the closed conversation
```

**Tests.** Three, at three scopes:
`test_first_offer_after_refusal_does_not_claim_a_phantom_prior_offer` (the
case), `test_genuine_escalation_still_reports_both_numbers` (so the fix does
not flatten the real case into generic wording), and
`test_no_corpus_transcript_reports_a_phantom_prior_offer` (a corpus-wide sweep,
so the defect class is extinct rather than fixed in the one fixture I wrote).

**The part worth reporting.** My test asserted that ENFORCE mode would stop
firing `ESCALATING_PRESSURE` entirely, since the offer it blocked is no longer
in the history to escalate from. **I was wrong, and the code was right.** The
rule still fires, because the agent still persisted past a no; only the
explanation changes. I updated the test to pin what is actually true and left
the reasoning in the docstring, because "I predicted X, observed Y, and Y was
better" is the most useful thing in this file.

**What I'd take from it.** Building a second evaluation strategy over the same
predicates found a bug in the predicates that 300+ tests and a 100%-exact-match
corpus had both missed. Not because the new strategy was cleverer — because
it asked the same rules a *differently-shaped* question, and disagreement
between two views of one system is the cheapest bug detector I have.

---

## #9 — A hash function laundered garbage into an accusation

**What broke.** The consortium layer joins merchants on a salted hash of a
customer identifier, so no merchant ever sees another's plaintext phone
number. I wrote it, wrote five fixtures, and all five passed on the first
run.

Passing on the first run is not a good sign in this project, so I did what
FAILURES.md #2 taught me to do and probed it with inputs I had not designed
for. The first thing I tried was an empty identifier:

```
FINDINGS ON SIX UNRELATED PEOPLE WITH BLANK IDs: 7
  SUPPRESSION_BREACH_NETWORK  customer 219d6461077a5513 opted out at MERCH_A ...
  CONTACT_FLOODING_NETWORK    customer 219d6461077a5513 received 6 contacts in
                              7d across 6 merchants (MERCH_A ... MERCH_F)
```

Six different people, each with a missing phone number in their merchant's
CRM. `sha256(salt + "")` is a perfectly well-formed 64-bit hex string, so all
six joined onto one key. The engine then reported, with full confidence and
a legal citation attached, that a customer who **does not exist** was being
harassed by six merchants.

**Why.** I had been thinking of the hash as a privacy mechanism and stopped
thinking there. But `join_key` has a second job I never named: it decides
*who is the same person*. A hash function has no opinion about whether its
input identifies anybody — it maps bytes to bytes. Feeding it `""`, `"NA"`,
`"-"` or `"9999999999"` produces output indistinguishable from a real join
key, so every downstream rule treats it as authoritative.

The output format is what made this dangerous. A crash would have been fine.
Instead the failure produced a *confident, well-formatted, citation-bearing
finding* — the exact shape of output a reviewer is least likely to question.

This is also the worst possible direction to be wrong in. A missed violation
costs a merchant money. A false `SUPPRESSION_BREACH_NETWORK` is an accusation
of harassment against a customer who did nothing, generated by a system whose
entire pitch is that it is more trustworthy than an LLM.

**Fix.** Validation moved *before* the hash, as a hard failure rather than a
skip. `DegenerateIdentifier` is raised for placeholder values, for anything
that is not a valid 10-digit Indian mobile (NNP mobile series start 6–9), and
for anything that is not a plausible email. Silently dropping unjoinable rows
would under-report; silently joining them over-accuses. Both are quiet. An
exception is not.

Refusing to join is always safe. Guessing is not.

**Tests.** `test_degenerate_identifiers_are_refused` parametrised over 20
values that all previously produced valid keys; `test_phantom_join_cannot_
recur_end_to_end` reproducing the six-merchant scenario above; and —
importantly — `test_valid_identifiers_still_accepted`, because without it
`_reject_if_degenerate` could raise unconditionally and every other test in
the file would still pass. That is the same mistake I nearly made in #1: a
guard that rejects everything looks identical to a guard that works, if you
only test the rejection side.

**What I'd take from it.** I have now shipped the same class of bug twice.
In #1 I trusted a name blocklist to reason about capability. Here I trusted a
hash to reason about identity. Both times I took a function that is excellent
at one job and quietly assumed it was doing a second, harder job that nobody
had implemented.

The question I did not ask, and now ask by default: *what is this function
deciding that I never told it to decide?*
