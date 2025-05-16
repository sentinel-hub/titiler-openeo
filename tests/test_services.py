"""Test titiler.openeo services."""

from typing import Any

from fastapi import Header

from titiler.openeo.auth import Auth, User


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


def test_service_xyz_access_scopes(app_with_auth, app_no_auth):
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
        ) -> User | None:
            if not authorization:
                return None
            try:
                return self.validate(authorization)
            except HTTPException:
                return None

        def login(self, authorization: str = Header()) -> Any:
            return {"access_token": "mock_token"}

    test_auth = TestScopesAuth()

    # Test each scope
    for scope_name, config in scope_configs.items():
        app_with_auth.app.dependency_overrides[
            app_with_auth.app.endpoints.auth.validate
        ] = test_auth.validate
        app_with_auth.app.dependency_overrides[
            app_with_auth.app.endpoints.auth.validate_optional
        ] = test_auth.validate_optional

        # Create service with specific scope
        service_input = base_service.copy()
        service_input["configuration"] = service_input.get("configuration", {})
        service_input["configuration"]["scope"] = config["scope"]

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
