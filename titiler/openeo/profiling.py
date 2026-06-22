"""Optional memory-profiling harness for openEO process-graph evaluation.

This implements subtask 1 of the memory-optimization EPIC (#305): give us the
measurements needed to *confirm* where the OOM memory goes before changing any
behavior. See ``docs/audits/memory-oom-audit.md`` for the findings this is meant
to verify (per-node retention, heap-vs-native split) and
``docs/audits/memory-profiling.md`` for how to run it (incl. ``memray``).

Three things are provided, all **off by default** and gated behind
``TITILER_OPENEO_PROFILING_MEMORY``:

* :func:`profile_node` — per-process-node ``tracemalloc`` heap delta + current
  RSS, so we can attribute heap growth to ``load_collection`` vs ``ndvi`` vs
  ``aggregate_temporal`` etc. Wired into the ``@process`` decorator so every
  node passes through it.
* :func:`profile_graph` — a request-level summary: heap high-water mark and the
  ``RSS - heap`` split that approximates native (GDAL/VSI) memory.
* :func:`report_retention` — at ``save_result`` time, how many node results the
  graph engine is still pinning and how many bytes of array data they hold.

NOTE: this is a debug tool. ``tracemalloc`` is process-global and the RSS
sampling assumes a single graph is evaluating at a time, so run it on a
low-concurrency dev instance or via the standalone ``scripts/profile_memory.py``
runner — not in production.
"""

import logging
import threading
import tracemalloc
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Cached settings instance so the hot-path gate (called once per node) doesn't
# rebuild a pydantic-settings object every time. Use ``reset_profiling_cache()``
# in tests after mutating the environment.
_settings: Optional[Any] = None

# Per-thread nesting depth so nested process calls (e.g. a reducer invoking
# ``mean`` -> ``apply_pixel_selection``) are indented and the depth==0 lines map
# to top-level graph nodes.
_local = threading.local()


def _get_settings() -> Any:
    global _settings
    if _settings is None:
        from .settings import ProfilingSettings

        _settings = ProfilingSettings()
    return _settings


def reset_profiling_cache() -> None:
    """Drop the cached settings so the next call re-reads the environment (tests)."""
    global _settings
    _settings = None


def memory_profiling_enabled() -> bool:
    """Whether the memory-profiling harness is switched on."""
    return bool(_get_settings().memory)


def new_results_cache() -> Optional[dict]:
    """A fresh ``results_cache`` dict to pass to ``to_callable`` when profiling.

    Returns ``None`` when disabled so the graph engine uses its own internal
    cache and nothing changes in production.
    """
    return {} if memory_profiling_enabled() else None


def _ensure_started() -> None:
    if not tracemalloc.is_tracing():
        tracemalloc.start()


def _current_rss_bytes() -> Optional[int]:
    """Best-effort *current* resident set size (includes native/off-heap memory).

    Reads ``/proc/self/statm`` on Linux (current RSS, what we want for the
    heap-vs-native split). Falls back to ``ru_maxrss`` (a high-water mark) on
    platforms without procfs.
    """
    try:
        import resource

        with open("/proc/self/statm") as fh:
            resident_pages = int(fh.read().split()[1])
        return resident_pages * resource.getpagesize()
    except (OSError, IndexError, ValueError):
        try:
            import resource
            import sys

            ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # ru_maxrss is KiB on Linux, bytes on macOS.
            return ru if sys.platform == "darwin" else ru * 1024
        except Exception:  # pragma: no cover - extremely defensive
            return None


