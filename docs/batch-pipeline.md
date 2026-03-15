# バッチ合成パイプライン

WEB小説の全文を事前分析し、連番WAVファイルとして合成・結合する非リアルタイムパイプライン。

## 概要

3フェーズの逐次パイプライン:

```
Phase A: 全文分析          Phase B: バッチ合成         Phase C: 結合
┌────────────────┐      ┌────────────────────┐     ┌──────────────┐
│ Pass 1: キャラ  │      │ manifest.json      │     │ ffmpeg concat│
│  クター発見     │ ───► │  ↓                 │ ──► │  ↓           │
│ Pass 2: 詳細   │      │ 文ごとに合成        │     │ chapter_N.wav│
│  分析          │      │  → NNNN.wav         │     │ full.wav     │
└────────────────┘      └────────────────────┘     └──────────────┘
```

## Phase A: 2段階LLM全文分析

### Pass 1: キャラクター発見

全チャプターを取得し、ウィンドウ分割（デフォルト3000文字）でSLMに投げる。

- 登場人物の抽出（名前、性別、年齢層、性格、役割）
- 名前の揺れ（エイリアス）の統合
- 既知キャラリストを毎回プロンプトに含め重複防止
- VoiSonaモード: キャラごとのパラメータプロファイル自動生成

### Pass 2: 章ごと詳細分析

1. 文単位に分割
2. ルールベースパイプライン適用（TextClassifier → SpeakerExtractor）
3. ウィンドウ単位（デフォルト25文）でSLMに投げ、以下を判定:
   - 話者確定
   - シーン分類（daily/battle/romance/tense/comedy/sad/horror）
   - 感情分析（neutral/happy/angry/sad/surprised/scared/gentle）
   - 感情強度（0.0〜1.0）
   - 地の文の視点キャラクター

### 出力

`output/{work_id}/manifest.json`:

```json
{
  "work_id": "c8113c64b0dc",
  "work_title": "羅生門",
  "source_url": "https://...",
  "mode": "voisona",
  "chapters": [...],
  "characters": {
    "下人": {"name": "下人", "gender": "male", "base_params": {"pitch": -100, ...}},
    "老婆": {"name": "老婆", "gender": "female", "base_params": {"pitch": 50, ...}}
  },
  "sentences": [
    {
      "index": 0,
      "text": "ある日の暮方の事である。",
      "chapter_index": 0,
      "segment_type": "narration",
      "speaker": null,
      "scene": "daily",
      "emotion": "neutral",
      "intensity": 0.5,
      "status": "pending"
    },
    ...
  ]
}
```

## Phase B: バッチ合成

### VoiSonaモード

VoiSona Talk APIの `destination: "file"` を使用し、virtiofs経由でホスト側に直接WAV出力する。

```
API POST → VoiSona VM → Z:\{work_id}\NNNN.wav → virtiofs → output/{work_id}/NNNN.wav
```

パラメータ決定:
1. キャラクターの `base_params`（pitch, huskiness, alp, speed）
2. シーン修飾子（speed乗算, volume加算）
3. 感情 → style_weights（intensity補間）
4. 地の文は視点キャラのパラメータを0.3倍で控えめ適用

### VOICEVOXモード

VOICEVOX Engine APIで合成し、WAVバイトをファイルに書き出す。

キャラクターごとに異なるスピーカーID（voice_id）を割当。

### 共通仕様

- **レジューム対応**: `status` フィールド（pending/synthesized/failed）でリカバリ
- **シーンブレーク**: 1.5秒の無音WAVを連番に挿入
- **記号スキップ**: 括弧のみ等の発話不可テキストは自動スキップ
- **アルファベット変換**: 合成直前に1文単位でLLMによるカタカナ変換
- **進捗保存**: 10文ごとにマニフェストを保存

## Phase C: 音声結合

ffmpegの concat demuxer を使用。

- チャプター単位: `output/{work_id}/chapter_001.wav`
- 作品全体: `output/{work_id}/full.wav`
- 出力フォーマット: wav / mp3 / flac
- `--cleanup` で個別WAVファイルを削除可能

## 使い方

```bash
# フルパイプライン
yomiage batch run "https://ncode.syosetu.com/n1234ab/" --mode voisona

# フェーズ個別実行
yomiage batch analyze "https://..." --chapters 1-5
yomiage batch synthesize abc123def456 --mode voisona
yomiage batch concat abc123def456 --format mp3

# 進捗確認
yomiage batch status abc123def456

# 失敗リトライ
yomiage batch retry abc123def456
```

## ディレクトリ構造

```
output/
└── c8113c64b0dc/           # work_id（URLのMD5先頭12文字）
    ├── manifest.json        # マニフェスト（全状態管理）
    ├── 0000.wav             # 文0の音声
    ├── 0001.wav             # 文1の音声
    ├── ...
    ├── chapter_001.wav      # チャプター1結合
    ├── chapter_002.wav      # チャプター2結合
    └── full.wav             # 全体結合
```

## 設定

`config/default.yaml`:

```yaml
batch:
  output_dir: output              # 出力ベースディレクトリ
  default_mode: voisona           # デフォルトTTSモード
  analysis_window_chars: 3000     # Pass 1 ウィンドウサイズ（文字）
  analysis_window_sentences: 25   # Pass 2 ウィンドウサイズ（文数）
  manifest_save_interval: 10      # マニフェスト保存間隔（文数）
  silence_duration: 1.5           # シーンブレーク無音秒数
  concat_format: wav              # デフォルト出力フォーマット
  cleanup_after_concat: false     # 結合後に個別ファイル削除
  voisona_vm_mount: "Z:"          # VM内のvirtifsマウントポイント
```
