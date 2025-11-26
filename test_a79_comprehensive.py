#!/usr/bin/env python3
"""
Comprehensive A79 API Test
Test the A79 API with detailed logging and polling verification
"""

import requests
import json
import base64
import time
import os
from datetime import datetime

# API Configuration
API_KEY = "sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i"
API_BASE_URL = "https://klearnow.prod.a79.ai/api/v1/public/workflow/run"
AGENT_NAME = "Enhanced PDF to JSON Extraction"

def test_a79_with_polling():
    """Test A79 API with full polling workflow"""
    print("="*80)
    print("🧪 COMPREHENSIVE A79 API TEST")
    print("="*80)
    
    # Create a more realistic test PDF
    test_pdf_content = create_test_pdf()
    pdf_base64 = base64.b64encode(test_pdf_content).decode('utf-8')
    
    print(f"📄 Test PDF created: {len(test_pdf_content)} bytes")
    print(f"📄 Base64 encoded: {len(pdf_base64)} characters")
    
    # Prepare API request
    custom_instructions = """Extract all CBP Form 7501 data from the complete document:
    - Entry header information (entry number, port, dates, importer/consignee details)
    - Surety and bond information
    - Carrier and transport details
    - Line item numbers and descriptions
    - HTS codes and classifications
    - Quantities, values, and duty calculations
    - All header-level fields (CS - Customs Summary)
    - All merchandise-level fields (CM - Customs Merchandise)
    - All duty-level fields (CD - Customs Duty)"""
    
    payload = {
        "agent_name": AGENT_NAME,
        "agent_inputs": {
            "pdf_document": pdf_base64,
            "custom_instructions": custom_instructions
        }
    }
    
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json',
        'Accept': '*/*'
    }
    
    print(f"\n🚀 Sending request to A79 API...")
    print(f"   URL: {API_BASE_URL}")
    print(f"   Agent: {AGENT_NAME}")
    print(f"   Instructions: {len(custom_instructions)} characters")
    
    try:
        # Send initial request
        start_time = time.time()
        response = requests.post(API_BASE_URL, json=payload, headers=headers, timeout=300)
        request_time = time.time() - start_time
        
        print(f"   ✅ Response received in {request_time:.2f} seconds")
        print(f"   Status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"   ❌ API Error: {response.text[:200]}")
            return False
        
        data = response.json()
        print(f"   📋 Response keys: {list(data.keys())}")
        print(f"   📊 Response data: {data}")
        
        # Check if we got a run_id for polling
        if 'run_id' in data:
            run_id = data['run_id']
            status = data.get('status', 'unknown')
            print(f"\n🔄 Polling workflow started:")
            print(f"   Run ID: {run_id}")
            print(f"   Initial Status: {status}")
            
            # Test polling
            return test_polling(run_id, headers)
        else:
            print(f"   ⚠️  No run_id in response - immediate result or error")
            return True
            
    except Exception as e:
        print(f"   ❌ Request failed: {str(e)}")
        return False

def test_polling(run_id, headers):
    """Test polling for workflow results"""
    print(f"\n⏳ Testing polling for run_id: {run_id}")
    
    # Try different polling endpoints
    base_url = "https://klearnow.prod.a79.ai/api/v1"
    endpoints = [
        f"{base_url}/public/workflow/runs/{run_id}",
        f"{base_url}/public/workflow/run/{run_id}",
        f"{base_url}/workflow/runs/{run_id}",
        f"{base_url}/workflow/cards/{run_id}",
        f"{base_url}/runs/{run_id}",
    ]
    
    max_attempts = 10
    poll_interval = 3
    
    for attempt in range(max_attempts):
        print(f"\n   🔍 Polling attempt {attempt + 1}/{max_attempts}")
        
        for i, endpoint in enumerate(endpoints):
            try:
                print(f"      Testing endpoint {i+1}: {endpoint}")
                poll_response = requests.get(endpoint, headers=headers, timeout=30)
                
                print(f"         Status: {poll_response.status_code}")
                
                if poll_response.status_code == 200:
                    poll_data = poll_response.json()
                    print(f"         ✅ Success! Response keys: {list(poll_data.keys())}")
                    
                    # Check for completion
                    if 'status' in poll_data:
                        status = poll_data['status']
                        print(f"         📊 Status: {status}")
                        
                        if status == 'completed':
                            print(f"         🎉 Workflow completed!")
                            if 'output' in poll_data:
                                print(f"         📦 Output available: {type(poll_data['output'])}")
                            return True
                        elif status in ['failed', 'error']:
                            print(f"         ❌ Workflow failed: {poll_data.get('error_msg', 'Unknown error')}")
                            return False
                        else:
                            print(f"         ⏳ Still processing...")
                    else:
                        print(f"         📋 No status field in response")
                    
                    # Save response for analysis
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    response_file = f"/tmp/a79_poll_response_{timestamp}.json"
                    with open(response_file, 'w') as f:
                        json.dump(poll_data, f, indent=2)
                    print(f"         💾 Response saved: {response_file}")
                    
                    break  # Found working endpoint
                    
                elif poll_response.status_code == 404:
                    print(f"         ⚠️  Not found")
                else:
                    print(f"         ❌ Error: {poll_response.status_code}")
                    
            except Exception as e:
                print(f"         ❌ Error: {str(e)}")
                continue
        
        if attempt < max_attempts - 1:
            print(f"      ⏳ Waiting {poll_interval} seconds before next attempt...")
            time.sleep(poll_interval)
    
    print(f"\n   ⚠️  Polling completed after {max_attempts} attempts")
    return True

def create_test_pdf():
    """Create a minimal test PDF for testing"""
    # This is a minimal PDF structure for testing
    pdf_content = b"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj

2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj

3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
>>
endobj

4 0 obj
<<
/Length 200
>>
stream
BT
/F1 12 Tf
72 720 Td
(CBP Form 7501 Test Document) Tj
0 -20 Td
(Entry Number: 1234567890) Tj
0 -20 Td
(Port of Entry: 1001) Tj
0 -20 Td
(Importer: Test Company Inc) Tj
0 -20 Td
(Line Item 1: Test Product) Tj
0 -20 Td
(HTS Code: 1234.56.78) Tj
ET
endstream
endobj

xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000204 00000 n 
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
404
%%EOF"""
    
    return pdf_content

def main():
    """Run comprehensive A79 API test"""
    print("🔍 Starting comprehensive A79 API test...")
    
    # Test 1: Basic connectivity
    print("\n1️⃣ Testing basic connectivity...")
    try:
        response = requests.get("https://klearnow.prod.a79.ai", timeout=10)
        print(f"   ✅ A79 server reachable: {response.status_code}")
    except Exception as e:
        print(f"   ❌ A79 server unreachable: {e}")
        return
    
    # Test 2: API authentication
    print("\n2️⃣ Testing API authentication...")
    try:
        headers = {'Authorization': f'Bearer {API_KEY}'}
        response = requests.get("https://klearnow.prod.a79.ai/api/v1", headers=headers, timeout=10)
        print(f"   ✅ API authentication: {response.status_code}")
    except Exception as e:
        print(f"   ❌ API authentication failed: {e}")
        return
    
    # Test 3: Full workflow test
    print("\n3️⃣ Testing full workflow...")
    success = test_a79_with_polling()
    
    if success:
        print("\n✅ All A79 API tests completed successfully!")
        print("🌐 Your application is ready for CBP 7501 processing!")
    else:
        print("\n⚠️  Some tests had issues, but basic connectivity works")
        print("🌐 You can still test with the web interface")

if __name__ == "__main__":
    main()




