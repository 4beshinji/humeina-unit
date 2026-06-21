# REST API リファレンス

FastAPIサーバー（`yomiage serve`）が提供するエンドポイント。

デフォルト: `http://0.0.0.0:8030`

## ヘルスチェック

### `GET /health`

```json
{
  "status": "ok",
  "reading": false
}
```

## 読み上げ制御

### `GET /api/yomiage/status`

現在の読み上げ状態を返す。

```json
{
  "running": true,
  "paused": false,
  "chapter": "第一章",
  "chunk": 42,
  "total_chunks": 150
}
```

### `POST /api/yomiage/read`

URLの読み上げを開始する。

リクエスト:
```json
{
  "url": "https://www.aozora.gr.jp/cards/...",
  "provider": "voisona"
}
```

レスポンス:
```json
{
  "status": "started",
  "url": "https://..."
}
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `url` | string | はい | 読み上げ対象URL |
| `provider` | string | いいえ | TTSプロバイダー（`voisona` / `voicevox` / `voicepeak`） |

エラー:
- `409`: 既に読み上げ中
- `503`: エンジン未初期化

### `POST /api/yomiage/pause`

読み上げを一時停止する。

```json
{"status": "paused"}
```

### `POST /api/yomiage/resume`

一時停止中の読み上げを再開する。

```json
{"status": "resumed"}
```

### `POST /api/yomiage/stop`

読み上げを停止する。

```json
{"status": "stopped"}
```

## 音声合成

### `POST /api/yomiage/synthesize`

テキストを即座に合成し、音声ファイルを返す。

リクエスト:
```json
{
  "text": "こんにちは",
  "voice_id": "4",
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

レスポンス: `audio/wav` などの音声バイナリ（`StreamingResponse`）。

HTTP ヘッダ:
- `X-Audio-Duration`: 音声の長さ（秒）
- `X-Audio-Format`: フォーマット（`wav` / `mp3` など）

| フィールド | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `text` | string | — | 合成するテキスト |
| `voice_id` | string | `null` | ボイスID |
| `speed` | float | `1.0` | 話速 |
| `pitch` | float | `0.0` | ピッチ |
| `volume` | float | `0.0` | 音量 |
| `intonation` | float | `1.0` | イントネーション |
| `preset` | string | `null` | VoiceProfile プリセット |
| `emotion` | string | `"neutral"` | 感情タグ |
| `intensity` | float | `0.5` | 感情の強度 |
| `output_format` | string | `"wav"` | 出力フォーマット |

### `GET /api/yomiage/voices`

利用可能なボイス一覧を返す。

クエリパラメータ:
- `provider`: `voisona` / `voicevox` / `voicepeak`（未指定時は primary）

レスポンス:
```json
{
  "engine": "voicevox",
  "voices": [
    {"id": "4", "name": "ナースロボ＿タイプＴ"}
  ]
}
```

### `POST /api/yomiage/synthesize/batch`

URL を対象にバッチ合成ジョブを開始する。

リクエスト:
```json
{
  "url": "https://www.aozora.gr.jp/cards/...",
  "mode": "voicevox",
  "output_format": "wav",
  "video": false,
  "style": null
}
```

レスポンス:
```json
{
  "job_id": "abc123",
  "status": "pending"
}
```

### `GET /api/yomiage/synthesize/batch/{job_id}`

ジョブの状態を取得する。

レスポンス:
```json
{
  "id": "abc123",
  "url": "https://...",
  "mode": "voicevox",
  "status": "synthesizing",
  "percent": 45.0,
  "message": "音声を合成中です",
  "output_path": null,
  "error": null
}
```

### `GET /api/yomiage/synthesize/batch/{job_id}/progress`

SSE でジョブ進捗を配信する。

```text
event: progress
data: {"job_id":"abc123","status":"synthesizing","percent":45.0,...}
```

## メトリクス

### `GET /api/yomiage/metrics`

TTS 合成・バッチジョブの実行メトリクスを返す。

レスポンス:
```json
{
  "synthesis": {
    "total": 120,
    "errors": 2,
    "error_rate": 0.0167,
    "cache_hits": 80,
    "cache_misses": 40,
    "cache_hit_rate": 0.6667,
    "average_duration_ms": 145.2,
    "max_duration_ms": 1200.0,
    "total_chars": 5600
  },
  "batch_jobs": {
    "total": 5,
    "completed": 4,
    "failed": 1
  }
}
```

## ニュース

### `POST /api/yomiage/news`

日次ニュースサマリを生成して読み上げる。

レスポンス:
```json
{
  "status": "reading",
  "article_count": 15
}
```

## 使用例

```bash
# ヘルスチェック
curl http://localhost:8030/health

# 読み上げ開始
curl -X POST http://localhost:8030/api/yomiage/read \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.aozora.gr.jp/cards/000879/files/127_15260.html"}'

# 状態確認
curl http://localhost:8030/api/yomiage/status

# 一時停止
curl -X POST http://localhost:8030/api/yomiage/pause

# 再開
curl -X POST http://localhost:8030/api/yomiage/resume

# 停止
curl -X POST http://localhost:8030/api/yomiage/stop

# テキスト合成（音声ファイルを保存）
curl -X POST http://localhost:8030/api/yomiage/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "テスト読み上げです", "speed": 1.1}' \
  --output output.wav

# ボイス一覧
curl http://localhost:8030/api/yomiage/voices

# バッチ合成ジョブ開始
JOB=$(curl -s -X POST http://localhost:8030/api/yomiage/synthesize/batch \
  -H "Content-Type: application/json" \
  -d '{"url": "https://...", "mode": "voicevox"}' | jq -r '.job_id')

# ジョブ状態確認
curl http://localhost:8030/api/yomiage/synthesize/batch/$JOB

# SSE 進捗確認
curl -N http://localhost:8030/api/yomiage/synthesize/batch/$JOB/progress

# メトリクス
curl http://localhost:8030/api/yomiage/metrics

# ニュース
curl -X POST http://localhost:8030/api/yomiage/news
```
