#!/usr/bin/env python3
"""
WARN Layoff Monitor API Server
Usage:
    python server.py                  # default: localhost:8000
    python server.py --host 0.0.0.0 --port 8080
    python server.py --reload         # dev mode with auto-reload
"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    p = argparse.ArgumentParser(description="WARN Layoff Monitor API Server")
    p.add_argument("--host",   default="127.0.0.1")
    p.add_argument("--port",   type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    args = p.parse_args()

    import uvicorn
    print(f"""
============================================================
  WARN Act Weekly Layoff Monitor
============================================================
  URL      : http://{args.host}:{args.port}
  Dashboard: http://{args.host}:{args.port}/
  API Docs : http://{args.host}:{args.port}/docs
============================================================
""")
    uvicorn.run(
        "api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
