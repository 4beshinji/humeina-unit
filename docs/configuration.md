# 設定リファレンス

設定は `load_settings()`/`Settings`（新規コード）または互換用 `load_config()`（legacy
Provider/CLI）から読み込みます。解決順は次のとおりです。

1. wheel に同梱された immutable 標準 YAML
2. `YOMIAGE_USER_CONFIG_DIR` のユーザー YAML
3. YAML 内 `${VAR}` 展開と `YOMIAGE_*` 環境変数
4. `overrides` など呼び出し側の明示値

相対パスはロード時に絶対化されます。標準 YAML はカレントディレクトリではなく
`importlib.resources` から読み込まれます。

## ファイルとパスの境界

| 種類 | 既定値/例 | 編集可否 |
| --- | --- | --- |
| 標準設定 | `yomiage/resources/config/{default,voices,scene_params,math_dict}.yaml` | 不可（wheel 内） |
| ユーザー設定 | `~/.config/humeina-unit/config.yaml` または `settings.yaml` | 可 |
| Batchプロファイル | `batch.voice_profile_dir` 等で指定したディレクトリ | 可 |
| runtime data | `./data/`（`YOMIAGE_DATA_DIR`） | 可/バックアップ対象 |
| Batch output | `./output/`（`YOMIAGE_OUTPUT_DIR` または `batch.output_dir`） | 可/成果物 |
| assets | `./assets/`（`YOMIAGE_ASSETS_DIR`） | 可 |
| Job DB | `./data/jobs.sqlite3`（`YOMIAGE_SQLITE_PATH`） | 可/バックアップ対象 |

checkout の `config/` を開発用に使う場合は `load_config(config_dir=Path("config"))` または
`Settings.from_yaml("config", base_dir=...)` のように明示します。インストール後の標準値を
リポジトリ相対パスから解決しないでください。

## 主要な YAML セクション

### `tts`

```yaml
tts:
  primary_provider: voicevox      # voisona / voicevox / voicepeak
  fallback_provider: null         # primary と同じ値は禁止
  lookahead_chunks: 5
  max_chunk_chars: 200
```

### Provider

```yaml
voisona:
  url: ${VOISONA_URL:-http://localhost:32766}
  username: ${VOISONA_USERNAME}
  password: ${VOISONA_PASSWORD}
  default_voice: nurse-robot-type-t_ja_JP

voicevox:
  url: ${VOICEVOX_URL:-http://localhost:50021}
  default_speaker: 47

voicepeak:
  path: /absolute/path/to/voicepeak
  default_narrator: "Otomachi Una"
  max_chars: 140
```

`Settings` では同じ内容を `providers.voisona` / `providers.voicevox` /
`providers.voicepeak` に正規化します。primary Provider が有効な場合、VoiSona の username/password、
VOICEPEAK の executable path など必要値を起動前に検証します。無効な fallback の secret は要求
されません。

### `llm` とニュース

```yaml
llm:
  providers:
    local:
      type: llama_cpp
      base_url: ${LLAMA_CPP_URL:-http://127.0.0.1:8080/v1}
      model: ${LLAMA_CPP_MODEL:-Qwen3.8-27B}
      protocol: chat_completions
      context_tokens: 32768
      timeout_seconds: 600
      min_output_tokens: 512
      enabled: false
    opencode_go:
      type: opencode_go
      base_url: https://opencode.ai/zen/go/v1
      api_key: ${OPENCODE_GO_API_KEY:-}
      model: ${OPENCODE_GO_MODEL:-qwen3.8-flash}
      protocol: auto
      context_tokens: 131072
      min_output_tokens: 2048
      enabled: true
  routes:
    scene_analysis: {provider: opencode_go, max_output_tokens: 16384}
    news_summary: {provider: opencode_go, max_output_tokens: 16384}
    urgency_scoring: {provider: opencode_go, max_output_tokens: 4096}
  fallback:
    order: [opencode_go]
    "on": [unavailable, timeout, rate_limit]

news:
  urgency_threshold: 0.8
  sources: [nhk_main, bbc_world, guardian_world]
  tts: {speed: 1.1}
```

Provider（接続先）とProtocol（`chat_completions` / `responses` / `messages`）は独立しています。
OpenCode Go が既定で、API keyは環境変数・KWallet・所有者専用のOpenCode認証ファイルの
いずれかから実行時にだけメモリへ読み込みます。本文、完全URL、API keyはmetrics、manifest、
Job DBへ保存されません。旧`ollama:`は`ollama_legacy`へ自動変換され、deprecation warningを
出します。

OpenCode Goへ小説、ニュース、Slack本文を送る前に、利用モデルのprivacy条件を確認してください。
標準fallbackにローカルLLMは含まれません。ローカルLLMを使うには、`local.enabled: true` と
task routeの`provider: local`を両方指定するか、環境変数
`YOMIAGE_LOCAL_LLM_ENABLED=true YOMIAGE_LLM_PROVIDER=local`を指定します。

`yomiage configure`はAPI keyをYAMLへ書かず、`secrets.opencode_go`へKWallet、既存OpenCode
mode 0600ファイル、または環境変数の参照だけを保存します。詳細は
[API key保管方針](credential-storage.md)を参照してください。

クラウド既定では、呼び出し側が小さな値を指定しても`min_output_tokens: 2048`を確保し、
`max_output_tokens`は通常taskで8192〜16384、短い採点でも4096とします。上限は暴走・課金事故を
防ぐガードであり、回答の短さを強制する値ではありません。短さと形式はprompt、JSON schema、
response validationで制御します。ローカルLLMを選択した場合も最低512を確保し、VRAM/生成時間向け
の別上限（通常2048〜8192、採点1024）へ切り替わります。OpenCode Go公式catalogはQwen3.8 Flashの
context上限を掲載していないため、クラウド設定の`context_tokens`は保守的な128Kです。

