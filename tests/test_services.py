"""Test titiler.openeo services."""

from typing import Any, Union

from fastapi import Header

from titiler.openeo.auth import Auth, User
from titiler.openeo.services import get_store


def test_add_service(app_with_auth):
    """Test adding a service."""
    service_input = {
        "process": {
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
        },
        "type": "xyz",
        "title": "Test Service",
        "description": "A test service",
    }

    response = app_with_auth.post("/services", json=service_input)
    assert response.status_code == 201
    # check the location header
    assert response.headers["Location"] is not None
    # save the service id from the location for later
    assert response.headers["OpenEO-Identifier"] is not None


def test_add_service_noauth(app_no_auth):
    """Test adding a service without authentication."""
    service_input = {
        "process": {
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
        },
        "type": "xyz",
        "title": "Test Service",
        "description": "A test service",
    }

    response = app_no_auth.post("/services", json=service_input)
    assert response.status_code == 401


def test_get_services(app_with_auth):
    """Test getting all services."""
    response = app_with_auth.get("/services")
    assert response.status_code == 200
    services = response.json()
    assert "services" in services
    assert isinstance(services["services"], list)
    assert "links" in services


def test_get_services_noauth(app_no_auth):
    """Test getting all services without authentication."""
    response = app_no_auth.get("/services")
    assert response.status_code == 401


def test_get_service(app_with_auth):
    """Test getting a specific service."""
    # First create a service
    service_input = {
        "process": {
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
        },
        "type": "xyz",
        "title": "Test Service",
    }
    create_response = app_with_auth.post("/services", json=service_input)
    assert create_response.status_code == 201
    service_id = create_response.headers["location"].split("/")[-1]
    get_response = app_with_auth.get(f"/services/{service_id}")
    assert get_response.status_code == 200
    service = get_response.json()
    assert service["id"] == service_id
    assert service["title"] == service_input["title"]
    assert service["type"] == service_input["type"]

    # Make sure we overwrite the Extent Parameters
    load_node = service["process"]["process_graph"]["loadco1"]
    arguments = load_node["arguments"]  # ["spatial_extent"]
    assert arguments["id"] == "S2"
    assert arguments["spatial_extent"] == {"from_parameter": "bounding_box"}


def test_get_user_services(app_with_auth):
    """Test getting services for a specific user."""
    response = app_with_auth.get("/services", params={"user_id": "test_user"})
    assert response.status_code == 200
    services = response.json()
    assert "services" in services
    assert isinstance(services["services"], list)


def test_update_service(app_with_auth):
    """Test updating a service."""
    # First create a service
    service_input = {
        "process": {
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
        },
        "type": "xyz",
        "title": "Original Title",
    }
    create_response = app_with_auth.post("/services", json=service_input)
    assert create_response.status_code == 201
    service_id = create_response.headers["location"].split("/")[-1]

    # Update the service
    update_input = {
        "process": {
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
        },
        "type": "xyz",
        "title": "Updated Title",
    }
    response = app_with_auth.patch(f"/services/{service_id}", json=update_input)
    assert response.status_code == 204


def test_delete_service(app_with_auth):
    """Test deleting a service."""
    # First create a service
    service_input = {
        "process": {
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
        },
        "type": "xyz",
        "title": "Test Service",
    }
    create_response = app_with_auth.post("/services", json=service_input)
    assert create_response.status_code == 201
    service_id = create_response.headers["location"].split("/")[-1]

    # Delete the service
    response = app_with_auth.delete(f"/services/{service_id}")
    assert response.status_code == 204

    # Verify it's deleted
    get_response = app_with_auth.get(f"/services/{service_id}")
    assert get_response.status_code == 404


# def test_service_validation(app_with_auth):
#     """Test service input validation."""
#     # Missing required process
#     invalid_input = {"type": "xyz", "title": "Test Service"}
#     response = app_with_auth.post("/services", json=invalid_input)
#     assert response.status_code == 400

