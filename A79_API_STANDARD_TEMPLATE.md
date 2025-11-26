# AI79 API Standard Integration Template

Version: 1.0  
Last Updated: November 2024  
Purpose: Standardized AI79 API setup for reuse across Cursor projects

---

## Quick Start

1. Copy the configuration constants to your project
2. Copy the function templates you need
3. Update `AGENT_NAME` for your specific use case
4. Optionally set `WORKFLOW_ID` from AI79 dashboard

---

## 1. Configuration Constants

```python
# ============================================================================
# AI79 API Configuration - Standard Template
# ============================================================================

# Base Configuration (Shared across all projects)
API_KEY = "sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i"
API_BASE_URL = "https://klearnow.prod.a79.ai/api/v1/public/workflow"
DASHBOARD_URL = "https://klearnow.prod.a79.ai"

# Agent/Workflow Configuration (Project-specific)
AGENT_NAME = "Unified PDF Parser"  # ⚠️ CHANGE THIS per project
WORKFLOW_ID = None  # Optional: Set to "wf_xxxxxxxxxxxx" if available

# Timeout Configuration (Standard - don't change unless needed)
REQUEST_TIMEOUT = 300  # 5 minutes for initial request
POLL_TIMEOUT = 30  # 30 seconds per poll request
MAX_POLL_ATTEMPTS = 120  # 120 attempts
POLL_INTERVAL = 5  # 5 seconds between polls
MAX_TOTAL_WAIT = MAX_POLL_ATTEMPTS * POLL_INTERVAL  # 10 minutes
```

---

## 2. Core API Functions

### Function 1: Call AI79 API (Initial Request)

```python
import requests
import time
import json
import base64

def call_a79_api(pdf_path, custom_instructions, agent_name=None, workflow_id=None):
    """
    Standard AI79 API call function template
    
    Args:
        pdf_path: Path to PDF file
        custom_instructions: Extraction instructions string
        agent_name: Agent name (required if workflow_id is None)
        workflow_id: Workflow ID (optional, overrides agent_name)
    
    Returns:
        dict: API response with run_id and initial status
    """
    # Read and encode PDF
    with open(pdf_path, 'rb') as f:
        pdf_base64 = base64.b64encode(f.read()).decode('utf-8')
    
    # Determine endpoint
    if workflow_id:
        api_url = f"{API_BASE_URL}/{workflow_id}/run"
        payload = {
            "agent_inputs": {
                "pdf_document": pdf_base64,
                "custom_instructions": custom_instructions
            }
        }
    else:
        api_url = f"{API_BASE_URL}/run"
        payload = {
            "agent_name": agent_name or AGENT_NAME,
            "agent_inputs": {
                "pdf_document": pdf_base64,
                "custom_instructions": custom_instructions
            }
        }
    
    # Standard headers
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json',
        'Accept': '*/*'
    }
    
    # Make request
    print(f"📤 Calling AI79 API...")
    print(f"   Endpoint: {api_url}")
    print(f"   Agent: {agent_name or AGENT_NAME}")
    
    response = requests.post(
        api_url,
        data=json.dumps(payload),
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )
    
    if response.status_code != 200:
        raise Exception(f"API Error {response.status_code}: {response.text[:200]}")
    
    return response.json()
```

### Function 2: Poll for Results

