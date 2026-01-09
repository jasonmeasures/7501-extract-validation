from flask import Flask, render_template_string, request, send_file, jsonify
import os
from datetime import datetime
import pandas as pd
import json
import base64
from typing import Dict, List, Any
import io
from PyPDF2 import PdfReader, PdfWriter
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
import zipfile

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/cbp_debug.log')
    ]
)
logger = logging.getLogger(__name__)

# Enable Flask debug logging
app.logger.setLevel(logging.DEBUG)

# API Configuration - Unified endpoint for both page processing
# Load API key from environment variable for security
API_KEY = os.environ.get('A79_API_KEY', '')  # Set environment variable A79_API_KEY
API_BASE_URL = "https://klearnow.prod.a79.ai/api/v1/public/workflow/run"

# Agent Names and Workflow ID
# Currently using API 1 (Unified PDF Parser) for entire PDF

# API 1 - Active (processes entire document)
API1_AGENT_NAME = "Unified PDF Parser"
API1_WORKFLOW_ID = None  # IMPORTANT: Get this from AI79 dashboard to enable polling
                         # Go to https://klearnow.prod.a79.ai
                         # Find "Unified PDF Parser" workflow
                         # Copy the workflow ID (looks like: wf_xxxxxxxxxxxx)
                         # Set it here: API1_WORKFLOW_ID = "wf_xxxxxxxxxxxx"

