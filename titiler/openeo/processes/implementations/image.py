"""titiler.openeo.processes.implementations image methods."""

from typing import Dict, Sequence, Tuple, Any
import os
import numpy
from numpy.typing import ArrayLike
import morecantile
from rio_tiler.colormap import cmap as default_cmap
from rio_tiler.types import ColorMapType
from skimage.draw import disk
import colour
from PIL import Image

# Path to the EEA Coastline shapefile zip
COASTLINE_ZIP_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "simplified-land-polygons-complete-3857.zip"
)

# Initialize coastline shapefile data
try:
    import geopandas as gpd
    import shapely.geometry
    from shapely.prepared import prep

    # Load the EEA coastline shapefile directly from the zip file
    # The path format for zip files is: zip://path/to/zip/file.zip!path/inside/zip/file.shp
    zip_path = f"zip://{COASTLINE_ZIP_PATH}!simplified-land-polygons-complete-3857/simplified_land_polygons.shp"
    _COASTLINE_GDF = gpd.read_file(zip_path)
    
    # Convert to a CRS that's suitable for point-in-polygon tests (WGS84)
    if _COASTLINE_GDF.crs != "EPSG:4326":
        _COASTLINE_GDF = _COASTLINE_GDF.to_crs("EPSG:4326")
    
    # Create a unified geometry from all polygons for faster checks
    _COASTLINE_GEOMETRY = _COASTLINE_GDF.geometry.unary_union
    
    # Prepare geometry for efficient containment tests
    _PREPARED_COASTLINE = prep(_COASTLINE_GEOMETRY)
    
    print(f"Loaded coastline shapefile with {len(_COASTLINE_GDF)} features")
    _COASTLINE_LOADED = True
except Exception as e:
    print(f"Error loading coastline shapefile: {e}")
    _COASTLINE_LOADED = False


def is_land(lon, lat):
    """Check if a point is land (inside coastline) or water.
    
    Args:
        lon: Longitude in degrees (-180 to 180)
        lat: Latitude in degrees (-90 to 90)
        
    Returns:
        Boolean: True if point is land, False if water
    """
    # Use the coastline shapefile for land/water determination in Europe
    # Create a point geometry
    point = shapely.geometry.Point(lon, lat)
    
    # Check if the point is inside the coastline (land)
    return _PREPARED_COASTLINE.contains(point)


from .data_model import ImageData, RasterStack

__all__ = [
    "image_indexes",
    "to_array",
    "color_formula",
    "colormap",
    "get_colormap",
    "legofication",
    "generate_subtiles_ref",
    "generate_lego_instructions",
]


def _get_tiles(x, y, z, zoom):
    """Get the subtiles numbers for a given tile (XYZ) necessary to make a mosaic and form the complete tile.
    Args:
        x: X coordinate of the tile
        y: Y coordinate of the tile
        z: Zoom level of the tile
        zoom: Zoom level to generate subtiles for
    Returns:
        List of tuples containing the X and Y coordinates of the subtiles
    """

    # Calculate the tile numbers
    tile_numbers = []
    for i in range(0, 2 ** int(zoom - z)):
        for j in range(0, 2 ** int(zoom - z)):
            tile_numbers.append(
                (x * 2 ** int(zoom - z) + i, y * 2 ** int(zoom - z) + j)
            )
    return tile_numbers


class TileRef:
    """Tile reference class."""

    array: morecantile.Tile = None

    def __init__(self, x: int, y: int, z: int):
        self.array = morecantile.Tile(x=x, y=y, z=z)

    def __repr__(self) -> str:
        return f"TileRef(x={self.array.x}, y={self.array.y}, z={self.array.z})"

    def __str__(self) -> str:
        return f"{self.array.x}_{self.array.y}_{self.array.z}"


def generate_subtiles_ref(x: int, y: int, z: int, zoom: int) -> ArrayLike:
    """Generate subtiles reference for a given tile (XYZ) necessary to make a mosaic and form the complete tile.

    Args:
        x: X coordinate of the tile
        y: Y coordinate of the tile
        z: Zoom level of the tile
        zoom: Zoom level to generate subtiles for

    Returns:
        Dictionary mapping subtiles to ImageData objects
    """
    # Get the subtiles numbers
    subtiles = _get_tiles(x, y, z, zoom)

    # Create an array of tuples containing all the subtiles references
    # Each subtiles reference is a tuple of (x, y, z)
    subtiles_ref = {
        f"{tile[0]}_{tile[1]}_{zoom}": TileRef(tile[0], tile[1], zoom)
        for tile in subtiles
    }
    # Create a dictionary mapping subtiles to ImageData objects
    return subtiles_ref


