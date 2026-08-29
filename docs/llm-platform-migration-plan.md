# LLM基盤刷新・llama.cpp移行計画

> 状態: 完遂（ソフトウェア移行・ROCm実機検証完了）
> 作成日: 2026-08-29
> 実装日: 2026-08-29
> 対象: OpenCode Go、llama.cpp、Ollama互換移行、LLM利用コスト最適化

## 1. 目的

本計画は、現在のOllama直接依存を解消し、次のローカル・クラウドLLMを同じアプリケーション
境界から利用できるようにするための実装方針を定める。

- ローカル推論の標準実行基盤をOllamaから`llama-server`へ移行する。
- OpenCode Goが提供する3種類のAPI形式をすべて扱う。
  - OpenAI Chat Completions
  - OpenAI Responses
  - Anthropic Messages
- Reader、Batch、News、Slack、EX Voiceなどから具象LLMクライアントへの直接依存を除去する。
- TTS向け文字数分割とLLM向けトークン分割を分離する。
- リクエスト回数、入力重複、出力トークン、再試行、推定コストを計測・最適化する。
- 外部LLMが利用できない場合も、ローカルLLMまたはルールベース処理へ安全に縮退する。

本計画は旧文書`docs/refactor-ollama-to-llamacpp.md`を置き換える。旧文書の専用
`LlamaCppClient`追加案は採用せず、API形式別の共通Transportをllama.cppとOpenCode Goで
共有する。

## 2. 決定事項

### 2.1 ProviderとAPI形式を分離する

`openai`や`anthropic`をProvider名として扱わない。Providerは接続先・認証・モデル供給元を表し、
API形式は独立したTransportとして扱う。

```text
Provider
  llama_cpp
  opencode_go
  ollama_legacy
  gemini

Protocol
  chat_completions
  responses
  messages
  ollama_chat（移行期間のみ）
```

これにより、同じChat Completions Transportを`llama-server`とOpenCode Goの双方で利用できる。

### 2.2 llama.cppを標準ローカルProviderにする

新規設定の既定ローカルProviderを`llama_cpp`とする。Ollamaは移行期間中のみ互換Adapterとして
残し、機能コードからの`OllamaClient`直接利用は禁止する。

### 2.3 OpenCode CLIは実行経路に含めない

アプリケーションは`opencode run`や`opencode serve`を推論ごとに起動しない。OpenCode Goの
APIキーを明示的な環境変数から受け取り、公開HTTP APIへ直接接続する。

### 2.4 固定文字数ではなくトークン予算で分割する

`3,000文字`、`25文`などの固定値をモデル制約として扱わない。入力トークン、予想出力トークン、
モデルのcontext上限、安全余白からリクエスト単位を決める。

### 2.5 有料APIテストは明示的に有効化する

通常のpytestはローカルHTTPモックのみを使用する。OpenCode Goへの実リクエストは
`OPENCODE_GO_API_KEY`と専用マーカーが指定された場合だけ実行する。

## 3. 現状

### 3.1 既存の抽象化

`src/yomiage/nlp/llm_backend.py`には次の型が存在する。

- `LLMBackend`
- `OllamaBackend`
- `OpenAIBackend`
- `AnthropicBackend`
- `create_llm_backend()`

ただし、現在の実行経路はこの抽象化へ統一されていない。

### 3.2 Ollama直接依存

主な直接依存は次のとおりである。

| 対象 | 現在の用途 |
|---|---|
| `nlp/ollama_client.py` | `/api/chat`、`/api/tags`、romanize |
| `nlp/scene_analyzer.py` | `OllamaClient`の後方互換受け入れ |
| `batch/engine.py` | `OllamaClient`生成、文単位romanize |
| `batch/analyzer.py` | 登場人物発見・詳細分析・ボイス分類 |
| `news/summarizer.py` | 要約・翻訳 |
| `news/urgency.py` | 緊急度判定 |
| `news/scheduler.py` | LLM生成物の統合とromanize |
| `slack/scorer.py` | メッセージ重要度判定 |
| `interfaces/cli/composition.py` | Reader、News、Slack用クライアント生成 |
| `bootstrap.py` | Reader用クライアント生成 |
| `server.py` | News API用クライアント生成 |

