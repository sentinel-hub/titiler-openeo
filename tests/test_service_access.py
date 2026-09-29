"""Service update and access-control checks, run against every store."""

from copy import deepcopy
from typing import Any, Optional

import pytest
from fastapi import Header, HTTPException
from starlette.testclient import TestClient

from titiler.openeo.auth import Auth, User
from titiler.openeo.services.auth import (
    ServiceAuthorizationManager,
    get_scope,
    validate_access_configuration,
)

OWNER = {"Authorization": "Bearer basic//owner_token"}
OTHER = {"Authorization": "Bearer basic//other_token"}

TILE_SERVICE = {
    "process": {
        "process_graph": {
            "datacube1": {"process_id": "create_data_cube", "arguments": {}},
            "add_dims": {
                "process_id": "add_dimension",
                "arguments": {
                    "data": {"from_node": "datacube1"},
                    "name": "bands",
                    "label": "gray",
                    "type": "bands",
                },
            },
            "save1": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "add_dims"}, "format": "gtiff"},
                "result": True,
            },
        }
    },
    "type": "xyz",
    "title": "Test Service",
    "configuration": {"tile_size": 256, "tilematrixset": "WebMercatorQuad"},
}

LOAD_COLLECTION_PROCESS = {
    "process_graph": {
        "loadco1": {
            "process_id": "load_collection",
            "arguments": {
                "id": "S2",
                "spatial_extent": {
                    "west": 16.1,
                    "east": 16.6,
                    "north": 48.6,
                    "south": 47.2,
                },
                "temporal_extent": ["2017-01-01", "2017-02-01"],
            },
        },
        "save1": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "loadco1"}, "format": "png"},
            "result": True,
        },
    }
}


class TwoUsersAuth(Auth):
    """Mock auth that knows an owner and one other user."""

    def validate(self, authorization: str = Header(default=None)) -> User:
        """Map the test tokens to users."""
        if not authorization:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if "owner_token" in authorization:
            return User(user_id="test_user")
        if "other_token" in authorization:
            return User(user_id="other_user")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    def validate_optional(
        self, authorization: str = Header(default=None)
    ) -> Optional[User]:
        """Allow anonymous requests."""
        if not authorization:
            return None
        return self.validate(authorization)

    def login(self, authorization: str = Header()) -> Any:
        """Return a fake token."""
        return {"access_token": "mock_token"}


@pytest.fixture
def client(main_module) -> TestClient:
    """App where both the strict and the optional auth know two users."""
    app = main_module.create_app()
    auth = TwoUsersAuth(store=main_module.service_store)
    app.dependency_overrides[app.endpoints.auth.validate] = auth.validate
    app.dependency_overrides[app.endpoints.auth.validate_optional] = (
        auth.validate_optional
    )
    return TestClient(app)


def _create(client: TestClient, configuration: dict, **fields) -> str:
    body = deepcopy(TILE_SERVICE)
    body["configuration"] = {**body["configuration"], **configuration}
    body.update(fields)
    response = client.post("/services", json=body, headers=OWNER)
    assert response.status_code == 201, response.text
    return response.headers["openeo-identifier"]


def _tile(service_id: str) -> str:
    return f"/services/xyz/{service_id}/tiles/0/0/0"


def test_patch_configuration_keeps_scope(client):
    """A partial configuration update must not drop the stored scope."""
    service_id = _create(client, {"scope": "private"})
    assert client.get(_tile(service_id)).status_code == 401

    response = client.patch(
        f"/services/{service_id}",
        json={"configuration": {"tile_size": 512}},
        headers=OWNER,
    )
    assert response.status_code == 204

    configuration = client.get(f"/services/{service_id}", headers=OWNER).json()[
        "configuration"
    ]
    assert configuration["scope"] == "private"
    assert configuration["tile_size"] == 512
    assert configuration["tilematrixset"] == "WebMercatorQuad"
    assert client.get(_tile(service_id)).status_code == 401


def test_patch_configuration_null_removes_key(client):
    service_id = _create(client, {"scope": "private", "minzoom": 2})

    response = client.patch(
        f"/services/{service_id}",
        json={"configuration": {"minzoom": None}},
        headers=OWNER,
    )
    assert response.status_code == 204

    configuration = client.get(f"/services/{service_id}", headers=OWNER).json()[
        "configuration"
    ]
    assert "minzoom" not in configuration
    assert configuration["scope"] == "private"


def test_patch_process_is_validated(client):
    """An update gets the same process checks as a creation."""
    service_id = _create(client, {})
    before = client.get(f"/services/{service_id}", headers=OWNER).json()["process"]

    response = client.patch(
        f"/services/{service_id}",
        json={
            "process": {
                "process_graph": {
                    "bad": {
                        "process_id": "no_such_process",
                        "arguments": {},
                        "result": True,
                    }
                }
            }
        },
        headers=OWNER,
    )
    assert response.status_code == 422

    after = client.get(f"/services/{service_id}", headers=OWNER).json()["process"]
    assert after == before


