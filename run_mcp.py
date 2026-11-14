"""Starts the Un-Mix-a-Lot MCP server (for Claude Desktop and other MCP apps). Talks over stdio.

Anything else that prints (downloads, installers) is sent to stderr so it can't break the conversation.
"""
import os
import sys
from io import TextIOWrapper
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Keep the real stdout for the protocol; point everything else (including child processes) at stderr.
_real_out = os.fdopen(os.dup(1), "wb", buffering=0)
os.dup2(2, 1)
sys.stdout = sys.stderr
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

import anyio  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

from stemsplit.mcp_server import mcp  # noqa: E402


async def _main():
    stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))
    stdout = anyio.wrap_file(TextIOWrapper(_real_out, encoding="utf-8", line_buffering=True))
    server = mcp._mcp_server
    async with stdio_server(stdin=stdin, stdout=stdout) as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(_main)
