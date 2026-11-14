"""Vector data cube (ADR 0014 §2.1).

A vector data cube has a `geometry` dimension and, optionally, more
dimensions (for example `t` and `bands`). openEO has no `vector-cube` type:
the process specifications describe it as a `datacube` with a `geometry`
dimension. `VectorCube` is only the name of the Python class.
"""

import itertools
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import numpy
from rasterio.crs import CRS
from rasterio.warp import transform_geom
from rio_tiler.constants import WGS84_CRS

GEOMETRY_DIMENSION = "geometry"
TEMPORAL_DIMENSION = "t"

# Property values that the GIS formats store as they are. Others become JSON.
_SCALARS = (str, int, float, bool, date)


def _normalize_features(obj: Any) -> List[Dict[str, Any]]:
    """Return the features of a GeoJSON FeatureCollection, Feature or Geometry."""
    if not isinstance(obj, dict) or "type" not in obj:
        raise ValueError("Invalid GeoJSON format")
    if obj["type"] == "FeatureCollection":
        return list(obj.get("features", []))
    if obj["type"] == "Feature":
        return [obj]
    return [{"type": "Feature", "geometry": obj, "properties": {}}]


def _unique_name(name: str, used: set) -> str:
    """Return `name`, or `name_1`, `name_2`... if it is already used."""
    candidate, n = name, 0
    while candidate in used:
        n += 1
        candidate = f"{name}_{n}"
    used.add(candidate)
    return candidate


def _cell(value: Any) -> Any:
    """Return a table cell: a Python scalar, or None for no data."""
    if value is None or value is numpy.ma.masked:
        return None
    if isinstance(value, numpy.generic):
        return value.item()
    return value


def _property_cell(value: Any) -> Any:
    """Return a property as a table cell. A list or a dict becomes JSON text."""
    value = _cell(value)
    if value is None or isinstance(value, _SCALARS):
        return value
    return json.dumps(value, default=str)


@dataclass
class VectorRecords:
    """A vector data cube as a flat table, for the GIS formats.

    Attributes:
        geometries: One GeoJSON geometry for each row, in `crs`.
        columns: The columns by name. `None` is a null value.
        crs: The CRS of the geometries.

    """

    geometries: List[Optional[Dict[str, Any]]]
    columns: Dict[str, List[Any]]
    crs: CRS


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

    def select(self, indices: Iterable[int]) -> "VectorCube":
        """Return a cube with the geometries at `indices` only, in that order."""
        index = numpy.asarray(list(indices), dtype=numpy.intp)
        return VectorCube(
            geometries=[self.geometries[i] for i in index],
            crs=self.crs,
            properties=[dict(self.properties[i]) for i in index],
            values=None if self.values is None else self.values[index],
            dims=self.dims,
            labels=self.labels,
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

    def to_records(self, reserved: Iterable[str] = ()) -> VectorRecords:
        """Return the cube as a flat table, in the CRS of the cube.

        There is one row for each geometry and each label of the `t`
        dimension (one row for each geometry if there is no `t`). The columns
        are: the properties of the geometries, then `t`, then one column for
        each combination of labels of the other dimensions (for example
        `B03`, or `B03_value` with a target dimension). A cube with the
        `geometry` dimension only has a `value` column. The same layout is
        used by the CSV output.

        Args:
            reserved: Column names that the caller uses (for example
                `geometry`). A column with such a name gets a suffix.

        """
        used = set(reserved)
        property_names = list(
            dict.fromkeys(key for properties in self.properties for key in properties)
        )
        property_columns = {
            name: _unique_name(str(name), used) for name in property_names
        }

        if self.values is None:
            values = numpy.ma.masked_all((len(self), 1, 0), dtype=object)
            dates: List[Any] = [None]
            value_names: List[str] = []
        else:
            other = [dim for dim in self.dims[1:] if dim != TEMPORAL_DIMENSION]
            if TEMPORAL_DIMENSION in self.dims:
                axis = self.dims.index(TEMPORAL_DIMENSION)
                values = numpy.ma.MaskedArray(
                    numpy.moveaxis(self.values.data, axis, 1),
                    mask=numpy.moveaxis(numpy.ma.getmaskarray(self.values), axis, 1),
                )
                dates = list(self.labels[TEMPORAL_DIMENSION])
            else:
                values = self.values[:, numpy.newaxis]
                dates = [None]
            values = values.reshape((len(self), len(dates), -1))
            combinations = itertools.product(*(self.labels[dim] for dim in other))
            value_names = (
                ["_".join(str(label) for label in combo) for combo in combinations]
                if other
                else ["value"]
            )
        t_column = (
            _unique_name(TEMPORAL_DIMENSION, used)
            if TEMPORAL_DIMENSION in self.dims
            else None
        )
        value_columns = [_unique_name(name, used) for name in value_names]

        columns: Dict[str, List[Any]] = {
            column: [] for column in property_columns.values()
        }
        if t_column:
            columns[t_column] = []
        for column in value_columns:
            columns[column] = []

        mask = numpy.ma.getmaskarray(values)
        geometries: List[Optional[Dict[str, Any]]] = []
        for g, geometry in enumerate(self.geometries):
            for d, label in enumerate(dates):
                geometries.append(geometry)
                for name, column in property_columns.items():
                    columns[column].append(_property_cell(self.properties[g].get(name)))
                if t_column:
                    columns[t_column].append(label)
                for v, column in enumerate(value_columns):
                    cell = None if mask[g, d, v] else values.data[g, d, v]
                    columns[column].append(_cell(cell))

        return VectorRecords(geometries=geometries, columns=columns, crs=self.crs)

    @property
    def __geo_interface__(self) -> Dict[str, Any]:
        """Return the cube as a GeoJSON FeatureCollection in EPSG:4326."""
        return self.to_feature_collection()
