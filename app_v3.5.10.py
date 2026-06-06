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
API1_AGENT_NAME = "CBP 7501 Extraction Agent (Copy)"
API1_AGENT_ID = "852e4e58-8fc3-4109-9993-f6820786331d"
API1_WORKFLOW_ID = None


# Custom instructions for API 1 — loaded from prompt file
_prompt_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CBP_7501_MASTER_EXTRACTION_PROMPT_V3_1.txt")
with open(_prompt_file, "r") as _f:
    API1_CUSTOM_INSTRUCTIONS = _f.read()

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


def call_api(api_key, api_url, pdf_base64, custom_instructions, agent_name, workflow_id, page_description, agent_id=None):
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
    
    # If workflow_id is provided, use workflow-specific endpoint (legacy)
    if workflow_id:
        api_url = f"https://klearnow.prod.a79.ai/api/v1/public/workflow/{workflow_id}/run"

    logger.info(f"Using endpoint: {api_url}")
    print(f"   🚀 Calling API for {page_description}...")
    print(f"      Endpoint: {api_url}")
    print(f"      Agent: {agent_name} (ID: {agent_id or 'N/A'})")
    print(f"      Instructions: {custom_instructions[:80]}...")

    # Prepare payload — agent_name and agent_id go in the body
    payload = {
        "agent_name": agent_name,
        "agent_inputs": {
            "pdf_document": pdf_base64,
            "custom_instructions": custom_instructions
        }
    }
    if agent_id:
        payload["agent_id"] = agent_id
    
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
            "entire document",
            agent_id=API1_AGENT_ID
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
    <title>KlearNow · 7501 Extraction</title>
    <style>
        :root {
            --kn-primary:       #1746A2;
            --kn-primary-dark:  #0f3480;
            --kn-primary-light: #EBF2FF;
            --kn-accent:        #0EA5E9;
            --kn-success:       #16A34A;
            --kn-success-light: #DCFCE7;
            --kn-success-border:#86EFAC;
            --kn-error:         #DC2626;
            --kn-error-light:   #FEE2E2;
            --kn-error-border:  #FCA5A5;
            --kn-gray-50:       #F8FAFC;
            --kn-gray-100:      #F1F5F9;
            --kn-gray-200:      #E2E8F0;
            --kn-gray-400:      #94A3B8;
            --kn-gray-600:      #475569;
            --kn-gray-800:      #1E293B;
            --kn-border:        #E2E8F0;
            --kn-radius:        8px;
            --kn-radius-lg:     12px;
            --kn-shadow:        0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.05);
            --kn-shadow-md:     0 4px 6px -1px rgba(0,0,0,.08);
        }
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Inter', Roboto, sans-serif;
            background: var(--kn-gray-100);
            min-height: 100vh;
            color: var(--kn-gray-800);
            font-size: 14px;
        }

        /* ── Navbar ── */
        .navbar {
            background: #fff;
            border-bottom: 1px solid var(--kn-border);
            height: 56px;
            padding: 0 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky; top: 0; z-index: 100;
            box-shadow: var(--kn-shadow);
        }
        .navbar-left { display:flex; align-items:center; gap:10px; }
        .navbar-logo {
            width:32px; height:32px;
            background: var(--kn-primary);
            border-radius: 8px;
            display:flex; align-items:center; justify-content:center;
            color:#fff; font-weight:800; font-size:13px; letter-spacing:-.5px;
        }
        .navbar-name  { font-weight:700; font-size:16px; color:var(--kn-primary); }
        .navbar-sep   { width:1px; height:18px; background:var(--kn-border); margin:0 4px; }
        .navbar-sub   { font-size:12px; color:var(--kn-gray-400); font-weight:500; }
        .navbar-badge {
            background:var(--kn-primary-light); color:var(--kn-primary);
            font-size:11px; font-weight:700; padding:3px 10px; border-radius:20px;
            letter-spacing:.2px;
        }

        /* ── Layout ── */
        .main {
            display: grid;
            grid-template-columns: 400px 1fr;
            gap: 20px;
            padding: 24px;
            max-width: 1160px;
            margin: 0 auto;
        }
        @media (max-width:800px) {
            .main { grid-template-columns:1fr; }
            .runs-list { max-height:360px; }
        }

        /* ── Card ── */
        .card {
            background:#fff;
            border-radius: var(--kn-radius-lg);
            border: 1px solid var(--kn-border);
            box-shadow: var(--kn-shadow);
        }
        .card-header {
            padding:14px 18px;
            border-bottom:1px solid var(--kn-border);
            display:flex; align-items:center; justify-content:space-between;
        }
        .card-title { font-size:13px; font-weight:600; color:var(--kn-gray-800); }
        .card-hint  { font-size:11px; color:var(--kn-gray-400); }
        .card-body  { padding:18px; }

        /* ── Upload zone ── */
        .upload-zone {
            border: 2px dashed var(--kn-border);
            border-radius: var(--kn-radius);
            padding: 36px 16px;
            text-align:center;
            cursor:pointer;
            transition: all .18s;
            background: var(--kn-gray-50);
            user-select:none;
        }
        .upload-zone:hover, .upload-zone.dragover {
            border-color: var(--kn-primary);
            background: var(--kn-primary-light);
        }
        .upload-zone-icon {
            width:44px; height:44px;
            background:var(--kn-primary-light);
            border-radius:50%;
            display:flex; align-items:center; justify-content:center;
            margin: 0 auto 10px;
        }
        .upload-zone-icon svg { width:22px; height:22px; }
        .upload-zone-label { font-weight:600; font-size:14px; margin-bottom:4px; }
        .upload-zone-hint  { font-size:12px; color:var(--kn-gray-400); }

        /* ── File chip ── */
        .file-chip {
            display:none; align-items:center; gap:10px;
            background:var(--kn-success-light);
            border:1px solid var(--kn-success-border);
            border-radius: var(--kn-radius);
            padding:10px 12px;
            margin-top:12px;
        }
        .file-chip.show { display:flex; }
        .file-chip-icon {
            width:32px; height:32px; flex-shrink:0;
            background:var(--kn-success); border-radius:6px;
            display:flex; align-items:center; justify-content:center;
        }
        .file-chip-icon svg { width:16px; height:16px; }
        .file-chip-name { font-size:13px; font-weight:600; }
        .file-chip-size { font-size:11px; color:var(--kn-gray-400); margin-top:1px; }
        .file-chip-remove {
            margin-left:auto; background:none; border:none;
            cursor:pointer; color:var(--kn-gray-400); padding:2px;
            display:flex; align-items:center;
        }
        .file-chip-remove:hover { color:var(--kn-error); }

        /* ── Progress ── */
        .progress-wrap {
            display:none; padding:14px 0 4px;
        }
        .progress-wrap.show { display:block; }
        .progress-bar {
            width:100%; height:3px;
            background:var(--kn-gray-200); border-radius:2px;
            overflow:hidden; margin-bottom:8px;
        }
        .progress-fill {
            height:100%; width:35%;
            background:var(--kn-primary); border-radius:2px;
            animation: indeterminate 1.4s ease-in-out infinite;
        }
        @keyframes indeterminate {
            0%   { transform:translateX(-100%); }
            100% { transform:translateX(380%); }
        }
        .progress-label { font-size:12px; color:var(--kn-gray-600); font-weight:500; }

        /* ── Inline alerts ── */
        .alert {
            display:none; border-radius:var(--kn-radius);
            padding:10px 14px; margin-top:12px; font-size:13px;
        }
        .alert.show { display:block; }
        .alert-success {
            background:var(--kn-success-light);
            border:1px solid var(--kn-success-border);
            color:var(--kn-success);
        }
        .alert-success strong { font-size:13px; }
        .alert-error {
            background:var(--kn-error-light);
            border:1px solid var(--kn-error-border);
            color:var(--kn-error);
        }

        /* ── Buttons ── */
        .btn {
            display:inline-flex; align-items:center; justify-content:center;
            gap:7px; padding:10px 16px; border-radius:var(--kn-radius);
            font-size:13px; font-weight:600; cursor:pointer; border:none;
            transition: all .15s; text-decoration:none; width:100%;
        }
        .btn svg { flex-shrink:0; }
        .btn-primary {
            background:var(--kn-primary); color:#fff;
            margin-top:12px;
        }
        .btn-primary:hover:not(:disabled) { background:var(--kn-primary-dark); }
        .btn-primary:disabled { background:var(--kn-gray-400); cursor:not-allowed; }
        .btn-outline {
            background:#fff; color:var(--kn-primary);
            border:1.5px solid var(--kn-primary);
            margin-top:8px;
        }
        .btn-outline:hover { background:var(--kn-primary-light); }
        .btn-muted {
            background:var(--kn-gray-100); color:var(--kn-gray-600);
            border:1px solid var(--kn-border);
        }
        .btn-muted:hover { background:var(--kn-gray-200); }
        .btn-sm { padding:7px 12px; font-size:12px; width:auto; }

        /* ── Divider ── */
        .divider {
            display:flex; align-items:center; gap:10px;
            margin:18px 0 14px;
        }
        .divider::before, .divider::after {
            content:''; flex:1; height:1px; background:var(--kn-border);
        }
        .divider span {
            font-size:10px; font-weight:700; color:var(--kn-gray-400);
            text-transform:uppercase; letter-spacing:.6px;
        }

        /* ── Input field ── */
        .input-row { display:flex; gap:8px; }
        .input-field {
            flex:1; padding:8px 12px;
            border:1px solid var(--kn-border); border-radius:var(--kn-radius);
            font-size:13px; outline:none; background:#fff;
            transition:border-color .15s;
        }
        .input-field:focus { border-color:var(--kn-primary); }
        .field-label {
            font-size:12px; font-weight:600; color:var(--kn-gray-600);
            margin-bottom:6px; display:block;
        }
        .field-status {
            display:none; font-size:12px; margin-top:6px;
            padding:6px 10px; border-radius:4px;
        }

        /* ── Run History ── */
        .history-card {
            display:flex; flex-direction:column;
            /* fill viewport height minus navbar */
            max-height: calc(100vh - 88px);
        }
        .history-header {
            padding:14px 18px;
            border-bottom:1px solid var(--kn-border);
            display:flex; align-items:center; justify-content:space-between;
            flex-shrink:0;
        }
        .history-count {
            background:var(--kn-gray-100); border:1px solid var(--kn-border);
            color:var(--kn-gray-600); font-size:11px; font-weight:600;
            padding:2px 8px; border-radius:20px;
        }
        .history-search {
            padding:10px 14px;
            border-bottom:1px solid var(--kn-border);
            flex-shrink:0;
        }
        .search-input {
            width:100%; padding:7px 10px 7px 30px;
            border:1px solid var(--kn-border); border-radius:var(--kn-radius);
            font-size:12px; outline:none; background:var(--kn-gray-50);
            background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='%2394A3B8' stroke-width='2'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cpath d='m21 21-4.35-4.35'/%3E%3C/svg%3E");
            background-repeat:no-repeat; background-position:10px center;
        }
        .search-input:focus { border-color:var(--kn-primary); background-color:#fff; }
        .runs-list {
            overflow-y:auto; flex:1;
        }
        .run-item {
            padding:12px 18px;
            border-bottom:1px solid var(--kn-gray-100);
            display:grid; grid-template-columns:1fr auto;
            gap:8px; align-items:start;
            transition:background .1s;
        }
        .run-item:hover { background:var(--kn-gray-50); }
        .run-item:last-child { border-bottom:none; }
        .run-entry {
            font-size:13px; font-weight:700;
            font-family:'SF Mono','Fira Code','Consolas',monospace;
            color:var(--kn-gray-800); letter-spacing:.3px;
        }
        .run-file {
            font-size:11px; color:var(--kn-gray-600); margin-top:2px;
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
            max-width:240px;
        }
        .run-meta {
            display:flex; align-items:center; gap:6px;
            margin-top:5px; flex-wrap:wrap;
        }
        .run-time   { font-size:11px; color:var(--kn-gray-400); }
        .run-badge  {
            font-size:10px; font-weight:700; padding:2px 6px; border-radius:4px;
        }
        .run-badge.success { background:var(--kn-success-light); color:var(--kn-success); }
        .run-badge.failed  { background:var(--kn-error-light);   color:var(--kn-error); }
        .run-badge.processing { background:var(--kn-primary-light); color:var(--kn-primary); }
        .run-lines  {
            font-size:10px; color:var(--kn-gray-400);
            background:var(--kn-gray-100); padding:2px 5px; border-radius:4px;
        }
        .run-actions { display:flex; flex-direction:column; align-items:flex-end; gap:4px; }
        .run-del {
            background:none; border:none; cursor:pointer;
            color:var(--kn-gray-300); padding:2px;
            display:flex; align-items:center;
            transition:color .15s;
        }
        .run-del:hover { color:var(--kn-error); }
        .empty-state {
            padding:60px 20px; text-align:center; color:var(--kn-gray-400);
        }
        .empty-icon {
            width:44px; height:44px; background:var(--kn-gray-100); border-radius:50%;
            display:flex; align-items:center; justify-content:center;
            margin:0 auto 12px;
        }
        .empty-state p { font-size:13px; line-height:1.6; }
    </style>
</head>
<body>

<nav class="navbar">
    <div class="navbar-left">
        <div class="navbar-logo">KN</div>
        <span class="navbar-name">KlearNow</span>
        <div class="navbar-sep"></div>
        <span class="navbar-sub">7501 Extraction</span>
    </div>
    <span class="navbar-badge">CBP 7501 Agent &nbsp;v3.5.10</span>
</nav>

<div class="main">

    <!-- ── Left panel ───────────────────────────────────── -->
    <div style="display:flex;flex-direction:column;gap:16px;">

        <!-- Upload card -->
        <div class="card">
            <div class="card-header">
                <span class="card-title">New Extraction</span>
                <span class="card-hint">PDF &nbsp;·&nbsp; PNG &nbsp;·&nbsp; TIFF</span>
            </div>
            <div class="card-body">

                <div class="upload-zone" id="uploadZone">
                    <div class="upload-zone-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="#1746A2" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                            <polyline points="17 8 12 3 7 8"/>
                            <line x1="12" y1="3" x2="12" y2="15"/>
                        </svg>
                    </div>
                    <div class="upload-zone-label">Drop CBP 7501 documents here</div>
                    <div class="upload-zone-hint">or click to browse &nbsp;·&nbsp; multiple files supported</div>
                    <input type="file" id="fileInput" style="display:none;" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" multiple>
                </div>

                <!-- File chip -->
                <div class="file-chip" id="fileChip">
                    <div class="file-chip-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                            <polyline points="14 2 14 8 20 8"/>
                        </svg>
                    </div>
                    <div>
                        <div class="file-chip-name" id="chipName"></div>
                        <div class="file-chip-size" id="chipSize"></div>
                    </div>
                    <button class="file-chip-remove" onclick="clearFiles()">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                </div>

                <!-- Progress -->
                <div class="progress-wrap" id="progressWrap">
                    <div class="progress-bar"><div class="progress-fill"></div></div>
                    <div class="progress-label" id="progressLabel">Sending to AI agent...</div>
                </div>

                <!-- Success alert -->
                <div class="alert alert-success" id="alertSuccess">
                    <strong>Extraction complete</strong>
                    <div style="font-size:12px;margin-top:3px;color:#166534;" id="alertSuccessDetail"></div>
                </div>

                <!-- Error alert -->
                <div class="alert alert-error" id="alertError"></div>

                <!-- Run button -->
                <button class="btn btn-primary" id="runBtn" style="display:none;" onclick="runExtraction()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    <span id="runBtnLabel">Run Extraction</span>
                </button>

                <!-- New Run button -->
                <button class="btn btn-outline" id="newRunBtn" style="display:none;" onclick="newRun()">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    New Run
                </button>

            </div>
        </div>

        <!-- Manual recovery card -->
        <div class="card">
            <div class="card-header">
                <span class="card-title">Manual Recovery</span>
                <span class="card-hint">If polling times out</span>
            </div>
            <div class="card-body">

                <label class="field-label">Fetch by Run ID</label>
                <div class="input-row">
                    <input class="input-field" id="runIdInput" type="text" placeholder="run_id from console or dashboard">
                    <button class="btn btn-muted btn-sm" onclick="fetchByRunId()">Fetch</button>
                </div>
                <div class="field-status" id="runIdStatus"></div>

                <div class="divider"><span>or</span></div>

                <label class="field-label">Upload AI Agent JSON</label>
                <input type="file" id="jsonFileInput" accept=".json" style="display:none;">
                <button class="btn btn-muted" onclick="document.getElementById('jsonFileInput').click()">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    Upload JSON
                </button>
                <div class="field-status" id="jsonStatus"></div>

            </div>
        </div>
    </div>

    <!-- ── Right panel: Run History ─────────────────────── -->
    <div class="card history-card">
        <div class="history-header">
            <span class="card-title">Run History</span>
            <span class="history-count" id="historyCount">0 runs</span>
        </div>
        <div class="history-search">
            <input class="search-input" id="searchInput" type="text"
                   placeholder="Search by entry number or filename..."
                   oninput="renderRuns()">
        </div>
        <div class="runs-list" id="runsList">
            <div class="empty-state" id="emptyState">
                <div class="empty-icon">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                </div>
                <p>No extractions yet.<br>Upload a CBP 7501 to get started.</p>
            </div>
        </div>
    </div>

</div>

<script>
let selectedFiles = [];
let runs = [];
const STORAGE_KEY = 'kn_7501_runs';

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => { loadRuns(); renderRuns(); });

