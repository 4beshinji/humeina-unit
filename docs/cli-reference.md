# CLI リファレンス

エントリポイントは `yomiage`（`src/yomiage/cli.py`）です。オプションの完全な表示は実装の
help を正とします。

```bash
uv run yomiage --help
uv run yomiage <command> --help
```

## Reader

### `yomiage read URL`

URL を取得し、リアルタイム読み上げを開始します。

```bash
yomiage read "https://www.aozora.gr.jp/cards/000879/files/127_15260.html" --provider voicevox
yomiage read "https://example.com/article" --ex-voice --verbose
```

`--provider/-p` は `voisona|voicevox|voicepeak`、`--ex-voice` は設定済み WAV クリップを有効化、
`--verbose/-v` は詳細ログです。

### `yomiage resume`

保存済み bookmark から再開します。

```bash
yomiage resume --provider voisona
```

## Batch

Batch は `work_id` とは別に immutable な `plan_id` と実行ごとの `run_id` を記録します。新規
成果物は `output/works/{work_id}/plans/` と `runs/{run_id}/` に保存されます。

### `yomiage batch analyze URL`

Phase A（全文分析）を実行します。

```bash
yomiage batch analyze "https://ncode.syosetu.com/n1234ab/" --mode voicevox --chapters 1-5
```

| option | default | 説明 |
| --- | --- | --- |
| `--mode/-m` | `voisona` | `voisona|voicevox|voicepeak` |
| `--chapters` | なし | `1-5` または `3` |
| `--output/-o` | `output` | output base directory |
| `--verbose/-v` | false | 詳細ログ |

### `yomiage batch synthesize WORK_ID`

既存 manifest/plan を使い Phase B の合成を行います。

```bash
yomiage batch synthesize c8113c64b0dc --mode voicevox --output output
```

`--mode/-m`、`--output/-o`、`--verbose/-v` を指定できます。

### `yomiage batch concat WORK_ID`

検証済み WAV を Phase C で結合します。

```bash
yomiage batch concat c8113c64b0dc --format mp3 --cleanup
```

`--format/-f` は `wav|mp3|flac`、`--cleanup` は個別 WAV の削除、`--output/-o` は output base
directory です。未解決文や欠損 artifact があると通常 concat は拒否されます。

### `yomiage batch run URL`

Analyze→synthesize→concat（`--video` 時は Phase D）を実行します。

```bash
yomiage batch run "https://www.aozora.gr.jp/cards/000879/files/127_15260.html" \
  --mode voicevox --format wav
yomiage batch run "https://example.com/story" --mode voicepeak --video --style portrait
```

`--mode/-m`、`--chapters`、`--output/-o`、`--format/-f (wav|mp3|flac)`、`--cleanup`、
`--video`、`--style/-s (subtitle|portrait)`、`--verbose/-v` を指定できます。

### `yomiage batch subtitle WORK_ID`

```bash
yomiage batch subtitle c8113c64b0dc --format ass
```

`--format/-f` は `ass|srt`（既定 `ass`）、`--output/-o`、`--verbose/-v` を指定できます。

### `yomiage batch video WORK_ID`

```bash
yomiage batch video c8113c64b0dc --style subtitle
```

`--style/-s` は `subtitle|portrait`、`--output/-o`、`--verbose/-v` を指定できます。partial
run から動画は生成されません。

### `yomiage batch status WORK_ID`

```bash
yomiage batch status c8113c64b0dc --output output
```

`--output/-o` と `--verbose/-v` を指定できます。status には analysis/synthesis と
`completion_status`（`complete|complete_with_warnings|partial|failed`）が反映されます。

### `yomiage batch retry WORK_ID`

```bash
yomiage batch retry c8113c64b0dc --mode voicevox
```

失敗文のみを再合成します。`--mode/-m`、`--output/-o`、`--verbose/-v` を指定できます。