def _apply_image_indexes(data: ImageData, indexes: Sequence[int]) -> ImageData:
    """Select indexes from a single ImageData."""
    if not all(v > 0 for v in indexes):
        raise IndexError(f"Indexes value must be >= 1, {indexes}")

    if not all(v <= data.count + 1 for v in indexes):
        raise IndexError(f"Indexes value must be =< {data.count + 1}, {indexes}")

    stats = None
    if stats := data.dataset_statistics:
        stats = [stats[ix - 1] for ix in indexes]

    return ImageData(
        data.array[[idx - 1 for idx in indexes]],
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_names=[data.band_names[ix - 1] for ix in indexes],
        metadata=data.metadata,
        dataset_statistics=stats,
        cutline_mask=data.cutline_mask,
    )


def image_indexes(data: RasterStack, indexes: Sequence[int]) -> RasterStack:
    """Select indexes from a RasterStack.

    Args:
        data: RasterStack to process
        indexes: Sequence of band indexes to select (1-based)

    Returns:
        RasterStack with selected indexes
    """
    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _apply_image_indexes(img_data, indexes)
    return result


def to_array(
    data: RasterStack,
) -> Dict[str, numpy.ma.MaskedArray]:
    """Convert RasterStack to array(s).

    Args:
        data: RasterStack to convert

    Returns:
        Dictionary mapping keys to numpy.ma.MaskedArray
    """
    # Convert each item to array
    return {key: img_data.array for key, img_data in data.items()}


def _apply_color_formula(data: ImageData, formula: str) -> ImageData:
    """Apply color formula to a single ImageData."""
    return data.apply_color_formula(formula)


def color_formula(data: RasterStack, formula: str) -> RasterStack:
    """Apply color formula to RasterStack.

    Args:
        data: RasterStack to process
        formula: Color formula to apply

    Returns:
        RasterStack with color formula applied
    """
    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _apply_color_formula(img_data, formula)
    return result


def get_colormap(name: str) -> ColorMapType:
    """Return rio-tiler colormap."""
    return default_cmap.get(name)


def _apply_colormap(data: ImageData, colormap: ColorMapType) -> ImageData:
    """Apply colormap to a single ImageData."""
    return data.apply_colormap(colormap)


def _get_water_mask_for_bounds(bounds, crs, shape, intersection_threshold=0.20):
    """Get water mask for the given bounds, using coastline shapefile for high accuracy.

    Args:
        bounds: Bounds in the source CRS (minx, miny, maxx, maxy) or (left, bottom, right, top)
        crs: Source coordinate reference system
        shape: Shape of the target mask (height, width)
        intersection_threshold: Threshold for determining if a cell is land (default 0.20)
                               If the intersection area is > 20% of the cell area, it's considered land

    Returns:
        Water mask array resampled to the target shape
    """
    import pyproj
    import numpy as np
    from shapely.geometry import Point, box

    try:
        # Unpack bounds
        minx, miny, maxx, maxy = bounds

        # Create transformers for coordinate conversion
        if crs:
            # If source CRS is already EPSG:4326, no need to transform
            if str(crs).upper() == "EPSG:4326":
                # Bounds are already in lat/lon
                west, south, east, north = minx, miny, maxx, maxy
            else:
                # Create transformer to convert from source CRS to WGS84 (lat/lon)
                transformer = pyproj.Transformer.from_crs(
                    crs, "EPSG:4326", always_xy=True
                )

                # Convert corners to lat/lon
                west, south = transformer.transform(minx, miny)
                east, north = transformer.transform(maxx, maxy)

                # Check if we need to handle corners (for some projections)
                nw_lon, nw_lat = transformer.transform(minx, maxy)
                se_lon, se_lat = transformer.transform(maxx, miny)

                # Adjust bounds if needed
                west = min(west, nw_lon)
                east = max(east, se_lon)
                south = min(south, se_lat)
                north = max(north, nw_lat)
        else:
            # If no CRS provided, assume bounds are already in lat/lon
            west, south, east, north = minx, miny, maxx, maxy

        # Create an empty water mask (1 = water, 0 = land)
        water_mask = np.ones(shape, dtype=np.float32)
        
        # Check if the bounds are within Europe where EEA coastline data is valid
        # The EEA coastline covers roughly -30° to 50° longitude and 30° to 72° latitude
        is_europe_region = (
            (-30 <= west <= 50 or -30 <= east <= 50) and 
            (30 <= south <= 72 or 30 <= north <= 72) and
            _COASTLINE_LOADED
        )
        
        if not is_europe_region:
            # If not in Europe or coastline data unavailable, return all water
            return water_mask
        
        # Calculate the grid of lat/lon points to check using coastline data
        y_steps = np.linspace(north, south, shape[0])
        x_steps = np.linspace(west, east, shape[1])
        
        # Calculate cell size
        cell_height = (north - south) / shape[0]
        cell_width = (east - west) / shape[1]
        
        # Create grid of points and check each cell
        for i, lat in enumerate(y_steps):
            for j, lon in enumerate(x_steps):
                # Instead of just checking the center point, create a small box for each cell
                # This ensures coastlines are properly detected
                cell_minx = lon - cell_width/2
                cell_miny = lat - cell_height/2
                cell_maxx = lon + cell_width/2
                cell_maxy = lat + cell_height/2
                
                # Create a box geometry for this cell
                cell_box = box(cell_minx, cell_miny, cell_maxx, cell_maxy)
                
                # Check if this cell intersects with land
                # If the intersection area is > threshold of the cell area, mark it as land
                if _PREPARED_COASTLINE.intersects(cell_box):
                    # For more precise detection, calculate the intersection area
                    intersection = _COASTLINE_GEOMETRY.intersection(cell_box)
                    intersection_area = intersection.area
                    
                    # If the intersection area is > threshold of the cell area, mark it as land
                    if intersection_area > intersection_threshold:
                        water_mask[i, j] = 0  # Set to 0 for land
                
        return water_mask

    except Exception as e:
        print(f"Error processing water mask: {e}")
        # Return all water as fallback
        return numpy.ones(shape, dtype=numpy.float32)