// ── Upload zone ───────────────────────────────────────────────────────────
const uploadZone = document.getElementById('uploadZone');
const fileInput  = document.getElementById('fileInput');

['dragenter','dragover','dragleave','drop'].forEach(ev =>
    uploadZone.addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); }));
['dragenter','dragover'].forEach(ev =>
    uploadZone.addEventListener(ev, () => uploadZone.classList.add('dragover')));
['dragleave','drop'].forEach(ev =>
    uploadZone.addEventListener(ev, () => uploadZone.classList.remove('dragover')));
uploadZone.addEventListener('drop', e => {
    const f = Array.from(e.dataTransfer.files);
    if (f.length) handleFiles(f);
});
uploadZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', e => { if (e.target.files.length) handleFiles(Array.from(e.target.files)); });

function handleFiles(files) {
    selectedFiles = files;
    const total = files.reduce((s, f) => s + f.size, 0);
    document.getElementById('chipName').textContent =
        files.length === 1 ? files[0].name : `${files.length} files selected`;
    document.getElementById('chipSize').textContent = formatBytes(files.length === 1 ? files[0].size : total);
    document.getElementById('fileChip').classList.add('show');
    document.getElementById('runBtnLabel').textContent =
        files.length > 1 ? `Run Extraction  (${files.length} files)` : 'Run Extraction';
    document.getElementById('runBtn').style.display = 'flex';
    document.getElementById('newRunBtn').style.display = 'none';
    hide('alertSuccess'); hide('alertError'); hide('progressWrap');
}

