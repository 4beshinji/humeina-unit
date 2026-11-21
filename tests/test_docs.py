"""ドキュメントのリンクと CLI snippet の軽量 smoke test。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yomiage.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = (REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").rglob("*.md")))
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_local_markdown_links_resolve() -> None:
    """外部サイトを取得せず、repo 内相対リンクだけを検証する。"""

    missing: list[str] = []
    for document in MARKDOWN_FILES:
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", maxsplit=1)[0]
            if target and not (document.parent / target).resolve().exists():
                missing.append(f"{document.relative_to(REPO_ROOT)} -> {target}")
    assert not missing, "broken local markdown links: " + ", ".join(missing)


# This is the command subset shown in docs/cli-reference.md and README.  Appending --help
# exercises registration/option parsing without contacting a Provider or executing a job.
DOCUMENTED_COMMANDS = (
    ("configure",),
    ("read",),
    ("resume",),
    ("voices", "list"),
    ("characters", "list"),
    ("characters", "assign"),
    ("batch", "analyze"),
    ("batch", "synthesize"),
    ("batch", "concat"),
    ("batch", "run"),
    ("batch", "subtitle"),
    ("batch", "video"),
    ("batch", "status"),
    ("batch", "retry"),
    ("tune", "range"),
    ("tune", "preset"),
    ("tune", "emotion"),
    ("tune", "noise"),
    ("tune", "demo"),
    ("tune", "test"),
    ("vp-tune", "range"),
    ("vp-tune", "preset"),
    ("vp-tune", "emotion"),
    ("vp-tune", "noise"),
    ("vp-tune", "demo"),
    ("vp-tune", "test"),
    ("studio", "synth"),
    ("studio", "preview"),
    ("studio", "voices"),
    ("news",),
    ("slack",),
    ("serve",),
)


@pytest.mark.parametrize("command", DOCUMENTED_COMMANDS)
def test_documented_cli_commands_render_help(command: tuple[str, ...]) -> None:
    result = CliRunner().invoke(app, [*command, "--help"])
    assert result.exit_code == 0, result.stdout
    assert "Usage:" in result.stdout
