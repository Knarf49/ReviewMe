"""Dev server entrypoint.

Usage:
    python run.py            # default: localhost:8000, reload on
    python run.py --no-reload
    python run.py --port 9000
"""
import argparse

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-reload", action="store_true")
    args = p.parse_args()

    uvicorn.run(
        "app.web.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
    )


if __name__ == "__main__":
    main()
