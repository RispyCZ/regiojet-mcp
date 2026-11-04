import os
import signal
import sys
import threading

import anyio

from .server import close_client, mcp

__all__ = ["main", "mcp"]

# How long to wait for a clean shutdown after the first Ctrl+C / SIGTERM
# before forcing the process to exit.
SHUTDOWN_GRACE_SECONDS = 3.0


def _log(msg: str) -> None:
    # stdout carries the MCP stdio protocol, so log to stderr only.
    print(msg, file=sys.stderr, flush=True)


def _force_exit(code: int) -> None:
    sys.stderr.flush()
    os._exit(code)


async def _watch_signals(scope: anyio.CancelScope) -> None:
    with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
        async for _ in signals:
            if scope.cancel_called:
                _log("Forced shutdown.")
                _force_exit(130)
            _log("Shutting down RegioJet MCP server (press Ctrl+C again to force)...")
            # Close the HTTP client right away. With stdio, cancellation cannot
            # finish while anyio's stdin reader thread is blocked in read(), so
            # the server lifespan cleanup may never get a chance to run.
            with (
                anyio.CancelScope(shield=True),
                anyio.move_on_after(SHUTDOWN_GRACE_SECONDS),
            ):
                await close_client()
            scope.cancel()
            # Exit anyway if the graceful path stalls (e.g. the stdin thread).
            timer = threading.Timer(SHUTDOWN_GRACE_SECONDS, _force_exit, args=(0,))
            timer.daemon = True
            timer.start()


async def _serve_stdio() -> None:
    async with anyio.create_task_group() as tg:
        tg.start_soon(_watch_signals, tg.cancel_scope)
        await mcp.run_async("stdio")
        tg.cancel_scope.cancel()


def main() -> None:
    transport = os.getenv("REGIOJET_MCP_TRANSPORT", "stdio")
    if transport != "stdio":
        # uvicorn handles SIGINT/SIGTERM gracefully on its own.
        try:
            mcp.run(
                transport=transport,
                host=os.getenv("REGIOJET_MCP_HOST", "127.0.0.1"),
                port=int(os.getenv("REGIOJET_MCP_PORT", "8000")),
            )
        except KeyboardInterrupt:
            pass
        return

    try:
        anyio.run(_serve_stdio)
    except KeyboardInterrupt:
        _force_exit(130)
    # Don't let interpreter shutdown wait on the stdin reader thread.
    _force_exit(0)