def _legofication(
    data: ImageData,
    nbbricks: int = 16,
    bricksize: int = 16,
    water_threshold: float = 0.20,
) -> ImageData:
    """Apply legofication to ImageData by converting the image to LEGO colors and adding brick effects.

    Args:
        data: ImageData to process
        nbbricks: Number of LEGO bricks for the smallest image dimension
        bricksize: Size of each LEGO brick in pixels
        water_threshold: Threshold for water classification (0-1.0, default 0.75)
                        If the intersection area is > 75% of the cell area, it's considered land

    Returns:
        ImageData with legofication applied and brick information stored in metadata
    """
    """Apply legofication to ImageData by converting the image to LEGO colors and adding brick effects.

    Args:
        data: ImageData to process
        nbbricks: Number of LEGO bricks for the smallest image dimension
        bricksize: Size of each LEGO brick in pixels
        water_threshold: Threshold for water classification (0-1.0, default 0.75)
                        If the intersection area is > 75% of the cell area, it's considered land
    """

    def _compress(img: ImageData, nbbricks: int = 16) -> ImageData:
        min_side = min(img.array.shape[-2:])
        new_shape = numpy.round(
            numpy.array(img.array.shape[-2:]) / min_side * nbbricks
        ).astype(int)
        return img.resize(new_shape[0], new_shape[1], resampling_method="bilinear")

    def _upscale(img: ImageData, bricksize: int = 16) -> ImageData:
        # Store water pixels information before resizing
        water_pixels = getattr(img.array, "_water_pixels", set())

        # Calculate new dimensions
        new_shape = (bricksize * numpy.array(img.array.shape[-2:])).astype(int)

        # Resize the image
        upscaled_img = img.resize(
            new_shape[0], new_shape[1], resampling_method="nearest"
        )

        # Transfer water pixel information to the new image with scaling
        if water_pixels:
            # Create a new set to hold scaled water pixel coordinates
            upscaled_water_pixels = set()

            # Scale factor between original and upscaled image
            scale_y = new_shape[0] / img.array.shape[-2]
            scale_x = new_shape[1] / img.array.shape[-1]

            # For each water pixel in the original image, calculate the corresponding
            # block of pixels in the upscaled image
            for y, x in water_pixels:
                # Calculate bounds of the upscaled block
                y_start = int(y * scale_y)
                y_end = int((y + 1) * scale_y)
                x_start = int(x * scale_x)
                x_end = int((x + 1) * scale_x)

                # Add all pixels in this block to the set of upscaled water pixels
                for new_y in range(y_start, y_end):
                    for new_x in range(x_start, x_end):
                        upscaled_water_pixels.add((new_y, new_x))

            # Attach the upscaled water pixels to the new image
            upscaled_img.array._water_pixels = upscaled_water_pixels

        return upscaled_img

    def _brickification(img: ImageData, nblocks: Tuple[int, int]) -> ImageData:
        nmin = numpy.min(nblocks)
        d = (numpy.min(numpy.array(img.array.data.shape[-2:])) // nmin) / 2

        # Track water pixels (they're already in upscaled coordinates after _upscale)
        water_pixels = getattr(img.array, "_water_pixels", set())

        # Calculate center coordinates for each brick in upscaled image
        for i in range(nblocks[0]):
            for j in range(nblocks[1]):
                xc = round(d + 2 * d * i)
                yc = round(d + 2 * d * j)
                cur_values = img.array.data[:, xc, yc].copy()

                # Check if this is a water area that should use transparent bricks
                is_water = (xc, yc) in water_pixels

                # Different rendering for transparent water bricks
                if is_water:
                    # For transparent bricks:
                    # 1. Make the brick more "glassy" by applying subtle highlights
                    # 2. Add softer specular highlights
                    # 3. Create a more realistic glass-like appearance

                    # Add a subtle overall brightness to simulate transparency
                    rr, cc = disk((xc, yc), 0.9 * d, shape=img.array.data.shape[::-1])
                    for b in range(img.array.data.shape[0]):
                        img.array.data[b, rr, cc] = (
                            img.array.data[b, rr, cc] * 0.85 + 40 * 0.15
                        ).astype(img.array.data.dtype)

                    # Light the top-left edge with a soft specular highlight
                    rr, cc = disk(
                        (xc - 2, yc - 2), 0.7 * d, shape=img.array.data.shape[::-1]
                    )
                    for b in range(img.array.data.shape[0]):
                        # Softer highlight for transparent bricks (40% white)
                        img.array.data[b, rr, cc] = (
                            img.array.data[b, rr, cc] * 0.6 + 200 * 0.4
                        ).astype(img.array.data.dtype)

                    # Add subtle internal reflection on bottom-right
                    rr, cc = disk(
                        (xc + 2, yc + 2), 0.6 * d, shape=img.array.data.shape[::-1]
                    )
                    for b in range(img.array.data.shape[0]):
                        # Gentle darkening for depth
                        img.array.data[b, rr, cc] = (
                            img.array.data[b, rr, cc] * 0.8 + 20 * 0.2
                        ).astype(img.array.data.dtype)

                    # Make the stud more glass-like
                    rr, cc = disk((xc, yc), 0.65 * d, shape=img.array.data.shape[::-1])
                    for b in range(img.array.data.shape[0]):
                        # Subtle brightening of the stud
                        img.array.data[b, rr, cc] = (
                            cur_values[b] * 0.8 + 120 * 0.2
                        ).astype(img.array.data.dtype)

                    # Add a soft specular highlight to the stud
                    rr, cc = disk(
                        (xc - 1, yc - 1), 0.25 * d, shape=img.array.data.shape[::-1]
                    )
                    for b in range(img.array.data.shape[0]):
                        # Softer highlight (45% white)
                        img.array.data[b, rr, cc] = (
                            img.array.data[b, rr, cc] * 0.55 + 200 * 0.45
                        ).astype(img.array.data.dtype)

                else:
                    # Regular brick rendering for land
                    # Light the bricks on top left
                    rr, cc = disk(
                        (xc - 2, yc - 2), 0.6 * d, shape=img.array.data.shape[::-1]
                    )
                    for b in range(img.array.data.shape[0]):
                        img.array.data[b, rr, cc] = (
                            img.array.data[b, rr, cc] * 0.5 + 200 * 0.5
                        ).astype(img.array.data.dtype)

                    # Dark the bricks on bottom right
                    rr, cc = disk(
                        (xc + 2, yc + 2), 0.6 * d, shape=img.array.data.shape[::-1]
                    )
                    for b in range(img.array.data.shape[0]):
                        img.array.data[b, rr, cc] = (
                            img.array.data[b, rr, cc] * 0.5 + 10 * 0.5
                        ).astype(img.array.data.dtype)

                    # Draw the studs
                    rr, cc = disk((xc, yc), 0.6 * d, shape=img.array.data.shape[::-1])
                    for b in range(img.array.data.shape[0]):
                        img.array.data[b, rr, cc] = cur_values[b]

        return img

    # Compress the image
    small_img = _compress(data, nbbricks)

    # Map each pixel to the closest LEGO color
    rgb_data = small_img.array.data
    shape = rgb_data.shape
    if shape[0] == 3:  # Only process RGB images
        # Get water mask for the image's bounds with proper CRS handling
        water_mask = _get_water_mask_for_bounds(
            bounds=data.bounds,
            crs=data.crs,
            shape=(shape[1], shape[2]),  # Height, width
            intersection_threshold=water_threshold
        )

        for i in range(shape[1]):
            for j in range(shape[2]):
                rgb_pixel = rgb_data[:, i, j]
                # Check if this is a water area that should use transparent bricks
                is_water = water_mask[i, j]

                # Get the appropriate LEGO color for this pixel
                lego_color_name, lego_rgb = find_best_lego_color(rgb_pixel, is_water)

                # Initialize brick information in metadata if not present
                if 'brick_info' not in small_img.metadata:
                    small_img.metadata['brick_info'] = {'tiles': {}}

                # Store brick information including position, color name, and whether it's water
                tile_key = f"{i}_{j}"
                small_img.metadata['brick_info']['tiles'][tile_key] = {
                    'position': (i, j),
                    'color_name': lego_color_name,
                    'is_water': is_water
                }

                # Store information about transparent bricks for later processing
                if is_water:
                    # For water areas, store the water percentage to control transparency effect
                    # Higher water percentage = more transparent
                    rgb_data[:, i, j] = lego_rgb
                    # Tag this pixel as water/transparent in the alpha channel metadata
                    if not hasattr(small_img.array, "_water_pixels"):
                        small_img.array._water_pixels = set()
                    small_img.array._water_pixels.add((i, j))
                else:
                    # For land areas, use normal color
                    rgb_data[:, i, j] = lego_rgb

                # Store overall brick grid dimensions for later reference
                small_img.metadata['brick_info']['grid_dimensions'] = (shape[1], shape[2])

    # Upscale and add brick effects
    lego_img = _upscale(small_img, bricksize)
    return _brickification(lego_img, small_img.array.shape[-2:])


# LEGO colors dictionary with HSL values and transparency information
lego_colors = {
    # Add transparent water colors
    "Transparent Water Blue Light": {
        "hsl": [200, 80, 60],
        "rgb": [51, 153, 204],
        "pantone": "2915 C",
        "hex": "#3399CC",
        "transparent": True,
    },
    "Transparent Water Blue Dark": {
        "hsl": [210, 90, 30],
        "rgb": [8, 45, 82],
        "pantone": "534 C",
        "hex": "#082D52",
        "transparent": True,
    },
    # Regular colors
    "White": {
        "hsl": [0, 0, 96],
        "rgb": [244, 244, 244],
        "pantone": "TBC",
        "hex": "#F4F4F4",
        "transparent": False,
    },
    "Light Bluish Grey (Medium Stone Grey)": {
        "hsl": [196, 6, 66],
        "rgb": [162, 170, 173],
        "pantone": "429 C",
        "hex": "#A2AAAD",
    },
    "Dark Bluish Grey (Dark Stone Grey)": {
        "hsl": [214, 3, 40],
        "rgb": [99, 102, 106],
        "pantone": "CG 10 C",
        "hex": "#63666A",
    },
    "Black": {
        "hsl": [210, 33, 9],
        "rgb": [16, 24, 32],
        "pantone": "Black 6 C",
        "hex": "#101820",
    },
    "Tan (Brick Yellow)": {
        "hsl": [40, 66, 82],
        "rgb": [239, 219, 178],
        "pantone": "7506 C",
        "hex": "#EFDBB2",
    },
    "Dark Tan (Sand Yellow)": {
        "hsl": [31, 22, 49],
        "rgb": [154, 127, 98],
        "pantone": "2470 C",
        "hex": "#9A7F62",
    },
    "Olive Green": {
        "hsl": [58, 26, 44],
        "rgb": [141, 139, 83],
        "pantone": "4238 C",
        "hex": "#8D8B53",
    },
    "Sand Green": {
        "hsl": [132, 13, 56],
        "rgb": [129, 158, 135],
        "pantone": "2406 C",
        "hex": "#819E87",
    },
    "Sand Blue": {
        "hsl": [208, 18, 50],
        "rgb": [104, 129, 151],
        "pantone": "2165 C",
        "hex": "#688197",
        "transparent": False,
    },
    "Coral (Vibrant Coral)": {
        "hsl": [354, 100, 67],
        "rgb": [255, 88, 105],
        "pantone": "2346 C",
        "hex": "#FF5869",
        "transparent": False,
    },
    "Red (Bright Red)": {
        "hsl": [352, 100, 40],
        "rgb": [205, 0, 26],
        "pantone": "3546 C",
        "hex": "#CD001A",
        "transparent": False,
    },
    "Dark Red (New Dark Red)": {
        "hsl": [0, 53, 36],
        "rgb": [138, 43, 43],
        "pantone": "7623 C",
        "hex": "#8A2B2B",
        "transparent": False,
    },
    "Reddish Brown": {
        "hsl": [10, 47, 33],
        "rgb": [124, 58, 45],
        "pantone": "7594 C",
        "hex": "#7C3A2D",
        "transparent": False,
    },
    "Dark Brown": {
        "hsl": [358, 33, 19],
        "rgb": [63, 32, 33],
        "pantone": "4975 C",
        "hex": "#3F2021",
        "transparent": False,
    },
    "Light Nougat": {
        "hsl": [18, 60, 81],
        "rgb": [236, 195, 178],
        "pantone": "489 C",
        "hex": "#ECC3B2",
        "transparent": False,
    },
    "Medium Tan (Warm Tan)": {
        "hsl": [32, 100, 74],
        "rgb": [255, 194, 123],
        "pantone": "149 C",
        "hex": "#FFC27B",
        "transparent": False,
    },
    "Nougat": {
        "hsl": [25, 70, 66],
        "rgb": [229, 158, 109],
        "pantone": "472 C",
        "hex": "#E59E6D",
        "transparent": False,
    },
    "Medium Nougat": {
        "hsl": [29, 55, 52],
        "rgb": [200, 130, 66],
        "pantone": "722 C",
        "hex": "#C88242",
        "transparent": False,
    },
    "Orange (Bright Orange)": {
        "hsl": [31, 100, 50],
        "rgb": [255, 130, 0],
        "pantone": "151 C",
        "hex": "#FF8200",
        "transparent": False,
    },
    "Dark Orange": {
        "hsl": [27, 100, 37],
        "rgb": [190, 84, 0],
        "pantone": "2020 C",
        "hex": "#BE5400",
        "transparent": False,
    },
    "Medium Brown": {
        "hsl": [25, 38, 33],
        "rgb": [120, 81, 53],
        "pantone": "7568 C",
        "hex": "#785135",
        "transparent": False,
    },
    "Bright Light Yellow (Cool Yellow)": {
        "hsl": [48, 91, 73],
        "rgb": [249, 226, 125],
        "pantone": "2002 C",
        "hex": "#F9E27D",
        "transparent": False,
    },
    "Yellow (Bright Yellow)": {
        "hsl": [48, 100, 50],
        "rgb": [255, 205, 0],
        "pantone": "116 C",
        "hex": "#FFCD00",
        "transparent": False,
    },
    "Bright Light Orange (Flame Yellowish Orange)": {
        "hsl": [43, 100, 50],
        "rgb": [255, 182, 0],
        "pantone": "2010 C",
        "hex": "#FFB600",
        "transparent": False,
    },
    "Neon Yellow (Vibrant Yellow)": {
        "hsl": [59, 100, 50],
        "rgb": [255, 252, 0],
        "pantone": "TBC",
        "hex": "#FFFC00",
        "transparent": False,
    },
    "Yellowish Green (Spring Yellowish Green)": {
        "hsl": [76, 72, 71],
        "rgb": [205, 234, 128],
        "pantone": "373 C",
        "hex": "#CDEA80",
        "transparent": False,
    },
    "Lime (Bright Yellowish Green)": {
        "hsl": [70, 100, 41],
        "rgb": [174, 208, 0],
        "pantone": "3507 C",
        "hex": "#AED000",
    },
    "Bright Green": {
        "hsl": [127, 100, 33],
        "rgb": [0, 170, 19],
        "pantone": "2423 C",
        "hex": "#00AA13",
    },
    "Green (Dark Green)": {
        "hsl": [141, 100, 27],
        "rgb": [0, 137, 47],
        "pantone": "3522 C",
        "hex": "#00892F",
    },
    "Dark Green (Earth Green)": {
        "hsl": [145, 100, 14],
        "rgb": [0, 73, 30],
        "pantone": "3537 C",
        "hex": "#00491E",
    },
    "Light Aqua (Aqua)": {
        "hsl": [163, 33, 79],
        "rgb": [185, 220, 210],
        "pantone": "566 C",
        "hex": "#B9DCD2",
    },
    "Dark Turquoise (Bright Bluish Green)": {
        "hsl": [184, 100, 31],
        "rgb": [0, 147, 157],
        "pantone": "3541 C",
        "hex": "#00939D",
    },
    "Medium Azure": {
        "hsl": [190, 100, 44],
        "rgb": [0, 188, 225],
        "pantone": "3545 C",
        "hex": "#00BCE1",
    },
    "Dark Azure": {
        "hsl": [198, 100, 42],
        "rgb": [0, 148, 213],
        "pantone": "3538 C",
        "hex": "#0094D5",
    },
    "Bright Light Blue (Light Royal Blue)": {
        "hsl": [208, 66, 74],
        "rgb": [146, 193, 233],
        "pantone": "283 C",
        "hex": "#92C1E9",
    },
    "Medium Blue": {
        "hsl": [208, 69, 66],
        "rgb": [108, 172, 228],
        "pantone": "284 C",
        "hex": "#6CACE4",
    },
    "Blue (Bright Blue)": {
        "hsl": [208, 100, 39],
        "rgb": [0, 106, 198],
        "pantone": "2175C",
        "hex": "#006AC6",
    },
    "Dark Blue (Earth Blue)": {
        "hsl": [207, 100, 17],
        "rgb": [0, 48, 87],
        "pantone": "540 C",
        "hex": "#003057",
    },
    "Lavender": {
        "hsl": [269, 40, 78],
        "rgb": [199, 178, 222],
        "pantone": "2071 C",
        "hex": "#C7B2DE",
    },
    "Medium Lavender": {
        "hsl": [273, 43, 64],
        "rgb": [167, 123, 202],
        "pantone": "2577 C",
        "hex": "#A77BCA",
    },
    "Dark Purple (Medium Lilac)": {
        "hsl": [262, 36, 38],
        "rgb": [86, 61, 130],
        "pantone": "7679 C",
        "hex": "#563D82",
    },
    "Bright Pink (Light Purple)": {
        "hsl": [317, 73, 80],
        "rgb": [241, 167, 220],
        "pantone": "236 C",
        "hex": "#F1A7DC",
    },
    "Dark Pink (Bright Purple)": {
        "hsl": [326, 70, 66],
        "rgb": [229, 109, 177],
        "pantone": "218 C",
        "hex": "#E56DB1",
    },
    "Magenta (Bright Reddish Violet)": {
        "hsl": [322, 100, 32],
        "rgb": [162, 0, 103],
        "pantone": "234 C",
        "hex": "#A20067",
    },
}


def find_best_lego_color(
    rgb: numpy.ndarray, use_transparent: bool = False
) -> Tuple[str, numpy.ndarray]:
    """Find the best matching LEGO color for an RGB value using colour-science.

    Args:
        rgb: RGB values as numpy array with shape (3,) and values in range [0, 255]
        use_transparent: Whether to use transparent colors for water areas

    Returns:
        Tuple with the best LEGO color name and RGB values as numpy array with shape (3,)
    """
    # Convert input RGB to Lab color space
    rgb_normalized = rgb.astype(float) / 255.0
    lab_input = colour.XYZ_to_Lab(colour.sRGB_to_XYZ(rgb_normalized))

    min_distance = float("inf")
    best_color = None

    # Filter colors based on transparency preference
    available_colors = {
        name: info
        for name, info in lego_colors.items()
        if info.get("transparent", False) == use_transparent
    }

    # Find closest LEGO color using CIEDE2000 color difference
    for color_name, color_info in available_colors.items():
        rgb_lego = numpy.array(color_info["rgb"], dtype=float) / 255.0
        lab_lego = colour.XYZ_to_Lab(colour.sRGB_to_XYZ(rgb_lego))

        distance = colour.delta_E(lab_input, lab_lego, method="CIE 2000")

        if distance < min_distance:
            min_distance = distance
            best_color = color_name

    return (best_color, lego_colors[best_color]["rgb"])


def legofication(
    data: RasterStack,
    nbbricks: int = 16,
    bricksize: int = 16,
    water_threshold: float = 0.20,
) -> RasterStack:
    """Apply legofication to RasterStack by converting images to LEGO colors and adding brick effects.

    Args:
        data: RasterStack to process
        nbbricks: Number of LEGO bricks for the smallest image dimension
        bricksize: Size of each LEGO brick in pixels
        water_threshold: Percentage threshold for water classification (default 20%)

    Returns:
        RasterStack with legofication applied
    """
    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _legofication(img_data, nbbricks, bricksize, water_threshold)
    return result


def generate_lego_instructions(
    data: RasterStack,
    grid_size: int = 50,
    include_legend: bool = True,
) -> RasterStack:
    """Generate building instructions for a legofied image.

    Args:
        data: RasterStack containing legofied image with brick information
        grid_size: Size of each grid cell in pixels
        include_legend: Whether to include a color legend with brick counts

    Returns:
        RasterStack containing the instruction image
    """
    def _generate_instruction_image(img_data: ImageData) -> ImageData:
        if 'brick_info' not in img_data.metadata:
            raise ValueError("Input image must be legofied with brick information in metadata")

        brick_info = img_data.metadata['brick_info']
        grid_dims = brick_info['grid_dimensions']
        
        # Calculate image dimensions
        legend_width = 300 if include_legend else 0
        img_width = grid_dims[1] * grid_size + legend_width
        img_height = grid_dims[0] * grid_size
        
        # Create a blank white image
        instruction_img = Image.new('RGB', (img_width, img_height), 'white')
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(instruction_img)
        
        # Try to load a font, fall back to default if not available
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=int(grid_size/4))
        except:
            font = ImageFont.load_default()
        
        # Draw grid lines
        for i in range(grid_dims[0] + 1):
            y = i * grid_size
            draw.line([(0, y), (grid_dims[1] * grid_size, y)], fill='gray', width=1)
        for j in range(grid_dims[1] + 1):
            x = j * grid_size
            draw.line([(x, 0), (x, grid_dims[0] * grid_size)], fill='gray', width=1)
        
        # Track brick colors for the legend
        color_counts = {}
        
        # Draw bricks
        for tile_key, tile_info in brick_info['tiles'].items():
            i, j = tile_info['position']
            color_name = tile_info['color_name']
            is_water = tile_info['is_water']
            
            # Get RGB color
            rgb_color = lego_colors[color_name]['rgb']
            hex_color = lego_colors[color_name]['hex']
            
            # Update color count
            color_counts[color_name] = color_counts.get(color_name, 0) + 1
            
            # Calculate cell position
            x = j * grid_size
            y = i * grid_size
            
            # Draw filled rectangle for the brick
            draw.rectangle([x+2, y+2, x+grid_size-2, y+grid_size-2], 
                         fill=hex_color)
            
            # Add brick coordinates
            text = f"{i},{j}"
            # Get text size for centering
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            # Center text in cell
            text_x = x + (grid_size - text_width) // 2
            text_y = y + (grid_size - text_height) // 2
            
            # Draw text with outline for better visibility
            text_color = 'white' if sum(rgb_color) < 384 else 'black'
            draw.text((text_x, text_y), text, font=font, fill=text_color)
            
            # Add indicator for transparent/water bricks
            if is_water:
                draw.rectangle([x+2, y+2, x+grid_size-2, y+grid_size-2], 
                             outline='cyan', width=2)
        
        # Add legend if requested
        if include_legend and color_counts:
            legend_x = grid_dims[1] * grid_size + 10
            legend_y = 10
            legend_square_size = int(grid_size/2)
            
            # Draw legend title
            draw.text((legend_x, legend_y), "Brick Colors:", font=font, fill='black')
            legend_y += int(grid_size/2)
            
            # Sort colors by count
            sorted_colors = sorted(color_counts.items(), 
                                 key=lambda x: (-x[1], x[0]))
            
            for color_name, count in sorted_colors:
                hex_color = lego_colors[color_name]['hex']
                
                # Draw color square
                draw.rectangle([legend_x, legend_y, 
                              legend_x + legend_square_size, 
                              legend_y + legend_square_size], 
                             fill=hex_color)
                
                # Draw text
                text = f"{color_name}: {count}"
                draw.text((legend_x + legend_square_size + 5, legend_y), 
                         text, font=font, fill='black')
                
                legend_y += legend_square_size + 5
        
        # Convert PIL Image to numpy array
        instruction_array = numpy.array(instruction_img)
        # Convert from HxWxC to CxHxW format
        instruction_array = numpy.transpose(instruction_array, (2, 0, 1))
        
        # Create new ImageData object with the instruction image
        return ImageData(
            instruction_array,
            bounds=img_data.bounds,
            crs=img_data.crs,
            band_names=['R', 'G', 'B'],
            metadata={
                'instruction_metadata': {
                    'grid_size': grid_size,
                    'total_bricks': sum(color_counts.values()),
                    'color_counts': color_counts
                }
            }
        )

    # Process each image in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _generate_instruction_image(img_data)
    return result

def colormap(data: RasterStack, colormap: ColorMapType) -> RasterStack:
    """Apply colormap to RasterStack.

    Args:
        data: RasterStack to process
        colormap: Colormap to apply

    Returns:
        RasterStack with colormap applied
    """
    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _apply_colormap(img_data, colormap)
    return result
