# 設定リファレンス

設定は `load_settings()`/`Settings`（新規コード）または互換用 `load_config()`（legacy
Provider/CLI）から読み込みます。解決順は次のとおりです。

1. wheel に同梱された immutable 標準 YAML
2. `YOMIAGE_USER_CONFIG_DIR` のユーザー YAML
3. YAML 内 `${VAR}` 展開と `YOMIAGE_*` 環境変数
4. `overrides` など呼び出し側の明示値

相対パスは、その値を定義した YAML のディレクトリを基準に絶対化します。環境変数・
明示 overrides の相対値はユーザー設定ディレクトリが基準です。`base_dir` を渡す場合は
それを基準にします。標準 YAML は `importlib.resources` から読み込みます。
CLI の入力ファイルや明示した `--output` は、通常どおり呼び出し時の CWD が基準です。

## ファイルとパスの境界

| 種類 | 既定値/例 | 編集可否 |
| --- | --- | --- |
| 標準設定 | `yomiage/resources/config/{default,voices,scene_params,math_dict}.yaml` | 不可（wheel 内） |
| ユーザー設定 | `~/.config/humeina-unit/config.yaml` または `settings.yaml` | 可 |
| Batchプロファイル | `batch.voice_profile_dir` 等で指定したディレクトリ | 可 |
| runtime data | `~/.local/share/humeina-unit/`（`YOMIAGE_DATA_DIR`） | 可/バックアップ対象 |
| Batch output | `~/.local/share/humeina-unit/output/`（`YOMIAGE_OUTPUT_DIR` または `batch.output_dir`） | 可/成果物 |
| assets | `~/.local/share/humeina-unit/assets/`（`YOMIAGE_ASSETS_DIR`） | 可 |
| Job DB | `~/.local/share/humeina-unit/jobs.sqlite3`（`YOMIAGE_SQLITE_PATH`） | 可/バックアップ対象 |

`XDG_CONFIG_HOME` / `XDG_DATA_HOME` が指定されている場合は各 XDG ディレクトリを使います。
共有音声の既定はデータディレクトリ内の `voisona/` で、最終出力先とは独立します。

`.env` はユーザー設定ディレクトリから読み込み、プロセス環境変数を優先します。
任意の `.env` は `YOMIAGE_ENV_FILE=/absolute/path/to/.env` で指定します。
親ディレクトリやチェックアウトを暗黙に探索しません。

checkout の `config/` を開発用に使う場合は `load_config(config_dir=Path("config"))` または
`Settings.from_yaml("config", base_dir=...)` のように明示します。インストール後の標準値を
リポジトリ相対パスから解決しないでください。

## 主要な YAML セクション

### `tts`

```yaml
tts:
  primary_provider: voisona       # voisona / voicevox / voicepeak
  fallback_provider: voicevox     # null も可。primary と同じ値は禁止
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
    opencode_go:
      type: opencode_go
      base_url: https://opencode.ai/zen/go/v1
      api_key: ${OPENCODE_GO_API_KEY:-}
      model: ${OPENCODE_GO_MODEL:-qwen3.8-flash}
      protocol: auto
      enabled: false
  routes:
    scene_analysis: {provider: local, max_output_tokens: 4096}
    news_summary: {provider: local, max_output_tokens: 2048}
  fallback:
    order: [local]
    "on": [unavailable, timeout, rate_limit]

news:
  urgency_threshold: 0.8
  sources: [nhk_main, bbc_world, guardian_world]
  tts: {speed: 1.1}
```

Provider（接続先）とProtocol（`chat_completions` / `responses` / `messages`）は独立しています。
OpenCode Go はopt-inで、API keyは環境変数だけから渡します。本文、完全URL、API keyはmetrics、
manifest、Job DBへ保存されません。旧`ollama:`は`ollama_legacy`へ自動変換され、deprecation
warningを出します。

OpenCode Goへ小説、ニュース、Slack本文を送る前に、利用モデルのprivacy条件を確認してください。
標準fallbackはローカルだけで、有料Providerへ暗黙に切り替わりません。

