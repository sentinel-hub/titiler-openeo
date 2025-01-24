"""Test titiler.openeo services."""


def test_add_service(app):
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

    response = app.post("/services", json=service_input)
    assert response.status_code == 201
    # check the location header
    assert response.headers["Location"] is not None
    # save the service id from the location for later
    assert response.headers["OpenEO-Identifier"] is not None


def test_get_services(app):
    """Test getting all services."""
    response = app.get("/services")
    assert response.status_code == 200
    services = response.json()
    assert "services" in services
    assert isinstance(services["services"], list)
    assert "links" in services


def test_get_service(app):
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
    create_response = app.post("/services", json=service_input)
    assert create_response.status_code == 201
    service_id = create_response.headers["location"].split("/")[-1]
    get_response = app.get(f"/services/{service_id}")
    assert get_response.status_code == 200
    service = get_response.json()
    assert service["id"] == service_id
    assert service["title"] == service_input["title"]
    assert service["type"] == service_input["type"]


def test_get_user_services(app):
    """Test getting services for a specific user."""
    response = app.get("/services", params={"user_id": "test_user"})
    assert response.status_code == 200
    services = response.json()
    assert "services" in services
    assert isinstance(services["services"], list)


def test_update_service(app):
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
    create_response = app.post("/services", json=service_input)
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
    response = app.patch(f"/services/{service_id}", json=update_input)
    assert response.status_code == 204


def test_delete_service(app):
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
    create_response = app.post("/services", json=service_input)
    assert create_response.status_code == 201
    service_id = create_response.headers["location"].split("/")[-1]

    # Delete the service
    response = app.delete(f"/services/{service_id}")
    assert response.status_code == 204

    # Verify it's deleted
    get_response = app.get(f"/services/{service_id}")
    assert get_response.status_code == 404


def test_service_validation(app):
    """Test service input validation."""
    # Missing required process
    invalid_input = {"type": "xyz", "title": "Test Service"}
    response = app.post("/services", json=invalid_input)
    assert response.status_code == 422

    # Invalid process graph
    invalid_input = {
        "process": {
            "process_graph": {
                "loadco1": {"process_id": "invalid_process", "arguments": {}},
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
    response = app.post("/services", json=invalid_input)
    assert response.status_code == 400


def test_service_configuration(app):
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

    response = app.post("/services", json=service_input)
    assert response.status_code == 201
    service_id = response.headers["location"].split("/")[-1]
    service = app.get(f"/services/{service_id}").json()
    assert service["configuration"]["version"] == "1.0.0"
    assert service["configuration"]["format"] == "png"
