# voisona_yomiage

VoiSona Talk + VOICEVOX を利用した高品質音声読み上げシステム。

NLPパイプラインで話者識別・シーン判別・感情分析を行い、小説の登場人物ごとに声色やパラメータを自動で変える。青空文庫・なろう・カクヨムの小説を対象とし、リアルタイム読み上げとバッチ合成の2モードに対応。

## 特徴

- **2つのTTSエンジン**: VoiSona Talk（Windows VM）と VOICEVOX（Docker）をフォールバック付きで使い分け
- **NLPパイプライン**: ルールベース分類 → 話者識別 → SLMによるシーン・感情分析
- **キャラクター演じ分け**: VoiSonaモードでは1ボイスのパラメータ調整、VOICEVOXモードでは複数スピーカー割当
- **バッチ合成**: 全文事前分析 → 連番WAV出力 → ffmpeg結合（virtiofs経由）
- **コンテンツソース**: 青空文庫、小説家になろう、カクヨム
- **ニュース読み上げ**: RSS取得 → SLM要約 → 速報検知
- **REST API / CLI**: FastAPIサーバーとtyper CLIの両方で操作可能

## クイックスタート

```bash
# インストール
pip install -e ".[dev]"

# 環境変数設定
cp .env.example .env
# .env を編集して VoiSona/VOICEVOX の接続先を設定

# リアルタイム読み上げ
yomiage read "https://www.aozora.gr.jp/cards/000879/files/127_15260.html"

# バッチ合成（フルパイプライン）
yomiage batch run "https://www.aozora.gr.jp/cards/000879/files/127_15260.html" --mode voisona

# ニュース日次サマリ
yomiage news daily

# APIサーバー起動
yomiage serve
```

## 必要環境

- Python 3.12+
- [Ollama](https://ollama.ai/) + qwen3.5:3b以上のモデル
- 以下のいずれか（または両方）:
  - **VoiSona Talk**: Windows VM上で動作（virtiofs共有推奨）
  - **VOICEVOX**: Docker (`docker compose up -d`)
- **ffmpeg**: バッチ合成の結合フェーズで必要

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
