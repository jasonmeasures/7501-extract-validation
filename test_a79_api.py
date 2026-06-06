#!/usr/bin/env python3
"""
A79 API Test Script
Test the A79 API endpoints and functionality
"""

import requests
import json
import base64
import os
from datetime import datetime

# API Configuration
API_KEY = "sk-a79-wvymMMk2FdgHPGBP9mGakuGLnc/FZg3i"
API_BASE_URL = "https://klearnow.prod.a79.ai/api/v1/public/workflow/run"
AGENT_NAME = "Enhanced PDF to JSON Extraction"

def test_api_connection():
    """Test basic API connectivity"""
    print("🔍 Testing A79 API Connection...")
    
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json',
        'Accept': '*/*'
    }
    
    # Test with a simple request
    test_payload = {
        "agent_name": AGENT_NAME,
        "agent_inputs": {
            "pdf_document": "dGVzdA==",  # base64 for "test"
            "custom_instructions": "Test connection"
        }
    }
    
    try:
        response = requests.post(
            API_BASE_URL,
            json=test_payload,
            headers=headers,
            timeout=30
        )
        
        print(f"   Status Code: {response.status_code}")
        print(f"   Response Headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            print("   ✅ API Connection Successful!")
            return True
        else:
            print(f"   ❌ API Error: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"   ❌ Connection Error: {str(e)}")
        return False

def test_with_sample_pdf():
    """Test API with a sample PDF file"""
    print("\n📄 Testing with Sample PDF...")
    
    # Create a simple test PDF content (you can replace this with an actual PDF)
    sample_pdf_path = "/tmp/test_sample.pdf"
    
    # For testing, we'll create a minimal PDF-like file
    # In real usage, you would upload an actual CBP 7501 PDF
    test_content = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n/Contents 4 0 R\n>>\nendobj\n4 0 obj\n<<\n/Length 44\n>>\nstream\nBT\n/F1 12 Tf\n72 720 Td\n(Test CBP 7501 Document) Tj\nET\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000204 00000 n \ntrailer\n<<\n/Size 5\n/Root 1 0 R\n>>\nstartxref\n297\n%%EOF"
    
    with open(sample_pdf_path, 'wb') as f:
        f.write(test_content)
    
    try:
        # Read and encode PDF
        with open(sample_pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
        print(f"   📄 PDF Size: {len(pdf_bytes)} bytes")
        
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
        
        print("   🚀 Sending request to A79 API...")
        response = requests.post(
            API_BASE_URL,
            json=payload,
            headers=headers,
            timeout=300
        )
        
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("   ✅ API Response Received!")
            print(f"   📋 Response Keys: {list(data.keys())}")
            
            # Save response for inspection
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            response_file = f"/tmp/a79_test_response_{timestamp}.json"
            with open(response_file, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"   💾 Response saved to: {response_file}")
            
            return data
        else:
            print(f"   ❌ API Error: {response.text[:500]}")
            return None
            
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
        return None
    finally:
        # Clean up test file
        if os.path.exists(sample_pdf_path):
            os.remove(sample_pdf_path)

def test_workflow_endpoints():
    """Test different workflow endpoints"""
    print("\n🔗 Testing Workflow Endpoints...")
    
    base_url = "https://klearnow.prod.a79.ai/api/v1"
    endpoints = [
        f"{base_url}/public/workflow/run",
        f"{base_url}/public/workflow",
        f"{base_url}/workflow",
    ]
    
    headers = {'Authorization': f'Bearer {API_KEY}'}
    
    for endpoint in endpoints:
        try:
            print(f"   Testing: {endpoint}")
            response = requests.get(endpoint, headers=headers, timeout=10)
            print(f"      Status: {response.status_code}")
            if response.status_code == 200:
                print(f"      ✅ Endpoint accessible")
            else:
                print(f"      ⚠️  Response: {response.text[:100]}")
        except Exception as e:
            print(f"      ❌ Error: {str(e)}")

def main():
    """Run all tests"""
    print("="*80)
    print("🧪 A79 API Test Suite")
    print("="*80)
    
    # Test 1: Basic connection
    connection_ok = test_api_connection()
    
    if connection_ok:
        # Test 2: Workflow endpoints
        test_workflow_endpoints()
        
        # Test 3: Sample PDF processing
        result = test_with_sample_pdf()
        
        if result:
            print("\n✅ All tests completed successfully!")
            print("\n🌐 Your web application is running at: http://localhost:5002")
            print("📄 You can now upload CBP 7501 PDFs through the web interface")
        else:
            print("\n⚠️  Some tests failed, but basic connection works")
            print("🌐 Your web application is still running at: http://localhost:5002")
    else:
        print("\n❌ API connection failed. Please check your API key and network connection.")
    
    print("\n" + "="*80)

if __name__ == "__main__":
    main()
