"""TiTiler.openeo data models."""

from typing import Dict

from rio_tiler.models import ImageData

# https://openeo.org/documentation/1.0/developers/backends/performance.html#datacube-processing
# Here it is important to note that openEO does not enforce or define how the datacube should look like on the backend.
# The datacube can be a set of files, or arrays in memory distributed over a cluster.
# These choices are left to the backend implementor, this guide only tries to highlight the possibilities.
RasterStack = Dict[str, ImageData]
