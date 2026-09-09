"""Helpers for cleanup that must finish across task cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def await_cleanup[T](awaitable: Awaitable[T]) -> T:
    """Complete cleanup before re-delivering a cancellation request.

    A task cancelled while closing a database session can leave its pooled
    connection checked out. Running the cleanup in a child task and shielding
    it lets the cleanup finish before the original cancellation propagates.
    """

    cleanup_task = asyncio.create_task(awaitable)
    cancellation_requested = False
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            cancellation_requested = True

    result = cleanup_task.result()
    if cancellation_requested:
        raise asyncio.CancelledError
    return result
