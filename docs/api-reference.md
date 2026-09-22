# REST API リファレンス

`yomiage serve` が起動する FastAPI アプリケーションの契約です。既定の bind は
`127.0.0.1:8030`（CLI の `--port` で変更可能）です。別ホストから使う場合は、
`--host 0.0.0.0 --api-token "$YOMIAGE_API_TOKEN"` のように明示的な token が必要です。

## 認証・入力制限

- token 未設定の loopback 起動では認証なしで利用できます。
- `api_token` が設定されると `/health` と `/health/live` を除く全 endpoint が
  `Authorization: Bearer <token>` を要求します。`/health/ready` も保護対象です。
- token は constant-time 比較され、ログ・metrics・error payload に出ません。
- request body は既定 1 MiB、合成 text は既定 100,000 文字、URL response は既定 10 MiB までです。
  job queue、active job 数、peer address 単位の rate limit も適用されます。
- `url` は共通 URL policy で検証されます。HTTP(S) 以外、userinfo、loopback/private/link-local/
  multicast/reserved 宛先、private redirect は既定拒否です。

エラーは次の形式で返ります（本文や secret を含めません）。

```json
{"error": {"code": "url_not_allowed", "message": "URL is not allowed"}}
```

主な status は `400`（URL policy）、`401`（Bearer 欠落）、`403`（token 不正）、
`409`（状態競合）、`413`（body/text 上限）、`422`（request schema validation）、
`429`（rate/queue 上限）、`502`（上流応答/合成失敗）、`503`（runtime/Provider 未準備）、
`504`（Provider timeout）です。Provider の URL、資格情報、元の error 本文は response に含めません。

## Health

### `GET /health`

プロセスが HTTP を受け付けているかを返します。

```json
{"status": "ok", "reading": false}
```

### `GET /health/live`

外部 Provider が停止していても成功する liveness probe です。

```json
{"status": "ok", "live": true}
```

### `GET /health/ready`

Provider、LLM、ffmpeg、storage、SQLite など runtime dependency の readiness を返します。
`status: "ready"` は 200、未準備なら 503 です。token 設定時は認証が必要です。
既定のhealth checkerでは、集約結果に加えて `operations` が
`immediate_playback`、`batch_audio`、`video`、`slack` ごとの `ready` と不足dependency名を
返します。batchのVM fileには `vm_mount`、Slackにはtoken設定と別に `slack_sdk` が必要です。
path、endpoint、credential、元のexception本文は含めません。

`operations` 部分の例:

```json
{
  "operations": {
    "immediate_playback": {"ready": true, "missing": []},
    "batch_audio": {"ready": false, "missing": ["provider"]},
    "video": {"ready": false, "missing": ["provider", "ffmpeg"]},
    "slack": {"ready": false, "missing": ["slack"]}
  }
}
```

## Reader 制御

### `GET /api/yomiage/status`

Reader の現在状態を返します（engine の実装状態に応じて chapter/chunk/provider などが含まれます）。

### `GET /api/yomiage/capabilities`

Firefox拡張などのクライアントが、設定済みProvider、本文上限、利用可能なReader機能を確認する
ためのendpointです。`providers`は接続確認結果ではなく、起動時に構成されたProvider名です。

### `POST /api/yomiage/read`

URL の読み上げを background job として開始します。

```json
{
  "url": "https://www.aozora.gr.jp/cards/000879/files/127_15260.html",
  "provider": "auto"
}
```

```json
{"status": "started", "url": "https://…", "provider": "auto", "job_id": "…"}
```

`provider`は`auto|voisona|voicevox`です。`auto`は設定されたprimary/fallbackを使い、明示指定は
そのProviderだけを使います。起動時に構成されていないProviderは`400`です。URLがpolicyに
違反する場合はjob/artifactを作成する前に`400`、既に読み上げ中なら`409`になります。

### `POST /api/yomiage/read-text`

ブラウザ内で抽出した本文を、URL Readerと同じNLP・発話計画・再生経路へ渡します。ログインが
必要なページやJavaScript描画ページ、選択範囲の読み上げに使います。

