"""titiler.openeo processed reduce.

CRITICAL DEVELOPER WARNING - REDUCER INVOCATION PATTERNS
=========================================================

When implementing dimension reduction functions in this module, it is CRITICAL to understand
that reducer functions may maintain internal state, caching, or other stateful behavior.
This means you MUST call each reducer function EXACTLY ONCE per reduction operation.

KEY REQUIREMENTS:
-----------------
1. **NEVER iterate and call the reducer multiple times** on individual items
   ❌ BAD: for item in data: result = reducer(data=item)
   ✅ GOOD: stacked = stack_all_data(data); result = reducer(data=stacked)

2. **Stack/combine all data FIRST, then call reducer ONCE**
   - For spectral reduction across time: Stack to (time, bands, h, w), transpose to
     (bands, time, h, w), call reducer ONCE
   - For temporal reduction: Stack to (time, bands, h, w), call reducer ONCE

3. **Understand the reduction axis**
   - Spectral reduction: Reduce across BANDS (axis depends on how data is stacked)
   - Temporal reduction: Reduce across TIME dimension
   - Always ensure the reducer operates on the correct axis

4. **Why this matters:**
   - Some reducers maintain caches of computed values
   - Calling a reducer multiple times can return incorrect/stale results
   - This bug has occurred MULTIPLE TIMES and caused production issues

5. **Testing requirements:**
   - Always test with a stateful/caching reducer
   - Verify reducer is called exactly once (use mock.call_count)
   - Test with multiple time slices and multiple bands

If you're modifying _reduce_spectral_dimension_stack or _reduce_temporal_dimension,
READ THIS ENTIRE WARNING CAREFULLY before making changes.
"""

import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

import numpy
from openeo_pg_parser_networkx.pg_schema import TemporalInterval, TemporalIntervals
from rasterio.crs import CRS
from rio_tiler.models import ImageData
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.types import BBox
from rio_tiler.utils import resize_array

from .data_model import ImageRef, RasterStack, _normalize_to_naive_utc

logger = logging.getLogger(__name__)

__all__ = ["aggregate_temporal", "apply_pixel_selection", "reduce_dimension"]

# Mapping of reducer function names to their corresponding PixelSelectionMethod names
# This enables _reduce_temporal_dimension to use the efficient streaming approach
#
# NOTE: 'first' and 'last' from math.py are NOT included here because they have
# different semantics:
# - math.py first/last = first/last item in temporal order (chronologically)
# - PixelSelectionMethod first = first available (non-masked) pixel value
#
# Use 'firstpixel' reducer for pixel-wise first valid value behavior.
# Note: rio_tiler doesn't have a 'last' PixelSelectionMethod, so 'lastpixel'
# uses a fallback array-based implementation (not in this mapping).
# Canonical key used as the RasterStack key for temporally-reduced cubes.
# After reduce_dimension("t") the temporal dimension no longer exists per the
# openEO spec. In our RasterStack model we still need *some* key, but it must
# be the SAME for every reduced cube so that merge_cubes treats two reduced
# cubes as having the same (single) temporal label and merges them at the band
# level rather than keeping them as separate temporal slices.
# A date from 1900 is safely before any real satellite acquisition and trivially
# distinct from any user-supplied aggregate_temporal label.
REDUCED_TEMPORAL_SENTINEL = datetime(1900, 1, 1)

PIXEL_SELECTION_REDUCERS = {
    "firstpixel": "first",  # first available pixel (fills masked values)
    "mean": "mean",
    "median": "median",
    "sd": "stdev",  # openEO uses 'sd' for standard deviation
    "stdev": "stdev",
    "count": "count",
    "highestpixel": "highest",
    "lowestpixel": "lowest",
    "lastbandlow": "lastbandlow",
    "lastbandhight": "lastbandhight",
}


class DimensionNotAvailable(Exception):
    """Exception raised when a dimension is not available."""

    def __init__(self, dimension: str):
        self.dimension = dimension
        super().__init__(
            f"A dimension with the specified name '{dimension}' does not exist."
        )