### 3.3 現在のバッチLLM呼び出し

```text
本文
 ├─ 3,000文字ごとの登場人物発見
 ├─ 25文ごとの詳細分析
 ├─ 登場人物一覧のボイス分類
 └─ 英字を含む文ごとの個別ローマ字化
```

本文10万文字、2,000文、英字入り100文を仮定すると、現在の概算は次のようになる。

| 処理 | リクエスト数 |
|---|---:|
| 登場人物発見 | 34 |
| 詳細分析 | 80 |
| ボイス分類 | 1 |
| ローマ字化 | 最大100 |
| 合計 | 最大215 + 可用性確認 |

本文が登場人物発見と詳細分析でほぼ二重送信されること、romanizeが文単位であることが主要な
非効率である。

### 3.4 現在のOllama context設定

`OllamaClient`は`num_predict`を指定するが、`num_ctx`を指定していない。そのため実行時contextは
OllamaサーバーまたはModelfileの設定に依存する。現在の`3,000文字／25文`は4K contextを想定した
保守的な値と考えられるが、アプリケーションは実際のcontext長を検出していない。

## 4. 対象APIとProvider

### 4.1 llama-server

現在の`llama-server`は次のAPIを提供する。

- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/messages`
- `POST /v1/chat/completions/input_tokens`
- `POST /v1/responses/input_tokens`
- `POST /v1/messages/count_tokens`
- `GET /health`
- `GET /props`
- `GET /metrics`（`--metrics`指定時）

Chat CompletionsではJSON objectおよびJSON Schemaによる制約生成を利用できる。ローカル分析の
標準Protocolは、構造化出力が必要なため`chat_completions`とする。他の2形式も共通Transportの
契約テスト対象とする。

公式仕様:

- <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>
- <https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md>
- <https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md>

### 4.2 OpenCode Go

OpenCode GoではモデルごとにAPI形式が異なる。モデルカタログで既知の形式を自動解決し、設定で
明示的に上書きできるようにする。

```text
protocol: auto | chat_completions | responses | messages
```

モデル一覧は変更されるため、固定したモデル名だけで設計しない。起動時またはテスト時に
`GET https://opencode.ai/zen/go/v1/models`で存在確認を行う。

公式仕様:

- <https://opencode.ai/docs/go/>
- <https://opencode.ai/zen/go/v1/models>

### 4.3 Ollama

移行期間中は`ollama_chat` Transportを残す。ただし以下は禁止する。

- 機能クラスが`OllamaClient`を型として受け取ること。
- 機能クラスが`config["ollama"]`を直接読むこと。
- 機能クラスが生成前に毎回`is_available()`を呼ぶこと。

### 4.4 2026-08-29モデル選定結果

実装時に固定した過去知識ではなく、次の公式online情報を再確認した。

- Qwen公式repositoryではQwen3.8を最新世代とし、`Qwen3.8-27B`を2026-08-14公開の
  open-weight modelとして掲載している。
- ggml-org公式GGUFはQwen3.8-27Bの量子化を提供している。16GB実機用にはHugging Faceの
  `bartowski/Qwen3.8-27B-GGUF`にある`IQ4_XS`（15.57GB、モデルカードで推奨）を選定した。
- 同日公開のGLM-5.3-Flashは321Bであり、この16GBローカル用途の候補から除外した。
- OpenCode Go公式catalogは`qwen3.8-max`と`qwen3.8-flash`をMessages APIで提供している。

このため、新規ローカル既定model IDを`Qwen3.8-27B`、16GB ROCm実機のGGUFをIQ4_XS、
OpenCode Goの低コストMessages smoke候補を`qwen3.8-flash`とした。Qwen3.5は
旧`ollama:`設定の移行例にだけ残す。

