#!/usr/bin/env python3
"""
Application Status Checker
Monitor the CBP 7501 application status
"""

import requests
import json
import time
from datetime import datetime

def check_application_status():
    """Check if the web application is running"""
    try:
        response = requests.get("http://localhost:5002", timeout=5)
        if response.status_code == 200:
            print("✅ Web Application: Running")
            return True
        else:
            print(f"⚠️  Web Application: HTTP {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ Web Application: Not running")
        return False
    except Exception as e:
        print(f"❌ Web Application: Error - {str(e)}")
        return False

def check_api_endpoints():
    """Check API endpoints"""
    endpoints = [
        ("/", "Home Page"),
        ("/upload", "File Upload"),
        ("/process-json", "JSON Processing"),
        ("/fetch-by-runid", "Run ID Fetch"),
    ]
    
    print("\n🔗 Checking API Endpoints:")
    for endpoint, description in endpoints:
        try:
            if endpoint == "/":
                response = requests.get(f"http://localhost:5002{endpoint}", timeout=5)
            else:
                # For POST endpoints, just check if they exist (return 405 for GET)
                response = requests.get(f"http://localhost:5002{endpoint}", timeout=5)
            
            if response.status_code in [200, 405]:  # 405 = Method Not Allowed (endpoint exists)
                print(f"   ✅ {description}: Available")
            else:
                print(f"   ⚠️  {description}: HTTP {response.status_code}")
        except Exception as e:
            print(f"   ❌ {description}: Error - {str(e)}")

def check_directories():
    """Check if required directories exist"""
    import os
    
    directories = [
        ("/tmp/cbp_uploads", "Upload Directory"),
        ("/tmp/cbp_outputs", "Output Directory"),
    ]
    
    print("\n📁 Checking Directories:")
    for directory, description in directories:
        if os.path.exists(directory):
            print(f"   ✅ {description}: {directory}")
        else:
            print(f"   ❌ {description}: {directory} (Missing)")

def show_usage_instructions():
    """Show usage instructions"""
    print("\n" + "="*80)
    print("📖 USAGE INSTRUCTIONS")
    print("="*80)
    print("\n🌐 Web Interface:")
    print("   1. Open your browser: http://localhost:5002")
    print("   2. Drag & drop a CBP 7501 PDF file")
    print("   3. Click 'Extract & Generate Excel (80 Columns)'")
    print("   4. Download the generated Excel file")
    
    print("\n📄 Testing Options:")
    print("   • Upload PDF: Use the web interface")
    print("   • Upload JSON: Use 'Upload AI79 JSON' button")
    print("   • Fetch by Run ID: Enter run_id from console logs")
    
    print("\n🔧 API Endpoints:")
    print("   • POST /upload - Upload PDF files")
    print("   • POST /process-json - Upload JSON files")
    print("   • POST /fetch-by-runid - Fetch results by run_id")
    print("   • POST /process-json-data - Process JSON data directly")
    
    print("\n📊 Features Available:")
    print("   • 80-column Excel export")
    print("   • Complete CBP 7501 field mapping")
    print("   • Invoice header filtering")
    print("   • HTS code expansion")
    print("   • Duty and fee calculations")
    print("   • Data validation")

def main():
    """Main status check"""
    print("="*80)
    print("🔍 CBP 7501 Application Status Check")
    print("="*80)
    print(f"⏰ Check Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Check application
    app_running = check_application_status()
    
    if app_running:
        # Check endpoints
        check_api_endpoints()
        
        # Check directories
        check_directories()
        
        # Show usage
        show_usage_instructions()
        
        print("\n✅ Application is ready for testing!")
    else:
        print("\n❌ Application is not running. Please start it first:")
        print("   cd '/Users/jasonmeasures/Library/CloudStorage/OneDrive-KlearNow/VS Scripts/Clear Audit 7501'")
        print("   source venv/bin/activate")
        print("   python app_v3.5.10.py")
    
    print("\n" + "="*80)

if __name__ == "__main__":
    main()
