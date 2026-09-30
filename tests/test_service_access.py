"""Service update and access-control checks, run against every store."""

import json
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


def _make_client(main_module, **kwargs) -> TestClient:
    app = main_module.create_app()
    auth = TwoUsersAuth(store=main_module.service_store)
    app.dependency_overrides[app.endpoints.auth.validate] = auth.validate
    app.dependency_overrides[app.endpoints.auth.validate_optional] = (
        auth.validate_optional
    )
    return TestClient(app, **kwargs)


@pytest.fixture
def client(main_module) -> TestClient:
    """App where both the strict and the optional auth know two users."""
    return _make_client(main_module)


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


def test_service_json_types_round_trip(store_path, store_type):
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

    # Read back through a new store instance, so the data comes from disk.
    # The in-memory SQLite database is not shared between instances.
    if store_type != "sqlalchemy":
        reloaded = get_store(f"{store_path}")
        assert reloaded.get_service(service_id)["process"] == service["process"]


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
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        manager.authorize(
            {"user_id": "owner", "configuration": {"scope": "unknown"}}, None
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
    assert validate_access_configuration({"authorized_users": None}) == {
        "authorized_users": None
    }
    with pytest.raises(ValueError):
        validate_access_configuration({"scope": None})
    with pytest.raises(ValueError):
        validate_access_configuration({"authorized_users": ["a", 1]})


def _patch(client: TestClient, service_id: str, body: dict, headers=OWNER):
    return client.patch(f"/services/{service_id}", json=body, headers=headers)


def _get(client: TestClient, service_id: str, headers=OWNER) -> dict:
    response = client.get(f"/services/{service_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_service_with_links_is_stored(client, store_path, store_type):
    """Values such as URLs are stored as plain JSON."""
    service_id = _create(
        client,
        {},
        process={
            **TILE_SERVICE["process"],
            "links": [{"rel": "about", "href": "https://example.com/doc"}],
        },
    )
    process = _get(client, service_id)["process"]
    assert process["links"][0]["href"] == "https://example.com/doc"
    assert client.get("/services", headers=OWNER).status_code == 200

    if store_type == "local":
        with open(store_path) as f:
            json.load(f)


def test_local_store_write_is_atomic(tmp_path):
    """A value that cannot be serialized leaves the store file unchanged."""
    from titiler.openeo.services.local import _write_store_file

    path = tmp_path / "services.json"
    _write_store_file(str(path), {"services": {"a": 1}, "udp_definitions": {}})
    with pytest.raises(TypeError):
        _write_store_file(str(path), {"services": {"a": object()}})

    assert json.loads(path.read_text())["services"] == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["services.json"]


def test_caller_dependent_tile_is_not_public(client):
    """A public service whose graph reads the caller gets a private policy."""
    process = deepcopy(TILE_SERVICE["process"])
    process["process_graph"]["user_node"] = {
        "process_id": "constant",
        "arguments": {"x": {"from_parameter": "_openeo_user"}},
    }
    service_id = _create(client, {}, process=process)

    response = client.get(_tile(service_id), headers=OWNER)
    assert response.status_code == 200, response.text
    assert not response.headers["cache-control"].startswith("public")
    assert "Authorization" in response.headers["vary"]


@pytest.mark.parametrize(
    "graph,expected",
    [
        ({"a": {"arguments": {"x": {"from_parameter": "_openeo_user"}}}}, True),
        ({"a": {"arguments": {"x": [{"from_parameter": "_openeo_tile_store"}]}}}, True),
        ({"a": {"arguments": {"x": {"from_parameter": "bounding_box"}}}}, False),
    ],
)
def test_uses_caller_parameters(graph, expected):
    from titiler.openeo.factory import EndpointsFactory

    assert EndpointsFactory._uses_caller_parameters(graph) is expected


def test_patch_writes_only_request_fields(client, monkeypatch):
    """A PATCH does not write back fields it did not change."""
    service_id = _create(client, {"scope": "public"})
    store = client.app.endpoints.services_store
    store_class = type(store)
    update_service = store_class.update_service

    def concurrent_update(self, user_id, item_id, val, **kwargs):
        # Another PATCH makes the service private between this PATCH's read
        # and its write.
        monkeypatch.setattr(store_class, "update_service", update_service)
        stored = self.get_service(item_id)["configuration"]
        update_service(
            self,
            user_id,
            item_id,
            {"configuration": {**stored, "scope": "private"}},
        )
        return update_service(self, user_id, item_id, val, **kwargs)

    monkeypatch.setattr(store_class, "update_service", concurrent_update)

    assert _patch(client, service_id, {"title": "New"}).status_code == 204
    service = _get(client, service_id)
    assert service["title"] == "New"
    assert service["configuration"]["scope"] == "private"


def test_access_list_hidden_and_dropped(client):
    service_id = _create(
        client, {"scope": "restricted", "authorized_users": ["test_user", "a"]}
    )
    assert _get(client, service_id)["configuration"]["authorized_users"] == [
        "test_user",
        "a",
    ]

    assert (
        _patch(client, service_id, {"configuration": {"scope": "public"}}).status_code
        == 204
    )
    assert "authorized_users" not in _get(client, service_id)["configuration"]
    assert "authorized_users" not in _get(client, service_id, OTHER)["configuration"]


def test_access_list_hidden_from_non_owner(client):
    """A non-owner never sees the access list, even of a readable service."""
    store = client.app.endpoints.services_store
    # A row written outside the API, with a list on a public service.
    service_id = store.add_service(
        "test_user",
        {
            **deepcopy(TILE_SERVICE),
            "enabled": True,
            "configuration": {"scope": "public", "authorized_users": ["x"]},
        },
    )
    assert "authorized_users" not in _get(client, service_id, OTHER)["configuration"]
    assert _get(client, service_id)["configuration"]["authorized_users"] == ["x"]


def test_scope_cannot_be_removed(client):
    service_id = _create(client, {"scope": "private"})

    assert (
        _patch(client, service_id, {"configuration": {"scope": None}}).status_code
        == 400
    )
    assert client.get(_tile(service_id)).status_code == 401
    assert _get(client, service_id)["configuration"]["scope"] == "private"


def test_access_list_requires_restricted_scope(client):
    body = deepcopy(TILE_SERVICE)
    body["configuration"] = {"authorized_users": ["test_user"]}
    assert client.post("/services", json=body, headers=OWNER).status_code == 400

    body["configuration"] = {"scope": "public", "authorized_users": ["test_user"]}
    assert client.post("/services", json=body, headers=OWNER).status_code == 400

    service_id = _create(client, {"scope": "public"})
    response = _patch(
        client, service_id, {"configuration": {"authorized_users": ["test_user"]}}
    )
    assert response.status_code == 400

    # Setting both in one update is fine.
    response = _patch(
        client,
        service_id,
        {"configuration": {"scope": "restricted", "authorized_users": ["a"]}},
    )
    assert response.status_code == 204


def test_error_responses_are_not_stored(main_module, monkeypatch):
    """Auth errors are no-store even when the default policy is cacheable."""
    monkeypatch.setenv("TITILER_OPENEO_API_CACHE_DEFAULT", "public, max-age=600")
    client = _make_client(main_module)
    service_id = _create(client, {"scope": "private"})

    response = client.get(_tile(service_id))
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"

    response = client.get(_tile("no-such-service"))
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_disabled_service_error_is_not_stored(client):
    service_id = _create(client, {})
    assert _patch(client, service_id, {"enabled": False}).status_code == 204

    response = client.get(_tile(service_id))
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_catch_all_error_has_cache_control(main_module):
    """Errors from the catch-all handler are no-store too."""
    client = _make_client(main_module, raise_server_exceptions=False)
    service_id = _create(client, {"extent": [10, 40, 11, 41]})

    response = client.get(f"/services/xyz/{service_id}/tiles/5/0/0")
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"


def test_tile_client_error_is_briefly_cacheable(client):
    """A zoom level out of range does not depend on the caller."""
    service_id = _create(client, {"minzoom": 2})

    response = client.get(_tile(service_id))
    assert response.status_code == 400
    assert response.headers["cache-control"] == "private, max-age=60"


def test_owner_always_has_tile_access(client):
    service_id = _create(
        client, {"scope": "restricted", "authorized_users": ["other_user"]}
    )
    assert client.get(_tile(service_id), headers=OWNER).status_code == 200

    service_id = _create(client, {"scope": "restricted", "authorized_users": []})
    assert client.get(_tile(service_id), headers=OWNER).status_code == 200
    assert client.get(_tile(service_id), headers=OTHER).status_code == 403


def test_private_tile_other_user_gets_403(client):
    service_id = _create(client, {"scope": "private"})
    assert client.get(_tile(service_id), headers=OTHER).status_code == 403
    assert client.get(_tile(service_id)).status_code == 401


def test_load_collection_requires_spatial_extent(client):
    process = deepcopy(LOAD_COLLECTION_PROCESS)
    del process["process_graph"]["loadco1"]["arguments"]["spatial_extent"]

    body = {**deepcopy(TILE_SERVICE), "process": process}
    response = client.post("/services", json=body, headers=OWNER)
    assert 400 <= response.status_code < 500

    service_id = _create(client, {})
    response = _patch(client, service_id, {"process": process})
    assert 400 <= response.status_code < 500


def test_node_requires_arguments(client):
    process = deepcopy(TILE_SERVICE["process"])
    del process["process_graph"]["datacube1"]["arguments"]

    body = {**deepcopy(TILE_SERVICE), "process": process}
    response = client.post("/services", json=body, headers=OWNER)
    assert 400 <= response.status_code < 500

    service_id = _create(client, {})
    response = _patch(client, service_id, {"process": process})
    assert 400 <= response.status_code < 500


def test_enabled_must_be_boolean(client):
    body = {**deepcopy(TILE_SERVICE), "enabled": None}
    # The app reports request validation errors as 400.
    assert client.post("/services", json=body, headers=OWNER).status_code == 400
    assert client.get("/services", headers=OWNER).status_code == 200


def test_invalid_default_service_is_skipped(client, tmp_path):
    services = {
        "services": {
            "bad": {"service": {"type": "xyz"}},
            "good": {"service": {**deepcopy(TILE_SERVICE), "title": "Good"}},
        }
    }
    path = tmp_path / "defaults.json"
    path.write_text(json.dumps(services))
    client.app.endpoints.default_services_file = str(path)

    response = client.get("/services", headers=OWNER)
    assert response.status_code == 200
    assert [s["title"] for s in response.json()["services"]] == ["Good"]


def test_patch_repairs_non_dict_configuration(client):
    store = client.app.endpoints.services_store
    service_id = store.add_service(
        "test_user",
        {**deepcopy(TILE_SERVICE), "enabled": True, "configuration": ["bad"]},
    )
    # A malformed configuration fails closed for other users.
    assert client.get(_tile(service_id)).status_code == 401

    response = _patch(client, service_id, {"configuration": {"scope": "public"}})
    assert response.status_code == 204
    assert _get(client, service_id)["configuration"] == {"scope": "public"}


def test_maxzoom_zero_is_applied(client):
    service_id = _create(client, {"maxzoom": 0})
    assert client.get(_tile(service_id)).status_code == 200
    response = client.get(f"/services/xyz/{service_id}/tiles/1/0/0")
    assert response.status_code == 400


def test_authorize_refuses_unknown_scope(monkeypatch):
    """authorize() refuses a scope it does not know."""
    import titiler.openeo.services.auth as service_auth

    monkeypatch.setattr(service_auth, "get_scope", lambda configuration: "other")
    with pytest.raises(HTTPException) as exc:
        ServiceAuthorizationManager().authorize(
            {"user_id": "owner", "configuration": {}}, User(user_id="x")
        )
    assert exc.value.status_code == 403
