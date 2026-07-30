# インストール・環境構築

## 前提条件

- Python 3.12+
- Linux（Ubuntu 22.04+ 推奨）
- ffmpeg（バッチ合成の結合フェーズ・動画生成で必須）

## 基本インストール

```bash
git clone https://github.com/4beshinji/humeina-unit.git
cd humeina-unit

# uv を利用する場合（推奨）
uv sync --extra dev

# あるいは pip + venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 環境変数
cp .env.example .env
# .env を編集
```

実行コマンドは `uv run yomiage ...`（または venv 有効化後 `yomiage ...`）。

## 外部サービスのセットアップ

### Ollama（必須）

NLP分析のSLMバックエンド。標準設定のデフォルトは `qwen3:8b`。軽量運用なら `qwen3.5:3b`
程度でも動作する。要約用に別モデルを指定することも可能。

```bash
# インストール
curl -fsSL https://ollama.ai/install.sh | sh

# モデルダウンロード（例）
ollama pull qwen3:8b              # 分析用（デフォルト）
ollama pull qwen3.5:3b            # 軽量分析用
ollama pull qwen3:14b             # 要約用（高品質、オプション）
```

`.env` に設定:
```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_SUMMARY_MODEL=qwen3:14b
```

### Gemini API（オプション、Ollamaフォールバック）

Ollamaがレートリミット等で失敗した場合のフォールバックとして Gemini API を利用できる。`yomiage news` 系コマンドでは `--gemini-key`（または `GEMINI_API_KEY` 環境変数）で有効化される。

```
GEMINI_API_KEY=your-gemini-api-key
```

### VOICEVOX（Docker）

```bash
cd infra/
docker compose up -d
```

VOICEVOX Engine が `http://localhost:50021` で起動する。

確認:
```bash
curl http://localhost:50021/speakers | jq '.[0].name'
```

### VoiSona Talk（Windows VM）

QEMU/KVM上のWindows VMにVoiSona Talkをインストール。

1. VoiSona Talk をVMにインストール
2. VoiSona Talk API サーバーを起動
3. `.env` に接続情報を設定:
```
VOISONA_URL=http://<VM_IP>:32766
VOISONA_USERNAME=your-email@example.com
VOISONA_PASSWORD=your-password
```

バッチ合成でファイル直接出力を使う場合は [virtiofs セットアップ](virtiofs-setup.md) を参照。
VoiSona を使わない場合は VOICEVOX または VOICEPEAK だけを設定できます。

### VOICEPEAK（ローカルCLI）

VOICEPEAK のバイナリをローカルにインストールし、ユーザー YAML の `voicepeak.path` にパスを設定する。
checkout の `config/default.yaml` を使う場合は `load_config(config_dir=Path("config"))` などで
設定ディレクトリを明示する。

```yaml
voicepeak:
  path: /path/to/voicepeak
  default_narrator: "Otomachi Una"
  max_chars: 140
  pitch_scale: 300
```

CLIを `--narrator` 等で叩いて WAV を出力する仕組みのため、追加のサーバーは不要。

### ffmpeg

```bash
sudo apt install ffmpeg
```

## .env 設定項目

| 変数 | 必須 | 説明 |
|------|------|------|
| `VOISONA_URL` | VoiSona使用時 | VoiSona Talk APIのURL |
| `VOISONA_USERNAME` | VoiSona使用時 | VoiSona Talk のユーザー名 |
| `VOISONA_PASSWORD` | VoiSona使用時 | VoiSona Talk のパスワード |
| `VOICEVOX_URL` | VOICEVOX使用時 | VOICEVOX Engine のURL |
| `OLLAMA_URL` | Ollama使用時 | Ollama のURL |
| `OLLAMA_MODEL` | Ollama使用時 | NLP分析に使うモデル名 |
| `OLLAMA_SUMMARY_MODEL` | いいえ | ニュース要約用モデル（省略時は `OLLAMA_MODEL`） |
| `GEMINI_API_KEY` | いいえ | Gemini フォールバック有効化 |
| `VOISONA_VM_MOUNT` | バッチVoiSona時 | VM内のvirtiofsマウントポイント（デフォルト: `Z:`） |
| `EX_VOICE_WAV_DIR` | EXボイス使用時 | EXボイス用WAVクリップ格納ディレクトリ |
| `SLACK_BOT_TOKEN` | Slack連携時 | Slack Bot Token（`slack-sdk` extra） |
| `SLACK_APP_TOKEN` | Slack連携時 | Slack App Token（`slack-sdk` extra） |

HEMS/MQTT 連携用の設定やサービスは実装していません。

## 動作確認

```bash
# Ollama
curl http://localhost:11434/api/tags

# VOICEVOX
curl http://localhost:50021/speakers

# VoiSona
curl -u "$VOISONA_USERNAME:$VOISONA_PASSWORD" \
  http://192.168.1.173:32766/api/talk/v1/voices

# 読み上げテスト
uv run yomiage read "https://www.aozora.gr.jp/cards/000879/files/127_15260.html" -p voicevox

# テスト実行
uv run python -m pytest -q
```

## 開発環境

```bash
# lint
uv run python -m ruff check src/ tests/

# テスト
uv run pytest -v

# 型チェック（オプション）
uv pip install mypy
uv run mypy src/yomiage/
```
