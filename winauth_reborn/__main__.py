import os

from waitress import serve

from .app import create_app


def main():
    app = create_app()
    host = os.environ.get("WINAUTH_HOST", "127.0.0.1")
    port = int(os.environ.get("WINAUTH_PORT", "8080"))
    print(f"WinAuth Reborn listening on http://{host}:{port}", flush=True)
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
