# humeina-unit — AI Coding Agent Guide

> このファイルは AI コーディングエージェント向けのプロジェクトガイドです。以前の `AGENTS.md` は存在しなかったため、プロジェクトの実態に基づいて新規作成しています。

## プロジェクト概要

`humeina-unit` は、VoiSona Talk / VOICEVOX / VOICEPEAK を使った日本語音声読み上げシステムです。
NLP パイプラインで話者識別・シーン判別・感情分析を行い、小説の登場人物ごとに声色やパラメータを自動で変更します。

- **パッケージ名**: `humeina-unit`
- **バージョン**: `0.1.0`
- **Python**: 3.12 以上（`pyproject.toml` の `requires-python`）
- **ビルドバックエンド**: hatchling
- **CLI エントリポイント**: `yomiage`
- **主要モード**:
  - リアルタイム読み上げ（`yomiage read` / `yomiage serve`）
  - バッチ合成（`yomiage batch run` / `analyze` / `synthesize` / `concat`）
  - 動画素材生成（`yomiage studio synth`）
  - ニュース読み上げ（`yomiage news daily` / `check`）
  - Slack 連携（`yomiage slack start`）

## 技術スタック

- **言語**: Python 3.12+
- **非同期**: `asyncio` / `aiohttp`
- **CLI**: `typer`
- **REST API**: `FastAPI` + `uvicorn`
- **設定**: `pyyaml` + `pydantic`（API 層）
- **ログ**: `loguru`（統一）
- **HTML パース**: `beautifulsoup4` + `lxml`, `trafilatura`
- **RSS**: `feedparser`
- **音声再生**: `sounddevice`（フォールバック）, `aplay`
- **動画/画像**: `ffmpeg`（必須）, `Pillow`（オプション）
- **TTS エンジン**:
  - VoiSona Talk（Windows VM 上 REST API）
  - VOICEVOX Engine（Docker）
  - VOICEPEAK（ローカル CLI バイナリ）
- **SLM（NLP 分析）**: Ollama（必須）, Gemini API（オプションのフォールバック）

## ディレクトリ構成

```
.
├── src/yomiage/           # メインパッケージ
│   ├── cli.py             # typer CLI エントリポイント
│   ├── server.py          # FastAPI サーバー
│   ├── config.py          # YAML + 環境変数の設定ロード
│   ├── api/               # 公開 API（Pipeline / TTSBridge / TextAnalyzer）
│   ├── nlp/               # NLP パイプライン
│   ├── reader/            # リアルタイム読み上げエンジン
│   ├── batch/             # バッチ合成パイプライン（A/B/C/D フェーズ）
│   ├── tts/               # TTS プロバイダー抽象化・管理
│   ├── sources/           # コンテンツソース（青空/なろう/カクヨム/汎用Web）
│   ├── exvoice/           # EX ボイス自動挿入
│   ├── studio/            # 動画素材生成（台本→音声）
│   ├── video/             # 動画生成（バッチ Phase D）
│   ├── tools/             # ボイスプロファイル チューニング
│   ├── news/              # RSS 取得・要約・速報検知
│   └── slack/             # Slack WebSocket 監視
├── config/                # YAML 設定
├── tests/                 # pytest テスト
├── infra/                 # Docker Compose（VOICEVOX）
├── assets/                # 動画用背景・立ち絵・BGM/SE
├── data/                  # ランタイムデータ（gitignore）
├── output/                # 合成音声・動画出力（gitignore）
├── AIVoice/               # Python ソースからは参照されていない外部バイナリ群
└── docs/                  # 詳細ドキュメント
```

## インストール・ビルドコマンド

推奨は `uv` を使う方法です。

```bash
# 依存 + dev 依存（pytest / ruff / Pillow）をインストール
uv sync --extra dev

# または pip + venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 環境変数テンプレートをコピーして編集
cp .env.example .env
```

外部サービスの準備:

