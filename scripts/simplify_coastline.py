import geopandas as gpd
import os
from pathlib import Path

# Input and output paths
input_zip = Path("titiler/openeo/data/EEA_Coastline_Polygon_Shape.zip")
output_zip = Path("titiler/openeo/data/EEA_Coastline_Polygon_Shape_simplified.zip")

# Create temporary directory to extract the shapefile
os.makedirs("titiler/openeo/data/coastline_tmp", exist_ok=True)

# Read the shapefile from the zip
gdf = gpd.read_file(f"zip://{input_zip}")

# Apply simplification with tolerance=0.01
# This preserves the topology of the polygons while reducing detail
gdf_simplified = gdf.simplify(tolerance=1000, preserve_topology=True)

# Update the geometry column with simplified geometries
gdf.geometry = gdf_simplified

# Save to new zipfile
gdf.to_file(
    "titiler/openeo/data/coastline_tmp/coastline_simplified.shp",
    driver="ESRI Shapefile"
)

# Zip up the temporary directory
os.system(f"cd titiler/openeo/data/coastline_tmp && zip -r {output_zip.resolve()} *")

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
