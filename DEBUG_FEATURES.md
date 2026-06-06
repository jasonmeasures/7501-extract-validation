# 🔍 CBP 7501 Debug Features

## Overview
Comprehensive debug logging and monitoring system for the CBP 7501 application with real-time A79 API testing capabilities.

## 🚀 Debug Features Added

### 1. **Enhanced Logging System**
- **File Logging**: All debug info saved to `/tmp/cbp_debug.log`
- **Console Logging**: Real-time debug output in terminal
- **Structured Logging**: Timestamp, log level, and detailed messages
- **API Request Tracking**: Complete request/response logging
- **Error Tracking**: Full stack traces and error context

### 2. **Debug Endpoints**
- `GET /debug/status` - Application status and metrics
- `GET /debug/logs` - View debug logs in real-time
- `GET /debug/dashboard` - Interactive debug dashboard
- `POST /debug/clear` - Clear debug logs
- `POST /debug/restart` - Restart application

### 3. **Real-Time Monitoring**
- **Memory Usage**: Track application memory consumption
- **File System**: Monitor upload/output directories
- **API Configuration**: Check API keys and workflow IDs
- **Process Information**: PID and system metrics

### 4. **A79 API Debug Information**
- **Request Details**: Full API payload logging
- **Response Analysis**: Complete response structure logging
- **Timing Information**: Request duration tracking
- **Polling Status**: Real-time polling progress
- **Error Handling**: Detailed error messages and recovery

## 🛠️ How to Use Debug Features

### **Method 1: Debug Dashboard (Recommended)**
```bash
# Open debug dashboard in browser
open http://localhost:5001/debug/dashboard
```
- Real-time status monitoring
- Live log viewing
- Interactive controls
- Auto-refresh capability

### **Method 2: Terminal Monitoring**
```bash
# Run the debug monitor script
python monitor_debug.py
```
- Real-time log streaming
- Status checks
- Interactive menu

### **Method 3: Direct API Access**
```bash
# Check application status
curl http://localhost:5001/debug/status

# View logs
curl http://localhost:5001/debug/logs
```

## 📊 Debug Information Available

### **API Request Debugging**
- Request URL and method
- Headers and authentication
- Payload size and structure
- Response status and timing
- Error messages and stack traces

### **A79 API Specific Debugging**
- PDF file size and encoding
- Custom instructions length
- Workflow ID and agent name
- Polling attempts and intervals
- Response parsing and validation

### **Application Status**
- Process ID and memory usage
- File system status
- API configuration status
- Upload/output file counts
- Error rates and success metrics

## 🔧 Debug Log Levels

- **DEBUG**: Detailed technical information
- **INFO**: General application flow
- **WARNING**: Potential issues
- **ERROR**: Error conditions with stack traces

## 📋 Example Debug Output

```
2025-10-23 06:15:30,123 - INFO - 🚀 Starting API call for entire document
2025-10-23 06:15:30,124 - DEBUG - API URL: https://klearnow.prod.a79.ai/api/v1/public/workflow/run
2025-10-23 06:15:30,125 - DEBUG - Agent Name: Enhanced PDF to JSON Extraction
2025-10-23 06:15:30,126 - DEBUG - PDF Size: 245760 characters (base64)
2025-10-23 06:15:30,127 - DEBUG - Instructions Length: 456 characters
2025-10-23 06:15:30,128 - INFO - Sending POST request to https://klearnow.prod.a79.ai/api/v1/public/workflow/run
2025-10-23 06:15:32,456 - INFO - Request completed in 2.33 seconds
2025-10-23 06:15:32,457 - DEBUG - Response status: 200
2025-10-23 06:15:32,458 - INFO - Successfully parsed JSON response
2025-10-23 06:15:32,459 - DEBUG - Response data keys: ['run_id', 'status']
```

## 🎯 Testing A79 API

### **Step 1: Upload a PDF**
1. Go to http://localhost:5001
2. Upload a CBP 7501 PDF
3. Watch terminal for detailed debug output

### **Step 2: Monitor Progress**
1. Open http://localhost:5001/debug/dashboard
2. Enable auto-refresh
3. Watch real-time API calls and responses

### **Step 3: Analyze Results**
1. Check debug logs for any errors
2. Verify API response structure
3. Monitor polling progress
4. Review final Excel generation

## 🚨 Troubleshooting

### **Common Issues and Debug Solutions**

1. **API Connection Issues**
   - Check debug logs for authentication errors
   - Verify API key configuration
   - Monitor network request timing

2. **Polling Timeouts**
   - Watch polling attempts in debug logs
   - Check for 404 errors on polling URLs
   - Verify workflow ID configuration

3. **PDF Processing Errors**
   - Check PDF file size and format
   - Monitor base64 encoding process
   - Verify file upload success

4. **Excel Generation Issues**
   - Check data normalization logs
   - Verify field mapping success
   - Monitor validation reports

## 📁 Debug Files Created

- `/tmp/cbp_debug.log` - Main debug log file
- `debug_dashboard.html` - Interactive debug interface
- `monitor_debug.py` - Terminal monitoring script
- `test_a79_api.py` - API testing script
- `check_status.py` - Status checking script

## 🔄 Real-Time Monitoring

The debug system provides real-time monitoring of:
- A79 API requests and responses
- PDF processing progress
- Data normalization steps
- Excel generation process
- Error conditions and recovery
- System resource usage

## 🎉 Ready for Testing!

Your CBP 7501 application now has comprehensive debug capabilities:

1. **Main Application**: http://localhost:5001
2. **Debug Dashboard**: http://localhost:5001/debug/dashboard
3. **API Status**: http://localhost:5001/debug/status
4. **Debug Logs**: http://localhost:5001/debug/logs

All A79 API interactions are now fully logged and monitored in real-time! 🚀




