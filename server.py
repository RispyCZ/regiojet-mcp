"""Entry point for `fastmcp run server.py` / `fastmcp dev server.py`."""

from regiojet_mcp import main, mcp

__all__ = ["mcp"]

if __name__ == "__main__":
    main()