def _compute_aggregated_cutline_mask(
    cutline_masks: List[Optional[numpy.ndarray]],
) -> Optional[numpy.ndarray]:
    """Compute combined cutline mask from a list of individual masks.

    Combines masks using OR logic: a pixel is valid (False) if ANY image has it valid.
    If any image has no cutline_mask (None), all its pixels are considered valid,
    which means the aggregated result should indicate all pixels are valid.

    Args:
        cutline_masks: List of cutline_mask arrays (or None)

    Returns:
        Combined cutline mask, or None if all pixels are valid
    """
    if not cutline_masks:
        return None

    # If any image has no cutline_mask, all its pixels are valid
    # With OR logic, this means all pixels in the aggregate are valid
    if any(m is None for m in cutline_masks):
        return None

    # All masks are valid arrays at this point - filter to satisfy mypy
    valid_masks: List[numpy.ndarray] = [m for m in cutline_masks if m is not None]

    # Start with first mask
    aggregated = valid_masks[0].copy()

    # OR combination: pixel is valid (False) if ANY image has it valid
    # Since True = outside footprint, minimum gives the union of valid areas
    for mask in valid_masks[1:]:
        aggregated = numpy.minimum(aggregated, mask)

    return aggregated


def _create_pixel_selection_result(
    pixsel_method,
    assets_used: List,
    crs: Optional[CRS],
    bounds: Optional[BBox],
    band_names: Optional[List[str]],
    pixel_selection: str,
) -> RasterStack:
    """Create the final pixel selection result."""
    result_img = ImageData(
        pixsel_method.data,
        assets=assets_used,
        crs=crs,
        bounds=bounds,
        band_descriptions=band_names if band_names is not None else [],
        metadata={"pixel_selection_method": pixel_selection},
    )
    return RasterStack.from_images({REDUCED_TEMPORAL_SENTINEL: result_img})


def _collect_images_from_data(
    data: RasterStack,
) -> List[Tuple[datetime, ImageRef]]:
    """Collect all image references from a RasterStack.

    This function ALWAYS returns ImageRef instances. RasterStack should always
    have image refs available when created with proper dimensions.

    This enables deferred execution and cutline mask computation without loading
    actual pixel data, while still providing a uniform interface.

    Args:
        data: A RasterStack

    Returns:
        List of (datetime, ImageRef) tuples in temporal order
    """
    # Get ImageRef instances - RasterStack should always have them
    image_refs = data.get_image_refs()
    if image_refs:
        return image_refs

    # Fallback: create ImageRef from pre-loaded images
    # This handles RasterStacks created without dimension info
    result: List[Tuple[datetime, ImageRef]] = []
    for key in data.keys():
        try:
            result.append((key, ImageRef.from_image(image=data[key])))
        except KeyError:
            continue
    return result


def _feed_image_to_pixsel(
    img: ImageData,
    pixsel_method: Any,
) -> None:
    """Feed an image to the pixel selection method, handling size differences."""
    if any([img.width != pixsel_method.width, img.height != pixsel_method.height]):
        logging.warning(
            "Cannot concatenate images with different size. Will resize using first asset width/height"
        )
        h = pixsel_method.height
        w = pixsel_method.width
        pixsel_method.feed(
            numpy.ma.MaskedArray(
                resize_array(img.array.data, h, w),
                mask=resize_array(img.array.mask * 1, h, w).astype("bool"),
            )
        )
    else:
        pixsel_method.feed(img.array)


def apply_pixel_selection(
    data: RasterStack,
    pixel_selection: str = "first",
) -> RasterStack:
    """Apply PixelSelection method on a RasterStack with timestamp-based grouping.

    This function first collects all image refs and computes the aggregated cutline mask
    (without executing tasks for lazy refs), then processes images for pixel selection.
    This ensures early termination works correctly by knowing upfront which pixels will
    be covered by any image.

    All items are accessed through the ImageRef interface, which provides uniform access
    to metadata and data regardless of whether the image is lazy or pre-loaded.

    Returns:
        RasterStack: A single-image RasterStack containing the result of pixel selection
    """
    pixsel_method = PixelSelectionMethod[pixel_selection].value()
    assets_used: List = []
    crs: Optional[CRS] = None
    bounds: Optional[BBox] = None
    band_names: Optional[List[str]] = None

    # Collect all image refs (without executing tasks for lazy refs)
    all_items = _collect_images_from_data(data)

    if not all_items:
        raise ValueError("Method returned an empty array")

    # Compute aggregated cutline mask from all refs (using metadata, not pixel data)
    cutline_masks = [ref.cutline_mask() for _, ref in all_items]
    aggregated_cutline = _compute_aggregated_cutline_mask(cutline_masks)

    # Initialize pixsel_method from the first image.
    #
    # The band COUNT must come from the first *realized* image, not from declared
    # metadata: a band-name list (e.g. a single asset name produced by
    # load_collection's default) can map to a different number of decoded bands
    # when the asset is multi-band. Seeding the count from declared metadata makes
    # the per-slice assertion below fail on the very first slice even when every
    # slice is perfectly consistent. Width/height/cutline stay metadata-derived so
    # they remain aligned with the precomputed cutline mask.
    first_key, first_ref = all_items[0]
    first_img = first_ref.realize()
    pixsel_method.width = first_ref.width
    pixsel_method.height = first_ref.height
    pixsel_method.count = first_img.count
    crs = first_ref.crs
    bounds = first_ref.bounds
    band_names = (
        list(first_img.band_descriptions)
        if first_img.band_descriptions
        else first_ref.band_names
    )

    # Set the aggregated cutline mask
    if aggregated_cutline is not None:
        pixsel_method.cutline_mask = aggregated_cutline

    # Process items for pixel selection
    for key, ref in all_items:
        # Realize the image (execute task for lazy refs, return cached for eager refs)
        img = ref.realize()

        # Validate band count
        assert (
            img.count == pixsel_method.count
        ), "Assets HAVE TO have the same number of bands"

        _feed_image_to_pixsel(img, pixsel_method)
        assets_used.append(key)

        # Early termination check
        if pixsel_method.is_done and pixsel_method.data is not None:
            break

    if pixsel_method.data is None:
        raise ValueError("Method returned an empty array")

    return _create_pixel_selection_result(
        pixsel_method, assets_used, crs, bounds, band_names, pixel_selection
    )


