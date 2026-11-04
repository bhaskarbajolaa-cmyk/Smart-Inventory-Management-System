"""WSGI application entry point for production deployments (Render, Gunicorn)."""

from inventory.app.server import create_app

app = create_app()

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    app.run(host=host, port=port)
