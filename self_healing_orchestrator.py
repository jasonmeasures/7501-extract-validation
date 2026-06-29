"""
Self-Healing Orchestrator for CBP 7501 extraction.

Pipeline:
  A79/Gemini (primary extraction)
    → Validate (sequence gaps + missing values)
    → Claude (gap recovery — extracts items agent missed)
    → Claude (value recovery — fills null ITEM_ENTERED_VALUE)
    → Confidence score → escalate flag for human review

A79 stays authoritative for what it extracted.
Claude only adds what A79 missed or fills verified nulls.
"""

import json
import re
import time
import base64
import logging
from typing import Any, Dict, List, Optional

import requests

from validator_7501 import validate_extraction

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────
MAX_HEAL_ROUNDS = 3           # verify → repair passes (per self-healing spec)
ESCALATE_THRESHOLD = 88       # confidence % below which we flag for human review
MISSING_VAL_BATCH = 30        # max missing-value lines sent to Claude per call
GAP_ITEM_BATCH = 40           # max missing item numbers recovered per Claude call
CLAUDE_TIMEOUT = 120          # seconds per Claude API call


# ── Orchestrator ──────────────────────────────────────────────────────────────

class SelfHealingOrchestrator:
    """
    Wraps primary A79 extraction with Claude-powered validation and recovery.

    Usage (called from process_single_pdf):
        orch = SelfHealingOrchestrator(...)
        result = orch.run(filepath, filename, call_api_fn, parse_fn, audit_fn, normalizer_cls)
    """

    def __init__(
        self,
        a79_api_key: str,
        a79_url: str,
        a79_agent_name: str,
        a79_agent_id: str,
        a79_custom_instructions: str,
        claude_api_key: str,
        claude_model: str,
        self_healing_instructions: str = "",
    ):
        self.a79_api_key = a79_api_key
        self.a79_url = a79_url
        self.a79_agent_name = a79_agent_name
        self.a79_agent_id = a79_agent_id
        self.a79_custom_instructions = a79_custom_instructions
        self.claude_api_key = claude_api_key
        self.claude_model = claude_model
        self.self_healing_instructions = self_healing_instructions or ""
        self.heal_log: List[Dict] = []

    # ── Public entry point ────────────────────────────────────────────────────

    def run(
        self,
        filepath: str,
        filename: str,
        call_api_fn,
        parse_fn,
        audit_fn,
        normalizer_cls,
    ) -> Dict[str, Any]:
        """
        Full orchestration loop. Returns same shape as process_single_pdf()
        with additional 'confidence' and 'heal_log' keys.
        """
        self.heal_log = []

        with open(filepath, "rb") as fh:
            pdf_bytes = fh.read()
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

        print(f"\n{'='*70}")
        print(f"🤖 SELF-HEALING ORCHESTRATOR: {filename}")
        print(f"{'='*70}")

        # ── Step 1: primary A79 extraction ────────────────────────────────────
        print("\n   [1/4] Primary extraction via A79...")
        raw = call_api_fn(
            self.a79_api_key,
            self.a79_url,
            pdf_b64,
            self.a79_custom_instructions,
            self.a79_agent_name,
            None,
            "entire document",
            agent_id=self.a79_agent_id,
        )
        raw = parse_fn(raw)
        self._log("primary_extraction", {
            "item_count": len(raw.get("line_items", [])),
        })

        # ── Step 2: initial validation + deterministic verify ─────────────────
        print("\n   [2/4] Validating extraction...")
        audit = audit_fn(raw)
        verification = validate_extraction(raw)
        audit["verification"] = verification
        self._log("primary_verification", {
            "status": verification["status"],
            "line_failures": len(verification.get("line_failures", [])),
            "blocking_gates": verification.get("blocking_gates", []),
        })
        self._print_audit_summary(audit, "Initial")
        self._print_verification_summary(verification, "Initial")

        # ── Step 3: healing rounds (gap/value recovery + surgical repair) ───
        print(f"\n   [3/4] Self-healing (up to {MAX_HEAL_ROUNDS} rounds)...")
        for round_num in range(1, MAX_HEAL_ROUNDS + 1):
            gaps = audit.get("sequence_gaps", [])
            missing_vals = audit.get("missing_value_lines", [])
            line_failures = verification.get("line_failures", [])
            status = verification.get("status", "GREEN")

            if not gaps and not missing_vals and status in ("GREEN", "YELLOW"):
                print(f"      ✅ Nothing to heal after round {round_num - 1}")
                break

            print(f"\n      ── Round {round_num}/{MAX_HEAL_ROUNDS} ──")
            healed_anything = False

            # 3a. Recover items for missing item-number ranges
            if gaps and self.claude_api_key:
                recovered_items = self._heal_gaps(pdf_bytes, raw, gaps)
                if recovered_items:
                    raw = self._merge_items(raw, recovered_items)
                    healed_anything = True
                    self._log("gap_recovery", {
                        "round": round_num,
                        "gaps_targeted": gaps,
                        "items_recovered": len(recovered_items),
                        "recovered_numbers": [i.get("ITEM_NUMBER") for i in recovered_items],
                    })

            # 3b. Fill missing ITEM_ENTERED_VALUE fields
            missing_vals = audit.get("missing_value_lines", [])
            if missing_vals and self.claude_api_key:
                filled = self._heal_values(pdf_bytes, raw, missing_vals)
                if filled:
                    raw = self._apply_values(raw, filled)
                    healed_anything = True
                    self._log("value_recovery", {
                        "round": round_num,
                        "lines_targeted": len(missing_vals),
                        "values_filled": len(filled),
                        "filled_items": filled,
                    })

            # 3c. Surgical repair for validator-flagged lines
            if line_failures and status == "RED" and self.claude_api_key:
                repaired = self._heal_line_failures(pdf_bytes, raw, line_failures)
                if repaired:
                    raw = self._merge_repaired_items(raw, repaired)
                    healed_anything = True
                    self._log("line_repair", {
                        "round": round_num,
                        "lines_targeted": len(line_failures),
                        "lines_repaired": len(repaired),
                        "repaired_numbers": [i.get("ITEM_NUMBER") for i in repaired],
                    })

            if not healed_anything:
                print(f"      ⚠️  Claude could not recover additional data in round {round_num}")
                break

            # Re-validate
            audit = audit_fn(raw)
            verification = validate_extraction(raw)
            audit["verification"] = verification
            self._print_audit_summary(audit, f"After round {round_num}")
            self._print_verification_summary(verification, f"After round {round_num}")

        # ── Step 4: normalize + score ─────────────────────────────────────────
        print("\n   [4/4] Normalizing and scoring...")
        normalizer = normalizer_cls()
        normalized_data = normalizer.normalize(raw)

        confidence = self._score_confidence(audit, raw, verification)
        audit["confidence"] = confidence
        audit["heal_log"] = self.heal_log

        self._print_final_status(confidence)

        return {
            "success": True,
            "filename": filename,
            "raw_a79_data": raw,
            "extracted_data": raw,
            "normalized_data": normalized_data,
            "value_audit": audit,
            "row_count": len(normalized_data),
            "confidence": confidence,
            "heal_log": self.heal_log,
        }

    # ── Line integrity repair ─────────────────────────────────────────────────

    def _heal_line_failures(self, pdf_bytes: bytes, raw: dict, line_failures: list) -> list:
        """Surgical repair pass for lines that failed deterministic validator gates."""
        batch = line_failures[:MISSING_VAL_BATCH]
        if not batch:
            return []

        print(f"      🔧 Line repair: asking Claude to fix {len(batch)} failing line(s)...")

        items_by_num = {
            str(i.get("ITEM_NUMBER", "")).strip().zfill(3): i
            for i in raw.get("line_items", [])
            if str(i.get("ITEM_NUMBER", "")).strip()
        }

        blocks = []
        for lf in batch:
            num = lf["item_number"]
            item = items_by_num.get(num, {})
            blocks.append(
                f"--- LINE {num} ---\n"
                f"failed_checks: {json.dumps(lf['failed_checks'])}\n"
                f"current_extraction:\n{json.dumps(item, indent=2)}"
            )

        repair_rules = (
            "Apply these rules (the ones relevant to the failures):\n"
            "- STACKED HTS: Chapter-99 codes (9903.xx.xx) first, product classification "
            "(Ch 1-97) LAST. Every code is its own hts_data row. Product code is NEVER PART_NUMBER.\n"
            "- PART_NUMBER: only a printed style/SKU; if none printed, return null. "
            "A code shaped NNNN.NN.NNNN is an HTS code, not a part number.\n"
            "- ENTERED VALUE: the integer in the Entered Value column. "
            "It MUST satisfy EV × rate = printed duty for every percentage row.\n"
        )

        system = (
            "You are a CBP Form 7501 customs entry extraction specialist performing a "
            "surgical repair pass. Return ONLY valid JSON — no explanatory text."
        )

        user_content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(pdf_bytes).decode("utf-8"),
                },
            },
            {
                "type": "text",
                "text": (
                    "You previously extracted line(s) from a CBP 7501 entry. A deterministic "
                    "check found specific errors. Re-extract ONLY the line(s) below, fixing "
                    "exactly the named errors.\n\n"
                    f"{repair_rules}\n"
                    + "\n\n".join(blocks)
                    + "\n\nReturn corrected JSON for ONLY these line(s) as a JSON array, "
                    "same schema, with recomputed _confidence."
                ),
            },
        ]

        try:
            text = self._call_claude(system, user_content)
            items = self._extract_json(text)
            if not isinstance(items, list):
                items = [items]

            wanted = {lf["item_number"] for lf in batch}
            valid = []
            for item in items:
                num = str(item.get("ITEM_NUMBER", "")).strip().zfill(3)
                if num in wanted:
                    item["ITEM_NUMBER"] = num
                    item["_healed_by"] = "claude_line_repair"
                    valid.append(item)

            print(f"      ✅ Line repair: Claude corrected {len(valid)}/{len(batch)} line(s)")
            return valid

        except Exception as exc:
            print(f"      ⚠️  Line repair failed: {exc}")
            self._log("line_repair_error", {"error": str(exc)})
            return []

    def _merge_repaired_items(self, raw: dict, repaired_items: list) -> dict:
        """Replace line items by ITEM_NUMBER with repaired versions."""
        if not repaired_items:
            return raw

        by_num = {
            str(i.get("ITEM_NUMBER", "")).strip().zfill(3): i
            for i in repaired_items
        }
        merged = []
        for item in raw.get("line_items", []):
            num = str(item.get("ITEM_NUMBER", "")).strip().zfill(3)
            merged.append(by_num.pop(num, item))
        result = dict(raw)
        result["line_items"] = merged
        return result

    # ── Claude ────────────────────────────────────────────────────────────────

    def _call_claude(self, system: str, user_content: list) -> str:
        """
        POST to Claude API. user_content is a list of content blocks
        (text, document, etc.) as per the Messages API spec.
        """
        if not self.claude_api_key:
            raise ValueError("CLAUDE_API_KEY is not set — cannot call Claude for healing")

        payload = {
            "model": self.claude_model,
            "max_tokens": 8192,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
        }
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.claude_api_key,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "pdfs-2024-09-25",
                "content-type": "application/json",
            },
            json=payload,
            timeout=CLAUDE_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

    @staticmethod
    def _extract_json(text: str):
        """
        Pull JSON out of Claude's response, which may be wrapped in markdown fences.
        Returns the parsed object/list, or raises ValueError.
        """
        # Strip markdown fences if present
        clean = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
        # Find the first JSON object or array
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
        if match:
            return json.loads(match.group(1))
        return json.loads(clean)

    # ── Gap recovery ──────────────────────────────────────────────────────────

    def _heal_gaps(self, pdf_bytes: bytes, raw: dict, gaps: list) -> list:
        """
        Ask Claude to extract specific missing item-number ranges from the PDF.
        Returns list of recovered item dicts (A79 format).
        """
        # Build a flat list of item numbers we want, capped at GAP_ITEM_BATCH
        wanted = []
        for gap in gaps:
            for n in range(gap["from"], gap["to"] + 1):
                wanted.append(str(n).zfill(3))
                if len(wanted) >= GAP_ITEM_BATCH:
                    break
            if len(wanted) >= GAP_ITEM_BATCH:
                break

        if not wanted:
            return []

        range_desc = self._ranges_to_text(gaps)
        print(f"      🔍 Gap recovery: asking Claude for items {range_desc} ({len(wanted)} item numbers)...")

        # Pull a sample item from A79's result so Claude knows the exact format
        sample_items = raw.get("line_items", [])[:2]
        schema_example = json.dumps(sample_items, indent=2) if sample_items else "[]"

        system = (
            "You are a CBP Form 7501 customs entry extraction specialist. "
            "You extract structured data from official US customs entry documents. "
            "Return ONLY valid JSON — no explanatory text, no markdown outside code fences."
        )

        user_content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(pdf_bytes).decode("utf-8"),
                },
            },
            {
                "type": "text",
                "text": f"""A prior extraction of this CBP Form 7501 is MISSING these line items:
{range_desc} (item numbers: {', '.join(wanted[:20])}{'...' if len(wanted) > 20 else ''})

Extract ONLY the missing items listed above. For each item found on the form, return a JSON object matching this exact schema (taken from successfully extracted items):

{schema_example}

Critical rules:
- Use the SAME field names and structure shown above (ITEM_NUMBER, PRODUCT_DESCRIPTION, ITEM_ENTERED_VALUE, hts_data, etc.)
- ITEM_ENTERED_VALUE must be a number (float) or null — never a string
- hts_data must be a JSON array; each entry needs HTS_US_CODE
- If an item number is not found on the form, omit it — do not invent data
- Return a JSON array of item objects, even if only one item is found
- Return an empty array [] if none of the requested items appear on the form

Return ONLY the JSON array.""",
            },
        ]

        try:
            text = self._call_claude(system, user_content)
            items = self._extract_json(text)
            if not isinstance(items, list):
                items = [items]

            # Validate: only keep items with a recognizable ITEM_NUMBER
            wanted_set = set(wanted)
            valid = []
            for item in items:
                num = str(item.get("ITEM_NUMBER", "")).strip().zfill(3)
                if num in wanted_set:
                    item["ITEM_NUMBER"] = num
                    item["_healed_by"] = "claude_gap_recovery"
                    valid.append(item)

            print(f"      ✅ Gap recovery: Claude found {len(valid)}/{len(wanted)} missing items")
            return valid

        except Exception as exc:
            print(f"      ⚠️  Gap recovery failed: {exc}")
            self._log("gap_recovery_error", {"error": str(exc), "gaps": gaps})
            return []

    # ── Value recovery ────────────────────────────────────────────────────────

    def _heal_values(self, pdf_bytes: bytes, raw: dict, missing_lines: list) -> dict:
        """
        Ask Claude to find ITEM_ENTERED_VALUE for lines where A79 returned null.
        Returns dict: {item_number_str: float_value}.
        """
        # Cap the batch
        batch = missing_lines[:MISSING_VAL_BATCH]
        print(f"      🔍 Value recovery: asking Claude for {len(batch)} null-value lines...")

        lines_json = json.dumps([
            {
                "ITEM_NUMBER": m["line_number"],
                "PRODUCT_DESCRIPTION": m["description"],
                "HTS_CODE": m["hts_code"],
            }
            for m in batch
        ], indent=2)

        # Also pass current value totals for context
        vr = raw.get("validation_results", {}).get("total_value_check", {})
        total_context = ""
        if isinstance(vr, dict) and vr.get("shipment_total"):
            total_context = (
                f"\nFor reference, the shipment's TOTAL_ENTERED_VALUE on the form is "
                f"${vr['shipment_total']:,.2f}. The sum of already-extracted line values is "
                f"${vr.get('line_sum', 0):,.2f}."
            )

        system = (
            "You are a CBP Form 7501 customs entry value specialist. "
            "You find the declared customs entered value (ITEM_ENTERED_VALUE) for specific "
            "line items on US customs entry summaries. "
            "Return ONLY valid JSON — no explanatory text."
        )

        user_content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(pdf_bytes).decode("utf-8"),
                },
            },
            {
                "type": "text",
                "text": f"""These line items were extracted from this CBP Form 7501 but their ITEM_ENTERED_VALUE (customs entered value in USD) could not be determined:

{lines_json}{total_context}

For EACH item, locate its entered/customs value on the form. This is typically labeled "Entered Value", "Invoice Value", or appears in a value column on the entry summary or attached commercial invoice.

Return a JSON object mapping ITEM_NUMBER to its numeric value:
{{"001": 8240.00, "088": 350.00, ...}}

Rules:
- Values must be numbers (float), not strings
- Omit an item if you genuinely cannot find its value on the form — do not guess
- Do NOT round or estimate — use the exact figure shown
- Return ONLY the JSON object""",
            },
        ]

        try:
            text = self._call_claude(system, user_content)
            filled = self._extract_json(text)
            if not isinstance(filled, dict):
                raise ValueError(f"Expected dict, got {type(filled).__name__}")

            # Validate: numeric values only
            clean = {}
            for k, v in filled.items():
                try:
                    clean[str(k).strip().zfill(3)] = float(v)
                except (TypeError, ValueError):
                    pass

            print(f"      ✅ Value recovery: Claude filled {len(clean)}/{len(batch)} values")
            return clean

        except Exception as exc:
            print(f"      ⚠️  Value recovery failed: {exc}")
            self._log("value_recovery_error", {"error": str(exc)})
            return {}

    # ── Merge helpers ─────────────────────────────────────────────────────────

    def _merge_items(self, raw: dict, new_items: list) -> dict:
        """
        Insert Claude-recovered items into raw['line_items'].
        Existing items from A79 are never overwritten.
        Items are inserted in numeric ITEM_NUMBER order.
        """
        existing_nums = {
            str(i.get("ITEM_NUMBER", "")).strip().zfill(3)
            for i in raw.get("line_items", [])
        }
        added = [i for i in new_items if str(i.get("ITEM_NUMBER", "")).strip().zfill(3) not in existing_nums]
        if not added:
            return raw

        merged = list(raw.get("line_items", [])) + added
        merged.sort(key=lambda i: int(str(i.get("ITEM_NUMBER", "0")).strip().lstrip("0") or "0"))

        result = dict(raw)
        result["line_items"] = merged
        return result

    def _apply_values(self, raw: dict, filled: dict) -> dict:
        """
        Set ITEM_ENTERED_VALUE on existing line items where Claude found a value.
        Only updates items that currently have a null value — never overwrites a real value.
        """
        if not filled:
            return raw

        result = dict(raw)
        updated_items = []
        for item in result.get("line_items", []):
            num = str(item.get("ITEM_NUMBER", "")).strip().zfill(3)
            if num in filled and item.get("ITEM_ENTERED_VALUE") is None:
                item = dict(item)
                item["ITEM_ENTERED_VALUE"] = filled[num]
                item["_value_healed"] = True
            updated_items.append(item)

        result["line_items"] = updated_items
        return result

    # ── Confidence scoring ────────────────────────────────────────────────────

    def _score_confidence(self, audit: dict, raw: dict, verification: Optional[dict] = None) -> dict:
        """
        Multi-dimensional confidence score (0–100).

        Dimensions:
          - Sequence completeness  (40 pts): no gaps in item numbering
          - Value completeness     (40 pts): % of items with entered value
          - Sum reconciliation     (20 pts): line_sum matches shipment_total
        """
        items = raw.get("line_items", [])
        total = len(items) or 1
        breakdown = {}

        # Sequence completeness
        gaps = audit.get("sequence_gaps", [])
        gap_count = sum(g["count"] for g in gaps)
        seq_score = max(0, 40 - int((gap_count / total) * 40)) if gaps else 40
        breakdown["sequence"] = {
            "score": seq_score,
            "max": 40,
            "gaps": gaps,
            "gap_items": gap_count,
        }

        # Value completeness
        missing_count = len(audit.get("missing_value_lines", []))
        val_score = round(((total - missing_count) / total) * 40)
        breakdown["values"] = {
            "score": val_score,
            "max": 40,
            "total_items": total,
            "missing_values": missing_count,
        }

        # Sum reconciliation
        gap_dollars = audit.get("gap")
        shipment_total = audit.get("shipment_total") or 0
        if audit.get("match"):
            sum_score = 20
            sum_detail = "match"
        elif gap_dollars is not None and shipment_total > 0:
            pct_off = abs(gap_dollars) / shipment_total
            sum_score = max(0, round(20 * (1 - pct_off * 5)))
            sum_detail = f"${abs(gap_dollars):,.2f} gap ({pct_off*100:.1f}% of total)"
        else:
            sum_score = 10  # partial credit — no total to compare against
            sum_detail = "no shipment total"
        breakdown["reconciliation"] = {
            "score": sum_score,
            "max": 20,
            "detail": sum_detail,
        }

        total_score = seq_score + val_score + sum_score

        # Penalize RED verification status
        if verification and verification.get("status") == "RED":
            total_score = min(total_score, ESCALATE_THRESHOLD - 1)

        # Escalation decision
        escalate = total_score < ESCALATE_THRESHOLD
        reasons = []
        if gap_count > 0:
            ranges = self._ranges_to_text(gaps)
            reasons.append(f"Items {ranges} not found in extraction")
        if missing_count > 0:
            pct = round(missing_count / total * 100)
            reasons.append(f"{missing_count} items ({pct}%) still missing entered value")
        if audit.get("gap") and abs(audit["gap"]) > 0.01:
            reasons.append(f"${abs(audit['gap']):,.2f} gap vs shipment total")

        if verification and verification.get("status") == "RED":
            reasons.append(f"Verification status RED ({len(verification.get('line_failures', []))} failing lines)")

        label = "High" if total_score >= 90 else "Medium" if total_score >= 70 else "Low"
        color = "#16A34A" if total_score >= 90 else "#D97706" if total_score >= 70 else "#DC2626"
        bg = "#DCFCE7" if total_score >= 90 else "#FEF3C7" if total_score >= 70 else "#FEE2E2"

        healed_items = sum(
            1 for entry in self.heal_log
            if entry["event"] in ("gap_recovery", "value_recovery")
            and entry["data"].get("items_recovered", 0) + entry["data"].get("values_filled", 0) > 0
        )

        return {
            "score": total_score,
            "label": label,
            "color": color,
            "bg": bg,
            "escalate": escalate,
            "reasons": reasons,
            "breakdown": breakdown,
            "healed_items": healed_items,
            "verification_status": (verification or {}).get("status"),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, event: str, data: dict):
        self.heal_log.append({"event": event, "ts": time.time(), "data": data})

    @staticmethod
    def _ranges_to_text(gaps: list) -> str:
        parts = []
        for g in gaps:
            parts.append(f"{g['from']}–{g['to']}" if g["from"] != g["to"] else str(g["from"]))
        return ", ".join(parts)

    @staticmethod
    def _print_verification_summary(verification: dict, label: str):
        status = verification.get("status", "?")
        failures = len(verification.get("line_failures", []))
        blocking = verification.get("blocking_gates", [])
        mean = verification.get("mean_line_score")
        mean_str = f"{mean:.3f}" if mean is not None else "n/a"
        block_str = f"  |  blocking: {', '.join(blocking)}" if blocking else ""
        print(
            f"      🔍 {label} verify: status={status}  |  "
            f"{failures} line failure(s)  |  mean_score={mean_str}{block_str}"
        )

    @staticmethod
    def _print_audit_summary(audit: dict, label: str):
        gaps = audit.get("sequence_gaps", [])
        missing = len(audit.get("missing_value_lines", []))
        line_sum = audit.get("line_sum", 0)
        total = audit.get("shipment_total")
        gap_dollars = audit.get("gap")
        gap_str = f"  |  gap ${abs(gap_dollars):,.2f}" if gap_dollars else ""
        print(
            f"      📊 {label}: "
            f"{len(audit.get('line_breakdown', []))} items  |  "
            f"{len(gaps)} seq gap(s)  |  "
            f"{missing} missing values  |  "
            f"line sum ${line_sum:,.2f}"
            f"{gap_str}"
        )

    @staticmethod
    def _print_final_status(confidence: dict):
        score = confidence["score"]
        label = confidence["label"]
        escalate = confidence["escalate"]
        healed = confidence.get("healed_items", 0)
        flag = "🚩 HUMAN REVIEW FLAGGED" if escalate else "✅ Passes confidence threshold"
        print(f"\n   🎯 Confidence: {score}% ({label})  |  Items healed: {healed}  |  {flag}")
        if escalate and confidence.get("reasons"):
            for r in confidence["reasons"]:
                print(f"      • {r}")