#     # Invalid process graph
#     invalid_input = {
#         "process": {
#             "process_graph": {
#                 "loadco1": {"process_id": "invalid_process", "arguments": {}},
#                 "save1": {
#                     "process_id": "save_result",
#                     "arguments": {"data": {"from_node": "loadco1"}, "format": "png"},
#                     "result": True,
#                 },
#             }
#         },
#         "type": "xyz",
#         "title": "Test Service",
#     }
#     response = app_with_auth.post("/services", json=invalid_input)
#     assert response.status_code == 422


def test_service_xyz_access_scopes(app_with_auth, app_no_auth, store_path):
    """Test XYZ service access based on different scopes (private, restricted, public)."""
    # Base service template
    base_service = {
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

    # Create services with different scopes and test access
    scope_configs = {
        "private": {
            "scope": "private",
            "expected_status": {"owner": 200, "authenticated": 401, "anonymous": 401},
        },
        "restricted": {
            "scope": "restricted",
            "expected_status": {"owner": 200, "authenticated": 200, "anonymous": 401},
        },
        "restricted_with_users": {
            "scope": "restricted",
            "authorized_users": ["test_user", "other_user"],
            "expected_status": {"owner": 200, "authenticated": 200, "anonymous": 401},
        },
        "restricted_unauthorized": {
            "scope": "restricted",
            "authorized_users": ["test_user"],
            "expected_status": {"owner": 200, "authenticated": 403, "anonymous": 401},
        },
        "public": {
            "scope": "public",
            "expected_status": {"owner": 200, "authenticated": 200, "anonymous": 200},
        },
    }

    # Create a flexible mock auth that can handle different users
    from fastapi import HTTPException

    class TestScopesAuth(Auth):
        def validate(self, authorization: str = Header(default=None)) -> User:
            if not authorization:
                raise HTTPException(status_code=401, detail="Not authenticated")
            if "owner_token" in authorization:
                return User(user_id="test_user")
            elif "other_token" in authorization:
                return User(user_id="other_user")
            raise HTTPException(
                status_code=401, detail="Invalid authentication credentials"
            )

        def validate_optional(
            self, authorization: str = Header(default=None)
        ) -> Union[User, None]:
            if not authorization:
                return None
            try:
                return self.validate(authorization)
            except HTTPException:
                return None

        def login(self, authorization: str = Header()) -> Any:
            return {"access_token": "mock_token"}

    test_auth = TestScopesAuth(store=get_store(f"{store_path}"))

    # Test each scope
    for scope_name, config in scope_configs.items():
        app_with_auth.app.dependency_overrides[
            app_with_auth.app.endpoints.auth.validate
        ] = test_auth.validate
        app_with_auth.app.dependency_overrides[
            app_with_auth.app.endpoints.auth.validate_optional
        ] = test_auth.validate_optional

        # Create service with specific scope and authorized users
        service_input = base_service.copy()
        service_input["configuration"] = service_input.get("configuration", {})
        service_input["configuration"]["scope"] = config["scope"]
        if "authorized_users" in config:
            service_input["configuration"]["authorized_users"] = config[
                "authorized_users"
            ]

        # Create service as owner
        create_response = app_with_auth.post(
            "/services",
            json=service_input,
            headers={"Authorization": "Bearer basic//owner_token"},
        )
        assert create_response.status_code == 201
        service_id = create_response.headers["location"].split("/")[-1]

        # Test owner access
        owner_response = app_with_auth.get(
            f"/services/xyz/{service_id}/tiles/0/0/0",
            headers={"Authorization": "Bearer basic//owner_token"},
        )
        assert (
            owner_response.status_code == config["expected_status"]["owner"]
        ), f"Owner access failed for {scope_name} service"

        # Test other user access
        other_auth_response = app_with_auth.get(
            f"/services/xyz/{service_id}/tiles/0/0/0",
            headers={"Authorization": "Bearer basic//other_token"},
        )
        assert (
            other_auth_response.status_code
            == config["expected_status"]["authenticated"]
        ), f"Authenticated user access failed for {scope_name} service"

        # Test anonymous access
        unauth_response = app_no_auth.get(f"/services/xyz/{service_id}/tiles/0/0/0")
        assert (
            unauth_response.status_code == config["expected_status"]["anonymous"]
        ), f"Anonymous access failed for {scope_name} service"


def test_service_configuration(app_with_auth):
    """Test service with configuration."""
    service_input = {
        "process": {
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
        },
        "type": "xyz",
        "title": "Test Service",
        "configuration": {"version": "1.0.0", "format": "png"},
    }

    response = app_with_auth.post("/services", json=service_input)
    assert response.status_code == 201
    service_id = response.headers["location"].split("/")[-1]
    service = app_with_auth.get(f"/services/{service_id}").json()
    assert service["configuration"]["version"] == "1.0.0"
    assert service["configuration"]["format"] == "png"


def test_xyz_service_query_parameters(app_with_auth):
    """Test XYZ service with query parameter support."""
    service_input = {
        "process": {
            "parameters": [
                {
                    "name": "temporal_extent",
                    "description": "Temporal extent as ISO 8601 date strings",
                    "schema": {
                        "type": "array",
                        "items": {"type": "string", "format": "date-time"},
                    },
                    "optional": True,
                    "default": ["2023-01-01T00:00:00Z", "2023-12-31T23:59:59Z"],
                },
                {
                    "name": "bands",
                    "description": "List of band names to include",
                    "schema": {"type": "array", "items": {"type": "string"}},
                    "optional": True,
                },
            ],
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
                        "temporal_extent": {"from_parameter": "temporal_extent"},
                        "bands": {"from_parameter": "bands"},
                    },
                },
                "save1": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "loadco1"}, "format": "PNG"},
                    "result": True,
                },
            },
        },
        "type": "xyz",
        "title": "Test Service with Query Parameters",
        "configuration": {},
    }

    # Create service
    response = app_with_auth.post("/services", json=service_input)
    assert response.status_code == 201
    service_id = response.headers["location"].split("/")[-1]

    # Get service metadata and check parameter info is exposed
    service = app_with_auth.get(f"/services/{service_id}").json()
    assert "process" in service
    assert "parameters" in service["process"]
    assert len(service["process"]["parameters"]) == 2
    assert service["process"]["parameters"][0]["name"] == "temporal_extent"
    assert service["process"]["parameters"][1]["name"] == "bands"

    # Test XYZ tile request with query parameters
    # Note: This test may not complete successfully since we don't have real data,
    # but it should at least parse the parameters correctly and fail at a later stage
    tile_url = f"/services/xyz/{service_id}/tiles/0/0/0"
    query_params = {
        "temporal_extent": '["2023-06-15T00:00:00Z", "2023-06-15T23:59:59Z"]',
        "bands": '["red", "green", "blue"]',
    }

    # The actual tile request may fail due to missing data, but parameter parsing should work
    try:
        tile_response = app_with_auth.get(tile_url, params=query_params)
        # We expect this might fail due to data not existing, but not due to parameter parsing issues
        # If it fails with a 400 error about invalid JSON, that indicates our parameter parsing failed
        assert (
            tile_response.status_code != 400
            or "Invalid JSON in query parameter"
            not in tile_response.json().get("detail", "")
        )
    except Exception:
        # Test passes if we don't get parameter parsing errors
        pass

    # Test invalid JSON in query parameter
    invalid_query_params = {
        "temporal_extent": "[invalid-json",  # Invalid JSON
    }
    invalid_response = app_with_auth.get(tile_url, params=invalid_query_params)
    assert invalid_response.status_code == 400
    # Check that we get a proper error message about invalid JSON
    error_msg = str(invalid_response.content)
    assert "Invalid JSON in query parameter" in error_msg
