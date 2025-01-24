"""titiler-openeo app."""

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from openeo_pg_parser_networkx.process_registry import Process
from starlette.middleware.cors import CORSMiddleware
from starlette_cramjam.middleware import CompressionMiddleware

from titiler.core.middleware import CacheControlMiddleware
from titiler.openeo import __version__ as titiler_version
from titiler.openeo.errors import OpenEOException
from titiler.openeo.factory import EndpointsFactory
from titiler.openeo.processes import PROCESS_SPECIFICATIONS, process_registry
from titiler.openeo.services import get_store
from titiler.openeo.settings import ApiSettings, BackendSettings
from titiler.openeo.stacapi import LoadCollection, stacApiBackend

STAC_VERSION = "1.0.0"

api_settings = ApiSettings()
backend_settings = BackendSettings()

stac_client = stacApiBackend(str(backend_settings.stac_api_url))  # type: ignore
service_store = get_store(str(backend_settings.service_store_url))

###############################################################################

app = FastAPI(
    title=api_settings.name,
    openapi_url="/api",
    docs_url="/api.html",
    description="""TiTiler backend for openEO.

---

**Documentation**: <a href="https://developmentseed.org/titiler-openeo/" target="_blank">https://developmentseed.org/titiler-openeo/</a>

**Source Code**: <a href="https://github.com/sentinel-hub/titiler-openeo" target="_blank">https://github.com/sentinel-hub/titiler-openeo</a>

---
    """,
    version=titiler_version,
    root_path=api_settings.root_path,
)

# Set all CORS enabled origins
if api_settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=api_settings.cors_origins,
        allow_credentials=True,
        allow_methods=api_settings.cors_allow_methods,
        allow_headers=["*"],
    )

app.add_middleware(
    CompressionMiddleware,
    minimum_size=0,
    exclude_mediatype={
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/jp2",
        "image/webp",
    },
    compression_level=6,
)

app.add_middleware(
    CacheControlMiddleware,
    cachecontrol=api_settings.cachecontrol,
)

# Register backend specific load_collection methods
loaders = LoadCollection(stac_client)  # type: ignore
process_registry["load_collection"] = process_registry["load_collection"] = Process(
    spec=PROCESS_SPECIFICATIONS["load_collection"],
    implementation=loaders.load_collection,
)
process_registry["load_collection_and_reduce"] = process_registry[
    "load_collection_and_reduce"
] = Process(
    spec=PROCESS_SPECIFICATIONS["load_collection_and_reduce"],
    implementation=loaders.load_collection_and_reduce,
)


# Register OpenEO endpoints
endpoints = EndpointsFactory(
    services_store=service_store,
    stac_client=stac_client,
    process_registry=process_registry,
)
app.include_router(endpoints.router)

# Add OpenEO-specific exception handlers
app.add_exception_handler(OpenEOException, endpoints.openeo_exception_handler)

app.add_exception_handler(
    RequestValidationError, endpoints.validation_exception_handler
)

app.add_exception_handler(HTTPException, endpoints.http_exception_handler)
