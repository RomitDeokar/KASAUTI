"""
The JSON boundary (kasauti/io.py) and the CLI (kasauti/cli.py).

The claim under test: KASAUTI judges transcripts *you* wrote, not only the
98 it shipped with, and the JSON path is lossless -- the verdict hash of a
round-tripped transcript equals the verdict hash of the original, for every
corpus case. A serialiser that drops one field would turn a violation into
a CLEAN verdict silently; the round-trip test is what makes that impossible.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corpus.builder import build_corpus  # noqa: E402
from kasauti import io as kio  # noqa: E402
from kasauti.adversary import mutate_offline  # noqa: E402
from kasauti.cli import EXIT_BLOCKED, EXIT_CLEAN, EXIT_UNPARSEABLE, main  # noqa: E402
from kasauti.engine import judge, verdict_hash  # noqa: E402

# The same 98 `make demo` scores: 38 handwritten/hard-negative/provenance
# plus the 60 seeded adversary mutations.
CORPUS = build_corpus() + mutate_offline(60, seed=20260905)
BY_ID = {t.transcript_id: t for t in CORPUS}
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# ---------------------------------------------------------------------------
# Lossless round trip, every corpus transcript
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("t", CORPUS, ids=[t.transcript_id for t in CORPUS])
def test_round_trip_preserves_verdict_hash(t):
    text = kio.dumps(t)
    back = kio.loads(text)
    assert verdict_hash(judge(back)) == verdict_hash(judge(t))
    assert back.expected_violations == t.expected_violations
    assert back.origin == t.origin


@pytest.mark.parametrize("t", CORPUS, ids=[t.transcript_id for t in CORPUS])
def test_round_trip_is_a_fixed_point(t):
    """to_dict(from_dict(to_dict(t))) == to_dict(t): no drift on re-serialise."""
    d1 = kio.to_dict(t)
    d2 = kio.to_dict(kio.from_dict(json.loads(json.dumps(d1))))
    assert d1 == d2


def test_examples_dir_matches_corpus():
    """The shipped example files are exports, not hand-edited copies."""
    for path in EXAMPLES.glob("*.json"):
        t = kio.loads(path.read_text(encoding="utf-8"))
        assert t.transcript_id in BY_ID, path.name
        assert verdict_hash(judge(t)) == verdict_hash(judge(BY_ID[t.transcript_id]))


# ---------------------------------------------------------------------------
# Strictness: the parser refuses, it never guesses
# ---------------------------------------------------------------------------

def _demo() -> dict:
    return kio.to_dict(BY_ID["MEDIANAMA_DEMO"])


def test_unknown_top_level_key_is_rejected():
    d = _demo()
    d["consnet"] = "granted"
    with pytest.raises(kio.SchemaError, match=r"\$: unknown field"):
        kio.from_dict(d)


def test_typo_in_turn_flag_cannot_silently_pass():
    """`is_opt_out` instead of `is_optout` must refuse, not judge CLEAN."""
    d = _demo()
    d["turns"][1]["is_opt_out"] = True
    with pytest.raises(kio.SchemaError, match=r"turns\[1\]: unknown field"):
        kio.from_dict(d)


def test_missing_required_field_names_the_path():
    d = _demo()
    del d["turns"][0]["at"]
    with pytest.raises(kio.SchemaError, match=r"\$\.turns\[0\]: missing required field 'at'"):
        kio.from_dict(d)


def test_bad_enum_lists_valid_values():
    d = _demo()
    d["consent"] = "yes"
    with pytest.raises(kio.SchemaError, match=r"\$\.consent: 'yes' is not one of \['granted'"):
        kio.from_dict(d)


def test_bool_is_not_an_integer():
    d = _demo()
    d["turns"][0]["idx"] = True
    with pytest.raises(kio.SchemaError, match=r"turns\[0\]\.idx: expected integer"):
        kio.from_dict(d)


def test_bad_datetime_names_the_path():
    d = _demo()
    d["turns"][0]["at"] = "yesterday"
    with pytest.raises(kio.SchemaError, match=r"turns\[0\]\.at: not an ISO-8601"):
        kio.from_dict(d)


def test_schema_own_validation_is_wrapped_with_path():
    """Offer.__post_init__'s ValueError surfaces as SchemaError + JSON path."""
    d = _demo()
    d["turns"][2]["offer"]["discount_pct"] = 150
    with pytest.raises(kio.SchemaError, match=r"turns\[2\]\.offer: discount_pct must be within"):
        kio.from_dict(d)


def test_catalog_key_must_match_sku():
    d = _demo()
    d["catalog"]["SKU_OTHER"] = d["catalog"].pop("SKU_AIRFRYER")
    with pytest.raises(kio.SchemaError, match=r"catalog\['SKU_OTHER'\]: key .* != item\.sku"):
        kio.from_dict(d)


