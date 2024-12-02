"""titiler-openeo app."""

import jinja2
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.templating import Jinja2Templates
from starlette_cramjam.middleware import CompressionMiddleware

from titiler.core.middleware import CacheControlMiddleware
from titiler.openeo import __version__ as titiler_version
from titiler.openeo import models
from titiler.openeo.models import OPENEO_VERSION
from titiler.openeo.settings import ApiSettings

STAC_VERSION = "1.0.0"

jinja2_env = jinja2.Environment(
    loader=jinja2.ChoiceLoader([jinja2.PackageLoader(__package__, "templates")])
)
templates = Jinja2Templates(env=jinja2_env)


api_settings = ApiSettings()


###############################################################################

app = FastAPI(
    title=api_settings.name,
    openapi_url="/api",
    docs_url="/api.html",
    description="""TiTiler backend for openEO.

---

**Documentation**: <a href="https://developmentseed.org/titiler-openeo/" target="_blank">https://developmentseed.org/titiler-openeo/</a>

**Source Code**: <a href="https://github.com/developmentseed/titiler-openeo" target="_blank">https://github.com/developmentseed/titiler-openeo</a>

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
    exclude_path={r"/healthz"},
)


@app.get(
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
            # /collections
            # /collections/{collection_id}
            # /processes
        ],
        "links": [
            {
                "href": str(request.url_for("openeo_well_known")),
                "rel": "version-history",
                "type": "application/json",
                "title": "List of supported openEO version",
            }
        ],
        "conformsTo": [
            "https://api.openeo.org/1.2.0",
        ],
    }


@app.get(
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
    """Lists all implemented openEO versions supported by the service provider.
    This endpoint is the Well-Known URI (see RFC 5785) for openEO.

    """
    return {
        "versions": [
            {
                "url": str(request.url_for("openeo_root")),
                "api_version": OPENEO_VERSION,
            },
        ]
    }
