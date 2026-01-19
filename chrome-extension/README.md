# Loan Origination to Borrower Intelligence Chrome Extension

This Chrome extension extracts loan application data from the mock loan origination page and opens the Borrower Intelligence Platform with pre-filled fields.

## Setup Instructions

### 1. Prepare Extension Icons

Before loading the extension, you need to add icon files:

1. Navigate to the `icons/` directory
2. Create or add three PNG icon files:
   - `icon16.png` (16x16 pixels)
   - `icon48.png` (48x48 pixels)
   - `icon128.png` (128x128 pixels)

You can use any image editor or online icon generator (see `icons/README.md` for suggestions).

**Quick test option:** Create simple colored square PNGs or temporarily copy any PNG files and rename them to the required filenames.

### 2. Load Extension in Chrome

1. Open Chrome and navigate to `chrome://extensions/`
2. Enable **Developer mode** (toggle in top-right corner)
3. Click **Load unpacked**
4. Select the `chrome-extension` directory (this folder)
5. The extension should now appear in your extensions list

### 3. Verify Extension is Loaded

- You should see the extension icon in your Chrome toolbar
- Check that there are no errors in the extension details page
- The extension name should be "Loan Origination to Borrower Intelligence"

## Usage

### Step 1: Open the Mock Loan Origination Page

1. Open the `mock-loan-system/index.html` file in your browser
   - You can serve it locally using a simple HTTP server, or
   - Open it directly using `file://` protocol

**To serve locally:**
```bash
cd mock-loan-system
# Using Python 3
python3 -m http.server 8080
# Then open http://localhost:8080/index.html
```

### Step 2: Fill Out the Loan Application Form

Enter the following information:
- **Full Name** (required)
- **Email Address** (required)
- **City** (required)
- **Company Name** (optional)

### Step 3: Click the Extension Icon

1. Click the extension icon in your Chrome toolbar
2. The extension will:
   - Extract the form data from the current page
   - Open a new tab with the Borrower Intelligence Platform
   - Pre-populate the form fields with the extracted data

### Step 4: Verify Data Population

- The new tab should open at `https://bounceback-demo.web.app/index.html`
- The form fields should be pre-filled with:
  - Full Name
  - Email
  - City
  - Company Name
- The URL parameters will be automatically cleaned after population

## Troubleshooting

### Extension icon is grayed out or not working

- Make sure you're on a page that matches the content script pattern
- For local testing, the extension is configured to work with:
  - `http://localhost/*/mock-loan-system/*`
  - `file:///*/mock-loan-system/*`

### "No form data found" error

- Ensure the mock loan origination page is loaded
- Verify that you've filled out at least one field (name, email, city, or company)
- Check the browser console for detailed error messages

### Fields not populating in Borrower Intelligence Platform

- Verify the front-end code has been deployed to the live site
- The `populateFromURLParams()` function must be present in `app.js`
- Check browser console for JavaScript errors
- Ensure you're accessing the live URL: `https://bounceback-demo.web.app`

### Extension shows errors when loading

- Verify all icon files exist in the `icons/` directory
- Check that `manifest.json` is valid JSON
- Review Chrome's extension error console for specific issues

## Technical Details

### Manifest V3

This extension uses Chrome's Manifest V3 specification:
- Service worker (`background.js`) instead of background page
- Content scripts for page interaction
- Declarative permissions

### Permissions Used

- `activeTab` - Access to the active tab when extension is clicked
- `tabs` - Create new tabs
- `scripting` - Inject scripts to extract form data
- `notifications` - Show error notifications

### Data Flow

1. User clicks extension icon
2. Background script injects data extraction function
3. Form fields are extracted from the page
4. URL is constructed with query parameters
5. New tab opens with Borrower Intelligence Platform
6. Front-end reads URL parameters and populates form

### URL Format

The extension creates URLs in this format:
```
https://bounceback-demo.web.app/index.html?fullName=John+Doe&email=john@example.com&city=Toronto&companyName=Acme+Corp
```

## Development Notes

- The extension is configured to work with the live Borrower Intelligence Platform
- For local testing, modify `BORROWER_INTELLIGENCE_URL` in `background.js`
- Content script matches patterns for localhost and file:// protocols
- All form field extraction uses multiple selector strategies for reliability

## Files

- `manifest.json` - Extension configuration
- `background.js` - Service worker handling extension clicks
- `content.js` - Content script for data extraction (currently not used, but available)
- `icons/` - Extension icons directory