# Custom instructions for API 1 (entire document)
# Using CBP Form 7501 Master Extraction Prompt v3.0
API1_CUSTOM_INSTRUCTIONS = """
# CBP Form 7501 Master Extraction Prompt v2
## Comprehensive System for All Brokers
## Enhanced with Validation Rules & Edge Case Handling

---

# SECTION 1: SYSTEM OVERVIEW

You are extracting data from U.S. Customs and Border Protection Form 7501 (Entry Summary). This form documents merchandise entering U.S. commerce and calculates duties, taxes, and fees owed.

## 1.1 MANDATORY DATA HIERARCHY (THREE LEVELS)

```
LEVEL 1: SHIPMENT (KEY_VALUE_PAIR)
├── Appears ONCE per entry
├── Entry identification, transport, parties, totals
├── Addresses (CONSIGNEE, IMPORTER, BROKER)
│
└── LEVEL 2: LINE_ITEM
    ├── Repeats per product line (001, 002, 003...)
    ├── Product description, weights, values, invoice data
    ├── Country of origin AT LINE LEVEL (can differ from shipment)
    │
    └── LEVEL 3: LINE_ITEM_LEVEL_II
        ├── Repeats per HTS code within each line item
        ├── MULTIPLE HTS codes possible per single line item
        ├── HTS classification, rates, duties, fees (MPF, HMF, Dairy, Cotton)
        └── Each HTS entry can have its own rate and duty calculation
```

**CRITICAL**: A single LINE_ITEM (e.g., Line 001) can have MULTIPLE LINE_ITEM_LEVEL_II entries. For USMCA goods, expect 3+ HTS codes per line item.

## 1.2 STANDARD KEYS ENFORCEMENT

**RULE: Only use keys from the US_Keys.xlsx reference file.**

Common Mapping Errors to Avoid:
| WRONG | CORRECT |
|-------|---------|
| P_N | PART_NUMBER |
| P/N | PART_NUMBER |
| PN | PART_NUMBER |
| delivery_order_no | (EXCLUDE - not CBP 7501 data) |
| sender | (EXCLUDE - not CBP 7501 data) |
| receiver | (EXCLUDE - not CBP 7501 data) |
| dates | (EXCLUDE - use specific date keys) |
| other_details | (EXCLUDE - map to specific keys or omit) |

---

# SECTION 2: FORM VERSION DETECTION (CRITICAL)

## 2.1 Version Detection Patterns

Different form versions have DIFFERENT block layouts. Detecting the wrong version causes systematic extraction errors.

### Version 7/21 or 2/18 (OLD FORMAT)
```
Detection Patterns:
- "CBP Form 7501 (7/21)"
- "CBP Form 7501 (2/18)"
- "OMB APPROVAL NO. 1651-0022"
- "EXPIRATION DATE 01/31/2021"

Block Layout:
- Blocks 21-24: Location, Consignee, Importer, Reference
- Line Items: Columns 27-34
- Totals: Blocks 35-43
```

### Version 10/25 (NEW FORMAT - October 2025)
```
Detection Patterns:
- "CBP Form 7501 (10/25)"
- "OMB CONTROL NUMBER 1651-0022"
- "EXPIRATION DATE 11/30/2025"

Block Layout:
- Blocks 21-24: Section 232 Steel/Aluminum fields (NEW)
- Blocks 25-28: Location, Consignee, Importer, Reference (SHIFTED +4)
- Line Items: Columns 31-38 (SHIFTED +4)
- Totals: Blocks 39-47 (SHIFTED +4)
```

### Version 5/22
```
Detection Patterns:
- "CBP Form 7501 (5/22)"
- "PAPERLESS" header

Block Layout: Same as 7/21 (OLD FORMAT)
```

## 2.2 Block Mapping Tables

### OLD FORMAT (7/21, 2/18, 5/22)
| Block | Field |
|-------|-------|
| 21 | Location of Goods/G.O. Number |
| 22 | Consignee Number |
| 23 | Importer Number |
| 24 | Reference Number |
| 25 | Ultimate Consignee Name and Address |
| 26 | Importer of Record Name and Address |
| 27 | Line Number |
| 28 | Description of Merchandise |
| 29 | HTSUS No. / AD/CVD No. |
| 30 | Gross Weight / Manifest Qty |
| 31 | Net Quantity in HTSUS Units |
| 32 | Entered Value / CHGS / Relationship |
| 33 | HTSUS Rate / AD/CVD Rate / IRC Rate / Visa |
| 34 | Duty and IR Tax |
| 35 | Total Entered Value |
| 37 | Duty |
| 38 | Tax |
| 39 | Other |
| 40 | Total |
| 41 | Declarant Name/Title/Signature/Date |
| 42 | Broker/Filer Information |
| 43 | Broker/Importer File Number |

### NEW FORMAT (10/25)
| Block | Field |
|-------|-------|
| 21 | Country of Melt and Pour (Steel Section 232) |
| 22 | Primary Country of Smelt (Aluminum Section 232) |
| 23 | Secondary Country of Smelt (Aluminum Section 232) |
| 24 | Country of Cast (Aluminum Section 232) |
| 25 | Location of Goods/G.O. Number |
| 26 | Consignee Number |
| 27 | Importer Number |
| 28 | Reference Number |
| 29 | Ultimate Consignee Name and Address |
| 30 | Importer of Record Name and Address |
| 31 | Line Number |
| 32 | Description of Merchandise |
| 33 | HTSUS No. / AD/CVD No. |
| 34 | Gross Weight / Manifest Qty |
| 35 | Net Quantity in HTSUS Units |
| 36 | Entered Value / CHGS / Relationship |
| 37 | HTSUS Rate / AD/CVD Rate / IRC Rate / Visa |
| 38 | Duty and IR Tax |
| 39 | Total Entered Value |
| 41 | Duty |
| 42 | Tax |
| 43 | Other |
| 44 | Total |
| 45 | Declarant Name/Title/Signature/Date |
| 46 | Broker/Filer Information |
| 47 | Broker/Importer File Number |

---

# SECTION 3: BROKER-SPECIFIC PATTERNS

## 3.1 Broker Identification

| Broker | Filer Code | Entry Number Format | Primary Port | Primary Mode |
|--------|------------|---------------------|--------------|--------------|
| **GEODIS** | 916 | 916-XXXXXXX-X | 4601, 1102 | 11 (Vessel) |
| **BDP** | BDP, B12 | BDP-XXXXXXX-X | Various | 11 (Vessel), 40 (Air) |
| **KIS** | KIS | KIS-XXXXXXX-X | 2304 (Laredo) | 30 (Truck) |
| **EFP** | EFP | EFP-XXXXXXX-X | 2303, 2304 | 30 (Truck), 21 (Rail) |
| **CH Robinson** | CHR, CRW | CHR-XXXXXXX-X | Various | Various |
| **Expeditors** | EXP, EII | EXP-XXXXXXX-X | Various | Various |
| **DHL** | DHL, DGF | DHL-XXXXXXX-X | Various | 40 (Air) |
| **UPS** | UPS, USC | UPS-XXXXXXX-X | Various | 40 (Air) |
| **Kuehne+Nagel** | KNI, KNL | KNI-XXXXXXX-X | Various | Various |
| **FedEx** | FTN, FED | FTN-XXXXXXX-X | Various | 40 (Air) |

## 3.2 CRITICAL: Filer Code vs Broker Name

**RULE: The filer code in the entry number determines the FILING broker, which may differ from the broker name printed on the form.**

Example:
```
Entry Number: 916-5187505-5
Filer Code: 916 = GEODIS (the filing broker)
Broker Name on Form: BDP INTERNATIONAL (the broker of record)

In this case:
- GEODIS (filer code 916) is electronically filing the entry
- BDP is the licensed customs broker handling the account
- Both are valid; extraction should note BOTH:
  - FILER_CODE: "916" (from entry number)
  - BROKER_NAME: "BDP INTERNATIONAL" (from Box 42/46)
```

## 3.3 GEODIS Broker Specifics

```
Broker Name: GEODIS USA LLC
Filer Code: 916
Address: 75 Northfield Ave, US Building B Edison, NJ 08837
Phone: +17326236600

Characteristics:
- Heavy vessel (container) traffic from Asia and Europe
- Form versions: 7/21, 2/18
- Declarants: Florentin Milagros, Romero Stephanie
- BOL format: MEDUXXNNNNNN, IILUXXXXXXXXXX
- Importer: Primarily The Hershey Company
- Origins: VN, ID, BR, MY, IE, CN
```

## 3.4 BDP Broker Specifics

```
Broker Name: BDP International
Filer Codes: BDP, B12
Address: 206 Eddystone Ave, 2nd Floor, Crum Lynne, PA 19022
Phone: 1-844-209-8752

Characteristics:
- Mixed vessel and air shipments
- Global origin coverage (Ireland, Asia, Europe)
- Form versions: 7/21, 2/18
- Multiple MFR IDs common
- AD/CVD handling for Chinese goods
- Often uses GEODIS (916) as filer
```

## 3.5 KIS Broker Specifics

```
Broker Name: KIS Customs
Filer Code: KIS
Entry Format: KIS-XXXXXXX-X

Characteristics:
- Land border (Mexico-US via Laredo)
- Mode 30 (Truck)
- USMCA origin goods (MX primarily)
- Form versions: 7/21, 2/18
- BOL format: CINLKISXXXXXXXX or [Carrier]KISXXXXXXXX

CRITICAL - CHGS Field Usage:
KIS uses Box 32B (CHGS) for SPI codes, NOT freight charges:
- C50 = USMCA preferential treatment
- C100 = US Goods Returned
- C300 = US Goods Returned (specific provision)
```

## 3.6 EFP Broker Specifics

```
Broker Name: Raul S. Villarreal dba PG Customs Brokers
Filer Code: EFP
Address: 416 Shiloh Dr Ste C2, Laredo, TX 78045-6755
Phone: 956-790-0010

Characteristics:
- Land border (Mexico-US via Laredo)
- Mode 30 (Truck), Mode 21 (Rail)
- USMCA origin goods
- Form versions: 5/22, 2/18
- Product codes: XXXXX-XXXXX-XXX format
- Declarant: Raul S. Villarreal, Attorney-in-Fact

SPI Patterns:
- MX EO USMCA = Mexico Eligible Origin USMCA
- CA EO USCMA = Canada Eligible Origin USMCA
- S = Statistical reporting
- S+ = Statistical + Additional (quota goods)
```

---

# SECTION 4: DATA SOURCE FILTERING

## 4.1 CBP 7501 Data vs Non-7501 Data

**CRITICAL: Extract ONLY from CBP Form 7501. Exclude delivery orders, packing lists, commercial invoices, and manifest data.**

### Include (CBP 7501 Only):
- Entry number, dates, parties from Blocks 1-30/34
- Line item data from Columns 27-34 / 31-38
- Totals from Blocks 35-43 / 39-47
- Declarant and broker info from Blocks 41-43 / 45-47
- Fee summary (499, 501, 056, 110, etc.)

### EXCLUDE These Non-Standard Fields:
| Field to EXCLUDE | Reason |
|------------------|--------|
| `delivery_order_no` | Delivery order data, not CBP 7501 |
| `sender` | Delivery order section |
| `receiver` | Delivery order section |
| `dates` (as object) | Use specific date keys instead |
| `other_details` | Map to specific keys or omit |
| `P_N` | Use `PART_NUMBER` |
| `unique_id` | Not a US_Keys field (use ITEM_NUMBER) |

### Exclude Document Types:
- Delivery Order data (dates, carrier, voyage, delivery instructions)
- Packing List data (container contents, piece counts)
- Manifest data (container numbers, seal numbers)
- Commercial Invoice data (unless mapping to line items)
- Any field not in US_Keys.xlsx standard keys

### Handling Multi-Document Packages:
```
If package contains multiple documents:
1. Identify the CBP Form 7501 pages (look for "CBP Form 7501" header)
2. Extract ONLY from those pages
3. Ignore delivery orders, packing lists, invoices
4. Note: page_count should reflect ONLY 7501 pages
5. cbp_7501_line_count should reflect ONLY valid 7501 line items
```

## 4.2 Line Item Number Validation

**RULE: Valid CBP 7501 line items use 3-digit numbering (001, 002, 003...).**

### Valid Line Numbers:
- 001, 002, 003... 099, 100, 101...
- Format: 3 digits, zero-padded

### Invalid/Suspect Line Numbers:
- 0001, 0002... (4-digit) = Likely manifest/packing list data
- 1, 2, 3... (no padding) = May need formatting
- A, B, C... (alpha) = Likely different document

### Example:
```json
// CORRECT - CBP 7501 line items only
"items": [
  {"ITEM_NUMBER": "001", ...}
]

// WRONG - Mixed with manifest data
"items": [
  {"ITEM_NUMBER": "001", ...},    // CBP 7501
  {"ITEM_NUMBER": "0001", ...},   // Manifest - EXCLUDE
  {"ITEM_NUMBER": "0002", ...}    // Manifest - EXCLUDE
]
```

## 4.3 Single-Line Entry with Multiple Containers

**CRITICAL: Many entries have ONE actual 7501 line item but MULTIPLE containers/shipments.**

### Pattern Recognition:
```
CBP 7501 Entry Summary:
- Line 001: HTS 1806.20.2400, Value $1,433,417, Duty $71,670.85 ← ACTUAL 7501 LINE
- Lines 002-013: Container breakdowns with no values/duties ← INFORMATIONAL ONLY
- Items 0001-0010: Manifest data ← EXCLUDE ENTIRELY

Result: This is a SINGLE LINE ITEM entry, not 13+ line items
```

### Indicators of Container Breakdown Lines (NOT Actual 7501 Lines):
| Indicator | Example | Action |
|-----------|---------|--------|
| No ITEM_ENTERED_VALUE | null, blank | Exclude from line_items |
| No DUTY_AND_TAXES | null, blank | Exclude from line_items |
| HTSUS_RATE = "N/A" | Rate not applicable | Exclude from line_items |
| Description = "SAID TO CONTAIN" | Manifest language | Exclude entirely |
| Description = "X BAG(S) of Y BAGS" | Container breakdown | Exclude from line_items |
| 4-digit item number | 0001, 0002 | Exclude entirely |

### Decision Algorithm:
```python
def is_valid_7501_line_item(item):
    # Must have 3-digit format
    if len(item.ITEM_NUMBER) != 3:
        return False
    
    # Must have actual value OR duty
    if item.ITEM_ENTERED_VALUE is None and item.DUTY_AND_TAXES is None:
        return False
    
    # Must not be manifest language
    if "SAID TO CONTAIN" in item.PRODUCT_DESCRIPTION:
        return False
    
    # Must have valid HTS rate (not "N/A")
    if all(hts.HTSUS_RATE == "N/A" for hts in item.hts_data):
        return False
    
    return True
```

### Correct Extraction for Single-Line Entry:
```json
{
  "extraction_metadata": {
    "total_document_items": 23,
    "valid_7501_line_items": 1,
    "excluded_container_breakdowns": 12,
    "excluded_manifest_items": 10
  },
  "line_items": [
    {
      "ITEM_NUMBER": "001",
      "PRODUCT_DESCRIPTION": "COCO IN PRP CON OV 5.5 BFA",
      "PART_NUMBER": "496308",
      "ITEM_ENTERED_VALUE": 1433417,
      "hts_data": [
        {"HTS_US_CODE": "1806.20.2400", "HTSUS_RATE": "5%", "DUTY_AND_TAXES": 71670.85}
      ]
    }
  ]
}
```

## 4.4 Continuation Sheet vs Manifest Data

**CRITICAL: Distinguish CBP 7501 continuation sheets from manifest/packing list pages.**

### CBP 7501 Continuation Sheet Indicators:
- Header: "ENTRY SUMMARY CONTINUATION SHEET"
- Includes: Entry Number (Block 1)
- Columns: Same structure as main form (27-34 / 31-38)
- Line numbers continue sequence (002, 003, etc.)

### Manifest/Packing List Indicators:
- Different headers (no "CBP Form 7501")
- Container numbers, seal numbers
- "SAID TO CONTAIN" descriptions
- 4-digit item numbers (0001, 0002)
- Missing HTS codes, values, duties

### Decision Rule:
```
IF page has "CBP Form 7501" header OR "ENTRY SUMMARY" header:
    → Include as 7501 data
ELSE IF page has HTS codes, duties, values in 7501 format:
    → Include as continuation data
ELSE:
    → Exclude (manifest/delivery order/packing list)
```

---

# SECTION 5: SHIPMENT LEVEL EXTRACTION (KEY_VALUE_PAIR)

## 5.1 Required Fields

| STANDARD_KEY | Box (Old) | Box (New) | Format/Example | Notes |
|--------------|-----------|-----------|----------------|-------|
| ENTRY_NUMBER | 1 | 1 | XXX-XXXXXXX-X | 11 alphanumeric |
| ENTRY_TYPE | 2 | 2 | 01, 03, 06, 11, 21 + ABI/A, ABI/P | Entry type + ABI indicator |
| SUMMARY_DATE | 3 | 3 | MM/DD/YYYY | Filing date |
| SURETY_NUMBER | 4 | 4 | 001, 998, 999 | 3-digit code |
| BOND_TYPE | 5 | 5 | 0, 8, 9 | Single digit |
| PORT_OF_ENTRY | 6 | 6 | DDPP (4 digits) | Schedule D code |
| ENTRY_DATE | 7 | 7 | MM/DD/YYYY | Release date |
| MODE_OF_TRANSPORT | 9 | 9 | 10, 11, 20, 21, 30, 31, 40, 41 | 2-digit code |
| COUNTRY_OF_ORIGIN | 10 | 10 | 2-letter ISO or MULTI | Country code |
| IMPORT_DATE | 11 | 11 | MM/DD/YYYY | Arrival date |
| BOL_NUMBER | 12 | 12 | SCAC + Number | Bill of lading |
| MANUFACTURER_ID | 13 | 13 | 15-char MID or MULTI | Manufacturer code |
| EXPORT_COUNTRY | 14 | 14 | 2-letter ISO or MULTI | Exporting country |
| EXPORT_DATE | 15 | 15 | MM/DD/YYYY or MULTI | Export date |
| IT_NUMBER | 16 | 16 | IT number | Immediate transport |
| IT_DATE | 17 | 17 | MM/DD/YYYY | IT date |
| MISSING_DOCS | 18 | 18 | 01, 10, 16, etc. | Document codes |
| PORT_OF_LADING | 19 | 19 | 5-digit Schedule K | Foreign port |
| PORT_OF_UNLADING | 20 | 20 | 4-digit code | US port |
| LOCATION_FIRMS_CODE | 21 | 25 | FIRMS code | Location of goods |
| CONSIGNEE_ID | 22 | 26 | NN-NNNNNNN or SAME | EIN format |
| IMPORTER_ID | 23 | 27 | NN-NNNNNNN | EIN format |
| REF_NUMBER | 24 | 28 | EIN format | Reference party |
| TOTAL_ENTERED_VALUE | 35 | 39 | USD amount | Sum of line values |
| TOTALS_DUTY | 37 | 41 | USD amount | Total duty |
| TOTALS_TAX | 38 | 42 | USD amount | Total tax |
| TOTAL_OTHER_FEES | 39 | 43 | USD amount | Total other fees |
| DUTY_GRAND_TOTAL | 40 | 44 | USD amount | Sum of 37+38+39 |
| DECLARANT_NAME | 41 | 45 | Name | Person signing |
| BROKER_CODE | 43 | 47 | Broker file number | Internal reference |

## 5.2 Fee Summary Fields (Shipment Level)

| STANDARD_KEY | Code | Description | Notes |
|--------------|------|-------------|-------|
| MPF_AMOUNT | 499 | Merchandise Processing Fee | ACTUAL BILLED (after min/max) |
| HARBOR_MAINTENANCE | 501 | Harbor Maintenance Fee | Vessel only (Mode 10, 11, 12) |
| COTTON_AMOUNT | 056 | Cotton Fee | Agricultural fee |
| Dairy_AMOUNT | 110 | Dairy Fee | Agricultural fee |

## 5.3 Section 232 Fields (10/25 Version Only)

| STANDARD_KEY | Box | Description |
|--------------|-----|-------------|
| COUNTRY_MELT_POUR | 21 | Steel: Country where raw steel first produced/poured |
| PRIMARY_COUNTRY_SMELT | 22 | Aluminum: Largest volume country of smelt |
| SECONDARY_COUNTRY_SMELT | 23 | Aluminum: Second largest volume country |
| COUNTRY_CAST | 24 | Aluminum: Country where last liquified/cast |

---

# SECTION 6: ADDRESS EXTRACTION

## 6.1 Address Types

| ADDRESS Type | Box (Old) | Box (New) | STANDARD_KEY |
|--------------|-----------|-----------|--------------|
| Ultimate Consignee | 25 | 29 | CONSIGNEE |
| Importer of Record | 26 | 30 | IMPORTER |
| Broker/Filer | 42 | 46 | BROKER |

## 6.2 Address Fields (ADDRESS_FIELD)

| STANDARD_KEY | Description |
|--------------|-------------|
| NAME | Company/person name |
| ACTOR_ID | ID number (EIN/SSN) |
| STREET_NUMBER | House/building number |
| STREET_NAME | Street name |
| UNSTRUCTURED_STREET_ADDRESS | Full street line |
| CITY | City name |
| COUNTRY_SUB_ENTITY | State (2-letter code) |
| POSTAL_CODE | ZIP code |
| COUNTRY | Country code |

## 6.3 "SAME" Consignee Handling

When Consignee Number (Box 22/26) = "SAME":
- Skip CONSIGNEE address extraction
- Set CONSIGNEE to null or note "SAME AS IMPORTER"
- Do NOT copy Importer data to Consignee fields
- Consignee is identical to Importer of Record

---

# SECTION 7: LINE ITEM EXTRACTION (LINE_ITEM)

## 7.1 Required Fields

| STANDARD_KEY | Column (Old) | Column (New) | Notes |
|--------------|--------------|--------------|-------|
| ITEM_NUMBER | 27 | 31 | Sequential: 001, 002, 003... |
| PRODUCT_DESCRIPTION | 28 | 32 | Merchandise description |
| PART_NUMBER | 28 | 32 | Product code/SKU (NOT P_N) |
| ITEM_GROSS_WEIGHT | 30A | 34A | Gross weight in KG |
| ITEM_MANIFEST_QTY | 30B | 34B | Manifest quantity |
| NET_WEIGHT_QTY | 31 | 35 | Net quantity in HTS units |
| WEIGHT_UNITS | 31 | 35 | Unit of measure (KG, NO, DOZ) |
| ITEM_ENTERED_VALUE | 32A | 36A | USD value |
| ITEM_CHARGES | 32B | 36B | Charges OR SPI code (broker-specific) |
| RELATIONSHIP | 32C | 36C | Y = Related, N = Not related |
| MANUFACTURER_ID | 13 | 13 | Line-level MFR ID |
| COUNTRY_OF_ORIGIN | Derived | Derived | See derivation rules |

## 7.2 Optional Line Item Fields

| STANDARD_KEY | Source | Notes |
|--------------|--------|-------|
| INVOICE_NUMBER | 28/Description area | Invoice reference |
| INVOICE_AMOUNT | Invoice total | Per-line invoice value |
| PO_NUMBER | Description area | Purchase order |
| COMMERCIAL_DESCRIPTION | 28 | Detailed product description |
| TEXTILE_CATEGORY | 29C | CAT NNN format |

## 7.3 Incomplete Line Item Handling

**SCENARIO: Line items 002+ may have incomplete data (no value, no duty)**

### Possible Reasons:
1. **Continuation of Line 001** - Same product, different container
2. **Informational Line** - Manifest data included on form
3. **Apportioned Entry** - Values consolidated on first line

### Handling Rules:
```
IF line has HTS code AND duty amount:
    → Full extraction
ELSE IF line has HTS code but NO value/duty:
    → Extract available fields, note incomplete
ELSE IF line has description only:
    → May be manifest/informational, flag for review
```

### Example:
```json
{
  "ITEM_NUMBER": "002",
  "PRODUCT_DESCRIPTION": "IRISH CHOCOLATE MILK CRUMB IN POWDERED FORM",
  "ITEM_GROSS_WEIGHT": "25411 KG",
  "NET_WEIGHT_QTY": "22 BAG",
  "MANUFACTURER_ID": "IEORNCOODUB",
  "COUNTRY_OF_ORIGIN": "IE",
  "ITEM_ENTERED_VALUE": null,
  "ITEM_CHARGES": null,
  "_extraction_note": "Continuation line - no separate value"
}
```

## 7.4 Country of Origin Derivation Rules (LINE ITEM LEVEL)

**Priority Order:**

| Priority | Rule | Source | Example | Result |
|----------|------|--------|---------|--------|
| 1 | O + 2-digit code | Line item text | OUS, OMX | US, MX |
| 2 | MFR ID first 2 chars | MANUFACTURER_ID | USHERCOM6HAZ | US |
| 3 | Shipment-level COO | Box 10 | MX | MX |

```
Algorithm:
1. Search line item data for pattern "O" + 2-letter code (OUS, OMX, OCN)
   → If found, use the 2-letter code as COUNTRY_OF_ORIGIN
   
2. If no O+XX code, check MANUFACTURER_ID at line level
   → Extract first 2 characters (e.g., "IE" from "IEORNCOODUB")
   → Validate against ISO country codes
   → Use as COUNTRY_OF_ORIGIN
   
3. If no line-level MFR ID, use shipment-level COUNTRY_OF_ORIGIN (Box 10)

4. If shipment COO = "MULTI", each line MUST have individual COO
```

---

# SECTION 8: HTS/DUTY EXTRACTION (LINE_ITEM_LEVEL_II)

## 8.1 Required Fields

| STANDARD_KEY | Column (Old) | Column (New) | Notes |
|--------------|--------------|--------------|-------|
| HTS_US_CODE | 29A | 33A | 10-digit HTS code WITH PERIODS |
| HTS_DESCRIPTION | 28 | 32 | HTS-level description |
| HTSUS_RATE | 33A | 37A | FREE or percentage |
| ESTIMATED_VALUE | 32A | 36A | Value per HTS line |
| DUTY_AND_TAXES | 34 | 38 | Calculated duty amount |
| AD_CVD_CASE_NUMBER | 29B | 33B | A-XXX-XXX-XXX format |
| AD_CVD_RATE | 33B | 37B | AD/CVD rate |

## 8.2 HTS Code Formatting (CRITICAL)

**RULE: Always format HTS codes with periods in standard positions.**

### Correct Format:
```
XXXX.XX.XXXX
Examples:
- 1806.20.2400 ✓
- 9903.88.15 ✓
- 3924.90.5650 ✓
```

### Incorrect Formats to Fix:
```
1806202400 → 1806.20.2400
990388.15 → 9903.88.15
3924905650 → 3924.90.5650
```

### Formatting Algorithm:
```
IF HTS has no periods AND length >= 8:
    Insert period after position 4
    Insert period after position 6
    Result: XXXX.XX.XXXX
```

## 8.3 Fee Codes vs HTS Codes

**CRITICAL: Distinguish fee codes from HTS codes in LINE_ITEM_LEVEL_II.**

### Fee Codes (Extract to Separate Fee Fields):
| Pattern | Fee Type | STANDARD_KEY |
|---------|----------|--------------|
| 499 | MPF | MPF_FEE |
| 501 | HMF | HMF_FEE |
| 056 | Cotton | COTTON_FEE_AMOUNT |
| 110 | Dairy | Dairy_FEE |

### Handling:
```
IF code is 499, 501, 056, 110:
    → Extract as fee field, NOT as HTS code
    → Include in hts_data array with fee-specific keys
    
Example:
{
  "HTS_US_CODE": "499 MPF",
  "HTSUS_RATE": "0.3464%",
  "DUTY_AND_TAXES": 4965.36,
  "MPF_FEE": 4965.36,
  "MPF_RATE": "0.3464%",
  "_is_fee": true
}
```

## 8.4 Multiple HTS Codes Per Line Item

**CRITICAL**: USMCA and Chinese goods often have multiple HTS codes per line item.

### USMCA Pattern (Mexico/Canada Origin):
```
Line 001:
├── HTS 1: 9903.01.04 (IEEPA Exclusion) → FREE, $0.00
├── HTS 2: 9903.01.27 (Reciprocal Exclusion) → FREE, $0.00
└── HTS 3: 1806.90.9019 (Primary HTS) → FREE, $0.00
```

### Irish/European Goods Pattern (Dutiable):
```
Line 001:
├── HTS 1: 1806.20.2400 (Primary HTS) → 5%, $71,670.85
├── 499 MPF: 0.3464% → $4,965.36 (calculated, capped at $538.40)
├── 501 HMF: 0.125% → $1,791.77
└── 110 Dairy: 1.327% → $503.60
```

### Chinese Goods Pattern (Section 301):
```
Line 005:
├── HTS 1: 9903.88.15 (Section 301 - 7.5%) → 7.5%, $1,234.88
├── HTS 2: 9903.01.24 (Section 301 - 10%) → 10%, $1,646.50
├── HTS 3: 3924.90.5650 (Primary HTS) → 3.4%, $559.81
└── 499 MPF: 0.3464% → $57.03
```

## 8.5 HTS Code Types

| Code Pattern | Type | Description |
|--------------|------|-------------|
| 9903.01.XX | USMCA Chapter 99 | Trade agreement provisions |
| 9903.88.XX | Section 301 | China tariff codes |
| 9823.10.01 | Sugar quota | Sugar-containing goods |
| 1XXX-8XXX | Primary HTS | Actual product classification |
| 98XX.XX.XXXX | Chapter 98 | Special provisions |

---

# SECTION 9: FEE RULES

## 9.1 Fee Code Reference

| Code | Fee Name | STANDARD_KEY (Total) | STANDARD_KEY (Line) | Rate |
|------|----------|---------------------|---------------------|------|
| 499 | Merchandise Processing Fee | MPF_AMOUNT | MPF_FEE | 0.3464% |
| 501 | Harbor Maintenance Fee | HARBOR_MAINTENANCE | HMF_FEE | 0.125% |
| 056 | Cotton Fee | COTTON_AMOUNT | COTTON_FEE_AMOUNT | Variable |
| 110 | Dairy Fee | Dairy_AMOUNT | Dairy_FEE | Variable |

## 9.2 MPF Rules (CRITICAL)

```
Rate: 0.3464% ad valorem
Minimum: $27.75 per entry
Maximum: $538.40 per entry (for single-line entries)

Calculation:
calculated_mpf = entered_value * 0.003464
if calculated_mpf < 27.75: actual_mpf = 27.75
elif calculated_mpf > 538.40: actual_mpf = 538.40
else: actual_mpf = calculated_mpf
```

### CRITICAL: Line-Level vs Shipment-Level MPF Discrepancy

**This is a common source of confusion. The numbers WILL differ.**

| Level | What It Shows | Example |
|-------|--------------|---------|
| LINE-LEVEL (hts_data) | CALCULATED amount (before cap) | $4,965.36 |
| SHIPMENT-LEVEL | ACTUAL BILLED amount (after cap) | $538.40 |

### Example Explanation:
```
Entry Value: $1,433,417
Calculated MPF: $1,433,417 × 0.003464 = $4,965.36
Maximum Cap: $538.40
Actual Billed: $538.40 (capped)

In extraction:
- Line 001 hts_data shows: MPF_FEE = $4,965.36 (calculated)
- Shipment shows: MPF_AMOUNT = $538.40 (actual billed after cap)

THIS IS CORRECT - both values serve different purposes
```

### Multi-Line Entry MPF:
```
For entries with multiple dutiable line items:
- Each line item gets its own MPF cap calculation
- Total MPF = sum of all line-level capped MPFs
- Maximum possible = (number of lines) × $538.40

Example with 3 dutiable lines:
- Line 001: Value $500,000, Calculated MPF $1,732, Capped to $538.40
- Line 002: Value $300,000, Calculated MPF $1,039.20, Capped to $538.40
- Line 003: Value $50,000, Calculated MPF $173.20, Actual $173.20 (under cap)
- Total MPF: $538.40 + $538.40 + $173.20 = $1,250.00
```

### MPF Anomaly Detection:
```
Flag for review if:
1. Shipment MPF > $538.40 for single-line entry
2. Shipment MPF < $27.75 for dutiable entry
3. Shipment MPF differs significantly from expected cap calculation
4. Line-level MPF shows $0 for dutiable non-FTA goods

Common causes of anomalies:
- Multiple entries consolidated
- Informal entry (different MPF rules)
- Prior disclosure or penalty
- Data entry error in source document
```

### MPF Exemptions:
```
FTA goods with proper SPI are MPF-EXEMPT:
- S, S+ (USMCA) → MPF = $0
- A (GSP) → MPF = $0
- AU, BH, CL, CO, IL, JO, KR, MA, OM, P, PA, PE, SG → MPF = $0

If FTA SPI present, both:
- Line-level MPF_FEE should be $0 or absent
- Shipment-level MPF_AMOUNT should be $0 or absent
```

## 9.3 HMF Rules

```
Rate: 0.125% ad valorem

CRITICAL: HMF only applies to VESSEL transport!

Mode Check:
- Mode 10, 11, 12 (Vessel) → HMF applies
- Mode 20, 21 (Rail) → HMF = 0
- Mode 30, 31 (Truck) → HMF = 0
- Mode 40, 41 (Air) → HMF = 0

Algorithm:
if mode_of_transport in ['10', '11', '12']:
    hmf = entered_value * 0.00125
else:
    hmf = 0.00
```

## 9.4 Fee Extraction Pattern

```
Look for pattern in Other Fee Summary section:

499  MPF                    538.40
501  Harbor Maintenance    1791.77
056  Cotton Fee              45.00
110  Dairy                  503.60

Regex: (499|501|056|110)\s+\w+.*?\s+([\d,]+\.?\d*)
```

---

# SECTION 10: SPECIAL PROGRAM INDICATORS (SPI)

## 10.1 SPI Code Categories

### Primary FTA Codes (Establishes Rate)
| Code | Agreement/Program |
|------|-------------------|
| A | GSP (Generalized System of Preferences) |
| AU | United States-Australia FTA |
| BH | United States-Bahrain FTA |
| CA | NAFTA - Canada (DISCONTINUED, use S) |
| CL | United States-Chile FTA |
| CO | United States-Colombia TPA |
| E | Caribbean Basin Economic Recovery Act |
| IL | United States-Israel FTA |
| JO | United States-Jordan FTA |
| JP | United States-Japan Trade Agreement |
| KR | United States-Korea FTA (KORUS) |
| MA | United States-Morocco FTA |
| MX | NAFTA - Mexico (DISCONTINUED, use S) |
| OM | United States-Oman FTA |
| P | CAFTA-DR |
| PA | United States-Panama TPA |
| PE | United States-Peru TPA |
| S | USMCA (US-Mexico-Canada Agreement) |
| S+ | USMCA - Agricultural TRQ, staging, textile TPL |
| SG | United States-Singapore FTA |

### Secondary Codes (Can Combine with Primary)
| Code | Description |
|------|-------------|
| F | Folklore Products |
| G | Made to Measure Suits (Hong Kong) |
| H | Special Regime Garments |
| M | Fashion Samples |
| V | Set Component (GRI 3b/3c) |
| X | Set Header (Rate from this item) |

### US Goods Codes (9802 Provisions)
| Code | Provision |
|------|-----------|
| 01 | 9802.00.5010 - Articles repaired/assembled abroad |
| 02 | 9802.00.8040 - Articles repaired/assembled abroad |
| 03 | 9802.00.6000 - Metal articles processed/returned |
| 04-12 | Various textile and apparel provisions |

## 10.2 SPI Combination Rules

```
Format: [Primary/Country].[Secondary]

Examples:
- A.F = GSP + Folklore
- S.V = USMCA + Set Component
- KR.X = Korea FTA + Set Header

Rules:
- Maximum 1 Primary/Country code
- Can add multiple secondary codes
- V and X are mutually exclusive with each other
```

## 10.3 Broker-Specific SPI Patterns

### KIS Broker (CHGS Field = SPI)
```
Box 32B shows SPI codes, NOT charges:
- C50 = USMCA preferential
- C100 = US Goods Returned
- C300 = US Goods Returned (specific)
```

### EFP Broker (Inline SPI)
```
SPI appears inline with line item:
- "MX EO USMCA" = Mexico Eligible Origin USMCA
- "CA EO USCMA" = Canada Eligible Origin USMCA
- "S 2106.90.9985" = Statistical reporting HTS
- "S+ 9823.10.01" = Statistical + quota
```

---

# SECTION 11: VALIDATION RULES

## 11.1 Entry Number Validation

```
Pattern: XXX-XXXXXXX-X

Components:
- Filer Code: 3 alphanumeric characters
- Entry Number: 7 digits
- Check Digit: 1 digit (calculated)

Known Filer Codes: 916, BDP, B12, KIS, EFP, GEO, G09, CHR, DHL, UPS, KNI, FTN
```

## 11.2 Value Validation

```
TOTAL_ENTERED_VALUE == sum(line_items.ITEM_ENTERED_VALUE)

Note: Only sum items with actual values (skip null/continuation items)
Tolerance: ±$1.00 for rounding
```

## 11.3 Totals Validation

```
DUTY_GRAND_TOTAL == TOTALS_DUTY + TOTALS_TAX + TOTAL_OTHER_FEES

Tolerance: ±$0.01 for rounding
```

## 11.4 MULTI Field Validation

```
If COUNTRY_OF_ORIGIN == 'MULTI':
    Each LINE_ITEM MUST have individual COUNTRY_OF_ORIGIN

If MANUFACTURER_ID == 'MULTI':
    Each LINE_ITEM MUST have individual MANUFACTURER_ID
```

## 11.5 HMF Transport Mode Validation

```
If MODE_OF_TRANSPORT not in ['10', '11', '12']:
    HARBOR_MAINTENANCE must be 0.00
    All HMF_FEE values must be 0.00
```

## 11.6 Fee Cap Validation

```
MPF Validation:
- If single line: MPF_AMOUNT should be between $27.75 and $538.40
- If multiple lines: Each line's MPF should be capped individually
- Total MPF = sum of capped line MPFs

MPF Discrepancy Check:
- calculated_mpf = TOTAL_ENTERED_VALUE × 0.003464
- If MPF_AMOUNT significantly differs from calculated, verify:
  - Multiple line items (each capped separately)
  - FTA exemptions (USMCA, GSP = $0 MPF)
  - Entry type exemptions
```

## 11.7 MPF Anomaly Detection

**Flag these scenarios for review:**

| Scenario | Example | Likely Cause |
|----------|---------|--------------|
| Shipment MPF > $538.40 (single line) | $614.35 | Multiple containers billed separately, data error, or prior disclosure penalty |
| Shipment MPF < $27.75 (dutiable) | $15.00 | Partial entry, adjustment, or data error |
| Shipment MPF = $0 (dutiable, no FTA) | $0.00 | Missing data or exemption not captured |
| Line MPF ≠ 0.3464% × value | Off by >$1 | Rate changed or calculation error |

### Specific Case: MPF Exceeds Single-Entry Cap
```
If MPF_AMOUNT > $538.40 for what appears to be single-line entry:

Possible explanations:
1. Entry covers multiple entries consolidated (check ENTRY_NUMBER pattern)
2. Multiple informal entries (different MPF rules)
3. Prior disclosure penalty included
4. Data entry error in source document
5. Container-level MPF billing (unusual but possible)

Action: 
- Flag in validation_results
- Include note: "MPF $614.35 exceeds standard cap $538.40 - review source"
- Do NOT automatically adjust the value
```

### Validation Output Example:
```json
"validation_results": {
  "mpf_validation": {
    "shipment_mpf": 614.35,
    "expected_max": 538.40,
    "status": "ANOMALY",
    "flag": "MPF exceeds standard single-entry cap",
    "possible_causes": [
      "Multiple entries consolidated",
      "Prior disclosure penalty",
      "Data entry error"
    ]
  }
}
```

---

# SECTION 12: EXTRACTION DO's AND DON'Ts

## 12.1 DO's

✓ Detect form version FIRST before extracting any fields
✓ Use correct block mapping for detected version
✓ Extract MULTIPLE HTS codes per line item
✓ Derive line-level COUNTRY_OF_ORIGIN using priority rules
✓ Handle "SAME" consignee correctly
✓ Set HMF to 0 for non-vessel transport
✓ Validate totals against sum of line items
✓ Include all three hierarchy levels in output
✓ Map fields to US_Keys.xlsx standard keys only
✓ Format HTS codes with periods (XXXX.XX.XXXX)
✓ Distinguish fee codes (499, 501, etc.) from HTS codes
✓ Note filer code vs broker name differences
✓ Exclude non-7501 data (delivery orders, packing lists)
✓ Count only VALID 7501 line items (with values/duties)
✓ Exclude container breakdown lines (no value, no duty)
✓ Flag MPF anomalies (> $538.40 for single entry)
✓ Document excluded items in metadata

## 12.2 DON'Ts

✗ DO NOT mix up form versions - block numbers differ!
✗ DO NOT assume one HTS code per line item
✗ DO NOT treat KIS CHGS field as charges (it's SPI codes)
✗ DO NOT apply HMF to non-vessel shipments
✗ DO NOT forget to derive line-level country of origin
✗ DO NOT create keys that don't exist in US_Keys.xlsx
✗ DO NOT duplicate fees at both line and shipment level
✗ DO NOT assume Consignee address exists - check for "SAME"
✗ DO NOT extract table headers as data values
✗ DO NOT invent values - only extract what exists
✗ DO NOT use `P_N` instead of `PART_NUMBER`
✗ DO NOT include 4-digit item numbers (manifest data)
✗ DO NOT include delivery order fields (sender, receiver, dates)
✗ DO NOT include `unique_id` field (use ITEM_NUMBER)
✗ DO NOT count container breakdowns as valid line items
✗ DO NOT include lines with HTS rate "N/A" and no values
✗ DO NOT include "SAID TO CONTAIN" manifest descriptions

---

# SECTION 13: OUTPUT FORMAT

## 13.1 JSON Structure

```json
{
  "extraction_metadata": {
    "form_version": "2/18",
    "filer_code": "916",
    "broker": "BDP",
    "extraction_date": "2024-07-07",
    "page_count": 6,
    "cbp_7501_pages": 2,
    "total_document_items": 23,
    "valid_7501_line_items": 1,
    "excluded_items": {
      "container_breakdowns": 12,
      "manifest_items": 10
    }
  },
  "shipment": {
    "ENTRY_NUMBER": "916-5187505-5",
    "ENTRY_TYPE": "02 ABI/P/L",
    "SUMMARY_DATE": "07/17/2024",
    "MODE_OF_TRANSPORT": "11",
    "COUNTRY_OF_ORIGIN": "IE",
    "TOTAL_ENTERED_VALUE": 1433417,
    "MPF_AMOUNT": 538.40,
    "HARBOR_MAINTENANCE": 1791.77,
    "Dairy_AMOUNT": 503.60
  },
  "addresses": {
    "CONSIGNEE": {
      "NAME": "THE HERSHEY COMPANY",
      "UNSTRUCTURED_STREET_ADDRESS": "19 E. CHOCOLATE AVE",
      "CITY": "HERSHEY",
      "COUNTRY_SUB_ENTITY": "PA",
      "POSTAL_CODE": "17033"
    },
    "IMPORTER": { },
    "BROKER": { }
  },
  "line_items": [
    {
      "ITEM_NUMBER": "001",
      "PRODUCT_DESCRIPTION": "COCO IN PRP CON OV 5.5 BFA",
      "PART_NUMBER": "496308",
      "COUNTRY_OF_ORIGIN": "IE",
      "MANUFACTURER_ID": "IEORNCOODUB",
      "NET_WEIGHT_QTY": "264 BAG",
      "ITEM_ENTERED_VALUE": 1433417,
      "ITEM_CHARGES": "C24000",
      "RELATIONSHIP": "NOT RELATED",
      "hts_data": [
        {
          "HTS_US_CODE": "1806.20.2400",
          "HTSUS_RATE": "5%",
          "DUTY_AND_TAXES": 71670.85
        },
        {
          "HTS_US_CODE": "499",
          "HTSUS_RATE": "0.3464%",
          "MPF_FEE": 4965.36,
          "_is_fee": true,
          "_note": "Calculated amount; actual billed is $538.40 (capped)"
        },
        {
          "HTS_US_CODE": "501",
          "HTSUS_RATE": "0.125%",
          "HMF_FEE": 1791.77,
          "_is_fee": true
        },
        {
          "HTS_US_CODE": "110",
          "HTSUS_RATE": "1.327%",
          "Dairy_FEE": 503.60,
          "_is_fee": true
        }
      ]
    }
  ],
  "validation_results": {
    "total_value_check": true,
    "duty_total_check": true,
    "hmf_mode_check": true,
    "mpf_validation": {
      "line_calculated": 4965.36,
      "shipment_billed": 538.40,
      "cap_applied": true,
      "status": "VALID - capped at maximum"
    },
    "line_item_filtering": {
      "items_in_document": 23,
      "valid_7501_items": 1,
      "excluded_reason": "12 container breakdowns (no values), 10 manifest items (4-digit numbers)"
    }
  }
}
```

## 13.2 Field Naming Rules

**CRITICAL: Use ONLY keys from US_Keys.xlsx**

| WRONG | CORRECT |
|-------|---------|
| `P_N` | `PART_NUMBER` |
| `unique_id` | (omit - use `ITEM_NUMBER`) |
| `delivery_order_no` | (omit - not CBP 7501) |
| `sender` | (omit - not CBP 7501) |
| `receiver` | (omit - not CBP 7501) |
| `dates` | (omit - use specific keys like `ENTRY_DATE`) |
| `other_details` | (omit - map to specific keys or exclude) |
| `total_items_count` | `valid_7501_line_items` in metadata |

## 13.3 Handling Non-Standard Source Data

When source document contains extra fields not in US_Keys:
1. **If mappable**: Map to standard key (P_N → PART_NUMBER)
2. **If CBP 7501 data but no key**: Note in extraction_metadata.unmapped_fields
3. **If non-7501 data**: Exclude entirely (delivery orders, manifests)
4. **If internal tracking**: Exclude from output (unique_id, etc.)
```

---

# SECTION 14: QUICK REFERENCE TABLES

## 14.1 Mode of Transport Codes

| Code | Description | HMF? |
|------|-------------|------|
| 10 | Vessel, non-container | YES |
| 11 | Vessel, container | YES |
| 12 | Border, Waterborne | YES |
| 20 | Rail, non-container | NO |
| 21 | Rail, container | NO |
| 30 | Truck, non-container | NO |
| 31 | Truck, container | NO |
| 32 | Auto | NO |
| 40 | Air, non-container | NO |
| 41 | Air, container | NO |
| 50 | Mail | NO |
| 60 | Passenger, hand-carried | NO |

## 14.2 Entry Type Codes

| Code | Type |
|------|------|
| 01 | Consumption - Free and Dutiable |
| 02 | Consumption - Quota/Visa |
| 03 | Consumption - AD/CVD |
| 06 | FTZ Consumption |
| 11 | Informal - Free and Dutiable |
| 21 | Warehouse |
| 23 | TIB (Temporary Importation Bond) |
| 31 | Warehouse Withdrawal - Consumption |

## 14.3 Bond Type Codes

| Code | Type |
|------|------|
| 0 | U.S. Government / Not required |
| 8 | Continuous |
| 9 | Single Transaction |

## 14.4 Relationship Codes

| Code | Meaning |
|------|---------|
| Y | Related parties |
| N | Not related |
| NOT RELATED | Not related (expanded) |
| RELATED | Related (expanded) |

---

# SECTION 15: TESTING CHECKLIST

Before deployment, verify:

**Form Detection & Mapping**
- [ ] Form version detection works for 7/21, 2/18, 5/22, 10/25
- [ ] Block number mapping correct per version
- [ ] Entry number validation passes for all broker formats

**Field Standards**
- [ ] All standard keys from US_Keys.xlsx used (no P_N, unique_id, etc.)
- [ ] No non-7501 fields included (sender, receiver, dates, other_details)
- [ ] PART_NUMBER used instead of P_N
- [ ] No unique_id field (use ITEM_NUMBER only)

**Line Item Filtering**
- [ ] 4-digit item numbers excluded (0001, 0002 = manifest)
- [ ] Container breakdown lines excluded (no value, no duty)
- [ ] "SAID TO CONTAIN" items excluded
- [ ] Lines with HTSUS_RATE = "N/A" and no values excluded
- [ ] valid_7501_line_items count accurate
- [ ] excluded_items metadata populated

**HTS Extraction**
- [ ] Multiple HTS codes extracted per line item
- [ ] HTS codes formatted with periods (XXXX.XX.XXXX)
- [ ] Fee codes (499, 501, 056, 110) distinguished from HTS codes

**Country & SPI**
- [ ] Country of origin derived correctly (O+XX, MFR ID, shipment)
- [ ] SPI codes handled per broker pattern

**Fee Validation**
- [ ] MPF cap logic verified (min $27.75, max $538.40)
- [ ] MPF anomalies flagged (> $538.40 for single entry)
- [ ] Line-level vs shipment-level MPF discrepancy documented
- [ ] HMF correctly set to 0 for non-vessel modes

**Address Handling**
- [ ] Addresses extracted for CONSIGNEE, IMPORTER, BROKER
- [ ] "SAME" consignee handled correctly
- [ ] Filer code vs broker name noted when different

**Totals Validation**
- [ ] Total entered value = sum of line item values
- [ ] Duty grand total = duty + tax + other fees
- [ ] Validation results populated in output

---

# SECTION 16: EXAMPLE CORRECTIONS

## 16.1 Before (Incorrect Extraction)

```json
{
  "delivery_order_no": "142400557",  // ❌ Non-7501 field
  "total_items_count": 23,           // ❌ Includes manifest data
  "items": [
    {
      "unique_id": "001",            // ❌ Non-standard key
      "P_N": "496308",               // ❌ Should be PART_NUMBER
      ...
    },
    {
      "ITEM_NUMBER": "002",          // ❌ No value/duty - container breakdown
      "hts_data": [{"HTS_US_CODE": "1806202400", "HTSUS_RATE": "N/A"}]
    },
    {
      "ITEM_NUMBER": "0001",         // ❌ 4-digit = manifest data
      ...
    }
  ],
  "sender": {...},                   // ❌ Non-7501 field
  "receiver": {...},                 // ❌ Non-7501 field
  "dates": {...},                    // ❌ Non-7501 field
  "other_details": {...}             // ❌ Non-7501 field
}
```

## 16.2 After (Correct Extraction)

```json
{
  "extraction_metadata": {
    "form_version": "2/18",
    "filer_code": "916",
    "broker": "BDP",
    "total_document_items": 23,
    "valid_7501_line_items": 1,
    "excluded_items": {
      "container_breakdowns": 12,
      "manifest_items": 10
    }
  },
  "shipment": {
    "ENTRY_NUMBER": "916-5187505-5",
    "MPF_AMOUNT": 614.35,
    ...
  },
  "line_items": [
    {
      "ITEM_NUMBER": "001",
      "PART_NUMBER": "496308",
      "ITEM_ENTERED_VALUE": 1433417,
      "hts_data": [
        {"HTS_US_CODE": "1806.20.2400", "HTSUS_RATE": "5%", "DUTY_AND_TAXES": 71670.85}
      ]
    }
  ],
  "validation_results": {
    "mpf_validation": {
      "shipment_mpf": 614.35,
      "expected_max": 538.40,
      "status": "ANOMALY",
      "flag": "MPF exceeds standard single-entry cap - review source"
    },
    "line_item_filtering": {
      "items_in_document": 23,
      "valid_7501_items": 1,
      "excluded": "12 container breakdowns, 10 manifest items"
    }
  }
}
```

---

*Master Prompt Version 3.0*
*Enhanced with single-line entry handling, MPF anomaly detection, and strict data filtering*
*Covers: GEODIS, BDP, KIS, EFP, CH Robinson, Expeditors, DHL, UPS, Kuehne+Nagel, FedEx*
*Form Versions: 7/21, 2/18, 5/22, 10/25*

"""

