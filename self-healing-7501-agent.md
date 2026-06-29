# Self-Healing CBP 7501 Extraction Agent
**Version A1.0 · Companion to `apparel-7501-extraction-prompt.md` and `validator_7501.py`**

## The core idea

Extraction LLMs fail probabilistically and silently. A bigger prompt doesn't fix that —
it just moves the failures around. What fixes it is a loop where a **deterministic verifier
gates the output** and a **surgical repair pass** re-prompts the model only on the lines
that actually broke. The model proposes; arithmetic disposes. Confidence is not the model's
opinion of itself — it is whether `value × rate` reproduces the printed duty.

This agent ran against your current Zara output and, with no human in the loop, would have
flagged it `RED` immediately: value reconciliation off by **$3,780**, totals double-counted
by **$139.17**, and **264 of 297 lines** failing the HTS-integrity checks (mean line
confidence 0.115). None of that should ever reach a customer-facing dataset unlabeled.

---

## Architecture — a generate → verify → repair loop

```
            ┌──────────────────────────────────────────────────────────┐
            │                                                          │
  PDF ──►  EXTRACT  ──►  VERIFY  ──►  status?                          │
          (LLM, the     (validator_                                    │
           A1.0 prompt)  7501.py)      │                               │
                                       ├─ GREEN ──►  EMIT (clean)       │
                                       ├─ YELLOW ─►  EMIT + flag list   │
                                       └─ RED / fatal lines             │
                                              │                         │
                                              ▼                         │
                                     BUILD REPAIR PACKET                │
                                     (only the failing lines +          │
                                      the exact checks they failed +    │
                                      their raw source rows)            │
                                              │                         │
                                              ▼                         │
                                       REPAIR PASS (LLM) ───────────────┘
                                       (re-extract ONLY those lines)
                                              │
                                    pass N exhausted?
                                              │
                                              ▼
                                     ESCALATE to human queue
                                     (discrepancy report attached)
```

Five components, each with a single responsibility:

1. **Extractor** — the A1.0 prompt. One job: produce the JSON with honest per-line and
   per-document confidence.
2. **Verifier** — `validator_7501.py`. Pure, deterministic. Recomputes the arithmetic,
   assigns earned confidence, returns `status` + `line_failures` naming exactly which lines
   failed which checks. This is the trust anchor; it never calls a model.
3. **Repair-packet builder** — for each failing line, pull its raw source rows from the PDF
   text (by `ITEM_NUMBER`) and pair them with the specific failed-check names.
4. **Repair pass** — re-prompts the LLM with *only* the failing lines and the targeted
   correction rules. Small input, focused task, far higher hit rate than re-running 300
   lines.
5. **Escalator** — anything still `RED` after `MAX_PASSES` goes to a human queue with a
   discrepancy report, never silently into the dataset.

---

## The verify stage (what's already built)

`validator_7501.py` runs these gates. All tolerances are explicit and tunable at the top of
the file.

**Per line:** duty cross-check (`EV × rate ≈ printed duty`), no null product HTS code,
`PART_NUMBER` is not an HTS code, COO present and not `MULTI`, a real product HTS (Ch 1–97)
exists, MFR-ID format. Fatal failures (`DUTY_CROSSCHECK`, `ENTERED_VALUE_NULL`,
`HTS_CODE_NULL`, `NO_PRODUCT_HTS`) set the line score to `null` → mandatory repair.
Non-fatal issues score `0.30`.

**Per document:** value reconciliation (`Σ EV ≈ Box 35 ± $1`), totals consistency
(`Duty + Tax + Other = Grand Total` **and** `Other = MPF + Cotton + HMF + Dairy`, which
catches the Tax/Other double-count), and HMF-by-mode.

**Status:** `GREEN` (all gates pass, no failing lines) · `YELLOW` (gates pass, soft line
flags) · `RED` (any gate fails or any fatal line).

Confidence is **derived here**, not trusted from the model. A line only scores `1.00` if its
value actually reproduces every printed duty. That's the whole point: a clean number is one
that survives the arithmetic.

---

## The repair pass — surgical, not wholesale

Do **not** re-run the full document. Re-prompt only the failing lines. Template:

