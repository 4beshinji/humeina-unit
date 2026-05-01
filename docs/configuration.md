# 設定リファレンス

## 設定ファイル

設定は `config/` ディレクトリのYAMLファイルと `.env` 環境変数の2層構造。

| ファイル | 内容 |
|---------|------|
| `config/default.yaml` | メイン設定 |
| `config/scene_params.yaml` | シーン修飾子・感情スタイルウェイト |
| `config/voices.yaml` | ボイス定義 |
| `config/math_dict.yaml` | 数式記号→読み下し辞書 |
| `config/voice_profiles/*.yaml` | VoiSona/VOICEVOX 用ボイスプロファイル |
| `config/voicepeak_profiles/*.yaml` | VOICEPEAK 用ナレータープロファイル |
| `.env` | 認証情報・接続先（gitignore対象） |

YAMLファイル内で `${ENV_VAR:-default}` 構文を使って環境変数を参照できる。

## config/default.yaml

### tts

```yaml
tts:
  primary_provider: voisona       # voisona / voicevox / voicepeak
  fallback_provider: voicevox     # フォールバックプロバイダー
  lookahead_chunks: 5             # NLP先読みチャンク数
  max_chunk_chars: 200            # チャンク最大文字数
```

### voisona

```yaml
voisona:
  url: ${VOISONA_URL:-http://192.168.1.173:32766}
  username: ${VOISONA_USERNAME}
  password: ${VOISONA_PASSWORD}
  default_voice: nurse-robot-type-t_ja_JP
  language: ja_JP
```

### voicevox

```yaml
voicevox:
  url: ${VOICEVOX_URL:-http://localhost:50021}
  default_speaker: 47             # デフォルトスピーカーID
```

### voicepeak

```yaml
voicepeak:
  path: /home/sin/code/una/Voicepeak-linux64/Voicepeak/voicepeak
  default_narrator: "Otomachi Una"
  max_chars: 140                  # 1呼び出しあたり最大文字数（超えたら分割）
  pitch_scale: 300                # ピッチスケール基準
```

### ollama

```yaml
ollama:
  url: ${OLLAMA_URL:-http://localhost:11434}
  model: ${OLLAMA_MODEL:-qwen3:8b}        # NLP分析用モデル
  summary_model: ${OLLAMA_SUMMARY_MODEL}  # ニュース要約用（オプション、未指定時は model と同じ）
```

Ollama 呼び出しが失敗した場合のフォールバックとして Gemini API を使うことができる（`yomiage news --gemini-key` または `GEMINI_API_KEY` 環境変数）。

### news

```yaml
news:
  daily_schedule: "08:00"         # 日次サマリ時刻
  poll_interval_minutes: 5        # 速報チェック間隔（分）
  urgency_threshold: 0.8          # 速報判定閾値（0.0〜1.0）
  foreign_language: translate     # 外国語記事の処理
  sources:                        # RSSソース
    - nhk_main
    - bbc_world
    - guardian_world
  tts:
    speed: 1.1                    # ニュース読み上げ速度
```

利用可能なRSSソース:
- `nhk_main` — NHK 主要ニュース
- `nhk_international` — NHK 国際ニュース
- `bbc_world` — BBC World News
- `guardian_world` — The Guardian World

### slack

```yaml
slack:
  enabled: false
  channels: []                    # 監視対象チャンネル
  mention_boost: 0.5              # メンション時のスコアブースト
  importance_threshold: 0.6       # 重要度閾値
```

### reader

```yaml
reader:
  auto_advance: true              # チャプター自動遷移
  bookmark_auto_save: true        # ブックマーク自動保存
```

### batch

```yaml
batch:
  output_dir: output                          # 出力ベースディレクトリ
  default_mode: voisona                       # デフォルトTTSモード
  analysis_window_chars: 3000                 # Pass 1 ウィンドウサイズ（文字）
  analysis_window_sentences: 25               # Pass 2 ウィンドウサイズ（文数）
  manifest_save_interval: 10                  # マニフェスト保存間隔（文数）
  silence_duration: 1.5                       # シーンブレーク無音秒数
  concat_format: wav                          # デフォルト結合フォーマット
  cleanup_after_concat: false                 # 結合後に個別ファイル削除
  voisona_vm_mount: ${VOISONA_VM_MOUNT:-Z:}   # VM内のvirtiofsマウントポイント
  voice_profile_dir: config/voice_profiles    # ボイスプロファイル探索先
  synth_concurrency: 1                        # VoiSona同時合成ジョブ数（流量制御）
```

`synth_concurrency` を 1 にしておくと VoiSona に対する同時接続が 1 本のみ（推奨）。値を増やすと並列合成で高速化するが VoiSona 側のキューが増える点に注意。

### ex_voice

```yaml
ex_voice:
  enabled: false                                         # CLI の --ex-voice でも有効化可
  wav_dir: ${EX_VOICE_WAV_DIR:-/home/sin/code/una/VoiceWav}
  cooldown_chunks: 10                                    # 直近何チャンク以内は再挿入しない
  max_per_chapter: 8                                     # 1チャプターあたりの最大挿入数
  llm_max_insertions: 2                                  # LLMバッチごとの最大挿入数
```

### studio

```yaml
studio:
  default_format: ymm4            # ymm4 / plain
  default_pause: 0.3              # セリフ間ポーズ（秒）
  max_slug_chars: 15              # ファイル名スラッグの最大文字数
  default_provider: voicevox
  cache_enabled: true             # 音声キャッシュ
```

