# AI79 API Endpoints and Setup Guide

## Overview
This document provides complete information about the AI79 Public Workflow API endpoints, configuration, and setup for the CBP 7501 processing application.

---

## Base Configuration

### API Base URL
```
https://klearnow.prod.a79.ai/api/v1/public/workflow
```

### API Key
```
sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i
```

### Agent Name
```
Unified PDF Parser
```

### Dashboard URL
```
https://klearnow.prod.a79.ai
```

---

## Endpoints

### 1. Workflow Run Endpoint (Initial Request)

#### Agent-Based (No Workflow ID)
**Endpoint:**
```
POST https://klearnow.prod.a79.ai/api/v1/public/workflow/run
```

**Headers:**
```json
{
  "Authorization": "Bearer sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i",
  "Content-Type": "application/json",
  "Accept": "*/*"
}
```

**Request Body:**
```json
{
  "agent_name": "Unified PDF Parser",
  "agent_inputs": {
    "pdf_document": "<base64_encoded_pdf>",
    "custom_instructions": "<extraction_instructions>"
  }
}
```

#### Workflow ID-Based (With Workflow ID)
**Endpoint:**
```
POST https://klearnow.prod.a79.ai/api/v1/public/workflow/{workflow_id}/run
```

**Example:**
```
POST https://klearnow.prod.a79.ai/api/v1/public/workflow/wf_xxxxxxxxxxxx/run
```

**Headers:** Same as above

**Request Body:**
```json
{
  "agent_inputs": {
    "pdf_document": "<base64_encoded_pdf>",
    "custom_instructions": "<extraction_instructions>"
  }
}
```
*Note: No `agent_name` needed when using workflow_id*

---

### 2. Polling Endpoints (Status Check)

The application tries multiple polling endpoints in order of preference:

#### Primary Polling Endpoint (Recommended)
```
GET https://klearnow.prod.a79.ai/api/v1/public/workflow/{run_id}/status?output_var=final_display_output
```

**Headers:**
```json
{
  "Authorization": "Bearer sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i",
  "Content-Type": "application/json"
}
```

#### Alternate Polling Endpoints (Fallback)
If the primary endpoint returns 404, the application tries these in order:

1. **Workflow-specific with run_id:**
   ```
   GET https://klearnow.prod.a79.ai/api/v1/public/workflow/{workflow_id}/run/{run_id}
   ```

2. **Workflow-specific status:**
   ```
   GET https://klearnow.prod.a79.ai/api/v1/public/workflow/{workflow_id}/runs/{run_id}/status
   ```

3. **Simple run status:**
   ```
   GET https://klearnow.prod.a79.ai/api/v1/public/workflow/run/{run_id}
   ```

4. **Run ID only:**
   ```
   GET https://klearnow.prod.a79.ai/api/v1/public/workflow/{run_id}
   ```

5. **Legacy workflow cards:**
   ```
   GET https://klearnow.prod.a79.ai/api/v1/workflow/cards/{run_id}
   ```

---

## Response Format

### Initial Response (Workflow Started)
```json
{
  "run_id": "run_xxxxxxxxxxxx",
  "workflow_id": "wf_xxxxxxxxxxxx",
  "status": "running" | "pending" | "completed",
  "message": "Workflow started successfully"
}
```

### Polling Response (In Progress)
```json
{
  "status": "running" | "pending",
  "run_id": "run_xxxxxxxxxxxx",
  "workflow_id": "wf_xxxxxxxxxxxx",
  "progress": 0.5,
  "message": "Processing..."
}
```

### Polling Response (Completed)
```json
{
  "status": "completed",
  "run_id": "run_xxxxxxxxxxxx",
  "workflow_id": "wf_xxxxxxxxxxxx",
  "output": {
    // Extracted JSON data structure
    "line_items": [...],
    "entry_summary": {...}
  }
}
```

**Note:** The `output` field may be:
- A JSON object (dict)
- A JSON string (needs parsing)
- An escaped JSON string (double-encoded, needs unescaping)

---

## Polling Configuration

### Polling Parameters
- **Max Attempts:** 120
- **Poll Interval:** 5 seconds
- **Max Wait Time:** 10 minutes (120 × 5 seconds)
- **Request Timeout:** 30 seconds per poll request

### Polling Logic
1. Wait 5 seconds between attempts
2. Check status from polling endpoint
3. If status is "completed", extract `output` field
4. If `output` is a string, attempt JSON parsing
5. Return extracted data when available
6. Timeout after 10 minutes with instructions to check dashboard

---

## Workflow ID Setup

