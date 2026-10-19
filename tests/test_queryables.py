"""Tests for GET /collections/{collection_id}/queryables."""

from typing import Any, Dict, List

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


class _FakeClient:
    def __init__(self, result: Any):
        self._result = result
        self.urls: List[str] = []

    def get_queryables_from(self, url: str) -> Dict[str, Any]:
        self.urls.append(url)
        if isinstance(self._result, Exception):
            raise self._result
        return dict(self._result)


@pytest.fixture
def fake_client(monkeypatch, request):
    fake = _FakeClient(request.param)
    monkeypatch.setattr(stacApiBackend, "client", property(lambda self: fake))
    stacapi.queryables_cache.clear()
    yield fake
    stacapi.queryables_cache.clear()


@pytest.mark.parametrize("fake_client", [QUERYABLES], indirect=True)
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
    [APIError("Not Found"), APIError("Invalid response: expected 'properties'")],
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
