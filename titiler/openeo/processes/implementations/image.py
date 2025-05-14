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
import xarray

# Load the IMERG water mask from NetCDF file
WATER_MASK_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", 
                               "notebooks", "IMERG_land_sea_mask.nc.gz")

try:
    # Open the NetCDF file and extract the water mask data
    with xarray.open_dataset(WATER_MASK_PATH) as ds:
        # Get the water mask variable (landseamask)
        var_name = list(ds.data_vars)[0]  # Should be 'landseamask'
        _WATER_MASK = ds[var_name].values
        
        # Store coordinate information for proper lat/lon conversion
        _LAT_START = float(ds.lat.values.min())  # Should be -89.95
        _LAT_RES = 0.1
        _LAT_SIZE = len(ds.lat)
        
        _LON_START = float(ds.lon.values.min())  # Should be -0.05
        _LON_RES = 0.1
        _LON_SIZE = len(ds.lon)
except Exception as e:
    print(f"Error loading water mask: {e}")
    # Fallback empty water mask if file not found
    _WATER_MASK = numpy.zeros((1800, 3600), dtype=numpy.float32)
    _LAT_START = -89.95
    _LAT_RES = 0.1
    _LAT_SIZE = 1800
    _LON_START = -0.05
    _LON_RES = 0.1
    _LON_SIZE = 3600


def lat_to_index(lat):
    """Convert latitude to array index in the water mask.
    
    Args:
        lat: Latitude in degrees (-90 to 90)
    
    Returns:
        Index in the array
    """
    # Calculate index (from south to north, -90 to 90)
    idx = int(round((lat - _LAT_START) / _LAT_RES))
    # Ensure within bounds
    return max(0, min(idx, _LAT_SIZE - 1))


def lon_to_index(lon):
    """Convert longitude to array index in the water mask.
    
    Args:
        lon: Longitude in degrees (-180 to 180)
    
    Returns:
        Index in the array
    """
    # First normalize longitude to 0-360 range
    lon_360 = lon % 360 if lon >= 0 else (lon + 360)
    # Calculate index
    idx = int(round((lon_360 - _LON_START) / _LON_RES))
    # Handle edge case for longitude 359.95
    if idx >= _LON_SIZE:
        idx = 0
    # Ensure within bounds
    return max(0, min(idx, _LON_SIZE - 1))

from .data_model import ImageData, RasterStack