### Current Configuration
```python
API1_WORKFLOW_ID = None  # Set to None to use agent_name
```

### To Enable Workflow ID (Recommended)
1. Go to https://klearnow.prod.a79.ai
2. Navigate to workflows
3. Find "Unified PDF Parser" workflow
4. Copy the workflow ID (format: `wf_xxxxxxxxxxxx`)
5. Update configuration:
   ```python
   API1_WORKFLOW_ID = "wf_xxxxxxxxxxxx"
   ```

### Benefits of Using Workflow ID
- More reliable endpoint routing
- Better tracking in dashboard
- Potentially faster processing
- More consistent results

---

## Request Payload Structure

### Agent-Based Request
```python
{
    "agent_name": "Unified PDF Parser",
    "agent_inputs": {
        "pdf_document": "<base64_string>",
        "custom_instructions": "<instructions_text>"
    }
}
```

### Workflow ID-Based Request
```python
{
    "agent_inputs": {
        "pdf_document": "<base64_string>",
        "custom_instructions": "<instructions_text>"
    }
}
```

---

## Error Handling

### HTTP Status Codes
- **200:** Success
- **404:** Endpoint not found (try alternate endpoints)
- **401:** Unauthorized (check API key)
- **500:** Server error (retry or check dashboard)

### Error Response Format
```json
{
  "error": "Error message",
  "error_msg": "Detailed error description",
  "status": "failed"
}
```

---

## Timeout Configuration

### Request Timeouts
- **Initial Request:** 300 seconds (5 minutes)
- **Polling Requests:** 30 seconds per request
- **Total Max Wait:** 600 seconds (10 minutes)

---

## Custom Instructions

The application sends detailed extraction instructions with each request. These instructions include:
- Primary HTS identification rules
- Additional HTS nesting requirements
- Fees nesting structure
- Charge type placement
- Manifest information extraction
- Line item structure requirements

See `API1_CUSTOM_INSTRUCTIONS` in `app_v3.5.10.py` for full instructions.

---

## Testing Endpoints

### Manual Test (cURL)
```bash
# Start workflow
curl -X POST https://klearnow.prod.a79.ai/api/v1/public/workflow/run \
  -H "Authorization: Bearer sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "Unified PDF Parser",
    "agent_inputs": {
      "pdf_document": "<base64_pdf>",
      "custom_instructions": "Extract all line items"
    }
  }'

# Poll for results (replace {run_id} with actual run_id)
curl -X GET "https://klearnow.prod.a79.ai/api/v1/public/workflow/{run_id}/status?output_var=final_display_output" \
  -H "Authorization: Bearer sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i" \
  -H "Content-Type: application/json"
```

---

## Debug Endpoints (Application)

### Status Check
```
GET http://localhost:5002/api-status
```

Returns:
```json
{
  "api_key_configured": true,
  "workflow_id_configured": false,
  "agent_name": "Unified PDF Parser",
  "base_url": "https://klearnow.prod.a79.ai/api/v1/public/workflow/run"
}
```

### Fetch Results by Run ID
```
POST http://localhost:5002/fetch-results
```

Request Body:
```json
{
  "run_id": "run_xxxxxxxxxxxx"
}
```

---

## Notes

1. **Processing Time:** AI79 workflows process documents in 6-page chunks and can take several minutes for large PDFs.

2. **Dashboard Access:** If polling fails, results can always be retrieved manually from the AI79 dashboard.

3. **Output Format:** The `output` field structure varies. The application handles:
   - Direct JSON objects
   - JSON strings (parsed automatically)
   - Escaped JSON strings (unescaped and parsed)

4. **Line Items Structure:** The application expects line items in one of these formats:
   - `{"items": [...]}` (preferred)
   - `{"line_items": [...]}`
   - `{"entry_summary": {"line_items": [...]}}`

5. **Primary HTS Structure:** Line items should have `primary_hts` object containing:
   - `hts_code`
   - `entered_value`
   - `rate`
   - `duty_amount`
   - `additional_hts_codes[]` (nested array)
   - `fees` (nested object)

---

## Configuration File Location

All configuration is in:
```
app_v3.5.10.py
```

Key configuration variables:
- `API_KEY` (line 31)
- `API_BASE_URL` (line 32)
- `API1_AGENT_NAME` (line 38)
- `API1_WORKFLOW_ID` (line 39)
- `API1_CUSTOM_INSTRUCTIONS` (line 46+)

---

## Support

For issues or questions:
1. Check AI79 dashboard: https://klearnow.prod.a79.ai
2. Review application logs: `/tmp/cbp_debug.log`
3. Check server console output for detailed API call information


