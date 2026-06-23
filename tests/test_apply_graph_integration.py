"""Process-graph integration tests for apply / apply_dimension / array_apply.

These run the processes through the real openEO executor
(``OpenEOProcessGraph.to_callable``), so the callback is the actual
``node_callable`` with its shared ``results_cache`` — the path that plain-Python
callback unit tests bypass.

They guard against the class of bug documented in apply.py / reduce.py: invoking
a callback more than once returns the first call's cached result (or, run
concurrently, crashes with the child process missing its ``data``). Each test
below would fail on the previous per-element / per-image implementations.
"""

from datetime import datetime

import numpy as np
import pytest
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from rio_tiler.models import ImageData

from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.data_model import RasterStack


def _run(pg: dict, **named_parameters):
    """Compile and execute a process graph against the real registry."""
    callable_ = OpenEOProcessGraph(pg_data={"process_graph": pg}).to_callable(
        process_registry=process_registry
    )
    return callable_(named_parameters=named_parameters)


def _three_image_stack() -> RasterStack:
    """A 3-timestamp single-band stack with values 3, 7, 5 (temporal max is 7)."""
    return RasterStack.from_images(
        {
            datetime(2024, 6, 1): ImageData(
                np.ma.array(np.full((1, 2, 2), 3, np.float32))
            ),
            datetime(2024, 6, 2): ImageData(
                np.ma.array(np.full((1, 2, 2), 7, np.float32))
            ),
            datetime(2024, 6, 3): ImageData(
                np.ma.array(np.full((1, 2, 2), 5, np.float32))
            ),
        }
    )


def _two_image_stack() -> RasterStack:
    """A 2-timestamp stack with distinct constant values (1 and 5)."""
    return RasterStack.from_images(
        {
            datetime(2021, 1, 1): ImageData(
                np.ma.array(np.full((1, 2, 2), 1, np.uint8))
            ),
            datetime(2021, 1, 2): ImageData(
                np.ma.array(np.full((1, 2, 2), 5, np.uint8))
            ),
        }
    )


