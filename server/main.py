"""Backward-compatible ASGI import for source checkouts."""

from shadow_web.server import app, main

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
