#!/usr/bin/env python3
"""Serve only packaged demo assets, with HTTP range support for chapter seeks."""
import argparse
import json
from pathlib import Path
from urllib.parse import unquote

from starlette.applications import Starlette
from starlette.responses import FileResponse, Response
from starlette.routing import Route
import uvicorn


def application(folder):
    root = Path(folder).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    allowed = {"index.html", "manifest.json", "verification.json", "README.md", "simulator-demo.zip"}
    for record in manifest["records"]:
        allowed.update(unquote(record[key]) for key in ("video", "raw", "transcript", "evidence", "subtitles", "poster"))
        allowed.update(unquote(item["url"]) for item in [*record["images"], *record["segments"]])

    async def asset(request):
        name = request.path_params.get("name") or "index.html"
        file = root / name
        if name not in allowed or not file.is_file() or not file.resolve().is_relative_to(root):
            return Response(status_code=404)
        return FileResponse(file)

    return Starlette(routes=[Route("/{name:path}", asset)])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=8884)
    args = parser.parse_args()
    uvicorn.run(application(args.directory), host="127.0.0.1", port=args.port, log_level="warning")