```python
def poll_a79_results(run_id, workflow_id=None):
    """
    Standard polling function for AI79 workflow results
    
    Args:
        run_id: Run ID from initial API call
        workflow_id: Optional workflow ID for workflow-specific endpoints
    
    Returns:
        dict: Extracted output data
    """
    base_url = API_BASE_URL
    
    # Primary polling endpoint (most reliable)
    poll_url = f"{base_url}/{run_id}/status?output_var=final_display_output"
    
    # Alternate endpoints (fallback)
    alternate_urls = [
        f"{base_url}/{run_id}/status?output_var=final_display_output",
        f"{base_url}/run/{run_id}",
        f"{base_url}/{run_id}",
        f"{base_url}/run/{run_id}/status",
        f"https://klearnow.prod.a79.ai/api/v1/workflow/cards/{run_id}",
    ]
    
    # Add workflow-specific endpoints if workflow_id available
    if workflow_id:
        alternate_urls.extend([
            f"{base_url}/{workflow_id}/run/{run_id}",
            f"{base_url}/{workflow_id}/runs/{run_id}/status",
        ])
    
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    
    print(f"⏳ Polling for results (max {MAX_TOTAL_WAIT/60:.1f} minutes)...")
    
    # Polling loop
    for attempt in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL)
        elapsed_time = (attempt + 1) * POLL_INTERVAL
        elapsed_mins = elapsed_time / 60
        
        # Try primary endpoint first
        try:
            poll_response = requests.get(poll_url, headers=headers, timeout=POLL_TIMEOUT)
            
            # If 404 on first attempt, try alternates
            if poll_response.status_code == 404 and attempt == 0:
                print(f"   ⚠️  Primary endpoint not found, trying alternates...")
                for alt_url in alternate_urls:
                    alt_response = requests.get(alt_url, headers=headers, timeout=POLL_TIMEOUT)
                    if alt_response.status_code == 200:
                        poll_url = alt_url
                        poll_response = alt_response
                        print(f"   ✅ Found working endpoint: {alt_url}")
                        break
            
            if poll_response.status_code == 200:
                poll_data = poll_response.json()
                status = poll_data.get('status', 'unknown')
                
                if attempt % 12 == 0:  # Log every minute
                    print(f"   ⏱️  {elapsed_mins:.1f}min - Status: {status}")
                
                # Check if completed
                if status == 'completed':
                    output = poll_data.get('output')
                    if output:
                        # Handle string output (parse JSON)
                        if isinstance(output, str):
                            try:
                                output = json.loads(output)
                            except json.JSONDecodeError:
                                # Try unescaping if double-encoded
                                try:
                                    unescaped = output.encode().decode('unicode_escape')
                                    output = json.loads(unescaped)
                                except:
                                    pass
                        return output
                    else:
                        # Check if poll_data itself is the output
                        if isinstance(poll_data, dict) and len(poll_data) > 3:
                            return poll_data
                
                # Check for output even if status isn't "completed"
                if 'output' in poll_data and poll_data['output']:
                    output = poll_data['output']
                    if isinstance(output, str):
                        try:
                            output = json.loads(output)
                        except:
                            pass
                    # Check if output looks valid
                    if isinstance(output, (dict, list)) and len(output) > 0:
                        if isinstance(output, dict) and ('line_items' in output or 'items' in output or 'entry_summary' in output):
                            print(f"   ✅ Found valid output (status: {status})")
                            return output
                        elif isinstance(output, list) and len(output) > 0:
                            print(f"   ✅ Found valid output (status: {status})")
                            return output
                
                # Check for error status
                if status in ['failed', 'error']:
                    error_msg = poll_data.get('error_msg', poll_data.get('error', 'Unknown error'))
                    raise Exception(f"Workflow failed: {error_msg}")
                        
        except requests.exceptions.Timeout:
            print(f"   ⏱️  Poll timeout at {elapsed_mins:.1f}min, continuing...")
        except requests.exceptions.RequestException as e:
            if attempt < 3:  # Log first few errors
                print(f"   ⚠️  Poll attempt {attempt + 1} error: {e}")
    
    # Timeout
    raise Exception(
        f"Polling timed out after {MAX_TOTAL_WAIT} seconds. "
        f"Workflow may still be processing. Check dashboard: {DASHBOARD_URL} (run_id: {run_id})"
    )
```

### Function 3: Complete Workflow (All-in-One)

