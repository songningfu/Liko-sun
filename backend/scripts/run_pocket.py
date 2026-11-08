"""Run an isolated local database with the existing Liko account system."""

import argparse
import os
from pathlib import Path
import secrets

import uvicorn
from dotenv import dotenv_values


def reuse_model_config(path: Path, asr_model: str = "", llm_source: str = "original", llm_model: str = "") -> None:
    """Read selected model settings on the host; do not copy or print secrets."""
    from urllib.parse import urlparse

    if not path.is_file():
        raise SystemExit("没有找到指定的模型配置文件。")
    values = dotenv_values(path)
    mapping = {
        "POCKET_LLM_BASE_URL": "MEM0_LLM_BASE_URL",
        "POCKET_LLM_API_KEY": "MEM0_LLM_API_KEY",
        "POCKET_LLM_MODEL": "MEM0_LLM_MODEL",
    }
    for target, source in mapping.items():
        if values.get(source):
            os.environ.setdefault(target, values[source])
    # ASR reuse is explicit and restricted to the verified provider endpoint.
    base = values.get("MEM0_EMBEDDER_BASE_URL") or ""
    if llm_source == "siliconflow":
        if urlparse(base).hostname != "api.siliconflow.cn" or not values.get("MEM0_EMBEDDER_API_KEY") or not llm_model:
            raise SystemExit("复用硅基流动整理服务需要有效配置和明确的模型名。")
        os.environ["POCKET_LLM_BASE_URL"] = base
        os.environ["POCKET_LLM_API_KEY"] = values["MEM0_EMBEDDER_API_KEY"]
        os.environ["POCKET_LLM_MODEL"] = llm_model
    if asr_model and urlparse(base).hostname == "api.siliconflow.cn":
        if values.get("MEM0_EMBEDDER_API_KEY"):
            os.environ.setdefault("POCKET_ASR_BASE_URL", base)
            os.environ.setdefault("POCKET_ASR_API_KEY", values["MEM0_EMBEDDER_API_KEY"])
            os.environ.setdefault("POCKET_ASR_MODEL", asr_model)


def main() -> None:
    parser = argparse.ArgumentParser(description="Liko Pocket 本地与隔离预览服务")
    parser.add_argument("--port", type=int, default=8011, choices=range(1024, 65536), metavar="PORT")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--reuse-config", type=Path)
    parser.add_argument("--asr-model", default="")
    parser.add_argument("--llm-source", choices=["original", "siliconflow"], default="original")
    parser.add_argument("--llm-model", default="")
    args = parser.parse_args()
    os.umask(0o077)
    if args.reuse_config:
        reuse_model_config(args.reuse_config, args.asr_model, args.llm_source, args.llm_model)
    backend = Path(__file__).resolve().parents[1]
    os.chdir(backend)
    data = (args.data_dir or backend / "data" / "pocket-local").resolve()
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
    print(f"Liko Pocket 原型：http://127.0.0.1:{args.port}/pocket/", flush=True)
    uvicorn.run("app.local_pocket:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
