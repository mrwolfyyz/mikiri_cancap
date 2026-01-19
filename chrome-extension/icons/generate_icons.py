#!/usr/bin/env python3
"""
Simple script to generate default extension icons.
Requires PIL/Pillow: pip install Pillow
"""

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("PIL/Pillow not found. Installing...")
    import subprocess
    import sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
    from PIL import Image, ImageDraw, ImageFont

def create_icon(size):
    """Create a simple icon with a document and arrow symbol"""
    # Create image with transparent background
    img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    
    # Background circle
    margin = size * 0.1
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(102, 126, 234, 255)  # Blue-purple gradient color
    )
    
    # Draw a simple document icon
    doc_width = size * 0.35
    doc_height = size * 0.45
    doc_x = size * 0.325
    doc_y = size * 0.275
    
    # Document rectangle
    draw.rectangle(
        [doc_x, doc_y, doc_x + doc_width, doc_y + doc_height],
        fill=(255, 255, 255, 255),
        outline=None
    )
    
    # Document corner fold
    fold_size = size * 0.08
    draw.polygon(
        [
            (doc_x, doc_y),
            (doc_x + fold_size, doc_y),
            (doc_x, doc_y + fold_size)
        ],
        fill=(230, 230, 230, 255)
    )
    
    # Arrow pointing right
    arrow_x = doc_x + doc_width + size * 0.05
    arrow_y = doc_y + doc_height / 2
    arrow_size = size * 0.15
    
    # Arrow line
    draw.line(
        [arrow_x, arrow_y, arrow_x + arrow_size * 0.6, arrow_y],
        fill=(255, 255, 255, 255),
        width=max(2, int(size * 0.03))
    )
    
    # Arrow head
    draw.polygon(
        [
            (arrow_x + arrow_size * 0.6, arrow_y),
            (arrow_x + arrow_size * 0.4, arrow_y - arrow_size * 0.3),
            (arrow_x + arrow_size * 0.4, arrow_y + arrow_size * 0.3)
        ],
        fill=(255, 255, 255, 255)
    )
    
    return img

# Generate all three icon sizes
sizes = [16, 48, 128]
for size in sizes:
    icon = create_icon(size)
    filename = f'icon{size}.png'
    icon.save(filename)
    print(f'Generated {filename} ({size}x{size})')

print('\nAll icons generated successfully!')



