# 設定リファレンス

## 設定ファイル

設定は `config/` ディレクトリのYAMLファイルと `.env` 環境変数の2層構造。

| ファイル | 内容 |
|---------|------|
| `config/default.yaml` | メイン設定 |
| `config/scene_params.yaml` | シーン修飾子・感情スタイルウェイト |
| `config/voices.yaml` | ボイス定義 |
| `.env` | 認証情報・接続先（gitignore対象） |

YAMLファイル内で `${ENV_VAR:-default}` 構文を使って環境変数を参照できる。

## config/default.yaml

### tts

```yaml
tts:
  primary_provider: voisona       # メインTTSプロバイダー
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

### ollama

```yaml
ollama:
  url: ${OLLAMA_URL:-http://localhost:11434}
  model: ${OLLAMA_MODEL:-qwen3.5:3b}   # NLP分析用モデル
  summary_model: ${OLLAMA_SUMMARY_MODEL}  # ニュース要約用（オプション）
```

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
  output_dir: output              # 出力ベースディレクトリ
  default_mode: voisona           # デフォルトTTSモード
  analysis_window_chars: 3000     # Pass 1 ウィンドウサイズ（文字）
  analysis_window_sentences: 25   # Pass 2 ウィンドウサイズ（文数）
  manifest_save_interval: 10      # マニフェスト保存間隔（文数）
  silence_duration: 1.5           # シーンブレーク無音秒数
  concat_format: wav              # デフォルト出力フォーマット
  cleanup_after_concat: false     # 結合後に個別ファイル削除
  voisona_vm_mount: ${VOISONA_VM_MOUNT:-Z:}   # VM内のvirtifsマウントポイント
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

## 環境変数一覧

| 変数 | 用途 | デフォルト |
|------|------|-----------|
| `VOISONA_URL` | VoiSona Talk APIのURL | `http://192.168.1.173:32766` |
| `VOISONA_USERNAME` | VoiSona Talk ユーザー名 | — |
| `VOISONA_PASSWORD` | VoiSona Talk パスワード | — |
| `VOICEVOX_URL` | VOICEVOX Engine URL | `http://localhost:50021` |
| `OLLAMA_URL` | Ollama URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | NLP分析用モデル | `qwen3.5:3b` |
| `OLLAMA_SUMMARY_MODEL` | ニュース要約用モデル | （OLLAMA_MODELと同じ） |
| `VOISONA_VM_MOUNT` | VM内virtifsマウントポイント | `Z:` |
| `SLACK_BOT_TOKEN` | Slack Bot Token | — |
| `SLACK_APP_TOKEN` | Slack App Token | — |
| `MQTT_BROKER` | MQTTブローカー | `localhost` |
| `MQTT_PORT` | MQTTポート | `1883` |
