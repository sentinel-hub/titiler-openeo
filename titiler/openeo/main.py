"""titiler-openeo app."""

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette_cramjam.middleware import CompressionMiddleware

from titiler.core.middleware import CacheControlMiddleware
from titiler.openeo import __version__ as titiler_version
from titiler.openeo.factory import EndpointsFactory
from titiler.openeo.settings import ApiSettings, STACSettings
from titiler.openeo.stac import get_stac_backend

STAC_VERSION = "1.0.0"

api_settings = ApiSettings()
stac_settings = STACSettings()

stac_backend = get_stac_backend(str(stac_settings.api_url))


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
    lifespan=stac_backend.get_lifespan(),
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

# Register OpenEO endpoints
endpoints = EndpointsFactory(stac_backend=stac_backend)
app.include_router(endpoints.router)
