# インストール・環境構築

## 前提条件

- Python 3.12+
- Linux（Ubuntu 22.04+ 推奨）
- ffmpeg（バッチ合成の結合に必要）

## 基本インストール

```bash
git clone https://github.com/4beshinji/voisona-yomiage.git
cd voisona-yomiage

# 仮想環境
python3 -m venv .venv
source .venv/bin/activate

# パッケージインストール
pip install -e ".[dev]"

# 環境変数
cp .env.example .env
# .env を編集
```

## 外部サービスのセットアップ

### Ollama（必須）

NLP分析のSLMバックエンド。

```bash
# インストール
curl -fsSL https://ollama.ai/install.sh | sh

# モデルダウンロード
ollama pull qwen3.5:4b       # 分析用（軽量）
ollama pull qwen3:14b         # 要約用（高品質、オプション）
```

`.env` に設定:
```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:4b
OLLAMA_SUMMARY_MODEL=qwen3:14b
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
| `OLLAMA_URL` | はい | Ollama のURL |
| `OLLAMA_MODEL` | はい | NLP分析に使うモデル名 |
| `OLLAMA_SUMMARY_MODEL` | いいえ | ニュース要約用モデル（省略時はOLLAMA_MODEL） |
| `VOISONA_VM_MOUNT` | バッチVoiSona時 | VM内のvirtifsマウントポイント（デフォルト: `Z:`） |
| `SLACK_BOT_TOKEN` | Slack連携時 | Slack Bot Token |
| `SLACK_APP_TOKEN` | Slack連携時 | Slack App Token |

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
yomiage read "https://www.aozora.gr.jp/cards/000879/files/127_15260.html" -p voicevox

# テスト実行
pytest
```

## 開発環境

```bash
# lint
ruff check src/

# テスト
pytest -v

# 型チェック（オプション）
pip install mypy
mypy src/yomiage/
```
