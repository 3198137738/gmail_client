"""Background thread pool for non-blocking IMAP/SMTP operations.

HTTP request handlers should never block on slow network I/O. Anything that
talks to ``imap.gmail.com`` or ``smtp.gmail.com`` is submitted here so the
response returns immediately while the work continues asynchronously.

Exceptions raised inside background tasks are logged but never propagate.
"""
from __future__ import annotations

import atexit
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from ..config import BACKGROUND_MAX_WORKERS

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=BACKGROUND_MAX_WORKERS,
            thread_name_prefix="gmail-bg",
        )
        atexit.register(shutdown_executor)
    return _executor


def _safe_call(fn: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.exception(
            "Background task failed: %s.%s",
            getattr(fn, "__module__", "?"),
            getattr(fn, "__qualname__", getattr(fn, "__name__", "?")),
        )
        return None


def submit_bg(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Schedule ``fn(*args, **kwargs)`` to run in the shared pool."""
    return _get_executor().submit(_safe_call, fn, args, kwargs)


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        try:
            _executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _executor = None