function clearFiles() {
    selectedFiles = []; fileInput.value = '';
    document.getElementById('fileChip').classList.remove('show');
    document.getElementById('runBtn').style.display = 'none';
    document.getElementById('newRunBtn').style.display = 'none';
    hide('alertSuccess'); hide('alertError'); hide('progressWrap');
}

function newRun() { clearFiles(); }

// ── Run extraction ────────────────────────────────────────────────────────
async function runExtraction() {
    if (!selectedFiles.length) return;

    const runBtn = document.getElementById('runBtn');
    runBtn.disabled = true;
    hide('alertSuccess'); hide('alertError');
    show('progressWrap');
    setLabel('progressLabel',
        selectedFiles.length > 1 ? `Processing ${selectedFiles.length} documents...` : 'Sending to AI agent...');

    // Pending run record
    const rid = 'run_' + Date.now();
    runs.unshift({
        id: rid,
        timestamp: new Date().toISOString(),
        filenames: selectedFiles.map(f => f.name),
        status: 'processing',
        entry_number: null, line_count: null,
    });
    saveRuns(); renderRuns();

    const fd = new FormData();
    if (selectedFiles.length === 1) fd.append('file', selectedFiles[0]);
    else selectedFiles.forEach(f => fd.append('files[]', f));

    try {
        setLabel('progressLabel', 'AI agent processing — this may take a few minutes...');
        const res = await fetch('/upload', { method: 'POST', body: fd });

        if (!res.ok) {
            let msg = `Error ${res.status}`;
            try { const d = await res.json(); msg = d.error || msg; } catch(e) {}
            throw new Error(msg);
        }

        const blob     = await res.blob();
        const isZip    = res.headers.get('content-type')?.includes('zip');
        const basename = selectedFiles.length > 1 ? 'cbp7501_batch' : `cbp7501_${selectedFiles[0].name.replace(/\\.[^/.]+$/, '')}`;
        const filename = `${basename}_${Date.now()}.${isZip ? 'zip' : 'json'}`;

        let entryNumber = null, lineCount = null;
        if (!isZip) {
            try {
                const t = await blob.text();
                const d = JSON.parse(t);
                entryNumber = d?.shipment?.ENTRY_NUMBER || d?.ENTRY_NUMBER || null;
                lineCount   = d?.line_items?.length
                           || d?.extraction_metadata?.valid_7501_line_items
                           || null;
            } catch(e) {}
        }

        downloadBlob(blob, filename);

        const idx = runs.findIndex(r => r.id === rid);
        if (idx !== -1) Object.assign(runs[idx], { status:'success', entry_number:entryNumber, line_count:lineCount, filename });
        saveRuns(); renderRuns();

        hide('progressWrap');
        document.getElementById('alertSuccessDetail').textContent =
            lineCount ? `${lineCount} line items extracted  ·  ${filename}` : `Downloaded: ${filename}`;
        show('alertSuccess');
        document.getElementById('newRunBtn').style.display = 'flex';
        document.getElementById('fileChip').classList.remove('show');

    } catch(err) {
        const idx = runs.findIndex(r => r.id === rid);
        if (idx !== -1) Object.assign(runs[idx], { status:'failed', error: err.message });
        saveRuns(); renderRuns();
        hide('progressWrap');
        document.getElementById('alertError').textContent = err.message;
        show('alertError');
        runBtn.disabled = false;
    }
}