- Ollama（必須）: `ollama pull qwen3:8b` など
- VOICEVOX: `cd infra && docker compose up -d`
- VoiSona Talk: Windows VM 上で API サーバーを起動
- VOICEPEAK: ローカル CLI バイナリを設置し `config/default.yaml` の `voicepeak.path` を設定
- ffmpeg: `sudo apt install ffmpeg`

## 実行コマンド

```bash
# リアルタイム読み上げ
uv run yomiage read "https://www.aozora.gr.jp/cards/000879/files/127_15260.html"

# バッチ合成（分析→合成→結合）
uv run yomiage batch run "https://..." --mode voisona

# 動画まで生成
uv run yomiage batch run "https://..." --mode voicepeak --video --style portrait

# ニュース日次サマリ
uv run yomiage news daily

# 台本から素材合成
uv run yomiage studio synth script.txt --format ymm4

# API サーバー
uv run yomiage serve
```

## テスト・Lint コマンド

`pyproject.toml` に `tool.pytest.ini_options` と `tool.ruff` が設定されています。

```bash
# テスト（362 passed / 21 skipped を確認済み）
uv run python -m pytest -q
# または
.venv/bin/python -m pytest -q

# Lint
uv run python -m ruff check src/ tests/
```

注意:

- `uv run pytest` が失敗する場合があります。`.venv/bin/pytest` の shebang が旧プロジェクトパスを指していることが原因です。対処は `uv run python -m pytest` を使うか、`.venv` を作り直してください。
- `ruff check src/ tests/` は 0 件の指摘となることを確認済みです。

## ランタイムアーキテクチャ

```
CLI (cli.py) / FastAPI (server.py)
        ↓
ReadingEngine / BatchEngine / StudioEngine
        ↓
NLP Pipeline
  TextProcessor → TextClassifier → SpeakerExtractor → SceneAnalyzer
  + MathProcessor / TextSplitter
        ↓
ParamMapper（キャラ × シーン × 感情 → TTSParams）
        ↓
TTS Layer（VoiSona / VOICEVOX / VOICEPEAK）
        ↓
Playback / ffmpeg concat / Video Composer
```

### 主要データフロー

1. **URL → ソース**: `sources/registry.py` が URL から `AozoraSource` / `NarouSource` / `KakuyomuSource` / `GenericWebSource` を選択します。
2. **テキスト前処理**: `TextProcessor` で NFKC 正規化・注記除去・句読点統一などを行います。
3. **チャンク分割**: `TextSplitter` が TTS に適した長さ（デフォルト 200 文字）に分割します。
4. **NLP 分析**:
   - `TextClassifier`: DIALOGUE / NARRATION / THOUGHT / SCENE_BREAK に分類
   - `SpeakerExtractor`: 発話動詞パターンから話者候補を抽出
   - `SceneAnalyzer`: Ollama/Gemini で話者確定・シーン・感情・強度を判定
5. **パラメータ生成**: `ParamMapper` が `scene_params.yaml` / キャラクター DB に基づいて `TTSParams` を生成します。
6. **音声合成**: `TTSManager`（リアルタイム）または `BatchSynthesizer`（バッチ）がプロバイダーを制御します。
7. **動画生成（任意）**: `video/` モジュールが字幕（ASS/SRT）・立ち絵・BGM/SE を合成します。

### バッチパイプライン

- **Phase A**: 全文分析 → `output/{work_id}/manifest.json`
- **Phase B**: 文ごとに音声合成 → `NNNN.wav`
- **Phase C**: ffmpeg concat → `chapter_NNN.wav` / `full.wav`
- **Phase D**: 動画生成 → `full.mp4`

## 設定体系

設定は **YAML ファイル** + **`.env` 環境変数**の 2 層です。

