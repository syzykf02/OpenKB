"""Dual-sink progress output for the compile pipeline.

Compiler progress historically wrote straight to stdout so the CLI shows it
regardless of the default WARNING logging level (``openkb.cli``'s
``basicConfig``). Routing the same line through a logger lets REST-API jobs
surface it as SSE ``log`` frames: ``openkb.api_ingest.JobLogHandler`` forwards
``openkb.*`` INFO records emitted by the job's worker thread to the job event
ring the UI tails.
"""

from __future__ import annotations

import logging
import sys


def emit_progress(message: str, logger_name: str = "openkb.agent.compiler") -> None:
    """Write ``message`` to stdout (CLI) and as an INFO log (job UI)."""
    sys.stdout.write(message if message.endswith("\n") else message + "\n")
    sys.stdout.flush()
    logging.getLogger(logger_name).info("%s", message)