### `batch`

```yaml
batch:
  output_dir: output
  default_mode: voisona
  analysis_window_chars: 3000
  analysis_window_sentences: 25
  analysis_strategy: single_pass
  manifest_save_interval: 10
  silence_duration: 1.5
  concat_format: wav
  cleanup_after_concat: false
  voisona_vm_mount: Z:  # 互換入力。新規設定は voisona.vm_mount
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
| `LLAMA_CPP_URL`, `LLAMA_CPP_MODEL` | 標準llama.cpp接続/model alias |
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
| `VOISONA_SHARED_DIR` | libvirt の source dir と一致する Linux 共有元 |
| `YOMIAGE_ENV_FILE` | 明示的に読み込む .env のパス |
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

### `analysis`

CLI・RESTのReader/Batchと、`PipelineConfig.from_dict()`は同じ分析設定を読む。
一般文章を外部LLMなしで読む例:

```yaml
analysis:
  content_kind: plain
  llm_policy: "off"
  common_dictionary: dictionaries/common.yaml
  work_dictionary: dictionaries/my-novel.yaml
  suggest_readings: false
  reading_proposals: dictionaries/proposed.yaml
```

相対パスはその設定ファイルの基準ディレクトリで解決する。辞書がない場合は空辞書として扱う。
設定を省略した旧構成・配布設定は`novel/full`。小説で未解決の話者だけ分析するなら
`novel/selective`、全対象に演出を付けるなら`novel/full`を明示する。
`batch.analysis_strategy: single_pass/exhaustive`は人物発見のパス数で、`llm_policy`とは別。
`exhaustive`の追加人物発見は`full`の場合だけ有効になる。

| フィールド | 既定値 | 意味 |
|---|---|---|
| `content_kind` | `novel` | `plain` / `novel` / `script` |
| `llm_policy` | `full` | `off` / `selective` / `full` |
| `common_dictionary`, `work_dictionary` | null | 確認済みYAML辞書 |
| `suggest_readings` | false | 未指定英字語句をLLM提案の対象にする |
| `reading_proposals` | null | 未確認提案のYAML保存先 |
| `context_tokens` | 32768 | モデルの文脈上限に合わせる |
| `context_safety_ratio` | 0.8 | 入力・出力合計の安全余裕 |
| `max_output_tokens` | 4096 | route上限と比較して小さい方を採用 |
| `max_items` | 25 | 一窓の対象数上限 |
| `max_requests` | 100 | 一回の窓分析での論理要求上限。物理要求予算は`llm.limits` |

辞書は大小文字を区別し、`before`・`after`を指定すると直前・直後の文字列が一致する箇所だけ適用する。
正規表現ではなく文字列の条件である。

```yaml
schema_version: 1
status: confirmed
entries:
  - id: takanashi
    surface: 小鳥遊
    reading: たかなし
    version: "1"
  - id: first-day
    surface: 一日
    reading: ついたち
    version: "1"
    after: に集合
```

更新は`PronunciationDictionary.save()`で検証後にatomic writeできる。単一更新元を前提とし、
複数プロセスの同時編集APIは提供しない。`status: proposed`は未確認候補で、確認済み辞書として
読み込むとエラーになる。内容を確認し、必要な項目を確認済み辞書へ移してから利用する。

以前の解析範囲へ戻す場合は`novel/full`を明示する。実装自体を旧版に戻す場合は旧Plan/runを使い、
schema 2のUtterancePlan・schema 3のBatch manifestを旧版へ読み込ませない。

合成音声cacheを起動間で共有するときは、Provider設定に`cache_version`を指定する。
例えば`voicevox.cache_version: "engine-0.25-bank-20260914"`のように、確認したengine・音源の版を
識別できる値を使い、音源更新時に変更する。Providerが版を提供せず設定もない場合、標準adapterは
インスタンス固有のcache識別子を使う。同じ起動内のcache再利用は可能だが、起動間の音声cacheは
再利用しない。読み辞書と保存済みPlanの再利用には影響しない。