参照:

- <https://github.com/QwenLM/Qwen3.8>
- <https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF>
- <https://huggingface.co/bartowski/Qwen3.8-27B-GGUF>
- <https://huggingface.co/zai-org/GLM-5.3-Flash>
- <https://opencode.ai/docs/go/>

## 5. 目標アーキテクチャ

```text
Reader / Batch / News / Slack / EX Voice / Public API
                         │
                         ▼
                    LLMService
        ┌─────────────────────────────────┐
        │ Task Router                     │
        │ Token Budget Planner            │
        │ Structured Output Validator     │
        │ Cache / Deduplication            │
        │ Retry / Rate Limit / Circuit     │
        │ Usage / Cost Metrics             │
        └─────────────────────────────────┘
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
       Chat Transport  Responses   Messages
             │           │           │
             ├──── llama-server ─────┤
             └──── OpenCode Go ──────┘
                         │
                  Ollama legacy adapter
```

## 6. 新しいコード境界

新規パッケージ`src/yomiage/llm/`へ責務を分離する。

```text
src/yomiage/llm/
├── models.py          # LLMRequest / LLMResponse / LLMUsage / error
├── protocols.py       # Protocol列挙と共通Transport契約
├── service.py         # ルーティング、fallback、retry、metrics
├── budgeting.py       # token予算、batch分割、context超過時の二分
├── cache.py           # text/JSON/romanizeキャッシュ
├── catalog.py         # OpenCode Goモデル→Protocol対応
├── factory.py         # 設定からProvider/Serviceを構築
└── transports/
    ├── chat_completions.py
    ├── responses.py
    ├── messages.py
    ├── ollama.py
    └── gemini.py
```

`src/yomiage/nlp/llm_backend.py`は一時的な互換re-exportとし、新規機能は`yomiage.llm`を参照する。

### 6.1 リクエスト契約

```python
@dataclass(frozen=True)
class LLMRequest:
    task: str
    prompt: str
    system: str | None
    response_mode: Literal["text", "json"]
    json_schema: dict | None
    temperature: float
    max_output_tokens: int
```

### 6.2 レスポンス契約

```python
@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    protocol: str
    usage: LLMUsage
    latency_ms: float
    retry_count: int
```

並行リクエストに対応するため、`last_usage`のようなBackend可変状態は持たない。

## 7. 設定設計

新しい標準設定は次の形とする。

```yaml
llm:
  providers:
    local:
      type: llama_cpp
      base_url: ${LLAMA_CPP_URL:-http://127.0.0.1:8080/v1}
      api_key: ${LLAMA_CPP_API_KEY:-}
      model: ${LLAMA_CPP_MODEL:-Qwen3.8-27B}
      protocol: chat_completions
      context_tokens: 32768
      timeout_seconds: 600

    opencode_go:
      type: opencode_go
      base_url: https://opencode.ai/zen/go/v1
      api_key: ${OPENCODE_GO_API_KEY:-}
      protocol: auto

  routes:
    scene_analysis:
      provider: local
      max_output_tokens: 4096

    character_analysis:
      provider: local
      max_output_tokens: 4096

    romanization:
      provider: local
      max_output_tokens: 1024

    news_summary:
      provider: local
      max_output_tokens: 2048

    urgency_scoring:
      provider: local
      max_output_tokens: 32

  fallback:
    order: [local, opencode_go]
    on: [unavailable, timeout, rate_limit]

  limits:
    context_safety_ratio: 0.80
    availability_ttl_seconds: 60
    max_retries: 2
    max_requests_per_job: 100
    max_input_tokens_per_job: 500000
    on_budget_exceeded: rule_based
```

実際のモデル選択とfallback順序は利用者設定で変更できるようにする。APIキーは`.env`または
プロセス環境だけから読み込み、YAML、ログ、Job DB、manifestへ保存しない。

### 7.1 旧設定の移行

```yaml
ollama:
  url: http://localhost:11434
  model: qwen3.5:9b
```

