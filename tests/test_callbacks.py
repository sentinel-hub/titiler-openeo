"""Tests for the callback helpers."""

from titiler.openeo.processes.implementations.callbacks import call_reducer


def _plain_reducer(data, context=None):
    """A plain Python reducer, without ``named_parameters``."""
    return {"data": data, "context": context}


def _graph_reducer(data, context=None, named_parameters=None):
    """A reducer that takes ``named_parameters``, like a callback graph."""
    return {"data": data, "context": context, "named_parameters": named_parameters}


def test_call_reducer_data_only():
    """Without context or enclosing parameters, the reducer gets only ``data``."""
    assert call_reducer(_graph_reducer, 1) == {
        "data": 1,
        "context": None,
        "named_parameters": None,
    }


def test_call_reducer_plain_reducer_ignores_enclosing_parameters():
    """A plain reducer cannot take ``named_parameters``: it gets ``data`` only."""
    assert call_reducer(_plain_reducer, 1, named_parameters={"threshold": 4}) == {
        "data": 1,
        "context": None,
    }


def test_call_reducer_plain_reducer_with_context():
    """A plain reducer gets ``context`` as a keyword argument."""
    assert call_reducer(_plain_reducer, 1, context="c") == {"data": 1, "context": "c"}


def test_call_reducer_forwards_enclosing_parameters():
    """The enclosing parameters are forwarded, with ``data`` and ``context`` on top."""
    result = call_reducer(
        _graph_reducer,
        1,
        context="c",
        named_parameters={"threshold": 4, "data": "outer", "context": "outer"},
    )
    assert result["named_parameters"] == {"threshold": 4, "data": 1, "context": "c"}