```
You previously extracted line(s) from a CBP 7501 apparel entry. A deterministic check
found specific errors. Re-extract ONLY the line(s) below, fixing exactly the named errors.

For each line you are given: the failed checks and the raw source rows as printed.

Apply these rules (the ones relevant to the failures):
- STACKED HTS: the column lists Chapter-99 codes (9903.xx.xx) first and the product
  classification (Ch 1-97, e.g. 6204.62.8041) LAST. Every code is its own hts_data row.
  The product code is NEVER PART_NUMBER. No hts_data row may have a null code.
- PART_NUMBER: only a printed style/SKU number; if none is printed, return null. A code
  shaped NNNN.NN.NNNN is an HTS code, not a part number.
- ENTERED VALUE: the integer in the Entered Value column, not carton/manifest/invoice
  numbers. It MUST satisfy EV × rate = printed duty for every percentage row.

--- LINE 001 ---
failed_checks: ["HTS_CODE_NULL", "PART_NUMBER_IS_HTS:6204.62.8041", "NO_PRODUCT_HTS"]
source_rows:
  001 OTH OTWR,TROUS,GRL,BL DNM,OTHR
  9903.02.05
  OBD 6204.62.8041  1  1DOZ  23  20%  4.60
  CAT 348 (1 KG) C2             16.6% 3.82
  BDAATRODHA
  MERCHANDISE PROCESSING FEE   0.3464% 0.08
  COTTON FEE (1) 0.01562564/K          0.02

Return corrected JSON for ONLY these line(s), same schema, with a recomputed _confidence.
```

The repaired lines are merged back by `ITEM_NUMBER`, then the **whole document is
re-verified**. A repair that introduces a new reconciliation break is caught on the next
pass — the loop is self-correcting, not blindly trusting.

### Loop control
- `MAX_PASSES = 3` (typical). Most stacked-HTS and value misreads clear on pass 1 because
  the failure is now isolated and the model is told exactly what's wrong.
- Track per-pass `mean_line_score` and `status`. If a pass doesn't improve the score, stop
  and escalate rather than spinning.
- Hard stop: if `Σ EV` still misses Box 35 after `MAX_PASSES`, the document escalates even
  if individual lines look locally consistent — the entry doesn't foot.

---

## Escalation — fail loud, never silent

When the loop exhausts without reaching `GREEN`/`YELLOW`, emit a human-review record:

```json
{
  "entry_number": "B6V-0270803-0",
  "final_status": "RED",
  "passes_run": 3,
  "blocking_gates": ["value_reconciliation"],
  "unresolved_lines": [
    {"item_number": "061", "failed_checks": ["DUTY_CROSSCHECK:6204.62.8011:..."]}
  ],
  "reconciliation": {"line_sum": 36120.00, "box35": 36464.00, "delta": -344.00},
  "note": "344 dollars unaccounted across 4 lines; route to brokerage QA."
}
```

The dataset never receives a silently-wrong entry. It receives either a clean (GREEN),
softly-flagged (YELLOW), or a withheld-and-escalated (RED) result. That distinction is the
product — a Zero-Touch pipeline can auto-advance GREEN, sample YELLOW, and queue RED.

---

## Orchestration (Dify)

The deterministic stages all live in one module, `pipeline_7501.py` (chunk, completeness,
recover, validate). Only the extract and repair nodes call an LLM.

| Node | Type | Does |
|---|---|---|
| 1. Chunk | Code (`chunk_text`) | split into header + line-range chunks, each with its carried-forward duty checksum |
| 2. Extract | LLM (×N, parallel) | run the universal prompt on each chunk + the header chunk |
| 3. Completeness | Code (`check_completeness`) | per chunk: do extracted duties foot to the carried-forward delta? shortfall → re-extract that chunk only |
| 4. Merge | Code | stitch chunks by `ITEM_NUMBER` into one document |
| 5. Recover | Code (`recover`) | derive `entered_value = duty/rate` for any line that lost it |
| 6. Validate | Code (`validate`) | arithmetic gates + earned confidence + RED/YELLOW/GREEN |
| 7. Branch | IF/ELSE | GREEN/YELLOW → emit · RED/fatal → repair |
| 8. Packet | Code | join failing `ITEM_NUMBER`s to their raw source rows |
| 9. Repair | LLM | corrected lines only → back to node 5 |
| 10. Escalate | Code | after MAX_PASSES, write review record |

`run_deterministic(doc)` wraps recover→validate (nodes 5–6) for a single call after merge.
Keep every deterministic node as **code, not an LLM** — determinism is the whole point of
the gate. Chunking turns one slow, drift-prone 300-line decode into N short parallel calls;
the completeness checksum catches any dropped line at the chunk level before it can corrupt
the merged total; recovery fills algebraically-determined values so nothing solvable is ever
flagged "missing"; and validation is the final arithmetic arbiter.

---

## Why this beats a bigger prompt

The previous 16-section master prompt failed because it asked one forward pass to be
perfect across 300 lines with no feedback. This design assumes the forward pass will be
imperfect and **measures** it. The arithmetic that catches a wrong entered value is the same
arithmetic a broker would use to check the entry by hand — it just runs on every line, every
time, in milliseconds, and it tells the model precisely what to fix. Confidence stops being a
vibe and becomes a property you can audit: a `GREEN` document is one where every line's value
reproduces its printed duty and the entry foots to the penny.