def _reduce_temporal_dimension(
    data: RasterStack,
    reducer: Callable,
) -> RasterStack:
    """Reduce the temporal dimension of a RasterStack.

    This function uses the efficient streaming approach via PixelSelectionMethod
    for supported reducers (first, mean, median, stdev, count, highest, lowest,
    lastbandlow, lastbandhight). For custom reducers, it falls back to loading
    all data and applying the reducer.

    Args:
        data: A RasterStack with temporal dimension
        reducer: A reducer function to apply on the temporal dimension

    Returns:
        A RasterStack with a single reduced temporal result

    Raises:
        ValueError: If the data is not a valid RasterStack or reducer doesn't return expected format
    """
    # Validate input - must be a non-empty RasterStack
    if not data:
        raise ValueError(
            "Expected a non-empty RasterStack for temporal dimension reduction"
        )

    # Note: The reducer will determine how much data it actually needs
    # Some reducers might be able to work with partial data
    reduced_array = reducer(data=data)

    # Validate the reducer output - must NOT be a RasterStack or dict
    if isinstance(reduced_array, dict):
        raise ValueError(
            "The reducer must return an array-like object for temporal dimension reduction, "
            "not a RasterStack (dict). The reducer should collapse the temporal dimension "
            "and return the resulting array directly."
        )

    # Validate array-like output while preserving MaskedArray.
    # IMPORTANT: We must NOT use numpy.asarray() on MaskedArray because it strips
    # the mask, losing nodata information. This would break transparency in outputs.
    if not isinstance(reduced_array, (numpy.ndarray, numpy.ma.MaskedArray)):
        try:
            reduced_array = numpy.asarray(reduced_array)
        except (TypeError, ValueError) as e:
            reducer_type = type(reduced_array).__name__
            raise ValueError(
                f"The reducer must return an array-like object for temporal dimension reduction, "
                f"but returned {reducer_type} which cannot be converted to an array. "
                f"Expected array-like data with dimensions like (bands, height, width) or (height, width)."
            ) from e

    # Get metadata from first ImageRef WITHOUT loading pixel data
    image_refs = data.get_image_refs()
    if not image_refs:
        raise ValueError("No image refs available for metadata")
    first_key, first_ref = image_refs[0]

    reduced_img = ImageData(
        reduced_array,  # Use the reduced array directly since it's already collapsed
        assets=[first_key],
        crs=first_ref.crs,
        bounds=first_ref.bounds,
        band_descriptions=first_ref.band_names,
        metadata={
            "reduced_dimension": "temporal",
            "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
        },
    )
    return RasterStack.from_images({REDUCED_TEMPORAL_SENTINEL: reduced_img})


