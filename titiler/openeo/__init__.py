"""titiler.openeo"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("titiler-openeo")
except PackageNotFoundError:
    # Package is not installed
    __version__ = "unknown"
