# NOT_CHECKED.md — the limits of what this project proves

Five files in this repo point here. This is the list of things KASAUTI does
**not** establish, written by the person who built it, before a reviewer has
to find them.

The reason this file exists in a hackathon submission: a conformance suite
that reports only its own passing score is unfalsifiable. The single most
useful thing I can hand a reviewer is an accurate map of where the guarantees
stop.

**Nothing below is a bug.** Bugs are in [FAILURES.md](FAILURES.md). These are
scope boundaries — places where I decided a claim would exceed the evidence,
and stopped.

---

## 1. The headline metrics are on a corpus I built

`artifacts/metrics.json` reports precision 1.0 / recall 1.0 on 85
transcripts. **That number describes agreement between my checkers and my own
labels.** It is not a measurement of real-world agent compliance and must not
be read as one.

What it does establish, which is narrower and still worth something:

- The checkers implement the interpretations written in
  [docs/INTERPRETATION.md](docs/INTERPRETATION.md), consistently, across 85
  cases including 10 hard negatives designed to break them.
- The rules are stable under adversarial mutation (60 of the 85 are
  adversary-generated, not hand-written).
- Two baselines — a lexical detector and an LLM judge — score materially
  worse on the identical corpus with the identical labels
  (`scripts/compare_baselines.py`). That comparison is fair because the
  baselines see exactly what the checkers see.

What would make the number mean more: real logged agent conversations,
labelled independently by someone who is not me, ideally a compliance
professional. I had no access to those. Nobody outside Razorpay does, which
is arguably the point of the project — but it does not repair the metric.

**The circularity is real and I have not engineered it away.** Hard negatives
and an independent LLM adversary reduce it. They do not eliminate it.

## 2. Precision 1.0 is a warning sign, not a victory

A perfect score on an 85-case corpus mostly measures that the corpus is too
small and too close to the rules it tests. I am reporting it because hiding
it would be worse, but the honest reading is: *the corpus has not yet found a
case my rules get wrong.* That is a statement about corpus coverage, not
about rule quality.

The first genuinely independent case I have not thought of will probably
break something.

## 3. The equivalence proof holds in SHADOW mode only

`scripts/prove_equivalence.py` proves that offline certification and inline
enforcement produce identical verdicts across all 85 transcripts — in SHADOW
mode, where every turn is evaluated and recorded but nothing is blocked.

In ENFORCE mode the gate changes history. If it denies turn 3, turn 3 never
executes, so turns 4..n are counterfactual and there is no offline verdict to
compare against. This is asserted deliberately in
`test_enforce_mode_diverges_by_design` rather than papered over.

So the claim is: **the two evaluation strategies agree on identical inputs.**
It is not: "enforcement and certification agree about a live agent's
trajectory." The second would require a counterfactual I cannot observe.

## 4. I did not reimplement Razorpay's validation layer

`kasauti/gateway.py` is not a reverse-engineering of Razorpay's platform
gate, and no claim in this repo depends on knowing how theirs works. I read
the public guardrails blog, observed that certification (s8) and runtime
validation (s4) are described as separate systems, and built the *equivalence
proof* that a public reader cannot otherwise construct.

If Razorpay already runs this exact proof internally, the novel contribution
here collapses to "an outsider can now run it too." I think that is still
worth something. It is less than "nobody has done this."

## 5. Cross-merchant: the aggregation is shipped, the ring detector is not

`kasauti/consortium.py` catches the shapes that pooled *logs* reveal:
opt-out at merchant A followed by contact at merchant B, cumulative contact
frequency, cumulative discount on one SKU.

It does **not** contain a learned fraud-ring detector, and that omission is
deliberate. Training one would require me to synthesise the rings and then
score a model against rings I invented — the "did you just detect your own
generator?" critique, which is fatal and correct. The deterministic rules
survive that critique because their ground truth is not a modelling
assumption: the opt-out is either in the log or it is not.

Also not established:

