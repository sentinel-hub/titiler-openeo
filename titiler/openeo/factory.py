"""titiler.openeo endpoint Factory."""

from copy import deepcopy

import morecantile
from attrs import define, field
from fastapi import Depends, Path
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from openeo_pg_parser_networkx import ProcessRegistry
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pyproj import Transformer
from rio_tiler.errors import TileOutsideBounds
from starlette.requests import Request
from starlette.responses import Response
from typing_extensions import Annotated

from titiler.core.factory import BaseFactory
from titiler.openeo import __version__ as titiler_version
from titiler.openeo import models
from titiler.openeo.auth import Auth, CredentialsBasic, FakeBasicAuth
from titiler.openeo.models import OPENEO_VERSION
from titiler.openeo.services import ServicesStore
from titiler.openeo.stacapi import stacApiBackend

STAC_VERSION = "1.0.0"


@define(kw_only=True)
class EndpointsFactory(BaseFactory):
    """OpenEO Endpoints Factory."""

    services_store: ServicesStore
    stac_client: stacApiBackend
    process_registry: ProcessRegistry
    auth: Auth = field(factory=FakeBasicAuth)

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
            response_model=models.Capabilities,
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
                "api_version": OPENEO_VERSION,
                "backend_version": titiler_version,
                "stac_version": STAC_VERSION,
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
            response_model=models.FileFormats,
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
                },
            }

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

        # Well-Known Document
        # Ref: https://openeo.org/documentation/1.0/developers/profiles/api.html#well-known-discovery
        @self.router.get(
            "/.well-known/openeo",
            response_class=JSONResponse,
            summary="Supported openEO versions",
            response_model=models.openEOVersions,
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
                        "api_version": OPENEO_VERSION,
                    },
                ]
            }

        # Pre-defined Processes
        # Ref: https://openeo.org/documentation/1.0/developers/profiles/api.html#pre-defined-processes
        @self.router.get(
            "/processes",
            response_class=JSONResponse,
            summary="Supported predefined processes",
            response_model=models.Processes,
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
            response_model=models.Collections,
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
            response_model=models.Collection,
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
            response_model=models.Conformance,
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
            response_model=models.Services,
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
            response_model=models.Service,
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

        # TODO: Next Phase
        # @self.router.post(
        #     "/services",
        #     response_class=JSONResponse,
        #     summary="Publish a new service",
        #     response_model=models.Service,
        #     response_model_exclude_none=True,
        #     operation_id="create-service",
        #     responses={
        #         201: {
        # .            "headers": {},
        #             "description": "Absolute URL to the newly created service.",
        #         },
        #     },
        #     tags=["Secondary Services"],
        # )
        # def openeo_service_create(request: Request, body: "ServiceInput"):
        #     """Deletes all data related to this secondary web service."""
        #     return Response(
        #         status_code=201
        #         headers={
        #             "Location": ...,  # The URL points to the metadata endpoint
        #             "OpenEO-Identifier": ...
        #         }
        #     )
        #
        # @self.router.delete(
        #     "/services/{service_id}",
        #     response_class=JSONResponse,
        #     summary="Delete a service",
        #     operation_id="delete-service",
        #     responses={
        #         204: {
        #             "description": "The service has been successfully deleted",
        #         },
        #     },
        #     tags=["Secondary Services"],
        # )
        # def openeo_service_delete(request: Request):
        #     """Deletes all data related to this secondary web service."""
        #     return Response(status_code=204)

        @self.router.get(
            "/service_types",
            response_class=JSONResponse,
            summary="Supported secondary web service protocols",
            response_model=models.ServiceTypes,
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
                        }
                    },
                    "process_parameters": [],
                    "title": "XYZ tiled web map",
                }
            }

        @self.router.post(
            "/result",
            response_class=Response,
            summary="Process and download data synchronously",
            response_model=models.Service,
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
            body: models.ResultRequest,
            user=Depends(self.auth.validate),
        ):
            """Executes a user-defined process directly (synchronously) and the result will be
            downloaded in the format specified in the process graph.

            """
            process = body.process.model_dump()

            media_type = "image/png"
            for _, node in process["process_graph"].items():
                if node["process_id"] == "save_result":
                    media_type = (
                        "image/png"
                        if node["arguments"]["format"] == "png"
                        else "image/jpeg"
                    )
                    break

            parsed_graph = OpenEOProcessGraph(pg_data=process)
            pg_callable = parsed_graph.to_callable(
                process_registry=self.process_registry
            )
            img = pg_callable()

            return Response(img, media_type=media_type)

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
            # user=Depends(self.auth.validate),
        ):
            """Create map tile."""
            service = self.services_store.get_service(service_id)
            tile_size = service.get("configuration", {}).get("tile_size", 256)
            process = deepcopy(service["process"])

            tms = morecantile.tms.get("WebMercatorQuad")

            media_type = "image/png"

            # Overwrite spatial extent for `load_collection`
            for _, node in process["process_graph"].items():
                if node["process_id"] in [
                    "load_collection",
                    "load_collection_and_reduce",
                ]:
                    bounds = tms.xy_bounds(x, y, z)
                    spatial_extent = BoundingBox(
                        west=bounds[0],
                        south=bounds[1],
                        east=bounds[2],
                        north=bounds[3],
                        crs=tms.crs.to_epsg(),
                    )
                    # Check if the tile is out of bounds
                    existing_extent = node["arguments"].get("spatial_extent")
                    if (
                        existing_extent
                        and isinstance(existing_extent.get("west"), (int, float))
                        and isinstance(existing_extent.get("south"), (int, float))
                        and isinstance(existing_extent.get("east"), (int, float))
                        and isinstance(existing_extent.get("north"), (int, float))
                    ):
                        existing_extent = BoundingBox(**existing_extent)
                        if existing_extent.crs != spatial_extent.crs:
                            transformer = Transformer.from_crs(
                                existing_extent.crs, spatial_extent.crs, always_xy=True
                            )
                            existing_extent = BoundingBox(
                                west=transformer.transform(
                                    existing_extent.west, existing_extent.south
                                )[0],
                                south=transformer.transform(
                                    existing_extent.west, existing_extent.south
                                )[1],
                                east=transformer.transform(
                                    existing_extent.east, existing_extent.north
                                )[0],
                                north=transformer.transform(
                                    existing_extent.east, existing_extent.north
                                )[1],
                                crs=spatial_extent.crs,
                            )
                        intersection = existing_extent.polygon.intersection(
                            spatial_extent.polygon
                        )
                        if intersection.is_empty:
                            raise TileOutsideBounds(
                                f"Tile(x={x}, y={y}, z={z}) is outside bounds defined by the process graph."
                            )

                    node["arguments"]["spatial_extent"] = spatial_extent.model_dump(
                        exclude_none=True
                    )
                    node["arguments"]["width"] = int(tile_size)
                    node["arguments"]["height"] = int(tile_size)
                    break

            for _, node in process["process_graph"].items():
                if node["process_id"] == "save_result":
                    media_type = (
                        "image/png"
                        if node["arguments"]["format"] == "png"
                        else "image/jpeg"
                    )
                    break

            parsed_graph = OpenEOProcessGraph(pg_data=process)
            pg_callable = parsed_graph.to_callable(
                process_registry=self.process_registry
            )
            img = pg_callable()
            return Response(img, media_type=media_type)