// ── Run History ───────────────────────────────────────────────────────────
function loadRuns()  { try { runs = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); } catch(e) { runs=[]; } }
function saveRuns()  { if (runs.length>100) runs=runs.slice(0,100); localStorage.setItem(STORAGE_KEY, JSON.stringify(runs)); }
function deleteRun(id) { runs = runs.filter(r=>r.id!==id); saveRuns(); renderRuns(); }

function renderRuns() {
    const q      = (document.getElementById('searchInput')?.value || '').toLowerCase();
    const list   = document.getElementById('runsList');
    const badge  = document.getElementById('historyCount');
    badge.textContent = `${runs.length} run${runs.length!==1?'s':''}`;

    const filtered = q ? runs.filter(r =>
        (r.entry_number||'').toLowerCase().includes(q) ||
        (r.filenames||[r.filename||'']).join(' ').toLowerCase().includes(q)
    ) : runs;

    if (!filtered.length) {
        list.innerHTML = '';
        list.innerHTML = `<div class="empty-state">
            <div class="empty-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            </div>
            <p>${q ? 'No runs match your search.' : 'No extractions yet.<br>Upload a CBP 7501 to get started.'}</p>
        </div>`;
        return;
    }

    list.innerHTML = filtered.map(r => {
        const names   = r.filenames || (r.filename ? [r.filename] : ['Unknown']);
        const display = names.length > 1 ? `${names.length} files` : names[0];
        const ts      = new Date(r.timestamp);
        const time    = ts.toLocaleDateString('en-US',{month:'short',day:'numeric'})
                      + '  ·  '
                      + ts.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});
        const label   = r.status==='processing' ? 'Running' : r.status==='success' ? 'Success' : 'Failed';
        return `<div class="run-item">
            <div>
                <div class="run-entry">${r.entry_number || '&mdash;'}</div>
                <div class="run-file" title="${display}">${display}</div>
                <div class="run-meta">
                    <span class="run-time">${time}</span>
                    <span class="run-badge ${r.status}">${label}</span>
                    ${r.line_count ? `<span class="run-lines">${r.line_count}&thinsp;lines</span>` : ''}
                </div>
            </div>
            <div class="run-actions">
                <button class="run-del" onclick="deleteRun('${r.id}')" title="Remove">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </div>
        </div>`;
    }).join('');
}

