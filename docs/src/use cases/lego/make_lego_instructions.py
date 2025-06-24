import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Dict, Optional, Tuple

import numpy as np
import requests
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

DEFAULT_COVER = """
# LEGO Mosaic Instructions

## Project Details
- **Title**: LEGO Sentinel-2 Mosaic
- **Size**: {width} x {height} tiles
- **Total Tiles**: {total_tiles}

## Instructions
1. Each page contains up to 12 tile instructions
2. Tiles are ordered by X coordinate, then Y coordinate
3. Coordinates are shown on each tile
4. Follow the page numbers to build systematically

*Created with OpenEO by TiTiler*
"""


def create_cover_page(
    c, page_width, page_height, markdown_content: Optional[str] = None
):
    """Create a cover page from markdown content"""
    if markdown_content is None:
        markdown_content = DEFAULT_COVER

    # Set up styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle", parent=styles["Heading1"], fontSize=24, spaceAfter=30
    )
    h2_style = ParagraphStyle(
        "CustomH2", parent=styles["Heading2"], fontSize=18, spaceAfter=20
    )
    body_style = ParagraphStyle(
        "CustomBody", parent=styles["Normal"], fontSize=12, spaceAfter=12
    )
    list_style = ParagraphStyle(
        "CustomList",
        parent=styles["Normal"],
        fontSize=12,
        spaceAfter=12,
        leftIndent=20,
        bulletIndent=10,
    )

    # Start at top of page
    y = page_height - 100

    # Split content into lines and process each
    lines = markdown_content.strip().split("\n")
    current_style = body_style

    for line in lines:
        if not line.strip():
            continue

        if line.startswith("# "):
            text = line[2:]
            style = title_style
        elif line.startswith("## "):
            text = line[3:]
            style = h2_style
        elif line.startswith("- "):
            # Handle bold text in list items properly
            content = line[2:]
            text = "•  " + content.replace("**", "<b>", 1).replace("**", "</b>", 1)
            style = list_style
        elif line.startswith("*") and line.endswith("*"):
            text = "<i>" + line[1:-1] + "</i>"
            style = body_style
        elif (
            line.startswith("1. ")
            or line.startswith("2. ")
            or line.startswith("3. ")
            or line.startswith("4. ")
        ):
            # Handle numbered lists
            text = line
            style = list_style
        else:
            # Handle bold text in regular paragraphs
            text = line.replace("**", "<b>", 1).replace("**", "</b>", 1)
            style = body_style

        p = Paragraph(text, style)
        w, h = p.wrap(page_width - 100, page_height)
        p.drawOn(c, 50, y - h)
        y -= h + style.spaceAfter

    c.showPage()


# using the following url https://openeo.ds.io/services/xyz/94937ec8-6669-4dba-a4df-52e217a02ea9/tiles/7/60/45
# create instruction pages with 12 tiles per page

# Create output directory if it doesn't exist
output_dir = "europe_tiles"
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# Configuration parameters
zoom = 7
max_workers = 3  # Number of concurrent downloads (adjust as needed)

# European spatial
spatial_extent_east = 45.0
spatial_extent_west = -25.0
spatial_extent_north = 71.0
spatial_extent_south = 30.0


def lat_lon_to_tile(lat, lon, zoom):
    """Convert latitude, longitude to Web Mercator tile coordinates"""
    lat_rad = np.radians(lat)
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - np.log(np.tan(lat_rad) + (1 / np.cos(lat_rad))) / np.pi) / 2.0 * n)
    return x, y


def get_tile_numbers(
    zoom,
    spatial_extent_east,
    spatial_extent_west,
    spatial_extent_north,
    spatial_extent_south,
):
    """Get all tile numbers for a bounding box using Web Mercator projection"""
    # Get corner tiles
    nw_tile = lat_lon_to_tile(spatial_extent_north, spatial_extent_west, zoom)
    se_tile = lat_lon_to_tile(spatial_extent_south, spatial_extent_east, zoom)

    # Generate all tile coordinates within the bounding box
    tile_numbers = []
    for x in range(nw_tile[0], se_tile[0] + 1):
        for y in range(nw_tile[1], se_tile[1] + 1):
            tile_numbers.append((x, y))

    return tile_numbers


# Get the tile numbers
tile_numbers = get_tile_numbers(
    zoom,
    spatial_extent_east,
    spatial_extent_west,
    spatial_extent_north,
    spatial_extent_south,
)
# Calculate the grid dimensions
min_x = min(x for x, _ in tile_numbers)
max_x = max(x for x, _ in tile_numbers)
min_y = min(y for _, y in tile_numbers)
max_y = max(y for _, y in tile_numbers)
grid_width = max_x - min_x + 1
grid_height = max_y - min_y + 1

print(f"Grid dimensions: {grid_width} x {grid_height}")
print(f"Tile numbers: {tile_numbers}")