| ファイル | 内容 |
|---|---|
| `config/default.yaml` | メイン設定（tts / voisona / voicevox / voicepeak / ollama / news / slack / reader / batch / studio / video / ex_voice） |
| `config/scene_params.yaml` | シーン修飾子・感情スタイルウェイト |
| `config/voices.yaml` | プロバイダー別ボイス定義 |
| `config/math_dict.yaml` | 数式記号→読み下し辞書 |
| `config/voice_profiles/*.yaml` | VoiSona/VOICEVOX 用プロファイル |
| `config/voicepeak_profiles/*.yaml` | VOICEPEAK 用プロファイル |
| `.env` | 認証情報・接続先（gitignore） |

YAML 内では `${VAR:-default}` 形式で環境変数を参照できます（`src/yomiage/config.py`）。

主な環境変数:

- `VOISONA_URL`, `VOISONA_USERNAME`, `VOISONA_PASSWORD`
- `VOICEVOX_URL`
- `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_SUMMARY_MODEL`
- `GEMINI_API_KEY`
- `VOISONA_VM_MOUNT`
- `EX_VOICE_WAV_DIR`
- `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`
- `MQTT_BROKER`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS`

## コードスタイル・開発規約

- **Python バージョン**: 3.12 以上。`from __future__ import annotations` を積極的に使用。
- **型ヒント**: 必須ではありませんが、新規コードでは積極的に付与してください。
- **非同期**: 入出力処理は原則 `async`/`await`。`asyncio.create_task` で先読みやパイプラインを組んでいます。
- **ログ**: 統一して `loguru` を使用。`print` は避けてください。
- **データクラス**: `dataclasses.dataclass` で結果型を定義。API 層では `pydantic.BaseModel` を使用。
- **抽象基底クラス**: `TTSProvider`, `ContentSource`, `LLMBackend` などは `ABC` で拡張ポイントを明確にしています。
- **ドキュメント**: ドキュメント文字列・コメントは日本語で記述するのが慣例です。
- **インポート順**: `ruff` の `I` ルールで整えます。標準ライブラリ → サードパーティ → 自パッケージの順。
- **行長**: 100 文字（`tool.ruff.line-length`）。

## テスト戦略

- **フレームワーク**: `pytest` + `pytest-asyncio`（`asyncio_mode = auto`）
- **テストファイル**: `tests/test_*.py`
- **外部依存の扱い**: TTS/LLM など外部サービスは `unittest.mock.AsyncMock` や自作モッククラスで置き換えます。外部サービスなしで実行可能です。
- **テスト分類例**:
  - NLP コンポーネント（classifier, text_processor, splitter, speaker）
  - API 層（pipeline, bridge, analyzer, llm_backend）
  - データ永続化（character_db, studio cache/naming/parser/engine）
  - 動画合成（video）
  - ボイスプロファイル（voice_profile, voicevox_profile, voicepeak_profile）

## セキュリティ・運用に関する注意

- **認証情報**: パスワード/API キーは `.env` に格納し、ソースコードや Git に含めないでください。
- **サブプロセス**: `ffmpeg` および `voicepeak` CLI を直接起動しています。入力パスや引数を検証する際は注入リスクに注意してください。
- **外部通信**: VoiSona Talk は通常 LAN 内の Windows VM、VOICEVOX/Ollama は localhost で動作します。
- **商用ライセンス**: VOICEPEAK は商用利用条件があります。CI や共有環境で実行する場合はライセンス境界を明確にしてください。
- **LLM プロンプト**: ユーザー提供テキストをそのまま LLM プロンプトに含める箇所があります。プロンプトインジェクションへの配慮が必要な場合は追加の検証を検討してください。
- **CI**: 現在 `.github/workflows/` は存在しません。`pytest` + `ruff` を回すシンプルな CI を追加する余地があります。

## 補足

- `AIVoice/` ディレクトリはリポジトリに含まれていますが、`src/yomiage/` 以下の Python ソースからは参照されていません。今後の運用方針（削除・移動・統合）を決定する必要があります。
- `output/` には過去の合成結果が滞留しやすいため、定期的なクリーンアップまたは自動削除機能の検討が必要です。
- プロジェクト名は `humeina-unit` に統一済みです。旧名 `voisona_yomiage` はソース・ドキュメントから置き換え済みです。
