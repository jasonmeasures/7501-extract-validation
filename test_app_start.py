#!/usr/bin/env python3
"""Test script to check if app can start"""
import sys
import traceback

print("Testing app startup...")
print("=" * 80)

try:
    # Test imports
    print("\n1. Testing imports...")
    from flask import Flask
    print("   ✅ Flask imported")
    
    import pandas as pd
    print("   ✅ Pandas imported")
    
    from PyPDF2 import PdfReader, PdfWriter
    print("   ✅ PyPDF2 imported")
    
    # Test app file
    print("\n2. Testing app file execution...")
    with open('app_v3.5.10.py', 'r') as f:
        code = f.read()
    
    # Create a namespace to execute in
    namespace = {
        '__name__': '__not_main__',  # Prevent app.run from executing
        '__file__': 'app_v3.5.10.py'
    }
    
    # Execute the code
    exec(compile(code, 'app_v3.5.10.py', 'exec'), namespace)
    
    print("   ✅ App code executed successfully")
    print(f"   ✅ App object created: {type(namespace.get('app', None))}")
    
    print("\n" + "=" * 80)
    print("✅ All tests passed! App should be able to start.")
    print("=" * 80)
    
except SyntaxError as e:
    print(f"\n❌ Syntax Error:")
    print(f"   File: {e.filename}")
    print(f"   Line {e.lineno}: {e.text}")
    print(f"   Error: {e.msg}")
    traceback.print_exc()
    sys.exit(1)
    
except ImportError as e:
    print(f"\n❌ Import Error: {e}")
    traceback.print_exc()
    sys.exit(1)
    
except Exception as e:
    print(f"\n❌ Error: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)