旧`ollama:`はロード時に次へ変換する。

```yaml
llm:
  providers:
    local:
      type: ollama_legacy
      base_url: http://localhost:11434
      model: qwen3.5:9b
```

移行警告は秘密情報を含めず、一度だけ出力する。

## 8. llama.cpp実行計画

### 8.1 初期プロファイル

実機ベンチマーク前の開始点を次とする。

```bash
llama-server \
  --model /models/Qwen3.8-27B-IQ4_XS.gguf \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 32768 \
  --parallel 1 \
  --device ROCm0 \
  --split-mode none \
  --main-gpu 0 \
  --n-gpu-layers auto \
  --fit on \
  --fit-target 2048 \
  --flash-attn on \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --jinja \
  --reasoning off \
  --metrics
```

話者・scene・romanize・採点は短い構造化出力が目的なので、ローカル既定ではthinkingを無効にする。
Qwen3.8の既定templateは`xhigh` reasoningであり、無効化しないと小さい出力予算をthinkingだけで
消費して本文が空になり得る。推論が必要な別taskでは`LLAMA_REASONING=on`で明示的に切り替える。

ROCmではこのcommandの前に`ROCR_VISIBLE_DEVICES`、`HIP_VISIBLE_DEVICES`、
`GPU_DEVICE_ORDINAL`を同じ明示indexへ設定する。`infra/llama-server-rocm.sh`はindex未指定時に
起動を拒否する。これは確定した性能設定ではない。次を計測して32K、64K、それ以上を比較する。

- モデルとKV cacheがGPUへ収まっているか。
- prompt tokens/second。
- generation tokens/second。
- 25、50、100文でのJSON完全率。
- 最大VRAM使用量。
- 並列数1と2のthroughput、1リクエストあたりlatency。

### 8.2 KV cache

初期値はK/Vとも`q8_0`とする。品質劣化が確認された場合はVまたは両方を`f16`へ戻す。VRAMが
不足する場合だけ`q4_0`を評価する。モデルごとに最適値が異なるため、設定をコードへ固定しない。

### 8.3 contextとparallel slot

LLM分析は長文バッチのlatencyを優先し、初期値を`--parallel 1`とする。複数ジョブを並列化する
場合は、slot数だけでなくslotごとのcontext上限と合計KV poolを測定する。

### 8.4 Docker

実機のGPU構成が確定後、`infra/docker-compose.yml`へ任意の`llama-server`サービスを追加する。
CPU、CUDA、ROCm/Vulkanを一つの固定Composeへ混在させず、profileまたはoverride fileで分離する。

## 9. コスト・リクエスト最適化

### 9.1 バッチ解析の原則1パス化

詳細分析のレスポンスへ新規キャラクター情報を含め、本文全体を先に登場人物発見へ送る既定動作を
廃止する。

```json
{
  "id": 10,
  "speaker": "太郎",
  "scene": "daily",
  "emotion": "happy",
  "intensity": 0.7,
  "new_character": {
    "name": "太郎",
    "gender": "male",
    "age_group": "young_adult"
  }
}
```

未知話者率、JSON欠落率、alias競合などが閾値を超えた部分だけ追加解析する。品質比較のため、明示的な
`exhaustive`モードでは旧2パス相当を選択可能にする。

### 9.2 ボイス分類の決定論化

`gender`と`age_group`から既存アーキタイプを決定できる場合はLLMを呼ばない。情報が不足する
キャラクターだけを一括問い合わせする。

### 9.3 動的バッチサイズ

リクエスト単位は次の小さい方で決める。

1. 入力トークン予算。
2. 予想JSON出力トークン予算。
3. 設定された最大項目数。

llama.cppでは各APIのtoken counting endpointを利用する。count endpointがないProviderでは、
日本語・ASCIIを分けた保守的な推定器を使用する。context超過が返った場合だけバッチを二分して
再実行する。

### 9.4 ローマ字化の集約

