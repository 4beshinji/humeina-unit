# voisona_yomiage

VoiSona Talk + VOICEVOX + VOICEPEAK を利用した高品質音声読み上げシステム。

NLPパイプラインで話者識別・シーン判別・感情分析を行い、小説の登場人物ごとに声色やパラメータを自動で変える。青空文庫・なろう・カクヨム・汎用Webページ（trafilatura抽出）に対応し、リアルタイム読み上げとバッチ合成・動画生成の各モードを提供する。

## 特徴

- **3つのTTSエンジン**: VoiSona Talk（Windows VM）、VOICEVOX（Docker）、VOICEPEAK（ローカルCLI）をフォールバック付きで使い分け
- **NLPパイプライン**: ルールベース分類 → 話者識別 → SLM（Ollama / Gemini フォールバック）によるシーン・感情・視点分析
- **キャラクター演じ分け**: VoiSona/VOICEPEAKモードでは1ボイスのパラメータ調整、VOICEVOXモードでは複数スピーカー割当
- **バッチ合成**: 全文事前分析 → 連番WAV出力 → ffmpeg結合 → （任意で）動画生成
- **動画生成**: 字幕付き動画 / 立ち絵差し替え動画を ffmpeg ベースで合成
- **EXボイス自動挿入**: 音街ウナ等の固定WAVクリップを文脈に合わせて差し込み
- **数式読み上げ**: 数学記号を辞書ベースで読み下し
- **コンテンツソース**: 青空文庫、小説家になろう、カクヨム、汎用Webページ
- **ニュース読み上げ**: RSS取得 → SLM要約 → 速報検知（日次/ポーリング）
- **Studio（動画素材生成）**: 台本ファイル（txt/csv/json）から YMM4 等の素材を一括合成
- **Slack連携**: WebSocket監視と重要度スコアリングで重要メッセージを読み上げ
- **REST API / CLI**: FastAPIサーバーとtyper CLIの両方で操作可能

## クイックスタート

```bash
# インストール（uv推奨）
uv sync --extra dev
# または
pip install -e ".[dev]"

# 環境変数設定
cp .env.example .env
# .env を編集して VoiSona/VOICEVOX/VOICEPEAK/Ollama の接続先を設定

# リアルタイム読み上げ
uv run yomiage read "https://www.aozora.gr.jp/cards/000879/files/127_15260.html"

# バッチ合成（フルパイプライン: 分析→合成→結合）
uv run yomiage batch run "https://www.aozora.gr.jp/cards/000879/files/127_15260.html" --mode voisona

# バッチ合成 + 動画生成
uv run yomiage batch run "https://..." --mode voicepeak --video --style subtitle

# ニュース日次サマリ
uv run yomiage news daily

# 台本から動画素材生成
uv run yomiage studio synth script.txt --format ymm4

# APIサーバー起動
uv run yomiage serve
```

## 必要環境

- Python 3.12+
- [Ollama](https://ollama.ai/) + 日本語対応モデル（`qwen3:8b` 推奨。軽量用途は `qwen3.5:3b` 等）
- 以下のいずれか（複数併用可、`tts.primary_provider` / `fallback_provider` で指定）:
  - **VoiSona Talk**: Windows VM上で動作（バッチ用途は virtiofs 共有を推奨）
  - **VOICEVOX**: Docker (`infra/docker compose up -d`)
  - **VOICEPEAK**: ローカル CLI バイナリ
- **ffmpeg**: バッチ合成の結合フェーズおよび動画生成で必須
- （任意）Gemini API キー: Ollama がレートリミット等で失敗した際のフォールバック

## ドキュメント

| ドキュメント | 内容 |
|-------------|------|
| [docs/architecture.md](docs/architecture.md) | システムアーキテクチャ |
| [docs/installation.md](docs/installation.md) | インストール・環境構築 |
| [docs/cli-reference.md](docs/cli-reference.md) | CLI コマンドリファレンス |
| [docs/batch-pipeline.md](docs/batch-pipeline.md) | バッチ合成パイプライン |
| [docs/nlp-pipeline.md](docs/nlp-pipeline.md) | NLP処理パイプライン |
| [docs/tts-providers.md](docs/tts-providers.md) | TTSプロバイダー詳細 |
| [docs/configuration.md](docs/configuration.md) | 設定リファレンス |
| [docs/api-reference.md](docs/api-reference.md) | REST API リファレンス |
| [docs/virtiofs-setup.md](docs/virtiofs-setup.md) | virtiofs セットアップ |

## ライセンス

Private
