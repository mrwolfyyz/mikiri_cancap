# Extension Icons

This directory should contain the following icon files for the Chrome extension:

- `icon16.png` - 16x16 pixels (toolbar icon)
- `icon48.png` - 48x48 pixels (extension management page)
- `icon128.png` - 128x128 pixels (Chrome Web Store)

## Easy Method: Use the HTML Generator (Recommended)

**The easiest way to create icons:**

1. Open `create_simple_icons.html` in your web browser
2. Click the "Download" button below each icon (16x16, 48x48, 128x128)
3. Save each file with the exact names: `icon16.png`, `icon48.png`, `icon128.png`
4. Make sure all three files are saved in this `icons/` directory

The HTML file will generate professional-looking icons with a document and arrow symbol.

## Alternative: Python Script (If Pillow is installed)

If you have Python's Pillow library installed:

```bash
python3 generate_icons.py
```

This will automatically generate all three icon files.

## Manual Creation

You can also create icons using any image editor or online tool:

1. Create a simple icon design (suggested: document/page with arrow or similar loan/data transfer symbol)
2. Export as PNG files with the exact dimensions listed above
3. Place all three files in this directory

## Quick Placeholder Option

You can temporarily use any PNG images renamed to the required filenames, or use a simple colored square as a placeholder while testing.

## Online Icon Generators

- https://www.favicon-generator.org/
- https://realfavicongenerator.net/
- https://favicon.io/

For a professional look, consider using an icon representing:
- Document/data transfer
- Arrow or connection symbol
- Loan/money icon
- Simple "BI" (Borrower Intelligence) monogram

