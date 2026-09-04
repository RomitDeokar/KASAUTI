"""
JSON in, JSON out. The boundary between KASAUTI and everything else.

Until this module existed, the only transcripts the engine could judge were
the 98 it shipped with. A reviewer who asked "can I run this on MY agent's
transcript?" had to write Python dataclasses by hand. That made KASAUTI a
test suite *about its own corpus*, not a tool. This file is the difference.

Design constraints, in order:

  1. **Strict.** Unknown keys, wrong types, unknown enum values, and missing
     required fields raise `SchemaError` naming the exact path
     (`turns[3].offer.discount_pct`). A transcript that half-parses and gets
     judged CLEAN is worse than one that refuses to parse. Same philosophy as
     `_reject_if_degenerate` in consortium.py: fail where the bug is.

  2. **Lossless.** `from_dict(to_dict(t))` reproduces the same verdict hash
     for all 98 corpus transcripts. Asserted in tests/test_io.py, not claimed.

  3. **Pure.** No clock, no network, no RNG. `datetime.fromisoformat` only.
     The purity guard does not run over this module (it is not a checker),
     but nothing here would fail it.

The JSON shape is exactly the dataclass shape in schema.py with enums as
their string values, datetimes as ISO-8601 strings, and tuples as lists.
See examples/transcript.json for a complete instance.
"""
from __future__ import annotations

from dataclasses import fields as _dc_fields
from datetime import datetime
from enum import Enum
from typing import Any

from .schema import (
    Actor,
    CatalogItem,
    Channel,
    ConsentState,
    MerchantPolicy,
    Offer,
    Provenance,
    RetryAttempt,
    Transcript,
    Turn,
)

SCHEMA_VERSION = 1


class SchemaError(ValueError):
    """Raised for any malformed input, with the JSON path in the message."""


# ---------------------------------------------------------------------------
# Serialise
# ---------------------------------------------------------------------------

