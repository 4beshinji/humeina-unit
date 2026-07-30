"""FastAPI サーバー起動 CLI。"""

from __future__ import annotations

import ipaddress

import typer
from loguru import logger

from . import common


def is_loopback_host(host: str) -> bool:
    """bind先がloopbackアドレスまたはlocalhostか判定する。"""

    normalized = host.strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_server_bind(host: str, api_token: str | None) -> None:
    """安全なbind既定値と外部公開時の認証境界を検証する。"""

    if is_loopback_host(host):
        return
    if not api_token:
        raise typer.BadParameter(
            "非loopback bindにはAPI tokenが必要です。"
            " --api-tokenまたはYOMIAGE_API_TOKENを設定してください。",
            param_hint="--host",
        )


def serve(
    host: str = typer.Option("127.0.0.1", help="ホスト"),
    port: int = typer.Option(8030, help="ポート"),
    api_token: str | None = typer.Option(
        None,
        "--api-token",
        envvar="YOMIAGE_API_TOKEN",
        help="外部bind用API token",
        show_default=False,
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """FastAPIサーバー起動."""

    validate_server_bind(host, api_token)
    common.setup_logging(verbose)
    mode = "loopback" if is_loopback_host(host) else "authenticated external"
    logger.info("Server bind mode: {mode} ({host}:{port})", mode=mode, host=host, port=port)
    import uvicorn

    from ...server import create_app

    config = common.load_config()
    server_config = config.setdefault("server", {})
    server_config["host"] = host
    if api_token:
        server_config["api_token"] = api_token
    uvicorn.run(
        create_app(config),
        host=host,
        port=port,
        log_level="debug" if verbose else "info",
    )


def register(app: typer.Typer) -> None:
    """root app に server command を登録する。"""

    app.command()(serve)


__all__ = ["is_loopback_host", "register", "serve", "validate_server_bind"]
