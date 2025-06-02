"""titiler.openeo.processes.implementations image methods."""

import json
import os
from typing import Any, Dict, Tuple

import colour
import morecantile
import numpy
from numpy.typing import ArrayLike
from PIL import Image
import pyproj
from skimage.draw import disk

from .data_model import ImageData, RasterStack

__all__ = [
    "legofication",
    "generate_subtiles_ref",
    "generate_lego_instructions",
    "get_brick_quantities",
]


def _load_lego_colors(config_path=None):
    """Load LEGO colors from JSON configuration file.

    Args:
        config_path: Optional path to a custom colors configuration file

    Returns:
        Dictionary containing LEGO color definitions
    """
    # Check environment variable for config path
    env_path = os.getenv("TITILER_OPENEO_LEGO_COLORS_FILE")

    # Use provided path, environment variable, or default config
    if config_path is None:
        config_path = env_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "lego_colors.json"
        )

    try:
        with open(config_path) as f:
            return json.load(f)["colors"]
    except Exception as e:
        print(f"Error loading LEGO colors from {config_path}: {e}")
        return {}


# Load default LEGO colors
lego_colors = _load_lego_colors()

# Path to the EEA Coastline shapefile zip
COASTLINE_ZIP_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "data",
    "simplified-land-polygons-complete-3857.zip",
)

import threading
from typing import Optional

# Initialize coastline data placeholders
_COASTLINE_GDF: Optional[Any] = None
_COASTLINE_GEOMETRY: Optional[Any] = None
_PREPARED_COASTLINE: Optional[Any] = None
_COASTLINE_LOADED = False
_COASTLINE_LOADING = False
_COASTLINE_LOAD_ERROR: Optional[str] = None
_COASTLINE_LOCK = threading.Lock()


def _load_coastline_data():
    """Load coastline data in background thread."""
    global \
        _COASTLINE_GDF, \
        _COASTLINE_GEOMETRY, \
        _PREPARED_COASTLINE, \
        _COASTLINE_LOADED, \
        _COASTLINE_LOADING, \
        _COASTLINE_LOAD_ERROR

    try:
        import geopandas as gpd
        from shapely.prepared import prep

        # Load the EEA coastline shapefile directly from the zip file
        zip_path = f"zip://{COASTLINE_ZIP_PATH}!simplified-land-polygons-complete-3857/simplified_land_polygons.shp"
        gdf = gpd.read_file(zip_path)

        # Convert to a CRS that's suitable for point-in-polygon tests (WGS84)
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        # Create a unified geometry from all polygons for faster checks
        geometry = gdf.geometry.unary_union

        # Prepare geometry for efficient containment tests
        prepared = prep(geometry)

        # Update global variables atomically
        with _COASTLINE_LOCK:
            global _COASTLINE_GDF, _COASTLINE_GEOMETRY, _PREPARED_COASTLINE
            _COASTLINE_GDF = gdf
            _COASTLINE_GEOMETRY = geometry
            _PREPARED_COASTLINE = prepared
            _COASTLINE_LOADED = True
            _COASTLINE_LOAD_ERROR = None
            print(f"Loaded coastline shapefile with {len(gdf)} features")

    except Exception as e:
        with _COASTLINE_LOCK:
            _COASTLINE_LOAD_ERROR = str(e)
            print(f"Error loading coastline shapefile: {e}")
    finally:
        with _COASTLINE_LOCK:
            _COASTLINE_LOADING = False


# Start background loading
_COASTLINE_LOADING = True
thread = threading.Thread(target=_load_coastline_data, daemon=True)
thread.start()


def get_coastline_status():
    """Get the current status of coastline data loading."""
    with _COASTLINE_LOCK:
        return {
            "loaded": _COASTLINE_LOADED,
            "loading": _COASTLINE_LOADING,
            "error": _COASTLINE_LOAD_ERROR,
        }


def is_land(lon, lat, max_wait_seconds=30):
    """Check if a point is land (inside coastline) or water.

    Args:
        lon: Longitude in degrees (-180 to 180)
        lat: Latitude in degrees (-90 to 90)
        max_wait_seconds: Maximum time to wait for coastline data to load (default 30 seconds)

    Returns:
        Boolean: True if point is land, False if water or data not loaded
    """
    # Wait for coastline data to load
    import time

    start_time = time.time()
    while _COASTLINE_LOADING and time.time() - start_time < max_wait_seconds:
        time.sleep(1)  # Wait 1 second between checks

    with _COASTLINE_LOCK:
        if not _COASTLINE_LOADED:
            # If data is not loaded yet after waiting, assume water
            return False

        # Use the coastline shapefile for land/water determination in Europe
        import shapely.geometry

        point = shapely.geometry.Point(lon, lat)

        # Check if the point is inside the coastline (land)
        return _PREPARED_COASTLINE.contains(point)


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