// ── Fetch by Run ID ───────────────────────────────────────────────────────
async function fetchByRunId() {
    const inp    = document.getElementById('runIdInput');
    const status = document.getElementById('runIdStatus');
    const id     = inp.value.trim();
    if (!id) return;

    setStatus(status, 'Fetching...', 'info');
    try {
        const r1 = await fetch('/fetch-by-runid',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:id})});
        const d1 = await r1.json();
        if (!r1.ok || !d1.success) throw new Error(d1.error||'Could not fetch');

        setStatus(status, 'Processing...', 'info');
        const r2 = await fetch('/process-json-data',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d1.data)});
        if (!r2.ok) throw new Error('Processing failed');

        downloadBlob(await r2.blob(), `cbp7501_runid_${Date.now()}.json`);
        setStatus(status, 'Downloaded successfully', 'success');
        inp.value = '';
        runs.unshift({id:'fetch_'+Date.now(),timestamp:new Date().toISOString(),filenames:[`run_id: ${id}`],status:'success',entry_number:null,line_count:null});
        saveRuns(); renderRuns();
    } catch(e) { setStatus(status, e.message, 'error'); }
}

// ── Manual JSON ───────────────────────────────────────────────────────────
document.getElementById('jsonFileInput').addEventListener('change', async e => {
    if (!e.target.files.length) return;
    const status = document.getElementById('jsonStatus');
    setStatus(status, 'Processing...', 'info');
    const fd = new FormData(); fd.append('file', e.target.files[0]);
    try {
        const res = await fetch('/process-json',{method:'POST',body:fd});
        if (!res.ok) throw new Error('Processing failed');
        downloadBlob(await res.blob(), `cbp7501_manual_${Date.now()}.xlsx`);
        setStatus(status, 'Downloaded successfully', 'success');
    } catch(err) { setStatus(status, err.message, 'error'); }
    e.target.value = '';
    setTimeout(()=>{ status.style.display='none'; }, 3500);
});

// ── Helpers ───────────────────────────────────────────────────────────────
function show(id) { document.getElementById(id).classList.add('show'); }
function hide(id) { document.getElementById(id).classList.remove('show'); }
function setLabel(id, text) { document.getElementById(id).textContent = text; }

function setStatus(el, msg, type) {
    el.style.display = 'block';
    const colors = {
        info:    {bg:'var(--kn-primary-light)',  color:'var(--kn-primary)'},
        success: {bg:'var(--kn-success-light)',  color:'var(--kn-success)'},
        error:   {bg:'var(--kn-error-light)',    color:'var(--kn-error)'},
    };
    const c = colors[type] || colors.info;
    el.style.background = c.bg; el.style.color = c.color;
    el.style.padding = '6px 10px'; el.style.borderRadius = '4px';
    el.textContent = msg;
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    URL.revokeObjectURL(url); document.body.removeChild(a);
}

function formatBytes(b) {
    if (!b) return '0 B';
    const k=1024, s=['B','KB','MB'];
    const i=Math.floor(Math.log(b)/Math.log(k));
    return (b/Math.pow(k,i)).toFixed(1)+' '+s[i];
}
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
    
    app.run(debug=True, host='0.0.0.0', port=5002,
            extra_files=[_prompt_file])
