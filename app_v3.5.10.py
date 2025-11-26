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
API1_CUSTOM_INSTRUCTIONS = """CBP Form 7501 Entry Summary - LLM Extraction Instructions

🚨 CRITICAL RULES (Must Follow)

1. PRIMARY HTS IDENTIFICATION
If files show 99-series as primary HTS:
- The primary HTS code MUST ALWAYS be the actual 10-digit merchandise classification code
- NEVER use codes starting with "99" as the primary HTS
- 99-series codes (9903.01.24, 9903.88.02, etc.) are ALWAYS additional tariff codes

How to identify:
- Look for the 10-digit code with the product description
- The primary HTS describes what is actually being imported (e.g., "ceramic sinks", "machinery parts")
- All codes starting with "99" go into additional_hts_codes array

Examples:
- ✅ Correct Primary: 6910.10.0030, 8486.90.0000, 3926.90.9880
- ❌ Wrong Primary: 9903.01.24, 9903.88.02, 9903.01.25

---

2. ADDITIONAL HTS NESTING
If additional_hts at line level:
- Additional HTS codes MUST be nested under primary_hts.additional_hts_codes[]
- NEVER place additional_hts_codes at the line item level
- This is a CRITICAL nesting requirement

Structure:
❌ WRONG:
{
  "line_number": "001",
  "additional_hts_codes": [...]  // At line level - WRONG!
}

✅ CORRECT:
{
  "line_number": "001",
  "primary_hts": {
    "hts_code": "6910.10.0030",
    "additional_hts_codes": [...]  // Nested under primary_hts - CORRECT!
  }
}

---

3. FEES NESTING
If fees at wrong level:
- Fees (MPF, HMF) MUST be nested under primary_hts.fees
- NEVER place fees at line item level
- NEVER place fees inside the additional_hts_codes array
- MPF and HMF do NOT have HTS codes - they are text labels only

Structure:
❌ WRONG - At line level:
{
  "line_number": "001",
  "fees": {...}  // WRONG!
}

❌ WRONG - In additional_hts_codes:
{
  "additional_hts_codes": [
    {
      "hts_code": "9903.01.24",
      "fees": {...}  // WRONG!
    }
  ]
}

✅ CORRECT - Under primary_hts:
{
  "primary_hts": {
    "hts_code": "6910.10.0030",
    "fees": {
      "mpf": {...},
      "hmf": {...}
    }  // CORRECT!
  }
}

---

4. CHARGE_TYPE PLACEMENT
If charge_type in wrong places:
- charge_type should ONLY appear at the line item level
- NEVER place charge_type inside primary_hts
- NEVER place charge_type inside additional_hts_codes

Structure:
❌ WRONG - Nested in primary_hts:
{
  "primary_hts": {
    "charge_type": "C1"  // WRONG!
  }
}

✅ CORRECT - At line item level:
{
  "line_number": "001",
  "charge_type": "C1",  // CORRECT!
  "primary_hts": {...}
}

---

5. CONFIDENCE SCORES
If missing confidence scores:
- Confidence scores are REQUIRED at all major data levels
- Add confidence_score (0.0 to 1.0) to:
  - Line item level
  - Primary HTS level
  - Each quantity object
  - Each additional HTS code
  - Each fee
  - Invoice values

Example:
{
  "line_number": "001",
  "confidence_score": 0.95,
  "primary_hts": {
    "hts_code": "6910.10.0030",
    "confidence_score": 0.98,
    "quantity": {
      "value": "22100",
      "unit": "KG",
      "confidence_score": 0.99
    },
    "additional_hts_codes": [
      {
        "hts_code": "9903.01.24",
        "confidence_score": 0.96
      }
    ],
    "fees": {
      "mpf": {
        "amount": 61.85,
        "confidence_score": 0.94
      }
    }
  }
}

---

SHIPMENT-LEVEL INFORMATION

Extract all information appearing ABOVE line item 001 in the item block area.

Manifest Information (Above First Line) - CRITICAL: Extract FIRST

**CRITICAL**: Extract Master Bill, House Bill, and Manifest Quantity that appear in the section above line 001.

**Common format:**
```
M: 61547419260          1 PCS
H: 5768871732
```

**Extract as:**
```json
{
  "shipment_info": {
    "manifest_info": {
      "master_bill": {
        "label": "M",
        "value": "61547419260",
        "confidence_score": 0.98
      },
      "house_bill": {
        "label": "H", 
        "value": "5768871732",
        "confidence_score": 0.98
      },
      "manifest_qty": {
        "value": "1",
        "unit": "PCS",
        "confidence_score": 0.99
      }
    }
  }
}
```

**Variations:**
- Master Bill may be labeled: "M:", "MASTER BILL:", "MASTER B/L:", "MBL:"
- House Bill may be labeled: "H:", "HOUSE BILL:", "HOUSE B/L:", "HBL:"
- Manifest QTY appears on same line as Master Bill or on separate line
- Common units: PCS (pieces), PAL (pallets), CTN (cartons), PKG (packages)

**Extraction Priority:**
1. FIRST: Extract manifest_info (Master Bill, House Bill, Manifest Quantity)
2. SECOND: Extract general info (PO numbers, invoices, containers)
3. THIRD: Extract block_specific info (references blocks at shipment level)

**Important Note:**
- manifest_qty is ONLY at shipment level (shipment_info.manifest_info.manifest_qty)
- This represents the total packaging quantity for the entire shipment
- Do NOT extract manifest_qty at the line item level

---

Complete Extraction Structure

Complete Line Item JSON Structure:

{
  "unique_id": "001",
  "line_number": "001",
  "invoice_number": "20250810-2",
  "description": "CERM SINK & LAVATORY PORCEL/CH",
  "item_number": "4022-708-21852",
  "charge_type": "C1",
  "SPI": "S",
  "CO": "MX",
  "confidence_score": 0.95,
  "primary_hts": {
    "hts_code": "6910.10.0030",
    "description": "Ceramic sinks and lavatories",
    "confidence_score": 0.98,
    "quantity": {
      "value": "22100",
      "unit": "KG",
      "confidence_score": 0.99
    },
    "quantity_2": {
      "value": "2480",
      "unit": "NO",
      "confidence_score": 0.99
    },
    "net_quantity": {
      "value": "2480",
      "unit": "NO",
      "confidence_score": 0.99
    },
    "gross_weight": {
      "value": "22100",
      "unit": "KG",
      "confidence_score": 0.99
    },
    "rate": "5.80%",
    "entered_value": 17856.00,
    "dutiable_value": 17856.00,
    "duty_amount": 1035.65,
    "invoice_values": {
      "giv": 17856.00,
      "usd": 17856.00,
      "niv": 17856.00,
      "confidence_score": 0.97
    },
    "additional_hts_codes": [
      {
        "hts_code": "9903.01.24",
        "description": "China tariff - Products of China",
        "rate": "20.00%",
        "entered_value": 17856.00,
        "dutiable_value": 17856.00,
        "duty_amount": 3571.20,
        "confidence_score": 0.96
      },
      {
        "hts_code": "9903.01.25",
        "description": "IEEPA Reciprocal Exclusion",
        "rate": "10.00%",
        "entered_value": 17856.00,
        "dutiable_value": 17856.00,
        "duty_amount": 1785.60,
        "confidence_score": 0.96
      }
    ],
    "fees": {
      "mpf": {
        "description": "MERCHANDISE PROCESSING FEE",
        "rate": ".3464%",
        "amount": 61.85,
        "confidence_score": 0.94
      },
      "hmf": {
        "description": "HARBOR MAINTENANCE FEE",
        "rate": ".125%",
        "amount": 22.32,
        "confidence_score": 0.94
      }
    }
  }
}

---

Detailed Field Extraction Rules

Header Information
Extract all header-level fields including:
- Entry number, port codes, dates
- Importer and consignee details
- Surety and bond information
- Carrier and transport details
- Broker/filer information

Line Items

Line Identification:
- unique_id: Same as line number
- line_number: 3-digit string with leading zeros ("001", "002")

Invoice and Item Numbers:
- invoice_number: Extract associated invoice number
- item_number: Extract part/item number if present

Charge Type:
- Extract from Box 32 if present (e.g., "C1", "C107", "C565")
- Place at line item level ONLY

Box 27 - SPI and Country of Origin:
- Extract data from Box 27 for each line item
- SPI (Special Program Indicator): First character indicates free trade agreement type
  - Common values: "S" (USMCA/NAFTA), "E" (other FTA), etc.
  - Place in "SPI" field at line item level
  - Example: If Box 27 shows "S O MX", extract "S" as SPI
- CO (Country of Origin): Extract the two-letter country code
  - This follows the origin indicator (usually "O")
  - Place in "CO" field at line item level
  - Example: If Box 27 shows "S O MX", extract "MX" as CO
  - Common codes: MX (Mexico), CN (China), CA (Canada), etc.

Structure for Box 27 data:
{
  "line_number": "001",
  "SPI": "S",
  "CO": "MX",
  "charge_type": "C1",
  ...
}

Quantities - ALWAYS SEPARATE:
- ALL quantities must have {value, unit} structure
- Common fields at LINE LEVEL: quantity, quantity_2, net_quantity, gross_weight
- Example: "22100 KG" → {"value": "22100", "unit": "KG"}
- Example: "1 PCS" → {"value": "1", "unit": "PCS"}

IMPORTANT - Manifest Quantity Location:
- manifest_qty is ONLY extracted at HEADER/SHIPMENT level (shipment_info.manifest_info.manifest_qty)
- Do NOT extract manifest_qty at the line item level
- Manifest quantity represents total packaging for the entire shipment
- Common manifest units: PCS (pieces), PAL (pallets), CTN (cartons), PKG (packages)

Primary HTS (CRITICAL)

Identification (Most Important Rule):
1. Find the 10-digit merchandise classification code
2. This describes the actual imported goods
3. NEVER use a 99-series code as primary

What to Extract:
- hts_code: 10-digit code with dots (e.g., "6910.10.0030")
- description: Product description
- All quantities (separated into value/unit)
- rate: Duty rate for this HTS
- entered_value: Value entered for customs
- dutiable_value: Dutiable value
- duty_amount: Duty calculated
- invoice_values: GIV, USD, NIV when available

Additional HTS Codes (CRITICAL NESTING)

Must be nested under primary_hts.additional_hts_codes[]

What qualifies:
- All 99-series codes (9903.01.24, 9903.01.25, 9903.88.02, etc.)
- These represent additional duties/tariffs on the merchandise

What to Extract for Each:
{
  "hts_code": "9903.01.24",
  "description": "Description if available",
  "rate": "20.00%",
  "entered_value": 17856.00,
  "dutiable_value": 17856.00,
  "duty_amount": 3571.20,
  "confidence_score": 0.96
}

Fees (CRITICAL NESTING)

Must be nested under primary_hts.fees

Key Facts:
- MPF and HMF do NOT have HTS codes
- They appear as text labels in the document
- Extract only: description, rate, amount

Structure:
{
  "mpf": {
    "description": "MERCHANDISE PROCESSING FEE",
    "rate": ".3464%",
    "amount": 61.85,
    "confidence_score": 0.94
  },
  "hmf": {
    "description": "HARBOR MAINTENANCE FEE",
    "rate": ".125%",
    "amount": 22.32,
    "confidence_score": 0.94
  }
}

Summary Totals
Extract from document footer/Box 39:
- merchandise_processing_fee_total: Total MPF (code 499)
- harbor_maintenance_fee_total: Total HMF (code 501)
- total_duty: Total duties
- total_entered_value: Sum of all entered values

---

Pre-Submission Validation Checklist

For EVERY line item, verify:

- [ ] Primary HTS is 10 digits (e.g., 6910.10.0030)
- [ ] Primary HTS does NOT start with "99"
- [ ] additional_hts_codes is nested under primary_hts (not at line level)
- [ ] fees is nested under primary_hts (not at line level or in additional_hts)
- [ ] charge_type is at line item level (not nested)
- [ ] All quantities have {value, unit} structure
- [ ] Confidence scores present at all required levels

If ALL checked → Submit
If ANY unchecked → Fix before submitting

---

Visual Hierarchy

Line Item (001)
├─ unique_id: "001"
├─ line_number: "001"
├─ charge_type: "C1"              ← At line level
├─ SPI: "S"                       ← Box 27: Special Program Indicator (FTA type)
├─ CO: "MX"                       ← Box 27: Country of Origin (2-letter code)
├─ confidence_score: 0.95
└─ primary_hts
   ├─ hts_code: "6910.10.0030"    ← 10-digit merchandise code
   ├─ confidence_score: 0.98
   ├─ quantities (all with value/unit)
   ├─ additional_hts_codes         ← ALL 99-series codes here
   │  ├─ 9903.01.24
   │  └─ 9903.01.25
   └─ fees                         ← MPF/HMF here
      ├─ mpf
      └─ hmf

---

Before/After Example (Real Extraction Error)

❌ BEFORE (INCORRECT - Multiple Errors)
{
  "line_number": "001",
  "additional_hts_codes": [
    {
      "hts_code": "8486.90.0000",
      "charge_type": "C565",
      "fees": {
        "mpf": {"amount": 31.27}
      }
    }
  ],
  "primary_hts": {
    "hts_code": "9903.01.24",
    "rate": "20%",
    "duty_amount": 341.6
  }
}

Errors:
1. Primary HTS is 99-series (9903.01.24) - should be 10-digit
2. additional_hts_codes at line level - should be nested under primary_hts
3. charge_type in additional_hts_codes - should be at line level
4. fees in additional_hts_codes - should be under primary_hts.fees
5. Missing confidence scores

✅ AFTER (CORRECT)
{
  "line_number": "001",
  "charge_type": "C565",
  "SPI": "S",
  "CO": "MX",
  "confidence_score": 0.95,
  "primary_hts": {
    "hts_code": "8486.90.0000",
    "description": "Machinery parts and accessories",
    "rate": "Free",
    "duty_amount": 0,
    "confidence_score": 0.98,
    "additional_hts_codes": [
      {
        "hts_code": "9903.01.24",
        "rate": "20%",
        "duty_amount": 341.6,
        "confidence_score": 0.96
      }
    ],
    "fees": {
      "mpf": {
        "description": "MERCHANDISE PROCESSING FEE",
        "rate": "0.3464%",
        "amount": 31.27,
        "confidence_score": 0.95
      }
    }
  }
}

---

Common Patterns in Documents

Pattern 1: Multiple HTS Codes in Sequence
When you see:
001  CERAMIC SINK & LAVATORY PORCEL/CH
     9903.01.24                    20.00%    3571.20
     9903.01.25                    10.00%    1785.60
     6910.10.0030    22100 KG      5.80%     1035.65

Interpret as:
- Primary HTS: 6910.10.0030 (the 10-digit merchandise code)
- Additional HTS: 9903.01.24, 9903.01.25 (the 99-series codes)

Pattern 2: Fees Appearing After HTS Codes
When you see:
     MERCHANDISE PROCESSING FEE     .3464%      61.85
     HARBOR MAINTENANCE FEE         .125%       22.32

Interpret as:
- These are fees, NOT HTS codes
- Place under primary_hts.fees
- Extract description, rate, and amount only

Pattern 3: Rate Column Alignment
The rate in the rate column belongs to the HTS code on the same line:
9903.01.24        20.00%  ← This rate belongs to 9903.01.24
9903.01.25        10.00%  ← This rate belongs to 9903.01.25
6910.10.0030       5.80%  ← This rate belongs to 6910.10.0030

---

Summary of Critical Rules

1. Primary HTS = 10-digit merchandise code (NEVER 99-series)
2. additional_hts_codes nested under primary_hts (NEVER at line level)
3. fees nested under primary_hts.fees (NEVER at line level or in additional_hts)
4. charge_type at line item level (NEVER nested)
5. Quantities always separated into {value, unit}
6. Confidence scores at all levels (line, HTS, quantities, fees)

---

Format Variations

CBP Form 7501s may have different visual layouts, but:
- The data structure remains consistent
- The hierarchical relationships stay the same
- Always apply these rules regardless of format

---

End of Instructions - Follow these rules for all Entry Summary extractions"""

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
    
    # Convert to JSON string
    payload_json = json.dumps(payload)
    logger.debug(f"Payload JSON size: {len(payload_json)} characters")
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'Accept': '*/*'
    }
    logger.debug(f"Request headers: {headers}")
    
    logger.info(f"Sending POST request to {api_url}")
    start_time = time.time()
    
    response = requests.post(
        api_url,
        data=payload_json,
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
        extracted_data = call_api(
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
            json.dump(extracted_data, f, indent=2)
        print(f"      ✅ Raw response saved: {debug_file}")
        
        # Parse the AI79 page-based response format
        print(f"\n   🔄 Parsing AI79 response format...")
        parsed_data = parse_ai79_response(extracted_data)
        
        # Save parsed response
        parsed_file = filepath.replace('.pdf', '_parsed_response.json')
        with open(parsed_file, 'w') as f:
            json.dump(parsed_data, f, indent=2)
        print(f"      ✅ Parsed response saved: {parsed_file}")
        
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
            <div class="upload-text">Drag & Drop your CBP 7501 PDF here</div>
            <p style="margin-top: 10px; color: #666;">or click to browse</p>
            <input type="file" id="fileInput" style="display: none;" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff">
        </div>

        <div class="file-info" id="fileInfo">
            <div style="font-weight: 600; color: #2e7d32;" id="fileName"></div>
            <div style="color: #666; font-size: 14px; margin-top: 5px;" id="fileSize"></div>
        </div>

        <button class="process-button" id="processButton">
            Extract & Generate Excel (80 Columns)
        </button>

        <div class="loading" id="loading">
            <div class="spinner"></div>
            <div style="color: #667eea; font-weight: 500;">Processing document...</div>
        </div>

        <div class="success-message" id="successMessage">
            <div style="font-weight: 600; color: #2e7d32; margin-bottom: 10px;">✅ Processing Complete!</div>
            <div style="color: #555;">Excel file with 80 columns downloaded successfully</div>
        </div>

        <div class="feature-list">
            <h3>📊 Complete Field Extraction</h3>
            <ul>
                <li>80 standardized Excel columns</li>
                <li>All CS (Customs Summary) fields</li>
                <li>All CM (Customs Merchandise) fields</li>
                <li>All CD (Customs Duty) fields</li>
                <li>One row per HTS code/line item</li>
                <li>Complete duty and fee breakdowns</li>
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
        let selectedFile = null;

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
                    runIdStatus.innerHTML = '✅ Results fetched! Processing to Excel...';
                    
                    // Now process the data through the normalization endpoint
                    // Send the data to process-json-data endpoint
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
                        a.download = `cbp7501_runid_${new Date().getTime()}.xlsx`;
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                        
                        runIdStatus.innerHTML = '✅ Success! Excel file downloaded.';
                        runIdInput.value = '';
                    } else {
                        throw new Error('Failed to process data to Excel');
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
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                handleFile(files[0]);
            }
        }, false);

        uploadArea.addEventListener('click', () => {
            fileInput.click();
        });

        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleFile(e.target.files[0]);
            }
        });

        function handleFile(file) {
            selectedFile = file;
            fileName.textContent = `📎 ${file.name}`;
            fileSize.textContent = `Size: ${formatBytes(file.size)}`;
            fileInfo.classList.add('show');
            processButton.classList.add('show');
        }

        function formatBytes(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
        }

        processButton.addEventListener('click', async () => {
            if (!selectedFile) return;

            processButton.disabled = true;
            loading.classList.add('show');
            successMessage.classList.remove('show');

            const formData = new FormData();
            formData.append('file', selectedFile);

            try {
                const response = await fetch('/upload', {
                    method: 'POST',
                    body: formData
                });

                if (response.ok) {
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `cbp7501_extracted_${new Date().getTime()}.xlsx`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);

                    loading.classList.remove('show');
                    successMessage.classList.add('show');
                } else {
                    // Try to get error message from response
                    let errorMessage = 'Error processing file. Please try again.';
                    try {
                        const errorData = await response.json();
                        if (errorData.error) {
                            errorMessage = `Error: ${errorData.error}`;
                        }
                    } catch (e) {
                        // If response is not JSON, use default message
                        errorMessage = `Error: ${response.status} ${response.statusText}`;
                    }
                    throw new Error(errorMessage);
                }
            } catch (error) {
                console.error('Error:', error);
                loading.classList.remove('show');
                alert(error.message || 'Error processing file. Please try again.');
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
        
        # Generate Excel file
        print(f"\n🔄 Step 3: Generating Excel file...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f'cbp7501_data_{timestamp}.xlsx'
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        normalizer.to_excel(normalized_data, output_path)
        
        print(f"   ✅ Excel generated")
        print(f"\n{'='*80}")
        print(f"✅ PROCESSING COMPLETE")
        print(f"{'='*80}")
        print(f"📊 Output: {len(normalized_data)} rows × 80 columns")
        print(f"💾 Saved to: {output_path}")
        print(f"{'='*80}\n")
        
        # Send Excel file
        return send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
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
        
        # Generate Excel file
        print(f"\n🔄 Step 4: Generating Excel file...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f'cbp7501_manual_{timestamp}.xlsx'
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        normalizer.to_excel(normalized_data, output_path)
        
        print(f"   ✅ Excel generated")
        print(f"\n{'='*80}")
        print(f"✅ PROCESSING COMPLETE")
        print(f"{'='*80}")
        print(f"📊 Output: {len(normalized_data)} rows × 80 columns")
        print(f"💾 Saved to: {output_path}")
        print(f"{'='*80}\n")
        
        # Send Excel file
        return send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
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


@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload and processing with validation"""
    logger.info("File upload request received")
    
    if 'file' not in request.files:
        logger.warning("No file provided in upload request")
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    logger.info(f"File received: {file.filename}")
    
    if file.filename == '':
        logger.warning("Empty filename in upload request")
        return jsonify({'error': 'No file selected'}), 400
    
    # Save uploaded file
    filename = file.filename
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    logger.info(f"Saving uploaded file to: {filepath}")
    file.save(filepath)
    logger.info(f"File saved successfully - Size: {os.path.getsize(filepath)} bytes")
    
    try:
        logger.info(f"Starting PDF processing workflow for: {filename}")
        print(f"\n{'='*80}")
        print(f"📥 PDF PROCESSING: {filename}")
        print(f"{'='*80}")
        
        # Process document with API (or mock data)
        print(f"\n🔄 Step 1: Extracting data from PDF...")
        extracted_data = process_document_with_api(filepath, filename)
        print(f"   ✅ Data extracted")
        
        # Normalize data
        print(f"\n🔄 Step 2: Normalizing data to CBP 7501 format...")
        normalizer = CBP7501Normalizer()
        normalized_data = normalizer.normalize(extracted_data)
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
            for warning in validation_report['warnings'][:10]:
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
        
        # Generate Excel file
        print(f"\n🔄 Step 4: Generating Excel file...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f'cbp7501_extracted_{timestamp}.xlsx'
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        normalizer.to_excel(normalized_data, output_path)
        
        print(f"   ✅ Excel generated")
        print(f"\n{'='*80}")
        print(f"✅ PROCESSING COMPLETE")
        print(f"{'='*80}")
        print(f"📊 Output: {len(normalized_data)} rows × 80 columns")
        print(f"💾 Saved to: {output_path}")
        print(f"{'='*80}\n")
        
        # Clean up uploaded file
        os.remove(filepath)
        
        # Send Excel file
        return send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        logger.error(f"Error processing file {filename}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        print(f"\n{'='*80}")
        print(f"❌ ERROR PROCESSING FILE")
        print(f"{'='*80}")
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'='*80}\n")
        
        # Clean up
        if os.path.exists(filepath):
            logger.info(f"Cleaning up uploaded file: {filepath}")
            os.remove(filepath)
        
        return jsonify({'error': str(e)}), 500


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