文全体を1文ずつ送らない。

1. アルファベット語句・略語だけを抽出する。
2. 同じ語句を作品内で重複排除する。
3. 静的辞書と永続キャッシュを先に参照する。
4. 未知語だけをID付きJSON配列でまとめて問い合わせる。
5. 変換結果を元の文章へローカルで戻す。

曖昧な語句だけ短い前後文脈を追加し、本文全体は送らない。

### 9.5 News

速報処理の重要度判定、必要な翻訳、読み上げ用整形を一つの構造化リクエストへまとめる。日次要約は
記事タイトル・要約の重複を除去し、既に日本語の記事を翻訳対象にしない。

### 9.6 Slack

ルールベース得点だけで結果が確定する場合はLLMを呼ばない。LLM補正により通知閾値を跨ぐ可能性が
ある範囲だけ問い合わせる。

### 9.7 EX Voice

旧Readerチャンク窓の重複問い合わせを廃止する。計画済みのscene/emotionを用いた候補抽出を先に
行い、LLMは章または非重複窓ごとに最大1回とする。

### 9.8 JSON再試行

構造化出力は次の順で処理する。

1. ProviderのJSON Schema機能を利用する。
2. ローカルでJSON抽出・schema validationを行う。
3. 有効なIDは採用する。
4. 欠落・不正なIDだけを再問い合わせする。
5. 再試行上限後はルールベース結果へ縮退する。

本文全体を同じプロンプトで再送しない。

## 10. 可用性・再試行・fallback

### 10.1 可用性確認

機能コードは生成前に`is_available()`を呼ばない。`LLMService`が次を管理する。

- 初回または起動時のhealth probe。
- 60秒を初期値とするTTL cache。
- 連続失敗時のCircuit Breaker。
- 成功した生成によるhealth状態の回復。

### 10.2 再試行

| エラー | 方針 |
|---|---|
| timeout、接続失敗 | 指数backoff後に再試行 |
| 429 | `Retry-After`を尊重 |
| 500、502、503、504 | 上限付き再試行 |
| 400、404 | 設定・request不正として再試行しない |
| 401、403 | 認証・権限エラーとして再試行しない |
| context超過 | 同じProviderでバッチを二分 |
| JSON一部不正 | 不正IDだけ再問い合わせ |

### 10.3 fallback

fallbackはタスク設定に従い、暗黙に高価なモデルへ切り替えない。Jobごとのrequest/token budgetを
超えた場合は`rule_based`、`stop`、`fallback_local`のいずれかを明示的に選ぶ。

## 11. 観測性

既存`MetricsCollector`へ、本文やプロンプトを保持しないLLMメトリクスを追加する。

- request count
- success/error count
- retry count
- fallback count
- input tokens
- output tokens
- cached input tokens
- latency total/max
- JSON validation failure count
- context split count
- cache hit/miss
- 推定コストまたはOpenCode Go利用枠消費量

ラベルは次だけに限定する。

- task
- provider
- model
- protocol
- error category

APIキー、本文、完全URL、エラー本文は保存しない。

llama.cppの`--metrics`出力も利用し、アプリ側メトリクスと次を突き合わせる。

- `llamacpp:prompt_tokens_total`
- `llamacpp:tokens_predicted_total`
- `llamacpp:requests_processing`
- `llamacpp:requests_deferred`
- `llamacpp:n_tokens_max`

## 12. 実装フェーズ

### Phase 0: ベースラインと契約

作業:

- 現在のLLM呼び出し数をfixtureで固定する。
- `LLMRequest`、`LLMResponse`、`LLMUsage`、エラー分類を追加する。
- 既存644件のテストをベースラインとして保存する。
- 既存の未close `aiohttp.ClientSession`警告の発生元を特定する。

終了条件:

- 既存動作を変えず、新旧APIを並行利用できる。

### Phase 1: 3 Protocol Transport

作業:

