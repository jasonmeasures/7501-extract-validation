#!/usr/bin/env python3
"""
Debug startup script for CBP 7501 Flask application
This script will help identify why the app isn't starting
"""

import sys
import traceback
import os

print("=" * 80)
print("🔍 DEBUGGING FLASK APP STARTUP")
print("=" * 80)

# Step 1: Check Python version
print("\n1. Python Environment:")
print(f"   Python version: {sys.version}")
print(f"   Python executable: {sys.executable}")

# Step 2: Check imports
print("\n2. Testing Imports:")
try:
    from flask import Flask
    print("   ✅ Flask imported")
except ImportError as e:
    print(f"   ❌ Flask import failed: {e}")
    sys.exit(1)

try:
    import pandas as pd
    print("   ✅ Pandas imported")
except ImportError as e:
    print(f"   ❌ Pandas import failed: {e}")

try:
    from PyPDF2 import PdfReader, PdfWriter
    print("   ✅ PyPDF2 imported")
except ImportError as e:
    print(f"   ❌ PyPDF2 import failed: {e}")

try:
    import requests
    print("   ✅ Requests imported")
except ImportError as e:
    print(f"   ❌ Requests import failed: {e}")

try:
    import psutil
    print("   ✅ psutil imported")
except ImportError as e:
    print(f"   ⚠️  psutil import failed: {e} (optional)")

# Step 3: Check directories
print("\n3. Checking Directories:")
directories = ['/tmp/cbp_uploads', '/tmp/cbp_outputs', '/tmp']
for dir_path in directories:
    if os.path.exists(dir_path):
        writable = os.access(dir_path, os.W_OK)
        status = "✅" if writable else "⚠️  (not writable)"
        print(f"   {status} {dir_path}")
    else:
        print(f"   ❌ {dir_path} (does not exist)")
        try:
            os.makedirs(dir_path, exist_ok=True)
            print(f"   ✅ Created {dir_path}")
        except Exception as e:
            print(f"   ❌ Failed to create: {e}")

# Step 4: Check log file
print("\n4. Checking Log File:")
log_file = '/tmp/cbp_debug.log'
try:
    with open(log_file, 'a') as f:
        f.write('')
    print(f"   ✅ Log file writable: {log_file}")
except Exception as e:
    print(f"   ❌ Log file error: {e}")

# Step 5: Try to load the app
print("\n5. Loading Application Code:")
try:
    with open('app_v3.5.10.py', 'r') as f:
        code = f.read()
    print(f"   ✅ App file read ({len(code)} bytes)")
    
    # Try to compile
    compiled = compile(code, 'app_v3.5.10.py', 'exec')
    print("   ✅ Code compiled successfully")
    
    # Try to execute (but prevent app.run)
    namespace = {
        '__name__': '__not_main__',  # Prevent if __name__ == '__main__' from running
        '__file__': 'app_v3.5.10.py',
        'sys': sys,
        'os': os
    }
    
    print("   🔄 Executing app code...")
    exec(compiled, namespace)
    
    if 'app' in namespace:
        app = namespace['app']
        print(f"   ✅ Flask app object created: {type(app)}")
        print(f"   ✅ App name: {app.name}")
        
        # Check routes
        routes = [str(rule) for rule in app.url_map.iter_rules()]
        print(f"   ✅ Found {len(routes)} routes")
        print(f"   ✅ Debug routes available:")
        for route in routes:
            if 'debug' in route.lower():
                print(f"      - {route}")
    else:
        print("   ❌ App object not found in namespace")
        
except SyntaxError as e:
    print(f"   ❌ Syntax Error:")
    print(f"      File: {e.filename}")
    print(f"      Line {e.lineno}: {e.text}")
    print(f"      Error: {e.msg}")
    traceback.print_exc()
    sys.exit(1)
    
except Exception as e:
    print(f"   ❌ Error loading app: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

# Step 6: Check port availability
print("\n6. Checking Port 5002:")
try:
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', 5002))
    sock.close()
    if result == 0:
        print("   ⚠️  Port 5002 is already in use")
    else:
        print("   ✅ Port 5002 is available")
except Exception as e:
    print(f"   ⚠️  Could not check port: {e}")

print("\n" + "=" * 80)
print("✅ Debug check complete!")
print("=" * 80)
print("\nTo start the app, run:")
print("  python app_v3.5.10.py")
print("\nOr use the debug monitor:")
print("  python monitor_debug.py")
print("\nDebug dashboard will be available at:")
print("  http://localhost:5002/debug/dashboard")




