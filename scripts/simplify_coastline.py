import geopandas as gpd
import os
from pathlib import Path

import zipfile
import glob

# Input and output paths
input_zip = Path("/home/emathot/Downloads/coastlines-split-3857.zip")
output_zip = Path("/home/emathot/Downloads/coastlines-split-3857-simplified.zip")

# Create and clean temporary directory
tmp_dir = Path("titiler/openeo/data/coastline_tmp")
os.makedirs(tmp_dir, exist_ok=True)
os.system(f"rm -rf {tmp_dir}/*")

# Extract the zip file
with zipfile.ZipFile(input_zip, 'r') as zip_ref:
    zip_ref.extractall(tmp_dir)
    print("Extracted files:")
    for file in zip_ref.namelist():
        print(f"  - {file}")

# Find the shapefile (search recursively)
shp_files = glob.glob(str(tmp_dir / "**/*.shp"), recursive=True)
if not shp_files:
    raise ValueError("No shapefile found in the zip archive")

print(f"\nFound shapefile: {shp_files[0]}")
# Read the first shapefile found
gdf = gpd.read_file(shp_files[0])

# Apply simplification with tolerance=10000 meters (since input is in 3857)
# This preserves the topology of the polygons while reducing detail
gdf_simplified = gdf.simplify(tolerance=50000, preserve_topology=True)

# Update the geometry column with simplified geometries
gdf.geometry = gdf_simplified

# Clean up temporary directory before saving
os.system(f"rm -rf {tmp_dir}/*")

# Save to new shapefile
gdf.to_file(
    f"{tmp_dir}/coastline_simplified.shp",
    driver="ESRI Shapefile"
)

# Create the output zip file with only the simplified files
os.system(f"cd {tmp_dir} && zip -r {output_zip.resolve()} coastline_simplified.*")

# Clean up temporary files
os.system("rm -rf titiler/openeo/data/coastline_tmp/*")
print(f"Simplified coastline saved to {output_zip}")

# Print size reduction
original_size = os.path.getsize(input_zip) / (1024 * 1024)  # MB
new_size = os.path.getsize(output_zip) / (1024 * 1024)  # MB
reduction = ((original_size - new_size) / original_size) * 100

print(f"\nFile size reduction:")
print(f"Original: {original_size:.1f}MB")
print(f"Simplified: {new_size:.1f}MB")
print(f"Reduction: {reduction:.1f}%")