def _reshape_reduced_spectral_data(
    reduced_data: numpy.ndarray, num_images: int
) -> numpy.ndarray:
    """Transform reducer output back to (time, ...) format for splitting.

    Args:
        reduced_data: Output from the spectral reducer
        num_images: Number of time slices expected

    Returns:
        Array reshaped to (time, ...) format
    """
    if reduced_data.ndim == 3:
        # Output is (time, height, width) - fully reduced spectral dimension
        return reduced_data
    elif reduced_data.ndim == 4:
        # Output is (reduced_bands, time, height, width) - partial spectral reduction
        # Move time axis back to position 0: (time, reduced_bands, height, width)
        return numpy.moveaxis(reduced_data, 1, 0)
    elif reduced_data.ndim == 2:
        # Output is (time, height) or (height, width) - need to determine which
        # If first dimension matches number of images, it's (time, height)
        if reduced_data.shape[0] == num_images:
            # Add width dimension: (time, height, 1)
            return reduced_data[:, :, numpy.newaxis]
        else:
            # Assume it's (height, width), expand for time: (1, height, width)
            expanded = reduced_data[numpy.newaxis, :, :]
            # Repeat for all time slices
            return numpy.repeat(expanded, num_images, axis=0)
    else:
        # Unexpected dimensionality - try to infer
        logging.warning(
            f"Unexpected reducer output shape {reduced_data.shape} for spectral reduction. "
            f"Expected 3D (time, height, width) or 4D (time, bands, height, width). "
            f"Attempting to reshape..."
        )
        # If first dimension matches images, assume it's already correct
        if reduced_data.shape[0] == num_images:
            return reduced_data
        else:
            # Try to move second dimension to first
            return (
                numpy.moveaxis(reduced_data, 1, 0)
                if reduced_data.ndim > 1
                else reduced_data
            )


def _determine_output_band_count(final_data: numpy.ndarray) -> int:
    """Determine the number of output bands from the final reduced data.

    Args:
        final_data: The reshaped data in (time, ...) format

    Returns:
        Number of output bands (0 if cannot be determined)
    """
    if final_data.ndim >= 1 and len(final_data) > 0:
        sample_slice = final_data[0] if final_data.ndim > 2 else final_data
        if sample_slice.ndim == 2:
            # (height, width) - single band output
            return 1
        elif sample_slice.ndim == 3:
            # (bands, height, width) - multi-band output
            return sample_slice.shape[0]
    return 0


def _reduce_spectral_dimension_stack(
    data: RasterStack,
    reducer: Callable,
) -> RasterStack:
    """Reduce the spectral dimension of a RasterStack.

    CRITICAL: This function calls the reducer EXACTLY ONCE to handle reducers with
    internal state/caching. All images are stacked first, then the reducer is invoked
    once on the combined data. DO NOT MODIFY to call reducer per-image.

    Args:
        data: A RasterStack with spectral dimension
        reducer: A reducer function to apply on the spectral dimension.
                 The reducer will be called ONCE with shape (bands, time, height, width)
                 and must reduce along axis 0 (bands).

    Returns:
        A RasterStack with the spectral dimension reduced for each image

    Raises:
        ValueError: If the reducer doesn't return valid data
    """
    # Validate input - must be a non-empty RasterStack
    if not data:
        raise ValueError(
            "Expected a non-empty RasterStack for spectral dimension reduction"
        )

    # Load all images first and collect metadata
    images = []
    keys = []
    metadata_list = []

    for key in data.keys():
        try:
            img = data[key]  # Access each image individually
            images.append(img.array)
            keys.append(key)
            metadata_list.append(
                {
                    "crs": img.crs,
                    "bounds": img.bounds,
                    "band_descriptions": img.band_descriptions
                    if img.band_descriptions is not None
                    else [],
                    "assets": [key],
                }
            )
        except KeyError as e:
            # Log task failures but continue processing other keys
            logging.warning(
                "Failed to load data for key '%s' during spectral dimension reduction: %s. "
                "This may be due to task execution failure in lazy loading. Skipping this item.",
                key,
                str(e),
            )
            continue

    if not images:
        raise ValueError("No valid images loaded for spectral dimension reduction")

    # CRITICAL: Stack all images FIRST, then call reducer ONCE
    # This is required for reducers with internal caching/state
    # Stack all images along a new temporal dimension (axis 0)
    # Shape will be (time, bands, height, width).
    # Use numpy.ma.stack (not numpy.stack) so per-image nodata masks are preserved;
    # plain numpy.stack silently drops them, turning masked nodata into valid data.
    stacked_data = numpy.ma.stack(images, axis=0)

    # To reduce the spectral dimension while maintaining time separation,
    # we move the bands axis to the front: (bands, time, height, width)
    # This allows the reducer to operate on the bands dimension (axis 0)
    transposed_data = numpy.moveaxis(stacked_data, 1, 0)

    # CRITICAL: Call the reducer EXACTLY ONCE with ALL the data
    # The reducer will reduce along axis 0 (bands dimension)
    # Input shape: (bands, time, height, width)
    # Expected output shape: (time, height, width) for full reduction
    #                    or: (reduced_bands, time, height, width) for partial reduction
    reduced_data = reducer(data=transposed_data)

    # Validate the reducer output - must NOT be a RasterStack or dict
    if isinstance(reduced_data, dict):
        raise ValueError(
            "The reducer must return an array-like object for spectral dimension reduction, "
            "not a RasterStack (dict). The reducer should process the spectral bands "
            "and return the resulting array directly."
        )

    # Validate array-like output while preserving MaskedArray.
    # IMPORTANT: We must NOT use numpy.asarray() on MaskedArray because it strips
    # the mask, losing nodata information. This would break transparency in outputs.
    if not isinstance(reduced_data, (numpy.ndarray, numpy.ma.MaskedArray)):
        try:
            reduced_data = numpy.asarray(reduced_data)
        except (TypeError, ValueError) as e:
            reducer_type = type(reduced_data).__name__
            raise ValueError(
                f"The reducer must return an array-like object for spectral dimension reduction, "
                f"but returned {reducer_type} which cannot be converted to an array. "
                f"Expected array-like data with reduced spectral bands."
            ) from e

    # Transform the result back to (time, ...) format for splitting into time slices
    final_data = _reshape_reduced_spectral_data(reduced_data, len(images))

    # Determine output band count for band_names handling
    num_output_bands = _determine_output_band_count(final_data)

    # Split the result back into individual ImageData objects
    result = {}
    for idx, (key, meta) in enumerate(zip(keys, metadata_list)):
        # Extract the data for this time slice
        if final_data.ndim >= 1 and len(final_data) > idx:
            time_slice_data = final_data[idx]
        else:
            time_slice_data = final_data

        # Adjust band_names to match output band count
        # If band count changed, clear band_names to avoid mismatches
        output_band_names = []
        if (
            num_output_bands > 0
            and meta["band_descriptions"]
            and len(meta["band_descriptions"]) == num_output_bands
        ):
            output_band_names = meta["band_descriptions"]

        result[key] = ImageData(
            time_slice_data,
            assets=meta["assets"],
            crs=meta["crs"],
            bounds=meta["bounds"],
            band_descriptions=output_band_names,
            metadata={
                "reduced_dimension": "spectral",
                "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
            },
        )

    return RasterStack.from_images(result)