# API 2 - Not currently used (kept for reference)
# API2_AGENT_NAME = "Process Document Compressed"
# API2_WORKFLOW_ID = None
# API2_CUSTOM_INSTRUCTIONS = """..."""

# General Configuration
MAX_CONCURRENT_PDFS = 10
REQUEST_TIMEOUT = 300
UPLOAD_FOLDER = '/tmp/cbp_uploads'
OUTPUT_FOLDER = '/tmp/cbp_outputs'

# Create folders if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Claude API Configuration
# Load Claude API key from environment variable for security
CLAUDE_API_KEY = os.environ.get('CLAUDE_API_KEY', '')  # Set environment variable CLAUDE_API_KEY
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"


class CBP7501Normalizer:
    """
    Complete CBP Form 7501 Normalizer with 80 field mappings
    Maps extracted API data to standardized Excel schema
    """
    
    def __init__(self):
        self.field_mapping = self._create_field_mapping()
    
    def _create_field_mapping(self) -> Dict[str, str]:
        """
        Complete mapping of 80 fields from API response to Excel column names
        Matches the exact structure in 7501_US_Entry_Summary_-KX-071I-108_All_Data.xlsx
        """
        return {
            # Header Fields (CS - Customs Summary)
            'shipment_id': 'CS Shipment ID',
            'entry_number': '1. CS Entry Number',
            'entry_type': '2. CS Entry Type',
            'summary_date': '3. CS Summary Date',
            'surety_number': '4. CS Surety Number',
            'bond_type': '5. CS Bond Type',
            'port_of_entry': '6. CS Port Of Entry',
            'entry_date': '7. CS Entry Date',
            'transport_name': '8. CS Transport Name',
            'carrier_name': '8. CS Carrier Name',
            'scac_code': '8. CS SCAC Code',
            'voyage_number': '8. CS Voyage Number',
            'mode_of_transport': '9. CS Mode Of Transport',
            'country_of_origin': '10. CS Country Of Origin',
            'import_date': '11. CS Import Date',
            'master_bol_number': '12. CS Master BOL Number',
            'manufacturer_id_header': '13. CS Manufacturer ID',
            'export_country': '14. CS Export Country',
            'export_date': '15. CS Export Date',
            'it_number': '16. CS IT Number',
            'it_date': '17. CS IT Date',
            'missing_docs': '18. CS Missing Docs',
            'port_of_lading': '19. CS Port Of Lading',
            'port_of_unlading': '20. CS Port Of Unlading',
            'location_firms_code': '21. CS Location Firms Code',
            'consignee_id': '22. CS Consignee ID',
            'importer_id': '23. CS Importer ID',
            'ref_number': '24. CS Ref Number',
            'consignee_name': '25. CS Consignee Name',
            'importer_name': '26. CS Importer Name',
            
            # Line Item Fields (CM - Customs Merchandise)
            'item_number': '27. CM Item Number',
            'item_country_of_origin': '27. CM Country Of Origin',
            'item_export_country': '27. CM Export Country Code',
            'free_trade': '27. CM Free Trade',
            'bol_number': '28. CS BOL Number',
            'items_description': '28. CS Items Description',
            'invoice_no': '28. CM Invoice No',
            'po_number': '28. CM PO Number',
            'manufacturer_id': '28. CM Manufacturer ID',
            'recon_value': '28. CM Recon Value',
            'textile_category': '28. CM Textile Category',
            'total_pack_qty': '28. CM Total Pack Qty',
            'total_pack_type': '28. CM Total Pack Type',
            'part_number': '28. CM Part Number',
            'invoice_amount': '28. CM Invoice Amount',
            'value_addition': '28. CM Value Addition Amount',
            'total_invoice_amount': '28. CM Total Invoice Amount',
            
            # Duty Fields (CD - Customs Duty)
            'hts_code': '29. CD HTS US Code',
            'hts_description': '29. CD HTS Description',
            
            # Pack quantities and types
            'pack_type_2': '31. CM Item Pack Type 2',
            'pack_qty_2': '31. CM Item Pack Qty 2',
            'pack_type_1': '31. CM Item Pack Type 1',
            'pack_qty_1': '31. CM Item Pack Qty 1',
            
            # Relationship and charges
            'relationship': '32. CM Relationship',
            'item_charges': '32. CM Item Charges',
            'entered_value': '32. CM Item Entered Value',
            'first_sale': '32. CM First Sale',
            
            # Rates and Fees at header level
            'hmf_rate_header': '33. CS HMF Rate',
            'hmf_fee_header': '33. CS HMF Fee',
            
            # Duty rates and amounts
            'hts_rate': '33. CD HTS US Rate',
            'ad_valorem_duty': '34. CD Ad Valorem Duty',
            'cotton_fee_rate': '33. CD Cotton Fee Rate',
            'cotton_fee_amount': '34. CD Cotton Fee Amount',
            'mpf_rate': '33. CD MPF Rate',
            'mpf_fee': '34. CD MPF Fee',
            'hmf_rate': '33. CD HMF Rate',
            'hmf_fee': '34. CD HMF Fee',
            'specific_rate': '33. CD Specific Rate',
            'specific_duty': '34. CD Specific Duty',
            'duty_and_taxes': '34. CD Duty And Taxes',
            
            # Totals (CS - Customs Summary)
            'total_entered_value': '35. CS Total Entered Value',
            'totals_duty': '37. CS Totals Duty',
            'totals_tax': '38. CS Totals Tax',
            'mpf_amount': '39. CS MPF Amount',
            'cotton_amount': '39. CS Cotton Amount',
            'total_other_fees': '39. CS Total Other Fees',
            'duty_grand_total': '40. CS Duty Grand Total',
            
            # Declarant and Broker Info
            'declarant_name': '41. CS Declarant Name',
            'broker_name': '42. CS Broker Name',
            'broker_code': '43. CS Broker Code',
        }
    
    def normalize(self, raw_json: Dict) -> List[Dict]:
        """
        Normalize raw JSON response to Excel-ready format
        Returns one row per HTS classification (handles nested hts_classifications)
        
        CRITICAL: If a line item has multiple HTS codes, create multiple rows
        Example: 1 item with 3 HTS codes = 3 Excel rows
        """
        # Extract header data
        header_data = self._extract_header_data(raw_json)
        
        # Extract line items
        line_items = self._extract_line_items(raw_json)
        
        print(f"      📊 Processing {len(line_items)} line items...")
        
        # Create normalized rows (one per HTS classification)
        normalized_rows = []
        current_line_no = None
        row_counter = 1
        
        for idx, line_item in enumerate(line_items, 1):
            # Track line numbers - use line_number from item
            item_line_no = line_item.get('line_number') or line_item.get('line_no') or line_item.get('line_item_number')
            if item_line_no:
                current_line_no = item_line_no
                row_counter = 1
            
            # Check if item has primary_hts structure (a79 format)
            primary_hts = line_item.get('primary_hts', {})
            additional_hts_codes = []
            
            if isinstance(primary_hts, dict) and primary_hts:
                # Extract additional HTS codes from primary_hts
                additional_hts_codes = primary_hts.get('additional_hts_codes', [])
                
                # Create row for primary HTS
                row = header_data.copy()  # Start with header data
                
                # Add line item number
                row['27. CM Item Number'] = current_line_no or str(idx).zfill(3)
                
                # Map base line item fields (description, charge_type, SPI, CO, etc.)
                line_item_data = self._map_line_item_fields(line_item, idx)
                row.update(line_item_data)
                
                # Map primary HTS data (hts_code, entered_value, rate, duty_amount, etc.)
                hts_data = self._map_hts_classification(primary_hts)
                row.update(hts_data)
                
                # Extract quantities from primary_hts
                if 'quantity' in primary_hts:
                    qty = primary_hts['quantity']
                    if isinstance(qty, dict):
                        row['31. CM Item Pack Qty 2'] = qty.get('value', '')
                        row['31. CM Item Pack Type 2'] = qty.get('unit', '')
                
                # Extract entered_value from primary_hts
                if 'entered_value' in primary_hts:
                    row['32. CM Item Entered Value'] = primary_hts['entered_value']
                
                # Extract rate from primary_hts
                if 'rate' in primary_hts:
                    row['33. CD HTS US Rate'] = primary_hts['rate']
                
                # Extract duty_amount from primary_hts
                if 'duty_amount' in primary_hts:
                    row['34. CD Duty And Taxes'] = primary_hts['duty_amount']
                
                normalized_rows.append(row)
                
                # Create rows for additional HTS codes
                for additional_hts in additional_hts_codes:
                    if isinstance(additional_hts, dict):
                        additional_row = header_data.copy()
                        additional_row['27. CM Item Number'] = current_line_no or str(idx).zfill(3)
                        
                        # Map base line item fields
                        line_item_data = self._map_line_item_fields(line_item, idx)
                        additional_row.update(line_item_data)
                        
                        # Map additional HTS data
                        additional_hts_data = self._map_hts_classification(additional_hts)
                        additional_row.update(additional_hts_data)
                        
                        normalized_rows.append(additional_row)
            
            # Check if item has nested HTS classifications array (alternative format)
            elif line_item.get('hts_classifications'):
                hts_classifications = line_item.get('hts_classifications', [])
                # EXPAND: Create one row per HTS classification
                for hts in hts_classifications:
                    row = header_data.copy()  # Start with header data
                    
                    # Add line item number
                    row['27. CM Item Number'] = current_line_no or str(idx).zfill(3)
                    
                    # Map base line item fields (part number, quantities, etc.)
                    line_item_data = self._map_line_item_fields(line_item, idx)
                    row.update(line_item_data)
                    
                    # Override with HTS-specific data
                    hts_data = self._map_hts_classification(hts)
                    row.update(hts_data)
                    
                    normalized_rows.append(row)
            else:
                # No nested HTS - treat line item itself as having HTS data
                row = header_data.copy()  # Start with header data
                
                # Add line item number
                row['27. CM Item Number'] = current_line_no or str(idx).zfill(3)
                
                # Map line item fields
                line_item_data = self._map_line_item_fields(line_item, idx)
                row.update(line_item_data)
                
                normalized_rows.append(row)
        
        print(f"      ✅ Generated {len(normalized_rows)} Excel rows")
        return normalized_rows
    
    def _map_hts_classification(self, hts: Dict) -> Dict:
        """Map HTS classification data to Excel columns"""
        mapped = {}
        
        # Get HTS description first to check for special cases
        hts_description = None
        for field in ['description', 'hts_description']:
            if field in hts:
                hts_description = hts[field]
                break
        
        # HTS-specific field mappings
        field_mappings = {
            'hts_code': ['htsus_no', 'hts_code', 'hts', 'hs_code'],
            'hts_description': ['description', 'hts_description'],
            'hts_rate': ['htsus_rate', 'hts_rate', 'duty_rate', 'rate'],
            'ad_valorem_duty': ['ad_valorem_duty', 'duty'],
            'duty_and_taxes': ['duty_and_ir_tax', 'duty_and_tax', 'total_duty', 'duty', 'duty_amount'],
            'entered_value': ['entered_value', 'value', 'entered_val', 'amount'],
            'cotton_fee_rate': ['cotton_fee_rate', 'cotton_rate'],
            'cotton_fee_amount': ['cotton_fee', 'cotton', 'cotton_fee_amount'],
            'mpf_fee': ['mpf_fee', 'mpf'],
            'mpf_rate': ['mpf_rate', 'merchandise_processing_fee_rate'],
            'hmf_fee': ['hmf_fee', 'hmf'],
            'hmf_rate': ['hmf_rate', 'harbor_maintenance_fee_rate'],
            'specific_rate': ['specific_rate', 'specific_duty_rate'],
            'specific_duty': ['specific_duty', 'specific_duty_amount'],
        }
        
        # Map all HTS fields
        for key, possible_fields in field_mappings.items():
            excel_col = self.field_mapping.get(key)
            if excel_col:
                for field in possible_fields:
                    if field in hts:
                        value = hts[field]
                        # Handle special conversions
                        if value is not None:
                            # Convert FREE to actual text
                            if isinstance(value, str) and value.upper() == 'FREE':
                                if 'rate' in key:
                                    value = 'FREE'
                                elif 'duty' in key or 'amount' in key:
                                    value = 0.0
                            mapped[excel_col] = value
                        break
        
        # Extract quantities from nested quantity objects
        if 'quantity' in hts and isinstance(hts['quantity'], dict):
            qty = hts['quantity']
            mapped['31. CM Item Pack Qty 2'] = qty.get('value', '')
            mapped['31. CM Item Pack Type 2'] = qty.get('unit', '')
        
        # Extract gross_weight if present
        if 'gross_weight' in hts:
            gw = hts['gross_weight']
            if isinstance(gw, dict):
                mapped['31. CM Item Pack Qty 1'] = gw.get('value', '')
                mapped['31. CM Item Pack Type 1'] = gw.get('unit', 'KG')  # Default to KG
            else:
                mapped['31. CM Item Pack Qty 1'] = gw
        
        # Extract net_quantity if present
        if 'net_quantity' in hts:
            nq = hts['net_quantity']
            if isinstance(nq, dict):
                mapped['31. CM Item Pack Qty 2'] = nq.get('value', '')
                mapped['31. CM Item Pack Type 2'] = nq.get('unit', '')
        
        # Handle nested MPF data - Map to specific MPF columns for 10-digit HTS codes
        if 'mpf' in hts and isinstance(hts['mpf'], dict):
            mpf_data = hts['mpf']
            
            # Map MPF fee amount to CD MPF Fee column (34. CD MPF Fee)
            if 'mpf_amount' in mpf_data:
                mapped['34. CD MPF Fee'] = mpf_data['mpf_amount']
            
            # Map MPF rate to CD MPF Rate column (33. CD MPF Rate)
            if 'mpf_hts_rate' in mpf_data:
                mapped['33. CD MPF Rate'] = mpf_data['mpf_hts_rate']
            
            # Map MPF HTS code to the main HTS code if it's a 10-digit code
            if 'mpf_hts_code' in mpf_data:
                mpf_hts_code = mpf_data['mpf_hts_code']
                # Ensure it's a 10-digit HTS code
                if len(str(mpf_hts_code).replace('.', '')) >= 10:
                    mapped['29. CD HTS US Code'] = mpf_hts_code
                else:
                    # Store as reference if not 10-digit
                    mapped['29. CD HTS US Code (MPF)'] = mpf_hts_code
            
            # Map MPF amount to duty_and_taxes if not already set
            if 'mpf_amount' in mpf_data and '33. CD Duty And IR Tax' not in mapped:
                mapped['33. CD Duty And IR Tax'] = mpf_data['mpf_amount']
            
            # Map MPF rate to HTS rate if not already set
            if 'mpf_hts_rate' in mpf_data and '33. CD HTS US Rate' not in mapped:
                mapped['33. CD HTS US Rate'] = mpf_data['mpf_hts_rate']
        
        # CRITICAL FIX: If HTS description contains "Merchandise Processing Fee",
        # place MPF rate into HTS US Rate column (33. CD HTS US Rate)
        # This handles cases where the HTS line IS the MPF fee line
        if hts_description and 'Merchandise Processing Fee' in hts_description:
            # Move MPF rate to HTS rate column
            mpf_rate_col = self.field_mapping.get('mpf_rate')  # 33. CD MPF Rate
            hts_rate_col = self.field_mapping.get('hts_rate')  # 33. CD HTS US Rate
            
            if mpf_rate_col in mapped and hts_rate_col:
                # Move the MPF rate value to HTS rate column
                mapped[hts_rate_col] = mapped[mpf_rate_col]
                # Optionally clear the MPF rate column to avoid duplication
                # (commented out to keep both for now)
                # del mapped[mpf_rate_col]
        
        return mapped
    
    def _extract_header_data(self, raw_json: Dict) -> Dict:
        """Extract header-level data from raw JSON"""
        data = {}
        
        # Navigate to entry_summary (handle different response structures)
        if 'entry_summary' in raw_json:
            entry = raw_json['entry_summary']
        elif 'data' in raw_json and 'entry_summary' in raw_json['data']:
            entry = raw_json['data']['entry_summary']
        else:
            entry = raw_json
        
        # Check for invoice header lines in items and extract invoice number
        invoice_number = None
        items = raw_json.get('items', []) or entry.get('line_items', [])
        for item in items:
            line_no = item.get('line_no', '') or ''
            description = item.get('description_of_merchandise', '') or ''
            
            # Check if this is an invoice header line
            if isinstance(line_no, str) and line_no.upper().startswith('INV'):
                # Extract invoice number from description
                if 'Commercial Invoice #:' in description or 'COMMERCIAL INVOICE #:' in description.upper():
                    import re
                    match = re.search(r'[Cc]ommercial [Ii]nvoice #?:?\s*(\d+)', description)
                    if match:
                        invoice_number = match.group(1)
                        print(f"      ℹ️  Extracted invoice number from header: {invoice_number}")
                        break
        
        # Field mappings with alternative names including AI79 format
        field_mappings = {
            'shipment_id': ['shipment_id', 'shipment_number'],
            'entry_number': ['filer_code_entry_no', 'filer_code_entry_number', 'entry_number', 'entry_no'],
            'entry_type': ['entry_type', 'type'],
            'summary_date': ['summary_date', 'filing_date'],
            'surety_number': ['surety_number', 'surety_no'],
            'bond_type': ['bond_type'],
            'port_of_entry': ['port_code', 'port_of_entry', 'entry_port'],
            'entry_date': ['entry_date'],
            'transport_name': ['transport_name'],
            'carrier_name': ['importing_carrier', 'carrier_name', 'carrier'],
            'scac_code': ['scac_code', 'scac'],
            'voyage_number': ['voyage_number', 'voyage_no', 'voyage'],
            'mode_of_transport': ['mode_of_transport', 'transport_mode'],
            'country_of_origin': ['country_of_origin', 'origin_country'],
            'import_date': ['import_date'],
            'master_bol_number': ['bl_awb_no', 'bl_awb_number', 'b_l_or_awb_no', 'master_bol', 'bol_awb_no'],
            'manufacturer_id_header': ['manufacturer_id'],
            'export_country': ['exporting_country', 'export_country'],
            'export_date': ['export_date'],
            'it_number': ['it_number', 'it_no'],
            'it_date': ['it_date'],
            'missing_docs': ['missing_docs', 'missing_documents'],
            'port_of_lading': ['port_of_lading', 'lading_port', 'foreign_port_of_lading'],
            'port_of_unlading': ['us_port_of_unlading', 'port_of_unlading', 'unlading_port'],
            'location_firms_code': ['location_of_goods', 'location_of_goods_go_number', 'location_code', 'firms_code'],
            'consignee_id': ['consignee_no', 'consignee_number', 'consignee_id'],
            'importer_id': ['importer_no', 'importer_number', 'importer_id'],
            'ref_number': ['ref_number', 'reference_number'],
            'consignee_name': ['ultimate_consignee_name', 'ultimate_consignee_name_address', 'consignee_name'],
            'importer_name': ['importer_of_record_name', 'importer_of_record_name_address', 'importer_name'],
            'total_entered_value': ['total_entered_value', 'entered_value_usd', 'total_value'],
            'totals_duty': ['duty', 'total_duty'],
            'totals_tax': ['tax', 'total_tax'],
            'mpf_amount': ['mpf_amount', 'mpf', 'merchandise_processing_fee_total'],
            'cotton_amount': ['cotton_amount', 'cotton_fee'],
            'total_other_fees': ['other', 'other_fees', 'total_other_fees'],
            'duty_grand_total': ['total', 'grand_total'],
            'declarant_name': ['declarant_name'],
            'broker_name': ['broker_filer_information', 'broker_name'],
            'broker_code': ['broker_importer_file_no', 'broker_importer_file_number', 'broker_code'],
            # Header-level HMF fields
            'hmf_rate_header': ['hmf_rate', 'harbor_maintenance_fee_rate'],
            'hmf_fee_header': ['hmf_fee', 'hmf', 'harbor_maintenance_fee'],
        }
        
        # Extract data using alternative field names
        for key, possible_fields in field_mappings.items():
            excel_col = self.field_mapping.get(key)
            if excel_col:
                for field in possible_fields:
                    if field in entry:
                        value = entry[field]
                        # Handle nested name/address objects
                        if isinstance(value, dict):
                            if 'name' in value:
                                data[excel_col] = value['name']
                                # Also try to get address if there's an address column
                                if 'address' in value:
                                    addr_parts = [value.get('address', ''),
                                                value.get('city', ''),
                                                value.get('state', ''),
                                                value.get('zip', '')]
                                    full_address = ', '.join([p for p in addr_parts if p])
                                    # Store full address if there's a column for it
                                    addr_col = excel_col.replace('Name', 'Address')
                                    if addr_col in self.field_mapping.values():
                                        data[addr_col] = full_address
                        else:
                            data[excel_col] = value
                        break
        
        # If we extracted an invoice number from header line, use it
        # Note: This will be applied to ALL rows since it's header data
        # Individual line items may have their own invoice numbers that override this
        if invoice_number:
            invoice_col = self.field_mapping.get('invoice_no')
            if invoice_col and invoice_col not in data:
                data[invoice_col] = invoice_number
        
        return data
    
    def _extract_line_items(self, raw_json: Dict) -> List[Dict]:
        """
        Extract line items from raw JSON
        Filters out invoice header lines (line_no starting with "INV#")
        """
        # Navigate to line items
        if 'entry_summary' in raw_json and 'line_items' in raw_json['entry_summary']:
            items = raw_json['entry_summary']['line_items']
        elif 'data' in raw_json and 'entry_summary' in raw_json['data']:
            if 'line_items' in raw_json['data']['entry_summary']:
                items = raw_json['data']['entry_summary']['line_items']
            else:
                items = []
        elif 'line_items' in raw_json:
            items = raw_json['line_items']
        elif 'items' in raw_json:
            items = raw_json['items']
        else:
            items = []
        
        # Filter out invoice header lines
        filtered_items = []
        for item in items:
            line_no = item.get('line_no', '') or item.get('line_item_number', '') or ''
            description = item.get('description_of_merchandise', '') or item.get('description', '') or ''
            
            # Skip invoice header lines
            # Indicators: line_no starts with "INV#" or description contains "Commercial Invoice #:"
            is_invoice_header = False
            
            if isinstance(line_no, str):
                if line_no.upper().startswith('INV'):
                    is_invoice_header = True
                    print(f"      ⚠️  Skipping invoice header line: {line_no}")
            
            if isinstance(description, str):
                if 'Commercial Invoice #:' in description or 'COMMERCIAL INVOICE #:' in description.upper():
                    is_invoice_header = True
                    if not isinstance(line_no, str) or not line_no.upper().startswith('INV'):
                        print(f"      ⚠️  Skipping invoice header by description: {description[:50]}...")
            
            # Skip lines with no entered_value and no HTS code (likely summary lines)
            # Check for entered_value at item level or nested in primary_hts
            has_value = item.get('entered_value') is not None
            if not has_value and 'primary_hts' in item:
                primary_hts = item['primary_hts']
                if isinstance(primary_hts, dict):
                    has_value = primary_hts.get('entered_value') is not None
            
            # Check for HTS code at item level or nested in primary_hts
            has_hts = (item.get('htsus_no') or item.get('a_htsus_no') or 
                      item.get('hts_code') or item.get('hts_us_no'))
            if not has_hts and 'primary_hts' in item:
                primary_hts = item['primary_hts']
                if isinstance(primary_hts, dict):
                    has_hts = bool(primary_hts.get('hts_code') or primary_hts.get('htsus_no'))
            
            if not is_invoice_header:
                # Only include items that have either a value or an HTS code
                # This filters out summary/header lines without being too aggressive
                # Also include items with primary_hts object (even if empty) as they're valid line items
                has_primary_hts = 'primary_hts' in item and isinstance(item.get('primary_hts'), dict)
                if has_value or has_hts or has_primary_hts or (isinstance(line_no, str) and line_no.isdigit()):
                    filtered_items.append(item)
                else:
                    print(f"      ⚠️  Skipping line without value/HTS: {line_no}")
        
        print(f"      📊 Filtered: {len(items)} → {len(filtered_items)} line items (skipped {len(items) - len(filtered_items)} header/summary lines)")
        return filtered_items
    
    def _map_line_item_fields(self, line_item: Dict, line_number: int) -> Dict:
        """Map line item data to Excel column names"""
        mapped = {}
        
        # Field mappings with alternative names including AI79 format
        field_mappings = {
            'hts_code': ['htsus_no', 'hts_code', 'hts', 'hs_code', 'hts_us_no', 'hts_code_a'],
            'hts_description': ['description', 'hts_description', 'item_description', 'desc', 'description_of_merchandise', 'product_description'],
            'part_number': ['part_number', 'part_no', 'item_no', 'party_number', 'item_number', 'p_n'],
            'invoice_no': ['invoice_number', 'invoice_no'],
            'po_number': ['po_number', 'po_no', 'purchase_order'],
            'manufacturer_id': ['manufacturer_id', 'mfg_id'],
            'entered_value': ['entered_value', 'value', 'entered_val', 'amount', 'entered_value_a'],
            'pack_qty_1': ['gross_weight', 'weight', 'wt', 'qty1', 'grossweight_a'],
            'pack_type_1': ['weight_unit', 'wt_unit', 'unit1', 'net_quantity_in_htsus_units'],
            'pack_qty_2': ['quantity', 'qty', 'qty2'],
            'pack_type_2': ['qty_unit', 'unit', 'unit2'],
            'relationship': ['relationship', 'rel', 'related'],
            'item_charges': ['charges', 'charge_code', 'chgs', 'chgs_b', 'charge_type'],
            'hts_rate': ['htsus_rate', 'hts_rate', 'duty_rate', 'rate', 'hts_us_rate', 'hts_us_a_rate'],
            'duty_and_taxes': ['duty_and_ir_tax', 'duty_and_tax', 'total_duty', 'duty', 'duty_amount', 'duty_and_ir_tax_dollars', 'duty_and_ir_tax_cents'],
            'item_country_of_origin': ['country_of_origin', 'origin_country'],
            'item_export_country': ['export_country', 'exporting_country'],
            'invoice_amount': ['invoice_amount', 'invoice_value'],
            'recon_value': ['recon_value', 'reconciliation_value'],
            'textile_category': ['textile_category', 'textile_cat'],
            'mpf_rate': ['mpf_rate', 'merchandise_processing_fee_rate'],
            'mpf_fee': ['mpf_fee', 'mpf', 'merchandise_processing_fee_tax', 'merchandise_processing_fee'],
            'ada_cvd_no': ['ada_cvd_no', 'ada_cvd'],
            # Additional fields that were missing extraction logic
            'free_trade': ['free_trade', 'free_trade_agreement', 'fta'],
            'bol_number': ['bol_number', 'bol_no', 'bill_of_lading', 'b_l_no'],
            'items_description': ['items_description', 'merchandise_description', 'item_desc'],
            'total_pack_qty': ['total_pack_qty', 'total_quantity', 'total_qty'],
            'total_pack_type': ['total_pack_type', 'total_pack_unit', 'total_unit'],
            'value_addition': ['value_addition', 'value_addition_amount', 'added_value'],
            'total_invoice_amount': ['total_invoice_amount', 'total_invoice_value', 'invoice_total'],
            'first_sale': ['first_sale', 'first_sale_price'],
            'cotton_fee_rate': ['cotton_fee_rate', 'cotton_rate'],
            'cotton_fee_amount': ['cotton_fee_amount', 'cotton_fee', 'cotton'],
            'specific_rate': ['specific_rate', 'specific_duty_rate'],
            'specific_duty': ['specific_duty', 'specific_duty_amount'],
        }
        
        # Note: manifest_qty is now HEADER-LEVEL only (not extracted from line items)
        # It should be extracted from shipment_info.manifest_info.manifest_qty
        
        # Map all fields
        for key, possible_fields in field_mappings.items():
            excel_col = self.field_mapping.get(key)
            if excel_col:
                for field in possible_fields:
                    if field in line_item:
                        value = line_item[field]
                        # Handle special conversions
                        if value is not None:
                            # Convert FREE to actual text
                            if isinstance(value, str) and value.upper() == 'FREE':
                                if 'rate' in key:
                                    value = 'FREE'
                                elif 'duty' in key or 'amount' in key:
                                    value = '0.00'
                            # Remove commas from numbers
                            if isinstance(value, str) and any(c.isdigit() for c in value):
                                value = value.replace(',', '')
                            mapped[excel_col] = value
                        break
        
        # Handle nested MPF data at line item level
        if 'mpf' in line_item and isinstance(line_item['mpf'], dict):
            mpf_data = line_item['mpf']
            
            # Map MPF fee amount to CD MPF Fee column (34. CD MPF Fee)
            if 'mpf_amount' in mpf_data:
                mapped['34. CD MPF Fee'] = mpf_data['mpf_amount']
            
            # Map MPF rate to CD MPF Rate column (33. CD MPF Rate)
            if 'mpf_hts_rate' in mpf_data:
                mapped['33. CD MPF Rate'] = mpf_data['mpf_hts_rate']
            
            # Map MPF HTS code to the main HTS code if it's a 10-digit code
            if 'mpf_hts_code' in mpf_data:
                mpf_hts_code = mpf_data['mpf_hts_code']
                # Ensure it's a 10-digit HTS code
                if len(str(mpf_hts_code).replace('.', '')) >= 10:
                    mapped['29. CD HTS US Code'] = mpf_hts_code
                else:
                    # Store as reference if not 10-digit
                    mapped['29. CD HTS US Code (MPF)'] = mpf_hts_code
            
            # Map MPF amount to duty_and_taxes if not already set
            if 'mpf_amount' in mpf_data and '33. CD Duty And IR Tax' not in mapped:
                mapped['33. CD Duty And IR Tax'] = mpf_data['mpf_amount']
            
            # Map MPF rate to HTS rate if not already set
            if 'mpf_hts_rate' in mpf_data and '33. CD HTS US Rate' not in mapped:
                mapped['33. CD HTS US Rate'] = mpf_data['mpf_hts_rate']
        
        # Handle MPF data that's not in nested structure (like last item line)
        # Check for direct MPF fields in the line item
        mpf_amount = None
        mpf_rate = None
        mpf_hts_code = None
        
        # Look for MPF amount in various field names
        for field in ['mpf_amount', 'mpf', 'merchandise_processing_fee', 'merchandise_processing_fee_tax']:
            if field in line_item and line_item[field]:
                mpf_amount = line_item[field]
                break
        
        # Look for MPF rate in various field names
        for field in ['mpf_rate', 'merchandise_processing_fee_rate']:
            if field in line_item and line_item[field]:
                mpf_rate = line_item[field]
                break
        
        # Look for MPF HTS code
        for field in ['mpf_hts_code', 'mpf_hts', 'merchandise_processing_fee_hts']:
            if field in line_item and line_item[field]:
                mpf_hts_code = line_item[field]
                break
        
        # If we found MPF data not in nested structure, map it
        if mpf_amount or mpf_rate or mpf_hts_code:
            if mpf_amount:
                mapped['34. CD MPF Fee'] = mpf_amount
                if '33. CD Duty And IR Tax' not in mapped:
                    mapped['33. CD Duty And IR Tax'] = mpf_amount
            
            if mpf_rate:
                mapped['33. CD MPF Rate'] = mpf_rate
                if '33. CD HTS US Rate' not in mapped:
                    mapped['33. CD HTS US Rate'] = mpf_rate
            
            if mpf_hts_code:
                # Ensure it's a 10-digit HTS code
                if len(str(mpf_hts_code).replace('.', '')) >= 10:
                    mapped['29. CD HTS US Code'] = mpf_hts_code
                else:
                    mapped['29. CD HTS US Code (MPF)'] = mpf_hts_code
        
        return mapped
    
    def to_excel(self, normalized_data: List[Dict], output_path: str) -> str:
        """Export normalized data to Excel file"""
        if not normalized_data:
            raise ValueError("No data to export")
        
        # Create DataFrame
        df = pd.DataFrame(normalized_data)
        
        # Ensure all 80 columns exist in the correct order
        all_columns = list(self.field_mapping.values())
        
        # Add any missing columns with empty values
        for col in all_columns:
            if col not in df.columns:
                df[col] = ''
        
        # Reorder columns to match expected order
        df = df[all_columns]
        
        # Export to Excel
        df.to_excel(output_path, index=False, engine='openpyxl')
        
        return output_path
    
    def to_json(self, normalized_data: List[Dict], output_path: str, indent: int = 2, extracted_data: Dict = None, raw_a79_data: Dict = None) -> str:
        """Export JSON exactly as it comes from A79 - header + items array, no modifications"""
        # Prefer raw_a79_data (unparsed) over extracted_data (parsed) to preserve all header fields
        if raw_a79_data:
            output = raw_a79_data
        elif extracted_data:
            output = extracted_data
        else:
            raise ValueError("No data to export")
        
        # Export exactly as it comes from A79 - no modifications, no normalization
        # This preserves all header fields (document_type, filer_code_entry_number, etc.)
        # and the items array exactly as A79 returns it
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=indent, ensure_ascii=False)
        
        return output_path