def _fmt_bytes(n: Optional[int]) -> str:
    if n is None:
        return "n/a"
    sign = "-" if n < 0 else ""
    value = float(abs(n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{sign}{value:.1f}{unit}"
        value /= 1024
    return f"{sign}{value:.1f}TB"  # pragma: no cover


def _fmt_native(rss: Optional[int], heap: Optional[int]) -> str:
    if rss is None or heap is None:
        return "n/a"
    return _fmt_bytes(rss - heap)


@contextmanager
def profile_node(name: str) -> Iterator[None]:
    """Log the heap delta and current RSS across a single process node.

    No-op (and essentially free) when profiling is disabled.
    """
    if not memory_profiling_enabled():
        yield
        return

    _ensure_started()
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    heap_before, _ = tracemalloc.get_traced_memory()
    rss_before = _current_rss_bytes()
    try:
        yield
    finally:
        _local.depth = depth
        heap_after, heap_peak = tracemalloc.get_traced_memory()
        rss_after = _current_rss_bytes()
        heap_delta = heap_after - heap_before
        rss_delta = (
            (rss_after - rss_before)
            if (rss_after is not None and rss_before is not None)
            else None
        )
        indent = "  " * depth
        logger.info(
            "[mem] %snode=%s depth=%d heap_delta=%s rss_delta=%s "
            "heap_now=%s rss_now=%s native~=%s",
            indent,
            name,
            depth,
            _fmt_bytes(heap_delta),
            _fmt_bytes(rss_delta),
            _fmt_bytes(heap_after),
            _fmt_bytes(rss_after),
            _fmt_native(rss_after, heap_after),
        )


@contextmanager
def profile_graph(label: str = "graph") -> Iterator[None]:
    """Log heap high-water and the RSS/heap (native) split across a whole graph.

    No-op when profiling is disabled.
    """
    if not memory_profiling_enabled():
        yield
        return

    _ensure_started()
    tracemalloc.reset_peak()
    heap_before, _ = tracemalloc.get_traced_memory()
    rss_before = _current_rss_bytes()
    logger.info(
        "[mem] %s START heap=%s rss=%s",
        label,
        _fmt_bytes(heap_before),
        _fmt_bytes(rss_before),
    )
    try:
        yield
    finally:
        heap_after, heap_peak = tracemalloc.get_traced_memory()
        rss_after = _current_rss_bytes()
        logger.info(
            "[mem] %s END heap=%s (peak=%s) rss=%s native~=%s",
            label,
            _fmt_bytes(heap_after),
            _fmt_bytes(heap_peak),
            _fmt_bytes(rss_after),
            _fmt_native(rss_after, heap_after),
        )


def _array_bytes(arr: Any) -> int:
    """Bytes held by a numpy array, including a materialized mask if present."""
    import numpy

    total = int(getattr(arr, "nbytes", 0) or 0)
    mask = numpy.ma.getmask(arr)
    if mask is not numpy.ma.nomask and hasattr(mask, "nbytes"):
        total += int(mask.nbytes)
    return total


def _iter_arrays(obj: Any) -> Iterator[Any]:
    """Yield the numpy arrays reachable from a graph result value.

    Handles RasterStack (its realized ``_data_cache``), ImageData, and bare
    arrays. Anything else is ignored.
    """
    import numpy
    from rio_tiler.models import ImageData

    from .processes.implementations.data_model import RasterStack

    if isinstance(obj, RasterStack):
        # Only realized slices hold pixel data; lazy refs do not.
        for img in list(getattr(obj, "_data_cache", {}).values()):
            yield from _iter_arrays(img)
    elif isinstance(obj, ImageData):
        if obj.array is not None:
            yield obj.array
    elif isinstance(obj, numpy.ndarray):
        yield obj


def _sum_retained_bytes(results_cache: dict) -> Tuple[int, int]:
    """Sum unique array bytes pinned by ``results_cache``.

    Arrays shared between nodes (the audit's key finding — a source ImageData
    referenced by both the load stack and a downstream stack) are counted once,
    so this reports the real retained footprint, not a double count.
    """
    seen: Set[int] = set()
    total = 0
    count = 0
    for value in results_cache.values():
        for arr in _iter_arrays(value):
            if id(arr) in seen:
                continue
            seen.add(id(arr))
            total += _array_bytes(arr)
            count += 1
    return total, count


def report_retention(
    results_cache: Optional[dict], label: str = "results_cache"
) -> None:
    """Log how much the graph engine is still pinning at end-of-graph.

    Pass the same dict handed to ``OpenEOProcessGraph.to_callable(results_cache=...)``.
    No-op when profiling is disabled or no cache was supplied.
    """
    if not memory_profiling_enabled() or results_cache is None:
        return

    total, count = _sum_retained_bytes(results_cache)
    logger.info(
        "[mem] %s: nodes_pinned=%d retained_arrays=%d retained_bytes=%s",
        label,
        len(results_cache),
        count,
        _fmt_bytes(total),
    )

    if _get_settings().memory_backrefs:
        _dump_backrefs(results_cache)


def _dump_backrefs(results_cache: dict) -> None:
    """Best-effort objgraph back-reference dump for a sample retained array.

    Shows *what* is keeping an array alive (e.g. results_cache AND a child
    stack). Requires the optional ``objgraph`` package; silently skipped if it
    is not installed.
    """
    try:
        import os

        import objgraph  # type: ignore
    except ImportError:
        logger.warning(
            "[mem] memory_backrefs requested but `objgraph` is not installed; skipping"
        )
        return

    sample = None
    for value in results_cache.values():
        for arr in _iter_arrays(value):
            sample = arr
            break
        if sample is not None:
            break
    if sample is None:
        return

    out_dir = _get_settings().memory_backrefs_dir
    path = os.path.join(out_dir, "openeo_array_backrefs.png")
    try:
        objgraph.show_backrefs([sample], max_depth=5, filename=path)
        logger.info("[mem] wrote array back-reference graph to %s", path)
    except Exception as exc:  # pragma: no cover - graphviz/runtime dependent
        logger.warning("[mem] objgraph back-ref dump failed: %s", exc)
