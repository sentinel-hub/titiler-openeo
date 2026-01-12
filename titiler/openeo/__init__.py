"""titiler.openeo"""

try:  # pragma: no cover
    from ._version import version as __version__
except ImportError:  # pragma: no cover
    # fallback for development installs
    from importlib.metadata import version

    __version__ = version("titiler-openeo")
