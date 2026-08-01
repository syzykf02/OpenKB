"""Async off-load of the compiler's synchronous LLM call.

The recompile pipeline (``iter_recompile``) runs on the API event loop, where
``compiler._llm_call`` would otherwise block on a synchronous
``litellm.completion`` for the whole request. Running it via
``asyncio.to_thread`` keeps the loop responsive; ``to_thread`` copies the
current contextvars, so cooperative cancellation (``check_cancelled``) still
sees the job's cancel flag.
"""

from __future__ import annotations

import asyncio
from typing import Any

__all__ = ["llm_call_off_loop"]


async def llm_call_off_loop(
    model: str,
    messages: list[dict],
    step_name: str,
    raise_on_truncation: bool = False,
    *,
    bundle=None,
    **kwargs: Any,
) -> str:
    """Run ``compiler._llm_call`` in a worker thread (non-blocking on the loop)."""
    from openkb.agent.compiler import _llm_call

    return await asyncio.to_thread(
        _llm_call,
        model,
        messages,
        step_name,
        raise_on_truncation=raise_on_truncation,
        bundle=bundle,
        **kwargs,
    )
