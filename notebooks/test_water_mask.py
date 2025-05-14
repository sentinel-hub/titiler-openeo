"""Test script to verify water mask functionality.

This script tests the IMERG Land-Sea Mask NetCDF file which provides 
percent water surface coverage on a 0.1°x0.1° global grid (3600x1800).
- 100% = all water
- 0% = all land
- Values in between represent mixed pixels

Usage:
  python test_water_mask.py                           # Run with default test locations
  python test_water_mask.py 40.7128 -74.0060          # Check New York City
  python test_water_mask.py 51.5074 -0.1278 London    # Check with custom name
  python test_water_mask.py --skip-global 40.7 -74.0  # Skip global visualizations
  
Options:
  --skip-global  Skip global water mask visualizations (faster for single location checks)
"""
import numpy as np
import matplotlib.pyplot as plt
import xarray as xr
import os
import sys
import traceback
from pathlib import Path


def lat_to_index(lat, lat_start=-89.95, lat_res=0.1, nlat=1800):
    """Convert latitude to array index.
    
    Args:
        lat: Latitude in degrees (-90 to 90)
        lat_start: Starting latitude of the grid (-89.95 for this dataset)
        lat_res: Resolution in degrees (0.1 for this dataset)
        nlat: Number of latitude points (1800 for this dataset)
    
    Returns:
        Index in the array
    """
    # Calculate index (from south to north, -90 to 90)
    idx = int(round((lat - lat_start) / lat_res))
    # Ensure within bounds
    return max(0, min(idx, nlat - 1))


def lon_to_index(lon, lon_start=-0.05, lon_res=0.1, nlon=3600):
    """Convert longitude to array index.
    
    Args:
        lon: Longitude in degrees (-180 to 180)
        lon_start: Starting longitude of the grid (-0.05 for this dataset)
        lon_res: Resolution in degrees (0.1 for this dataset)
        nlon: Number of longitude points (3600 for this dataset)
    
    Returns:
        Index in the array
    """
    # First normalize longitude to 0-360 range
    lon_360 = lon % 360 if lon >= 0 else (lon + 360)
    # Calculate index
    idx = int(round((lon_360 - lon_start) / lon_res))
    # Handle edge case for longitude 359.95
    if idx >= nlon:
        idx = 0
    # Ensure within bounds
    return max(0, min(idx, nlon - 1))


def visualize_mask_region(mask, center_lat, center_lon, window_size=20, title=None):
    """Visualize a region of the water mask around specified coordinates.
    
    Args:
        mask: The water mask array
        center_lat: Center latitude in degrees (-90 to 90)
        center_lon: Center longitude in degrees (-180 to 180)
        window_size: Half-width of the region to display in grid cells
        title: Optional title for the plot
    
    Returns:
        The extracted region as a numpy array
    """
    # Convert lat/lon to array indices
    lat_idx = lat_to_index(center_lat)
    lon_idx = lon_to_index(center_lon)
    
    print(f"Debug - Coordinate conversion:")
    print(f"  Latitude: {center_lat}°N → index {lat_idx}")
    print(f"  Longitude: {center_lon}°E → index {lon_idx}")
    
    # Extract region with bounds checking
    lat_min = max(0, lat_idx - window_size)
    lat_max = min(mask.shape[0], lat_idx + window_size)
    lon_min = max(0, lon_idx - window_size)
    lon_max = min(mask.shape[1], lon_idx + window_size)
    
    region = mask[lat_min:lat_max, lon_min:lon_max].copy()
    
    print(f"Region shape: {region.shape}")
    print(f"Region statistics:")
    print(f"  Mean: {np.ma.mean(region):.2f}%")
    print(f"  Max: {np.ma.max(region):.2f}%")
    print(f"  Min: {np.ma.min(region):.2f}%")
    
    # Create visualization
    plt.figure(figsize=(10, 8))
    plt.imshow(region, cmap='Blues', origin='lower', vmin=0, vmax=100)
    plt.colorbar(label='Water Percentage')
    
    if title:
        plt.title(title)
    plt.grid(True, alpha=0.3)
    
    # Calculate lat/lon for region borders
    lat_min_deg = -89.95 + lat_min * 0.1
    lat_max_deg = -89.95 + (lat_max - 1) * 0.1
    lon_min_deg = (lon_min * 0.1 - 0.05) % 360
    lon_max_deg = (lon_max * 0.1 - 0.05) % 360
    
    # Convert longitude from 0-360 to -180-180 for display if needed
    if lon_min_deg > 180:
        lon_min_deg -= 360
    if lon_max_deg > 180:
        lon_max_deg -= 360
    
    # Add lat/lon ticks
    lats = np.linspace(lat_min_deg, lat_max_deg, 5)
    lons = np.linspace(lon_min_deg, lon_max_deg, 5)
    
    plt.yticks(
        np.linspace(0, region.shape[0] - 1, 5),
        [f"{lat:.1f}°" for lat in lats]
    )
    plt.xticks(
        np.linspace(0, region.shape[1] - 1, 5),
        [f"{lon:.1f}°" for lon in lons]
    )
    
    return region