```json
{
  "text": "ブラウザから抽出した本文です。",
  "source_url": "https://example.com/article",
  "title": "記事タイトル",
  "source_kind": "article",
  "provider": "voicevox"
}
```

`source_kind`は`article|selection`です。本文はjob optionsやログへ保存せず、`max_text_chars`を
超えるとjob作成前に`413`を返します。`source_url`は出典・bookmark用であり、サーバーから再取得
しません。

### `GET /api/yomiage/jobs/{job_id}`

Readerを含む永続jobの`state`、進捗、サニタイズ済みerrorを返します。Firefox拡張は受付後の
Provider失敗もこのendpointで表示します。存在しないIDは`404`です。

### `POST /api/yomiage/pause`

```json
{"status": "paused"}
```

実行中でない場合は `409` です。

### `POST /api/yomiage/resume`

```json
{"status": "resumed"}
```

pause 中でない場合は `409` です。

### `POST /api/yomiage/stop`

```json
{"status": "stopped"}
```

## 即時合成

### `POST /api/yomiage/synthesize`

text を primary/fallback 設定で合成し、`audio/{output_format}` の binary を返します。

```json
{
  "text": "こんにちは",
  "voice_id": "47",
  "speed": 1.0,
  "pitch": 0.0,
  "volume": 0.0,
  "intonation": 1.0,
  "preset": "female_young",
  "emotion": "happy",
  "intensity": 0.5,
  "output_format": "wav"
}
```

| field | type | default | 備考 |
| --- | --- | --- | --- |
| `text` | string | 必須 | `max_text_chars` 以下 |
| `voice_id` | string/null | null | Provider に渡す ID（別 Provider へ再利用しない） |
| `speed` | float | 1.0 | `0 < speed <= 5` |
| `pitch` | float | 0.0 | ピッチ |
| `volume` | float | 0.0 | 音量 |
| `intonation` | float | 1.0 | `0 <= intonation <= 2` |
| `preset` | string/null | null | profile preset |
| `emotion` | string | `neutral` | 感情タグ |
| `intensity` | float | 0.5 | `0 <= intensity <= 1` |
| `output_format` | string | `wav` | 現在の即時合成は `wav` のみ |

response headers: `X-Audio-Duration`（秒）、`X-Audio-Format`。入力上限超過や admission 拒否時は
binary の代わりに JSON error を返します。

空文字・空白だけの `text`、上記範囲外の値、未知の `output_format` は Provider を呼ぶ前に
`422` です。Provider timeout は `504`、利用不能/rate limit は `503`、不正な上流応答と
その他の上流合成失敗は `502` です。

### `GET /api/yomiage/voices`

利用可能なボイスを返します。`provider=voisona|voicevox|voicepeak`（未指定時は primary、
fallback 名を指定した場合は fallback）を query で指定できます。

```json
{
  "engine": "voicevox",
  "voices": [{"id": "47", "name": "ナースロボ＿タイプＴ"}]
}
```

## Batch job

### `POST /api/yomiage/synthesize/batch`

URL を検証し、非同期 Batch job を受け付けます。

```json
{
  "url": "https://www.aozora.gr.jp/cards/...",
  "mode": "voicevox",
  "output_format": "wav",
  "video": false,
  "style": null,
  "allow_partial": false
}
```

`mode` は `voisona|voicevox|voicepeak`、`output_format` は `wav|mp3|flac` です。`allow_partial`
の既定は false です。

```json
{"job_id": "abc123", "status": "pending"}
```

### `GET /api/yomiage/synthesize/batch/{job_id}`

```json
{
  "id": "abc123",
  "url": "https://…",
  "mode": "voicevox",
  "status": "synthesizing",
  "percent": 45.0,
  "message": "音声を合成中です",
  "work_id": "…",
  "plan_id": "…",
  "run_id": "…",
  "allow_partial": false,
  "completion_status": null,
  "output_path": null,
  "result": null,
  "error": null
}
```

### `GET /api/yomiage/synthesize/batch/{job_id}/progress`