def _get_water_mask_for_bounds(
    bounds, crs, shape, intersection_threshold=0.20, max_wait_seconds=30
):
    """Get water mask for the given bounds, using coastline shapefile for high accuracy.

    Args:
        bounds: Bounds in the source CRS (minx, miny, maxx, maxy) or (left, bottom, right, top)
        crs: Source coordinate reference system
        shape: Shape of the target mask (height, width)
        intersection_threshold: Threshold for determining if a cell is land (default 0.20)
                               If the intersection area is > 20% of the cell area, it's considered land
        max_wait_seconds: Maximum time to wait for coastline data to load (default 30 seconds)

    Returns:
        Water mask array resampled to the target shape
    """
    # Wait for coastline data to load
    import time

    start_time = time.time()
    while _COASTLINE_LOADING and time.time() - start_time < max_wait_seconds:
        time.sleep(1)  # Wait 100ms between checks

    if _COASTLINE_LOADING:
        print(f"Warning: Coastline data still loading after {max_wait_seconds} seconds")
        return numpy.zeros(shape, dtype=bool)  # Return all land if still loading
    import numpy as np
    import pyproj
    from shapely.geometry import box

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
        water_mask = np.ones(shape, dtype=numpy.int8)

        # Check if the bounds are within Europe where EEA coastline data is valid
        # The EEA coastline covers roughly -30° to 50° longitude and 30° to 72° latitude
        is_europe_region = (
            (-30 <= west <= 50 or -30 <= east <= 50)
            and (30 <= south <= 72 or 30 <= north <= 72)
            and _COASTLINE_LOADED
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
                cell_minx = lon - cell_width / 2
                cell_miny = lat - cell_height / 2
                cell_maxx = lon + cell_width / 2
                cell_maxy = lat + cell_height / 2

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
        # Return all land if there's an error
        return numpy.zeros(shape, dtype=bool)


def _legofication(
    data: ImageData,
    nbbricks: int = 16,
    bricksize: int = 16,
    water_threshold: float = 0.20,
    colors_config: str = None,
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

        # Add the base plate to be used according to the bricks chosed in the previous step
        # If all the bricks are transparent, use a Dark Bluish Grey base plate
        # Otherwise, use a Light Bluish Grey base plate
        # get all the information from the lego_colors dict
        base_plate = {}
        if all(
            info["is_water"] for info in img.metadata["brick_info"]["pixels"].values()
        ):
            base_plate = {
                "color_name": "Dark Bluish Grey",
                "rgb": lego_colors["Dark Bluish Grey"]["rgb"],
                "hex": lego_colors["Dark Bluish Grey"]["hex"],
            }
        else:
            base_plate = {
                "color_name": "Light Bluish Grey",
                "rgb": lego_colors["Light Bluish Grey"]["rgb"],
                "hex": lego_colors["Light Bluish Grey"]["hex"],
            }

        # add the base plate in the metadata
        img.metadata["brick_info"]["base"] = base_plate

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
            intersection_threshold=water_threshold,
        )

        for i in range(shape[1]):
            for j in range(shape[2]):
                rgb_pixel = rgb_data[:, i, j]
                # Check if this is a water area that should use transparent bricks
                is_water = water_mask[i, j]

                # Get the appropriate LEGO color for this pixel
                lego_color_name, lego_rgb = find_best_lego_color(rgb_pixel, is_water)

                # Initialize brick information in metadata if not present
                if "brick_info" not in small_img.metadata:
                    small_img.metadata["brick_info"] = {"pixels": {}}

                # Store brick information including position, color name, and whether it's water
                tile_key = f"{i}_{j}"
                small_img.metadata["brick_info"]["pixels"][tile_key] = {
                    "position": (i, j),
                    "color_name": lego_color_name,
                    "is_water": bool(is_water),
                    "rgb": lego_rgb,
                    "hex": "#%02x%02x%02x" % tuple(lego_rgb),
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

                # Store overall brick grid dimensions and tile bbox for later reference
                # Convert bounds to EPSG:4326 (lat/lon)
                trans = pyproj.Transformer.from_crs(
                        data.crs,
                        pyproj.CRS.from_epsg(4326),
                        always_xy=True,
                )
                tile_bounds = trans.transform_bounds(*data.bounds, densify_pts=21)

                small_img.metadata["brick_info"].update({
                    "grid_dimensions": (shape[1], shape[2]),
                    "tile_bbox": {
                        "west": tile_bounds[0],
                        "south": tile_bounds[1],
                        "east": tile_bounds[2],
                        "north": tile_bounds[3],
                    }
                })

    # Upscale and add brick effects
    lego_img = _upscale(small_img, bricksize)
    return _brickification(lego_img, small_img.array.shape[-2:])


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
    colors_config: str = None,
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
    # Load the LEGO colors from the specified config file if provided
    global lego_colors
    if colors_config:
        lego_colors = _load_lego_colors(colors_config)

    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _legofication(
            img_data, nbbricks, bricksize, water_threshold, colors_config
        )
    return result


def generate_lego_instructions(
    data: RasterStack,
    grid_size: int = 50,
    tile_info: Dict[str, Any] = None,
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
        if "brick_info" not in img_data.metadata:
            raise ValueError(
                "Input image must be legofied with brick information in metadata"
            )

        brick_info = img_data.metadata["brick_info"]
        grid_dims = brick_info["grid_dimensions"]

        # Calculate dimensions for original image display
        original_height = grid_size * 4  # Height for original image display

        # Calculate image dimensions with padding for coordinates
        coord_padding = grid_size
        legend_width = 300 if include_legend else 0
        img_width = grid_dims[1] * grid_size + legend_width + coord_padding
        img_height = grid_dims[0] * grid_size + coord_padding + original_height

        # Create a blank white image with padding
        instruction_img = Image.new("RGB", (img_width, img_height), "white")
        from PIL import ImageDraw, ImageFont

        # Add original image at the top
        original_array = img_data.array.data
        original_img = Image.fromarray(numpy.transpose(original_array, (1, 2, 0)))

        # Calculate width to maintain aspect ratio
        aspect_ratio = original_img.width / original_img.height
        original_width = int(original_height * aspect_ratio)

        # Resize original image
        original_img = original_img.resize(
            (original_width, original_height), Image.Resampling.LANCZOS
        )

        # Center the original image
        original_x = (img_width - original_width) // 2
        instruction_img.paste(original_img, (original_x, 0))

        draw = ImageDraw.Draw(instruction_img)

        # Try to load fonts, fall back to default if not available
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                size=int(grid_size / 4),
            )
            bold_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                size=int(grid_size / 3),  # Slightly larger for emphasis
            )
            big_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                size=int(grid_size / 2),
            )
        except:
            font = ImageFont.load_default()
            bold_font = font

        # Add padding for labels, adjusted for original image
        coord_padding = grid_size
        grid_start_x = coord_padding * 1.2  # Double padding on the left for Y label
        grid_start_y = (
            coord_padding + original_height
        )  # Start grid below original image

        # Draw grid lines with padding
        for i in range(grid_dims[0] + 1):
            y = grid_start_y + i * grid_size
            draw.line(
                [(grid_start_x, y), (grid_start_x + grid_dims[1] * grid_size, y)],
                fill="gray",
                width=1,
            )
        for j in range(grid_dims[1] + 1):
            x = grid_start_x + j * grid_size
            draw.line(
                [(x, grid_start_y), (x, grid_start_y + grid_dims[0] * grid_size)],
                fill="gray",
                width=1,
            )

        # Draw tile position X at the top
        x_position = tile_info["x"]
        text = f"X: {x_position}"
        text_bbox = draw.textbbox((0, 0), text, font=big_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_x = grid_start_x + (grid_dims[1] * grid_size - text_width) // 2
        draw.text(
            (text_x, grid_start_y - grid_size + 10), text, font=big_font, fill="black"
        )

        # Draw tile position Y on the left (rotated 90° counterclockwise)
        y_position = tile_info["y"]
        text = f"Y: {y_position}"
        # Create a new image for the rotated text
        text_img = Image.new("RGB", (grid_size * 2, grid_size), "white")
        text_draw = ImageDraw.Draw(text_img)
        # Draw text
        text_draw.text((0, grid_size // 6), text, font=big_font, fill="black")
        # Rotate and paste
        rotated_text = text_img.rotate(90, expand=True)
        instruction_img.paste(
            rotated_text, (grid_size // 6, grid_start_y + (grid_dims[0] * 8))
        )

        # Track brick colors for the legend
        color_counts = {}

        # Draw bricks
        for pixel_key, pixel_info in brick_info["pixels"].items():
            i, j = pixel_info["position"]
            color_name = pixel_info["color_name"]
            is_water = pixel_info["is_water"]

            # Get RGB color
            rgb_color = lego_colors[color_name]["rgb"]
            hex_color = lego_colors[color_name]["hex"]

            # Update color count
            color_counts[color_name] = color_counts.get(color_name, 0) + 1

            # Calculate cell position with padding
            x = grid_start_x + j * grid_size
            y = grid_start_y + i * grid_size

            # Draw filled rectangle for the brick
            draw.rectangle(
                [x + 2, y + 2, x + grid_size - 2, y + grid_size - 2], fill=hex_color
            )

            # Add brick coordinates in bold
            text = f"{i},{j}"
            # Get text size for centering
            text_bbox = draw.textbbox((0, 0), text, font=bold_font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]

            # Center text in cell
            text_x = x + (grid_size - text_width) // 2
            text_y = y + (grid_size - text_height) // 2

            # Draw text with outline for better visibility
            text_color = "white" if sum(rgb_color) < 384 else "black"
            draw.text((text_x, text_y), text, font=bold_font, fill=text_color)

            # Add indicator for transparent/water bricks
            if is_water:
                draw.rectangle(
                    [x + 2, y + 2, x + grid_size - 2, y + grid_size - 2],
                    outline="cyan",
                    width=2,
                )

        # Add legend if requested
        if include_legend and color_counts:
            legend_x = grid_start_x + grid_dims[1] * grid_size + 10
            legend_y = grid_start_y
            legend_square_size = int(grid_size / 2)

            # Draw legend title
            draw.text(
                (legend_x, legend_y), "Brick Colors:", font=bold_font, fill="black"
            )
            legend_y += int(grid_size / 2)

            # Sort colors by count
            sorted_colors = sorted(color_counts.items(), key=lambda x: (-x[1], x[0]))

            for color_name, count in sorted_colors:
                hex_color = lego_colors[color_name]["hex"]

                # Draw color square
                draw.rectangle(
                    [
                        legend_x,
                        legend_y,
                        legend_x + legend_square_size,
                        legend_y + legend_square_size,
                    ],
                    fill=hex_color,
                )

                # Draw text
                text = f"{color_name}: {count}"
                draw.text(
                    (legend_x + legend_square_size + 5, legend_y),
                    text,
                    font=font,
                    fill="black",
                )

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
            band_names=["R", "G", "B"],
            metadata={
                "instruction_metadata": {
                    "total_bricks": sum(color_counts.values()),
                    "color_counts": color_counts,
                    "x_position": tile_info["x"],
                    "y_position": tile_info["y"],
                    "tile_stage": tile_info["stage"],
                },
                "brick_info": brick_info,
            },
        )

    # Process each image in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _generate_instruction_image(img_data)
    return result


def get_brick_quantities(data: RasterStack) -> Dict:
    """Get quantities of LEGO bricks needed for a legofied image.

    Args:
        data: RasterStack containing legofied image with brick information

    Returns:
        Dictionary with FeatureCollection containing brick quantities and details
    """

    def _get_brick_quantities(img_data: ImageData) -> Dict:
        if "brick_info" not in img_data.metadata:
            raise ValueError(
                "Input image must be legofied with brick information in metadata"
            )

        # Collect brick counts from metadata
        brick_counts: Dict[str, int] = {}
        for tile_info in img_data.metadata["brick_info"]["pixels"].values():
            color_name = tile_info["color_name"]
            brick_counts[color_name] = brick_counts.get(color_name, 0) + 1

        # Create a FeatureCollection for brick quantities
        features = []
        for color_name, count in sorted(brick_counts.items()):
            feature = {
                "type": "Feature",
                "properties": {
                    "color": color_name,
                    "pantone": lego_colors[color_name]["pantone"],
                    "hex": lego_colors[color_name]["hex"],
                    "transparent": lego_colors[color_name].get("transparent", False),
                    "values": {"quantity": count},
                },
            }
            features.append(feature)

        return {"type": "FeatureCollection", "features": features}

    # Process each image in the RasterStack
    result = {}
    for key, img_data in data.items():
        result[key] = _get_brick_quantities(img_data)

    # If there's only one image, return just that result
    if len(result) == 1:
        return next(iter(result.values()))
    return result