def classify_water_mask(mask, threshold=50):
    """Create a binary classification of water vs land based on threshold.
    
    Args:
        mask: The water mask array
        threshold: Percentage threshold above which is considered water
    
    Returns:
        Binary mask (1=water, 0=land)
    """
    binary = np.zeros_like(mask)
    binary[mask > threshold] = 1
    return binary


print("Loading water mask from NetCDF...")
WATER_MASK_PATH = Path("notebooks/IMERG_land_sea_mask.nc.gz")

try:
    # Open and read the NetCDF file
    with xr.open_dataset(WATER_MASK_PATH) as ds:
        print("\nDataset info:")
        print(ds.info())
        
        # Examine coordinate systems in the NetCDF file
        print("\nCoordinate system details:")
        print("Longitude coordinates:")
        print(f"  Range: {ds.lon.values.min()} to {ds.lon.values.max()}")
        print(f"  First 5 values: {ds.lon.values[:5]}")
        print(f"  Last 5 values: {ds.lon.values[-5:]}")
        
        print("Latitude coordinates:")
        print(f"  Range: {ds.lat.values.min()} to {ds.lat.values.max()}")
        print(f"  First 5 values: {ds.lat.values[:5]}")
        print(f"  Last 5 values: {ds.lat.values[-5:]}")
        
        # Get the water mask variable (assuming it's the first variable)
        var_name = list(ds.data_vars)[0]
        data = ds[var_name].values
        
        print(f"\nWater mask shape: {data.shape}")
        print(f"Variable name: {var_name}")
        
        # Print some diagnostic info about known locations
        print("\nSampling known locations directly from NetCDF:")
        
        # Mediterranean Sea near Malta
        malta_lat_idx = lat_to_index(35.9)
        malta_lon_idx = lon_to_index(14.4)
        print(f"Malta (35.9°N, 14.4°E): {data[malta_lat_idx, malta_lon_idx]:.2f}%")
        
        # North Atlantic
        atlantic_lat_idx = lat_to_index(40.0)
        atlantic_lon_idx = lon_to_index(-30.0)
        print(f"North Atlantic (40°N, -30°E): {data[atlantic_lat_idx, atlantic_lon_idx]:.2f}%")
        
        # Sahara Desert
        sahara_lat_idx = lat_to_index(25.0)
        sahara_lon_idx = lon_to_index(25.0)
        print(f"Sahara Desert (25°N, 25°E): {data[sahara_lat_idx, sahara_lon_idx]:.2f}%")
        
        # Paris
        paris_lat_idx = lat_to_index(48.85)
        paris_lon_idx = lon_to_index(2.35)
        print(f"Paris (48.85°N, 2.35°E): {data[paris_lat_idx, paris_lon_idx]:.2f}%")
        
        # Manhattan
        manhattan_lat_idx = lat_to_index(40.78)
        manhattan_lon_idx = lon_to_index(-73.97)
        print(f"Manhattan (40.78°N, -73.97°E): {data[manhattan_lat_idx, manhattan_lon_idx]:.2f}%")
        
        # Clean and mask the data
        # Handle the data values:
        # - Ensure all values are in range 0-100
        # - Invalid values (NaN, >100) are masked
        _WATER_MASK = np.ma.masked_invalid(data)
        _WATER_MASK = np.ma.masked_outside(_WATER_MASK, 0, 100)
        
        print("\nAfter cleaning data:")
        print(f"Number of land pixels (0): {np.sum(_WATER_MASK == 0)}")
        print(f"Number of water pixels (>0): {np.sum(_WATER_MASK > 0)}")
        print(f"Number of masked pixels: {_WATER_MASK.mask.sum()}")
        
        valid_range = _WATER_MASK.compressed()  # Get non-masked values
        if len(valid_range) > 0:
            print(f"\nValid value statistics:")
            print(f"Min: {valid_range.min():.2f}%")
            print(f"Max: {valid_range.max():.2f}%")
            print(f"Mean: {valid_range.mean():.2f}%")
            print(f"Median: {np.median(valid_range):.2f}%")
        
    print(f"\nWater mask loaded with shape: {_WATER_MASK.shape}")