- Chat Completions Transport。
- Responses Transport。
- Messages Transport。
- 共通認証、timeout、usage parsing、エラー分類。
- ローカルfake HTTP serverを使った契約テスト。

終了条件:

- 3形式すべてでtext、JSON、usage、代表エラーを正規化できる。

### Phase 2: llama.cpp Provider

作業:

- `llama_cpp` Provider設定。
- `/health`、token counting、JSON Schema、metrics連携。
- `llama-server`起動例とDocker profile。
- 32K contextの実機ベンチマーク。

終了条件:

- llama.cpp上で3形式のcontract testが通る。
- Chat Completionsで構造化分析が通る。

### Phase 3: Composition統一

作業:

- CLI、bootstrap、serverで`LLMService`を一度だけ構築する。
- Reader、Batch、News、Slack、EX Voiceへ注入する。
- public APIの`TextAnalyzer`、`Pipeline`も同じfactoryへ統一する。
- Gemini fallbackを共通Adapterへ移す。

終了条件:

- `OllamaClient`の参照が互換Adapterと互換テスト以外に存在しない。
- `config.get("ollama")`が設定移行処理以外に存在しない。

### Phase 4: OpenCode Go Provider

作業:

- APIキーとbase URL設定。
- モデル→Protocol catalog。
- `protocol: auto`と明示override。
- 429、利用上限、認証エラー分類。
- Privacy上の注意を設定ドキュメントへ追加する。

終了条件:

- モックで全モデル形式のroutingが通る。
- 明示的E2Eで3形式の最小リクエストが通る。

### Phase 5: コスト最適化

作業:

- Batchの原則1パス化。
- 動的token batching。
- voice archetypeの決定論化。
- romanizeの語句抽出、重複排除、cache。
- Newsの統合リクエスト。
- Slackの不確実範囲だけLLM利用。
- JSON部分再試行。

終了条件:

- 標準Batchで本文送信量が概ね1パス相当になる。
- romanizeが文単位リクエストにならない。
- 固定`3,000文字／25文`がモデル制約として使用されない。

### Phase 6: Ollama撤去準備

作業:

- `ollama:`設定利用時のdeprecation warning。
- `nlp/ollama_client.py`を互換shimへ縮小する。
- llama.cpp移行手順とrollback手順を完成させる。
- 互換期間終了後の削除対象を列挙する。

終了条件:

- 新規環境がOllamaなしでReader、Batch、News、Slackを起動できる。

## 13. テスト計画

### 13.1 課金なしテスト

- Transportごとのrequest URL、header、body。
- 3形式のresponse text抽出。
- 3形式のusage正規化。
- JSON Schemaとfallback parser。
- timeout、401、403、429、5xx。
- `Retry-After`。
- Circuit Breakerとavailability TTL。
- context超過時の二分。
- 欠落IDだけの再試行。
- 設定secretのmask。

### 13.2 呼び出し回数回帰テスト

`CountingLLM` fakeを利用し、次を直接assertする。

- Batch本文が通常モードで二重送信されない。
- romanizeが同じ英単語を複数回送らない。
- Slackの確定ケースでLLM呼び出しが0回である。
- availability probeがTTL内で1回以下である。
- News速報が最大1回の主要生成で完結する。

### 13.3 品質fixture

- 話者aliasを含む小説。
- 長い会話文。
- 章途中で登場する新キャラクター。
- 地の文・会話・心の声・場面転換の混在。
- 英字略語、固有名詞、数式を含む文章。
- 一部IDが欠落したLLMレスポンス。

単一パスと旧2パス相当の結果を比較し、話者・scene・emotion・新規キャラクター検出の回帰を測る。

### 13.4 llama.cpp実機テスト

- 3 Protocol smoke test。
- 25、50、100文のJSON完全率。
- 32K、64K context。
- KV cache f16、q8_0、必要時q4_0。
- parallel 1、2。
- GPU offload率、VRAM、prompt/generation throughput。

### 13.5 OpenCode Go実APIテスト

通常CIではskipする。

