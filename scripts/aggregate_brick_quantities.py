#!/usr/bin/env python3
"""Aggregate LEGO brick quantities from Europe map tiles."""

import os
import csv
import numpy as np
import requests
from io import StringIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Tuple

@dataclass
class BrickInfo:
    """Container for brick information."""
    color: str
    pantone: str
    hex_color: str
    transparent: bool
    quantity: int = 0

def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    """Convert latitude, longitude to Web Mercator tile coordinates"""
    lat_rad = np.radians(lat)
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - np.log(np.tan(lat_rad) + (1 / np.cos(lat_rad))) / np.pi) / 2.0 * n)
    return x, y

def get_tile_numbers(zoom: int, spatial_extent_east: float, spatial_extent_west: float, 
                    spatial_extent_north: float, spatial_extent_south: float) -> List[Tuple[int, int]]:
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

def ensure_output_dir(base_dir: str = "brick_quantities") -> str:
    """Ensure output directory exists."""
    os.makedirs(base_dir, exist_ok=True)
    return base_dir

def get_tile_csv_path(output_dir: str, x: int, y: int, zoom: int) -> str:
    """Get the path for a tile's CSV file."""
    return os.path.join(output_dir, f"tile_{zoom}_{x}_{y}.csv")

def parse_brick_data(reader) -> Dict[str, BrickInfo]:
    """Parse brick data from a CSV reader."""
    brick_data = {}
    for row in reader:
        color = row['color']
        if color not in brick_data:
            brick_data[color] = BrickInfo(
                color=color,
                pantone=row['pantone'],
                hex_color=row['hex'],
                transparent=row['transparent'].lower() == 'true',
                quantity=int(row['quantity'])
            )
        else:
            brick_data[color].quantity += int(row['quantity'])
    return brick_data

def download_brick_quantities(tile_number: Tuple[int, int], zoom: int, output_dir: str) -> Dict[str, BrickInfo]:
    """Download brick quantities for a single tile and save to CSV."""
    x, y = tile_number
    csv_path = get_tile_csv_path(output_dir, x, y, zoom)
    
    # Skip if file already exists
    if os.path.exists(csv_path):
        print(f"Using existing file for tile: {x}, {y}")
        try:
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                return parse_brick_data(reader)
        except Exception as e:
            print(f"Error reading existing file for tile {x}, {y}: {e}")
            # If reading fails, try downloading again
    
    print(f"Downloading brick quantities for tile: {x}, {y}")
    tile_url = f"https://openeo.ds.io/services/xyz/923afabd-b697-4823-8200-afa96fc1cef1/tiles/{zoom}/{x}/{y}"
    
    try:
        response = requests.get(tile_url)
        response.raise_for_status()
        
        # Save the CSV content
        with open(csv_path, 'w') as f:
            f.write(response.text)
            
        # Parse the data
        csv_data = StringIO(response.text)
        reader = csv.DictReader(csv_data)
        return parse_brick_data(reader)
        
    except Exception as e:
        print(f"Error downloading quantities for tile {x}, {y}: {e}")
        if os.path.exists(csv_path):
            os.remove(csv_path)  # Remove failed download
        return {}

def aggregate_quantities(tile_numbers: List[Tuple[int, int]], zoom: int, max_workers: int, output_dir: str) -> Dict[str, BrickInfo]:
    """Download and aggregate brick quantities from all tiles."""
    total_quantities = {}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_tile = {
            executor.submit(download_brick_quantities, tile_number, zoom, output_dir): tile_number
            for tile_number in tile_numbers
        }
        
        for future in as_completed(future_to_tile):
            tile_number = future_to_tile[future]
            try:
                brick_data = future.result()
                for color, info in brick_data.items():
                    if color not in total_quantities:
                        total_quantities[color] = info
                    else:
                        total_quantities[color].quantity += info.quantity
            except Exception as e:
                print(f"Error processing tile {tile_number}: {e}")
    
    return total_quantities

def generate_summary(quantities: Dict[str, BrickInfo], output_file: str):
    """Generate a summary CSV with total brick quantities."""
    # Sort bricks by quantity (descending) and then by color name
    sorted_quantities = sorted(
        quantities.values(),
        key=lambda x: (-x.quantity, x.color)
    )
    
    # Calculate totals
    total_bricks = sum(info.quantity for info in quantities.values())
    regular_bricks = sum(info.quantity for info in quantities.values() if not info.transparent)
    transparent_bricks = sum(info.quantity for info in quantities.values() if info.transparent)
    
    # Write summary file
    with open(output_file, 'w') as f:
        # Write header
        f.write("LEGO Brick Quantity Summary\n")
        f.write(f"Total bricks needed: {total_bricks}\n")
        f.write(f"Regular bricks: {regular_bricks}\n")
        f.write(f"Transparent bricks: {transparent_bricks}\n")
        f.write("\nDetailed Quantities:\n")
        f.write("color,quantity,pantone,hex,transparent\n")
        
        # Write brick details
        for brick in sorted_quantities:
            f.write(f"{brick.color},{brick.quantity},{brick.pantone},{brick.hex_color},{brick.transparent}\n")

def main():
    """Main function to download and aggregate brick quantities."""
    print("Aggregating LEGO brick quantities for Europe map...")
    
    # Configuration parameters (same as make_europe_map.py)
    zoom = 7
    max_workers = 3
    
    # European spatial extent
    spatial_extent_east = 45.0
    spatial_extent_west = -25.0
    spatial_extent_north = 72.0
    spatial_extent_south = 30.0
    
    # Setup output directory
    output_dir = ensure_output_dir()
    print(f"Saving tile data to: {output_dir}")
    
    # Get tile numbers
    tile_numbers = get_tile_numbers(zoom, spatial_extent_east, spatial_extent_west, 
                                  spatial_extent_north, spatial_extent_south)
    
    print(f"Processing {len(tile_numbers)} tiles...")
    
    # Aggregate quantities from all tiles
    total_quantities = aggregate_quantities(tile_numbers, zoom, max_workers, output_dir)
    
    # Check for missing tiles
    expected_files = {get_tile_csv_path(output_dir, x, y, zoom) for x, y in tile_numbers}
    existing_files = {f for f in expected_files if os.path.exists(f)}
    missing_files = expected_files - existing_files
    
    # Generate summary
    output_file = os.path.join(output_dir, "brick_quantities_summary.csv")
    generate_summary(total_quantities, output_file)
    
    print(f"\nSummary saved to {output_file}")
    print(f"Total unique colors: {len(total_quantities)}")
    print(f"Total bricks needed: {sum(info.quantity for info in total_quantities.values())}")
    
    if missing_files:
        print(f"\nWarning: {len(missing_files)} tiles are missing:")
        for f in sorted(missing_files):
            print(f"  - {os.path.basename(f)}")
        print("\nRun the script again to retry downloading missing tiles.")

if __name__ == "__main__":
    main()
