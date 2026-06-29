"""
Deterministic CBP 7501 extraction verifier.

Implements the gates described in self-healing-7501-agent.md.
Confidence is earned by arithmetic — not trusted from the model.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ── Tunable tolerances ────────────────────────────────────────────────────────
VALUE_RECON_TOLERANCE = 1.00          # dollars — Σ EV vs Box 35
DUTY_ABS_TOLERANCE = 0.02             # dollars — EV × rate vs printed duty
DUTY_PCT_TOLERANCE = 0.01             # 1%
TOTALS_TOLERANCE = 0.02               # dollars — duty + tax + other vs grand total
FATAL_CHECKS = {
    "DUTY_CROSSCHECK",
    "ENTERED_VALUE_NULL",
    "HTS_CODE_NULL",
    "NO_PRODUCT_HTS",
}
VESSEL_MODES = {"10", "11", "12"}

_HTS_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{4}$")


def validate_extraction(raw_data: dict) -> dict:
    """Run all document and line gates. Returns status + line_failures."""
    items = _extract_line_items(raw_data)
    shipment = _extract_shipment(raw_data)

    line_failures: List[dict] = []
    for item in items:
        item_no = _item_number(item)
        if not item_no or item_no.upper().startswith("INV"):
            continue
        failed, score = _validate_line(item, shipment)
        if failed:
            line_failures.append({
                "item_number": item_no,
                "failed_checks": failed,
                "line_score": score,
            })

    doc_checks = _validate_document(items, shipment)
    blocking = [k for k, v in doc_checks.items() if not v.get("pass")]

    has_fatal = any(
        any(c.split(":")[0] in FATAL_CHECKS for c in lf["failed_checks"])
        for lf in line_failures
    )

    if blocking or has_fatal:
        status = "RED"
    elif line_failures:
        status = "YELLOW"
    else:
        status = "GREEN"

    line_sum = round(sum(_entered_value(i) or 0 for i in items), 2)
    shipment_total = _shipment_total(shipment, raw_data)

    return {
        "status": status,
        "line_failures": line_failures,
        "document_checks": doc_checks,
        "blocking_gates": blocking,
        "reconciliation": {
            "line_sum": line_sum,
            "box35": shipment_total,
            "delta": round(shipment_total - line_sum, 2) if shipment_total is not None else None,
        },
        "mean_line_score": _mean_line_score(items, line_failures),
        "lines_below_0_8": sum(
            1 for lf in line_failures if lf.get("line_score") is not None and lf["line_score"] < 0.8
        ),
    }


def _validate_line(item: dict, shipment: dict) -> Tuple[List[str], Optional[float]]:
    failed: List[str] = []
    scores: List[float] = []

    ev = _entered_value(item)
    if ev is None:
        failed.append("ENTERED_VALUE_NULL")
        return failed, None

    part = item.get("PART_NUMBER")
    if part and _HTS_PATTERN.match(str(part).strip()):
        failed.append(f"PART_NUMBER_IS_HTS:{part}")

    coo = (item.get("COUNTRY_OF_ORIGIN") or item.get("country_of_origin") or "").strip()
    if not coo:
        failed.append("COO_MISSING")
        scores.append(0.30)
    elif coo.upper() == "MULTI":
        failed.append("COO_MULTI")
        scores.append(0.30)

    hts_rows = item.get("hts_data") or []
    product_hts = _product_hts_code(hts_rows)
    if not product_hts:
        failed.append("NO_PRODUCT_HTS")
    for row in hts_rows:
        if row.get("_is_fee"):
            continue
        code = str(row.get("HTS_US_CODE") or row.get("hts_code") or "").strip()
        if not code:
            failed.append("HTS_CODE_NULL")

    mfr = str(item.get("MANUFACTURER_ID") or item.get("manufacturer_id") or "").strip()
    if mfr and len(mfr) < 2:
        failed.append("MFR_ID_FORMAT")
        scores.append(0.30)

    duty_ok, duty_detail = _duty_crosscheck(ev, hts_rows)
    if not duty_ok:
        failed.append(duty_detail or "DUTY_CROSSCHECK")

    fatal = any(c.split(":")[0] in FATAL_CHECKS for c in failed)
    if fatal:
        return failed, None
    if failed:
        return failed, 0.30
    if scores:
        return failed, min(scores)
    return failed, 1.00


def _validate_document(items: List[dict], shipment: dict) -> Dict[str, dict]:
    checks: Dict[str, dict] = {}

    line_sum = sum(_entered_value(i) or 0 for i in items)
    box35 = _shipment_total(shipment, {})
    if box35 is not None:
        delta = abs(box35 - line_sum)
        checks["value_reconciliation"] = {
            "pass": delta <= VALUE_RECON_TOLERANCE,
            "line_sum": round(line_sum, 2),
            "box35": box35,
            "delta": round(box35 - line_sum, 2),
        }
    else:
        checks["value_reconciliation"] = {"pass": True, "note": "no shipment total"}

    duty = _float(shipment.get("TOTALS_DUTY") or shipment.get("totals_duty"))
    tax = _float(shipment.get("TOTALS_TAX") or shipment.get("totals_tax"))
    other = _float(shipment.get("TOTAL_OTHER_FEES") or shipment.get("total_other_fees"))
    grand = _float(shipment.get("DUTY_GRAND_TOTAL") or shipment.get("duty_grand_total"))
    if all(v is not None for v in (duty, tax, other, grand)):
        expected = duty + tax + other
        checks["totals_consistency"] = {
            "pass": abs(expected - grand) <= TOTALS_TOLERANCE,
            "duty": duty,
            "tax": tax,
            "other": other,
            "grand_total": grand,
            "expected": round(expected, 2),
        }
    else:
        checks["totals_consistency"] = {"pass": True, "note": "incomplete totals fields"}

    mode = str(shipment.get("MODE_OF_TRANSPORT") or shipment.get("mode_of_transport") or "").strip()
    if mode and mode not in VESSEL_MODES:
        hmf_total = 0.0
        for item in items:
            for row in item.get("hts_data") or []:
                hmf = row.get("HMF_FEE")
                if hmf is not None:
                    hmf_total += _float(hmf) or 0
        checks["hmf_by_mode"] = {
            "pass": abs(hmf_total) <= TOTALS_TOLERANCE,
            "mode": mode,
            "hmf_total": round(hmf_total, 2),
        }
    else:
        checks["hmf_by_mode"] = {"pass": True, "note": "vessel mode or unknown"}

    return checks


def _duty_crosscheck(ev: float, hts_rows: list) -> Tuple[bool, Optional[str]]:
    for row in hts_rows:
        if row.get("_is_fee"):
            continue
        rate_str = str(row.get("HTSUS_RATE") or row.get("htsus_rate") or "").strip()
        duty = _float(row.get("DUTY_AND_TAXES") or row.get("duty_and_taxes"))
        if duty is None:
            continue
        rate = _parse_rate(rate_str)
        if rate is None:
            continue
        expected = ev * rate
        code = row.get("HTS_US_CODE") or row.get("hts_code") or "?"
        if abs(expected - duty) > max(DUTY_ABS_TOLERANCE, abs(duty) * DUTY_PCT_TOLERANCE):
            return False, f"DUTY_CROSSCHECK:{code}:expected={expected:.2f},printed={duty:.2f}"
    return True, None


def _extract_line_items(raw_data: dict) -> List[dict]:
    if "entry_summary" in raw_data and "line_items" in raw_data.get("entry_summary", {}):
        return raw_data["entry_summary"]["line_items"]
    if "data" in raw_data and "entry_summary" in raw_data.get("data", {}):
        return raw_data["data"]["entry_summary"].get("line_items", [])
    if "line_items" in raw_data:
        return raw_data["line_items"]
    if "items" in raw_data:
        return raw_data["items"]
    return []


def _extract_shipment(raw_data: dict) -> dict:
    if "shipment" in raw_data:
        return raw_data["shipment"]
    if "entry_summary" in raw_data:
        return raw_data["entry_summary"]
    return raw_data


def _shipment_total(shipment: dict, raw_data: dict) -> Optional[float]:
    for key in ("TOTAL_ENTERED_VALUE", "total_entered_value", "entered_value_total"):
        val = shipment.get(key) or raw_data.get(key)
        if val is not None:
            return _float(val)
    vr = raw_data.get("validation_results", {})
    tvc = vr.get("total_value_check", {}) if isinstance(vr, dict) else {}
    if isinstance(tvc, dict) and tvc.get("shipment_total") is not None:
        return _float(tvc["shipment_total"])
    return None


def _entered_value(item: dict) -> Optional[float]:
    for field in ("ITEM_ENTERED_VALUE", "entered_value", "item_entered_value", "value"):
        if item.get(field) is not None:
            return _float(item[field])
    return None


def _item_number(item: dict) -> str:
    num = (item.get("ITEM_NUMBER") or item.get("line_number") or item.get("item_number") or "")
    return str(num).strip().zfill(3) if str(num).strip().isdigit() else str(num).strip()


def _product_hts_code(hts_rows: list) -> Optional[str]:
    codes = []
    for row in hts_rows:
        if row.get("_is_fee"):
            continue
        code = str(row.get("HTS_US_CODE") or row.get("hts_code") or "").strip()
        if code:
            codes.append(code)
    for code in reversed(codes):
        if not code.startswith("99"):
            return code
    return None


def _parse_rate(rate_str: str) -> Optional[float]:
    if not rate_str:
        return None
    upper = rate_str.upper()
    if upper in ("FREE", "N/A", "0", "0%"):
        return 0.0
    m = re.search(r"([\d.]+)\s*%", rate_str)
    if m:
        return float(m.group(1)) / 100.0
    return None


def _float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _mean_line_score(items: List[dict], line_failures: List[dict]) -> Optional[float]:
    failing = {lf["item_number"] for lf in line_failures}
    scores = []
    for item in items:
        num = _item_number(item)
        if num in failing:
            for lf in line_failures:
                if lf["item_number"] == num and lf.get("line_score") is not None:
                    scores.append(lf["line_score"])
                    break
            else:
                scores.append(0.0)
        elif num and not num.upper().startswith("INV"):
            scores.append(1.0)
    return round(sum(scores) / len(scores), 3) if scores else None
