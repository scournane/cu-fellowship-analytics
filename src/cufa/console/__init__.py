"""The web console.

``cufa serve`` runs ``cufa.console.app:app`` under uvicorn. The app object is
not re-exported here: importing this package would then import FastAPI, Jinja2
and every module the screens touch, which makes ``cufa --version`` pay for a web
server it is not going to start.
"""

from __future__ import annotations

__all__: list[str] = []