class DistinctDimensionLabelsRequired(Exception):
    """Exception raised when dimension labels are not distinct."""

    def __init__(self):
        super().__init__(
            "The dimension labels have duplicate values. "
            "Distinct labels must be specified."
        )


class TemporalExtentEmpty(Exception):
    """Exception raised when a temporal interval is empty."""

    def __init__(self):
        super().__init__(
            "At least one of the intervals is empty. "
            "The second instant in time must always be greater/later than the first instant."
        )


def _parse_temporal_value(
    value: Optional[str],
) -> Optional[Union[datetime, time]]:
    """Parse a temporal string value into a datetime or time object.

    Handles RFC 3339 date-time strings, date-only strings, time-only strings
    (HH:MM:SS), and null values. Timezone-aware values are normalized to UTC
    and returned as naive datetimes.

    Args:
        value: An ISO 8601/RFC 3339 string, time-only string, or None.

    Returns:
        A datetime object, a time object (for HH:MM:SS), or None if value is None.
    """
    if value is None:
        return None
    # Try time-only format (HH:MM:SS) first
    if re.match(r"^\d{2}:\d{2}:\d{2}$", value):
        return time.fromisoformat(value)
    # Handle Z suffix
    if isinstance(value, str) and value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            try:
                dt = datetime.strptime(value, "%Y")
            except ValueError as e:
                raise ValueError(f"Invalid temporal value: {value}") from e
    # Normalize tz-aware datetimes to naive UTC
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_intervals(
    intervals: List[List[Optional[str]]],
) -> List[Tuple[Optional[Union[datetime, time]], Optional[Union[datetime, time]]]]:
    """Parse and validate temporal intervals.

    Args:
        intervals: Raw interval pairs as strings.

    Returns:
        List of parsed (start, end) tuples (datetime or time objects).

    Raises:
        TemporalExtentEmpty: If any non-time interval has end <= start.
        ValueError: If interval format is invalid or mixes types.
    """
    parsed: List[
        Tuple[Optional[Union[datetime, time]], Optional[Union[datetime, time]]]
    ] = []
    for interval in intervals:
        if len(interval) != 2:
            raise ValueError(
                f"Each temporal interval must have exactly two elements, got {len(interval)}"
            )
        start = _parse_temporal_value(interval[0])
        end = _parse_temporal_value(interval[1])
        # Validate: both must be same type (time-only or datetime)
        if isinstance(start, time) != isinstance(end, time):
            if start is not None and end is not None:
                raise ValueError(
                    "Cannot mix time-only and date/datetime in the same interval"
                )
        # For datetime intervals, end must be > start (except time-only wrap-around)
        if (
            start is not None
            and end is not None
            and isinstance(start, datetime)
            and isinstance(end, datetime)
            and end <= start
        ):
            raise TemporalExtentEmpty()
        parsed.append((start, end))
    return parsed