def test_pair_fields_must_be_pairs():
    d = _demo()
    d["turns"][0]["stock_claims"] = [["SKU_AIRFRYER", True, "extra"]]
    with pytest.raises(kio.SchemaError, match=r"stock_claims\[0\]: expected a 2-element array"):
        kio.from_dict(d)


def test_unsupported_schema_version():
    d = _demo()
    d["schema_version"] = 99
    with pytest.raises(kio.SchemaError, match="schema_version"):
        kio.from_dict(d)


def test_not_json_at_all():
    with pytest.raises(kio.SchemaError, match="not valid JSON"):
        kio.loads("{not json")


def test_defaults_are_optional_on_input():
    """A minimal turn (idx/actor/at) parses; every default is really a default."""
    d = _demo()
    d["turns"] = [{"idx": 0, "actor": "customer", "at": "2026-04-14T10:00:00"}]
    d.pop("origin"); d.pop("notes"); d.pop("expected_violations"); d.pop("meta")
    t = kio.from_dict(d)
    assert judge(t).passed


# ---------------------------------------------------------------------------
# CLI exit codes: the contract a CI gate relies on
# ---------------------------------------------------------------------------

def test_cli_blocked_exit_code_and_hash(capsys):
    rc = main(["judge", str(EXAMPLES / "medianama_demo.json")])
    out = capsys.readouterr().out
    assert rc == EXIT_BLOCKED
    assert "BLOCKED" in out
    assert verdict_hash(judge(BY_ID["MEDIANAMA_DEMO"])) in out


def test_cli_clean_exit_code(capsys):
    rc = main(["judge", str(EXAMPLES / "clean_overnight_window.json")])
    assert rc == EXIT_CLEAN
    assert "PASS" in capsys.readouterr().out


def test_cli_unparseable_is_not_a_pass(capsys, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"transcript_id": "x"}')
    rc = main(["judge", str(p)])
    err = capsys.readouterr().err
    assert rc == EXIT_UNPARSEABLE
    assert "REFUSED" in err and "catalog" in err


def test_cli_accepts_utf8_bom_file(capsys, tmp_path):
    """FAILURES.md #20: a transcript saved from Windows Notepad / Excel starts
    with a UTF-8 BOM. json.loads rejects it, so the CLI said REFUSED on a
    valid file. A clean transcript must still judge as PASS, same hash."""
    src = (EXAMPLES / "clean_overnight_window.json").read_bytes()
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + src)
    rc = main(["judge", str(p)])
    out = capsys.readouterr().out
    assert rc == EXIT_CLEAN
    assert verdict_hash(judge(BY_ID["HN_OVERNIGHT_WINDOW_2300_OK"])) in out


def test_cli_accepts_utf8_bom_on_stdin(capsys, monkeypatch):
    import io as _io
    src = (EXAMPLES / "medianama_demo.json").read_bytes()
    fake = _io.TextIOWrapper(_io.BytesIO(b"\xef\xbb\xbf" + src), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", fake)
    rc = main(["judge", "-"])
    assert rc == EXIT_BLOCKED
    assert "REFUSED" not in capsys.readouterr().err


def test_cli_json_output_is_machine_readable(capsys):
    rc = main(["judge", str(EXAMPLES / "medianama_demo.json"), "--json"])
    assert rc == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["rules_fired"] == ["DISCOUNT_CEILING", "ESCALATING_PRESSURE", "FALSE_URGENCY"]
    assert payload["hash"] == verdict_hash(judge(BY_ID["MEDIANAMA_DEMO"]))


def test_cli_json_error_shape(capsys):
    rc = main(["judge", "/nonexistent/file.json", "--json"])
    assert rc == EXIT_UNPARSEABLE
    payload = json.loads(capsys.readouterr().out)
    assert payload["judged"] is False and "error" in payload


def test_cli_export_then_judge_round_trip(capsys, tmp_path):
    rc = main(["export", "PROV_UNDER_CEILING_CAPTURE"])
    assert rc == EXIT_CLEAN
    p = tmp_path / "t.json"
    p.write_text(capsys.readouterr().out)
    rc = main(["judge", str(p), "--json"])
    assert rc == EXIT_BLOCKED
    assert "INJECTED_INSTRUCTION" in json.loads(capsys.readouterr().out)["rules_fired"]


def test_cli_export_unknown_id(capsys):
    assert main(["export", "NOPE"]) == EXIT_UNPARSEABLE
    assert "no corpus transcript" in capsys.readouterr().err


def test_cli_rules_lists_every_rule(capsys):
    from kasauti.rules.checkers import RULE_IDS
    assert main(["rules"]) == EXIT_CLEAN
    out = capsys.readouterr().out
    for rid in RULE_IDS:
        assert rid in out