def split_pdf_by_pages(filepath):
    """
    Split PDF into first page and remaining pages
    
    Returns:
        tuple: (first_page_bytes, rest_pages_bytes) as base64 strings
    """
    reader = PdfReader(filepath)
    total_pages = len(reader.pages)
    
    print(f"   📄 PDF has {total_pages} pages")
    
    # Extract first page
    first_page_writer = PdfWriter()
    first_page_writer.add_page(reader.pages[0])
    
    first_page_buffer = io.BytesIO()
    first_page_writer.write(first_page_buffer)
    first_page_bytes = first_page_buffer.getvalue()
    first_page_base64 = base64.b64encode(first_page_bytes).decode('utf-8')
    
    print(f"   📄 First page: {len(first_page_bytes)} bytes")
    
    # Extract remaining pages if they exist
    rest_pages_base64 = None
    if total_pages > 1:
        rest_pages_writer = PdfWriter()
        for i in range(1, total_pages):
            rest_pages_writer.add_page(reader.pages[i])
        
        rest_pages_buffer = io.BytesIO()
        rest_pages_writer.write(rest_pages_buffer)
        rest_pages_bytes = rest_pages_buffer.getvalue()
        rest_pages_base64 = base64.b64encode(rest_pages_bytes).decode('utf-8')
        
        print(f"   📄 Remaining pages (2-{total_pages}): {len(rest_pages_bytes)} bytes (line items)")
    
    return first_page_base64, rest_pages_base64