def _resolve_output_keys(
    labels: Optional[List[Union[float, str]]],
    intervals: List[
        Tuple[Optional[Union[datetime, time]], Optional[Union[datetime, time]]]
    ],
) -> List[datetime]:
    """Resolve output datetime keys from labels or interval starts.

    Args:
        labels: User-provided labels, or None.
        intervals: Parsed interval tuples.

    Returns:
        List of datetime keys for the output RasterStack.

    Raises:
        DistinctDimensionLabelsRequired: If resolved keys are not distinct.
        ValueError: If labels count doesn't match intervals.
    """
    num_intervals = len(intervals)
    if labels is not None and len(labels) > 0:
        if len(labels) != num_intervals:
            raise ValueError(
                f"Number of labels ({len(labels)}) must match "
                f"number of intervals ({num_intervals})"
            )
        output_keys: List[datetime] = []
        for idx, label in enumerate(labels):
            if isinstance(label, str):
                try:
                    parsed = _parse_temporal_value(label)
                    if isinstance(parsed, time):
                        # Time-only label: use synthetic key
                        output_keys.append(
                            datetime(1970, 1, 1) + timedelta(seconds=idx)
                        )
                    else:
                        output_keys.append(parsed or datetime.min)
                except ValueError:
                    # Non-temporal string label: use unique synthetic key per index
                    output_keys.append(datetime(1970, 1, 1) + timedelta(days=idx))
            elif isinstance(label, (int, float)):
                output_keys.append(datetime(1970, 1, 1) + timedelta(days=float(label)))
            else:
                raise ValueError(f"Unsupported label type: {type(label)}")
        # Validate uniqueness of resolved keys
        if len(set(output_keys)) < len(output_keys):
            raise DistinctDimensionLabelsRequired()
        return output_keys

    starts = [iv[0] if isinstance(iv[0], datetime) else None for iv in intervals]
    # Check distinctness across all start values, including None.
    # This allows a single open-start interval (start=None) but still
    # rejects any duplicated start value (including multiple None).
    if len(set(starts)) < len(starts):
        raise DistinctDimensionLabelsRequired()
    return [s if s is not None else datetime.min for s in starts]


def _timestamp_in_interval(
    ts: datetime,
    start: Optional[Union[datetime, time]],
    end: Optional[Union[datetime, time]],
) -> bool:
    """Check if a timestamp falls within [start, end).

    Handles datetime intervals, time-only intervals (with wrap-around),
    and open-ended intervals.

    Args:
        ts: The timestamp to check.
        start: Interval start (inclusive), or None for open.
        end: Interval end (exclusive), or None for open.

    Returns:
        True if the timestamp is within the interval.
    """
    # Time-only interval: compare by time-of-day
    if isinstance(start, time) and isinstance(end, time):
        ts_time = ts.time()
        if start <= end:
            # Normal interval: [start, end)
            return start <= ts_time < end
        else:
            # Wrap-around interval: [start, 24:00) or [00:00, end)
            return ts_time >= start or ts_time < end

    # Datetime interval: normalize all to naive UTC
    ts_utc = _normalize_to_naive_utc(ts)
    if start is not None and isinstance(start, datetime):
        s_utc = _normalize_to_naive_utc(start)
        if ts_utc < s_utc:
            return False
    if end is not None and isinstance(end, datetime):
        e_utc = _normalize_to_naive_utc(end)
        if ts_utc >= e_utc:
            return False
    return True


def _make_nodata_image(data: RasterStack) -> Optional[ImageData]:
    """Create a fully-masked nodata ImageData from the first image ref in the stack."""
    first_ref_list = data.get_image_refs()
    if not first_ref_list:
        return None
    _, first_ref = first_ref_list[0]
    nodata_array = numpy.ma.masked_all(
        (first_ref.count, first_ref.height, first_ref.width)
    )
    return ImageData(
        nodata_array,
        crs=first_ref.crs,
        bounds=first_ref.bounds,
        band_descriptions=first_ref.band_names,
    )


def _coerce_reduced_array(reduced_array: Any) -> numpy.ndarray:
    """Validate and coerce a reducer result to a numpy array."""
    if isinstance(reduced_array, dict):
        raise ValueError("The reducer must return an array-like object, not a dict.")
    if not isinstance(reduced_array, (numpy.ndarray, numpy.ma.MaskedArray)):
        try:
            reduced_array = numpy.asarray(reduced_array)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"The reducer must return an array-like object, "
                f"but returned {type(reduced_array).__name__}."
            ) from e
    return reduced_array


