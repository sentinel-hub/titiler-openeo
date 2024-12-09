"""Stac API backend."""

from contextlib import asynccontextmanager
from typing import Dict, List

from attrs import define, field
from fastapi import FastAPI
from pystac import Collection
from pystac.extensions import datacube as dc
from pystac.extensions import eo
from pystac.extensions import item_assets as ia
from pystac_client import Client
from pystac_client.stac_api_io import StacApiIO
from urllib3 import Retry

from ..settings import PySTACSettings
from .base import STACBackend


@define
class stacApiBackend(STACBackend):
    """PySTAC-Client Backend."""

    url: str = field()

    client: Client = field(init=False)

    def get_collections(self, **kwargs) -> List[Dict]:
        """Return List of STAC Collections."""
        collections = []
        for collection in self.client.get_collections():
            collection = self.add_version_if_missing(collection)
            collection = self.add_data_cubes_if_missing(collection)
            collections.append(collection)
        return [col.to_dict() for col in collections]

    def add_version_if_missing(self, collection: Collection):
        """Add version to collection if missing."""
        if not collection.ext.has("version"):
            collection.ext.add("version")
        if not collection.ext.version.version:
            collection.ext.version.version = "1.0.0"
        return collection

    def add_data_cubes_if_missing(self, collection: Collection):
        """Add datacubes extension to collection if missing."""
        if not collection.ext.has("cube"):
            dc.DatacubeExtension.add_to(collection)
            """ Add minimal dimensions """
            collection.ext.cube.apply(
                dimensions=self.getdimensions(collection),
                # variables=self.getvariables(collection),
            )

        return collection

    def getdimensions(self, collection: Collection) -> Dict[str, dc.Dimension]:
        """Get dimensions from collection"""
        dims = {}
        """ Sptial extent """
        if collection.extent.spatial.bboxes:
            dims["x"] = dc.Dimension.from_dict(
                {
                    "type": "spatial",
                    "axis": "x",
                    "extent": [
                        collection.extent.spatial.bboxes[0][0],
                        collection.extent.spatial.bboxes[0][2],
                    ],
                }
            )
            dims["y"] = dc.Dimension.from_dict(
                {
                    "type": "spatial",
                    "axis": "y",
                    "extent": [
                        collection.extent.spatial.bboxes[0][1],
                        collection.extent.spatial.bboxes[0][3],
                    ],
                }
            )
        """ Temporal extent """
        if collection.extent.temporal.intervals:
            dims["t"] = dc.Dimension.from_dict(
                {
                    "type": "temporal",
                    "extent": [
                        collection.extent.temporal.intervals[0][0],
                        collection.extent.temporal.intervals[0][1],
                    ],
                }
            )

        """ Add spectral bands """
        # TEMP FIX: The item_assets in core collection is not supported in PySTAC yet.
        if (
            eo.EOExtension.has_extension(collection)
            and "item_assets" in collection.extra_fields
        ):
            ia.ItemAssetsExtension.add_to(collection)
            item_assets = collection.ext.item_assets.values()
            bands_name = set()
            for asset in item_assets:
                bands_name.add(asset.ext.eo.name)
            if len(bands_name) > 0:
                dims["spectral"] = dc.Dimension.from_dict(
                    {
                        "type": "bands",
                        "values": bands_name,
                    }
                )

        return dims

    def getvariables(self, collection: Collection) -> Dict[str, dc.Variable]:
        """Get variables from collection"""
        variables = {}
        if eo.EOExtension.has_extension(collection):
            eo_ext = eo.EOExtension.ext(collection)
            if eo_ext.bands:
                for band in eo_ext.bands:
                    variables[band.name] = dc.Variable.from_dict(
                        {
                            "name": band.name,
                            "description": band.description,
                            "unit": band.unit,
                            "data_type": band.data_type,
                        }
                    )
        return variables

    def get_collection(self, collection_id: str, **kwargs) -> Dict:
        """Return STAC Collection"""
        col = self.client.get_collection(collection_id)
        col = self.add_version_if_missing(col)
        col = self.add_data_cubes_if_missing(col)
        return col.to_dict()

    def get_items(self, **kwargs) -> List[Dict]:
        """Return List of STAC Items."""
        return []

    def get_lifespan(self):
        """pystac lifespan function."""

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """init PySTAC Client."""
            settings = PySTACSettings()

            stac_api_io = StacApiIO(
                max_retries=Retry(
                    total=settings.retry,
                    backoff_factor=settings.retry_factor,
                ),
            )

            self.client = Client.open(self.url, stac_io=stac_api_io)
            yield
            self.client = None

        return lifespan
