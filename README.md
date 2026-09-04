# KASAUTI

**An executable conformance suite for money-moving AI agents in India.**

Razorpay's Agent Studio promises every published agent is screened for dark
patterns, consent and compliance. That promise is real, and it is enforced by
an internal, proprietary, partly-manual pipeline. **There is no public,
executable test a developer can run to find out whether their agent clears the
bar before submitting it.**

KASAUTI is that test.

An LLM writes the attacks. Deterministic code writes the verdicts. The
separation is enforced by a static guard, not by a promise in a README.

```bash
git clone https://github.com/RomitDeokar/razorpay.git && cd razorpay
make demo          # no API key, no network, no install beyond stdlib+pytest
```

---

## Metrics first

On an 85-transcript corpus (9 handwritten, 10 hard negatives, 6 provenance,
60 LLM-adversary-generated), scored against the same labels for every system:

| System | Precision | Recall | Exact-match | Clean txns wrongly blocked |
|---|---|---|---|---|
| **KASAUTI (deterministic)** | **1.00** | **1.00** | **1.00** | **0 / 13** |
| Lexical keyword baseline | 0.76 | 0.72 | 0.38 | 3 / 13 |
| LLM judge baseline | *requires `GEMINI_API_KEY`; skip is reported, not hidden* | | | |

**Read that 1.00 with suspicion, and read [NOT_CHECKED.md](NOT_CHECKED.md)
before you quote it.** It measures agreement between my checkers and my own
labels on a corpus I built. A perfect score on 85 cases mostly means the
corpus has not yet found a case the rules get wrong. The honest version of
this claim, in full, is §1 and §2 of NOT_CHECKED.md.

What the comparison *does* establish fairly: three systems, one corpus, one
label set, and the two everybody builds first are materially worse. The
lexical baseline fires on truthful-but-urgent copy and misses every
arithmetic violation.

```
make demo        # the full suite + the Agent Studio launch-demo exhibit
make consortium  # abuse invisible to every merchant involved
make equivalence # certification == enforcement, proven on all 85
make baselines   # the two approaches KASAUTI claims to beat
make test        # 371 tests
```

---

## Why this is not a "policy firewall"

That was the first design, and it was wrong. Razorpay's guardrails blog §4
already describes a platform-level validation layer that checks amounts,
scope and compliance boundaries on every action. Building a second one is a
feature request wearing a project's clothes.

Reading the same blog closely, though, there are **two** systems described:

- **§8 certification** — offline screening at publish time. Sees whole
  conversations, after the fact.
- **§4 runtime validation** — inline gating before execution. Sees one action
  at a time, before the fact.

Both are sensible. Nothing establishes that they *agree with each other*, and
in every other regulated industry the gap between them has a name:
**certified-but-drifted**. An agent passes certification, ships, and behaves
differently in production — not through malice, but because two systems built
from two specs diverge, invisibly, until it costs money.

So KASAUTI does not implement a second rule set. There is exactly one set of
predicates, and `kasauti/gateway.py` is a different *evaluation strategy* over
the same functions. They cannot drift, because they are the same code.
`scripts/prove_equivalence.py` proves it on all 85 transcripts.

The novel artifact is the proof, not the gate.

> The load-bearing case is `ESCALATING_PRESSURE`: offering 20% off is
> perfectly legal until you know the customer refused 10% one turn earlier.
> An inline gate that inspects only the action in front of it — the obvious
> implementation — cannot catch it even in principle.

---

## Where the AI is, and where it is banned

| Decision | Tool | Why |
|---|---|---|
| Generate novel attacks | **LLM** (Gemini free tier) | Open-ended language generation. A human cannot enumerate this space; an LLM genuinely can. |
| Extract numeric claims from prose | **LLM**, in the harness | Reading "that'll be ₹2,499" into an int is language work. |
| **Decide whether a rule was broken** | **Pure Python. LLM forbidden.** | Closed-form predicate over structured data. An LLM here makes verdicts unreproducible — and per DECEPTICON (ICLR 2026) only ~28.6% effective. |
| **Admit or deny an action** | **Pure Python. LLM forbidden.** | Money moves only through code paths that a prompt cannot argue with. |
| Cross-merchant join | **Pure Python (sha256)** | Arithmetic over logs. |

The ban is **structural, not aspirational**. `kasauti/engine.py::assert_no_llm`
walks the AST of every checker module and rejects any import outside a
hand-maintained allowlist, then walks the bytecode for unambiguously impure
attributes (`urlopen`, `utcnow`, `randint`, …). A pure function cannot reach
a network it never imported.

That guard has fired on my own legitimate code three times. Each time the
allowlist grew by hand, deliberately — see FAILURES.md #1, #5 and #9. A
blocklist would have permitted all three and taught me nothing.

---

## The three layers

Each exists because the one below it has a hole that better rules cannot fix.
The hole is in the **arity** of the question, not the quality of the answer.

**1. Per-episode** (`kasauti/rules/checkers.py`) — 8 rules over one
conversation. False urgency, escalating pressure, consent, opt-out, contact
window, discount ceiling, fabricated facts, prompt injection.

**2. Cross-episode** (`kasauti/crossepisode.py`) — the shapes one transcript
cannot show. Customer opts out in episode 7, is contacted in episode 41. Eleven
contacts in seven days, every one individually legal. 10% + 10% + 10% on one
SKU, never breaching a 10% cap, giving away 30%. *Every episode in these
fixtures passes the per-episode engine individually — asserted in code before
any metric prints, not claimed in a comment.*

