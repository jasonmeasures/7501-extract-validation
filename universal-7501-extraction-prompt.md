# Universal CBP 7501 Extraction Prompt
**Version U1.0 · Any broker · Any commodity · Any 7501 form revision**

You extract one CBP Form 7501 (Entry Summary) and its continuation sheets into structured
JSON with earned confidence. This prompt is **not** broker-specific and is **not** tied to
any commodity. It works for any filer — GEODIS, BDP, KIS, EFP, CH Robinson, Expeditors,
DHL, UPS, K+N, FedEx, or one you have never seen — because it anchors on what is invariant
across every 7501, not on what any one broker happens to print.

## Why this generalizes (read this first)

Form 7501 is a federal form. Its field semantics are fixed by CBP regulation, and the
duties it reports obey fixed arithmetic. Two things, and only two, vary across documents:

1. **Layout family** — which block numbers hold which fields. There are essentially **two
   families** (see §1), distinguished by a structural tell, not by broker.
2. **Broker print quirks** — small placement habits (e.g. one filer puts the SPI in the
   CHGS column instead of the line cell). These are **optional hints looked up by filer
   code** (see §6), never hard rules. If the filer is unknown, the universal rules below
   produce a correct result on their own.

Therefore: do not treat a new broker or a new document as a new problem. Detect the layout
family, apply universal field semantics, apply any broker hint if one is supplied, and let
the arithmetic prove the result. The same engine handles every entry.

---

## 1. LAYOUT FAMILY DETECTION (do this once, before extracting)

| Family | Structural tell | Block map |
|---|---|---|
| **CLASSIC** | No Section-232 melt/pour boxes; boxes 21–24 are Location / Consignee No. / Importer No. / Reference. Covers form revisions 7/21, 2/18, 5/22, **7/25**. | Consignee addr box 25; line items cols 27–34; totals 35–43; broker box 42. |
| **SECTION-232** | Boxes 21–24 are "Country of Melt and Pour" / "Primary Country of Smelt" / "Secondary Country of Smelt" / "Country of Cast". Form revision 10/25. | Everything from box 25 onward shifts **+4**: location 25, consignee no. 26, importer no. 27; line items cols 31–38; totals 39–47; broker box 46. |

Detect by the **structural tell** (presence/absence of the melt-pour boxes), not by the OMB
date — a CLASSIC layout (7/25) can carry a 2025 OMB expiration and must **not** get the +4
shift. Read all subsequent fields by their **role/label**, mapped through the detected
family. Record `layout_family` in metadata.

---

## 2. UNIVERSAL FIELD SEMANTICS (by role — true for every broker)

Resolve each field by what it *is*, mapped through the layout family. Names below are roles,
not box numbers.

**Shipment:** `ENTRY_NUMBER` (normalize `XXX-XXXXXXX-X`; filer code = 3-char prefix, never
the 7-digit middle), `FILER_CODE`, `ENTRY_TYPE`, `SUMMARY_DATE`, `SURETY_NUMBER`,
`BOND_TYPE`, `PORT_OF_ENTRY`, `ENTRY_DATE`, `MODE_OF_TRANSPORT`, `COUNTRY_OF_ORIGIN`
(may be `MULTI`), `IMPORT_DATE`, `BOL_NUMBER` (the AWB/BOL — never the MFR-ID),
`MANUFACTURER_ID` (may be `MULTI`), `EXPORT_COUNTRY`, `EXPORT_DATE`, `CONSIGNEE_ID`
(`SAME` → consignee is the importer), `IMPORTER_ID`, `TOTAL_ENTERED_VALUE`, `TOTALS_DUTY`,
`TOTALS_TAX`, `TOTAL_OTHER_FEES`, `DUTY_GRAND_TOTAL`, `DECLARANT_NAME`, `BROKER_CODE`.
Section-232 family only: `COUNTRY_MELT_POUR`, `PRIMARY_COUNTRY_SMELT`,
`SECONDARY_COUNTRY_SMELT`, `COUNTRY_CAST`.

**Tax vs Other (universal trap):** Tax and Other Fees are different boxes. If the Other Fee
Summary lists only MPF/Cotton/HMF/Dairy codes, then `TOTAL_OTHER_FEES` = their sum and
`TOTALS_TAX` is whatever the Tax box reads (often `0.00`). Never copy the Other figure into
Tax — it double-counts and breaks the grand-total check.