| Protocol | 初期候補 | 備考 |
|---|---|---|
| Chat Completions | `mimo-v2.5` | 廉価な合成データsmoke候補 |
| Chat代替 | `glm-5.3-flash` | availability/品質比較用 |
| Responses | `gpt-5.6-luna` | 学習利用を避ける初期候補 |
| Responses最安候補 | `muse-spark-1.2-contributor` | 合成データ限定、地域・学習条件に注意 |
| Messages | `qwen3.8-flash` | 廉価なMessages候補 |

テストは`max_output_tokens`を最小にし、本文・Slack・ニュースなどの実データを送信しない。

## 14. 受入条件

- Chat Completions、Responses、Messagesの全形式に対応している。
- llama.cppとOpenCode Goが同じProtocol Transportを共有している。
- llama.cppが標準ローカルProviderとして利用できる。
- `OllamaClient`直接参照が互換層以外にない。
- `config.get("ollama")`が移行層以外にない。
- Featureコードが生成前に直接`is_available()`を呼ばない。
- TTS文字数制限とLLMトークン制限が独立している。
- Batchの通常モードで本文送信量が概ね1パス相当である。
- romanizeが文単位リクエストにならない。
- provider/model/protocol/task別のrequest、token、retry、latencyが観測できる。
- APIキーと本文がログ、metrics、manifest、Job DBへ保存されない。
- 既存テスト、新規テスト、Ruffが成功する。
- OpenCode Go実APIテストは明示的な環境でのみ実行される。

### 14.1 実装結果

ソフトウェア受入条件は次の実装と課金なしtestで満たした。

- `yomiage.llm`にrequest/response/usage/error契約、3 Protocol Transport、Ollama/Gemini
  Adapter、task router、retry/fallback/circuit、TTL health、hash-key LRU cache、token budgetを追加。
- CLI、bootstrap、server、Reader、Batch、News、Slack、EX Voiceを`LLMBackend` / `LLMService`注入へ
  統一。`OllamaClient`は互換moduleと互換test以外のfeatureから除去。
- Batch通常modeを1-pass化し、token予算分割、context超過時の二分、欠落IDだけの再問い合わせ、
  決定論的archetype、英字語句の一括重複排除を実装。
- `MetricsCollector`へtask/provider/model/protocol別request、success/error、retry、fallback、token、
  latency、cache、JSON検証失敗を追加。本文、URL、credentialは受け取らない。
- `infra/llama-server-rocm.sh`とComposeの`rocm` profileはGPU indexを必須とし、指定GPU以外を
  ROCm runtimeから隠す。
- fake HTTP serverの3 Protocol contract、429分類、retry/fallback、TTL、1-pass送信量、partial
  retry、romanize重複排除、Slack 0-callを通常testへ追加。
- OpenCode Goの3 Protocol実API testは`OPENCODE_GO_API_KEY`と`RUN_PAID_LLM_TESTS=1`の両方、
  llama.cpp実機testは`RUN_LLAMA_CPP_E2E=1`と`YOMIAGE_LLM_GPU_DEVICE`を要求する。

実Provider testは外部状態を変更するため通常testでは実行しない。2026-08-29にRX 6900 XT上で
明示opt-in実機testを行い、次を確認した。

| profile | 結果 |
|---|---|
| 32K、q8 K/V、parallel 1 | `n_ctx=32768`、VRAM 15.13GB、生成約13.35 tokens/s |
| 64K、q8 K/V、parallel 1 | `n_ctx=65536`、VRAM 14.84GB、短文生成約10 tokens/s |
| 3 Protocol smoke | 32K、64KともChat/Responses/Messages各3件すべて成功 |
| JSON完全率 | 25/50/100文すべて全IDを欠落なく返却。100文は1493 tokens、112.02秒 |

