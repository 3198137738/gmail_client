"""Local development entry point.

Run:
    python run.py
or:
    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import uvicorn

from app.config import WEB_AUTH_ENABLED


def main() -> None:
    if WEB_AUTH_ENABLED:
        print("Web password protection: enabled")
    else:
        print("Web password protection: disabled; set WEB_AUTH_PASSWORD in .env or environment")

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
