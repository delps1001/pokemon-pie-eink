#!/usr/bin/env python3
"""
Read-only image server for ESP32 e-ink frames.

Renders today's Pokemon page with the same code the Raspberry Pi frame uses, as a
1-bit 800x480 image, and serves it over HTTP. It exposes nothing else: no config,
no control endpoints. Put it behind a reverse proxy that only forwards /frame/*.

  GET /frame/today.bin   raw 1-bit buffer, 48,000 bytes (see FORMAT below)
  GET /frame/today.png   the same image as a PNG, for checking by eye
  GET /health            liveness probe

FORMAT of today.bin: 800x480, row-major, 8 pixels per byte, most significant bit first,
bit 1 = white, bit 0 = black. No header, no padding (800 is a multiple of 8).

"Today" is the server's local date, so set TZ for the container.
"""

import sys

# No panel here: make the app fall back to its simulation mode instead of
# trying (and failing) to drive Waveshare hardware at import time.
sys.modules["waveshare_epd"] = None

import hashlib
import logging
import os
import threading
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Response

from pokemon_eink_calendar import PokemonEInkCalendar

HERE = Path(__file__).resolve().parent
CONFIG_FILE = os.environ.get("FRAME_SERVER_CONFIG", str(HERE / "frame_server_config.json"))
WIDTH, HEIGHT = 800, 480
CACHE_CONTROL = "public, max-age=600"


class FrameRenderer:
    """Renders the day's image once and keeps it until the date changes."""

    def __init__(self, config_file):
        self.calendar = PokemonEInkCalendar(
            demo_mode=False, config_file=config_file, enable_web_server=False
        )
        self._lock = threading.Lock()
        self._day = None
        self._frame = None

    def today(self):
        """Return (day, bin_bytes, png_bytes, etag) for the current local date."""
        with self._lock:
            while True:
                day = datetime.now().date()
                if day == self._day:
                    return self._frame
                self._frame = self._render(day)
                self._day = day
                # If midnight passed while rendering, loop and render the new day.
                if datetime.now().date() == day:
                    return self._frame

    def _render(self, day: date):
        image = self.calendar.create_display_image()
        if image.size != (WIDTH, HEIGHT):
            raise RuntimeError(f"unexpected image size {image.size}, expected {(WIDTH, HEIGHT)}")
        if image.mode != "1":
            raise RuntimeError(f"unexpected image mode {image.mode}, expected 1-bit")
        raw = image.tobytes()  # 1-bit, MSB first, 1 = white
        if len(raw) != WIDTH * HEIGHT // 8:
            raise RuntimeError(f"unexpected buffer length {len(raw)}")
        png = BytesIO()
        image.save(png, format="PNG")
        etag = '"%s-%s"' % (day.isoformat(), hashlib.sha1(raw).hexdigest()[:12])
        logging.info("Rendered frame for %s (%d bytes), etag %s", day, len(raw), etag)
        return (day, raw, png.getvalue(), etag)


def create_app(config_file=CONFIG_FILE):
    renderer = FrameRenderer(config_file)
    # Docs and schema routes off: this server serves two images and a health check.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def respond(request: Request, body: bytes, media_type: str, day: date, etag: str):
        headers = {
            "ETag": etag,
            "Cache-Control": CACHE_CONTROL,
            "X-Frame-Date": day.isoformat(),
            "X-Frame-Width": str(WIDTH),
            "X-Frame-Height": str(HEIGHT),
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=body, media_type=media_type, headers=headers)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/frame/today.bin")
    def frame_bin(request: Request):
        day, raw, _png, etag = renderer.today()
        return respond(request, raw, "application/octet-stream", day, etag)

    @app.get("/frame/today.png")
    def frame_png(request: Request):
        day, _raw, png, etag = renderer.today()
        return respond(request, png, "image/png", day, etag)

    renderer.today()  # render at startup so the first request is fast and bad config fails early
    return app


if __name__ == "__main__":
    uvicorn.run(
        create_app(),
        host=os.environ.get("FRAME_SERVER_HOST", "0.0.0.0"),
        port=int(os.environ.get("FRAME_SERVER_PORT", "8100")),
        access_log=True,
    )
