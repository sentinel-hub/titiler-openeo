"""titiler.openeo.processes."""

from typing import Dict, Optional

from rio_tiler.models import ImageData

__all__ = ["save_result"]


def save_result(
    data: ImageData,
    format: str,
    options: Optional[Dict] = None,
) -> bytes:
    """Save Result."""
    options = options or {}
    return data.render(img_format=format, **options)