def test_patch_process_rewrites_spatial_extent(client):
    """A new load_collection gets its extent bound to the tile, as on creation."""
    service_id = _create(client, {})

    response = client.patch(
        f"/services/{service_id}",
        json={"process": LOAD_COLLECTION_PROCESS},
        headers=OWNER,
    )
    assert response.status_code == 204

    process = client.get(f"/services/{service_id}", headers=OWNER).json()["process"]
    assert process["process_graph"]["loadco1"]["arguments"]["spatial_extent"] == {
        "from_parameter": "bounding_box"
    }


def test_patch_does_not_store_record_fields(client, monkeypatch):
    """`id` and `user_id` are record fields and must not go into the service data."""
    service_id = _create(client, {})
    store_class = type(client.app.endpoints.services_store)
    sent = {}
    update_service = store_class.update_service

    def spy(self, user_id, item_id, val, **kwargs):
        sent.update(val)
        return update_service(self, user_id, item_id, val, **kwargs)

    monkeypatch.setattr(store_class, "update_service", spy)

    response = client.patch(
        f"/services/{service_id}", json={"title": "New"}, headers=OWNER
    )
    assert response.status_code == 204
    assert sent["title"] == "New"
    assert "id" not in sent
    assert "user_id" not in sent


@pytest.mark.parametrize(
    "configuration",
    [{"scope": "secret"}, {"scope": 1}, {"authorized_users": "test_user"}],
)
def test_invalid_access_configuration_rejected(client, configuration):
    body = deepcopy(TILE_SERVICE)
    body["configuration"] = configuration
    # The app reports request validation errors as 400.
    assert client.post("/services", json=body, headers=OWNER).status_code == 400

    service_id = _create(client, {})
    response = client.patch(
        f"/services/{service_id}",
        json={"configuration": configuration},
        headers=OWNER,
    )
    assert response.status_code == 400


def test_scope_is_normalized(client):
    service_id = _create(client, {"scope": " Private "})

    configuration = client.get(f"/services/{service_id}", headers=OWNER).json()[
        "configuration"
    ]
    assert configuration["scope"] == "private"
    assert client.get(_tile(service_id)).status_code == 401


@pytest.mark.parametrize(
    "scope,expected",
    [("public", 200), ("private", 403), ("restricted", 403)],
)
def test_get_service_by_other_user(client, scope, expected):
    """Other users can read the metadata of public services only."""
    service_id = _create(client, {"scope": scope})

    assert client.get(f"/services/{service_id}", headers=OWNER).status_code == 200
    response = client.get(f"/services/{service_id}", headers=OTHER)
    assert response.status_code == expected


def test_disabled_service_serves_no_tiles(client):
    service_id = _create(client, {"scope": "public"})
    assert client.get(_tile(service_id)).status_code == 200

    response = client.patch(
        f"/services/{service_id}", json={"enabled": False}, headers=OWNER
    )
    assert response.status_code == 204
    assert client.get(_tile(service_id)).status_code == 404


def test_tile_cache_control_follows_scope(client):
    public_id = _create(client, {"scope": "public"})
    private_id = _create(client, {"scope": "private"})

    response = client.get(_tile(public_id))
    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("public")

    response = client.get(_tile(private_id), headers=OWNER)
    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("private")

    response = client.get(_tile(private_id))
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_service_json_types_round_trip(store_path):
    """Mixed-type lists keep their item types in every store."""
    from titiler.openeo.services import get_store

    store = get_store(f"{store_path}")
    service = {
        "type": "XYZ",
        "process": {
            "parameters": [{"name": "p", "default": [0.3, "viridis"]}],
            "process_graph": {
                "n": {
                    "process_id": "multiply",
                    "arguments": {"x": {"from_parameter": "p"}, "y": 0},
                    "result": True,
                }
            },
        },
    }
    service_id = store.add_service("owner", deepcopy(service))
    assert store.get_service(service_id)["process"] == service["process"]

    store.update_service("owner", service_id, deepcopy(service))
    assert store.get_service(service_id)["process"] == service["process"]


@pytest.mark.parametrize(
    "configuration,expected",
    [
        (None, "public"),
        ({}, "public"),
        ({"scope": "Restricted"}, "restricted"),
        ({"scope": "unknown"}, "private"),
        ({"scope": 1}, "private"),
    ],
)
def test_get_scope(configuration, expected):
    assert get_scope(configuration) == expected


def test_authorize_fails_closed():
    manager = ServiceAuthorizationManager()
    other = User(user_id="lic")

    with pytest.raises(HTTPException) as exc:
        manager.authorize(
            {"user_id": "owner", "configuration": {"scope": "unknown"}}, other
        )
    assert exc.value.status_code == 401

    # A string is not a user list: "lic" is a substring of "alice".
    with pytest.raises(HTTPException) as exc:
        manager.authorize(
            {
                "user_id": "owner",
                "configuration": {"scope": "restricted", "authorized_users": "alice"},
            },
            other,
        )
    assert exc.value.status_code == 403


def test_validate_access_configuration():
    assert validate_access_configuration(None) is None
    assert validate_access_configuration({"scope": "PUBLIC"}) == {"scope": "public"}
    assert validate_access_configuration({"scope": None}) == {"scope": None}
    with pytest.raises(ValueError):
        validate_access_configuration({"authorized_users": ["a", 1]})