**3. Consortium** (`kasauti/consortium.py`) — the shapes no single merchant
can see at all. Opt out at merchant A, get contacted by B. Two contacts each
from four merchants: eight messages, every merchant inside its own cap.
Razorpay is the only party in the Indian stack that sits across all of them.

Merchants never exchange identifiers — the join is a salted `sha256` over a
normalised phone/email. **That is a real mitigation and a weak one**; exactly
what it does and does not protect against is NOT_CHECKED.md §6.

### What I deliberately did not build

A learned fraud-ring detector. Training one requires synthesising the rings
and then scoring a model against rings I invented — *"did you just detect
your own generator?"*, which is a fatal and correct critique.

The deterministic rules survive it because their ground truth is not a
modelling assumption. `SUPPRESSION_BREACH_NETWORK` does not infer that a ring
exists; it reads whether an opt-out is in one log and a contact is in another.

> "these accounts form a fraud ring" → a **model's** claim. Not shipped.
> "this person said stop at A and was contacted at B" → a **log's** claim. Shipped.

Less impressive. True.

---

## Prompt injection, without a blocklist

`Provenance` is an enum on every piece of content an agent reads:
`MERCHANT_CONFIG`, `FIRST_PARTY`, `CUSTOMER`, `UNTRUSTED`.

The rule is not *"does this text look like an attack."* It is: **did a
money-moving decision cite UNTRUSTED content as its authority?**

A blocklist of phrases like "ignore previous instructions" loses to
paraphrase, translation, or base64. Provenance does not, because it never
reads the attack at all — it reads where the authority came from. An agent
that *reads* a poisoned review is careless; an agent that justifies a 100%
discount *by* that review has been captured. Only the second is a violation.

Same lesson as the purity guard: **reason about capability, not about
strings.**

---

## What broke

[FAILURES.md](FAILURES.md) is the long version, kept from commit 1 and
written as things broke rather than reconstructed afterwards. The pattern
worth noticing is that **every bug in it was found by tooling I built to
attack my own work**, not by re-reading code.

The one I'd lead with — #7, found while writing this layer:

Blank customer identifiers hashed to a perfectly valid-looking join key. Every
merchant reporting a customer with a missing phone number joined onto that
same key, and the engine confidently accused **six unrelated people of being
one harassed customer**. A hash function has no opinion about whether its
input means anything; it laundered garbage into something that looked
authoritative. Validation had to move *before* the hash
(`DegenerateIdentifier`), and it is pinned by 20 parametrised regression
tests plus an end-to-end reproduction.

That bug is the strongest argument in the repo for why verdicts belong in
deterministic code: it was findable, reproducible, and fixable in one commit.
The same failure inside a prompt would have been a silent behaviour change.

---

## Honest limitations

Fully enumerated in **[NOT_CHECKED.md](NOT_CHECKED.md)**. The short version:

- The corpus is mine; the metric measures self-consistency, not real-world
  compliance.
- Equivalence is proven in SHADOW mode; ENFORCE mode diverges by design.
- Cross-merchant rules are `WARN`, never `BLOCK` — whether an opt-out at A
  legally binds B is genuinely unsettled, so I did not legislate it.
- I am a student, not a lawyer, and I read six regulations alone.
  [docs/INTERPRETATION.md](docs/INTERPRETATION.md) records every judgement
  call so you can attack the interpretation directly.
- No multilingual coverage, no voice, no concurrency, no scale testing.

---

## Layout

```
kasauti/
  schema.py        the contract; checkers read only this
  engine.py        verdicts + assert_no_llm (the purity guard)
  rules/checkers.py  8 per-episode rules, each with a citation
  crossepisode.py  3 aggregate rules over one customer's history
  consortium.py    3 network rules across merchants, salted-hash join
  gateway.py       the same predicates, evaluated inline
  adversary.py     the LLM red-teamer (offline mode is the default)
  baselines.py     the systems KASAUTI claims to beat
corpus/            85 transcripts + histories + consortium fixtures
tests/             371 tests, incl. property-based and regression
docs/INTERPRETATION.md   prose → predicate, every judgement call
FAILURES.md        what broke
NOT_CHECKED.md     what this does not prove
```

## Setup

Python 3.10+. Runtime dependencies: **none** beyond the standard library.

```bash
pip install -r requirements.txt   # pytest + hypothesis, for tests only
make demo
```

The LLM adversary uses `GEMINI_API_KEY` if present and falls back to seeded
combinatorial mutation otherwise, so **the demo runs on a plane** and the
metrics above reproduce byte-for-byte offline.

## Citations

Regulation: CCPA Dark Patterns Guidelines 2023 · RBI recovery-agent
directions · RBI e-mandate framework · TRAI TCCCPR 2018 · DPDP Act 2023.
Razorpay Agent Studio guardrails blog (30 Mar 2026). MediaNama's Agent Studio
launch report (18 Mar 2026) — the `MEDIANAMA_DEMO` exhibit reproduces
*behaviour as reported*, and is not a claim about Razorpay's shipped code.
DECEPTICON (Cuvin, Zhu & Yang, ICLR 2026) for the LLM-judge effectiveness
figure.

Built for the Razorpay AI Buildathon 2026.
