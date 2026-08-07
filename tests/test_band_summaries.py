"""Tests for summaries.bands derivation from item_assets band metadata.

openEO Studio's band parser (as of
https://github.com/developmentseed/openeo-studio/pull/103) only reads
summaries.bands; backends that publish bands solely via the datacube
extension's cube:dimensions show no bands in the UI until that parser fix
lands. ``stacApiBackend._add_band_summaries`` derives the same rich
per-band objects from item_assets so those collections work today.
"""

from titiler.openeo.stacapi import stacApiBackend


def _backend() -> stacApiBackend:
    return stacApiBackend(url="https://example.com")


def test_add_band_summaries_derives_from_eo_bands():
    collection = {
        "id": "sentinel-2-l2a",
        "item_assets": {
            "B02": {
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                "roles": ["data"],
                "description": "Blue (band 2) - 10m",
                "eo:bands": [
                    {
                        "name": "B02",
                        "common_name": "blue",
                        "center_wavelength": 0.49,
                        "full_width_half_max": 0.098,
                    }
                ],
            },
            "B03": {
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                "roles": ["data"],
                "description": "Green (band 3) - 10m",
                "eo:bands": [{"name": "B03", "common_name": "green"}],
            },
            "granule_metadata": {
                "type": "application/xml",
                "roles": ["metadata"],
            },
        },
    }

    stacApiBackend._add_band_summaries(collection)

    bands = collection["summaries"]["bands"]
    assert bands == [
        {
            "name": "B02",
            "description": "Blue (band 2) - 10m",
            "eo:common_name": "blue",
            "eo:center_wavelength": 0.49,
            "eo:full_width_half_max": 0.098,
        },
        {
            "name": "B03",
            "description": "Green (band 3) - 10m",
            "eo:common_name": "green",
        },
    ]


def test_add_band_summaries_supports_stac_1_1_unprefixed_bands():
    collection = {
        "id": "sentinel-2-l2a",
        "item_assets": {
            "B02": {
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                "roles": ["data"],
                "title": "Blue - 10m",
                "bands": [
                    {
                        "name": "B02",
                        "common_name": "blue",
                        "center_wavelength": 0.49,
                    }
                ],
            },
        },
    }

    stacApiBackend._add_band_summaries(collection)

    assert collection["summaries"]["bands"] == [
        {
            "name": "B02",
            "description": "Blue - 10m",
            "eo:common_name": "blue",
            "eo:center_wavelength": 0.49,
        },
    ]


def test_add_band_summaries_leaves_existing_summaries_bands_untouched():
    existing_bands = [{"name": "B02", "description": "Already rich"}]
    collection = {
        "id": "eopf-collection",
        "summaries": {"bands": existing_bands},
        "item_assets": {
            "B02": {
                "type": "image/tiff",
                "roles": ["data"],
                "eo:bands": [{"name": "B02"}],
            },
        },
    }

    stacApiBackend._add_band_summaries(collection)

    assert collection["summaries"]["bands"] is existing_bands


def test_add_band_summaries_noop_when_no_band_metadata():
    collection = {
        "id": "sentinel-1-grd",
        "item_assets": {
            "vh": {"type": "image/tiff", "roles": ["data"]},
        },
    }

    stacApiBackend._add_band_summaries(collection)

    assert "bands" not in collection.get("summaries", {})


def test_add_band_summaries_noop_without_item_assets():
    collection = {"id": "no-assets"}

    stacApiBackend._add_band_summaries(collection)

    assert "summaries" not in collection or "bands" not in collection["summaries"]


def test_fix_collection_wires_band_summaries_in():
    collection = {
        "id": "sentinel-2-l2a",
        "item_assets": {
            "B02": {
                "type": "image/tiff",
                "roles": ["data"],
                "description": "Blue (band 2)",
                "eo:bands": [{"name": "B02", "common_name": "blue"}],
            },
        },
    }

    _backend()._fix_collection(collection)

    assert collection["summaries"]["bands"] == [
        {"name": "B02", "description": "Blue (band 2)", "eo:common_name": "blue"},
    ]