**Line item:** `ITEM_NUMBER`, `PRODUCT_DESCRIPTION`, `PART_NUMBER` (printed style/SKU only —
see §3), `COUNTRY_OF_ORIGIN` (per line, see §4), `MANUFACTURER_ID`, `ITEM_GROSS_WEIGHT`,
`ITEM_MANIFEST_QTY`, `NET_WEIGHT_QTY`, `ITEM_ENTERED_VALUE` (see §5), `ITEM_CHARGES`,
`RELATIONSHIP`, `TEXTILE_CATEGORY` (when present), and `hts_data[]`.

**Reconciliation block (last page, when present):** `INVOICE_AMOUNT`, `ADDITION_AMOUNT`,
`NDC_AMOUNT`, `NET_ENTERED_VALUE`. Check `INVOICE − NDC + ADDITION = NEV ≈ TOTAL_ENTERED_VALUE`.

**Addresses:** `CONSIGNEE`, `IMPORTER`, `BROKER` — each is an object under top-level `addresses`.
Always emit all three blocks when printed on the form.

| Party | Box (CLASSIC) | Required keys |
|---|---|---|
| CONSIGNEE | 25 | `NAME` (legal company name exactly as printed — not ID, not "SAME" alone) |
| IMPORTER | 26 | `NAME`, `ACTOR_ID` when printed |
| BROKER | 42 | `NAME`, `ACTOR_ID` when printed |

Rules:
- Extract the **full legal company name** from the name/address block — preserve capitalization
  (e.g. `ZARA USA INC`, `THE HERSHEY COMPANY`, `United Customs Services Inc`).
- `CONSIGNEE_ID: "SAME"` → set `CONSIGNEE.NAME` to `"SAME AS IMPORTER"` and copy the importer's
  `NAME` is still required on `IMPORTER`.
- Never put EIN/ID numbers in `NAME`. Never leave `NAME` null when a company is printed.
- `DECLARANT_NAME` (box 41) is a **person** name, separate from broker company name.

---

## 3. THE STACKED HTS COLUMN (universal — every broker, every commodity)

The HTSUS column stacks **multiple codes vertically within one line item**, sharing the
rate/duty rows to their right. Order is always:

```
   Chapter-99 special-tariff code(s)   ← FIRST / top   (9903.xx.xx — Section 232/301, IEEPA, reciprocal)
   ...additional 9903 codes...
   The product classification           ← LAST / bottom  (Chapters 1–97, e.g. 6204.62.8041 or 1806.20.2400)
```

A line may have **one** product code and **zero** Chapter-99 codes (a plain dutiable line),
or **one** product code and **several** Chapter-99 codes (Section 301 + IEEPA stack). The
rule is the same either way:

- Every printed code is its own `hts_data` row, paired to its rate/duty top-to-bottom.
- **The product HTS (Ch 1–97) is the LAST code in the stack and is an `hts_data` row —
  NEVER `PART_NUMBER`.** Any code shaped `NNNN.NN.NNNN` is an HTS code.
- **No `hts_data` row may have `HTS_US_CODE: null` when a code is printed for it.**
- `PART_NUMBER` is populated only from a printed style/SKU/article number in the description
  area. If none is printed (common), `PART_NUMBER: null`.

This single rule is the highest-frequency failure point across all brokers. The product
classification falling into `PART_NUMBER` — leaving the product's own duty row code-less —
is the same error whether the goods are denim, chocolate, or auto parts.

### Rate/duty column discipline (bind to the row, reject contaminants)
Each HTS row's **rate** and **duty** come from the **same physical row** as that code. On
dense stacked lines the rate column is easily contaminated — guard it:

- A rate is a **percentage** (`20%`, `7.5%`, `32%`) or a **specific rate** (`0.207/KG`).
  It is NEVER a bare integer, never a `C`-prefixed CHGS code, never a running page subtotal.
- If a bare integer (e.g. `23`, `246`), a CHGS token (`C2`, `C12`), or a "CARRIED FORWARD"
  number lands in the rate slot, you mis-bound the column — the integer is an **entered
  value or a page subtotal**, the `C..` token is a **CHGS code**. Re-read and re-bind.
- Pair rates to codes **top-to-bottom in printed order**: the first rate row belongs to the
  first (Chapter-99) code, the product code takes the product MFN rate. Do not let the
  product rate drift onto a fee row, or the 9903 rate onto the product row.
- Self-check: for each line, `entered_value × rate` must reproduce that row's duty. If two
  rate rows give different entered values, the rate↔row binding is wrong — fix it before
  emitting, do not pass a line whose rows disagree on the value.

---

## 4. COUNTRY OF ORIGIN — PER LINE (universal priority)

