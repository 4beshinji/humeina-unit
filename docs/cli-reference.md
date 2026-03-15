# CLI コマンドリファレンス

エントリポイント: `yomiage` (`src/yomiage/cli.py`)

## リアルタイム読み上げ

### `yomiage read <URL>`

URLのコンテンツを読み上げる。チャプター自動遷移対応。

```bash
yomiage read "https://www.aozora.gr.jp/cards/000879/files/127_15260.html"
yomiage read "https://ncode.syosetu.com/n1234ab/" --provider voicevox
yomiage read "https://kakuyomu.jp/works/..." -v
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--provider` | `-p` | TTSプロバイダー指定（`voisona`/`voicevox`） |
| `--verbose` | `-v` | 詳細ログ |

### `yomiage resume`

最後のブックマークから読み上げを再開する。

```bash
yomiage resume
yomiage resume --provider voisona
```

## バッチ合成

### `yomiage batch analyze <URL>`

Phase A: 全文NLP分析を行い、マニフェスト（`manifest.json`）を生成する。

```bash
yomiage batch analyze "https://ncode.syosetu.com/n1234ab/"
yomiage batch analyze "https://..." --mode voicevox --chapters 1-5
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--mode` | `-m` | `voisona` / `voicevox`（デフォルト: `voisona`） |
| `--chapters` | | チャプター範囲（例: `1-5`, `3`） |
| `--output` | `-o` | 出力ディレクトリ（デフォルト: `output`） |
| `--verbose` | `-v` | 詳細ログ |

出力:
```
output/{work_id}/manifest.json
```

### `yomiage batch synthesize <work_id>`

Phase B: マニフェストに基づいて音声合成を実行する。

```bash
yomiage batch synthesize c8113c64b0dc --mode voisona
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--mode` | `-m` | TTSモード |
| `--output` | `-o` | 出力ディレクトリ |
| `--verbose` | `-v` | 詳細ログ |

出力:
```
output/{work_id}/0000.wav
output/{work_id}/0001.wav
...
```

### `yomiage batch concat <work_id>`

Phase C: 連番WAVファイルをffmpegで結合する。

```bash
yomiage batch concat c8113c64b0dc
yomiage batch concat c8113c64b0dc --format mp3 --cleanup
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--format` | `-f` | 出力フォーマット: `wav`/`mp3`/`flac` |
| `--cleanup` | | 結合後に個別WAVを削除 |
| `--output` | `-o` | 出力ディレクトリ |
| `--verbose` | `-v` | 詳細ログ |

出力:
```
output/{work_id}/chapter_001.wav
output/{work_id}/full.wav
```

### `yomiage batch run <URL>`

Phase A + B + C をフルパイプラインで実行する。

```bash
yomiage batch run "https://www.aozora.gr.jp/cards/..." --mode voisona
yomiage batch run "https://ncode.syosetu.com/..." --mode voicevox --chapters 1-3 --format mp3
```

全オプションは `analyze` + `synthesize` + `concat` の組み合わせ。

### `yomiage batch status <work_id>`

バッチ処理の進捗状況を表示する。

```bash
yomiage batch status c8113c64b0dc
```

出力例:
```
Work: c8113c64b0dc
  Title: 羅生門
  Mode: voisona
  Chapters: 1
  Progress: 162/162
  Pending: 0
  Failed: 0
  Analysis: done
  Synthesis: done
```

### `yomiage batch retry <work_id>`

失敗した文を再合成する。

```bash
yomiage batch retry c8113c64b0dc --mode voisona
```

## ボイス管理

### `yomiage voices list`

利用可能なボイス一覧を表示する。

```bash
yomiage voices list
yomiage voices list --provider voicevox
```

## キャラクター管理

### `yomiage characters list`

全作品のキャラクター一覧を表示する。

```bash
yomiage characters list
```

### `yomiage characters assign <name> <voice_id>`

キャラクターにボイスを手動割当する（ロック）。

```bash
yomiage characters assign "太郎" 47
yomiage characters assign "花子" 48 --work-id c8113c64b0dc
```

## ニュース

### `yomiage news daily`

日次ニュースサマリを生成して読み上げる。

```bash
yomiage news daily
```

### `yomiage news check`

速報をチェックし、閾値を超えたものを読み上げる。

```bash
yomiage news check
```

## Slack連携

### `yomiage slack start`

Slack WebSocket監視を開始する。重要度の高いメッセージを読み上げる。

```bash
yomiage slack start
```

要件: `SLACK_BOT_TOKEN` と `SLACK_APP_TOKEN` を `.env` に設定。

## APIサーバー

### `yomiage serve`

FastAPIサーバーを起動する。

```bash
yomiage serve
yomiage serve --host 0.0.0.0 --port 8030
```

| オプション | 説明 |
|-----------|------|
| `--host` | バインドホスト（デフォルト: `0.0.0.0`） |
| `--port` | バインドポート（デフォルト: `8030`） |
| `--verbose` / `-v` | 詳細ログ |
