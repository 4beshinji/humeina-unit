# TTSプロバイダー詳細

## プロバイダー構成

```
TTSProvider (ABC)
├── VoisonaProvider    VoiSona Talk REST API（Windows VM）
└── VoicevoxProvider   VOICEVOX Engine REST API（Docker）
        │
        ▼
TTSManager             フォールバック + 合成キュー管理
```

## VoiSona Talk (`voisona.py`)

Windows VM上で動作するVoiSona Talk APIクライアント。

### 接続情報

| 項目 | デフォルト |
|------|-----------|
| URL | `http://192.168.1.173:32766` |
| APIベース | `/api/talk/v1` |
| 認証 | Basic認証 |
| ポーリング間隔 | 0.5秒 |
| タイムアウト | 120秒 |

### パラメータ

| パラメータ | 範囲 | デフォルト | 説明 |
|-----------|------|-----------|------|
| `speed` | 0.2 〜 5.0 | 1.0 | 話速 |
| `pitch` | -600 〜 600 | 0 | 声の高さ（セント単位） |
| `volume` | -8 〜 8 | 0 | 音量（dB） |
| `intonation` | 0 〜 2 | 1.0 | 抑揚の強さ |
| `huskiness` | -20 〜 20 | 0 | ハスキー度（+でガサ声、-でクリア） |
| `alp` | -1.0 〜 1.0 | 0 | 声質変換（-で大人、+で子供） |
| `style_weights` | 各0.0〜1.0 | [1,0,0,0,0] | [Normal, Happy, Angry, Sad, Smol] |

### 合成モード

#### リアルタイム（synthesize）

```
POST /api/talk/v1/speech-syntheses → UUID
GET  /api/talk/v1/speech-syntheses/{uuid} → ポーリング → succeeded
```

音声はVMのSPICE経由でホスト側に再生される。`AudioResult.audio_data` は空（`b""`）で、`duration` のみ返る。

#### パイプライン（enqueue_only + poll_until_done）

VoiSonaモードの場合、TTSManagerが以下のパイプラインを実行:
- チャンクN再生中にチャンクN+1の合成を開始
- `asyncio.gather()` で並行実行して途切れを防ぐ

#### ファイル出力（synthesize_to_file）

バッチ合成用。`destination: "file"` で直接WAV出力。

```json
{
  "destination": "file",
  "output_file_path": "Z:\\work_id\\0001.wav"
}
```

virtiofs経由でホスト側の `output/` に直接書き出される。

### 利用可能ボイス

現在: **ナースロボ＿タイプT** (`nurse-robot-type-t_ja_JP` v2.0.0)

スタイルウェイト5要素: Normal, Happy, Angry, Sad, Smol

## VOICEVOX (`voicevox.py`)

Docker上で動作するVOICEVOX Engine APIクライアント。

### 接続情報

| 項目 | デフォルト |
|------|-----------|
| URL | `http://localhost:50021` |
| 認証 | なし |

### パラメータ

| パラメータ | 説明 |
|-----------|------|
| `speaker` (int) | スピーカーID |
| `speedScale` | 話速 |
| `pitchScale` | ピッチ |
| `intonationScale` | 抑揚 |
| `volumeScale` | 音量 |

### 合成フロー

```
POST /audio_query?text=...&speaker=47 → query JSON
POST /synthesis?speaker=47 (body: query) → WAV bytes
```

2段階のAPI呼び出し。WAVバイトがそのまま返る。

### デフォルトスピーカー

| トーン | スピーカーID | ボイス |
|--------|------------|--------|
| neutral | 47 | ナースロボ＿タイプT（ノーマル） |
| caring | 47 | ナースロボ＿タイプT（ノーマル） |
| humorous | 48 | ナースロボ＿タイプT（恐怖） |
| alert | 46 | ナースロボ＿タイプT（楽々） |
| happy | 47 | ナースロボ＿タイプT（ノーマル） |

## TTSManager (`manager.py`)

プロバイダー選択とフォールバック、合成キューを管理する。

### フォールバック

```
primary (VoiSona) が unhealthy → fallback (VOICEVOX) に切替
primary の合成が失敗 → fallback で再試行
```

### キューモデル

```
enqueue(text, params) → _queue → _synth_loop → _audio_queue → _play_loop
```

- VoiSona: パイプライン合成（前チャンク再生と次チャンク合成の並行）
- VOICEVOX: 逐次合成

## バッチ合成モード

バッチ合成では TTSManager を使わず、`BatchSynthesizer` を直接使用。

### VoisonaBatchSynthesizer

- `destination: "file"` で直接WAV出力
- virtiofs のパスマッピング: `Z:\{work_id}\NNNN.wav` → `output/{work_id}/NNNN.wav`
- キャラ別パラメータ + シーン + 感情の組み合わせでパラメータ構築

### VoicevoxBatchSynthesizer

- `synthesize()` → WAVバイト → ファイル書き出し
- キャラごとに異なるスピーカーID

### 無音生成

シーンブレーク用に1.5秒の無音WAVを生成（PCM 16bit, 24kHz, mono）。
