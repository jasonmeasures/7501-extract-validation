#!/bin/bash
# Start Flask app with debugging enabled

cd "/Users/jasonmeasures/Library/CloudStorage/OneDrive-KlearNow/VS Scripts/Clear Audit 7501"
source venv/bin/activate

echo "🚀 Starting Flask app with debugging..."
echo "📊 Debug dashboard will be available at: http://localhost:5002/debug/dashboard"
echo "📋 Debug logs: http://localhost:5002/debug/logs"
echo "📈 Debug status: http://localhost:5002/debug/status"
echo ""
echo "Press CTRL+C to stop"
echo ""

# Start the app
python app_v3.5.10.py