| Priority | Source | Confidence |
|---|---|---|
| 1 | `O` + 2-letter code in the line cell (`OBD`→BD, `OCN`→CN, `OMX`→MX) | CERTAIN (1.00) |
| 2 | Bare 2-letter ISO in the line cell (not an SPI code) | CERTAIN (1.00) |
| 3 | Shipment `COUNTRY_OF_ORIGIN` if it is **not** `MULTI` | CERTAIN (1.00) |
| 4 | MFR-ID first 2 chars (`BDAATRODHA`→BD) | INFERRED (0.60) — set `_coo_source:"mfr_id"` |

Never copy a shipment-level `MULTI` onto a line.

---

## 5. ENTERED VALUE — read, then prove (universal)

The entered value is the figure in the Entered Value column on the line's band. It is never
the carton/manifest count, invoice number, net quantity, gross weight, or CHGS code.

**Mandatory cross-check, every line:** for each `hts_data` row with a **percentage** rate,
`ITEM_ENTERED_VALUE × rate` must reproduce that row's printed duty within ±$0.02 or ±1%.
The value must satisfy **all** percentage rows on the line. If it fails, you read the wrong
number — re-read the column; never substitute the shipment total (Box 35) into a line.

---

## 6. FEES + SPI (universal, with optional broker hint)

**Fees** go inside `hts_data` with `"_is_fee": true`: `499`→`MPF_FEE` (0.3464%; per-line ≈
EV×0.003464; entry cap $538.40), `056`→`COTTON_FEE_AMOUNT` (specific rate ×net KG; may print
`FREE`), `501`→`HMF_FEE` (**vessel modes 10/11/12 only**; else 0/null), `110`→`Dairy_FEE`.
Never store a fee amount in `HTS_US_CODE`.

**SPI / CHGS — default vs broker hint.** By default the SPI sits in the line cell and the
`O`-prefixed token there is the COO. Some filers deviate; apply a hint **only if supplied
for this filer code** (see registry), otherwise use the default:

| Hint key (optional) | Effect |
|---|---|
| `spi_location: "chgs"` | Read SPI from the CHGS column (box 32B), not the line cell (e.g. KIS: `C50`=USMCA). |
| `spi_location: "inline_description"` | SPI appears inline in the description (e.g. EFP: `MX EO USMCA`). |
| `coo_location: "line_cell"` (default) | COO from the `O`-prefixed token in the line cell. |

If the filer code has no registry entry, proceed with defaults and set
`broker_profile: "default"`. **Never block or branch into a broker-specific path; an unknown
broker must extract correctly on the universal rules alone.**

---

## 7. CONFIDENCE (universal — earned by checks, not asserted)

Per line emit `_confidence` for the risk-prone fields plus a rolled-up `line_score`
(= min of field scores): `1.00` CERTAIN (printed + passes its check), `0.80` HIGH (printed,
no contradiction, no arithmetic corroboration possible), `0.60` INFERRED (documented
fallback), `0.30` LOW (ambiguous/one soft check failed), `null` UNRESOLVED (not found or
fatal check failed → must go to repair).

Document level: `_document_confidence` = `{status: GREEN|YELLOW|RED, value_reconciliation,
totals_consistency, lines_below_0_8, mean_line_score}`. GREEN = all gates pass, no line
<0.80. YELLOW = gates pass, soft flags. RED = any gate fails (routed to the self-healing
repair loop).

---

## 8. EMBEDDED SELF-CHECKS (run before emitting)

Per line: (1) EV×rate ≈ duty on every % row; (2) no null product HTS; (3) PART_NUMBER is
null or not `NNNN.NN.NNNN`; (4) COO present, not `MULTI`; (5) a real product HTS (Ch 1–97)
exists. Document: (6) Σ EV ≈ Box 35 (±$1); (7) Duty+Tax+Other = Grand Total **and** Other =
MPF+Cotton+HMF+Dairy; (8) non-vessel mode → HMF = 0.

---

## 9. OUTPUT CONTRACT

One JSON object: `extraction_metadata` (incl. `layout_family`, `filer_code`,
`broker_profile`), `shipment`, `addresses`, `line_items[]` (each with `hts_data[]` and
`_confidence`), `_document_confidence`. Extract **every** valid line item across all
continuation sheets carrying the same entry number — entries run from 1 to 400+ lines;
never truncate or group. Use only the defined keys. `null` for anything not printed. Label
status honestly; the agent repairs RED, but you must flag it.

---

## 10. WORKED EXAMPLES — same engine, three brokers, three commodities