```python
def process_pdf_with_a79(pdf_path, custom_instructions, agent_name=None, workflow_id=None):
    """
    Complete workflow: Submit PDF and poll for results
    
    Args:
        pdf_path: Path to PDF file
        custom_instructions: Extraction instructions
        agent_name: Agent name (if not using workflow_id)
        workflow_id: Workflow ID (optional)
    
    Returns:
        dict: Extracted JSON data
    """
    # Step 1: Submit PDF
    print(f"📄 Processing PDF: {pdf_path}")
    initial_response = call_a79_api(pdf_path, custom_instructions, agent_name, workflow_id)
    
    run_id = initial_response.get('run_id')
    if not run_id:
        raise Exception("No run_id in response. Response: " + str(initial_response))
    
    workflow_id_from_response = initial_response.get('workflow_id', workflow_id)
    
    print(f"✅ Workflow started")
    print(f"   Run ID: {run_id}")
    print(f"   Workflow ID: {workflow_id_from_response or 'N/A'}")
    
    # Step 2: Poll for results
    output_data = poll_a79_results(run_id, workflow_id_from_response)
    
    print(f"✅ Results received")
    return output_data
```

### Function 4: Parse Output (Handle Various Formats)

```python
def parse_a79_output(output_data):
    """
    Standard output parsing - handles various response formats
    
    Args:
        output_data: Raw output from API (dict, list, or string)
    
    Returns:
        dict: Normalized structure with 'line_items' and 'header'
    """
    # Handle string output
    if isinstance(output_data, str):
        try:
            output_data = json.loads(output_data)
        except json.JSONDecodeError:
            # Try unescaping
            try:
                unescaped = output_data.encode().decode('unicode_escape')
                output_data = json.loads(unescaped)
            except:
                return {"error": "Could not parse output", "raw": output_data}
    
    # Handle list output (array of line items)
    if isinstance(output_data, list):
        return {
            "line_items": output_data,
            "header": {},
            "format": "list"
        }
    
    # Handle dict output
    if isinstance(output_data, dict):
        # Check for common structures
        if 'items' in output_data:
            return {
                "line_items": output_data['items'],
                "header": {k: v for k, v in output_data.items() if k != 'items'}
            }
        elif 'line_items' in output_data:
            return {
                "line_items": output_data['line_items'],
                "header": {k: v for k, v in output_data.items() if k != 'line_items'}
            }
        elif 'entry_summary' in output_data:
            entry = output_data['entry_summary']
            if isinstance(entry, dict):
                return {
                    "line_items": entry.get('line_items', []),
                    "header": {k: v for k, v in entry.items() if k != 'line_items'}
                }
            return output_data
        else:
            # Assume the whole dict is valid
            return {
                "line_items": [],
                "header": output_data
            }
    
    return {"error": "Unknown output format", "raw": output_data}
```

### Function 5: Error Handling

```python
def handle_a79_error(error, run_id=None):
    """
    Standard error handling for AI79 API
    
    Args:
        error: Exception or error message
        run_id: Optional run_id for dashboard link
    
    Returns:
        dict: Error information with helpful messages
    """
    error_msg = str(error)
    
    if "401" in error_msg or "Unauthorized" in error_msg:
        return {
            "error": "Authentication failed",
            "message": "Check API_KEY configuration",
            "fix": "Verify API_KEY matches your AI79 dashboard key",
            "dashboard": DASHBOARD_URL
        }
    elif "404" in error_msg:
        return {
            "error": "Endpoint not found",
            "message": "Check agent_name or workflow_id",
            "fix": "Verify agent name exists in dashboard or use workflow_id",
            "dashboard": DASHBOARD_URL
        }
    elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
        return {
            "error": "Request timeout",
            "message": f"Workflow may still be processing. Check dashboard.",
            "run_id": run_id,
            "fix": f"Go to {DASHBOARD_URL} and search for run_id: {run_id}",
            "dashboard": f"{DASHBOARD_URL}?run_id={run_id}" if run_id else DASHBOARD_URL
        }
    elif "500" in error_msg:
        return {
            "error": "Server error",
            "message": "AI79 server error",
            "fix": "Retry request or contact support",
            "dashboard": DASHBOARD_URL
        }
    else:
        return {
            "error": "API error",
            "message": error_msg,
            "fix": "Check error message and logs",
            "dashboard": DASHBOARD_URL
        }
```

---

## 3. Usage Examples

### Basic Usage

