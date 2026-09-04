.PHONY: demo test consortium equivalence baselines failures all clean help

help:
	@echo "make demo        - full conformance suite + Agent Studio exhibit"
	@echo "make consortium  - abuse invisible to every merchant involved"
	@echo "make equivalence - proof: certification == enforcement"
	@echo "make baselines   - the two approaches KASAUTI claims to beat"
	@echo "make failures    - replay the bugs, as regression tests"
	@echo "make test        - the whole test suite"
	@echo "make all         - everything, in the order a reviewer should read it"

demo:
	python scripts/run_suite.py

consortium:
	python scripts/run_consortium.py

equivalence:
	python scripts/prove_equivalence.py

baselines:
	python scripts/compare_baselines.py

failures:
	python scripts/failure_lab.py

test:
	python -m pytest -q

all: test demo consortium equivalence baselines
	@echo
	@echo "Read NOT_CHECKED.md next. It says what none of the above proves."

clean:
	rm -rf .pytest_cache .hypothesis **/__pycache__ __pycache__