def call_api(api_key, api_url, pdf_base64, custom_instructions, agent_name, workflow_id, page_description):
    """
    Call AI79 Public Workflow API with PDF and custom instructions
    
    Args:
        api_key: Authorization key
        api_url: API endpoint
        pdf_base64: Base64 encoded PDF
        custom_instructions: Text instructions for extraction
        agent_name: Name of the AI agent to use for processing
        workflow_id: Optional workflow ID (if None, uses agent_name)
        page_description: Description for logging
    
    Returns:
        dict: API response data with actual extraction results
    """
    import requests
    import time
    
    logger.info(f"🚀 Starting API call for {page_description}")
    logger.debug(f"API URL: {api_url}")
    logger.debug(f"Agent Name: {agent_name}")
    logger.debug(f"Workflow ID: {workflow_id}")
    logger.debug(f"PDF Size: {len(pdf_base64)} characters (base64)")
    logger.debug(f"Instructions Length: {len(custom_instructions)} characters")
    
    # If workflow_id is provided, use workflow-specific endpoint
    if workflow_id:
        api_url = f"https://klearnow.prod.a79.ai/api/v1/public/workflow/{workflow_id}/run"
        logger.info(f"Using workflow-specific endpoint: {api_url}")
        print(f"   🚀 Calling API for {page_description}...")
        print(f"      Endpoint: {api_url}")
        print(f"      Workflow ID: {workflow_id}")
        print(f"      Instructions: {custom_instructions[:80]}...")
    else:
        logger.info(f"Using agent-based endpoint: {api_url}")
        print(f"   🚀 Calling API for {page_description}...")
        print(f"      Endpoint: {api_url}")
        print(f"      Agent: {agent_name}")
        print(f"      Instructions: {custom_instructions[:80]}...")
    
    # Prepare payload with agent_inputs structure as required by AI79 API
    payload = {
        "agent_inputs": {
            "pdf_document": pdf_base64,
            "custom_instructions": custom_instructions
        }
    }
    
    # Add agent_name if no workflow_id (workflow_id goes in URL)
    if not workflow_id:
        payload["agent_name"] = agent_name
    
    logger.debug(f"Payload structure: {list(payload.keys())}")
    logger.debug(f"Agent inputs: {list(payload['agent_inputs'].keys())}")
    print(f"      📦 Payload keys: {list(payload.keys())}")
    print(f"      📦 Agent inputs: {list(payload['agent_inputs'].keys())}")
    
    # Validate API key before making request
    if not api_key or api_key.strip() == '':
        error_msg = "API key is empty. Please set A79_API_KEY environment variable."
        logger.error(error_msg)
        raise Exception(error_msg)
    
    # Convert to JSON string for logging
    payload_json = json.dumps(payload)
    logger.debug(f"Payload JSON size: {len(payload_json)} characters")
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'Accept': '*/*'
    }
    logger.debug(f"Request headers: {headers}")
    # Don't log the full API key, just confirm it's set
    logger.debug(f"Authorization header set: {'Yes' if api_key else 'No'}")
    
    logger.info(f"Sending POST request to {api_url}")
    start_time = time.time()
    
    # Use json= parameter instead of data= to match test files and ensure proper serialization
    # This automatically sets Content-Type and handles JSON encoding
    response = requests.post(
        api_url,
        json=payload,  # Pass dict directly, requests will serialize it
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )
    
    request_time = time.time() - start_time
    logger.info(f"Request completed in {request_time:.2f} seconds")
    logger.debug(f"Response status: {response.status_code}")
    logger.debug(f"Response headers: {dict(response.headers)}")
    
    print(f"      Status: {response.status_code}")
    print(f"      ⏱️  Request time: {request_time:.2f}s")
    
    if response.status_code != 200:
        # Provide more specific error messages
        if response.status_code == 401:
            error_msg = "API Error 401: Unauthorized - Invalid or missing API key. Please check your A79_API_KEY environment variable."
        elif response.status_code == 500:
            error_msg = f"API Error 500: Internal server error from A79 API. Response: {response.text[:200]}"
        else:
            error_msg = f"API Error {response.status_code}: {response.text[:200]}"
        logger.error(f"API request failed: {error_msg}")
        print(f"      ❌ {error_msg}")
        raise Exception(error_msg)
    
    try:
        data = response.json()
        logger.info(f"Successfully parsed JSON response")
        logger.debug(f"Response data keys: {list(data.keys())}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response: {e}")
        logger.debug(f"Raw response: {response.text[:500]}")
        raise Exception(f"Invalid JSON response: {e}")
    
    print(f"      📦 Response status: {data.get('status')}")
    print(f"      📋 All response keys: {list(data.keys())}")
    
    # Check if we got immediate results (no polling needed)
    if 'output' in data and data.get('status') == 'completed':
        print(f"      ✅ Immediate result available (no polling needed)")
        print(f"      📦 Output keys: {list(data['output'].keys()) if isinstance(data['output'], dict) else 'string'}")
        return data['output']
    
    # Check if there's output data even without completed status
    if 'output' in data and data['output']:
        print(f"      ℹ️  Output present in response (status: {data.get('status')})")
        print(f"      📦 Output keys: {list(data['output'].keys()) if isinstance(data['output'], dict) else type(data['output'])}")
        # Return it if it looks like valid data
        if isinstance(data['output'], dict) or isinstance(data['output'], list):
            return data['output']
    
    # Check if this is a workflow run that needs polling (any status with run_id)
    if 'run_id' in data:
        run_id = data['run_id']
        response_workflow_id = data.get('workflow_id', workflow_id)  # Use from response or parameter
        current_status = data.get('status', 'unknown')
        
        logger.info(f"Workflow started - Run ID: {run_id}, Workflow ID: {response_workflow_id}, Status: {current_status}")
        print(f"      🔄 Workflow started (run_id: {run_id}, workflow_id: {response_workflow_id or 'N/A'}, status: {current_status})")
        
        # A79 API Architecture: No polling endpoints available
        # This is different from typical certificate extraction APIs
        print(f"      ⚠️  A79 API uses dashboard-based retrieval (no polling endpoints)")
        print(f"      📋 Run ID for manual retrieval: {run_id}")
        print(f"      🌐 Check A79 dashboard: https://klearnow.prod.a79.ai")
        print(f"      🔄 Or use 'Fetch by Run ID' feature in this app")
        
        # Check if there's a polling URL in the response
        if 'polling_url' in data or 'status_url' in data or 'callback_url' in data:
            poll_url = data.get('polling_url') or data.get('status_url') or data.get('callback_url')
            logger.info(f"Found polling URL in response: {poll_url}")
            print(f"      🔗 Found polling URL in response: {poll_url}")
        else:
            print(f"      ℹ️  No polling URL provided - A79 uses dashboard retrieval")
        
        logger.info("Starting polling for results...")
        print(f"      ⏳ Polling for results...")
        
        # Poll for results using the public workflow API
        # AI79 workflows can take several minutes (processes in 6-page chunks)
        max_attempts = 120  # 120 attempts × 5 seconds = 10 minutes max
        poll_interval = 5
        
        print(f"      ⏰ Max wait time: {max_attempts * poll_interval} seconds ({max_attempts * poll_interval / 60:.1f} minutes)")
        
        # Build polling URL using the WORKING pattern from certificate app
        base_url = "https://klearnow.prod.a79.ai/api/v1/public/workflow"
        
        # Use the proven working pattern from certificate extraction app
        poll_url = f"{base_url}/{run_id}/status?output_var=final_display_output"
        
        print(f"      🔗 Polling URL: {poll_url}")
        
        for attempt in range(max_attempts):
            time.sleep(poll_interval)
            elapsed_time = (attempt + 1) * poll_interval
            elapsed_mins = elapsed_time / 60
            
            logger.debug(f"Polling attempt {attempt + 1}/{max_attempts} - URL: {poll_url}")
            
            try:
                poll_response = requests.get(
                    poll_url,
                    headers={
                        'Authorization': f'Bearer {api_key}',
                        'Content-Type': 'application/json'
                    },
                    timeout=30
                )
                logger.debug(f"Poll response status: {poll_response.status_code}")
                
                # If 404 on first attempt, try alternate endpoints
                if poll_response.status_code == 404 and attempt == 0:
                    print(f"      ⚠️  Initial polling URL failed, trying alternates...")
                    
                    # Try different endpoint patterns (including the working certificate app pattern)
                    alternate_urls = [
                        f"{base_url}/{run_id}/status?output_var=final_display_output",  # Working pattern from certificate app
                        f"{base_url}/run/{run_id}",
                        f"{base_url}/{run_id}",
                        f"{base_url}/run/{run_id}/status",
                        f"https://klearnow.prod.a79.ai/api/v1/workflow/cards/{run_id}",
                    ]
                    
                    # Add workflow_id based patterns if available
                    if response_workflow_id and response_workflow_id != 'N/A':
                        alternate_urls.extend([
                            f"{base_url}/{response_workflow_id}/run/{run_id}",
                            f"{base_url}/{response_workflow_id}/runs/{run_id}/status",
                        ])
                    
                    for alt_url in alternate_urls:
                        print(f"      🔄 Trying: {alt_url}")
                        poll_response = requests.get(
                            alt_url,
                            headers={
                                'Authorization': f'Bearer {api_key}',
                                'Content-Type': 'application/json'
                            },
                            timeout=30
                        )
                        if poll_response.status_code == 200:
                            poll_url = alt_url  # Update to working URL
                            print(f"      ✅ Found working endpoint: {alt_url}")
                            break
                        elif poll_response.status_code != 404:
                            print(f"         → HTTP {poll_response.status_code}")
                
                if poll_response.status_code == 200:
                    poll_data = poll_response.json()
                    status = poll_data.get('status', 'unknown')
                    
                    # Debug: Show what we're getting in the response
                    if attempt < 3:  # Only show first few attempts to avoid spam
                        print(f"      🔍 DEBUG - Response keys: {list(poll_data.keys())}")
                        print(f"      🔍 DEBUG - Status: '{status}'")
                        if 'output' in poll_data:
                            print(f"      🔍 DEBUG - Output type: {type(poll_data['output'])}")
                            if isinstance(poll_data['output'], str):
                                print(f"      🔍 DEBUG - Output preview: {poll_data['output'][:200]}...")
                            elif isinstance(poll_data['output'], dict):
                                print(f"      🔍 DEBUG - Output keys: {list(poll_data['output'].keys())[:10]}")
                            elif isinstance(poll_data['output'], list):
                                print(f"      🔍 DEBUG - Output is list with {len(poll_data['output'])} items")
                        # Check if poll_data itself might be the output
                        if isinstance(poll_data, dict) and ('line_items' in poll_data or 'items' in poll_data):
                            print(f"      🔍 DEBUG - Poll data contains line_items/items directly")
                        # Check if poll_data is a list (might be direct line items)
                        if isinstance(poll_data, list):
                            print(f"      🔍 DEBUG - Poll data is a list with {len(poll_data)} items")
                            if len(poll_data) > 0:
                                print(f"      🔍 DEBUG - First item keys: {list(poll_data[0].keys())[:10] if isinstance(poll_data[0], dict) else 'not dict'}")
                    
                    # Show progress with elapsed time
                    if elapsed_mins < 1:
                        time_str = f"{elapsed_time}s"
                    else:
                        time_str = f"{elapsed_mins:.1f}m"
                    
                    print(f"      📊 [{time_str}] Attempt {attempt + 1}/{max_attempts}: {status}", end="")
                    
                    # Check for completion status (including certificate app patterns)
                    if status.upper() in ['COMPLETED', 'SUCCEEDED', 'FINISHED', 'completed', 'succeeded', 'finished']:
                        print(" ✅")
                        # Check for output in the response
                        if 'output' in poll_data and poll_data['output']:
                            output_data = poll_data['output']
                            # If output is a string, try to parse it as JSON
                            if isinstance(output_data, str):
                                try:
                                    output_data = json.loads(output_data)
                                    print(f"      🔄 Parsed output string to {type(output_data).__name__}")
                                except json.JSONDecodeError:
                                    print(f"      ⚠️  Output is string but not valid JSON")
                            print(f"      📦 Output keys: {list(output_data.keys()) if isinstance(output_data, dict) else 'string'}")
                            return output_data
                        else:
                            print(f"      ⚠️  Completed but no output found. Response keys: {list(poll_data.keys())}")
                            # Check if the entire poll_data might be the output
                            if isinstance(poll_data, dict) and ('line_items' in poll_data or 'items' in poll_data or any(k in poll_data for k in ['line_number', 'primary_hts', 'entry_summary'])):
                                print(f"      ℹ️  Poll data appears to contain line items, returning it directly")
                                return poll_data
                            return poll_data
                    
                    # Also check if we have output data even if status isn't completed yet
                    # Sometimes a79 returns data before status is "completed"
                    if 'output' in poll_data and poll_data['output']:
                        output_data = poll_data['output']
                        # If output is a string, try to parse it as JSON
                        if isinstance(output_data, str):
                            try:
                                output_data = json.loads(output_data)
                                print(f"      🔄 Parsed output string to {type(output_data).__name__}")
                            except json.JSONDecodeError:
                                pass  # Keep as string if not valid JSON
                        # Check if output looks like valid extraction data
                        if isinstance(output_data, (dict, list)):
                            # If it's a dict, check for line items or entry_summary
                            if isinstance(output_data, dict) and ('line_items' in output_data or 'entry_summary' in output_data or 'items' in output_data):
                                print(f"      ✅ Found output data in response (status: {status}), returning it")
                                return output_data
                            # If it's a list, check if items look like line items
                            elif isinstance(output_data, list) and len(output_data) > 0:
                                first_item = output_data[0] if output_data else {}
                                if isinstance(first_item, dict) and ('line_number' in first_item or 'primary_hts' in first_item or 'line_no' in first_item):
                                    print(f"      ✅ Found line items list in output (status: {status}), returning it")
                                    return output_data
                    
                    # Check if poll_data itself is the output (might be a list or dict with line items)
                    # Do this before checking status, as data might be available even if status isn't "completed"
                    if isinstance(poll_data, list) and len(poll_data) > 0:
                        first_item = poll_data[0] if poll_data else {}
                        if isinstance(first_item, dict) and ('line_number' in first_item or 'primary_hts' in first_item or 'line_no' in first_item):
                            print(f"      ✅ Poll data is a list of line items (status: {status}), returning it")
                            return poll_data
                    elif isinstance(poll_data, dict) and ('line_items' in poll_data or 'items' in poll_data):
                        print(f"      ✅ Poll data contains line_items/items (status: {status}), returning it")
                        return poll_data
                    
                    # Now check status for completion/failure
                    if status.upper() in ['FAILED', 'ERROR', 'CANCELLED', 'failed', 'error', 'cancelled']:
                        print(" ❌")
                        raise Exception(f"Workflow failed: {poll_data.get('error_msg', 'Unknown error')}")
                    elif status.upper() in ['NOT_STARTED', 'RUNNING', 'IN_PROGRESS', 'PENDING', 'not_started', 'running', 'in_progress', 'pending']:
                        # Show progress indicator for long waits
                        if attempt % 10 == 0 and attempt > 0:
                            print(f" (still processing...)")
                        else:
                            print()
                    else:
                        # Unknown status - show it and continue
                        print(f" (status: {status})")
                else:
                    error_msg = ""
                    try:
                        error_data = poll_response.json()
                        error_msg = f" - {error_data.get('detail', error_data)}"
                    except:
                        error_msg = f" - {poll_response.text[:100]}"
                    
                    print(f"      📊 Attempt {attempt + 1}: HTTP {poll_response.status_code}{error_msg if attempt == 0 else ''}")
                    
                    # On first attempt, show what endpoints we're trying
                    if attempt == 0 and poll_response.status_code == 404:
                        print(f"      ℹ️  Note: The workflow may use webhooks or a different polling pattern")
            except Exception as e:
                print(f"      📊 Attempt {attempt + 1}: Error - {str(e)[:50]}")
        
        # Before giving up, check if user manually saved the JSON
        manual_json_path = os.path.join(OUTPUT_FOLDER, f"{run_id}.json")
        if os.path.exists(manual_json_path):
            print(f"\n      ✅ Found manually saved JSON: {manual_json_path}")
            with open(manual_json_path, 'r') as f:
                return json.load(f)
        
        raise Exception(
            f"Workflow polling timed out after {max_attempts * poll_interval} seconds.\n"
            f"\n"
            f"🎯 GOOD NEWS: Your workflow completed! (run_id: {run_id})\n"
            f"\n"
            f"📥 MANUAL MODE - Get your results:\n"
            f"1. Go to AI79 dashboard: https://klearnow.prod.a79.ai\n"
            f"2. Find run_id: {run_id}\n"
            f"3. Download the JSON output\n"
            f"4. Save it as: /tmp/cbp_outputs/{run_id}.json\n"
            f"5. The app will detect and process it automatically\n"
            f"\n"
            f"Or use the manual upload endpoint at /process-json\n"
            f"\n"
            f"💡 TO FIX POLLING PERMANENTLY:\n"
            f"1. Get workflow_id from AI79 dashboard for 'Process Document Compressed'\n"
            f"2. Update app.py: API2_WORKFLOW_ID = 'wf_your_id_here'\n"
            f"3. Restart - polling will work automatically\n"
        )
    
    # If we get here without a run_id, something is wrong
    print(f"      ⚠️  WARNING: No run_id in response, returning data as-is")
    print(f"      ✅ Response keys: {list(data.keys())}")
    if 'output' in data:
        print(f"      📦 Output keys: {list(data['output'].keys()) if isinstance(data['output'], dict) else type(data['output'])}")
    return data


