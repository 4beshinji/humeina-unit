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

対話セットアップではLLM/TTSの選択、OpenCode Go API keyの安全な保管、VOICEVOXの
キャラクター、スタイルを順に選び、話速・ピッチ・抑揚・音量を矢印キーで一括調整できます。
値を変更するたびにバックグラウンドで自動合成・再生します。

```bash
uv run yomiage configure
```

API keyはYAMLへ保存せず、利用可能ならKWalletを使います。詳細と脅威境界は
[API key保管方針](credential-storage.md)を参照してください。

## 外部サービスのセットアップ

### OpenCode Go（標準クラウドLLM）

`OPENCODE_GO_API_KEY`を環境変数へ設定します。標準モデルはQwen3.8 Flash（Messages API）です。

```dotenv
OPENCODE_GO_API_KEY=your-api-key
OPENCODE_GO_MODEL=qwen3.8-flash
YOMIAGE_LLM_PROVIDER=opencode_go
```

実API testは課金を避けるため通常skipされ、`RUN_PAID_LLM_TESTS=1`も指定した場合だけ
実行されます。OpenCode Goへ送る本文のprivacy条件と利用枠を事前に確認してください。

### llama.cpp（明示的に有効化するローカルLLM）

2026-08-29時点でQwen公式の最新open-weight世代はQwen3.8です。標準モデルIDは
`Qwen3.8-27B`です。RX 6900 XT 16GB向けのGGUFには、Hugging Faceのモデルカードが
16GB級向けとして推奨する`bartowski/Qwen3.8-27B-GGUF:IQ4_XS`（15.57GB）を選びます。
32K KV cache用の余白を確保するため、起動時の`--fit-target 2048`による一部CPU offloadを許容します。

```bash
# upstreamのROCm buildまたは公式server-rocm imageを用意する
# GGUFを任意のmodel directoryへ配置後、GPU indexとpathを必ず明示する
export YOMIAGE_LLM_GPU_DEVICE=0
export LLAMA_MODEL_PATH=/models/Qwen3.8-27B-IQ4_XS.gguf
infra/llama-server-rocm.sh
```

`.env`に設定:

```dotenv
YOMIAGE_LOCAL_LLM_ENABLED=true
YOMIAGE_LLM_PROVIDER=local
LLAMA_CPP_URL=http://127.0.0.1:8080/v1
LLAMA_CPP_MODEL=Qwen3.8-27B
YOMIAGE_LLM_GPU_DEVICE=0
```

ROCm複数GPU環境ではGPU自動選択を使用しません。起動scriptは
`ROCR_VISIBLE_DEVICES` / `HIP_VISIBLE_DEVICES` / `GPU_DEVICE_ORDINAL`を同じindexへ固定し、
可視化後の論理deviceを`--device ROCm0 --split-mode none --main-gpu 0`でも固定します。
index未指定なら起動前に失敗します。このホストでは
GPU 0がRX 6900 XT（gfx1030、16GB）、GPU 1がRaphael iGPU（512MB）です。
短い構造化NLP処理で出力予算を内部thinkingに消費しないよう、scriptは`--reasoning off`も指定します。

Dockerを使う場合:

```bash
YOMIAGE_LLM_GPU_DEVICE=0 \
LLAMA_MODEL_DIR=/absolute/path/to/models \
LLAMA_MODEL_FILE=Qwen3.8-27B-IQ4_XS.gguf \
docker compose \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.local-llm.yml \
  --profile local-llm up llama-server-rocm
```

### Gemini API（オプションfallback）

ローカルProviderが失敗した場合の明示fallbackとして利用できる。`yomiage news`系コマンドでは
`--gemini-key`（または`GEMINI_API_KEY`）で有効化される。

```
GEMINI_API_KEY=your-gemini-api-key
```

### VOICEVOX（Docker）

```bash
docker compose -f infra/docker-compose.yml up -d voicevox
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
| `LLAMA_CPP_URL` | 標準LLM使用時 | llama-serverのOpenAI互換URL |
| `LLAMA_CPP_MODEL` | 標準LLM使用時 | API上のmodel alias |
| `LLAMA_MODEL_PATH` | llama-server起動時 | GGUFの絶対path |
| `YOMIAGE_LLM_GPU_DEVICE` | ROCm使用時 | 明示的なROCm GPU index |
| `OLLAMA_URL`, `OLLAMA_MODEL` | rollback時のみ | 旧Ollama互換設定 |
| `GEMINI_API_KEY` | いいえ | Gemini フォールバック有効化 |
| `VOISONA_VM_MOUNT` | バッチVoiSona時 | VM内のvirtiofsマウントポイント（デフォルト: `Z:`） |
| `EX_VOICE_WAV_DIR` | EXボイス使用時 | EXボイス用WAVクリップ格納ディレクトリ |
| `SLACK_BOT_TOKEN` | Slack連携時 | Slack Bot Token（`slack-sdk` extra） |
| `SLACK_APP_TOKEN` | Slack連携時 | Slack App Token（`slack-sdk` extra） |

HEMS/MQTT 連携用の設定やサービスは実装していません。

## 動作確認

```bash
# llama.cpp
curl http://127.0.0.1:8080/health

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