```python
# Simple PDF processing
try:
    result = process_pdf_with_a79(
        pdf_path="document.pdf",
        custom_instructions="Extract all line items with HTS codes",
        agent_name="Unified PDF Parser"
    )
    
    parsed = parse_a79_output(result)
    print(f"✅ Extracted {len(parsed['line_items'])} line items")
    
except Exception as e:
    error_info = handle_a79_error(e)
    print(f"❌ Error: {error_info['message']}")
    print(f"💡 Fix: {error_info['fix']}")
```

### With Workflow ID

```python
# Using workflow ID for better reliability
try:
    result = process_pdf_with_a79(
        pdf_path="document.pdf",
        custom_instructions="Extract form data",
        workflow_id="wf_xxxxxxxxxxxx"  # Get from AI79 dashboard
    )
    
    print(f"✅ Success: {result}")
    
except Exception as e:
    error_info = handle_a79_error(e, run_id=result.get('run_id'))
    print(f"❌ {error_info}")
```

### Flask/Web Application Integration

```python
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/process-pdf', methods=['POST'])
def process_pdf_endpoint():
    """Web endpoint for PDF processing"""
    try:
        # Get uploaded file
        pdf_file = request.files['pdf']
        instructions = request.form.get('instructions', 'Extract all data')
        
        # Save temporarily
        temp_path = f"/tmp/{pdf_file.filename}"
        pdf_file.save(temp_path)
        
        # Process with AI79
        result = process_pdf_with_a79(
            pdf_path=temp_path,
            custom_instructions=instructions,
            agent_name=AGENT_NAME
        )
        
        # Parse output
        parsed = parse_a79_output(result)
        
        return jsonify({
            "success": True,
            "data": parsed,
            "line_items_count": len(parsed.get('line_items', []))
        })
        
    except Exception as e:
        error_info = handle_a79_error(e)
        return jsonify({
            "success": False,
            "error": error_info
        }), 500
```

---

## 4. Endpoint Reference

### Submit Workflow (Initial Request)

**Agent-Based:**
```
POST https://klearnow.prod.a79.ai/api/v1/public/workflow/run
Headers:
  Authorization: Bearer {API_KEY}
  Content-Type: application/json
Body:
  {
    "agent_name": "Your Agent Name",
    "agent_inputs": {
      "pdf_document": "<base64>",
      "custom_instructions": "..."
    }
  }
```

**Workflow ID-Based:**
```
POST https://klearnow.prod.a79.ai/api/v1/public/workflow/{workflow_id}/run
Headers:
  Authorization: Bearer {API_KEY}
  Content-Type: application/json
Body:
  {
    "agent_inputs": {
      "pdf_document": "<base64>",
      "custom_instructions": "..."
    }
  }
```

### Poll for Results

**Primary Endpoint:**
```
GET https://klearnow.prod.a79.ai/api/v1/public/workflow/{run_id}/status?output_var=final_display_output
Headers:
  Authorization: Bearer {API_KEY}
  Content-Type: application/json
```

**Fallback Endpoints:**
- `/workflow/run/{run_id}`
- `/workflow/{run_id}`
- `/workflow/{workflow_id}/run/{run_id}`
- `/workflow/cards/{run_id}`

---

## 5. Response Format Reference

### Initial Response
```json
{
  "run_id": "run_xxxxxxxxxxxx",
  "workflow_id": "wf_xxxxxxxxxxxx",
  "status": "running",
  "message": "Workflow started"
}
```

### Polling Response (Completed)
```json
{
  "status": "completed",
  "run_id": "run_xxxxxxxxxxxx",
  "output": {
    "items": [...],
    "header_field": "value"
  }
}
```

### Error Response
```json
{
  "status": "failed",
  "error": "Error message",
  "error_msg": "Detailed description"
}
```

---

## 6. Common Patterns

### Pattern 1: Batch Processing Multiple PDFs

