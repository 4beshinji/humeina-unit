# humeina-unit

VoiSona Talk、VOICEVOX、VOICEPEAK を切り替えて日本語テキストを読み上げる、Python 3.12+ の
CLI/API/SDK です。NLP 分析、発話計画、音声合成、バッチ成果物管理を単一プロセスの
モジュラーモノリスとして提供します。

## できること

- URL（青空文庫・小説家になろう・カクヨム・一般公開 Web）の取得と読み上げ
- ルールベース分類、話者候補抽出、llama.cpp/OpenCode Go/Geminiによるシーン・感情分析
- VoiSona Talk（Windows VM）、VOICEVOX（HTTP/Docker）、VOICEPEAK（ローカル CLI）の選択と
  fallback
- `UtterancePlan` による Reader、Batch、API、SDK 間の発話境界共有
- Batch の分析→合成→結合→任意動画生成、検証済み artifact manifest、partial 成果物の明示
- Studio 台本合成、ニュース RSS 読み上げ、任意の Slack WebSocket 監視

HEMS/MQTT 接続や常駐クラウドサービスは提供していません。Slack は `slack-sdk` と Bot/App
token を設定した場合だけ利用できます。

## インストール

```bash
uv sync --extra dev
# または
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

標準 YAML は wheel 内の `yomiage/resources/config/` から読み込まれます。編集用設定と
プロファイルは `YOMIAGE_USER_CONFIG_DIR`（既定 `~/.config/humeina-unit`）へ置きます。
Provider/LLM の接続先や secret は `.env` または環境変数で指定してください。詳細は
[インストール](docs/installation.md) と [設定リファレンス](docs/configuration.md) を参照してください。

## 起動方法

### クラウドLLM + CPU音声合成（既定）

既定のLLMはOpenCode Go、TTSはCPU版VOICEVOXです。`OPENCODE_GO_API_KEY`を環境変数で
設定し、VOICEVOX Engineを起動します。LLMとTTSは独立して選択でき、ローカルLLMは
明示的に有効化した場合だけ利用されます。

```bash
docker compose -f infra/docker-compose.yml up -d voicevox
export OPENCODE_GO_API_KEY='...'
uv run yomiage read "https://www.aozora.gr.jp/cards/000879/files/127_15260.html" -p voicevox
uv run yomiage batch run "https://www.aozora.gr.jp/cards/000879/files/127_15260.html" --mode voicevox
uv run yomiage studio synth script.txt --format ymm4
```

サービス選択は `YOMIAGE_LLM_PROVIDER=opencode_go|local` と
`YOMIAGE_TTS_PRIMARY_PROVIDER=voicevox|voisona|voicepeak`、または各CLIの
`--provider` / `--mode` で上書きできます。

API は既定で `127.0.0.1:8030` に bind します。

```bash
uv run yomiage serve
```

### 認証付き外部 bind（明示的な運用時だけ）

```bash
YOMIAGE_API_TOKEN='replace-with-a-long-random-token' \
  uv run yomiage serve --host 0.0.0.0 --port 8030
```

非 loopback bind は token がないと起動前に拒否されます。`/health` と `/health/live` 以外の
HTTP endpoint は Bearer token を要求します。token をログや成果物へ記録しないでください。
TLS、reverse proxy、Firewall は運用側で用意してください。

## CLI の入口

全コマンドの登録とオプションは、実装から生成される help を正とします。

```bash
uv run yomiage --help
uv run yomiage batch --help
uv run yomiage serve --help
```

代表的なコマンドは [CLI リファレンス](docs/cli-reference.md) に記載しています。

## データと移行

新規 Batch は次の canonical layout を使います。

```text
output/works/{work_id}/
├── plans/{plan_id}.json
└── runs/{run_id}/
    ├── manifest.json
    ├── artifact-manifest.json
    └── *.wav / full.wav / full.mp4
```

旧 `output/{work_id}/manifest.json` と音声は読み取り互換です。移行は旧データをバックアップ
してから read-only import/copy-on-write で新しい plan/run を作り、検証後に切り替えます。
失敗時は旧ディレクトリをそのまま使って rollback できます。具体的なチェックリストは
[legacy artifact 移行手順](docs/migration.md) を参照してください。

## ドキュメント

| 文書 | 内容 |
| --- | --- |
| [インストール](docs/installation.md) | 依存、Provider、ローカル/API 起動 |
| [アーキテクチャ](docs/architecture.md) | 実装パッケージ、依存方向、データフロー |
| [CLI リファレンス](docs/cli-reference.md) | `yomiage --help` と一致するコマンド |
| [設定リファレンス](docs/configuration.md) | Settings、環境変数、パス分類 |
| [API key保管方針](docs/credential-storage.md) | Coding Agent時代の秘密情報と対話設定 |
| [Batch パイプライン](docs/batch-pipeline.md) | plan/run、partial、artifact、動画 |
| [REST API](docs/api-reference.md) | endpoint、認証、制限、状態 semantics |
| [Python SDK](docs/sdk-reference.md) | `TTSBridge`、`TextAnalyzer`、`Pipeline` |
| [NLP パイプライン](docs/nlp-pipeline.md) | 分類・話者・シーン分析 |
| [TTS Provider](docs/tts-providers.md) | VoiSona/VOICEVOX/VOICEPEAK |
| [virtiofs](docs/virtiofs-setup.md) | VoiSona VM の file destination |
| [移行手順](docs/migration.md) | 旧 manifest/artifact の backup/import/rollback |
| [ADR 索引](docs/adr/README.md) | 設計決定と実装タスクの対応 |
| [改善ロードマップ](docs/improvement/README.md) | 作業計画と受入条件 |

## 開発時の検証

```bash
uv run python -m pytest -q
uv run python -m ruff check src/ tests/
```

外部 Provider を使う E2E は通常テストに含めず、利用可能な環境で明示的に実行します。

## ライセンス

Private
