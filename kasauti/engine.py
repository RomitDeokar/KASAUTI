"""
KASAUTI verdict engine.

The engine is the *only* thing that issues verdicts, and it is pure:
same transcript in => same verdict out, forever, on any machine.

The `assert_no_llm` guard below is the load-bearing piece of the whole
project's AI-judgment claim. It is not a README promise, it is a test:
we walk the bytecode of every checker and fail if it can reach a network
call or a model client. See tests/test_no_llm_in_checkers.py.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime

from .rules.checkers import ALL_CHECKERS, RULE_IDS
from .schema import Finding, Severity, Transcript


@dataclass
class Verdict:
    transcript_id: str
    passed: bool
    findings: list[Finding]
    rules_fired: list[str]

    def to_dict(self) -> dict:
        return {
            "transcript_id": self.transcript_id,
            "passed": self.passed,
            "rules_fired": self.rules_fired,
            "findings": [asdict(f) for f in self.findings],
        }


# Modules a checker may import. Anything else = it can reach the outside world.
#
# NOTE on why this is an import allowlist and not a name blocklist:
# v1 of this guard blocked bare names including "get", and it immediately
# failed on my own `t.catalog.get(sku)` -- because `dict.get` and
# `requests.get` are the same identifier in CPython bytecode. Blocking names
# is unsound in both directions. Checking what the module can IMPORT is
# sound: a pure function cannot reach a network it never imported.
# See FAILURES.md #1.
_ALLOWED_IMPORTS = {
    "__future__", "kasauti", "kasauti.schema", "dataclasses", "enum",
    "datetime", "typing",
}

# These are unambiguous: no legitimate spelling of them exists in a pure checker.
_FORBIDDEN_ATTRS = {
    "urlopen", "generate_content", "getrandbits", "randint", "uniform",
    "utcnow", "now", "today", "monotonic", "perf_counter",
}


def assert_no_llm(checkers=ALL_CHECKERS) -> None:
    """Static guarantee: no checker can reach a model, a network, a clock or
    a RNG. Raises AssertionError naming the offender.

    Two independent checks:
      1. The defining module imports nothing outside _ALLOWED_IMPORTS.
      2. No checker's bytecode (including nested code objects) references an
         unambiguously impure attribute such as datetime.now or urlopen.

    This is what makes the verdict reproducible, and what makes the
    "LLM proposes, code decides" claim structural rather than aspirational.
    """
    import ast
    import inspect

    seen_modules: set[str] = set()
    for fn in checkers:
        module = inspect.getmodule(fn)
        if module is None or module.__name__ in seen_modules:
            continue
        seen_modules.add(module.__name__)
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Relative import inside the kasauti package.
                mods = ["kasauti" if node.level and not node.module
                        else (node.module or "")]
                if node.level and node.module:
                    mods = [f"kasauti.{node.module}"]
            for m in mods:
                root = m.split(".")[0]
                if m not in _ALLOWED_IMPORTS and root not in _ALLOWED_IMPORTS:
                    raise AssertionError(
                        f"module {module.__name__} imports {m!r}, which is not on "
                        f"the pure-checker allowlist {sorted(_ALLOWED_IMPORTS)}"
                    )

    for fn in checkers:
        code = fn.__code__
        names = set(code.co_names)
        stack = list(code.co_consts)
        while stack:
            c = stack.pop()
            if hasattr(c, "co_names"):
                names |= set(c.co_names)
                stack.extend(c.co_consts)
        bad = names & _FORBIDDEN_ATTRS
        if bad:
            raise AssertionError(
                f"checker {fn.__name__} references impure attribute(s) "
                f"{sorted(bad)} -- checkers must be deterministic"
            )


def judge(t: Transcript) -> Verdict:
    """Run all checkers. Deterministic, ordered output."""
    findings: list[Finding] = []
    for fn in ALL_CHECKERS:
        findings.extend(fn(t))
    findings.sort(key=lambda f: (f.turn_idx, f.rule_id))
    fired = sorted({f.rule_id for f in findings})
    blocked = any(f.severity is Severity.BLOCK for f in findings)
    return Verdict(
        transcript_id=t.transcript_id,
        passed=not blocked,
        findings=findings,
        rules_fired=fired,
    )


def verdict_hash(v: Verdict) -> str:
    """Stable hash of a verdict, so a reviewer can prove a rerun matched."""
    blob = json.dumps(v.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Scoring against labels
# ---------------------------------------------------------------------------

@dataclass
class RuleMetrics:
    rule_id: str
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        d = self.tp + self.fp
        return self.tp / d if d else None

    @property
    def recall(self) -> float | None:
        d = self.tp + self.fn
        return self.tp / d if d else None


def score_corpus(transcripts: list[Transcript]) -> dict:
    """Per-rule and overall precision/recall against expected_violations.

    Honest accounting: a false positive here is a *clean* agent turn we
    wrongly blocked. In production that is a lost sale and an annoyed
    merchant, so we report it per-rule and never average it away.
    """
    per_rule = {rid: RuleMetrics(rid) for rid in RULE_IDS}
    exact = 0
    clean_total = 0
    clean_wrongly_blocked = 0

    for t in transcripts:
        v = judge(t)
        got = set(v.rules_fired)
        want = set(t.expected_violations)
        if got == want:
            exact += 1
        if not want:
            clean_total += 1
            if got:
                clean_wrongly_blocked += 1
        for rid in RULE_IDS:
            m = per_rule[rid]
            if rid in got and rid in want:
                m.tp += 1
            elif rid in got and rid not in want:
                m.fp += 1
            elif rid not in got and rid in want:
                m.fn += 1

    tp = sum(m.tp for m in per_rule.values())
    fp = sum(m.fp for m in per_rule.values())
    fn = sum(m.fn for m in per_rule.values())
    origins = Counter(t.origin for t in transcripts)

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "n_transcripts": len(transcripts),
        "origins": dict(origins),
        "micro": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": tp / (tp + fp) if (tp + fp) else None,
            "recall": tp / (tp + fn) if (tp + fn) else None,
        },
        "exact_match_rate": exact / len(transcripts) if transcripts else None,
        "clean_transcripts": clean_total,
        "clean_wrongly_blocked": clean_wrongly_blocked,
        "false_positive_rate_on_clean": (
            clean_wrongly_blocked / clean_total if clean_total else None
        ),
        "per_rule": {
            rid: {
                "tp": m.tp,
                "fp": m.fp,
                "fn": m.fn,
                "precision": m.precision,
                "recall": m.recall,
            }
            for rid, m in per_rule.items()
        },
    }
