"""
Optional-dependency shim for Hypothesis. See FAILURES.md #12.

`hypothesis` is a genuine test dependency (it found the bugs in FAILURES.md
#3), and requirements.txt still asks for it. But its absence used to kill
pytest during COLLECTION -- `Interrupted: 2 errors during collection`, exit
code 2, zero tests run -- which made the README's "stdlib+pytest" claim false
and made a missing pip install look identical to a broken repo.

Importing from here instead of from `hypothesis` directly means:

  - with hypothesis installed: the real thing, unchanged. Same properties,
    same 200 examples, no behaviour difference whatsoever.
  - without it: `@given` tests report as SKIPPED with a reason, and every
    deterministic test in the same module still runs.

What this deliberately does NOT do is let a property test *pass* when the
engine that checks it is absent. A skip is visible in the summary; a false
pass is not. That distinction is the entire point of the file.
"""
from __future__ import annotations

try:
    from hypothesis import assume, given, settings  # noqa: F401
    from hypothesis import strategies as st  # noqa: F401

    HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover - exercised only in the degraded env
    from conftest import _missing_given as given  # type: ignore # noqa: F401
    from conftest import _missing_settings as settings  # type: ignore # noqa: F401

    HAS_HYPOTHESIS = False

    def assume(_condition):  # type: ignore
        """No-op: unreachable, since every @given test is skipped."""
        return None

    class _StrategyStub:
        """Any attribute access returns a callable returning None.

        `@given(x=st.integers())` is evaluated at DECORATION time, i.e. at
        import, before the skip marker can intervene. So `st.integers()` must
        not raise -- but the value it produces is never consumed, because the
        test body never executes.
        """

        def __getattr__(self, _name):
            def _stub(*a, **k):
                return None
            return _stub

    st = _StrategyStub()  # type: ignore
