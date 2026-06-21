# CLI コマンドリファレンス

エントリポイント: `yomiage` (`src/yomiage/cli.py`)

実行例は `uv run yomiage ...`（venv 有効化後は `yomiage ...`）。

## リアルタイム読み上げ

### `yomiage read <URL>`

URLのコンテンツを読み上げる。チャプター自動遷移対応。

```bash
yomiage read "https://www.aozora.gr.jp/cards/000879/files/127_15260.html"
yomiage read "https://ncode.syosetu.com/n1234ab/" --provider voicevox
yomiage read "https://kakuyomu.jp/works/..." -v
yomiage read "https://example.com/article" --ex-voice
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--provider` | `-p` | TTSプロバイダー指定（`voisona`/`voicevox`/`voicepeak`） |
| `--ex-voice` | | EXボイスクリップを文脈に応じて自動挿入 |
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
| `--mode` | `-m` | `voisona` / `voicevox` / `voicepeak`（デフォルト: `voisona`） |
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
| `--mode` | `-m` | TTSモード（`voisona`/`voicevox`/`voicepeak`） |
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

Phase A + B + C（必要に応じて + D）をフルパイプラインで実行する。

```bash
yomiage batch run "https://www.aozora.gr.jp/cards/..." --mode voisona
yomiage batch run "https://ncode.syosetu.com/..." --mode voicevox --chapters 1-3 --format mp3
yomiage batch run "https://..." --mode voicepeak --video --style portrait
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--mode` | `-m` | `voisona` / `voicevox` / `voicepeak` |
| `--chapters` | | チャプター範囲 |
| `--output` | `-o` | 出力ディレクトリ |
| `--format` | `-f` | 結合出力フォーマット（`wav`/`mp3`/`flac`） |
| `--cleanup` | | 結合後に個別WAVを削除 |
| `--video` | | 動画も生成（Phase D） |
| `--style` | `-s` | 動画スタイル: `subtitle` / `portrait` |
| `--verbose` | `-v` | 詳細ログ |

### `yomiage batch subtitle <work_id>`

字幕ファイルを生成する。

```bash
yomiage batch subtitle c8113c64b0dc --format ass
yomiage batch subtitle c8113c64b0dc --format srt
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--format` | `-f` | `ass` / `srt`（デフォルト: `ass`） |
| `--output` | `-o` | 出力ディレクトリ |

### `yomiage batch video <work_id>`

Phase D: 動画ファイルを生成する。

```bash
yomiage batch video c8113c64b0dc --style subtitle
yomiage batch video c8113c64b0dc --style portrait
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--style` | `-s` | `subtitle`（字幕のみ） / `portrait`（立ち絵差し替え） |
| `--output` | `-o` | 出力ディレクトリ |

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

`data/characters/` 以下の全作品のキャラクター一覧を表示する。

```bash
yomiage characters list
```

### `yomiage characters assign <name> <voice_id>`

キャラクターにボイスを手動割当する（ロック）。

```bash
yomiage characters assign "太郎" 47
yomiage characters assign "花子" 48 --work-id c8113c64b0dc
```

## ボイスプロファイル チューニング

### VoiSona / VOICEVOX 向け: `yomiage tune ...`

`tune` グループは VoiSona ボイス（および設定により VOICEVOX）のプロファイルを段階的に作る。

```bash
yomiage tune range nurse-robot-type-t_ja_JP        # Phase 1: パラメータ実用範囲探索
yomiage tune preset nurse-robot-type-t_ja_JP       # Phase 2: アーキタイププリセット作成
yomiage tune emotion nurse-robot-type-t_ja_JP --base female_young
yomiage tune noise nurse-robot-type-t_ja_JP --base female_young
yomiage tune demo nurse-robot-type-t_ja_JP         # Phase 5: 全プリセット×全感情デモ
yomiage tune test nurse-robot-type-t_ja_JP male_young --emotion happy --intensity 0.7
```

プロファイルは `config/voice_profiles/<voice>.yaml` に保存される。

### VOICEPEAK 向け: `yomiage vp-tune ...`

VOICEPEAK ナレーター用の同等コマンド群。

```bash
yomiage vp-tune range "Otomachi Una"
yomiage vp-tune preset "Otomachi Una"
yomiage vp-tune emotion "Otomachi Una" --base female_young
yomiage vp-tune noise "Otomachi Una" --base female_young
yomiage vp-tune demo "Otomachi Una"
yomiage vp-tune test "Otomachi Una" female_young --emotion happy --intensity 0.7
```

プロファイルは `config/voicepeak_profiles/<narrator>.yaml` に保存される。

## Studio（動画素材生成）

台本ファイルから音声素材を一括合成するコマンド群。

### `yomiage studio synth <script>`

```bash
yomiage studio synth script.txt --format ymm4
yomiage studio synth script.csv --speaker-map speakers.yaml --pause 0.4
yomiage studio synth script.json --provider voicevox --project demo01 --no-cache
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--provider` | `-p` | TTSプロバイダー |
| `--output` | `-o` | 出力ディレクトリ |
| `--format` | `-f` | `ymm4` / `plain`（デフォルト: `ymm4`） |
| `--pause` | | セリフ間ポーズ秒（デフォルト: 0.3） |
| `--speaker-map` | | 話者→ボイスのマッピングYAML/JSON |
| `--project` | `-n` | プロジェクト名 |
| `--no-cache` | | キャッシュ無効化 |

### `yomiage studio preview <script>`

台本の指定行をプレビュー再生する。

```bash
yomiage studio preview script.txt --line 3
```

### `yomiage studio voices`

各プロバイダーで利用可能なボイス一覧を表示する。

```bash
yomiage studio voices
yomiage studio voices --provider voicepeak
```

## ニュース

### `yomiage news daily`

日次ニュースサマリを生成して読み上げる。

```bash
yomiage news daily
yomiage news daily --output news.wav
yomiage news daily --gemini-key "$GEMINI_API_KEY"
```

| オプション | 短縮 | 説明 |
|-----------|------|------|
| `--provider` | `-p` | TTSプロバイダー |
| `--output` | `-o` | 音声ファイル出力先（指定時は再生せずファイル化） |
| `--gemini-key` | | Gemini APIキー（Ollamaフォールバック）。`GEMINI_API_KEY` 環境変数からも取得 |
| `--verbose` | `-v` | 詳細ログ |

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
