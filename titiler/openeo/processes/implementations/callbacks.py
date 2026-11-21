"""titiler.openeo.processes callback helpers.

The openEO executor (openeo_pg_parser_networkx) memoizes each callback node by id
in a ``results_cache`` dict SHARED across the whole graph. A process that invokes
a callback more than once (e.g. ``merge_cubes`` once per timestamp and band,
``aggregate_temporal`` once per interval, ``aggregate_spatial`` once per geometry)
therefore gets the FIRST call's result for every later call. The executor skips
the cache only for a callback node whose direct parent is ``aggregate_spatial`` or
``aggregate_temporal_period``, so a callback with more than one node is still hit.

Prefer evaluating a callback ONCE on stacked data (see apply.py). When the calls
cannot be vectorized, wrap the callback with :func:`recompute_each_call`.
"""

import inspect
from typing import Any, Callable, Dict, Optional

# Named parameter that ``apply_dimension`` sets to the labels of the dimension,
# so that a callback process (e.g. ``array_interpolate_linear``) can use the
# labels of its labeled array. The leading underscore keeps it out of the way of
# real process parameters.
DIMENSION_LABELS_PARAMETER = "_dimension_labels"


def _callback_results_cache(callback: Callable) -> Optional[Dict[Any, Any]]:
    """Return the openEO executor's shared ``results_cache`` backing a callback.

    The callback compiled by openeo_pg_parser_networkx is a
    ``functools.partial(node_callable, ...)`` whose closure holds the
    ``results_cache`` dict. Returns ``None`` if the structure can't be
    introspected (e.g. a plain Python callable); degrade gracefully.
    """
    func = getattr(callback, "func", callback)  # unwrap functools.partial
    freevars = getattr(getattr(func, "__code__", None), "co_freevars", ()) or ()
    closure = getattr(func, "__closure__", None) or ()
    for name, cell in zip(freevars, closure):
        if name == "results_cache" and isinstance(cell.cell_contents, dict):
            return cell.cell_contents
    return None


def recompute_each_call(callback: Callable) -> Callable:
    """Wrap a callback so that the executor recomputes it on each call.

    The callback's ``results_cache`` is snapshot when wrapping and reset to that
    snapshot before each call: the callback's own nodes recompute, while upstream
    entries are preserved (no re-reads). Wrap after any upstream work that must
    stay cached, and before the first call.
    """
    cache = _callback_results_cache(callback)
    if cache is None:
        return callback
    baseline = dict(cache)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        cache.clear()
        cache.update(baseline)
        return callback(*args, **kwargs)

    return wrapper


def _accepts_named_parameters(callback: Callable) -> bool:
    """Return True if ``callback`` takes ``named_parameters`` or ``**kwargs``."""
    try:
        params = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        p.name == "named_parameters" or p.kind is inspect.Parameter.VAR_KEYWORD
        for p in params
    )


def call_reducer(
    reducer: Callable,
    data: Any,
    context: Optional[Any] = None,
    named_parameters: Optional[Dict[str, Any]] = None,
) -> Any:
    """Call a reducer callback with ``data`` and, if given, ``context``.

    A callback graph resolves ``{"from_parameter": ...}`` references from
    ``named_parameters``, not from keyword arguments, so ``context`` is also
    passed there (as ``apply`` does) when the reducer accepts it. The enclosing
    ``named_parameters`` (e.g. the parameters of a UDP or a service) are
    forwarded too, with ``data`` and ``context`` on top. A plain Python reducer
    gets ``context`` as a keyword argument, and only ``data`` when there is no
    context.
    """
    if context is None and not named_parameters:
        return reducer(data=data)
    if not _accepts_named_parameters(reducer):
        if context is None:
            return reducer(data=data)
        return reducer(data=data, context=context)
    return reducer(
        data=data,
        context=context,
        named_parameters={**(named_parameters or {}), "data": data, "context": context},
    )
