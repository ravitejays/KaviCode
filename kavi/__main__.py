"""Enable ``python -m kavi`` as an alternative to the ``kavi`` console script."""

from __future__ import annotations

from kavi.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