`text/event-stream` で `event: progress` を送ります。終端 status は `completed`,
`complete_with_warnings`, `partial`, `failed`, `cancelled` のいずれかです。
存在しない `job_id` は stream を開始する前に JSON の `404` を返します。

```text
event: progress
data: {"job_id":"abc123","status":"synthesizing","percent":45.0,...}
```

### partial semantics

`failed`/`pending`/欠損 WAV が一つでもある場合、完全成功 (`complete`) にはなりません。

- `allow_partial=false`（既定）: concat を拒否し、job は `failed`。通常の `full.wav` は生成しません。
- `allow_partial=true`: 検証済み utterance だけを結合し、job/result/manifest/SSE に `partial` を残します。
  partial run では動画生成を実行しません。
- すべて成功なら `completed`、明示 skip のみなら `complete_with_warnings` です。

この状態は `completion_status` と run の `artifact-manifest.json` を正として判定します。

## Metrics

### `GET /api/yomiage/metrics`

合成回数、error/fallback/cache、latency、queue、partial などの process-local counters と、
利用可能なら SQLite JobRepository から算出した `persistent_jobs` を返します。process counters は
再起動でリセットされます。本文、token、password、完全 URL は保存しません。

## News

### `POST /api/yomiage/news`

設定済み RSS を取得し、LLM で日次要約して読み上げます。

```json
{"status": "reading", "article_count": 15}
```

## 起動と curl

### local-only

```bash
uv run yomiage serve --host 127.0.0.1 --port 8030
curl http://127.0.0.1:8030/health
curl -X POST http://127.0.0.1:8030/api/yomiage/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text":"テスト読み上げです","output_format":"wav"}' \
  --output output.wav
```

### authenticated external bind

```bash
export YOMIAGE_API_TOKEN='replace-with-a-long-random-token'
uv run yomiage serve --host 0.0.0.0 --port 8030
curl -H "Authorization: Bearer $YOMIAGE_API_TOKEN" http://127.0.0.1:8030/api/yomiage/status
```

公開 bind で token を URL query、ログ、shell history、成果物へ書かないでください。TLS、proxy、
network ACL は別途設定が必要です。

## Python SDK

SDK の型、`TTSBridge`、`TextAnalyzer`、`Pipeline` の使用例は [Python SDK リファレンス](sdk-reference.md)
を参照してください。

### 共通発話計画と読み指定

`Pipeline.process()` / `stream()`は発話ごとに結果を返す。複数話者を含む本文を、最長セグメントの
設定で一括合成する処理は廃止した。`PipelineChunk.text`は表示用本文で、合成時だけ読み指定を適用する。
構築時の`AnalyzerConfig.analysis`でLLMを無効化できる。

```python
from yomiage.api.config import AnalyzerConfig, PipelineConfig, TTSEngineConfig
from yomiage.api.pipeline import Pipeline
from yomiage.nlp.text_processor import extract_html_document
from yomiage.settings import AnalysisSettings

config = PipelineConfig(
    tts=TTSEngineConfig(engine="voicevox", url="http://localhost:50021"),
    analyzer=AnalyzerConfig(analysis=AnalysisSettings(
        content_kind="plain", llm_policy="off",
    )),
)
document = extract_html_document("<ruby>小鳥遊<rt>たかなし</rt></ruby>さん")
async with Pipeline(config) as pipeline:
    chunks = await pipeline.process(document)
```

`TextDocument.plain(text).with_readings([ReadingSpan(start, end, surface, reading)])`で
出現箇所への明示修正を指定できる。位置は正規化前の原文を基準にする。
`TextAnalyzer.plan()`・`UtterancePlanner.from_text()`も同じ文書を受け取る。
低水準の`TTSBridge.synthesize()`とREST即時合成は、呼び出し元が準備した合成用本文を受け取る契約を維持する。

`analysis.llm_policy: off`のREST読み上げはLLMを構築せず、readinessのLLM項目は
`healthy: true` / `status: disabled`になる。ニュースの要約・翻訳には別途LLM設定が必要。
