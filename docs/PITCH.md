# The 5-minute pitch, as a terminal session

Every claim in the video is a command a reviewer can re-run. Timestamps are
targets; the commands are the script.

---

**0:00 — The problem (30s, face to camera)**

Razorpay Agent Studio screens every published agent for dark patterns,
consent and compliance. That screening is internal and partly manual. No
developer can run it before submitting. KASAUTI is the public, executable
version of that bar: the LLM writes the attacks, deterministic code writes
the verdicts, and a static guard makes sure those two never swap places.

**0:30 — It runs, offline, with nothing installed (45s)**

```bash
git clone https://github.com/RomitDeokar/KASAUTI.git && cd KASAUTI
make demo
```

Point at the end of the output: 98 transcripts, 11 rules, precision 1.0 /
recall 1.0 micro, **0 of 18 clean transcripts wrongly blocked**. Then say
the honest thing out loud: *these numbers are against a corpus I control,
which is why `make baselines` exists.*

**1:15 — Exhibit A: Razorpay's own launch demo (60s)**

```bash
make judge                      # examples/medianama_demo.json
```

This is the air-fryer negotiation from the Agent Studio launch coverage,
transcribed. Four rules fire. Read one evidence line aloud —
`DISCOUNT_CEILING: offered 20% on SKU_AIRFRYER; merchant ceiling is 10%` —
and the citation under it. The verdict is not an opinion; it is a
predicate applied to the merchant's own catalog. Exit code 1.

Then:

```bash
make judge F=examples/clean_overnight_window.json    # exit 0
echo '{}' | python -m kasauti judge -                # exit 2, REFUSED
```

Three exit codes. Pass, blocked, *refused to guess*. A CI gate can use them
today.

**2:15 — Where the AI is banned (45s)**

```bash
python -m pytest tests/test_purity.py -q
```

`assert_no_llm` walks the AST of every checker and rejects any import or
call that could reach a model. Show FAILURES.md #1: the first version of
this guard was unsound and fired on my own code on the first run. That is
the pattern for the whole repo — every safety claim has a test that tried
to break it.

**3:00 — The shape one merchant cannot see (45s)**

```bash
make consortium
```

Seven fixtures. Three fire (ceiling laundering across merchants, contact
flooding, suppression breach); **four are negative controls** that must
stay silent — including the same SKU at two merchants six years apart,
which an earlier version flagged. Say: *I spent as long proving it stays
quiet as proving it fires.*

**3:45 — What broke (60s — the answer they read first)**

Open FAILURES.md. Nineteen entries, one per commit, written as they
happened. Pick two:

- **#12** — the README's first command exited 2 and ran zero tests without
  `hypothesis`. A claim in a README is a test nobody runs; now
  `test_readme_stdlib_claim_holds` runs it, and CI runs both environments.
- **#18** — the identifier validator I wrote to fix #9 (garbage hashed into
  a confident cross-merchant join) had the *same* hole one field over:
  `@a.com` passed. Found by an external reviewer running the suite. Same
  bug class, second time. The fix is one line; the lesson is that I had
  tested the phone branch and assumed the email branch.

**4:45 — Close (15s)**

Point at NOT_CHECKED.md. *This file says what none of the above proves.*
A learned ring detector is not shipped, because scoring a model against
rings I invented would be a number without meaning. Everything that *is*
shipped, you just watched run.

---

## If a judge asks

- **"Why not just prompt an LLM to grade the transcript?"** —
  `make baselines`. The keyword baseline (the thing most "compliance
  checkers" actually are) hits 0.61 recall and wrongly blocks 4 of 18
  clean transcripts, missing 48 real violations. The LLM-as-judge baseline
  runs with `--llm` and a `GEMINI_API_KEY`; neither can cite the regulation
  section it is applying, which is the whole point of a verdict.
- **"Isn't this just a policy firewall?"** — README § *Why this is not a
  "policy firewall"*. A firewall blocks at runtime; KASAUTI is proven
  equivalent to enforcement (`make equivalence`, 98/98) *and* usable as
  offline certification before the agent is ever deployed.
- **"What would you build next?"** — the Ed25519 mandate-authority layer
  (SETU in the idea bank). The same capability-not-content reasoning that
  defeats prompt injection here is the reasoning an agent-to-agent mandate
  adjudicator needs.
