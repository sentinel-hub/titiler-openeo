import requests
from PIL import Image
from io import BytesIO
import numpy as np
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, Dict

# using the following url https://openeo.ds.io/services/xyz/94937ec8-6669-4dba-a4df-52e217a02ea9/tiles/7/60/45
# create a mosaic of the tiles for making a map of europe

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
spatial_extent_north = 72.0
spatial_extent_south = 30.0

def lat_lon_to_tile(lat, lon, zoom):
    """Convert latitude, longitude to Web Mercator tile coordinates"""
    lat_rad = np.radians(lat)
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - np.log(np.tan(lat_rad) + (1 / np.cos(lat_rad))) / np.pi) / 2.0 * n)
    return x, y

def get_tile_numbers(zoom, spatial_extent_east, spatial_extent_west, spatial_extent_north, spatial_extent_south):
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
tile_numbers = get_tile_numbers(zoom, spatial_extent_east, spatial_extent_west, spatial_extent_north, spatial_extent_south)
# Calculate the grid dimensions
min_x = min(x for x, _ in tile_numbers)
max_x = max(x for x, _ in tile_numbers)
min_y = min(y for _, y in tile_numbers)
max_y = max(y for _, y in tile_numbers)
grid_width = max_x - min_x + 1
grid_height = max_y - min_y + 1

print(f"Grid dimensions: {grid_width} x {grid_height}")
print(f"Tile numbers: {tile_numbers}")

def download_tile(tile_number: Tuple[int, int], zoom: int, output_dir: str) -> Tuple[Tuple[int, int], Image.Image]:
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
    tile_url = f"https://openeo.ds.io/services/xyz/c0f8f010-f939-440c-9f40-36830959bf91/tiles/{zoom}/{x}/{y}"
    
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
print(f"Processing tiles for zoom level {zoom} with {max_workers} concurrent downloads...")

with ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_tile = {
        executor.submit(download_tile, tile_number, zoom, output_dir): tile_number
        for tile_number in tile_numbers
    }
    
    for future in as_completed(future_to_tile):
        coords, img = future.result()
        if img is not None:
            tiles[coords] = img

# Get tile dimensions (assuming all tiles are the same size)
tile_width, tile_height = next(iter(tiles.values())).size

# Create the final image
final_width = grid_width * tile_width
final_height = grid_height * tile_height
final_image = Image.new('RGB', (final_width, final_height))

# Stitch tiles together
print("Stitching tiles together...")
for (x, y), img in tiles.items():
    # Calculate position in the final image
    paste_x = (x - min_x) * tile_width
    paste_y = (y - min_y) * tile_height
    final_image.paste(img, (paste_x, paste_y))

# Save the final stitched image
final_image.save("europe_map.png")
print("Europe map has been created as 'europe_map.png'")