def _temporal_bound_to_str(value: Any) -> Optional[str]:
    """Serialize a single temporal-interval bound to an RFC 3339 string.

    Handles the raw strings/None passed by direct callers as well as the parsed
    wrapper objects (Year/Date/DateTime/Time) the openEO graph parser produces,
    whose parsed value lives in ``.root`` (a pendulum datetime/date/time).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    root = getattr(value, "root", value)
    return root.isoformat() if hasattr(root, "isoformat") else str(root)


def _interval_to_pair(interval: Any) -> List[Optional[str]]:
    """Normalize a single interval to a ``[start, end]`` list of RFC 3339 strings.

    Accepts a parser-produced ``TemporalInterval`` or a two-element list/tuple of
    strings/None. The length is preserved so downstream validation can still
    reject malformed intervals.
    """
    if isinstance(interval, TemporalInterval):
        return [
            _temporal_bound_to_str(interval.start),
            _temporal_bound_to_str(interval.end),
        ]
    if isinstance(interval, (list, tuple)):
        return [_temporal_bound_to_str(v) for v in interval]
    raise ValueError("Each temporal interval must be a two-element array.")


def _normalize_intervals(intervals: Any) -> List[List[Optional[str]]]:
    """Normalize the ``intervals`` argument to a list of string pairs.

    The openEO graph parser passes ``intervals`` as a ``TemporalIntervals`` whose
    bounds are already parsed into pendulum datetimes, while direct Python/test
    callers pass a list of two-element string lists. Both are reduced here to the
    string-pair form understood by :func:`_parse_intervals`.
    """
    if isinstance(intervals, TemporalIntervals):
        items: List[Any] = list(intervals)
    elif isinstance(intervals, (list, tuple)):
        items = list(intervals)
    else:
        raise ValueError("At least one temporal interval must be provided")
    return [_interval_to_pair(iv) for iv in items]


def _callback_results_cache(callback: Callable) -> Optional[Dict[Any, Any]]:
    """Return the openEO executor's shared ``results_cache`` backing a callback.

    The reducer/callback compiled by openeo_pg_parser_networkx is a
    ``functools.partial(node_callable, ...)`` whose closure holds the
    ``results_cache`` dict shared across the whole graph. We reach it so a process
    that must invoke the callback more than once (e.g. ``aggregate_temporal``, once
    per interval) can reset the callback's subgraph cache between calls — otherwise
    the executor memoizes each node by id and every call returns the FIRST call's
    result. Returns ``None`` if the structure can't be introspected (degrade
    gracefully).
    """
    func = getattr(callback, "func", callback)  # unwrap functools.partial
    freevars = getattr(getattr(func, "__code__", None), "co_freevars", ()) or ()
    closure = getattr(func, "__closure__", None) or ()
    for name, cell in zip(freevars, closure):
        if name == "results_cache" and isinstance(cell.cell_contents, dict):
            return cell.cell_contents
    return None


def _reset_results_cache(
    cache: Optional[Dict[Any, Any]], baseline: Dict[Any, Any]
) -> None:
    """Reset a callback's shared results_cache to ``baseline`` (no-op if None).

    Used to force a per-call recompute (e.g. ``aggregate_temporal`` per interval)
    while preserving upstream cache entries captured in ``baseline``.
    """
    if cache is None:
        return
    cache.clear()
    cache.update(baseline)


def aggregate_temporal(
    data: RasterStack,
    intervals: Union[TemporalIntervals, List[List[Optional[str]]]],
    reducer: Callable,
    labels: Optional[List[Union[float, str]]] = None,
    dimension: Optional[Literal["t", "temporal", "time"]] = None,
    context: Optional[Any] = None,
) -> RasterStack:
    """Computes a temporal aggregation based on an array of temporal intervals.

    For each interval, all data along the temporal dimension will be passed through
    the reducer. Intervals are left-closed, right-open: [start, end).

    Args:
        data: A data cube with at least one temporal dimension.
        intervals: Left-closed temporal intervals. Each interval is [start, end]
            where start is included and end is excluded. Values are RFC 3339 strings
            or None for open-ended intervals.
        reducer: A reducer to be applied for the values contained in each interval.
        labels: Distinct labels for the intervals. Required if interval start values
            are not distinct.
        dimension: The name of the temporal dimension for aggregation. If None, the
            data cube is expected to have only one temporal dimension.
        context: Additional data to be passed to the reducer.

    Returns:
        A new data cube with the same dimensions but aggregated temporal values.

    Raises:
        TemporalExtentEmpty: If any interval has end <= start.
        DistinctDimensionLabelsRequired: If interval starts are not distinct and
            no labels are provided.
        DimensionNotAvailable: If the specified dimension does not exist.
    """
    if not data:
        raise ValueError("Expected a non-empty data cube")

    normalized_intervals = _normalize_intervals(intervals)
    if not normalized_intervals:
        raise ValueError("At least one temporal interval must be provided")

    if dimension is not None:
        if dimension.lower() not in ["t", "temporal", "time"]:
            raise DimensionNotAvailable(dimension)

    parsed_intervals = _parse_intervals(normalized_intervals)
    output_keys = _resolve_output_keys(labels, parsed_intervals)
    timestamps = data.timestamps()

    # Resolve in-interval timestamps once per interval (each runs the
    # _timestamp_in_interval comparison, incl. UTC normalization), then reuse
    # the lists below instead of re-scanning the stack inside the loop.
    matching_keys_per_interval = [
        [ts for ts in timestamps if _timestamp_in_interval(ts, start, end)]
        for (start, end) in parsed_intervals
    ]

    # Concurrently pre-load ONLY the slices inside the union of all intervals.
    # Out-of-interval slices are still never read; in-interval slices are read
    # in parallel instead of one-at-a-time, so the per-interval loop below (and
    # any reducer that pulls slices, e.g. mean -> apply_pixel_selection) hits the
    # cache rather than blocking on serial I/O.
    union_keys = {ts for keys in matching_keys_per_interval for ts in keys}
    if union_keys:
        data.prefetch(union_keys)

    # The reducer is invoked once PER interval, but the executor memoizes each
    # callback node in a results_cache SHARED across those calls. Without
    # intervention every interval after the first returns the first interval's
    # result (identical labels -> e.g. a grayscale composite instead of distinct
    # per-period values). Snapshot the cache before any reduction and reset it to
    # that state before each interval, so each recomputes on its own sub-stack
    # while upstream entries are preserved (no re-reads).
    reducer_cache = _callback_results_cache(reducer)
    cache_baseline = dict(reducer_cache or {})

    result_images: Dict[datetime, ImageData] = {}

    for idx in range(len(parsed_intervals)):
        matching_keys = matching_keys_per_interval[idx]
        output_key = output_keys[idx]

        if not matching_keys:
            nodata_img = _make_nodata_image(data)
            if nodata_img is not None:
                result_images[output_key] = nodata_img
            continue

        sub_images: Dict[datetime, ImageData] = {}
        for key in matching_keys:
            try:
                sub_images[key] = data[key]
            except KeyError:
                logger.warning("Failed to load image for timestamp %s, skipping", key)
                continue
        if not sub_images:
            continue

        sub_stack = RasterStack.from_images(sub_images)
        reducer_kwargs: Dict[str, Any] = {"data": sub_stack}
        if context is not None:
            reducer_kwargs["context"] = context

        # Force this interval's reducer to recompute (see snapshot above).
        _reset_results_cache(reducer_cache, cache_baseline)

        reduced_array = _coerce_reduced_array(reducer(**reducer_kwargs))

        first_img = next(iter(sub_images.values()))
        result_images[output_key] = ImageData(
            reduced_array,
            crs=first_img.crs,
            bounds=first_img.bounds,
            band_descriptions=first_img.band_descriptions or [],
        )

    if not result_images:
        raise ValueError("No data matched any of the specified intervals")

    return RasterStack.from_images(result_images)


def reduce_dimension(
    data: RasterStack,
    reducer: Callable,
    dimension: str,
    context: Optional[Any] = None,
) -> Union[RasterStack, ImageData]:
    """Applies a reducer to a data cube dimension by collapsing all the values along the specified dimension.

    Args:
        data: A RasterStack data cube
        reducer: A reducer function to apply on the specified dimension
        dimension: The name of the dimension over which to reduce
        context: Additional data to be passed to the reducer

    Returns:
        A data cube with the newly computed values, missing the given dimension

    Raises:
        DimensionNotAvailable: If the specified dimension does not exist
        ValueError: If the input data is invalid or the reducer returns invalid data
    """
    # Normalize dimension name
    dim_lower = dimension.lower()

    # Handle temporal dimension
    if dim_lower in ["t", "temporal", "time"]:
        # If there's only one item in the stack, there's no temporal dimension to reduce
        if len(data) <= 1:
            return data

        return _reduce_temporal_dimension(data, reducer)

    # Handle spectral dimension
    elif dim_lower in ["bands", "spectral"]:
        # Use unified stack reduction for both single and multi-image cases
        return _reduce_spectral_dimension_stack(data, reducer)

    # Unsupported dimension
    else:
        raise DimensionNotAvailable(dimension)
