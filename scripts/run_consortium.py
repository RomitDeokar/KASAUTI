"""
The consortium demo: abuse that is invisible to every merchant involved.

Run:  python scripts/run_consortium.py

This script asserts its own premise before reporting anything. For every
fixture it first proves that each participating merchant's report is
individually compliant, and only then reports what the pooled view shows.
Without that assertion the network findings would be unfalsifiable -- I could
be re-reporting violations the single-merchant layer already catches and
calling it a new capability.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from corpus.consortium_fixtures import ALL_FIXTURES  # noqa: E402
from kasauti.consortium import (  # noqa: E402
    ConsortiumLedger,
    evaluate_consortium,
)

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _each_merchant_is_individually_clean(led: ConsortiumLedger) -> bool:
    """The premise of the whole layer, machine-checked.

    Evaluate each merchant's report ALONE. If a network rule fires on a
    single merchant in isolation, then that finding was never a network
    finding and the fixture is lying about what it demonstrates.
    """
    for r in led.reports:
        solo = ConsortiumLedger(config=led.config, reports=[r])
        if evaluate_consortium(solo):
            return False
    return True


def main() -> int:
    print()
    print(f"{BOLD}KASAUTI consortium layer{RESET}")
    print("the abuse shape no single merchant's data can reveal")
    print("=" * 74)
    print()
    print(f"{DIM}Merchants never exchange identifiers. The join is a salted{RESET}")
    print(f"{DIM}sha256 over a normalised phone/email. What a hash does and{RESET}")
    print(f"{DIM}does NOT protect against is stated in consortium.py and{RESET}")
    print(f"{DIM}NOT_CHECKED.md -- it is not a PSI protocol.{RESET}")
    print()

    failures = 0
    for name in sorted(ALL_FIXTURES):
        led, expected, why = ALL_FIXTURES[name]()
        findings = evaluate_consortium(led)
        got = sorted({f.rule_id for f in findings})
        ok = got == sorted(expected)
        failures += 0 if ok else 1

        premise = _each_merchant_is_individually_clean(led)
        mark = f"{GREEN}ok  {RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{mark}] {BOLD}{name}{RESET}")
        print(f"         merchants: {len(led.reports)}   "
              f"each individually clean: "
              f"{GREEN if premise else RED}{premise}{RESET}")
        print(f"         {DIM}{why}{RESET}")
        if got:
            for f in findings[:3]:
                print(f"         {YELLOW}[{f.rule_id}]{RESET} "
                      f"{f.evidence[:150]}")
        else:
            print(f"         {CYAN}-> CLEAN (correctly silent){RESET}")
        print()

    print("-" * 74)
    fired = set()
    for fn in ALL_FIXTURES.values():
        led, _, _ = fn()
        fired |= {f.rule_id for f in evaluate_consortium(led)}
    print(f"  fixtures      : {len(ALL_FIXTURES)}")
    print(f"  network rules : {len(fired)} exercised")
    print(f"  mismatches    : {failures if failures else 'none'}")
    print()
    print(f"{DIM}Honest scope: the deterministic aggregation is shipped. A{RESET}")
    print(f"{DIM}LEARNED ring detector is NOT -- synthetic rings would mean{RESET}")
    print(f"{DIM}scoring a model against rings I invented. See NOT_CHECKED.md.{RESET}")
    print()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