### `batch`

```yaml
batch:
  output_dir: output
  default_mode: voicevox
  analysis_window_chars: 3000
  analysis_window_sentences: 25
  analysis_strategy: single_pass
  manifest_save_interval: 10
  silence_duration: 1.5
  concat_format: wav
  cleanup_after_concat: false
  voisona_vm_mount: Z:
  voice_profile_dir: ~/.config/humeina-unit/voice_profiles
  voicevox_profile_dir: ~/.config/humeina-unit/voicevox_profiles
  voicepeak_profile_dir: ~/.config/humeina-unit/voicepeak_profiles
  synth_concurrency: 1
```

`analysis_window_sentences`はモデルcontextではなく1batchの最大項目数です。実際の分割は
`context_tokens`、出力token予算、安全率から決まり、`analysis_strategy: exhaustive`だけが品質比較用の
旧2-pass相当動作を有効にします。`output/works/{work_id}/plans/{plan_id}.json` と `runs/{run_id}/` が
新しい保存形式です。
旧 manifest は読み取り互換ですが、既存ファイルを上書きして移行しません。`synth_concurrency`
は 1 以上 256 以下です。

### `server` と `storage`

```yaml
server:
  host: 127.0.0.1
  port: 8030
  api_token: ${YOMIAGE_API_TOKEN}
  max_body_bytes: 1048576
  max_text_chars: 100000
  max_url_response_bytes: 10485760
  job_queue_size: 64
  max_active_jobs: 65
  client_rate_limit: 60
  client_rate_window_seconds: 60

storage:
  data_dir: data
  output_dir: output
  assets_dir: assets
  sqlite_path: data/jobs.sqlite3
  ffmpeg_executable: null
```

`server.host` の既定は loopback です。非 loopback bind は token がないと拒否されます。
`/health` と `/health/live` 以外の endpoint に Bearer 認証を付ける場合は、[REST API](api-reference.md)
の起動手順に従います。本文、URL取得サイズ、job admission、rate limit は API 側でも検証されます。

## 環境変数

Provider/LLM の接続先は従来の変数も受け付けます。パス・運用制御は `YOMIAGE_*` を優先します。

| 変数 | 用途 |
| --- | --- |
| `VOISONA_URL`, `VOISONA_USERNAME`, `VOISONA_PASSWORD` | VoiSona 接続/認証 |
| `VOICEVOX_URL` | VOICEVOX 接続 |
| `VOICEPEAK_PATH` | VOICEPEAK executable |
| `OPENCODE_GO_API_KEY`, `OPENCODE_GO_MODEL` | 標準OpenCode Go接続 |
| `YOMIAGE_LLM_PROVIDER` | LLM選択（`opencode_go` / `local`） |
| `YOMIAGE_LOCAL_LLM_ENABLED` | ローカルLLMの明示的な有効化 |
| `LLAMA_CPP_URL`, `LLAMA_CPP_MODEL` | 任意llama.cpp接続/model alias |
| `YOMIAGE_LLM_GPU_DEVICE`, `LLAMA_MODEL_PATH` | ROCm GPU選択/server起動 |
| `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_SUMMARY_MODEL` | 旧Ollama rollback |
| `GEMINI_API_KEY` | ニュース用 Gemini fallback |
| `YOMIAGE_USER_CONFIG_DIR` | ユーザー設定/プロファイル root |
| `YOMIAGE_DATA_DIR`, `YOMIAGE_OUTPUT_DIR`, `YOMIAGE_ASSETS_DIR` | data/output/assets |
| `YOMIAGE_SQLITE_PATH` | JobRepository DB |
| `YOMIAGE_FFMPEG_EXECUTABLE` | ffmpeg の絶対パス |
| `YOMIAGE_API_TOKEN` | 外部 bind と API Bearer token |
| `YOMIAGE_SERVER_MAX_TEXT_CHARS`, `YOMIAGE_SERVER_MAX_BODY_BYTES` | API 入力上限 |
| `YOMIAGE_SERVER_MAX_URL_RESPONSE_BYTES` | URL 取得 bytes 上限 |
| `YOMIAGE_SERVER_JOB_QUEUE_SIZE`, `YOMIAGE_SERVER_MAX_ACTIVE_JOBS` | admission 上限 |
| `YOMIAGE_SERVER_CLIENT_RATE_LIMIT`, `YOMIAGE_SERVER_CLIENT_RATE_WINDOW_SECONDS` | peer 単位 rate limit |
| `VOISONA_VM_MOUNT` | VoiSona VM 内の file destination mount |
| `EX_VOICE_WAV_DIR` | EX voice WAV ディレクトリ |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | 任意 Slack 監視 |

HEMS/MQTT 用の設定キーや接続手順はありません。

## Python から読む

```python
from yomiage.settings import Settings
from yomiage.config import load_settings

settings = load_settings(user_config_dir="~/.config/humeina-unit")
settings = Settings.from_yaml(user_config_dir="~/.config/humeina-unit")
legacy = settings.to_legacy_dict()  # 旧 Provider/CLI adapter 用
```

公開 SDK の `PipelineConfig.from_yaml()` は互換用の軽量 DTO です。新規 application 境界では
型付き `Settings` を渡してください。Provider secret を `print(settings)` やログに出さないでください。
