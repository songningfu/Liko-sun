"""Start Pocket with its own environment, database and HTTPS cookie boundary."""

import argparse
import os
from pathlib import Path
import secrets

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Liko Pocket 独立云端服务")
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    os.chdir(Path(__file__).resolve().parents[1])
    args.data_dir.mkdir(parents=True, exist_ok=True)
    secret = args.data_dir / "session-secret"
    if not secret.exists():
        with secret.open("x") as output:
            output.write(secrets.token_urlsafe(48))
    os.environ.update(
        DATABASE_URL="sqlite+pysqlite:///" + str((args.data_dir / "pocket.db").resolve()),
        SESSION_SECRET=secret.read_text().strip(),
        ENVIRONMENT="production", POCKET_ENABLED="true", MEMORY_ENABLED="false",
    )
    from app.pocket_cloud import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1", log_level="warning")


if __name__ == "__main__":
    main()
