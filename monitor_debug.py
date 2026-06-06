#!/usr/bin/env python3
"""
Debug Monitor for CBP 7501 Application
Real-time monitoring of debug logs and application status
"""

import requests
import time
import os
from datetime import datetime

def monitor_logs():
    """Monitor debug logs in real-time"""
    print("🔍 Starting debug log monitoring...")
    print("Press Ctrl+C to stop\n")
    
    log_file = '/tmp/cbp_debug.log'
    last_size = 0
    
    try:
        while True:
            if os.path.exists(log_file):
                current_size = os.path.getsize(log_file)
                if current_size > last_size:
                    with open(log_file, 'r') as f:
                        f.seek(last_size)
                        new_content = f.read()
                        if new_content.strip():
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] {new_content.strip()}")
                    last_size = current_size
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for log file...")
            
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n🛑 Monitoring stopped")

def check_application_status():
    """Check application status via API"""
    try:
        response = requests.get("http://localhost:5002/debug/status", timeout=5)
        if response.status_code == 200:
            status = response.json()
            print("\n📊 Application Status:")
            print(f"   PID: {status['pid']}")
            print(f"   Memory: {status['memory_usage']:.1f} MB")
            print(f"   Upload folder: {'✅' if status['upload_folder_exists'] else '❌'}")
            print(f"   Output folder: {'✅' if status['output_folder_exists'] else '❌'}")
            print(f"   Upload files: {status['upload_files']}")
            print(f"   Output files: {status['output_files']}")
            print(f"   API key: {'✅' if status['api_key_configured'] else '❌'}")
            print(f"   Workflow ID: {'✅' if status['workflow_id_configured'] else '❌'}")
            return True
        else:
            print(f"❌ Status check failed: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Status check error: {e}")
        return False

def show_recent_logs(lines=50):
    """Show recent log entries"""
    log_file = '/tmp/cbp_debug.log'
    if not os.path.exists(log_file):
        print("No debug log file found")
        return
    
    print(f"\n📋 Recent {lines} log entries:")
    print("-" * 80)
    
    with open(log_file, 'r') as f:
        all_lines = f.readlines()
        recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        
        for line in recent_lines:
            print(line.rstrip())

def main():
    """Main monitoring interface"""
    print("="*80)
    print("🔍 CBP 7501 Debug Monitor")
    print("="*80)
    
    while True:
        print("\nOptions:")
        print("1. Monitor logs in real-time")
        print("2. Check application status")
        print("3. Show recent logs")
        print("4. Open web interface")
        print("5. Exit")
        
        choice = input("\nSelect option (1-5): ").strip()
        
        if choice == '1':
            monitor_logs()
        elif choice == '2':
            check_application_status()
        elif choice == '3':
            show_recent_logs()
        elif choice == '4':
            print("🌐 Opening web interface...")
            os.system("open http://localhost:5002")
        elif choice == '5':
            print("👋 Goodbye!")
            break
        else:
            print("❌ Invalid option")

if __name__ == "__main__":
    main()
