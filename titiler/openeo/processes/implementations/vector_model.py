"""Vector data cube (ADR 0014 §2.1).

A vector data cube has a `geometry` dimension and, optionally, more
dimensions (for example `t` and `bands`). openEO has no `vector-cube` type:
the process specifications describe it as a `datacube` with a `geometry`
dimension. `VectorCube` is only the name of the Python class.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy
from rasterio.crs import CRS
from rasterio.warp import transform_geom
from rio_tiler.constants import WGS84_CRS

GEOMETRY_DIMENSION = "geometry"


def _normalize_features(obj: Any) -> List[Dict[str, Any]]:
    """Return the features of a GeoJSON FeatureCollection, Feature or Geometry."""
    if not isinstance(obj, dict) or "type" not in obj:
        raise ValueError("Invalid GeoJSON format")
    if obj["type"] == "FeatureCollection":
        return list(obj.get("features", []))
    if obj["type"] == "Feature":
        return [obj]
    return [{"type": "Feature", "geometry": obj, "properties": {}}]


@dataclass
class VectorCube:
    """A vector data cube.

    Attributes:
        geometries: GeoJSON geometry dicts, in `crs`. One for each label of
            the `geometry` dimension. GeoJSON permits a null geometry.
        crs: The coordinate reference system of the geometries.
        properties: The attributes of each geometry. They are not a
            dimension of the cube.
        values: The data. The first axis is the `geometry` dimension and each
            other axis is a dimension in `dims`. A masked cell has no data.
            `None` for a cube that has geometries only.
        dims: The dimension names. The first one is always `geometry`.
        labels: The labels of each dimension other than `geometry`.

    """

    geometries: List[Optional[Dict[str, Any]]]
    crs: CRS = field(default_factory=lambda: WGS84_CRS)
    properties: List[Dict[str, Any]] = field(default_factory=list)
    values: Optional[numpy.ma.MaskedArray] = None
    dims: Tuple[str, ...] = (GEOMETRY_DIMENSION,)
    labels: Dict[str, List[Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Check that the parts of the cube agree."""
        if not self.properties:
            self.properties = [{} for _ in self.geometries]
        if len(self.properties) != len(self.geometries):
            raise ValueError(
                f"A vector cube needs one properties dict for each geometry: "
                f"got {len(self.properties)} for {len(self.geometries)} geometries"
            )
        if not self.dims or self.dims[0] != GEOMETRY_DIMENSION:
            raise ValueError(
                f"The first dimension of a vector cube must be "
                f"'{GEOMETRY_DIMENSION}', got {self.dims}"
            )
        missing = [dim for dim in self.dims[1:] if dim not in self.labels]
        if missing:
            raise ValueError(f"No labels for the dimensions {missing}")
        if self.values is None:
            if len(self.dims) > 1:
                raise ValueError(
                    "A vector cube with more than one dimension needs values"
                )
            return
        expected = (len(self.geometries),) + tuple(
            len(self.labels[dim]) for dim in self.dims[1:]
        )
        if self.values.shape != expected:
            raise ValueError(
                f"The values of the vector cube have the shape "
                f"{self.values.shape}, expected {expected} for the dimensions "
                f"{self.dims}"
            )

    def __len__(self) -> int:
        """Return the number of geometries."""
        return len(self.geometries)

    @classmethod
    def from_geojson(cls, obj: Any, crs: CRS = WGS84_CRS) -> "VectorCube":
        """Make a cube with geometries only from a GeoJSON object.

        Args:
            obj: A GeoJSON FeatureCollection, Feature or Geometry.
            crs: The CRS of the coordinates. GeoJSON uses EPSG:4326.

        """
        features = _normalize_features(obj)
        return cls(
            geometries=[feature.get("geometry") for feature in features],
            crs=crs,
            properties=[dict(feature.get("properties") or {}) for feature in features],
        )

    def _geometries_in(self, crs: CRS) -> List[Optional[Dict[str, Any]]]:
        """Return the geometries in `crs`. They are not copied if the CRS is the same."""
        if self.crs == crs:
            return self.geometries
        return [
            transform_geom(self.crs, crs, geometry) if geometry else geometry
            for geometry in self.geometries
        ]

    def _nested_values(self, index: Tuple[int, ...], axis: int) -> Any:
        """Return the values of one geometry as dicts nested by dimension label."""
        if axis == len(self.dims):
            assert self.values is not None
            if numpy.ma.getmaskarray(self.values)[index]:
                return numpy.ma.masked
            return self.values.data[index]
        return {
            label: self._nested_values(index + (i,), axis + 1)
            for i, label in enumerate(self.labels[self.dims[axis]])
        }

    def iter_features(self, crs: CRS = WGS84_CRS) -> Iterator[Dict[str, Any]]:
        """Yield the cube as GeoJSON features.

        The values of each geometry go in the property `values`, as dicts
        nested by dimension label (for example `{date: {band: value}}`). For a
        cube with the `geometry` dimension only, the value goes in the
        property `value`. A masked cell is `numpy.ma.masked`.
        """
        geometries = self._geometries_in(crs)
        for i, geometry in enumerate(geometries):
            properties = dict(self.properties[i])
            if self.values is not None:
                key = "values" if len(self.dims) > 1 else "value"
                properties[key] = self._nested_values((i,), 1)
            yield {"type": "Feature", "geometry": geometry, "properties": properties}

    def to_feature_collection(self, crs: CRS = WGS84_CRS) -> Dict[str, Any]:
        """Return the cube as a GeoJSON FeatureCollection, in EPSG:4326 by default.

        RFC 7946 requires EPSG:4326 for GeoJSON. See `iter_features` for the
        properties.
        """
        return {"type": "FeatureCollection", "features": list(self.iter_features(crs))}

    @property
    def __geo_interface__(self) -> Dict[str, Any]:
        """Return the cube as a GeoJSON FeatureCollection in EPSG:4326."""
        return self.to_feature_collection()