def process_document_with_api(filepath, filename):
    """
    Process CBP 7501 document using API 1 (Unified PDF Parser)
    - Processes entire PDF with one API call
    - Polls for results automatically
    """
    import requests
    
    logger.info(f"Starting document processing: {filename}")
    print(f"📤 Processing CBP 7501: {filename}")
    print(f"   Using API 1 (Unified PDF Parser) for entire document")
    
    try:
        # Read entire PDF and convert to base64
        logger.debug(f"Reading PDF file: {filepath}")
        with open(filepath, 'rb') as f:
            pdf_bytes = f.read()
        
        pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
        logger.info(f"PDF loaded - Size: {len(pdf_bytes)} bytes, Base64: {len(pdf_base64)} chars")
        print(f"   📄 PDF size: {len(pdf_bytes)} bytes")
        
        # Process entire PDF with API 1
        print(f"\n   📋 Processing entire document...")
        raw_a79_response = call_api(
            API_KEY,
            API_BASE_URL,
            pdf_base64,
            API1_CUSTOM_INSTRUCTIONS,
            API1_AGENT_NAME,
            API1_WORKFLOW_ID,
            "entire document"
        )
        
        # Save raw response
        debug_file = filepath.replace('.pdf', '_api1_response.json')
        with open(debug_file, 'w') as f:
            json.dump(raw_a79_response, f, indent=2)
        print(f"      ✅ Raw response saved: {debug_file}")
        
        # Parse the AI79 page-based response format
        print(f"\n   🔄 Parsing AI79 response format...")
        parsed_data = parse_ai79_response(raw_a79_response)
        
        # Save parsed response
        parsed_file = filepath.replace('.pdf', '_parsed_response.json')
        with open(parsed_file, 'w') as f:
            json.dump(parsed_data, f, indent=2)
        print(f"      ✅ Parsed response saved: {parsed_file}")
        
        # Return both raw and parsed - store raw in a way that can be accessed
        # Attach raw response to parsed data for later retrieval
        parsed_data['_raw_a79_response'] = raw_a79_response
        
        return parsed_data
            
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
        raise


def parse_ai79_response(api_response):
    """
    Parse AI79 API response and normalize to standard structure
    
    Handles multiple AI79 response formats:
    1. List of pages (workflow response)
    2. Direct dictionary with entry_summary
    3. Wrapped in 'output' or 'result' keys
    4. String that needs JSON parsing
    
    Args:
        api_response: AI79 response in various formats
    
    Returns:
        dict: Normalized data structure for CBP7501Normalizer
    """
    print(f"\n{'='*80}")
    print(f"🔄 AI79 JSON NORMALIZATION")
    print(f"{'='*80}")
    
    # Step 1: Detect and log input format
    original_type = type(api_response).__name__
    print(f"\n📥 Input Format: {original_type}")
    
    # Step 2: Handle string JSON (including escaped JSON strings)
    if isinstance(api_response, str):
        print(f"   🔄 Parsing JSON string...")
        try:
            # Try parsing directly
            api_response = json.loads(api_response)
            print(f"   ✅ Parsed to {type(api_response).__name__}")
        except json.JSONDecodeError:
            # If that fails, it might be an escaped JSON string (double-encoded)
            try:
                # Remove outer quotes and unescape
                unescaped = api_response.strip()
                if unescaped.startswith('"') and unescaped.endswith('"'):
                    unescaped = unescaped[1:-1]
                    # Replace escaped newlines and quotes
                    unescaped = unescaped.replace('\\n', '\n').replace('\\"', '"').replace('\\/', '/')
                    api_response = json.loads(unescaped)
                    print(f"   ✅ Parsed escaped JSON string to {type(api_response).__name__}")
                else:
                    raise ValueError("Could not parse JSON string")
            except (json.JSONDecodeError, ValueError) as e:
                print(f"   ❌ JSON parse error: {e}")
                print(f"   📋 First 200 chars: {api_response[:200]}")
                raise ValueError(f"Invalid JSON string: {e}")
    
    # Step 3: Handle wrapped responses
    if isinstance(api_response, dict):
        print(f"   📦 Dictionary detected - checking for wrapped data...")
        
        # Try common wrapper keys
        if 'pages' in api_response:
            print(f"   ✅ Found 'pages' wrapper")
            api_response = api_response['pages']
        elif 'output' in api_response:
            print(f"   ✅ Found 'output' wrapper")
            output = api_response['output']
            if isinstance(output, str):
                api_response = json.loads(output)
            else:
                api_response = output
        elif 'result' in api_response:
            print(f"   ✅ Found 'result' wrapper")
            api_response = api_response['result']
        elif 'data' in api_response:
            print(f"   ✅ Found 'data' wrapper")
            api_response = api_response['data']
        
        # Check if already normalized (has entry_summary)
        if 'entry_summary' in api_response:
            print(f"   ✅ Already normalized - has entry_summary structure")
            print(f"{'='*80}\n")
            return api_response
    
    # Step 4: Handle list of pages (standard AI79 workflow format)
    if isinstance(api_response, list):
        print(f"\n📄 List Format: Processing {len(api_response)} pages...")
        return _parse_ai79_pages_format(api_response)
    
    # Step 5: Handle direct dictionary format
    if isinstance(api_response, dict):
        print(f"\n📋 Dictionary Format: Normalizing structure...")
        # Check if this is a flat list of line items wrapped in a dict (new a79 format)
        # Look for keys that might contain line items array - check 'items' first (common in a79 responses)
        if 'items' in api_response and isinstance(api_response['items'], list):
            print(f"   ✅ Found 'items' array with {len(api_response['items'])} items")
            result = {
                'entry_summary': {
                    'line_items': api_response['items']
                }
            }
            # Copy other top-level fields as header info
            for key, value in api_response.items():
                if key != 'items' and not isinstance(value, list):
                    result['entry_summary'][key] = value
            print(f"   📊 Total line items: {len(result['entry_summary']['line_items'])}")
            print(f"{'='*80}\n")
            return result
        elif 'line_items' in api_response and isinstance(api_response['line_items'], list):
            print(f"   ✅ Found 'line_items' array with {len(api_response['line_items'])} items")
            result = {
                'entry_summary': {
                    'line_items': api_response['line_items']
                }
            }
            # Copy other top-level fields as header info
            for key, value in api_response.items():
                if key != 'line_items' and not isinstance(value, list):
                    result['entry_summary'][key] = value
            print(f"   📊 Total line items: {len(result['entry_summary']['line_items'])}")
            print(f"{'='*80}\n")
            return result
        return _parse_ai79_dict_format(api_response)
    
    # Unknown format
    print(f"\n❌ Unknown format: {type(api_response)}")
    raise ValueError(f"Unsupported AI79 response format: {type(api_response)}")


def _parse_ai79_pages_format(pages: list) -> dict:
    """Parse AI79 response in pages format (list of page objects)"""
    result = {
        'entry_summary': {
            'line_items': []
        }
    }
    
    # Process each page
    for page in pages:
        page_num = page.get('page_number', page.get('page', '?'))
        content = page.get('content', page)
        
        # Page 1 typically has header and initial merchandise
        if page_num == 1 or page_num == '1':
            print(f"   📄 Page {page_num}: Processing header...")
            
            # Extract header information
            header_info = content.get('header_information', content.get('header', {}))
            if header_info:
                result['entry_summary'].update(header_info)
                print(f"      ✅ Extracted {len(header_info)} header fields")
            
            # Extract summary totals
            summary = content.get('summary', {})
            if summary:
                result['entry_summary'].update(summary.get('totals', {}))
                if 'total_entered_value' in summary:
                    result['entry_summary']['total_entered_value'] = summary['total_entered_value']
                if 'other_fee_summary' in summary:
                    for fee in summary['other_fee_summary']:
                        if 'Merchandise Process' in fee.get('description', ''):
                            result['entry_summary']['mpf_amount'] = fee.get('amount')
                print(f"      ✅ Extracted summary totals")
            
            # Extract broker info
            broker_info = content.get('broker_filer_information', content.get('broker', {}))
            if broker_info:
                result['entry_summary']['broker_name'] = broker_info.get('name')
                result['entry_summary']['broker_code'] = broker_info.get('broker_importer_file_no')
                print(f"      ✅ Extracted broker info")
            
            # Extract declarant info
            decl_info = content.get('declaration_information', content.get('declarant', {}))
            if decl_info:
                result['entry_summary']['declarant_name'] = decl_info.get('declarant_name')
                print(f"      ✅ Extracted declarant info")
            
            # Extract initial merchandise
            merchandise = content.get('merchandise_details', content.get('line_items', []))
            for item in merchandise:
                result['entry_summary']['line_items'].append(item)
            
            print(f"      ✅ Page {page_num}: Header + {len(merchandise)} items")
        
        # Pages 2+ typically have more line items
        else:
            items = content.get('items', content.get('line_items', content.get('merchandise_details', [])))
            if items:
                result['entry_summary']['line_items'].extend(items)
                print(f"      ✅ Page {page_num}: {len(items)} items")
    
    total_items = len(result['entry_summary']['line_items'])
    print(f"\n   📊 Total line items extracted: {total_items}")
    print(f"{'='*80}\n")
    
    return result


def _parse_ai79_dict_format(data: dict) -> dict:
    """Parse AI79 response in dictionary format (direct structure)"""
    result = {
        'entry_summary': {
            'line_items': []
        }
    }
    
    # Check for entry_summary key
    if 'entry_summary' in data:
        print(f"   ✅ Found entry_summary key")
        return data
    
    # Try to extract header information from various possible keys
    print(f"   🔍 Searching for header information...")
    header_keys = ['header_information', 'header', 'entry_header', 'summary_info']
    for key in header_keys:
        if key in data:
            result['entry_summary'].update(data[key])
            print(f"      ✅ Found header in '{key}'")
            break
    
    # Try to extract line items from various possible keys
    print(f"   🔍 Searching for line items...")
    item_keys = ['line_items', 'items', 'merchandise_details', 'merchandise', 'entries']
    found_items = False
    for key in item_keys:
        if key in data and isinstance(data[key], list):
            result['entry_summary']['line_items'] = data[key]
            print(f"      ✅ Found {len(data[key])} items in '{key}'")
            found_items = True
            break
    
    # If no line items found, check if the entire dict structure might be different
    # Some a79 responses have line items directly as array values
    if not found_items:
        print(f"   ⚠️  No line_items found in standard keys, checking alternative structures...")
        # Check if any top-level list might be line items
        for key, value in data.items():
            if isinstance(value, list) and len(value) > 0:
                # Check if first item looks like a line item (has line_number or primary_hts)
                first_item = value[0] if value else {}
                if isinstance(first_item, dict) and ('line_number' in first_item or 'primary_hts' in first_item or 'line_no' in first_item):
                    result['entry_summary']['line_items'] = value
                    print(f"      ✅ Found {len(value)} items in '{key}' (detected as line items)")
                    found_items = True
                    break
    
    # Extract any remaining top-level fields as header fields
    excluded_keys = {'line_items', 'items', 'merchandise_details', 'merchandise', 'entries', 'pages'}
    for key, value in data.items():
        if key not in excluded_keys and not isinstance(value, list):
            result['entry_summary'][key] = value
    
    total_items = len(result['entry_summary']['line_items'])
    print(f"\n   📊 Total line items extracted: {total_items}")
    if total_items == 0:
        print(f"   ⚠️  WARNING: No line items found! Available keys: {list(data.keys())[:10]}")
    print(f"{'='*80}\n")
    
    return result


