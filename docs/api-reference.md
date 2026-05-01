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

テキストを即座に合成・再生する。

リクエスト:
```json
{
  "text": "こんにちは",
  "voice": "neutral",
  "speed": 1.0
}
```

レスポンス:
```json
{
  "duration": 1.5,
  "format": "wav",
  "has_audio": true
}
```

| フィールド | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `text` | string | — | 合成するテキスト |
| `voice` | string | `"neutral"` | ボイストーン |
| `speed` | float | `1.0` | 話速 |

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

# テキスト合成
curl -X POST http://localhost:8030/api/yomiage/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "テスト読み上げです", "speed": 1.1}'

# ニュース
curl -X POST http://localhost:8030/api/yomiage/news
```