__all__ = [
    "image_indexes",
    "to_array",
    "color_formula",
    "colormap",
    "get_colormap",
    "legofication",
    "generate_subtiles_ref",
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


def _get_water_mask_for_bounds(bounds, crs, shape):
    """Get water mask for the given bounds, converting from the source CRS to lat/lon.
    
    Args:
        bounds: Bounds in the source CRS (minx, miny, maxx, maxy) or (left, bottom, right, top)
        crs: Source coordinate reference system
        shape: Shape of the target mask (height, width)
        
    Returns:
        Water mask array resampled to the target shape
    """
    import pyproj
    
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
        
        # Get indices in the water mask array
        # Remember: water mask is oriented with origin at top-left (-90°, 0°)
        north_idx = lat_to_index(north)
        south_idx = lat_to_index(south)
        west_idx = lon_to_index(west)
        east_idx = lon_to_index(east)
        
        # Ensure south_idx is always greater than north_idx (since array has origin at top)
        if south_idx < north_idx:
            south_idx, north_idx = north_idx, south_idx
    
        # Debug info
        print(f"Bounds in lat/lon: {west}, {south}, {east}, {north}")
        print(f"Indices: north={north_idx}, south={south_idx}, west={west_idx}, east={east_idx}")
            
        # Handle bounds that cross the antimeridian (international date line)
        if west_idx > east_idx:
            # Get two portions of the water mask (one on each side of 180/-180)
            west_portion = _WATER_MASK[north_idx:south_idx, west_idx:]
            east_portion = _WATER_MASK[north_idx:south_idx, :east_idx]
            mask = numpy.concatenate([west_portion, east_portion], axis=1)
        else:
            # Get the water mask for the requested region
            mask = _WATER_MASK[north_idx:south_idx, west_idx:east_idx]
    
        # Ensure we have a valid mask with at least 1 pixel
        if mask.shape[0] == 0 or mask.shape[1] == 0:
            print(f"Warning: Empty mask region ({mask.shape}), returning zeros")
            return numpy.zeros(shape, dtype=numpy.float32)
        
        # Convert mask to float32 if needed
        if mask.dtype != numpy.float32:
            mask = mask.astype(numpy.float32)
        
        # Resize to the target shape
        from PIL import Image
        return numpy.array(Image.fromarray(mask).resize(
            (shape[1], shape[0]), 
            resample=Image.BILINEAR
        ))
    
    except Exception as e:
        print(f"Error processing water mask: {e}")
        # Return zeros array as fallback
        return numpy.zeros(shape, dtype=numpy.float32)


def _legofication(data: ImageData, nbbricks: int = 16, bricksize: int = 16, water_threshold: float = 75.0) -> ImageData:
    """Apply legofication to ImageData by converting the image to LEGO colors and adding brick effects.
    
    Args:
        data: ImageData to process
        nbbricks: Number of LEGO bricks for the smallest image dimension
        bricksize: Size of each LEGO brick in pixels
        water_threshold: Percentage threshold for water classification (default 75%)
    """

    def _compress(img: ImageData, nbbricks: int = 16) -> ImageData:
        min_side = min(img.array.shape[-2:])
        new_shape = numpy.round(
            numpy.array(img.array.shape[-2:]) / min_side * nbbricks
        ).astype(int)
        return img.resize(new_shape[0], new_shape[1], resampling_method="bilinear")

    def _upscale(img: ImageData, bricksize: int = 16) -> ImageData:
        # Store water pixels information before resizing
        water_pixels = getattr(img.array, '_water_pixels', set())
        
        # Calculate new dimensions
        new_shape = (bricksize * numpy.array(img.array.shape[-2:])).astype(int)
        
        # Resize the image
        upscaled_img = img.resize(new_shape[0], new_shape[1], resampling_method="nearest")
        
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
            
            # Debug information
            print(f"Original water pixels: {len(water_pixels)}")
            print(f"Upscaled water pixels: {len(upscaled_water_pixels)}")
        
        return upscaled_img

    def _brickification(img: ImageData, nblocks: Tuple[int, int]) -> ImageData:
        nmin = numpy.min(nblocks)
        d = (numpy.min(numpy.array(img.array.data.shape[-2:])) // nmin) / 2
        
        # Track water pixels (they're already in upscaled coordinates after _upscale)
        water_pixels = getattr(img.array, '_water_pixels', set())
        
        # Calculate center coordinates for each brick in upscaled image
        for i in range(nblocks[0]):
            for j in range(nblocks[1]):
                xc = round(d + 2 * d * i)
                yc = round(d + 2 * d * j)
                cur_values = img.array.data[:, xc, yc].copy()
                
                # Calculate the actual pixel coordinates in the upscaled image
                # to check against water_pixels
                is_water = (xc, yc) in water_pixels
                
                # Debug info - print some water pixels if available
                if i == 0 and j == 0 and water_pixels:
                    print(f"First few water pixels: {list(water_pixels)[:5]}")
                    print(f"Current coordinates: ({xc}, {yc}), is_water: {is_water}")
                    
                    # Check a range around this pixel
                    water_found = False
                    for y_check in range(max(0, xc-10), min(img.array.data.shape[1], xc+10)):
                        for x_check in range(max(0, yc-10), min(img.array.data.shape[2], yc+10)):
                            if (y_check, x_check) in water_pixels:
                                water_found = True
                                print(f"Found water pixel near current brick: ({y_check}, {x_check})")
                                break
                        if water_found:
                            break
                    
                    if not water_found:
                        print("No water pixels found near this brick")
                
                # Different rendering for transparent water bricks
                if is_water:
                    # For transparent bricks:
                    # 1. Make the brick more "glassy" by brightening it
                    # 2. Add stronger specular highlights
                    # 3. Reduce the contrast of shading
                    
                    # Light the top-left edge with a stronger specular highlight
                    rr, cc = disk(
                        (xc - 2, yc - 2), 0.7 * d, shape=img.array.data.shape[::-1]
                    )
                    for b in range(img.array.data.shape[0]):
                        # Stronger highlight for transparent bricks (75% white)
                        img.array.data[b, rr, cc] = (
                            img.array.data[b, rr, cc] * 0.25 + 220 * 0.75
                        ).astype(img.array.data.dtype)
                    
                    # Add subtle internal reflection on bottom-right
                    rr, cc = disk(
                        (xc + 2, yc + 2), 0.5 * d, shape=img.array.data.shape[::-1]
                    )
                    for b in range(img.array.data.shape[0]):
                        # Less darkening for transparent bricks
                        img.array.data[b, rr, cc] = (
                            img.array.data[b, rr, cc] * 0.7 + 40 * 0.3
                        ).astype(img.array.data.dtype)
                    
                    # Make the stud more reflective/transparent
                    rr, cc = disk((xc, yc), 0.65 * d, shape=img.array.data.shape[::-1])
                    for b in range(img.array.data.shape[0]):
                        # Lighten the stud color to make it look glassy
                        img.array.data[b, rr, cc] = (
                            cur_values[b] * 0.7 + 180 * 0.3
                        ).astype(img.array.data.dtype)
                    
                    # Add a bright specular highlight to the stud
                    rr, cc = disk((xc-1, yc-1), 0.2 * d, shape=img.array.data.shape[::-1])
                    for b in range(img.array.data.shape[0]):
                        img.array.data[b, rr, cc] = (
                            img.array.data[b, rr, cc] * 0.2 + 250 * 0.8
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
            shape=(shape[1], shape[2])  # Height, width
        )

        for i in range(shape[1]):
            for j in range(shape[2]):
                rgb_pixel = rgb_data[:, i, j]
                # Check if this is a water area that should use transparent bricks
                is_water = water_mask[i, j] > water_threshold
                
                # Get the appropriate LEGO color for this pixel
                lego_color_name, lego_rgb = find_best_lego_color(rgb_pixel, is_water)
                
                # Store information about transparent bricks for later processing
                if is_water:
                    # For water areas, store the water percentage to control transparency effect
                    # Higher water percentage = more transparent
                    rgb_data[:, i, j] = lego_rgb
                    # Tag this pixel as water/transparent in the alpha channel metadata
                    # We'll store this in the mask of the array later
                    if not hasattr(small_img.array, '_water_pixels'):
                        small_img.array._water_pixels = set()
                    small_img.array._water_pixels.add((i, j))
                else:
                    # For land areas, use normal color
                    rgb_data[:, i, j] = lego_rgb

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
        "transparent": True
    },
    "Transparent Water Blue Dark": {
        "hsl": [210, 90, 30],
        "rgb": [8, 45, 82],
        "pantone": "534 C",
        "hex": "#082D52",
        "transparent": True
    },
    # Regular colors
    "White": {
        "hsl": [0, 0, 96],
        "rgb": [244, 244, 244],
        "pantone": "TBC",
        "hex": "#F4F4F4",
        "transparent": False
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
    },
    "Coral (Vibrant Coral)": {
        "hsl": [354, 100, 67],
        "rgb": [255, 88, 105],
        "pantone": "2346 C",
        "hex": "#FF5869",
    },
    "Red (Bright Red)": {
        "hsl": [352, 100, 40],
        "rgb": [205, 0, 26],
        "pantone": "3546 C",
        "hex": "#CD001A",
    },
    "Dark Red (New Dark Red)": {
        "hsl": [0, 53, 36],
        "rgb": [138, 43, 43],
        "pantone": "7623 C",
        "hex": "#8A2B2B",
    },
    "Reddish Brown": {
        "hsl": [10, 47, 33],
        "rgb": [124, 58, 45],
        "pantone": "7594 C",
        "hex": "#7C3A2D",
    },
    "Dark Brown": {
        "hsl": [358, 33, 19],
        "rgb": [63, 32, 33],
        "pantone": "4975 C",
        "hex": "#3F2021",
    },
    "Light Nougat": {
        "hsl": [18, 60, 81],
        "rgb": [236, 195, 178],
        "pantone": "489 C",
        "hex": "#ECC3B2",
    },
    "Medium Tan (Warm Tan)": {
        "hsl": [32, 100, 74],
        "rgb": [255, 194, 123],
        "pantone": "149 C",
        "hex": "#FFC27B",
    },
    "Nougat": {
        "hsl": [25, 70, 66],
        "rgb": [229, 158, 109],
        "pantone": "472 C",
        "hex": "#E59E6D",
    },
    "Medium Nougat": {
        "hsl": [29, 55, 52],
        "rgb": [200, 130, 66],
        "pantone": "722 C",
        "hex": "#C88242",
    },
    "Orange (Bright Orange)": {
        "hsl": [31, 100, 50],
        "rgb": [255, 130, 0],
        "pantone": "151 C",
        "hex": "#FF8200",
    },
    "Dark Orange": {
        "hsl": [27, 100, 37],
        "rgb": [190, 84, 0],
        "pantone": "2020 C",
        "hex": "#BE5400",
    },
    "Medium Brown": {
        "hsl": [25, 38, 33],
        "rgb": [120, 81, 53],
        "pantone": "7568 C",
        "hex": "#785135",
    },
    "Bright Light Yellow (Cool Yellow)": {
        "hsl": [48, 91, 73],
        "rgb": [249, 226, 125],
        "pantone": "2002 C",
        "hex": "#F9E27D",
    },
    "Yellow (Bright Yellow)": {
        "hsl": [48, 100, 50],
        "rgb": [255, 205, 0],
        "pantone": "116 C",
        "hex": "#FFCD00",
    },
    "Bright Light Orange (Flame Yellowish Orange)": {
        "hsl": [43, 100, 50],
        "rgb": [255, 182, 0],
        "pantone": "2010 C",
        "hex": "#FFB600",
    },
    "Neon Yellow (Vibrant Yellow)": {
        "hsl": [59, 100, 50],
        "rgb": [255, 252, 0],
        "pantone": "TBC",
        "hex": "#FFFC00",
    },
    "Yellowish Green (Spring Yellowish Green)": {
        "hsl": [76, 72, 71],
        "rgb": [205, 234, 128],
        "pantone": "373 C",
        "hex": "#CDEA80",
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


def find_best_lego_color(rgb: numpy.ndarray, use_transparent: bool = False) -> Tuple[str, numpy.ndarray]:
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
        name: info for name, info in lego_colors.items()
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


def legofication(data: RasterStack, nbbricks: int = 16, bricksize: int = 16, water_threshold: float = 75.0) -> RasterStack:
    """Apply legofication to RasterStack by converting images to LEGO colors and adding brick effects.

    Args:
        data: RasterStack to process
        nbbricks: Number of LEGO bricks for the smallest image dimension
        bricksize: Size of each LEGO brick in pixels
        water_threshold: Percentage threshold for water classification (default 75%)

    Returns:
        RasterStack with legofication applied
    """
    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _legofication(img_data, nbbricks, bricksize, water_threshold)
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