def _enc(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return {f.name: _enc(getattr(v, f.name)) for f in _dc_fields(v)}
    return v


def to_dict(t: Transcript) -> dict:
    """Transcript -> plain JSON-able dict. Inverse of from_dict."""
    d = _enc(t)
    d["schema_version"] = SCHEMA_VERSION
    return d


# ---------------------------------------------------------------------------
# Deserialise (strict)
# ---------------------------------------------------------------------------

def _need(d: dict, key: str, path: str) -> Any:
    if key not in d:
        raise SchemaError(f"{path}: missing required field {key!r}")
    return d[key]


def _obj(v: Any, path: str) -> dict:
    if not isinstance(v, dict):
        raise SchemaError(f"{path}: expected object, got {type(v).__name__}")
    return v


def _reject_unknown(d: dict, allowed: set[str], path: str) -> None:
    extra = sorted(set(d) - allowed)
    if extra:
        raise SchemaError(
            f"{path}: unknown field(s) {extra}. Unknown keys are rejected "
            f"rather than ignored so a typo like 'is_optout' -> 'is_opt_out' "
            f"cannot silently turn a violation into a CLEAN verdict."
        )


def _str(v: Any, path: str) -> str:
    if not isinstance(v, str):
        raise SchemaError(f"{path}: expected string, got {type(v).__name__}")
    return v


def _int(v: Any, path: str) -> int:
    # bool is an int subclass in Python; a JSON `true` is never a valid count.
    if isinstance(v, bool) or not isinstance(v, int):
        raise SchemaError(f"{path}: expected integer, got {type(v).__name__}")
    return v


def _num(v: Any, path: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SchemaError(f"{path}: expected number, got {type(v).__name__}")
    return float(v)


def _bool(v: Any, path: str) -> bool:
    if not isinstance(v, bool):
        raise SchemaError(f"{path}: expected boolean, got {type(v).__name__}")
    return v


def _dt(v: Any, path: str) -> datetime:
    s = _str(v, path)
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise SchemaError(f"{path}: not an ISO-8601 datetime: {s!r} ({e})") from e


def _opt(v: Any, fn, path: str):
    return None if v is None else fn(v, path)


def _enum(cls, v: Any, path: str):
    s = _str(v, path)
    try:
        return cls(s)
    except ValueError:
        valid = [m.value for m in cls]
        raise SchemaError(f"{path}: {s!r} is not one of {valid}") from None


def _list(v: Any, path: str) -> list:
    if not isinstance(v, list):
        raise SchemaError(f"{path}: expected array, got {type(v).__name__}")
    return v


def _wrap(fn, path: str):
    """Re-raise the schema's own ValueError (e.g. discount_pct out of range)
    as a SchemaError carrying the JSON path, so the caller sees one type."""
    try:
        return fn()
    except SchemaError:
        raise
    except ValueError as e:
        raise SchemaError(f"{path}: {e}") from e


_POLICY_KEYS = {
    "merchant_id", "max_discount_pct", "contact_window_start_hour",
    "contact_window_end_hour", "allowed_channels", "pre_debit_notice_hours",
    "mandate_retry_cap", "mandate_retry_window_days",
}
_ITEM_KEYS = {"sku", "name", "price_paise", "in_stock", "offer_expires_at", "mrp_paise"}
_OFFER_KEYS = {"sku", "discount_pct", "claimed_expires_at"}
_RETRY_KEYS = {
    "mandate_id", "amount_paise", "mandate_cap_paise", "attempt_number",
    "first_attempt_at", "notified_at",
}
_TURN_KEYS = {
    "idx", "actor", "at", "text", "channel", "offer", "is_refusal",
    "is_optout", "is_hardship_signal", "retry", "price_claims_paise",
    "stock_claims", "context_sources", "action_authority",
}
_TRANSCRIPT_KEYS = {
    "schema_version", "transcript_id", "merchant", "catalog", "consent",
    "turns", "origin", "expected_violations", "notes", "meta",
}


def _policy(v: Any, path: str) -> MerchantPolicy:
    d = _obj(v, path)
    _reject_unknown(d, _POLICY_KEYS, path)
    kw: dict[str, Any] = {
        "merchant_id": _str(_need(d, "merchant_id", path), f"{path}.merchant_id"),
        "max_discount_pct": _num(_need(d, "max_discount_pct", path), f"{path}.max_discount_pct"),
    }
    for k in ("contact_window_start_hour", "contact_window_end_hour",
              "pre_debit_notice_hours", "mandate_retry_cap", "mandate_retry_window_days"):
        if k in d:
            kw[k] = _int(d[k], f"{path}.{k}")
    if "allowed_channels" in d:
        kw["allowed_channels"] = tuple(
            _enum(Channel, c, f"{path}.allowed_channels[{i}]")
            for i, c in enumerate(_list(d["allowed_channels"], f"{path}.allowed_channels"))
        )
    return _wrap(lambda: MerchantPolicy(**kw), path)


def _item(v: Any, path: str) -> CatalogItem:
    d = _obj(v, path)
    _reject_unknown(d, _ITEM_KEYS, path)
    return _wrap(lambda: CatalogItem(
        sku=_str(_need(d, "sku", path), f"{path}.sku"),
        name=_str(_need(d, "name", path), f"{path}.name"),
        price_paise=_int(_need(d, "price_paise", path), f"{path}.price_paise"),
        in_stock=_bool(_need(d, "in_stock", path), f"{path}.in_stock"),
        offer_expires_at=_opt(d.get("offer_expires_at"), _dt, f"{path}.offer_expires_at"),
        mrp_paise=_opt(d.get("mrp_paise"), _int, f"{path}.mrp_paise"),
    ), path)


def _offer(v: Any, path: str) -> Offer:
    d = _obj(v, path)
    _reject_unknown(d, _OFFER_KEYS, path)
    return _wrap(lambda: Offer(
        sku=_str(_need(d, "sku", path), f"{path}.sku"),
        discount_pct=_num(_need(d, "discount_pct", path), f"{path}.discount_pct"),
        claimed_expires_at=_opt(d.get("claimed_expires_at"), _dt, f"{path}.claimed_expires_at"),
    ), path)


def _retry(v: Any, path: str) -> RetryAttempt:
    d = _obj(v, path)
    _reject_unknown(d, _RETRY_KEYS, path)
    return _wrap(lambda: RetryAttempt(
        mandate_id=_str(_need(d, "mandate_id", path), f"{path}.mandate_id"),
        amount_paise=_int(_need(d, "amount_paise", path), f"{path}.amount_paise"),
        mandate_cap_paise=_int(_need(d, "mandate_cap_paise", path), f"{path}.mandate_cap_paise"),
        attempt_number=_int(_need(d, "attempt_number", path), f"{path}.attempt_number"),
        first_attempt_at=_dt(_need(d, "first_attempt_at", path), f"{path}.first_attempt_at"),
        notified_at=_opt(_need(d, "notified_at", path), _dt, f"{path}.notified_at"),
    ), path)


def _pair(v: Any, path: str) -> list:
    lst = _list(v, path)
    if len(lst) != 2:
        raise SchemaError(f"{path}: expected a 2-element array, got {len(lst)}")
    return lst


def _turn(v: Any, path: str) -> Turn:
    d = _obj(v, path)
    _reject_unknown(d, _TURN_KEYS, path)
    kw: dict[str, Any] = {
        "idx": _int(_need(d, "idx", path), f"{path}.idx"),
        "actor": _enum(Actor, _need(d, "actor", path), f"{path}.actor"),
        "at": _dt(_need(d, "at", path), f"{path}.at"),
    }
    if "text" in d:
        kw["text"] = _str(d["text"], f"{path}.text")
    if "channel" in d:
        kw["channel"] = _opt(d["channel"], lambda x, p: _enum(Channel, x, p), f"{path}.channel")
    if "offer" in d:
        kw["offer"] = _opt(d["offer"], _offer, f"{path}.offer")
    if "retry" in d:
        kw["retry"] = _opt(d["retry"], _retry, f"{path}.retry")
    for k in ("is_refusal", "is_optout", "is_hardship_signal"):
        if k in d:
            kw[k] = _bool(d[k], f"{path}.{k}")
    if "price_claims_paise" in d:
        kw["price_claims_paise"] = tuple(
            _int(x, f"{path}.price_claims_paise[{i}]")
            for i, x in enumerate(_list(d["price_claims_paise"], f"{path}.price_claims_paise"))
        )
    if "stock_claims" in d:
        out = []
        for i, pr in enumerate(_list(d["stock_claims"], f"{path}.stock_claims")):
            p = f"{path}.stock_claims[{i}]"
            a, b = _pair(pr, p)
            out.append((_str(a, f"{p}[0]"), _bool(b, f"{p}[1]")))
        kw["stock_claims"] = tuple(out)
    if "context_sources" in d:
        out = []
        for i, pr in enumerate(_list(d["context_sources"], f"{path}.context_sources")):
            p = f"{path}.context_sources[{i}]"
            a, b = _pair(pr, p)
            out.append((_enum(Provenance, a, f"{p}[0]"), _str(b, f"{p}[1]")))
        kw["context_sources"] = tuple(out)
    if "action_authority" in d:
        kw["action_authority"] = _opt(
            d["action_authority"], lambda x, p: _enum(Provenance, x, p), f"{path}.action_authority")
    return _wrap(lambda: Turn(**kw), path)


def from_dict(d: Any) -> Transcript:
    """Strict JSON dict -> Transcript. Raises SchemaError with a JSON path."""
    path = "$"
    d = _obj(d, path)
    _reject_unknown(d, _TRANSCRIPT_KEYS, path)
    ver = d.get("schema_version", SCHEMA_VERSION)
    if ver != SCHEMA_VERSION:
        raise SchemaError(f"$.schema_version: {ver!r} unsupported; this build reads {SCHEMA_VERSION}")

    catalog_raw = _obj(_need(d, "catalog", path), "$.catalog")
    catalog: dict[str, CatalogItem] = {}
    for k, item in catalog_raw.items():
        ci = _item(item, f"$.catalog[{k!r}]")
        if ci.sku != k:
            raise SchemaError(
                f"$.catalog[{k!r}]: key {k!r} != item.sku {ci.sku!r}. Rules look "
                f"SKUs up by key; a mismatch would make a real offer look phantom.")
        catalog[k] = ci

    turns = [_turn(x, f"$.turns[{i}]") for i, x in enumerate(_list(_need(d, "turns", path), "$.turns"))]

    kw: dict[str, Any] = {
        "transcript_id": _str(_need(d, "transcript_id", path), "$.transcript_id"),
        "merchant": _policy(_need(d, "merchant", path), "$.merchant"),
        "catalog": catalog,
        "consent": _enum(ConsentState, _need(d, "consent", path), "$.consent"),
        "turns": turns,
    }
    if "origin" in d:
        kw["origin"] = _str(d["origin"], "$.origin")
    if "notes" in d:
        kw["notes"] = _str(d["notes"], "$.notes")
    if "expected_violations" in d:
        kw["expected_violations"] = tuple(
            _str(x, f"$.expected_violations[{i}]")
            for i, x in enumerate(_list(d["expected_violations"], "$.expected_violations"))
        )
    if "meta" in d:
        kw["meta"] = dict(_obj(d["meta"], "$.meta"))
    return _wrap(lambda: Transcript(**kw), path)


def loads(text: str) -> Transcript:
    import json
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SchemaError(f"$: not valid JSON: {e}") from e
    return from_dict(data)


def dumps(t: Transcript, *, indent: int | None = 2) -> str:
    import json
    return json.dumps(to_dict(t), indent=indent, sort_keys=False, ensure_ascii=False)