def validate_api_response(data: dict) -> bool:
    """Validate API response has expected structure"""
    # Check for entry_summary
    if 'entry_summary' in data:
        entry = data['entry_summary']
    elif 'data' in data and 'entry_summary' in data['data']:
        entry = data['data']['entry_summary']
    else:
        print("   ⚠️  No entry_summary found in response")
        return False
    
    # Check for line items
    has_line_items = False
    if 'line_items' in entry:
        has_line_items = True
        print(f"   ✅ Found {len(entry['line_items'])} line items")
        
        # Check for HTS classifications
        if len(entry['line_items']) > 0:
            first_item = entry['line_items'][0]
            if 'hts_classifications' in first_item:
                hts_count = len(first_item['hts_classifications'])
                print(f"   ✅ Found nested HTS classifications (first item has {hts_count})")
            else:
                print(f"   ℹ️  No nested HTS classifications (flat structure)")
    
    if not has_line_items:
        print("   ⚠️  No line_items found in entry_summary")
        return False
    
    return True


@app.route('/favicon.ico')
def favicon():
    """Return empty favicon to prevent 404 errors"""
    return '', 204

@app.route('/')
def index():
    """Render the web interface"""
    html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KlearAgent v3.5.10 - CBP 7501 with Invoice Header Filter</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }

        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            padding: 40px;
            max-width: 600px;
            width: 100%;
        }

        .header {
            text-align: center;
            margin-bottom: 30px;
        }

        .header h1 {
            color: #333;
            font-size: 32px;
            margin-bottom: 10px;
        }

        .header p {
            color: #666;
            font-size: 16px;
        }

        .version {
            background: #4CAF50;
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            display: inline-block;
            margin-top: 10px;
        }

        .upload-area {
            border: 3px dashed #667eea;
            border-radius: 15px;
            padding: 60px 20px;
            text-align: center;
            background: #f8f9ff;
            cursor: pointer;
            transition: all 0.3s ease;
        }

        .upload-area.dragover {
            background: #e8ebff;
            border-color: #764ba2;
            transform: scale(1.02);
        }

        .upload-icon {
            font-size: 64px;
            margin-bottom: 20px;
        }

        .upload-text {
            color: #333;
            font-size: 18px;
            font-weight: 500;
        }

        .file-info {
            display: none;
            margin-top: 20px;
            padding: 15px;
            background: #e8f5e9;
            border-radius: 10px;
            border-left: 4px solid #4caf50;
        }

        .file-info.show {
            display: block;
        }

        .process-button {
            width: 100%;
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
            color: white;
            border: none;
            padding: 15px;
            border-radius: 10px;
            font-size: 18px;
            cursor: pointer;
            margin-top: 20px;
            display: none;
            font-weight: 600;
        }

        .process-button.show {
            display: block;
        }

        .process-button:disabled {
            background: #ccc;
            cursor: not-allowed;
        }

        .loading {
            display: none;
            text-align: center;
            margin-top: 20px;
        }

        .loading.show {
            display: block;
        }

        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #667eea;
            border-radius: 50%;
            width: 50px;
            height: 50px;
            animation: spin 1s linear infinite;
            margin: 20px auto;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        .success-message {
            display: none;
            margin-top: 20px;
            padding: 15px;
            background: #e8f5e9;
            border-radius: 10px;
            border-left: 4px solid #4caf50;
        }

        .success-message.show {
            display: block;
        }

        .feature-list {
            margin-top: 30px;
            padding: 20px;
            background: #f8f9ff;
            border-radius: 10px;
        }

        .feature-list h3 {
            color: #667eea;
            margin-bottom: 15px;
        }

        .feature-list ul {
            list-style: none;
        }

        .feature-list li {
            padding: 8px 0;
            color: #555;
        }

        .feature-list li:before {
            content: "✓ ";
            color: #4CAF50;
            font-weight: bold;
            margin-right: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 KlearAgent</h1>
            <p>CBP Form 7501 AI Extraction (API 2)</p>
            <span class="version">v3.5.7 - Complete 80 Columns</span>
        </div>

        <div class="upload-area" id="uploadArea">
            <div class="upload-icon">📄</div>
            <div class="upload-text">Drag & Drop CBP 7501 PDF(s) here</div>
            <p style="margin-top: 10px; color: #666;">or click to browse (multiple files supported)</p>
            <input type="file" id="fileInput" style="display: none;" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" multiple>
        </div>

        <div class="file-info" id="fileInfo">
            <div style="font-weight: 600; color: #2e7d32;" id="fileName"></div>
            <div style="color: #666; font-size: 14px; margin-top: 5px;" id="fileSize"></div>
        </div>

        <button class="process-button" id="processButton">
            Extract & Generate JSON (Parallel Processing)
        </button>

        <div class="loading" id="loading">
            <div class="spinner"></div>
            <div style="color: #667eea; font-weight: 500;">Processing document...</div>
        </div>

        <div class="success-message" id="successMessage">
            <div style="font-weight: 600; color: #2e7d32; margin-bottom: 10px;">✅ Processing Complete!</div>
            <div style="color: #555;" id="successDetails">JSON file downloaded successfully</div>
        </div>

        <div class="feature-list">
            <h3>📊 Complete Field Extraction</h3>
            <ul>
                <li>JSON output format with metadata</li>
                <li>All CS (Customs Summary) fields</li>
                <li>All CM (Customs Merchandise) fields</li>
                <li>All CD (Customs Duty) fields</li>
                <li>One row per HTS code/line item</li>
                <li>Complete duty and fee breakdowns</li>
                <li>✨ Parallel processing for multiple PDFs</li>
                <li>✨ Batch processing with ZIP download</li>
            </ul>
        </div>

        <div style="margin-top: 30px; padding: 20px; background: #f5f5f5; border-radius: 10px; border-left: 4px solid #2196F3;">
            <h3 style="margin: 0 0 10px 0; color: #1976D2;">💡 Manual Mode</h3>
            <p style="margin: 0 0 15px 0; color: #666; font-size: 14px;">
                If polling times out, download JSON from AI79 dashboard and upload it here:
            </p>
            <input type="file" id="jsonFileInput" accept=".json" style="display: none;">
            <button onclick="document.getElementById('jsonFileInput').click()" 
                    style="background: #2196F3; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-weight: 600;">
                📥 Upload AI79 JSON
            </button>
            <div id="jsonProcessing" style="display: none; margin-top: 10px; color: #1976D2;">
                ⏳ Processing JSON...
            </div>
        </div>

        <div style="margin-top: 20px; padding: 20px; background: #fff3e0; border-radius: 10px; border-left: 4px solid #FF9800;">
            <h3 style="margin: 0 0 10px 0; color: #F57C00;">🔍 Fetch by Run ID</h3>
            <p style="margin: 0 0 15px 0; color: #666; font-size: 14px;">
                If you have a run_id from the console, try fetching the result directly:
            </p>
            <div style="display: flex; gap: 10px;">
                <input type="text" id="runIdInput" placeholder="Enter run_id (e.g., 69c26c8f-d195-4788-a294-d037107147fb)" 
                       style="flex: 1; padding: 10px; border: 2px solid #FFB74D; border-radius: 5px; font-size: 14px;">
                <button id="fetchRunIdButton" onclick="fetchByRunId()"
                        style="background: #FF9800; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-weight: 600; white-space: nowrap;">
                    🔍 Fetch Result
                </button>
            </div>
            <div id="runIdStatus" style="margin-top: 10px; display: none;"></div>
        </div>
    </div>

    <script>
        let selectedFiles = [];

        async function fetchByRunId() {
            const runIdInput = document.getElementById('runIdInput');
            const runIdStatus = document.getElementById('runIdStatus');
            const fetchButton = document.getElementById('fetchRunIdButton');
            
            const runId = runIdInput.value.trim();
            if (!runId) {
                alert('Please enter a run_id');
                return;
            }
            
            runIdStatus.style.display = 'block';
            runIdStatus.style.color = '#F57C00';
            runIdStatus.innerHTML = '🔄 Fetching results...';
            fetchButton.disabled = true;
            
            try {
                const response = await fetch('/fetch-by-runid', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ run_id: runId })
                });
                
                const result = await response.json();
                
                if (response.ok && result.success) {
                    runIdStatus.style.color = '#2e7d32';
                    runIdStatus.innerHTML = '✅ Results fetched! Processing to JSON...';
                    
                    // Now process the data through the normalization endpoint
                    const processResponse = await fetch('/process-json-data', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(result.data)
                    });
                    
                    if (processResponse.ok) {
                        const blob = await processResponse.blob();
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `cbp7501_runid_${new Date().getTime()}.json`;
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                        
                        runIdStatus.innerHTML = '✅ Success! JSON file downloaded.';
                        runIdInput.value = '';
                    } else {
                        throw new Error('Failed to process data to JSON');
                    }
                } else {
                    runIdStatus.style.color = '#d32f2f';
                    runIdStatus.innerHTML = `❌ ${result.error || result.message || 'Could not fetch results'}`;
                }
            } catch (error) {
                console.error('Error:', error);
                runIdStatus.style.color = '#d32f2f';
                runIdStatus.innerHTML = '❌ Error fetching results. Please try manual JSON upload.';
            } finally {
                fetchButton.disabled = false;
            }
        }

        const uploadArea = document.getElementById('uploadArea');
        const fileInput = document.getElementById('fileInput');
        const fileInfo = document.getElementById('fileInfo');
        const fileName = document.getElementById('fileName');
        const fileSize = document.getElementById('fileSize');
        const processButton = document.getElementById('processButton');
        const loading = document.getElementById('loading');
        const successMessage = document.getElementById('successMessage');
        const successDetails = document.getElementById('successDetails');

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            uploadArea.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
            }, false);
        });

        ['dragenter', 'dragover'].forEach(eventName => {
            uploadArea.addEventListener(eventName, () => {
                uploadArea.classList.add('dragover');
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            uploadArea.addEventListener(eventName, () => {
                uploadArea.classList.remove('dragover');
            }, false);
        });

        uploadArea.addEventListener('drop', (e) => {
            const files = Array.from(e.dataTransfer.files);
            if (files.length > 0) {
                handleFiles(files);
            }
        }, false);

        uploadArea.addEventListener('click', () => {
            fileInput.click();
        });

        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleFiles(Array.from(e.target.files));
            }
        });

        function handleFiles(files) {
            selectedFiles = files;
            const totalSize = files.reduce((sum, file) => sum + file.size, 0);
            
            if (files.length === 1) {
                fileName.textContent = `📎 ${files[0].name}`;
                fileSize.textContent = `Size: ${formatBytes(files[0].size)}`;
            } else {
                fileName.textContent = `📎 ${files.length} files selected`;
                fileSize.textContent = `Total size: ${formatBytes(totalSize)}`;
            }
            
            fileInfo.classList.add('show');
            processButton.classList.add('show');
            
            if (files.length > 1) {
                processButton.textContent = `Process ${files.length} PDFs (Parallel)`;
            } else {
                processButton.textContent = 'Extract & Generate JSON';
            }
        }

        function formatBytes(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
        }

        processButton.addEventListener('click', async () => {
            if (selectedFiles.length === 0) return;

            processButton.disabled = true;
            loading.classList.add('show');
            successMessage.classList.remove('show');
            loading.querySelector('div').textContent = selectedFiles.length > 1 
                ? `Processing ${selectedFiles.length} documents in parallel...` 
                : 'Processing document...';

            const formData = new FormData();
            
            // Append files as 'files[]' for multiple or 'file' for single
            if (selectedFiles.length === 1) {
                formData.append('file', selectedFiles[0]);
            } else {
                selectedFiles.forEach(file => {
                    formData.append('files[]', file);
                });
            }

            try {
                const response = await fetch('/upload', {
                    method: 'POST',
                    body: formData
                });

                if (response.ok) {
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    
                    // Determine file extension based on content type
                    const contentType = response.headers.get('content-type');
                    const isZip = contentType && contentType.includes('zip');
                    const extension = isZip ? 'zip' : 'json';
                    const filename = selectedFiles.length > 1 
                        ? `cbp7501_batch_${new Date().getTime()}.zip`
                        : `cbp7501_${selectedFiles[0].name.replace(/\.[^/.]+$/, '')}_${new Date().getTime()}.json`;
                    
                    a.href = url;
                    a.download = filename;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);

                    loading.classList.remove('show');
                    successMessage.classList.add('show');
                    successDetails.textContent = selectedFiles.length > 1
                        ? `${selectedFiles.length} files processed. ZIP archive downloaded.`
                        : 'JSON file downloaded successfully.';
                    
                    // Reset
                    selectedFiles = [];
                    fileInfo.classList.remove('show');
                    processButton.classList.remove('show');
                    fileInput.value = '';
                } else {
                    // Try to get error message from response
                    let errorMessage = 'Error processing file(s). Please try again.';
                    try {
                        const errorData = await response.json();
                        if (errorData.error) {
                            errorMessage = `Error: ${errorData.error}`;
                        }
                    } catch (e) {
                        errorMessage = `Error: ${response.status} ${response.statusText}`;
                    }
                    throw new Error(errorMessage);
                }
            } catch (error) {
                console.error('Error:', error);
                loading.classList.remove('show');
                alert(error.message || 'Error processing file(s). Please try again.');
                processButton.disabled = false;
            }
        });

        // Handle manual JSON upload
        const jsonFileInput = document.getElementById('jsonFileInput');
        const jsonProcessing = document.getElementById('jsonProcessing');

        jsonFileInput.addEventListener('change', async (e) => {
            if (e.target.files.length === 0) return;
            
            const jsonFile = e.target.files[0];
            jsonProcessing.style.display = 'block';
            
            const formData = new FormData();
            formData.append('file', jsonFile);
            
            try {
                const response = await fetch('/process-json', {
                    method: 'POST',
                    body: formData
                });
                
                if (response.ok) {
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `cbp7501_manual_${new Date().getTime()}.xlsx`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);
                    
                    jsonProcessing.style.display = 'none';
                    alert('✅ JSON processed successfully! Excel file downloaded.');
                } else {
                    throw new Error('Processing failed');
                }
            } catch (error) {
                console.error('Error:', error);
                jsonProcessing.style.display = 'none';
                alert('❌ Error processing JSON. Please check the file format.');
            }
            
            // Reset input
            jsonFileInput.value = '';
        });
    </script>