except Exception as e:
    print(f"Error loading water mask: {e}")
    traceback.print_exc()
    exit(1)

# Check for --skip-global flag
skip_global = False
args = sys.argv.copy()
if "--skip-global" in args:
    skip_global = True
    args.remove("--skip-global")
    print("Skipping global visualizations")

# Define test locations based on command-line arguments or use defaults
if len(args) >= 3:
    try:
        # Parse latitude and longitude from command-line arguments
        custom_lat = float(args[1])
        custom_lon = float(args[2])
        
        # Get location name if provided, otherwise use coordinates
        if len(args) >= 4:
            custom_name = args[3]
        else:
            custom_name = f"{custom_lat:.4f}N_{custom_lon:.4f}E"
            
        # Create test location
        test_locations = [
            {"name": custom_name, "lat": custom_lat, "lon": custom_lon, 
             "description": "Custom location from command line"}
        ]
        print(f"Testing custom location: {custom_name} at {custom_lat:.4f}°N, {custom_lon:.4f}°E")
    except ValueError:
        print("Error: Latitude and longitude must be valid numbers")
        print("Usage: python test_water_mask.py [--skip-global] [latitude] [longitude] [optional_name]")
        exit(1)
else:
    # Use default test locations
    test_locations = [
        {"name": "Malta", "lat": 35.9375, "lon": 14.3754, "description": "Mediterranean Sea (definitely water)"},
        {"name": "Manhattan", "lat": 40.7831, "lon": -73.9712, "description": "Island with surrounding water"},
        {"name": "Paris", "lat": 48.8566, "lon": 2.3522, "description": "Inland city with Seine river"}
    ]

# Iterate through test locations
for location in test_locations:
    center_lat = location["lat"]
    center_lon = location["lon"]
    name = location["name"]
    desc = location["description"]
    
    print(f"\n\n{'='*50}")
    print(f"Testing location: {name} ({desc})")
    print(f"Coordinates: {center_lat:.4f}°N, {center_lon:.4f}°E")
    print(f"{'='*50}")
    
    # Validate coordinates
    if not (-90 <= center_lat <= 90) or not (-180 <= center_lon <= 180):
        print(f"Error: Invalid coordinates for {name}")
        continue

    # Get water values for test location
    values = visualize_mask_region(_WATER_MASK, center_lat, center_lon, 
                               title=f'Water Mask Near {name}\n{center_lat:.4f}°N, {center_lon:.4f}°E')

    # Get center pixel values
    center_y = values.shape[0] // 2
    center_x = values.shape[1] // 2
    center_value = values[center_y, center_x]
    
    # Print statistics
    print(f"\nWater mask statistics for region around {name}:")
    print(f"Center value: {center_value:.2f}%")
    print(f"Mean value: {np.mean(values):.2f}%")
    print(f"Min value: {np.min(values):.2f}%")
    print(f"Max value: {np.max(values):.2f}%")

    # Apply different thresholds to see how classification changes
    thresholds = [25, 50, 75]
    threshold_results = {}
    
    for threshold in thresholds:
        binary = classify_water_mask(values, threshold)
        water_percent = 100 * np.sum(binary) / binary.size
        threshold_results[threshold] = water_percent
        
    print("\nWater classification with different thresholds:")
    for threshold, water_percent in threshold_results.items():
        print(f"  Threshold {threshold}%: {water_percent:.1f}% of region is water")
    
    # Save visualization
    filename = f'water_mask_{name.lower().replace(" ", "_")}.png'
    plt.savefig(filename)
    print(f"Saved visualization to {filename}")
    plt.close()

# Always print basic water mask properties
print("\nChecking water mask overall properties:")
unique_values = np.unique(_WATER_MASK)
print(f"Unique values count: {len(unique_values)}")
print(f"First 10 unique values: {unique_values[:10]}")
print(f"Last 10 unique values: {unique_values[-10:]}")

# Print final water/land distribution statistics
water_pixels = np.sum(_WATER_MASK > 0)
land_pixels = np.sum(_WATER_MASK == 0)
masked_pixels = _WATER_MASK.mask.sum() if hasattr(_WATER_MASK, 'mask') else 0
total_pixels = _WATER_MASK.size

