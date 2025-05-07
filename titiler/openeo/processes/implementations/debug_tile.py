"""Debug tile process implementation."""

import numpy as np
import numpy.ma as ma
from PIL import Image, ImageDraw, ImageFont
from typing import Dict, Any
from rio_tiler.models import ImageData

def debug_tile(x: int, y: int, z: int, width: int = 256, height: int = 256) -> Dict[str, Any]:
    """Create a debug tile with X/Y/Z coordinates.

    Args:
        x (int): Tile X coordinate
        y (int): Tile Y coordinate
        z (int): Tile Z coordinate
        width (int, optional): Tile width. Defaults to 256.
        height (int, optional): Tile height. Defaults to 256.

    Returns:
        Dict[str, Any]: Dictionary containing the RGB bands with alpha mask
    """
    # Force parameters to integers
    x = int(x)
    y = int(y)
    z = int(z)
    width = int(width)
    height = int(height)
    
    # Create a transparent image with PIL to draw text
    img = Image.new('RGBA', (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    
    # Draw border (2 pixels)
    border_width = 2
    draw.rectangle([(0, 0), (width-1, height-1)], outline=(0, 0, 0, 255), width=border_width)
    
    # Draw text with coordinates
    text = f"X: {x}\nY: {y}\nZ: {z}"
    text_bbox = draw.textbbox((0, 0), text)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    
    # Center text position
    x_pos = (width - text_width) // 2
    y_pos = (height - text_height) // 2
    
    # Draw semi-transparent background for text
    padding = 10
    bg_bbox = (
        x_pos - padding,
        y_pos - padding,
        x_pos + text_width + padding,
        y_pos + text_height + padding
    )
    draw.rectangle(bg_bbox, fill=(255, 255, 255, 128))
    
    # Draw text in black
    draw.text((x_pos, y_pos), text, fill=(0, 0, 0, 255))
    
    # Convert to numpy array
    arr = np.array(img)
    
    # Get RGB bands and alpha
    rgb = arr[:, :, :3].astype('uint8')
    alpha = arr[:, :, 3]
    
    # Stack RGB bands
    data = np.stack([
        rgb[:, :, 0],  # R
        rgb[:, :, 1],  # G
        rgb[:, :, 2]   # B
    ])
    
    # Use alpha as mask (True where alpha is 0)
    mask = np.broadcast_to((alpha == 0)[np.newaxis, :, :], data.shape)
    
    # Create masked array
    masked_data = ma.array(data, mask=mask)
    
    # Create ImageData object
    image_data = ImageData(masked_data, band_names=["R", "G", "B"])
    
    return {"data": image_data}