def test_array_apply_via_graph_maps_each_element():
    """array_apply must apply the child process to every element, not just the first.

    The old per-element loop returned the first element's cached result for all
    elements (e.g. [10, 10, 10, 10]).
    """
    pg = {
        "data": {
            "process_id": "array_create",
            "arguments": {"data": [1.0, 2.0, 3.0, 4.0]},
        },
        "apply": {
            "process_id": "array_apply",
            "arguments": {
                "data": {"from_node": "data"},
                "process": {
                    "process_graph": {
                        "mul": {
                            "process_id": "multiply",
                            "arguments": {"x": {"from_parameter": "x"}, "y": 10},
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        },
    }
    result = np.asarray(_run(pg))
    np.testing.assert_array_equal(result, np.array([10.0, 20.0, 30.0, 40.0]))


def test_apply_via_graph_maps_each_image():
    """apply must apply the callback to every image in the stack independently.

    The old per-image loop returned the first image's cached result for every
    timestamp.
    """
    pg = {
        "ap": {
            "process_id": "apply",
            "arguments": {
                "data": {"from_parameter": "data"},
                "process": {
                    "process_graph": {
                        "mul": {
                            "process_id": "multiply",
                            "arguments": {"x": {"from_parameter": "x"}, "y": 10},
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        }
    }
    result = _run(pg, data=_two_image_stack())
    by_ts = {k: v.array.data.ravel().tolist() for k, v in result.items()}
    assert by_ts[datetime(2021, 1, 1)] == [10, 10, 10, 10]
    assert by_ts[datetime(2021, 1, 2)] == [50, 50, 50, 50]


def test_max_rejects_non_boolean_ignore_nodata():
    """max/min must reject a non-boolean ``ignore_nodata`` with a clear validation
    error, not a cryptic numpy "truth value of an array is ambiguous" crash.

    This is the common misuse ``oe_max(a, b)``: openEO ``max`` is an array
    aggregator ``max(data, ignore_nodata)``, so the second positional ``b`` binds
    to ``ignore_nodata`` (which must be boolean). Element-wise max of two bands is
    ``max(array_create([a, b]))``.
    """
    band = ImageData(
        np.ma.array(np.full((1, 2, 2), 2.0), mask=[[[False, True], [False, False]]])
    )
    stack = RasterStack.from_images({datetime(2024, 6, 1): band})
    pg = {
        "m": {
            "process_id": "max",
            "arguments": {
                "data": {"from_parameter": "data"},
                "ignore_nodata": {"from_parameter": "data"},  # an array, not a bool
            },
            "result": True,
        }
    }
    with pytest.raises(TypeError, match="ignore_nodata.*expected 'boolean'"):
        _run(pg, data=stack)


def test_max_accepts_boolean_ignore_nodata():
    """A valid boolean ``ignore_nodata`` (and the default) still works."""
    band = ImageData(np.ma.array(np.full((2, 1, 2), 3.0)))
    stack = RasterStack.from_images({datetime(2024, 6, 1): band})
    pg = {
        "m": {
            "process_id": "max",
            "arguments": {"data": {"from_parameter": "data"}, "ignore_nodata": False},
            "result": True,
        }
    }
    out = np.asarray(_run(pg, data=stack))
    assert out.shape == (2, 1, 2)
    assert (out == 3.0).all()


def test_apply_dimension_bands_maps_each_image_via_graph():
    """apply_dimension over 'bands' on a MULTI-temporal stack must compute each
    timestamp from its OWN bands, not collapse every slice to the first image's.

    Regression for the cloud-free-mosaic bug: the per-image loop in
    _apply_spectral_dimension_stack hit the executor's node results_cache, so
    every timestamp received the FIRST image's band result. In the real graph
    that replicated the first acquisition's footprint (and its nodata) across all
    dates, so a later acquisition covering an area the first one didn't was
    dropped and the composite was never filled.
    """
    # Two timestamps, two bands, distinct constant values per slice/band.
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): ImageData(
                np.ma.array(np.stack([np.full((2, 2), 1.0), np.full((2, 2), 2.0)])),
                band_descriptions=["b0", "b1"],
            ),
            datetime(2024, 6, 2): ImageData(
                np.ma.array(np.stack([np.full((2, 2), 10.0), np.full((2, 2), 20.0)])),
                band_descriptions=["b0", "b1"],
            ),
        }
    )
    # Callback builds a 1-band output = b0 + b1, per pixel/timestamp.
    pg = {
        "ad": {
            "process_id": "apply_dimension",
            "arguments": {
                "data": {"from_parameter": "data"},
                "dimension": "bands",
                "process": {
                    "process_graph": {
                        "b0": {
                            "process_id": "array_element",
                            "arguments": {
                                "data": {"from_parameter": "data"},
                                "index": 0,
                            },
                        },
                        "b1": {
                            "process_id": "array_element",
                            "arguments": {
                                "data": {"from_parameter": "data"},
                                "index": 1,
                            },
                        },
                        "sum": {
                            "process_id": "add",
                            "arguments": {
                                "x": {"from_node": "b0"},
                                "y": {"from_node": "b1"},
                            },
                        },
                        "out": {
                            "process_id": "array_create",
                            "arguments": {"data": [{"from_node": "sum"}]},
                            "result": True,
                        },
                    }
                },
            },
            "result": True,
        }
    }
    result = _run(pg, data=stack)
    by_ts = {k: v.array.data.ravel().tolist() for k, v in result.items()}
    # Slice 1: 1 + 2 = 3 ; Slice 2: 10 + 20 = 30. The old bug gave [3,3,3,3] for both.
    assert by_ts[datetime(2024, 6, 1)] == [3.0, 3.0, 3.0, 3.0]
    assert by_ts[datetime(2024, 6, 2)] == [30.0, 30.0, 30.0, 30.0]


def test_apply_dimension_bands_preserves_per_image_nodata_mask():
    """apply_dimension over 'bands' must keep each slice's own nodata mask, so an
    area that is nodata in one acquisition but valid in another is not lost."""
    s1 = np.ma.MaskedArray(  # west (col 0) nodata in slice 1
        np.stack([np.full((1, 2), 1.0), np.full((1, 2), 2.0)]),
        mask=np.stack([[[True, False]], [[True, False]]]),
    )
    s2 = np.ma.MaskedArray(  # fully valid in slice 2
        np.stack([np.full((1, 2), 10.0), np.full((1, 2), 20.0)]),
        mask=False,
    )
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): ImageData(s1, band_descriptions=["b0", "b1"]),
            datetime(2024, 6, 2): ImageData(s2, band_descriptions=["b0", "b1"]),
        }
    )
    pg = {
        "ad": {
            "process_id": "apply_dimension",
            "arguments": {
                "data": {"from_parameter": "data"},
                "dimension": "bands",
                "process": {
                    "process_graph": {
                        "b0": {
                            "process_id": "array_element",
                            "arguments": {
                                "data": {"from_parameter": "data"},
                                "index": 0,
                            },
                        },
                        "out": {
                            "process_id": "array_create",
                            "arguments": {"data": [{"from_node": "b0"}]},
                            "result": True,
                        },
                    }
                },
            },
            "result": True,
        }
    }
    result = _run(pg, data=stack)
    m1 = np.ma.getmaskarray(result[datetime(2024, 6, 1)].array)
    m2 = np.ma.getmaskarray(result[datetime(2024, 6, 2)].array)
    assert m1.ravel().tolist() == [True, False]  # slice 1 keeps its west nodata
    assert m2.ravel().tolist() == [False, False]  # slice 2 stays fully valid


def test_apply_dimension_temporal_array_apply_via_graph():
    """The originally reported scenario: apply_dimension(t) -> array_apply -> multiply.

    Previously this raised ``expected 'array' but got 'datacube'`` (datacube passed
    to array_apply) and later crashed under the thread pool. It must now compute
    each timestamp correctly.
    """
    pg = {
        "ad": {
            "process_id": "apply_dimension",
            "arguments": {
                "data": {"from_parameter": "data"},
                "dimension": "t",
                "process": {
                    "process_graph": {
                        "aa": {
                            "process_id": "array_apply",
                            "arguments": {
                                "data": {"from_parameter": "data"},
                                "process": {
                                    "process_graph": {
                                        "mul": {
                                            "process_id": "multiply",
                                            "arguments": {
                                                "x": {"from_parameter": "x"},
                                                "y": 2,
                                            },
                                            "result": True,
                                        }
                                    }
                                },
                            },
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        }
    }
    result = _run(pg, data=_two_image_stack())
    by_ts = {k: v.array.data.ravel().tolist() for k, v in result.items()}
    assert by_ts[datetime(2021, 1, 1)] == [2, 2, 2, 2]
    assert by_ts[datetime(2021, 1, 2)] == [10, 10, 10, 10]


def test_array_apply_callback_references_enclosing_scope():
    """array_apply callbacks may reference an outer-scope parameter (`data`).

    Real-world pattern: apply_dimension(t) -> array_apply(neq(x, max(data))), where
    `max(data)` refers to the enclosing apply_dimension array (the whole temporal
    series), not array_apply's element `x`. array_apply must forward the enclosing
    `named_parameters` so the nested `from_parameter: data` resolves; otherwise the
    child `max` is called with no `data`. The callback input must also be a realized
    array, since `max` type-checks with isinstance(numpy.ndarray).
    """
    pg = {
        "ad": {
            "process_id": "apply_dimension",
            "arguments": {
                "data": {"from_parameter": "data"},
                "dimension": "t",
                "process": {
                    "process_graph": {
                        "arrayapply1": {
                            "process_id": "array_apply",
                            "arguments": {
                                "data": {"from_parameter": "data"},
                                "process": {
                                    "process_graph": {
                                        "max1": {
                                            "process_id": "max",
                                            "arguments": {
                                                "data": {"from_parameter": "data"}
                                            },
                                        },
                                        "neq1": {
                                            "process_id": "neq",
                                            "arguments": {
                                                "x": {"from_parameter": "x"},
                                                "y": {"from_node": "max1"},
                                            },
                                            "result": True,
                                        },
                                    }
                                },
                            },
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        }
    }
    result = _run(pg, data=_three_image_stack())
    by_ts = {k: v.array.data.ravel().tolist() for k, v in result.items()}
    # Each timestamp is flagged where it is NOT the temporal maximum (7 @ 06-02).
    assert by_ts[datetime(2024, 6, 1)] == [True, True, True, True]
    assert by_ts[datetime(2024, 6, 2)] == [False, False, False, False]
    assert by_ts[datetime(2024, 6, 3)] == [True, True, True, True]


def _selection_pg() -> dict:
    """apply_dimension(t) -> array_apply(neq(x, max(data))): the 'best pixel over
    time' selection. ``max(data)`` must be the per-pixel temporal max, not a
    single global scalar."""
    return {
        "ad": {
            "process_id": "apply_dimension",
            "arguments": {
                "data": {"from_parameter": "data"},
                "dimension": "t",
                "process": {
                    "process_graph": {
                        "arrayapply1": {
                            "process_id": "array_apply",
                            "arguments": {
                                "data": {"from_parameter": "data"},
                                "process": {
                                    "process_graph": {
                                        "max1": {
                                            "process_id": "max",
                                            "arguments": {
                                                "data": {"from_parameter": "data"}
                                            },
                                        },
                                        "neq1": {
                                            "process_id": "neq",
                                            "arguments": {
                                                "x": {"from_parameter": "x"},
                                                "y": {"from_node": "max1"},
                                            },
                                            "result": True,
                                        },
                                    }
                                },
                            },
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        }
    }


def test_temporal_array_apply_max_is_per_pixel_not_global():
    """Regression: max(data) inside a temporal array_apply must reduce over the
    time axis PER PIXEL, not collapse the whole cube to one global scalar.

    The earlier global-scalar behaviour broke the cloud-free "best pixel over
    time" composite: with two complementary acquisitions (one valid on the left
    half, one on the right), only the single globally-maximum pixel survived and
    the rest of the scene was masked out instead of being filled by the other
    acquisition. This test uses spatially VARYING values so global-max and
    per-pixel-temporal-max differ (the uniform-valued test above cannot tell them
    apart).
    """

    # 1x4 strip, single band. t0 valid on left (cols 0,1), t1 valid on right.
    def _img(vals, mask):
        return ImageData(
            np.ma.MaskedArray(
                np.array([[vals]], dtype=float), mask=np.array([[mask]], dtype=bool)
            )
        )

    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): _img(
                [5.0, 6.0, 0.0, 0.0], [False, False, True, True]
            ),
            datetime(2024, 6, 2): _img(
                [0.0, 0.0, 7.0, 8.0], [True, True, False, False]
            ),
        }
    )

    sel = _run(_selection_pg(), data=stack)
    flagged = {k: v.array.data.ravel().tolist() for k, v in sel.items()}
    # Per pixel, the single valid time is its own temporal max -> NOT flagged.
    # Global-scalar max (8) would instead flag every pixel except the one 8.
    assert flagged[datetime(2024, 6, 1)] == [False, False, True, True]
    assert flagged[datetime(2024, 6, 2)] == [True, True, False, False]