```python
def batch_process_pdfs(pdf_paths, custom_instructions):
    """Process multiple PDFs"""
    results = []
    
    for pdf_path in pdf_paths:
        try:
            print(f"\n{'='*60}")
            print(f"Processing: {pdf_path}")
            
            result = process_pdf_with_a79(pdf_path, custom_instructions)
            parsed = parse_a79_output(result)
            
            results.append({
                "file": pdf_path,
                "success": True,
                "data": parsed
            })
            
        except Exception as e:
            error_info = handle_a79_error(e)
            results.append({
                "file": pdf_path,
                "success": False,
                "error": error_info
            })
    
    return results
```

### Pattern 2: Save Results to File

```python
def process_and_save(pdf_path, output_path, custom_instructions):
    """Process PDF and save results to JSON"""
    result = process_pdf_with_a79(pdf_path, custom_instructions)
    parsed = parse_a79_output(result)
    
    with open(output_path, 'w') as f:
        json.dump(parsed, f, indent=2)
    
    print(f"✅ Results saved to: {output_path}")
    return parsed
```

### Pattern 3: Retry Logic

```python
def process_with_retry(pdf_path, custom_instructions, max_retries=3):
    """Process with automatic retry on failure"""
    for attempt in range(max_retries):
        try:
            result = process_pdf_with_a79(pdf_path, custom_instructions)
            return parse_a79_output(result)
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 30  # 30s, 60s, 90s
                print(f"⚠️  Attempt {attempt + 1} failed, retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise
```

---

## 7. Testing & Debugging

### Test Connection

```python
def test_a79_connection():
    """Test AI79 API connection"""
    try:
        headers = {
            'Authorization': f'Bearer {API_KEY}',
            'Content-Type': 'application/json'
        }
        response = requests.get(DASHBOARD_URL, headers=headers, timeout=10)
        print(f"✅ Connection successful (Status: {response.status_code})")
        return True
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False
```

### Debug Mode

```python
DEBUG_MODE = True  # Set to True for verbose logging

def debug_log(message):
    """Debug logging helper"""
    if DEBUG_MODE:
        print(f"🐛 DEBUG: {message}")

# Use in functions:
debug_log(f"Sending payload: {json.dumps(payload)[:200]}...")
debug_log(f"Response keys: {list(response.keys())}")
```

---

## 8. Quick Reference Card

```
┌─────────────────────────────────────────────────────────────┐
│ AI79 API Quick Reference                                    │
├─────────────────────────────────────────────────────────────┤
│ Base: https://klearnow.prod.a79.ai/api/v1/public/workflow  │
│ Key: sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i               │
│                                                             │
│ Submit: POST /run or /{workflow_id}/run                    │
│ Poll: GET /{run_id}/status?output_var=final_display_output │
│                                                             │
│ Headers: Authorization: Bearer {KEY}                        │
│          Content-Type: application/json                     │
│                                                             │
│ Payload: {"agent_name": "...", "agent_inputs": {...}}     │
│                                                             │
│ Timeouts: Initial=300s, Poll=30s/request, Max=600s        │
│                                                             │
│ Common Agents:                                              │
│ • "Unified PDF Parser" (general)                           │
│ • "Enhanced PDF to JSON Extraction" (legacy)               │
│                                                             │
│ Dashboard: https://klearnow.prod.a79.ai                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 9. Checklist for New Projects

- [ ] Copy configuration constants to new project
- [ ] Update `AGENT_NAME` to match your use case
- [ ] Optionally get and set `WORKFLOW_ID` from dashboard
- [ ] Copy required functions (call_a79_api, poll_a79_results, etc.)
- [ ] Write custom instructions for your extraction needs
- [ ] Test with a sample PDF
- [ ] Add error handling with handle_a79_error()
- [ ] Implement retry logic if needed
- [ ] Add logging for debugging
- [ ] Document agent name and workflow ID in project README

---

## 10. Version History

- **v1.0** - Initial template (November 2024)
  - Standard configuration
  - Core API functions
  - Polling with fallbacks
  - Error handling
  - Usage examples

---

## Support

- **Dashboard:** https://klearnow.prod.a79.ai
- **Documentation:** See dashboard for latest API docs
- **Issues:** Check run_id in dashboard for workflow status

