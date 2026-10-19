"""Tests for GET /collections/{collection_id}/queryables."""

import copy
from typing import Any, Dict, List

import pystac
import pytest
from pystac_client.exceptions import APIError

from titiler.openeo import stacapi
from titiler.openeo.stacapi import stacApiBackend

QUERYABLES: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2019-09/schema",
    "$id": "https://stac.eoapi.dev/collections/sentinel-1-grd/queryables",
    "type": "object",
    "title": "STAC Queryables.",
    "properties": {
        "id": {"title": "Item ID", "type": "string"},
        "datetime": {"type": "string", "format": "date-time"},
        "geometry": {"$ref": "https://geojson.org/schema/Feature.json"},
        "sat:orbit_state": {"type": "string", "enum": ["ascending", "descending"]},
        "sar:polarizations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "sat:orbit_state"],
    "additionalProperties": True,
}


def _api_error(status_code, message="error"):
    error = APIError(message)
    error.status_code = status_code
    return error


class _FakeStacIO:
    def __init__(self, result: Any):
        self._result = result
        self.urls: List[str] = []

    def read_json(self, url: str) -> Any:
        self.urls.append(url)
        if isinstance(self._result, Exception):
            raise self._result
        return copy.deepcopy(self._result)


class _FakeClient:
    def __init__(self, result: Any, collection_error: Any = None):
        self._stac_io = _FakeStacIO(result)
        self._collection_error = collection_error
        self.urls = self._stac_io.urls

    def get_collection(self, collection_id: str) -> pystac.Collection:
        if self._collection_error is not None:
            raise self._collection_error
        return pystac.Collection(
            id=collection_id,
            description="fake",
            extent=pystac.Extent(
                pystac.SpatialExtent([[-180, -90, 180, 90]]),
                pystac.TemporalExtent([[None, None]]),
            ),
        )


@pytest.fixture
def fake_client(monkeypatch, request):
    fake = _FakeClient(*request.param)
    monkeypatch.setattr(stacApiBackend, "client", property(lambda self: fake))
    stacapi.queryables_cache.clear()
    stacapi.collection_cache.clear()
    yield fake
    stacapi.queryables_cache.clear()
    stacapi.collection_cache.clear()


@pytest.mark.parametrize("fake_client", [(QUERYABLES,)], indirect=True)
def test_queryables_proxies_stac_api(fake_client, app_no_auth):
    response = app_no_auth.get("/collections/sentinel-1-grd/queryables")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/schema+json"
    assert fake_client.urls == [
        "https://stac.eoapi.dev/collections/sentinel-1-grd/queryables"
    ]

    body = response.json()
    assert body["$id"].endswith("/collections/sentinel-1-grd/queryables")
    assert body["$schema"] == QUERYABLES["$schema"]
    assert set(body["properties"]) == {"sat:orbit_state", "sar:polarizations"}
    assert body["required"] == ["sat:orbit_state"]
    assert "eo:cloud_cover" not in body["properties"]


@pytest.mark.parametrize(
    "fake_client",
    [
        (_api_error(404, "Not Found"),),
        (_api_error(501, "Not Implemented"),),
        ({"type": "object"},),
    ],
    indirect=True,
)
def test_queryables_fallback_to_empty_schema(fake_client, app_no_auth):
    response = app_no_auth.get("/collections/no-filter/queryables")

    assert response.status_code == 200
    body = response.json()
    assert body["properties"] == {}
    assert body["type"] == "object"
    assert body["additionalProperties"] is False
    assert body["$schema"] == "https://json-schema.org/draft/2019-09/schema"


@pytest.mark.parametrize(
    "fake_client",
    [(_api_error(404), _api_error(404, "Collection does not exist"))],
    indirect=True,
)
def test_queryables_unknown_collection(fake_client, app_no_auth):
    response = app_no_auth.get("/collections/does-not-exist/queryables")

    assert response.status_code == 404
    assert response.json()["code"] == "ResourceNotFound"


@pytest.mark.parametrize(
    "fake_client",
    [(_api_error(401, "Unauthorized"),), (_api_error(503, "Unavailable"),)],
    indirect=True,
)
def test_queryables_upstream_errors_are_not_masked(fake_client, app_no_auth):
    response = app_no_auth.get("/collections/sentinel-2-l2a/queryables")

    assert response.status_code == 500
    assert len(stacapi.queryables_cache) == 0


def test_queryables_keep_relative_refs_resolvable():
    schema = {
        "$id": "https://stac.example.com/collections/c/queryables",
        "properties": {
            "orbit": {"$ref": "../schemas/orbit.json"},
            "local": {"$ref": "#/$defs/local"},
            "nested": {
                "$id": "https://other.example.com/schemas/",
                "properties": {"x": {"$ref": "x.json"}},
            },
        },
    }

    out = stacapi._absolutize_refs(schema, "https://ignored.example.com/")

    props = out["properties"]
    assert (
        props["orbit"]["$ref"]
        == "https://stac.example.com/collections/schemas/orbit.json"
    )
    assert props["local"]["$ref"] == "#/$defs/local"
    assert props["nested"]["properties"]["x"]["$ref"] == (
        "https://other.example.com/schemas/x.json"
    )
    assert schema["properties"]["orbit"]["$ref"] == "../schemas/orbit.json"


def test_queryables_relative_root_id_resolved_once():
    schema = {
        "$id": "/collections/c/queryables",
        "properties": {"orbit": {"$ref": "../schemas/orbit.json"}},
    }

    out = stacapi._absolutize_refs(
        schema, "https://stac.example.com/api/collections/c/queryables"
    )

    assert out["$id"] == "https://stac.example.com/collections/c/queryables"
    assert out["properties"]["orbit"]["$ref"] == (
        "https://stac.example.com/collections/schemas/orbit.json"
    )


def test_queryables_literal_values_unchanged():
    literal = {"$id": "ascending", "$ref": "not-a-ref"}
    schema = {
        "$id": "https://stac.example.com/q",
        "properties": {
            "a": {"const": literal},
            "b": {"enum": [literal]},
            "c": {"default": literal, "examples": [literal]},
            "d": {"anyOf": [{"$ref": "d.json"}], "items": {"$ref": "i.json"}},
        },
    }

    props = stacapi._absolutize_refs(schema, "https://stac.example.com/q")["properties"]

    assert props["a"]["const"] == literal
    assert props["b"]["enum"] == [literal]
    assert props["c"]["default"] == literal
    assert props["c"]["examples"] == [literal]
    assert props["d"]["anyOf"][0]["$ref"] == "https://stac.example.com/d.json"
    assert props["d"]["items"]["$ref"] == "https://stac.example.com/i.json"


def test_queryables_excluded_collection():
    backend = stacApiBackend(url="https://example.com", exclude_collections=["hidden"])
    with pytest.raises(ValueError):
        backend.get_queryables("hidden")


def test_capabilities_list_queryables_endpoint(app_no_auth):
    response = app_no_auth.get("/")

    assert response.status_code == 200
    assert {
        "path": "/collections/{collection_id}/queryables",
        "methods": ["GET"],
    } in response.json()["endpoints"]
