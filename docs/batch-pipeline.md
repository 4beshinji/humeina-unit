# Batch パイプライン

Batch は URL から発話計画を作り、Provider ごとの音声を検証済み artifact として公開し、
必要なら結合・字幕・動画まで実行します。外部 API の allow_partial を除き、CLI と REST で
同じ completion semantics を使います。

## フェーズ

```text
Phase A: analyze
  URL → URLPolicy/ContentSource → NLP → UtterancePlan
  → output/works/{work_id}/plans/{plan_id}.json

Phase B: synthesize
  plan + run_id → Provider/SynthesisService
  → atomic publish + WAV validation
  → output/works/{work_id}/runs/{run_id}/artifact-manifest.json

Phase C: concat
  verified utterance artifacts → chapter/full output

Phase D (任意)
  complete run の manifest + audio + assets → subtitle/video
```

Phase A では TextProcessor、classifier、speaker extractor、SceneAnalyzer（LLM が利用できない
場合は rule-based fallback）を使います。TTS の文字数制限による分割でも話者と VoiceIntent を
継承します。

## Identity と保存レイアウト

```text
output/
├── works/{work_id}/
│   ├── plans/{plan_id}.json       # Provider 非依存、再利用可能
│   └── runs/{run_id}/             # 1回の実行
│       ├── manifest.json          # Batch 状態/文章/identity
│       ├── artifact-manifest.json # checksum/duration/provider/verified
│       ├── chapter_001.wav
│       ├── full.wav (または .mp3/.flac)
│       └── full.mp4 (任意)
└── {legacy_work_id}/manifest.json # 旧 layout（read-only compatibility）
```

work_id は作品 identity、plan_id は source/config fingerprint、run_id は Provider/format ごとの
実行、job_id は CLI/API の受付 identity です。同一 URL を並列に実行しても run directory は共有
しません。plan は音声パス・Provider 固有 ID・duration を持ちません。

## Phase A: analyze

```bash
yomiage batch analyze "https://ncode.syosetu.com/n1234ab/" --mode voicevox --chapters 1-5
```

分析が成功すると run manifest と immutable plan が保存されます。旧 manifest からの明示 import
は [移行手順](migration.md) を使用し、既存ファイルを上書きしません。

## Phase B: synthesize

```bash
yomiage batch synthesize c8113c64b0dc --mode voicevox
yomiage batch retry c8113c64b0dc --mode voicevox
```

Provider は SynthesisService 経由で retry/fallback/cache/metrics を適用します。音声ファイルは
同一ディレクトリの一時ファイルへ書き、WAV 検証・checksum 計算後に replace() で公開します。
artifact manifest の final entry は verified=true かつ terminal state のものだけです。

## Phase C: concat

```bash
yomiage batch concat c8113c64b0dc --format wav
yomiage batch concat c8113c64b0dc --format mp3 --cleanup
```

ffmpeg concat は検証済み artifact の順序だけを入力にします。--cleanup は concat 成功後に
個別ファイルを削除するため、実行前に backup を取ってください。

## Completion / partial semantics

BatchManifest.refresh_completion_status() が文の状態とファイル実体を一元判定します。

| 状態 | 条件 | CLI/API の扱い |
| --- | --- | --- |
| complete | 全文が synthesized、全 artifact が存在/検証済み | completed、concat/video 可 |
| complete_with_warnings | 明示 skip のみ | 成功だが warning を保持 |
| partial | synthesized と unresolved（pending/failed/欠損）が混在 | allow_partial=true のみ部分 concat。video は不可 |
| failed | unresolved、かつ利用可能な音声がない | concat を拒否し失敗終了 |

REST POST /api/yomiage/synthesize/batch の allow_partial は既定 false です。false の場合は
partial/failed manifest を completed と報告せず、通常の full.wav を生成しません。true の場合だけ
部分成果物を生成し、job、SSE、manifest、result に partial を残します。CLI に partial を
暗黙許可するオプションはありません。

## Phase D: subtitle / video

```bash
yomiage batch subtitle c8113c64b0dc --format ass
yomiage batch video c8113c64b0dc --style subtitle
yomiage batch run "https://example.com/story" --video --style portrait
```

subtitle|portrait と ASS/SRT を選択できます。partial run は字幕生成後でも動画生成を行いません。
背景、立ち絵、字幕、BGM/SE は video 設定と assets/ から解決し、動画生成には ffmpeg が必要です。

## 進捗確認

```bash
yomiage batch status c8113c64b0dc
```

REST では GET /api/yomiage/synthesize/batch/{job_id} と /progress (SSE) を使います。終端 status
は completed、complete_with_warnings、partial、failed、cancelled です。

## Provider 固有の補足

- VoiSona: destination=file と VM mount（既定 Z:）を使う場合は [virtiofs setup](virtiofs-setup.md)。
- VOICEVOX: HTTP Engine から WAV bytes を受け、artifact store が公開します。
- VOICEPEAK: ローカル executable を呼び出し、max_chars で長文を分割します。

## 失敗時の保全

分析・合成を中断しても atomic write により最後に検証済みの manifest/artifact は残ります。
run directory の一時ファイルや未検証ファイルは final artifact manifest に列挙されません。
既存 run を再利用する場合は status/artifact-manifest.json を確認してから retry を実行し、
Provider/format を変える場合は新しい run を作成してください。

旧 layout の backup、read-only import、検証、切り替え、rollback は [migration.md](migration.md) に
固定手順があります。