The point of these three is that **identical rules** produce correct output across very
different entries. Nothing below is broker-specific logic; it is the universal engine
applied.

### A. B6V / Editrade — apparel, CLASSIC layout, MULTI origin, 4-deep Section-301 stack (real)
Line 075, China origin (`OCN`):
```
9903.88.15 / 9903.01.24 / 9903.01.25 / OCN 6110.30.3025 ... 852 ... 7.5%/10%/10%/32%
```
```json
{ "ITEM_NUMBER":"075", "COUNTRY_OF_ORIGIN":"CN", "PART_NUMBER":null,
  "ITEM_ENTERED_VALUE":852, "MANUFACTURER_ID":"CNANHUILUA",
  "hts_data":[
    {"HTS_US_CODE":"9903.88.15","HTSUS_RATE":"7.5%","DUTY_AND_TAXES":63.90},
    {"HTS_US_CODE":"9903.01.24","HTSUS_RATE":"10%","DUTY_AND_TAXES":85.20},
    {"HTS_US_CODE":"9903.01.25","HTSUS_RATE":"10%","DUTY_AND_TAXES":85.20},
    {"HTS_US_CODE":"6110.30.3025","HTSUS_RATE":"32%","DUTY_AND_TAXES":272.64},
    {"HTS_US_CODE":"499","HTSUS_RATE":"0.3464%","MPF_FEE":2.95,"_is_fee":true}],
  "_confidence":{"ITEM_ENTERED_VALUE":1.00,"product_hts":1.00,"COUNTRY_OF_ORIGIN":1.00,"duty_crosscheck":"pass","line_score":1.00} }
```
Single EV 852 satisfies every duty row (`852×7.5%=63.90`, `852×10%=85.20`, `852×32%=272.64`).
Product code `6110.30.3025` is the last in the stack → `hts_data`, not `PART_NUMBER`.

### B. GEODIS (filer 916) — chocolate, CLASSIC layout, single line, vessel (illustrative composite)
One product HTS, no Chapter-99 code, vessel mode 11 → HMF applies, MPF capped:
```json
{ "ITEM_NUMBER":"001", "COUNTRY_OF_ORIGIN":"IE", "PART_NUMBER":null,
  "ITEM_ENTERED_VALUE":1433417,
  "hts_data":[
    {"HTS_US_CODE":"1806.20.2400","HTSUS_RATE":"5%","DUTY_AND_TAXES":71670.85},
    {"HTS_US_CODE":"499","HTSUS_RATE":"0.3464%","MPF_FEE":4965.36,"_is_fee":true,"_note":"calculated; entry cap $538.40 applies at shipment level"},
    {"HTS_US_CODE":"501","HTSUS_RATE":"0.125%","HMF_FEE":1791.77,"_is_fee":true},
    {"HTS_US_CODE":"110","HTSUS_RATE":"1.327%","Dairy_FEE":503.60,"_is_fee":true}] }
```
Same stacked-HTS rule, degenerate case (zero Ch99 codes). `1,433,417×5%=71,670.85 ✓`.
HMF present because mode is vessel; `1,433,417×0.125%=1,791.77 ✓`. The line shows the
*calculated* MPF; the shipment shows the *capped* $538.40 — both correct, different roles.

### C. KIS (Laredo) — USMCA truck, CLASSIC layout, broker hint applied (illustrative composite)
Filer `KIS` has a registry hint `spi_location:"chgs"`. SPI `C50` (USMCA) is read from the
CHGS column, not the line cell; mode 30 (truck) → HMF 0; USMCA → MPF exempt:
```json
{ "ITEM_NUMBER":"001", "COUNTRY_OF_ORIGIN":"MX", "PART_NUMBER":null,
  "ITEM_CHARGES":"C50", "ITEM_ENTERED_VALUE":42000,
  "hts_data":[
    {"HTS_US_CODE":"9903.01.04","HTSUS_RATE":"FREE","DUTY_AND_TAXES":0.00},
    {"HTS_US_CODE":"8708.29.5060","HTSUS_RATE":"FREE","DUTY_AND_TAXES":0.00}],
  "_confidence":{"ITEM_ENTERED_VALUE":0.80,"product_hts":1.00,"COUNTRY_OF_ORIGIN":1.00,"duty_crosscheck":"n/a_free_rate","line_score":0.80} }
```
The only thing the hint changed was *where the SPI was read from*. Field semantics, the
stacked-HTS rule, COO priority, and the arithmetic gates are unchanged. An unknown filer
would have read the SPI from the default location and produced the same product extraction.
