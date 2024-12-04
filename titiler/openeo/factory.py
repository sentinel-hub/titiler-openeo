"""titiler.openeo endpoint Factory."""

from attrs import define
from fastapi import Path
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.requests import Request
from typing_extensions import Annotated

from titiler.core.factory import BaseFactory
from titiler.openeo import __version__ as titiler_version
from titiler.openeo import models
from titiler.openeo.models import OPENEO_VERSION
from titiler.openeo.stac import STACBackend

STAC_VERSION = "1.0.0"


@define(kw_only=True)
class EndpointsFactory(BaseFactory):
    """OpenEO Endpoints Factory."""

    stac_backend: STACBackend

    def register_routes(self):
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
                    }
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
            return {
                "processes": [],
                "links": [],
            }

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
            collections = self.stac_backend.get_collections()
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
            collection = self.stac_backend.get_collection(collection_id)

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

        # TODO
        # # CollectionsId Queryables
        # # Ref: https://openeo.org/documentation/1.0/developers/profiles/api.html#collections-3
        # @self.router.get(
        #     r"/collections/{collection_id}/queryables",
        #     response_class=JSONResponse,
        #     summary="Metadata filters for a specific dataset",
        #     response_model=models.Queryable,
        #     response_model_exclude_none=True,
        #     operation_id="list-collection-queryables",
        #     responses={
        #         200: {
        #             "content": {
        #                 "application/json": {},
        #             },
        #         },
        #     },
        #     tags=["Capabilities"],
        # )
        # def openeo_collection_queryables(request: Request):
        #     """Lists **all** supported metadata filters (also called "queryables") for a specific collection."""