### video

```yaml
video:
  enabled: false
  resolution: [1920, 1080]
  fps: 24
  codec: libx264
  crf: 23
  preset: medium
  subtitle:
    font_size: 48
    font_name: "Noto Sans JP"
    outline_size: 3
    margin_bottom: 60
    max_chars_per_line: 20
    speaker_colors:
      _narrator: "#FFFFFF"
      _dialogue: "#FFFF00"
      _thought: "#87CEEB"
  background:
    transition: fade
    transition_duration: 1.0
    ken_burns_enabled: false
    ken_burns_zoom: 1.2
    scene_colors:
      daily: "#2C3E50"
      battle: "#8B0000"
      romance: "#FF69B4"
      tense: "#1C1C1C"
      comedy: "#FFD700"
      sad: "#4A4A8A"
      horror: "#0D0D0D"
  portrait:
    enabled: true
    position: bottom_right
    max_height_ratio: 0.7
    fade_duration: 0.3
    margin_x: 50
    margin_y: 20
  audio:
    bgm_enabled: false
    bgm_volume: 0.15
    bgm_idle_volume: 0.3
    se_enabled: false
    se_volume: 0.5
    ducking_fade: 0.5
  title_card:
    enabled: false
    duration: 3.0
    font_size: 72
    subtitle_font_size: 36
  assets_dir: assets
```

## config/scene_params.yaml

### シーン修飾子

ベースパラメータに対して乗算（speed）または加算（volume, intonation）される。

```yaml
scenes:
  daily:    { speed: 1.0,  volume: 0,  intonation: 1.0 }
  battle:   { speed: 1.1,  volume: 2,  intonation: 1.3 }
  romance:  { speed: 0.9,  volume: 0,  intonation: 0.8 }
  tense:    { speed: 1.05, volume: 1,  intonation: 1.1 }
  comedy:   { speed: 1.0,  volume: 0,  intonation: 1.2 }
  sad:      { speed: 0.85, volume: -1, intonation: 0.7 }
  horror:   { speed: 0.9,  volume: -1, intonation: 0.6 }
```

### 感情スタイルウェイト

VoiSona Talk の5要素スタイルウェイト: [Normal, Happy, Angry, Sad, Smol]

```yaml
emotion_styles:
  neutral:   [1.0, 0.0, 0.0, 0.0, 0.0]
  happy:     [0.3, 0.7, 0.0, 0.0, 0.0]
  angry:     [0.2, 0.0, 0.8, 0.0, 0.0]
  sad:       [0.2, 0.0, 0.0, 0.8, 0.0]
  surprised: [0.5, 0.3, 0.0, 0.0, 0.2]
  scared:    [0.3, 0.0, 0.2, 0.5, 0.0]
  gentle:    [0.4, 0.3, 0.0, 0.0, 0.3]
```

intensity < 1.0 の場合、neutral との線形補間が適用される。

## config/voices.yaml

プロバイダーごとのボイス定義。キャラクター自動割当時のプールとして使用。

```yaml
providers:
  voisona:
    voices:
      - id: "nurse-robot-type-t_ja_JP"
        label: "ナースロボ＿タイプT"
        gender: female
        age_group: young_adult
        favorite: true
  voicevox:
    voices:
      - id: 47
        label: "ナースロボ＿タイプT（ノーマル）"
        gender: female
        age_group: young_adult
        favorite: true
        default: true
      - id: 46
        label: "ナースロボ＿タイプT（楽々）"
        ...

favorites_only: true              # お気に入りボイスのみ使用
```

## config/math_dict.yaml

数式記号や演算子を読み下すための辞書。`MathProcessor` がリアルタイム/バッチ両方で利用する。

## ボイスプロファイル

`yomiage tune` / `yomiage vp-tune` コマンドで生成・更新される。

| ディレクトリ | 対象 |
|-------------|------|
| `config/voice_profiles/` | VoiSona / VOICEVOX |
| `config/voicepeak_profiles/` | VOICEPEAK |

## 環境変数一覧

| 変数 | 用途 | デフォルト |
|------|------|-----------|
| `VOISONA_URL` | VoiSona Talk APIのURL | `http://192.168.1.173:32766` |
| `VOISONA_USERNAME` | VoiSona Talk ユーザー名 | — |
| `VOISONA_PASSWORD` | VoiSona Talk パスワード | — |
| `VOISONA_VOICE_NAME` | VoiSona 既定ボイス名 | `nurse-robot-type-t_ja_JP` |
| `VOICEVOX_URL` | VOICEVOX Engine URL | `http://localhost:50021` |
| `OLLAMA_URL` | Ollama URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | NLP分析用モデル | `qwen3:8b` |
| `OLLAMA_SUMMARY_MODEL` | ニュース要約用モデル | （`OLLAMA_MODEL` と同じ） |
| `GEMINI_API_KEY` | Gemini フォールバック有効化 | — |
| `VOISONA_VM_MOUNT` | VM内virtiofsマウントポイント | `Z:` |
| `EX_VOICE_WAV_DIR` | EXボイス用WAV格納ディレクトリ | `/home/sin/code/una/VoiceWav` |
| `SLACK_BOT_TOKEN` | Slack Bot Token | — |
| `SLACK_APP_TOKEN` | Slack App Token | — |
| `MQTT_BROKER` | MQTTブローカー | `localhost` |
| `MQTT_PORT` | MQTTポート | `1883` |
| `MQTT_USER` / `MQTT_PASS` | MQTT認証 | — |
