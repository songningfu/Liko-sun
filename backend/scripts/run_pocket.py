"""Run an isolated local database with the existing Liko account system."""

import os
from pathlib import Path
import secrets

import uvicorn


def main() -> None:
    backend = Path(__file__).resolve().parents[1]
    os.chdir(backend)
    data = backend / "data" / "pocket-local"
    data.mkdir(parents=True, exist_ok=True)
    secret_file = data / "session-secret"
    if not secret_file.exists():
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(secrets.token_urlsafe(48))
    # Fixed local database avoids accidentally connecting to a deployed database.
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///" + str(data / "pocket.db")
    os.environ["SESSION_SECRET"] = secret_file.read_text().strip()
    os.environ["ENVIRONMENT"] = "development"
    os.environ["POCKET_ENABLED"] = "true"
    os.environ["MEMORY_ENABLED"] = "false"
    print("Liko Pocket 本地原型：http://127.0.0.1:8011/pocket/", flush=True)
    uvicorn.run("app.local_pocket:app", host="127.0.0.1", port=8011, log_level="warning")


if __name__ == "__main__":
    main()
