# CBP Form 7501 Extract & Validation Tool

A Flask-based web application for extracting and validating data from CBP Form 7501 Entry Summary documents using AI79 API integration.

## Features

- **Complete 80-column Excel export** with all CS/CM/CD field mappings
- **Box 27 parsing**: Extracts SPI (Special Program Indicator) and CO (Country of Origin)
- **Header-level manifest quantity** extraction from shipment information
- **Invoice header filtering** and automatic invoice number extraction
- **MPF values** correctly placed in HTS US Rate column
- **Run ID Fetch** functionality for retrieving results when polling fails
- **Manual JSON upload** and processing support
- **Unified PDF Parser** via A79 API integration

## Requirements

- Python 3.13+
- Flask 3.0+
- pandas 2.0+
- PyPDF2 3.0+
- requests 2.31+
- psutil 5.9+
- openpyxl 3.1+

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/jasonmeasures/7501-extract-validation.git
cd 7501-extract-validation
```

### 2. Set Up Environment

Run the rebuild script to create the virtual environment and install dependencies:

```bash
chmod +x rebuild.sh
./rebuild.sh
```

Or manually:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure API Keys

Set the required API keys as environment variables:

```bash
export A79_API_KEY="your_a79_api_key_here"
export CLAUDE_API_KEY="your_claude_api_key_here"
```

Or add them to your shell profile (~/.zshrc or ~/.bash_profile):

```bash
echo 'export A79_API_KEY="your_a79_api_key_here"' >> ~/.zshrc
echo 'export CLAUDE_API_KEY="your_claude_api_key_here"' >> ~/.zshrc
source ~/.zshrc
```

### 4. Start the Server

```bash
./start_server.sh
```

Or manually:

```bash
source venv/bin/activate
python app_v3.5.10.py
```

The server will start on http://localhost:5002

## Usage

1. Open your browser to http://localhost:5002
2. Upload a CBP Form 7501 PDF document
3. The application will:
   - Extract data using the A79 API
   - Parse Box 27 for SPI and Country of Origin
   - Extract manifest quantity at the header level
   - Filter invoice headers automatically
   - Generate an Excel file with all extracted data
4. Download the resulting Excel file with complete field mappings

## Structure

```
├── app_v3.5.10.py              # Main Flask application
├── requirements.txt             # Python dependencies
├── rebuild.sh                   # Setup script
├── start_server.sh              # Server startup script
├── A79_API_SETUP.md            # API setup documentation
├── A79_API_STANDARD_TEMPLATE.md # API template
├── A79_API_STATUS.md           # API status tracking
├── DEBUG_FEATURES.md           # Debug features documentation
└── test_*.py                    # Test scripts
```

## Box 27 Parsing

The application extracts two key fields from Box 27:

- **SPI (Special Program Indicator)**: First character indicating free trade agreement type
  - "S" = USMCA/NAFTA
  - "E" = Other FTA
  
- **CO (Country of Origin)**: Two-letter country code
  - Example: "MX" (Mexico), "CN" (China), "CA" (Canada)

Example Box 27 format: `S O MX` → SPI: "S", CO: "MX"

## Manifest Quantity

The `manifest_qty` field is extracted **only at the header/shipment level** in the structure:
- `shipment_info.manifest_info.manifest_qty`
- Represents total packaging quantity for the entire shipment
- Common units: PCS (pieces), PAL (pallets), CTN (cartons), PKG (packages)

## Development

### Running Tests

```bash
python test_a79_api.py
python test_a79_comprehensive.py
```

### Debug Mode

The server runs in debug mode by default. For production, disable debug mode in `app_v3.5.10.py`:

```python
app.run(debug=False, host='0.0.0.0', port=5002)
```

## Troubleshooting

### Virtual Environment Issues

If you encounter issues with the virtual environment (especially on OneDrive-synced folders):

```bash
./rebuild.sh
```

This will recreate the virtual environment and reinstall all dependencies.

### Port Already in Use

If port 5002 is already in use:

```bash
lsof -ti:5002 | xargs kill -9
./start_server.sh
```

## License

Proprietary - KlearNow

## Support

For issues or questions, please contact the development team.