def download_tile(
    tile_number: Tuple[int, int], zoom: int, output_dir: str
) -> Tuple[Tuple[int, int], Image.Image]:
    """Download a single tile and return it with its coordinates"""
    x, y = tile_number
    tile_path = os.path.join(output_dir, f"tile_{x}_{y}.png")

    if os.path.exists(tile_path):
        print(f"Loading existing tile: {x}, {y}")
        try:
            img = Image.open(tile_path)
            return (x, y), img
        except Exception as e:
            print(f"Error loading existing tile {x}, {y}: {e}")
            # If loading fails, we'll try downloading it again

    print(f"Downloading tile: {x}, {y}")
    # tile_url = f"https://openeo.ds.io/services/xyz/5fe543d9-50e0-4851-a3f7-0a9d6a8bd756/tiles/{zoom}/{x}/{y}" # LEGO mosaic
    # tile_url = f"https://openeo.ds.io/services/xyz/b7c52ea4-9120-4300-bb86-84ebe6d2201f/tiles/{zoom}/{x}/{y}"  # RGB mosaic
    tile_url = f"https://openeo.ds.io/services/xyz/943856b7-eda9-447b-bbeb-f5e0f1c554ed/tiles/{zoom}/{x}/{y}"  # instructions

    try:
        response = requests.get(tile_url)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content))
        img.save(tile_path)
        return (x, y), img
    except Exception as e:
        print(f"Error downloading tile {x}, {y}: {e}")
        return (x, y), None


# Download and store tiles with concurrent processing
tiles: Dict[Tuple[int, int], Image.Image] = {}
print(
    f"Processing tiles for zoom level {zoom} with {max_workers} concurrent downloads..."
)

with ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_tile = {
        executor.submit(download_tile, tile_number, zoom, output_dir): tile_number
        for tile_number in tile_numbers
    }

    for future in as_completed(future_to_tile):
        coords, img = future.result()
        if img is not None:
            tiles[coords] = img

# Create PDF with 12 tiles per page
print("Creating PDF instructions...")
pdf_path = "tile_instructions.pdf"
c = canvas.Canvas(pdf_path, pagesize=A4)
c._pageNumber = 1  # Enable page numbers
c.setPageCompression(0)  # Disable compression for better quality
page_width, page_height = A4

# Sort tiles by X then Y coordinates
sorted_tiles = sorted(tiles.items(), key=lambda x: (x[0][0], x[0][1]))

# Create cover page
total_tiles = len(sorted_tiles)
cover_content = DEFAULT_COVER.format(
    width=grid_width, height=grid_height, total_tiles=total_tiles
)
create_cover_page(c, page_width, page_height, cover_content)


# Calculate tile size for 3x4 grid on A4 with spacing
margin = 40  # Outer margin
spacing = 20  # Space between tiles
grid_cols, grid_rows = 3, 4
tile_width = (page_width - 2 * margin - (grid_cols - 1) * spacing) / grid_cols
full_height = (page_height - 2 * margin - (grid_rows - 1) * spacing) / grid_rows
tile_height = full_height * 0.75  # Reduce height to 80%
vertical_offset = (full_height - tile_height) / 2  # Center vertically in cell


def draw_grid(canvas, margin, spacing, grid_cols, grid_rows, page_width, page_height):
    """Draw grid lines to separate tiles"""
    # Vertical lines
    for i in range(grid_cols - 1):
        x = margin + (i + 1) * (tile_width + spacing) - spacing / 2
        canvas.line(x, margin - 10, x, page_height - margin + 10)

    # Horizontal lines
    for i in range(grid_rows - 1):
        y = margin + (i + 1) * (full_height + spacing) - spacing / 2
        canvas.line(margin - 10, y, page_width - margin + 10, y)


# Process tiles in groups of 12
for i in range(0, len(sorted_tiles), 12):
    page_tiles = sorted_tiles[i : i + 12]

    for idx, ((x, y), img) in enumerate(page_tiles):
        # Calculate position in 3x4 grid
        grid_x = idx % grid_cols
        grid_y = grid_rows - 1 - (idx // grid_cols)  # Reverse Y to start from top

        # Calculate position on page with spacing
        pos_x = margin + grid_x * (tile_width + spacing)
        pos_y = (
            margin + grid_y * (full_height + spacing) + vertical_offset
        )  # Add vertical offset

        # Save tile temporarily as PNG with high quality
        temp_path = f"temp_tile_{x}_{y}.png"
        # Calculate a larger size for better quality
        scale_factor = 4  # Increase internal resolution
        resized_img = img.resize(
            (int(tile_width * scale_factor), int(tile_height * scale_factor)),
            Image.Resampling.LANCZOS,
        )
        resized_img.save(temp_path, "PNG", quality=95, optimize=True)

        # Draw tile and coordinates on PDF with high DPI
        c.drawImage(
            ImageReader(temp_path),
            pos_x,
            pos_y,
            tile_width,
            tile_height,
            mask="auto",
            preserveAspectRatio=True,
        )
        c.setFont("Helvetica", 10)

        # Clean up temporary file
        os.remove(temp_path)

    # Draw grid lines
    draw_grid(c, margin, spacing, grid_cols, grid_rows, page_width, page_height)

    # Add page number at bottom
    page_num = (i // 12) + 1
    total_pages = (len(sorted_tiles) + 11) // 12  # Round up division
    c.setFont("Helvetica", 12)
    c.drawString(page_width / 2 - 40, 30, f"Page {page_num} of {total_pages}")

    c.showPage()  # Start new page

c.save()
print(f"Instructions have been created as '{pdf_path}'")
