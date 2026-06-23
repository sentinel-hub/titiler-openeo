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
