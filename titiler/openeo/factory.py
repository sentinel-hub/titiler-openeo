"""titiler.openeo endpoint Factory."""

import json
from copy import deepcopy
from typing import Annotated, Any, Dict, List, Optional

import morecantile
import pyproj
from attrs import define, field
from fastapi import Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from openeo_pg_parser_networkx import ProcessRegistry
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from rio_tiler.errors import TileOutsideBounds
from starlette.responses import Response

from titiler.core.factory import BaseFactory

from . import __version__ as titiler_version
from .auth import Auth, CredentialsBasic, OIDCAuth
from .errors import InvalidProcessGraph
from .models import openapi
from .models import udp as udp_models
from .models.auth import User
from .services import ServicesStore, TileAssignmentStore, UdpStore
from .stacapi import stacApiBackend

STAC_VERSION = "1.0.0"


@define(kw_only=True)
class EndpointsFactory(BaseFactory):
    """OpenEO Endpoints Factory."""

    services_store: ServicesStore
    udp_store: UdpStore
    tile_store: Optional[TileAssignmentStore] = None
    stac_client: stacApiBackend
    process_registry: ProcessRegistry
    auth: Auth
    default_services_file: Optional[str] = None
    load_nodes_ids: List[str] = field(
        factory=lambda: ["load_collection", "load_collection_and_reduce"]
    )

    def _get_media_type(self, process_graph: Dict[str, Any]) -> str:
        for _, node in process_graph.items():
            if node["process_id"] == "save_result":
                if node["arguments"]["format"] == "PNG":
                    return "image/png"
                elif node["arguments"]["format"] == "JPEG":
                    return "image/jpeg"
                elif node["arguments"]["format"] == "JPEG":
                    return "image/jpg"
                elif node["arguments"]["format"] == "GTiff":
                    return "image/tiff"
                elif node["arguments"]["format"] == "txt":
                    return "text/plain"
                elif (
                    node["arguments"]["format"] == "json"
                    or node["arguments"]["format"] == "metajson"
                ):
                    return "application/json"
                else:
                    return "application/PNG"

        raise ValueError("Couldn't find a `save_result` process in the process graph")

    def get_load_nodes(self, process_graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find all `load_collection/load_collection_and_reduce` processes"""
        return [
            node
            for _, node in process_graph.items()
            if node["process_id"] in self.load_nodes_ids
        ]

    def overwrite_spatial_extent_without_parameters(self, load_node):
        """Overwrite services Spatial Extent."""
        if load_node["arguments"]["spatial_extent"] is None:
            load_node["arguments"]["spatial_extent"] = {}

        load_node["arguments"]["spatial_extent"] = {"from_parameter": "bounding_box"}

        return

    def _parse_query_parameters(self, request: Request) -> dict:
        """Parse query parameters from request, handling JSON and simple types."""
        query_params = {}
        for param_name, param_value in request.query_params.items():
            try:
                # Try to parse as JSON for complex types (arrays, objects)
                if param_value.startswith(("[", "{")):
                    query_params[param_name] = json.loads(param_value)
                else:
                    # Handle simple types
                    # Try to convert to number if possible
                    try:
                        if "." in param_value:
                            query_params[param_name] = float(param_value)
                        else:
                            query_params[param_name] = int(param_value)
                    except ValueError:
                        # Keep as string if not a number
                        query_params[param_name] = param_value
            except json.JSONDecodeError as err:
                raise HTTPException(
                    400,
                    detail=f"Invalid JSON in query parameter '{param_name}': {param_value}",
                ) from err
        return query_params

    def _validate_tile_bounds(self, tile_bounds, service_extent, tms, x, y, z):
        """Validate that tile is within service extent if configured."""
        if service_extent:
            if not tms.crs._pyproj_crs.equals("EPSG:4326"):
                trans = pyproj.Transformer.from_crs(
                    tms.crs._pyproj_crs,
                    pyproj.CRS.from_epsg(4326),
                    always_xy=True,
                )
                tile_bounds = trans.transform_bounds(*tile_bounds, densify_pts=21)

            if not (
                (tile_bounds[0] < service_extent[2])
                and (tile_bounds[2] > service_extent[0])
                and (tile_bounds[3] > service_extent[1])
                and (tile_bounds[1] < service_extent[3])
            ):
                raise TileOutsideBounds(
                    f"Tile(x={x}, y={y}, z={z}) is outside bounds defined by the Service Configuration"
                )

    def register_routes(self):  # noqa: C901
        """Register Routes."""

        ###########################################################################################
        # L1 - Minimal https://openeo.org/documentation/1.0/developers/profiles/api.html#l1-minimal
        ###########################################################################################
        # Capabilities
        # Ref: https://openeo.org/documentation/1.0/developers/profiles/api.html#capabilities
        @self.router.get(
            "/",
            response_class=JSONResponse,
            summary="Information about the back-end",
            response_model=openapi.Capabilities,
            response_model_exclude_none=True,
            operation_id="capabilities",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                },
            },
            tags=["Capabilities"],
        )
        def openeo_root(request: Request):
            """Lists general information about the back-end, including which version
            and endpoints of the openEO API are supported. May also include billing
            information.

            """
            return {
                "api_version": openapi.OPENEO_VERSION,
                "backend_version": titiler_version,
                "stac_version": STAC_VERSION,
                "type": "Catalog",
                "id": "titiler-openeo",
                "title": "TiTiler for OpenEO",
                "description": "TiTiler OpenEO by [DevelopmentSeed](https://developmentseed.org)",
                "endpoints": [
                    {"path": route.path, "methods": route.methods}
                    for route in self.router.routes
                    if isinstance(route, APIRoute)
                ],
                "links": [
                    {
                        "href": self.url_for(request, "openeo_well_known"),
                        "rel": "version-history",
                        "type": "application/json",
                        "title": "List of supported openEO version",
                    },
                    {
                        "href": self.url_for(request, "openeo_collections"),
                        "rel": "data",
                        "title": "List of Datasets",
                        "type": "application/json",
                    },
                ],
                "conformsTo": [
                    "https://api.openeo.org/1.2.0",
                ],
            }

        # File Formats
        # Ref: https://openeo.org/documentation/1.0/developers/profiles/api.html#file-formats
        @self.router.get(
            "/file_formats",
            response_class=JSONResponse,
            summary="Supported file formats",
            response_model=openapi.FileFormats,
            response_model_exclude_none=True,
            operation_id="list-file-types",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                },
            },
            tags=[
                "Capabilities",
                "Data Processing",
            ],
        )
        def openeo_file_formats(request: Request):
            """Lists supported input and output file formats."""
            return {
                "input": {},
                "output": {
                    "JPEG": {
                        "gis_data_types": ["raster"],
                        "title": " Joint Photographic Experts Group",
                        "description": "JPEG is an image format that uses lossy compression and is one of the most widely used image formats. Compared to other EO raster formats, it is less flexible and standardized regarding number of bands, embedding geospatial metadata, etc.",
                        "parameters": {
                            "datatype": {
                                "type": "string",
                                "description": "The values data type.",
                                "enum": ["byte"],
                                "default": "byte",
                            }
                        },
                    },
                    "PNG": {
                        "gis_data_types": ["raster"],
                        "title": "Portable Network Graphics (PNG)",
                        "description": "PNG is a popular raster format used for graphics on the web. Compared to other EO raster formats, it is less flexible and standardized regarding number of bands, embedding geospatial metadata, etc.",
                        "parameters": {
                            "datatype": {
                                "type": "string",
                                "description": "The values data type.",
                                "enum": ["byte", "uint16"],
                                "default": "byte",
                            }
                        },
                    },
                    "GTiff": {
                        "gis_data_types": ["raster"],
                        "title": "GeoTIFF",
                        "description": "GeoTIFF is a public domain metadata standard which allows georeferencing information to be embedded within a TIFF file. The potential additional information includes map projection, coordinate systems, ellipsoids, datums, and everything else necessary to establish the exact spatial reference for the file.",
                        "parameters": {
                            "datatype": {
                                "type": "string",
                                "description": "The values data type.",
                                "enum": [
                                    "byte",
                                    "uint16",
                                    "int16",
                                    "uint32",
                                    "int32",
                                    "float32",
                                    "float64",
                                ],
                                "default": "byte",
                            }
                        },
                    },
                },
            }

        if isinstance(self.auth, OIDCAuth):

            @self.router.get(
                "/credentials/oidc",
                response_class=JSONResponse,
                summary="OpenID Connect authentication",
                response_model=openapi.OIDCProviders,
                response_model_exclude_none=True,
                operation_id="authenticate-oidc",
                responses={
                    200: {
                        "content": {
                            "application/json": {},
                        },
                        "description": "Lists the OpenID Connect Providers.",
                    },
                },
                tags=["Account Management"],
            )
            def openeo_credentials_oidc():
                """Lists the supported OpenID Connect providers (OP)."""
                if not isinstance(self.auth, OIDCAuth):
                    raise HTTPException(
                        status_code=501,
                        detail="OpenID Connect authentication not supported",
                    )

                return openapi.OIDCProviders(
                    providers=[
                        openapi.OIDCProvider(
                            id="oidc",
                            issuer=self.auth.config["issuer"],
                            title=self.auth.settings.oidc.title or "OpenID Connect",
                            scopes=self.auth.settings.oidc.scopes,
                            description=self.auth.settings.oidc.description
                            or "OpenID Connect Provider",
                            default_clients=[
                                openapi.OIDCDefaultClient(
                                    id=self.auth.settings.oidc.client_id,
                                    grant_types=[
                                        "authorization_code+pkce",
                                        "urn:ietf:params:oauth:grant-type:device_code+pkce",
                                        "refresh_token",
                                    ],
                                    redirect_urls=[
                                        self.auth.settings.oidc.redirect_url
                                    ],
                                )
                            ],
                        )
                    ]
                )
        else:

            @self.router.get(
                "/credentials/basic",
                response_class=JSONResponse,
                summary="HTTP Basic authentication",
                response_model=CredentialsBasic,
                response_model_exclude_none=True,
                operation_id="authenticate-basic",
                responses={
                    200: {
                        "content": {
                            "application/json": {},
                        },
                    },
                },
                tags=["Account Management"],
            )
            def openeo_credentials_basic(token=Depends(self.auth.login)):
                """Checks the credentials provided through [HTTP Basic Authentication
                according to RFC 7617](https://www.rfc-editor.org/rfc/rfc7617.html) and returns
                an access token for valid credentials.

                """
                return token

        @self.router.get(
            "/me",
            response_class=JSONResponse,
            summary="Get information about the authenticated user",
            response_model=User,
            response_model_exclude_none=True,
            operation_id="get-current-user",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                    "description": "Information about the currently authenticated user.",
                },
                401: {
                    "description": "The request could not be fulfilled since it was not authenticated.",
                },
            },
            tags=["Account Management"],
        )
        def openeo_me(user=Depends(self.auth.validate)):
            """Get information about the currently authenticated user."""
            return user

        # Well-Known Document
        # Ref: https://openeo.org/documentation/1.0/developers/profiles/api.html#well-known-discovery
        @self.router.get(
            "/.well-known/openeo",
            response_class=JSONResponse,
            summary="Supported openEO versions",
            response_model=openapi.openEOVersions,
            response_model_exclude_none=True,
            operation_id="connect",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                },
            },
            tags=["Capabilities"],
        )
        def openeo_well_known(request: Request):
            """Lists all implemented openEO versions supported by the service provider. This endpoint is the Well-Known URI (see RFC 5785) for openEO."""
            return {
                "versions": [
                    {
                        "url": self.url_for(request, "openeo_root"),
                        "api_version": openapi.OPENEO_VERSION,
                    },
                ]
            }

        # Pre-defined Processes
        # Ref: https://openeo.org/documentation/1.0/developers/profiles/api.html#pre-defined-processes
        @self.router.get(
            "/processes",
            response_class=JSONResponse,
            summary="Supported predefined processes",
            response_model=openapi.Processes,
            response_model_exclude_none=True,
            operation_id="list-processes",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                },
            },
            tags=["Process Discovery"],
        )
        def openeo_processes(request: Request):
            """Lists all predefined processes and returns detailed process descriptions, including parameters and return values."""
            processes = [
                process.spec for process in self.process_registry[None].values()
            ]
            return {"processes": processes, "links": []}

        # Collections
        # Ref: https://openeo.org/documentation/1.0/developers/profiles/api.html#collections
        @self.router.get(
            "/collections",
            response_class=JSONResponse,
            summary="Basic metadata for all datasets",
            response_model=openapi.Collections,
            response_model_exclude_none=True,
            operation_id="list-collections",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                },
            },
            tags=["EO Data Discovery"],
        )
        def openeo_collections(request: Request):
            """Lists available collections with at least the required information."""
            collections = self.stac_client.get_collections()
            for collection in collections:
                # TODO: add links
                collection["links"] = []

            return {
                "collections": collections,
                "links": [],
            }

        # CollectionsId
        # Ref: https://openeo.org/documentation/1.0/developers/profiles/api.html#collections
        @self.router.get(
            r"/collections/{collection_id}",
            response_class=JSONResponse,
            summary="Full metadata for a specific dataset",
            response_model=openapi.Collection,
            response_model_exclude_none=True,
            operation_id="describe-collection",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                },
            },
            tags=["EO Data Discovery"],
        )
        def openeo_collection(
            request: Request,
            collection_id: Annotated[
                str,
                Path(description="STAC Collection Identifier"),
            ],
        ):
            """Lists **all** information about a specific collection specified by the identifier `collection_id`."""
            collection = self.stac_client.get_collection(collection_id)

            # TODO: add links
            collection["links"] = []

            return collection

        #############################################################################################
        # L3 - Advanced https://openeo.org/documentation/1.0/developers/profiles/api.html#l3-advanced
        #############################################################################################
        @self.router.get(
            "/conformance",
            response_class=JSONResponse,
            summary="Conformance classes this API implements",
            response_model=openapi.Conformance,
            response_model_exclude_none=True,
            operation_id="conformance",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                },
            },
            tags=["Capabilities"],
        )
        def openeo_conformance():
            """Lists all conformance classes specified in various standards that the implementation conforms to."""
            return {
                "conformsTo": [
                    "https://api.openeo.org/1.2.0",
                ]
            }

        @self.router.get(
            "/services",
            response_class=JSONResponse,
            summary="List all web services",
            response_model=openapi.Services,
            response_model_exclude_none=True,
            operation_id="list-services",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                    "description": "Array of secondary web service descriptions",
                },
            },
            tags=["Secondary Services"],
        )
        def openeo_services(request: Request, user=Depends(self.auth.validate)):
            """Lists all secondary web services."""
            services = self.services_store.get_user_services(user.user_id)

            # If services list is empty and default_services_file is configured, load default services
            if not services and self.default_services_file:
                try:
                    import json
                    import os

                    # Check if the file exists
                    if os.path.exists(self.default_services_file):
                        with open(self.default_services_file, "r") as f:
                            default_services_config = json.load(f)

                        # Create each service using the service configuration
                        for _, service_data in default_services_config[
                            "services"
                        ].items():
                            # Extract just the service configuration, ignoring id and user_id
                            service_config = service_data.get("service", {})
                            if "id" in service_config:
                                del service_config[
                                    "id"
                                ]  # Remove the id as it will be generated

                            # Create the service
                            body = openapi.ServiceInput(**service_config)
                            self.services_store.add_service(
                                user.user_id, body.model_dump()
                            )

                        # Reload services after adding defaults
                        services = self.services_store.get_user_services(user.user_id)
                except Exception as e:
                    # Log the error but continue without default services
                    import logging

                    logging.error(f"Failed to load default services: {str(e)}")

            return {
                "services": [
                    {
                        **service,
                        "url": self.url_for(
                            request,
                            "openeo_xyz_service",
                            service_id=service["id"],
                            z="{z}",
                            x="{x}",
                            y="{y}",
                        ),
                    }
                    for service in services
                ],
                "links": [
                    {
                        "href": self.url_for(
                            request, "openeo_service", service_id=service["id"]
                        ),
                        "rel": "related",
                    }
                    for service in services
                ],
            }

        @self.router.get(
            "/services/{service_id}",
            response_class=JSONResponse,
            summary="Full metadata for a service",
            response_model=openapi.Service,
            response_model_exclude_none=True,
            operation_id="describe-service",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                    "description": "Details of the created service",
                },
            },
            tags=["Secondary Services"],
        )
        def openeo_service(
            request: Request,
            service_id: str = Path(
                description="A per-backend unique identifier of the secondary web service, generated by the back-end during creation. MUST match the specified pattern.",
            ),
            user=Depends(self.auth.validate),
        ):
            """Lists all information about a secondary web service."""
            service = self.services_store.get_service(service_id)
            if not service:
                raise HTTPException(404, f"Could not find service: {service_id}")
            return {
                **service,
                "url": self.url_for(
                    request,
                    "openeo_xyz_service",
                    service_id=service["id"],
                    z="{z}",
                    x="{x}",
                    y="{y}",
                ),
            }

        @self.router.get(
            "/process_graphs",
            response_class=JSONResponse,
            summary="List user-defined processes",
            response_model=udp_models.UserProcesses,
            response_model_exclude_none=True,
            operation_id="list-udp",
            tags=["Data Processing"],
        )
        def list_udps(
            request: Request,
            limit: int = Query(
                100, ge=0, description="Maximum number of UDPs to return"
            ),
            offset: int = Query(0, ge=0, description="Offset for pagination"),
            user=Depends(self.auth.validate),
        ):
            """List UDPs for the authenticated user."""
            udps = self.udp_store.list_udps(
                user_id=user.user_id, limit=limit, offset=offset
            )

            processes = []
            for udp in udps:
                processes.append(
                    {
                        "id": udp["id"],
                        "summary": udp.get("summary"),
                        "description": udp.get("description"),
                        "parameters": udp.get("parameters"),
                        "returns": udp.get("returns"),
                        "categories": udp.get("categories", []),
                        "deprecated": udp.get("deprecated", False),
                        "experimental": udp.get("experimental", False),
                        "process_graph": udp["process_graph"],
                    }
                )

            links = [
                {
                    "href": self.url_for(request, "list_udps"),
                    "rel": "self",
                }
            ]

            return {"processes": processes, "links": links}

        @self.router.get(
            "/process_graphs/{process_graph_id}",
            response_class=JSONResponse,
            summary="Full metadata for a user-defined process",
            response_model=udp_models.UserProcess,
            response_model_exclude_none=True,
            operation_id="describe-custom-process",
            tags=["Data Processing"],
        )
        def get_udp(
            process_graph_id: str,
            user=Depends(self.auth.validate),
        ):
            """Return full UDP definition for the authenticated user."""
            udp = self.udp_store.get_udp(user_id=user.user_id, udp_id=process_graph_id)
            if not udp:
                raise HTTPException(404, f"Could not find UDP: {process_graph_id}")

            process = {
                "id": udp["id"],
                "summary": udp.get("summary"),
                "description": udp.get("description"),
                "parameters": udp.get("parameters"),
                "returns": udp.get("returns"),
                "categories": udp.get("categories", []),
                "deprecated": udp.get("deprecated", False),
                "experimental": udp.get("experimental", False),
                "process_graph": udp["process_graph"],
            }

            for extra_field in ("exceptions", "examples", "links"):
                if udp.get(extra_field) is not None:
                    process[extra_field] = udp[extra_field]

            return process

        @self.router.delete(
            "/process_graphs/{process_graph_id}",
            response_class=Response,
            status_code=204,
            summary="Delete a user-defined process",
            operation_id="delete-custom-process",
            tags=["Data Processing"],
        )
        def delete_udp(
            process_graph_id: str,
            user=Depends(self.auth.validate),
        ):
            """Delete a UDP for the authenticated user."""
            try:
                self.udp_store.delete_udp(user_id=user.user_id, udp_id=process_graph_id)
            except ValueError as err:
                raise HTTPException(
                    404, f"Could not find UDP: {process_graph_id}"
                ) from err

            return Response(status_code=204)

        @self.router.post(
            "/validation",
            response_class=JSONResponse,
            summary="Validate a user-defined process (graph)",
            response_model=Dict[str, List[Dict[str, Any]]],
            response_model_exclude_none=True,
            operation_id="validate-custom-process",
            tags=["Data Processing"],
        )
        def validate_process_graph(
            body: udp_models.UserProcess,
            user=Depends(self.auth.validate_optional),
        ):
            """Validate a process graph without executing it."""
            errors: List[Dict[str, Any]] = []

            raw_pg = body.model_dump().get("process_graph") or {}

            # Basic per-node validation against registry specs (structure and required params)
            for node_id, node in raw_pg.items():
                node_dict = (
                    node
                    if isinstance(node, dict)
                    else node.model_dump()
                    if hasattr(node, "model_dump")
                    else None
                )
                if not isinstance(node_dict, dict):
                    errors.append(
                        {
                            "code": "ProcessGraphInvalid",
                            "message": f"Process node '{node_id}' must be an object",
                        }
                    )
                    continue

                process_id = node_dict.get("process_id")
                if not process_id:
                    errors.append(
                        {
                            "code": "ProcessGraphInvalid",
                            "message": f"Process node '{node_id}' missing process_id",
                        }
                    )
                    continue

                if process_id not in self.process_registry[None]:
                    errors.append(
                        {
                            "code": "ProcessUnsupported",
                            "message": f"Process '{process_id}' not found in registry",
                        }
                    )
                    continue

                spec = self.process_registry[process_id].spec
                required_params = [
                    p["name"]
                    for p in spec.get("parameters", [])
                    if not p.get("optional", False)
                ]
                args = node_dict.get("arguments", {}) or {}
                for param_name in required_params:
                    if param_name not in args or args.get(param_name) is None:
                        errors.append(
                            {
                                "code": "ProcessParameterMissing",
                                "message": f"Required parameter '{param_name}' missing for process '{process_id}'",
                            }
                        )

            if errors:
                return {"errors": errors}

            try:
                parsed_graph = OpenEOProcessGraph(pg_data=body.model_dump())
            except Exception as err:  # noqa: BLE001
                errors.append(
                    {
                        "code": "ProcessGraphInvalid",
                        "message": str(err),
                    }
                )
                return {"errors": errors}

            # Validate supported processes
            for _, node in parsed_graph.nodes:
                process_id = node.get("process_id")
                if process_id and process_id not in self.process_registry[None]:
                    errors.append(
                        {
                            "code": "ProcessUnsupported",
                            "message": f"Process '{process_id}' not found in registry",
                        }
                    )
                    continue

                if not process_id:
                    errors.append(
                        {
                            "code": "ProcessGraphInvalid",
                            "message": "Process node missing process_id",
                        }
                    )
                    continue

            # Validate argument schema against registry
            try:
                parsed_graph.to_callable(process_registry=self.process_registry)
            except Exception as err:  # noqa: BLE001
                msg = str(err)
                # Unresolvable parameters should not yield validation errors
                lower_msg = msg.lower()
                if "from_parameter" in lower_msg or "from parameter" in lower_msg:
                    # ignore unresolved user-supplied parameters
                    pass
                else:
                    errors.append({"code": "ProcessGraphInvalid", "message": msg})

            return {"errors": errors}

        @self.router.put(
            "/process_graphs/{process_graph_id}",
            response_class=JSONResponse,
            summary="Store a user-defined process",
            response_model=udp_models.UserProcess,
            response_model_exclude_none=True,
            operation_id="store-custom-process",
            tags=["Data Processing"],
        )
        def upsert_udp(
            process_graph_id: str,
            body: udp_models.UserProcess,
            user=Depends(self.auth.validate),
        ):
            """Create or replace a UDP for the authenticated user."""
            data = body.model_dump(exclude_none=True)
            # Enforce path ID over body ID to satisfy spec
            data["id"] = process_graph_id

            # Basic validation: ensure process graph exists
            if "process_graph" not in data or not data["process_graph"]:
                raise HTTPException(
                    400,
                    "process_graph is required and must not be empty",
                )

            # Validate processes exist in registry and required params are present
            for node_id, node in data["process_graph"].items():
                node_dict = (
                    node
                    if isinstance(node, dict)
                    else node.model_dump()
                    if hasattr(node, "model_dump")
                    else None
                )
                if not isinstance(node_dict, dict):
                    raise InvalidProcessGraph(
                        f"Process node '{node_id}' must be an object"
                    )

                process_id = node_dict.get("process_id")
                if not process_id:
                    raise InvalidProcessGraph("Process node missing process_id")

                if process_id not in self.process_registry[None]:
                    raise InvalidProcessGraph(
                        f"Process '{process_id}' not found in registry"
                    )

                spec = self.process_registry[process_id].spec
                required_params = [
                    p["name"]
                    for p in spec.get("parameters", [])
                    if not p.get("optional", False)
                ]
                args = node_dict.get("arguments", {}) or {}
                for param_name in required_params:
                    if param_name not in args or args.get(param_name) is None:
                        raise InvalidProcessGraph(
                            f"Required parameter '{param_name}' missing for process '{process_id}'"
                        )

            # Validate argument schema (will raise InvalidProcessGraph on failure)
            try:
                parsed_graph = OpenEOProcessGraph(pg_data=data)
                parsed_graph.to_callable(process_registry=self.process_registry)
            except Exception as err:  # noqa: BLE001
                raise InvalidProcessGraph(f"Invalid process graph: {str(err)}") from err

            self.udp_store.upsert_udp(
                user_id=user.user_id,
                udp_id=process_graph_id,
                process_graph=data["process_graph"],
                parameters=data.get("parameters"),
                summary=data.get("summary"),
                description=data.get("description"),
                returns=data.get("returns"),
                categories=data.get("categories"),
                deprecated=data.get("deprecated", False),
                experimental=data.get("experimental", False),
                exceptions=data.get("exceptions"),
                examples=data.get("examples"),
                links=data.get("links"),
            )

            stored = self.udp_store.get_udp(
                user_id=user.user_id, udp_id=process_graph_id
            )
            if not stored:
                raise HTTPException(404, f"Could not find UDP: {process_graph_id}")

            return {
                "id": stored["id"],
                "summary": stored.get("summary"),
                "description": stored.get("description"),
                "parameters": stored.get("parameters"),
                "returns": stored.get("returns"),
                "categories": stored.get("categories", []),
                "deprecated": stored.get("deprecated", False),
                "experimental": stored.get("experimental", False),
                "process_graph": stored["process_graph"],
                "exceptions": stored.get("exceptions"),
                "examples": stored.get("examples"),
                "links": stored.get("links"),
            }

        @self.router.post(
            "/services",
            response_class=Response,
            summary="Publish a new service",
            response_model=openapi.Service,
            response_model_exclude_none=True,
            operation_id="create-service",
            responses={
                201: {
                    "headers": {
                        "Location": {
                            "description": "URL to the newly created service metadata",
                            "schema": {"type": "string"},
                        },
                        "OpenEO-Identifier": {
                            "description": "Unique identifier for the created service",
                            "schema": {"type": "string"},
                        },
                    },
                    "description": "The service has been created successfully.",
                },
                400: {
                    "description": "The request could not be fulfilled due to an error in the request content.",
                },
                401: {
                    "description": "The request could not be fulfilled since it was not authenticated.",
                },
                403: {
                    "description": "The request is not allowed.",
                },
                422: {
                    "description": "The request could not be fulfilled due to an error in the request content.",
                },
            },
            tags=["Secondary Services"],
        )
        def openeo_service_create(
            request: Request,
            body: openapi.ServiceInput,
            user=Depends(self.auth.validate),
        ):
            """Creates a new secondary web service."""
            service_def = body.model_dump()

            try:
                # Parse and validate process graph structure
                parsed_graph = OpenEOProcessGraph(pg_data=service_def["process"])

                # Check if all processes exist in registry
                for node in parsed_graph.nodes:
                    process_id = node[1].get("process_id")
                    if process_id and process_id not in self.process_registry[None]:
                        raise InvalidProcessGraph(
                            f"Process '{process_id}' not found in registry"
                        )

                # Try to create callable to validate parameter types
                parsed_graph.to_callable(process_registry=self.process_registry)

            except Exception as e:
                raise InvalidProcessGraph(f"Invalid process graph: {str(e)}") from e

            # Check process and type are present
            if not body.process or not body.type:
                raise HTTPException(
                    422,
                    detail="Both 'process' and 'type' fields are required.",
                )

            for node in self.get_load_nodes(service_def["process"]["process_graph"]):
                self.overwrite_spatial_extent_without_parameters(node)

            service_id = self.services_store.add_service(user.user_id, service_def)
            service = self.services_store.get_service(service_id)
            if not service:
                raise HTTPException(404, f"Could not find service: {service_id}")

            service_url = self.url_for(
                request, "openeo_service", service_id=service["id"]
            )

            return Response(
                status_code=201,
                headers={
                    "Location": service_url,
                    "openeo-identifier": service["id"],
                },
            )

        @self.router.delete(
            "/services/{service_id}",
            response_class=Response,
            summary="Delete a service",
            operation_id="delete-service",
            responses={
                204: {
                    "description": "The service has been successfully deleted.",
                },
                400: {
                    "description": "The request could not be fulfilled due to an error in the request content.",
                },
                401: {
                    "description": "The request could not be fulfilled since it was not authenticated.",
                },
                403: {
                    "description": "The request is not allowed.",
                },
                404: {
                    "description": "The service with the specified identifier does not exist.",
                },
            },
            tags=["Secondary Services"],
        )
        def openeo_service_delete(
            service_id: str = Path(
                description="A per-backend unique identifier of the secondary web service.",
            ),
            user=Depends(self.auth.validate),
        ):
            """Deletes all data related to this secondary web service."""
            self.services_store.delete_service(service_id)
            return Response(status_code=204)

        @self.router.patch(
            "/services/{service_id}",
            response_class=Response,
            summary="Modify a service",
            operation_id="update-service",
            responses={
                204: {
                    "description": "The service has been successfully modified.",
                },
                400: {
                    "description": "The request could not be fulfilled due to an error in the request content.",
                },
                401: {
                    "description": "The request could not be fulfilled since it was not authenticated.",
                },
                403: {
                    "description": "The request is not allowed.",
                },
                404: {
                    "description": "The service with the specified identifier does not exist.",
                },
            },
            tags=["Secondary Services"],
        )
        def openeo_service_update(
            body: openapi.ServiceUpdateInput,
            service_id: str = Path(
                description="A per-backend unique identifier of the secondary web service.",
            ),
            user=Depends(self.auth.validate),
        ):
            """Updates an existing secondary web service."""
            # Get existing service
            existing = self.services_store.get_service(service_id)
            if not existing:
                raise HTTPException(404, f"Could not find service: {service_id}")

            # For PATCH, we need to merge new data with existing, keeping existing fields if not provided
            if body:
                update_data = {}
                # Only include non-None fields from the update
                body_data = body.model_dump(exclude_none=True)

                # Start with existing service data
                update_data = existing.copy()
                # Remove id since it's a special field from get_service
                if "id" in update_data:
                    del update_data["id"]

                # Update with any new values provided
                update_data.update(body_data)
            else:
                update_data = existing
                if "id" in update_data:
                    del update_data["id"]

            self.services_store.update_service(user.user_id, service_id, update_data)
            return Response(status_code=204)

        @self.router.get(
            "/service_types",
            response_class=JSONResponse,
            summary="Supported secondary web service protocols",
            response_model=openapi.ServiceTypes,
            response_model_exclude_none=True,
            operation_id="list-service-types",
            responses={
                200: {
                    "content": {
                        "application/json": {},
                    },
                    "description": "An object with a map containing all service names as keys and an object that defines supported configuration settings and process parameters.",
                },
            },
            tags=["Capabilities", "Secondary Services"],
        )
        def openeo_service_types(request: Request):
            """Lists supported secondary web service protocols."""
            return {
                "XYZ": {
                    "configuration": {
                        "tile_size": {
                            "default": 256,
                            "description": "Tile size in pixels.",
                            "type": "number",
                        },
                        "extent": {
                            "description": "Limits the XYZ service to the specified bounding box. In form of `[West, South, East, North]` in EPSG:4326 CRS.",
                            "type": "object",
                            "required": False,
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {
                                "type": "number",
                                "description": "Extent value",
                            },
                        },
                        "minzoom": {
                            "default": 0,
                            "description": "Minimum Zoom level for the XYZ service.",
                            "type": "number",
                        },
                        "maxzoom": {
                            "default": 24,
                            "description": "Maximum Zoom level for the XYZ service.",
                            "type": "number",
                        },
                        "tilematrixset": {
                            "description": "TileMatrixSetId for the tiling grid to use.",
                            "type": "string",
                            "enum": [
                                "CanadianNAD83_LCC",
                                "EuropeanETRS89_LAEAQuad",
                                "WGS1984Quad",
                                "WebMercatorQuad",
                                "WorldCRS84Quad",
                                "WorldMercatorWGS84Quad",
                            ],
                            "default": "WebMercatorQuad",
                        },
                        "scope": {
                            "description": "Service access scope. private: only owner can access; restricted: any authenticated user can access; public: no authentication required",
                            "type": "string",
                            "enum": ["private", "restricted", "public"],
                            "default": "public",
                        },
                        "authorized_users": {
                            "description": "List of user IDs authorized to access the service when scope is restricted. If not specified, all authenticated users can access.",
                            "type": "array",
                            "items": {"type": "string", "description": "User ID"},
                            "required": False,
                        },
                    },
                    "process_parameters": [
                        {
                            "name": "spatial_extent_west",
                            "description": "The lower left corner for coordinate axis 1 of the extent currently shown to the consumer.",
                            "schema": {"type": "number"},
                        },
                        {
                            "name": "spatial_extent_south",
                            "description": "The lower left corner for coordinate axis 2 of the extent currently shown to the consumer.",
                            "schema": {"type": "number"},
                        },
                        {
                            "name": "spatial_extent_east",
                            "description": "The upper right corner for coordinate axis 1 of the extent currently shown to the consumer.",
                            "schema": {"type": "number"},
                        },
                        {
                            "name": "spatial_extent_north",
                            "description": "The upper right corner for coordinate axis 2 of the extent currently shown to the consumer.",
                            "schema": {"type": "number"},
                        },
                        {
                            "name": "spatial_extent_crs",
                            "description": "The Coordinate reference system of the extent.",
                            "schema": [
                                {
                                    "title": "EPSG Code",
                                    "type": "integer",
                                    "subtype": "epsg-code",
                                    "minimum": 1000,
                                },
                                {
                                    "title": "WKT2",
                                    "type": "string",
                                    "subtype": "wkt2-definition",
                                },
                            ],
                        },
                    ],
                    "title": "XYZ tiled web map",
                }
            }

        @self.router.post(
            "/result",
            response_class=Response,
            summary="Process and download data synchronously",
            response_model=openapi.Service,
            response_model_exclude_none=True,
            operation_id="compute-result",
            responses={
                200: {
                    "content": {
                        "image/png": {},
                        "image/jpeg": {},
                        "image/jpg": {},
                    },
                    "description": "Return an image.",
                }
            },
            tags=["Data Processing"],
        )
        def openeo_result(
            request: Request,
            body: openapi.ResultRequest,
            user=Depends(self.auth.validate),
        ):
            """Executes a user-defined process directly (synchronously) and the result will be
            downloaded in the format specified in the process graph.

            """
            process = body.process.model_dump()

            # Parse query parameters for dynamic parameter substitution
            query_params = self._parse_query_parameters(request)
            query_params["_openeo_user"] = user

            # Set default parameter values from process definition
            parameters = query_params.copy()
            for param in process.get("parameters") or []:
                param_name = param.get("name")
                if param_name and param_name not in parameters:
                    default_value = param.get("default")
                    if default_value is not None:
                        parameters[param_name] = default_value

            parsed_graph = OpenEOProcessGraph(pg_data=process)
            pg_callable = parsed_graph.to_callable(
                process_registry=self.process_registry,
                parameters=process.get("parameters"),
            )
            result = pg_callable(named_parameters=parameters)

            media_type = result.media_type if hasattr(result, "media_type") else None
            if not media_type and isinstance(result, str):
                media_type = "text/plain"
            elif not media_type:
                media_type = "application/octet-stream"

            data = result.data if hasattr(result, "data") else result

            # if the result is not a SaveResultData object, convert it to one
            # if not isinstance(result, SaveResultData):
            #     result = save_result(result, "GTiff")

            return Response(data, media_type=media_type)

        @self.router.get(
            "/services/xyz/{service_id}/tiles/{z}/{x}/{y}",
            responses={
                200: {
                    "content": {
                        "image/png": {},
                        "image/jpeg": {},
                        "image/jpg": {},
                    },
                    "description": "Return an image.",
                }
            },
            response_class=Response,
            operation_id="tile-service",
            tags=["Secondary Services"],
        )
        def openeo_xyz_service(
            request: Request,
            service_id: Annotated[
                str,
                Path(
                    description="A per-backend unique identifier of the secondary web service, generated by the back-end during creation.",
                ),
            ],
            z: Annotated[
                int,
                Path(
                    description="Identifier (Z) selecting one of the scales defined in the TileMatrixSet and representing the scaleDenominator the tile.",
                ),
            ],
            x: Annotated[
                int,
                Path(
                    description="Column (X) index of the tile on the selected TileMatrix. It cannot exceed the MatrixHeight-1 for the selected TileMatrix.",
                ),
            ],
            y: Annotated[
                int,
                Path(
                    description="Row (Y) index of the tile on the selected TileMatrix. It cannot exceed the MatrixWidth-1 for the selected TileMatrix.",
                ),
            ],
            user=Depends(self.auth.validate_optional),
        ):
            """Create map tile."""
            from titiler.openeo.services.auth import ServiceAuthorizationManager

            service = self.services_store.get_service(service_id)
            if service is None:
                raise HTTPException(404, f"Could not find service: {service_id}")

            # Authorize service access
            auth_manager = ServiceAuthorizationManager()
            auth_manager.authorize(service, user)

            # Get service configuration
            configuration = service.get("configuration") or {}
            tilematrixset = configuration.get("tilematrixset", "WebMercatorQuad")
            tilesize = configuration.get("tile_size", 256)
            tms = morecantile.tms.get(tilematrixset)

            minzoom = configuration.get("minzoom") or tms.minzoom
            maxzoom = configuration.get("maxzoom") or tms.maxzoom
            if z < minzoom or z > maxzoom:
                raise HTTPException(
                    400,
                    f"Invalid ZOOM level {z}. Should be between {minzoom} and {maxzoom}",
                )

            process = deepcopy(service["process"])

            load_nodes = self.get_load_nodes(process["process_graph"])

            # Check that nodes have spatial-extent
            assert all(
                node["arguments"].get("spatial_extent") for node in load_nodes
            ), "Invalid `load` process, Missing spatial_extent"
            # Force size to tile size
            for node in load_nodes:
                node["arguments"]["width"] = tilesize
                node["arguments"]["height"] = tilesize

            tile_bounds = list(tms.xy_bounds(morecantile.Tile(x=x, y=y, z=z)))

            # Parse query parameters for dynamic parameter substitution
            query_params = self._parse_query_parameters(request)
            if self.tile_store:
                query_params["_openeo_tile_store"] = self.tile_store

            query_params["_openeo_user"] = user

            parameters = {
                "spatial_extent_west": tile_bounds[0],
                "spatial_extent_south": tile_bounds[1],
                "spatial_extent_east": tile_bounds[2],
                "spatial_extent_north": tile_bounds[3],
                "spatial_extent_crs": tms.crs.to_epsg() or tms.crs.to_wkt(),
                "target_crs": tms.crs.to_epsg() or tms.crs.to_wkt(),
                "bounding_box": BoundingBox(
                    west=tile_bounds[0],
                    east=tile_bounds[2],
                    south=tile_bounds[1],
                    north=tile_bounds[3],
                    crs=tms.crs.to_epsg(),
                ),
                "tile_x": x,
                "tile_y": y,
                "tile_z": z,
                **query_params,  # Merge query parameters, they override tile params if same name
            }

            # now,with the default parameters from the service configuration, We will fill the default values
            for param in process.get("parameters") or []:
                param_name = param.get("name")
                if param_name and param_name not in parameters:
                    default_value = param.get("default")
                    if default_value is not None:
                        parameters[param_name] = default_value

            # Validate tile bounds against service extent
            service_extent = configuration.get("extent")
            self._validate_tile_bounds(tile_bounds, service_extent, tms, x, y, z)

            media_type = self._get_media_type(process["process_graph"])

            parsed_graph = OpenEOProcessGraph(pg_data=process)
            pg_callable = parsed_graph.to_callable(
                process_registry=self.process_registry,
                parameters=process.get("parameters"),
                # parameters=args,  # Use built-in parameter substitution instead of manual
            )

            img = pg_callable(named_parameters=parameters)
            return Response(img.data, media_type=media_type)
