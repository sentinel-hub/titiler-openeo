"""titiler.openeo"""

try:
    from ._version import version as __version__
except ImportError:
    # fallback for development installs
    from importlib.metadata import version

    __version__ = version("titiler-openeo")