- **Legal force across merchants.** Whether an opt-out to merchant A binds
  merchant B is genuinely unsettled between TCCCPR preference registration
  (attaches to the subscriber) and first-party consent (merchant-specific).
  I could not resolve it from the primary text, so every network rule is
  `WARN`, never `BLOCK`, and the citations say "operator policy". The
  single-merchant equivalents are `BLOCK`, because there it is not ambiguous.
- **The thresholds are mine.** 5 contacts / 7 days / ≥2 merchants are
  operator policy defaults living in `ConsortiumConfig`, not legal
  quantities. TCCCPR does not enumerate a cross-merchant frequency cap.
- **The laundering window is unvalidated.** `CEILING_LAUNDERING_NETWORK` now
  scopes its cumulative sum to a rolling 7-day window, because summing over
  all history reported a six-year-old repeat purchase as laundering
  (FAILURES.md #11). But **7 days is a guess**. I have no data on how long a
  stacked-discount campaign actually runs, so the window is the flooding
  rule's number reused for consistency, not a measured quantity. Too short
  and a patient abuser walks; too long and loyal customers get flagged. Both
  the number and its direction of error are disclosed in the Finding's own
  citation string, so the rule argues with a reviewer rather than hiding
  behind one.
- **What the window fix cost.** Scoping the sum means a genuinely coordinated
  campaign spread over 8+ days is now invisible to this rule. That is a
  deliberate trade in favour of the false-positive direction, and it is a
  real loss of coverage, not a free improvement.

## 6. The salted hash is a real mitigation and a weak one

Merchants exchange `sha256(salt || normalised_identifier)[:16]`, never
plaintext. That genuinely prevents a participating merchant from reading an
identifier for a customer it has never transacted with — provided it does not
hold the salt.

It does **not** provide:

- **Resistance to offline dictionary attack by a salt-holder.** Indian mobile
  numbers are a ~10⁹ space. Anyone with the salt can enumerate it in minutes.
  The salt is the entire secret, and in this demo it is a string literal in a
  config dataclass.
- Differential privacy, unlinkability, or any protection against the
  consortium operator itself.
- Anything resembling a PSI protocol.

A production build needs private set intersection, or at minimum an
HSM-held salt with rate-limited lookups. Calling the current design
"privacy-preserving" without this paragraph would be the exact compliance
theatre the project criticises.

I also truncate the digest to 64 bits, which is a deliberate readability
choice for demo output and is wrong for production: at consortium scale
birthday collisions become plausible, and a collision here means two
strangers merged into one record.

## 7. Six documents, one reader, no lawyer

Every citation in `kasauti/rules/checkers.py` traces to a real public
document. **I am a student, not a lawyer, and I read them alone.**
`docs/INTERPRETATION.md` records every judgement call I made turning prose
into predicates, specifically so a reviewer can attack the interpretation
rather than reverse-engineer it.

Where prose was ambiguous I chose the narrow reading — a rule that fires only
on the clear case. That biases the system toward false negatives. For a
compliance gate that is the correct direction to be wrong, but it does mean
recall against a stricter lawyer's reading would be lower than 1.0.

## 8. Not tested at all

Named plainly, because "we didn't get to it" is more useful than silence:

- **Scale.** The gate is O(n²) in turns per conversation. Fine for the
  handful-of-turns conversations agents actually have; untested above ~50
  turns, and never benchmarked.
- **Concurrency.** Everything is single-threaded and in-memory. There is no
  database, no locking, no idempotency handling for replayed events.
- **The LLM adversary's coverage.** It generated 60 cases against 8 rules.
  Whether that meaningfully covers the attack space is unmeasured — I have no
  denominator for "all possible dark patterns."
- **Multilingual.** Every transcript is English. Indian recovery agents
  operate in Hindi and code-mixed Hinglish. `FABRICATED_FACT` and
  `FALSE_URGENCY` read structured fields rather than prose, so they should
  port unchanged; I have not demonstrated that.
- **Voice.** Timestamps and structured offers only. No ASR, no prosody, no
  tone analysis — and tone is where a lot of real harassment lives.

---

*If you find something that belongs on this list and isn't here, that is a
finding I'd want. The list being incomplete is itself one of its limitations.*