</body>
</html>
    """
    return render_template_string(html_template)


def validate_and_compare_with_reference(normalized_data: List[Dict]) -> Dict:
    """
    Validate normalized data against reference Excel structure
    Returns comparison report with warnings/errors
    """
    report = {
        'status': 'success',
        'warnings': [],
        'errors': [],
        'stats': {}
    }
    
    try:
        # Create DataFrame from normalized data
        df = pd.DataFrame(normalized_data)
        
        # Expected columns from reference
        expected_columns = [
            'CS Shipment ID', '1. CS Entry Number', '2. CS Entry Type', '3. CS Summary Date',
            '4. CS Surety Number', '5. CS Bond Type', '6. CS Port Of Entry', '7. CS Entry Date',
            '8. CS Transport Name', '8. CS Carrier Name', '8. CS SCAC Code', '8. CS Voyage Number',
            '9. CS Mode Of Transport', '10. CS Country Of Origin', '11. CS Import Date',
            '12. CS Master BOL Number', '13. CS Manufacturer ID', '14. CS Export Country',
            '15. CS Export Date', '16. CS IT Number', '17. CS IT Date', '18. CS Missing Docs',
            '19. CS Port Of Lading', '20. CS Port Of Unlading', '21. CS Location Firms Code',
            '22. CS Consignee ID', '23. CS Importer ID', '24. CS Ref Number',
            '25. CS Consignee Name', '26. CS Importer Name', '27. CM Item Number',
            '27. CM Country Of Origin', '27. CM Export Country Code', '27. CM Free Trade',
            '28. CS BOL Number', '28. CS Items Description', '28. CM Invoice No', '28. CM PO Number',
            '28. CM Manufacturer ID', '28. CM Recon Value', '28. CM Textile Category',
            '28. CM Total Pack Qty', '28. CM Total Pack Type', '28. CM Part Number',
            '28. CM Invoice Amount', '28. CM Value Addition Amount', '28. CM Total Invoice Amount',
            '29. CD HTS US Code', '29. CD HTS Description', '31. CM Item Pack Type 2',
            '31. CM Item Pack Qty 2', '31. CM Item Pack Type 1', '31. CM Item Pack Qty 1',
            '32. CM Relationship', '32. CM Item Charges', '32. CM Item Entered Value',
            '32. CM First Sale', '33. CS HMF Rate', '33. CS HMF Fee', '33. CD HTS US Rate',
            '34. CD Ad Valorem Duty', '33. CD Cotton Fee Rate', '34. CD Cotton Fee Amount',
            '33. CD MPF Rate', '34. CD MPF Fee', '33. CD HMF Rate', '34. CD HMF Fee',
            '33. CD Specific Rate', '34. CD Specific Duty', '34. CD Duty And Taxes',
            '35. CS Total Entered Value', '37. CS Totals Duty', '38. CS Totals Tax',
            '39. CS MPF Amount', '39. CS Cotton Amount', '39. CS Total Other Fees',
            '40. CS Duty Grand Total', '41. CS Declarant Name', '42. CS Broker Name',
            '43. CS Broker Code'
        ]
        
        # Check column count
        report['stats']['total_columns'] = len(df.columns)
        report['stats']['expected_columns'] = 80
        report['stats']['total_rows'] = len(df)
        
        # Check for missing columns
        missing_cols = set(expected_columns) - set(df.columns)
        if missing_cols:
            report['warnings'].append(f"Missing columns: {', '.join(list(missing_cols)[:5])}")
            if len(missing_cols) > 5:
                report['warnings'].append(f"... and {len(missing_cols) - 5} more missing columns")
        
        # Check for extra columns
        extra_cols = set(df.columns) - set(expected_columns)
        if extra_cols:
            report['warnings'].append(f"Extra columns found: {', '.join(list(extra_cols)[:5])}")
        
        # Check critical fields
        critical_fields = ['1. CS Entry Number', '29. CD HTS US Code', '27. CM Item Number']
        for field in critical_fields:
            if field in df.columns:
                empty_count = df[field].isna().sum()
                if empty_count > 0:
                    report['warnings'].append(f"Critical field '{field}' has {empty_count} empty values")
        
        # Check data types for key numeric fields
        numeric_fields = {
            '4. CS Surety Number': 'int',
            '27. CM Item Number': 'int',
            '34. CD Ad Valorem Duty': 'float',
            '70. 34. CD Duty And Taxes': 'float'
        }
        
        for field, expected_type in numeric_fields.items():
            if field in df.columns:
                try:
                    if expected_type == 'int':
                        df[field] = pd.to_numeric(df[field], errors='coerce')
                    elif expected_type == 'float':
                        df[field] = pd.to_numeric(df[field], errors='coerce')
                except Exception as e:
                    report['warnings'].append(f"Could not convert '{field}' to {expected_type}: {str(e)}")
        
        # Summary
        if len(missing_cols) == 0 and len(extra_cols) == 0:
            report['stats']['column_match'] = '✅ Perfect'
        else:
            report['stats']['column_match'] = f'⚠️  {len(missing_cols)} missing, {len(extra_cols)} extra'
        
        # Check if any critical errors
        if len(report['errors']) > 0:
            report['status'] = 'error'
        elif len(report['warnings']) > 0:
            report['status'] = 'warning'
        
    except Exception as e:
        report['status'] = 'error'
        report['errors'].append(f"Validation error: {str(e)}")
    
    return report


@app.route('/fetch-by-runid', methods=['POST'])
def fetch_by_runid():
    """Fetch workflow results by run_id"""
    data = request.json
    if not data or 'run_id' not in data:
        return jsonify({'error': 'run_id is required'}), 400
    
    run_id = data['run_id']
    
    try:
        import requests
        
        print(f"\n{'='*80}")
        print(f"🔍 FETCHING RESULTS BY RUN_ID")
        print(f"{'='*80}")
        print(f"Run ID: {run_id}")
        
        # Try multiple endpoint patterns to find the result
        base_url = "https://klearnow.prod.a79.ai/api/v1"
        endpoints_to_try = [
            f"{base_url}/public/workflow/runs/{run_id}",
            f"{base_url}/public/workflow/run/{run_id}",
            f"{base_url}/workflow/runs/{run_id}",
            f"{base_url}/workflow/cards/{run_id}",
            f"{base_url}/runs/{run_id}",
            f"https://klearnow.prod.a79.ai/api/v1/public/runs/{run_id}",
        ]
        
        headers = {'Authorization': f'Bearer {API_KEY}'}
        
        for idx, endpoint in enumerate(endpoints_to_try, 1):
            print(f"\n🔄 Attempt {idx}/{len(endpoints_to_try)}: {endpoint}")
            try:
                response = requests.get(endpoint, headers=headers, timeout=30)
                print(f"   Status: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    print(f"   ✅ Success! Got response")
                    print(f"   📋 Keys: {list(result.keys())}")
                    
                    # Check if completed and has output
                    if result.get('status') == 'completed' and 'output' in result:
                        print(f"   ✅ Workflow completed with output")
                        return jsonify({
                            'success': True,
                            'data': result['output'],
                            'endpoint_used': endpoint
                        })
                    else:
                        return jsonify({
                            'success': True,
                            'data': result,
                            'status': result.get('status', 'unknown'),
                            'endpoint_used': endpoint,
                            'message': 'Workflow found but may not be completed yet'
                        })
                        
            except Exception as e:
                print(f"   ❌ Error: {str(e)}")
                continue
        
        return jsonify({
            'error': 'Could not retrieve results from any endpoint',
            'run_id': run_id,
            'suggestion': 'Please download JSON from AI79 dashboard and use /process-json endpoint'
        }), 404
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/debug/logs')
def debug_logs():
    """View debug logs in real-time"""
    try:
        with open('/tmp/cbp_debug.log', 'r') as f:
            logs = f.read()
        return f"<pre>{logs}</pre>"
    except FileNotFoundError:
        return "No debug log file found yet."
    except Exception as e:
        return f"Error reading logs: {str(e)}"

@app.route('/debug/status')
def debug_status():
    """Debug status endpoint"""
    import psutil
    import os
    
    status = {
        'app_running': True,
        'pid': os.getpid(),
        'memory_usage': psutil.Process().memory_info().rss / 1024 / 1024,  # MB
        'upload_folder_exists': os.path.exists(UPLOAD_FOLDER),
        'output_folder_exists': os.path.exists(OUTPUT_FOLDER),
        'upload_files': len(os.listdir(UPLOAD_FOLDER)) if os.path.exists(UPLOAD_FOLDER) else 0,
        'output_files': len(os.listdir(OUTPUT_FOLDER)) if os.path.exists(OUTPUT_FOLDER) else 0,
        'api_key_configured': bool(API_KEY),
        'workflow_id_configured': bool(API1_WORKFLOW_ID),
    }
    
    return jsonify(status)

@app.route('/debug/dashboard')
def debug_dashboard():
    """Debug dashboard HTML page"""
    with open('debug_dashboard.html', 'r') as f:
        return f.read()

@app.route('/debug/clear', methods=['POST'])
def debug_clear():
    """Clear debug logs"""
    try:
        with open('/tmp/cbp_debug.log', 'w') as f:
            f.write('')
        return jsonify({'success': True, 'message': 'Logs cleared'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/debug/restart', methods=['POST'])
def debug_restart():
    """Restart application (placeholder)"""
    return jsonify({'success': True, 'message': 'Restart initiated'})

@app.route('/process-json-data', methods=['POST'])
def process_json_data():
    """Process JSON data sent directly in request body"""
    try:
        json_data = request.json
        
        if not json_data:
            return jsonify({'error': 'No JSON data provided'}), 400
        
        print(f"\n{'='*80}")
        print(f"📥 JSON DATA PROCESSING")
        print(f"{'='*80}")
        
        # Normalize data
        print(f"\n🔄 Step 1: Normalizing data to CBP 7501 format...")
        normalizer = CBP7501Normalizer()
        normalized_data = normalizer.normalize(json_data)
        print(f"   ✅ Generated {len(normalized_data)} rows")
        
        # Validate
        print(f"\n🔄 Step 2: Validating against reference Excel structure...")
        validation_report = validate_and_compare_with_reference(normalized_data)
        
        # Print validation report
        print(f"\n{'='*80}")
        print(f"📋 VALIDATION REPORT")
        print(f"{'='*80}")
        print(f"Status: {validation_report['status'].upper()}")
        print(f"Total Rows: {validation_report['stats']['total_rows']}")
        print(f"Total Columns: {validation_report['stats']['total_columns']}/80")
        
        if validation_report['status'] == 'success':
            print(f"\n✅ All validation checks passed!")
        
        # Generate JSON file
        print(f"\n🔄 Step 3: Generating JSON file...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f'cbp7501_data_{timestamp}.json'
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        normalizer.to_json(normalized_data, output_path)
        
        print(f"   ✅ JSON generated")
        print(f"\n{'='*80}")
        print(f"✅ PROCESSING COMPLETE")
        print(f"{'='*80}")
        print(f"📊 Output: {len(normalized_data)} rows")
        print(f"💾 Saved to: {output_path}")
        print(f"{'='*80}\n")
        
        # Send JSON file
        return send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"\n{'='*80}")
        print(f"❌ ERROR PROCESSING JSON DATA")
        print(f"{'='*80}")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'='*80}\n")
        return jsonify({'error': str(e)}), 500


@app.route('/process-json', methods=['POST'])
def process_json():
    """Process manually uploaded JSON from AI79 dashboard with validation"""
    if 'file' not in request.files:
        return jsonify({'error': 'No JSON file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '' or not file.filename.endswith('.json'):
        return jsonify({'error': 'Please upload a JSON file'}), 400
    
    try:
        # Load JSON directly
        json_data = json.load(file)
        
        print(f"\n{'='*80}")
        print(f"📥 MANUAL JSON PROCESSING: {file.filename}")
        print(f"{'='*80}")
        
        # Debug: Show JSON structure
        print(f"\n🔍 RAW JSON STRUCTURE:")
        print(f"   Type: {type(json_data).__name__}")
        if isinstance(json_data, dict):
            print(f"   Top-level keys: {list(json_data.keys())[:10]}")
            if len(json_data.keys()) > 10:
                print(f"   ... and {len(json_data.keys()) - 10} more keys")
        elif isinstance(json_data, list):
            print(f"   Array length: {len(json_data)}")
            if len(json_data) > 0:
                print(f"   First item type: {type(json_data[0]).__name__}")
                if isinstance(json_data[0], dict):
                    print(f"   First item keys: {list(json_data[0].keys())}")
        
        # Parse and normalize the AI79 response
        print(f"\n🔄 Step 1: Parsing & Normalizing AI79 response format...")
        parsed_data = parse_ai79_response(json_data)
        print(f"   ✅ Normalization complete")
        
        # Show normalized structure
        print(f"\n📋 NORMALIZED STRUCTURE:")
        if 'entry_summary' in parsed_data:
            entry = parsed_data['entry_summary']
            print(f"   ✅ entry_summary present")
            print(f"   Header fields: {len([k for k, v in entry.items() if k != 'line_items'])}")
            print(f"   Line items: {len(entry.get('line_items', []))}")
            if entry.get('line_items'):
                first_item = entry['line_items'][0]
                print(f"   First item keys: {list(first_item.keys())[:10]}")
        
        # Normalize data to CBP 7501 format
        print(f"\n🔄 Step 2: Transforming to CBP 7501 format...")
        normalizer = CBP7501Normalizer()
        normalized_data = normalizer.normalize(parsed_data)
        print(f"   ✅ Generated {len(normalized_data)} rows")
        
        # Validate and compare with reference
        print(f"\n🔄 Step 3: Validating against reference Excel structure...")
        validation_report = validate_and_compare_with_reference(normalized_data)
        
        # Print validation report
        print(f"\n{'='*80}")
        print(f"📋 VALIDATION REPORT")
        print(f"{'='*80}")
        print(f"Status: {validation_report['status'].upper()}")
        print(f"Total Rows: {validation_report['stats']['total_rows']}")
        print(f"Total Columns: {validation_report['stats']['total_columns']}/80")
        print(f"Column Match: {validation_report['stats'].get('column_match', 'N/A')}")
        
        if validation_report['warnings']:
            print(f"\n⚠️  WARNINGS ({len(validation_report['warnings'])}):")
            for warning in validation_report['warnings'][:10]:  # Show first 10
                print(f"   • {warning}")
            if len(validation_report['warnings']) > 10:
                print(f"   ... and {len(validation_report['warnings']) - 10} more warnings")
        
        if validation_report['errors']:
            print(f"\n❌ ERRORS ({len(validation_report['errors'])}):")
            for error in validation_report['errors']:
                print(f"   • {error}")
        
        if validation_report['status'] == 'success':
            print(f"\n✅ All validation checks passed!")
        elif validation_report['status'] == 'warning':
            print(f"\n⚠️  Validation passed with warnings")
        
        # Generate JSON file
        print(f"\n🔄 Step 4: Generating JSON file...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f'cbp7501_manual_{timestamp}.json'
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        normalizer.to_json(normalized_data, output_path)
        
        print(f"   ✅ JSON generated")
        print(f"\n{'='*80}")
        print(f"✅ PROCESSING COMPLETE")
        print(f"{'='*80}")
        print(f"📊 Output: {len(normalized_data)} rows")
        print(f"💾 Saved to: {output_path}")
        print(f"{'='*80}\n")
        
        # Send JSON file
        return send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"\n{'='*80}")
        print(f"❌ ERROR PROCESSING JSON")
        print(f"{'='*80}")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'='*80}\n")
        return jsonify({'error': str(e)}), 500


def process_single_pdf(filepath: str, filename: str) -> Dict[str, Any]:
    """
    Process a single PDF file and return both original and normalized data
    Returns dict with 'success', 'filename', 'raw_a79_data', 'extracted_data', 'normalized_data', 'error' keys
    """
    try:
        logger.info(f"Processing PDF: {filename}")
        
        # Process document with API - returns parsed data with _raw_a79_response attached
        extracted_data = process_document_with_api(filepath, filename)
        
        # Extract raw A79 response if attached
        raw_a79_response = extracted_data.pop('_raw_a79_response', None)
        
        # If not attached, try to load from debug file
        if raw_a79_response is None:
            debug_file = filepath.replace('.pdf', '_api1_response.json')
            if os.path.exists(debug_file):
                try:
                    with open(debug_file, 'r') as f:
                        raw_a79_response = json.load(f)
                    logger.info(f"Loaded raw A79 response from {debug_file}")
                except Exception as e:
                    logger.warning(f"Could not load raw response: {e}")
        
        # If still no raw response, use extracted_data (which is parsed)
        if raw_a79_response is None:
            raw_a79_response = extracted_data
        
        # Normalize data
        normalizer = CBP7501Normalizer()
        normalized_data = normalizer.normalize(extracted_data)
        
        # Validate
        validation_report = validate_and_compare_with_reference(normalized_data)
        
        return {
            'success': True,
            'filename': filename,
            'raw_a79_data': raw_a79_response,  # Raw A79 response with all header data
            'extracted_data': extracted_data,  # Parsed structure
            'normalized_data': normalized_data,  # Flattened structure for Excel-like export
            'validation': validation_report,
            'row_count': len(normalized_data)
        }
    except Exception as e:
        logger.error(f"Error processing {filename}: {str(e)}")
        return {
            'success': False,
            'filename': filename,
            'error': str(e),
            'data': None
        }


@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle single or multiple file uploads with parallel processing"""
    logger.info("File upload request received")
    
    # Check if API key is configured
    if not API_KEY or API_KEY.strip() == '':
        error_msg = 'A79_API_KEY environment variable is not set. Please set it before uploading files.'
        logger.error(error_msg)
        return jsonify({'error': error_msg}), 500
    
    # Handle multiple files
    if 'files[]' in request.files:
        files = request.files.getlist('files[]')
    elif 'file' in request.files:
        files = [request.files['file']]
    else:
        return jsonify({'error': 'No files provided'}), 400
    
    # Filter out empty files
    files = [f for f in files if f.filename and f.filename.strip() != '']
    
    if not files:
        return jsonify({'error': 'No valid files selected'}), 400
    
    logger.info(f"Received {len(files)} file(s) for processing")
    
    # Save all uploaded files
    file_tasks = []
    for file in files:
        filename = file.filename
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        file_tasks.append((filepath, filename))
        logger.info(f"Saved file: {filename} ({os.path.getsize(filepath)} bytes)")
    
    try:
        # Process files in parallel using ThreadPoolExecutor
        results = []
        max_workers = min(len(file_tasks), 5)  # Limit to 5 concurrent API calls
        
        print(f"\n{'='*80}")
        print(f"📥 PROCESSING {len(file_tasks)} PDF(S) IN PARALLEL")
        print(f"{'='*80}")
        print(f"🔄 Using {max_workers} worker threads")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_file = {
                executor.submit(process_single_pdf, filepath, filename): (filepath, filename)
                for filepath, filename in file_tasks
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_file):
                filepath, filename = future_to_file[future]
                try:
                    result = future.result()
                    results.append(result)
                    if result['success']:
                        print(f"   ✅ {filename}: {result['row_count']} rows extracted")
                    else:
                        print(f"   ❌ {filename}: {result['error']}")
                except Exception as e:
                    logger.error(f"Exception processing {filename}: {str(e)}")
                    results.append({
                        'success': False,
                        'filename': filename,
                        'error': str(e),
                        'data': None
                    })
        
        # Generate JSON output
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if len(results) == 1:
            # Single file - return JSON directly
            result = results[0]
            if not result['success']:
                return jsonify({'error': result['error'], 'filename': result['filename']}), 500
            
            output_filename = f'cbp7501_{os.path.splitext(result["filename"])[0]}_{timestamp}.json'
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            normalizer = CBP7501Normalizer()
            # Pass raw_a79_data (unparsed) to preserve all header fields exactly as A79 returns
            normalizer.to_json(
                result.get('normalized_data', []), 
                output_path, 
                extracted_data=result.get('extracted_data'),
                raw_a79_data=result.get('raw_a79_data')
            )
            
            print(f"\n{'='*80}")
            print(f"✅ PROCESSING COMPLETE")
            print(f"{'='*80}")
            print(f"📊 Output: {result['row_count']} rows")
            print(f"💾 Saved to: {output_path}")
            print(f"{'='*80}\n")
            
            # Clean up uploaded file
            if os.path.exists(file_tasks[0][0]):
                os.remove(file_tasks[0][0])
            
            return send_file(
                output_path,
                as_attachment=True,
                download_name=output_filename,
                mimetype='application/json'
            )
        else:
            # Multiple files - create ZIP archive with JSON files
            zip_filename = f'cbp7501_batch_{timestamp}.zip'
            zip_path = os.path.join(OUTPUT_FOLDER, zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for result in results:
                    if result['success']:
                        json_filename = f'cbp7501_{os.path.splitext(result["filename"])[0]}.json'
                        json_path = os.path.join(OUTPUT_FOLDER, json_filename)
                        
                        normalizer = CBP7501Normalizer()
                        # Pass raw_a79_data (unparsed) to preserve all header fields exactly as A79 returns
                        normalizer.to_json(
                            result.get('normalized_data', []), 
                            json_path,
                            extracted_data=result.get('extracted_data'),
                            raw_a79_data=result.get('raw_a79_data')
                        )
                        
                        zipf.write(json_path, json_filename)
                        os.remove(json_path)  # Clean up temp JSON file
                    else:
                        # Add error file to ZIP
                        error_filename = f'ERROR_{os.path.splitext(result["filename"])[0]}.txt'
                        error_content = f"Error processing {result['filename']}:\n{result['error']}"
                        zipf.writestr(error_filename, error_content)
            
            # Clean up uploaded files
            for filepath, _ in file_tasks:
                if os.path.exists(filepath):
                    os.remove(filepath)
            
            successful = sum(1 for r in results if r['success'])
            total_rows = sum(r['row_count'] for r in results if r['success'])
            
            print(f"\n{'='*80}")
            print(f"✅ BATCH PROCESSING COMPLETE")
            print(f"{'='*80}")
            print(f"📊 Processed: {successful}/{len(results)} files successfully")
            print(f"📊 Total rows: {total_rows}")
            print(f"💾 Saved to: {zip_path}")
            print(f"{'='*80}\n")
            
            return send_file(
                zip_path,
                as_attachment=True,
                download_name=zip_filename,
                mimetype='application/zip'
            )
        
    except Exception as e:
        error_message = str(e)
        logger.error(f"Error processing file {filename}: {error_message}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        print(f"\n{'='*80}")
        print(f"❌ ERROR PROCESSING FILE")
        print(f"{'='*80}")
        print(f"Error: {error_message}")
        import traceback
        traceback.print_exc()
        print(f"{'='*80}\n")
        
        # Clean up
        if os.path.exists(filepath):
            logger.info(f"Cleaning up uploaded file: {filepath}")
            os.remove(filepath)
        
        # Provide user-friendly error messages
        if 'A79_API_KEY' in error_message or 'API key' in error_message:
            user_message = "API key is not configured. Please set the A79_API_KEY environment variable."
        elif 'API Error 500' in error_message:
            user_message = "The A79 API returned an internal server error. Please try again or contact support."
        elif 'API Error 401' in error_message:
            user_message = "Invalid API key. Please check your A79_API_KEY environment variable."
        else:
            user_message = error_message
        
        return jsonify({'error': user_message}), 500


if __name__ == '__main__':
    print("\n" + "="*80)
    print("🚀 Klearagent v3.5.10 - CBP 7501 with Invoice Header Filter")
    print("="*80)
    print(f"\n✅ Server starting...")
    print(f"📂 Upload folder: {os.path.abspath(UPLOAD_FOLDER)}")
    print(f"📂 Output folder: {os.path.abspath(OUTPUT_FOLDER)}")
    print(f"\n📊 Features:")
    print(f"   • Using API 1 (Unified PDF Parser)")
    print(f"   • Complete 80-column Excel export")
    print(f"   • All CS/CM/CD field mappings")
    print(f"   • One row per line item/HTS code")
    print(f"   • Comprehensive duty breakdowns")
    print(f"   • ✨ NEW: Invoice header lines automatically filtered")
    print(f"   • ✨ NEW: Invoice numbers extracted from headers")
    print(f"   • ✨ NEW: MPF values in correct HTS US Rate column")
    print(f"   • ✨ NEW: Run ID Fetch - Get results even when polling fails")
    print(f"   • ✨ NEW: Manual JSON upload & processing")
    print(f"\n🌐 Open your browser: http://localhost:5002")
    print(f"\n⚠️  Press CTRL+C to stop")
    print("="*80 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5002)
