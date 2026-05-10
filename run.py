"""Entry point: python run.py"""
import uvicorn

if __name__ == "__main__":
    import sys
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload="--no-reload" not in sys.argv,
    )