print("\nFinal water mask statistics:")
print(f"Total pixels: {total_pixels}")
print(f"Land pixels (0%): {land_pixels} ({land_pixels/total_pixels*100:.1f}%)")
print(f"Water pixels (>0%): {water_pixels} ({water_pixels/total_pixels*100:.1f}%)")
print(f"  Of which:")
print(f"    Partially water (1-50%): {np.sum((0 < _WATER_MASK) & (_WATER_MASK <= 50))} pixels")
print(f"    Mostly water (50-90%): {np.sum((50 < _WATER_MASK) & (_WATER_MASK <= 90))} pixels")
print(f"    Nearly all water (90-99%): {np.sum((90 < _WATER_MASK) & (_WATER_MASK < 100))} pixels")
print(f"    Fully water (100%): {np.sum(_WATER_MASK == 100)} pixels")
print(f"Masked pixels: {masked_pixels} ({masked_pixels/total_pixels*100:.1f}%)")

# Skip global visualizations if flag is set
if not skip_global:
    # Generate a comprehensive visualization showing water mask distribution
    print("\nGenerating water mask distribution visualization...")
    plt.figure(figsize=(12, 8))
    
    # Use log scale for y-axis due to the large number of 0% and 100% values
    hist, bins = np.histogram(valid_range, bins=100)
    plt.bar(bins[:-1], hist, width=bins[1]-bins[0], alpha=0.7, color='skyblue')
    plt.yscale('log')
    
    plt.title('Distribution of Water Percentage Values')
    plt.xlabel('Water Percentage')
    plt.ylabel('Number of Pixels (log scale)')
    plt.grid(True, alpha=0.3)
    
    # Add vertical lines for common thresholds
    plt.axvline(x=25, color='green', linestyle='--', label='25% (Land)')
    plt.axvline(x=50, color='red', linestyle='--', label='50% (Mixed)')
    plt.axvline(x=75, color='blue', linestyle='--', label='75% (Water)')
    plt.legend()
    
    plt.savefig('water_mask_distribution.png')
    print("Saved water percentage distribution to water_mask_distribution.png")
    plt.close()

    # Create a global water mask visualization (downsampled for display)
    print("\nGenerating global water mask visualization...")
    downsample_factor = 10  # Downsample for better display
    global_mask = _WATER_MASK[::downsample_factor, ::downsample_factor]

    plt.figure(figsize=(14, 7))
    plt.imshow(global_mask, cmap='Blues', origin='lower', aspect='auto', vmin=0, vmax=100)
    plt.colorbar(label='Water Percentage')
    plt.title('Global Water Mask (Downsampled)')

    # Add latitude lines (every 30 degrees)
    lat_positions = np.linspace(0, global_mask.shape[0]-1, 7)
    lat_labels = ['90°S', '60°S', '30°S', '0°', '30°N', '60°N', '90°N']
    plt.yticks(lat_positions, lat_labels)

    # Add longitude lines (every 60 degrees)
    lon_positions = np.linspace(0, global_mask.shape[1]-1, 7)
    lon_labels = ['0°', '60°E', '120°E', '180°', '120°W', '60°W', '0°']
    plt.xticks(lon_positions, lon_labels)

    plt.grid(True, color='gray', linestyle='--', alpha=0.5)
    plt.savefig('water_mask_global.png')
    print("Saved global water mask visualization to water_mask_global.png")
    plt.close()

    # Generate water threshold classification
    print("\nGenerating water threshold test...")
    plt.figure(figsize=(12, 9))

    # Create a 2x2 subplot layout for different thresholds
    thresholds = [25, 50, 75, 99]
    plt.suptitle('Water Mask Classification with Different Thresholds', fontsize=16)

    for i, threshold in enumerate(thresholds):
        plt.subplot(2, 2, i+1)
        # Create a binary classification
        binary_mask = np.zeros_like(global_mask)
        binary_mask[global_mask > threshold] = 1
        
        plt.imshow(binary_mask, cmap='Blues_r', origin='lower', aspect='auto', 
               vmin=0, vmax=1)
        plt.title(f'Threshold: {threshold}% water')
        plt.colorbar(ticks=[0, 1], label='Classification')
        plt.grid(False)

    plt.tight_layout(rect=[0, 0, 1, 0.96])  # Adjust for suptitle
    plt.savefig('water_mask_thresholds.png')
    print("Saved water threshold classification to water_mask_thresholds.png")
    plt.close()
else:
    print("\nSkipping global visualizations (--skip-global flag was used)")

print("\nWater mask testing complete!")