`allow_partial` は REST API で明示する契約です。CLI の通常 concat/run は partial を自動で成功扱い
にしません。旧 `output/{work_id}/manifest.json` の移行は [migration.md](migration.md) を参照してください。

## Voice / Character

```bash
yomiage voices list --provider voicevox
yomiage characters list
yomiage characters assign "太郎" 47 --work-id c8113c64b0dc
```

`voices list` は `--provider/-p` と `--verbose/-v`、`characters list` は `--verbose/-v`、
`characters assign` は必須 `NAME VOICE_ID` と任意 `--work-id`/`--verbose/-v` です。

## Profile tuning

VoiSona/VOICEVOX 系:

```bash
yomiage tune range nurse-robot-type-t_ja_JP
yomiage tune preset nurse-robot-type-t_ja_JP
yomiage tune emotion nurse-robot-type-t_ja_JP --base female_young
yomiage tune noise nurse-robot-type-t_ja_JP --base female_young
yomiage tune demo nurse-robot-type-t_ja_JP
yomiage tune test nurse-robot-type-t_ja_JP male_young --emotion happy --intensity 0.7
```

`range/preset/demo` は `--text/-t`（demo はテスト文）と `--verbose/-v`、`emotion/noise` は
追加で `--base/-b`、`test` は `PRESET` 引数と `--emotion/-e`、`--intensity/-i`、`--text/-t`、
`--verbose/-v` を受け取ります。

VOICEPEAK 系:

```bash
yomiage vp-tune range "Otomachi Una"
yomiage vp-tune preset "Otomachi Una"
yomiage vp-tune emotion "Otomachi Una" --base female_young
yomiage vp-tune noise "Otomachi Una" --base female_young
yomiage vp-tune demo "Otomachi Una"
yomiage vp-tune test "Otomachi Una" male_young --emotion happy --intensity 0.7
```

チューニングの profile は checkout の `config/voice_profiles/` または
`config/voicepeak_profiles/` に保存されます。インストール環境では、実行時の作業ディレクトリと
profile path を明示してください。

## Studio

```bash
yomiage studio synth script.txt --format ymm4
yomiage studio preview script.txt --line 3
yomiage studio voices --provider voicevox
```

`studio synth SCRIPT_FILE` は `.txt/.csv/.json`、`--provider/-p`、`--output/-o`（既定 `output`）、
`--format/-f (ymm4|plain)`、`--pause`、`--speaker-map`、`--project/-n`、`--no-cache`、
`--verbose/-v` を受け取ります。`preview` は `--line/-l`（1-based）、provider、speaker-map、
verbose、`voices` は provider と verbose を受け取ります。

## News / Slack

News は action を positional に渡します。

```bash
yomiage news daily --output news.wav
yomiage news check --provider voicevox
yomiage news daily --gemini-key "$GEMINI_API_KEY"
```

`news` の action は `daily|check`（既定 `daily`）。共通 option は `--provider/-p`、`--output/-o`、
`--gemini-key`（`GEMINI_API_KEY` env も可）、`--verbose/-v` です。

Slack は `slack-sdk` extra と `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN` が必要です。

```bash
yomiage slack start
```

action は `start`（既定）、option は `--verbose/-v` です。HEMS/MQTT command はありません。

## FastAPI server

```bash
# local-only (default)
yomiage serve
yomiage serve --host 127.0.0.1 --port 8030

# explicitly authenticated external bind
YOMIAGE_API_TOKEN='replace-with-a-long-random-token' \
  yomiage serve --host 0.0.0.0 --port 8030
```

`--host` の既定は `127.0.0.1`、`--port` の既定は `8030`、`--api-token`（env:
`YOMIAGE_API_TOKEN`）は外部 bind に必須、`--verbose/-v` は詳細ログです。非 loopback で token が
ない場合は uvicorn 起動前に拒否されます。認証、rate limit、partial semantics は
[REST API リファレンス](api-reference.md)を参照してください。
