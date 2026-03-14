# voisona_yomiage

VoiSona Talk + VOICEVOX を利用した高品質音声読み上げシステム。
NLPパイプラインで話者識別・シーン判別・感情分析を行い、小説の登場人物ごとに声やパラメータを自動で変える。

## プロジェクト構造

- `src/yomiage/` — メインパッケージ
  - `tts/` — TTSプロバイダー層（VoiSona, VOICEVOX）
  - `sources/` — コンテンツソース（青空文庫, なろう, カクヨム）
  - `nlp/` — NLPパイプライン（テキスト処理, 話者識別, シーン分析）
  - `reader/` — 読み上げエンジン（オーケストレーター）
  - `news/` — ニュースモジュール
  - `slack/` — Slack連携
- `config/` — YAML設定ファイル
- `data/` — ランタイムデータ（gitignore）

## 技術スタック

- Python 3.12+, asyncio
- aiohttp (HTTP), typer (CLI), FastAPI (API server)
- loguru (ログ), pydantic (設定), pyyaml (設定ファイル)
- beautifulsoup4 + lxml (HTMLパース)

## コマンド

```bash
# インストール
pip install -e ".[dev]"

# 実行
yomiage read "https://www.aozora.gr.jp/cards/..."
yomiage news daily
yomiage serve

# テスト
pytest

# Lint
ruff check src/
```

## HEMS連携

../hems のパターン（TTSプロバイダー抽象化、MQTT、FastAPIブリッジ）を踏襲。
CLIでの単独動作も可能。