Qwen3.8の既定thinkingが短い出力予算を消費する問題を実測で発見し、`--reasoning off`を標準化した。
100文が従来の120秒timeout付近になるため、ローカルprovider timeoutは600秒とした。通常運用は
throughputの高い32Kを既定とし、64Kは長文Jobで明示的に選ぶ。OpenCode Go実APIはAPI key未設定の
ため課金なしcontract testまでとし、有料testは引き続き二重opt-inでのみ実行する。

再実行command:

```bash
YOMIAGE_LLM_GPU_DEVICE=0 \
LLAMA_MODEL_PATH=/absolute/path/Qwen3.8-27B-IQ4_XS.gguf \
infra/llama-server-rocm.sh

RUN_LLAMA_CPP_E2E=1 YOMIAGE_LLM_GPU_DEVICE=0 \
uv run python -m pytest -q tests/e2e/test_llm_real.py -k llama_cpp

RUN_LLAMA_CPP_QUALITY_E2E=1 YOMIAGE_LLM_GPU_DEVICE=0 \
uv run python -m pytest -q tests/e2e/test_llm_real.py -k json_completeness
```

## 15. Rolloutとrollback

### Rollout

1. 共通Transportを追加するが、既定Providerは変更しない。
2. llama.cppを任意Providerとして追加する。
3. fixtureと実機ベンチマークを通す。
4. 新規インストールの既定をllama.cppへ変更する。
5. 既存利用者には旧`ollama:`設定を自動移行する。
6. OpenCode Goはopt-in Providerとして追加する。
7. コスト最適化を機能単位で有効化する。

### Rollback

- `llm.routes`のProviderを`ollama_legacy`へ戻す。
- Batchの`analysis_strategy`を`exhaustive`へ戻す。
- 動的batchingを無効化し、安全なtoken上限へ固定する。
- OpenCode Go fallbackを無効化する。

artifact、manifest、UtterancePlanの形式はProvider非依存とし、Provider変更だけで既存成果物を読める
状態を維持する。

## 16. リスク

| リスク | 対策 |
|---|---|
| 単一パス化によるキャラクター検出低下 | 品質fixture、未知話者時だけ追加解析 |
| 長いJSONの途中切れ | 出力token予算、schema制約、欠落ID再試行 |
| llama.cpp長contextでVRAM不足 | 32K開始、q8 KV、parallel 1、metrics計測 |
| KV量子化による品質低下 | f16/q8/q4比較、モデル別設定 |
| OpenCode Goモデル一覧・Protocol変更 | catalog更新、明示override、`/models`確認 |
| 有料fallbackの意図しない利用 | task別明示設定、Job budget、opt-in |
| ProviderごとのAPI互換差 | 共通contract test、未知フィールドを前提にしないparser |
| aiohttp session leak | session所有権をServiceへ集約し`close()`を契約化 |
| 秘密情報漏洩 | SecretStr、限定metrics label、request body非保存 |

## 17. 実機情報と測定前提

2026-08-29に取得した実行host情報:

- GPU 0: AMD Radeon RX 6900 XT、gfx1030、VRAM 16GB（LLM対象）。
- GPU 1: AMD Raphael iGPU、gfx1036、VRAM 512MB（LLM対象外）。
- system RAM: 61GiB、swap 63GiB。
- filesystem空き: 約993GB。
- `rocm-smi` / `rocminfo`あり。llama.cppをROCm/gfx1030向けにbuildし、IQ4_XS GGUFを配置済み。
- `OPENCODE_GO_API_KEY`未設定。

測定結果:

- GGUF: `Qwen3.8-27B-IQ4_XS.gguf`、15,567,824,480 bytes。
- llama.cpp build: `b1-cc83d7b`、ROCm device `ROCm0`。
- 32K/64KのVRAM、throughput、3 Protocol、JSON完全率は14.1の表を参照。
- 既定同時Job数は`parallel 1`。parallel 2は一件latencyを悪化させるため初期既定にしない。

GPU誤選択によるstallを避けるため、model配置前後を問わずGPU自動選択では起動しない。
`--ctx-size`やKV cache形式はコードへ固定せず、script/Composeの変更可能な開始値として扱う。
