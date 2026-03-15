# システムアーキテクチャ

## 全体構成

```
┌──────────────────────────────────────────────────────────┐
│                      CLI / FastAPI                        │
│                    (cli.py / server.py)                   │
├────────────────────────┬─────────────────────────────────┤
│   ReadingEngine        │   BatchEngine                   │
│   (リアルタイム)        │   (バッチ合成)                   │
├────────────────────────┴─────────────────────────────────┤
│                    NLP Pipeline                           │
│  TextProcessor → TextClassifier → SpeakerExtractor       │
│                      → SceneAnalyzer (SLM)               │
├──────────────────────────────────────────────────────────┤
│                   ParamMapper                            │
│          キャラ × シーン × 感情 → TTSParams               │
├──────────────────────────────────────────────────────────┤
│                   TTS Layer                               │
│  ┌─────────────┐  ┌──────────────┐                       │
│  │  VoiSona     │  │  VOICEVOX    │                       │
│  │  (Win VM)    │  │  (Docker)    │                       │
│  └─────────────┘  └──────────────┘                       │
├──────────────────────────────────────────────────────────┤
│                Content Sources                           │
│  AozoraSource  │  NarouSource  │  KakuyomuSource         │
└──────────────────────────────────────────────────────────┘
```

## パッケージ構造

```
src/yomiage/
├── cli.py              # typer CLI エントリポイント
├── server.py           # FastAPI サーバー
├── config.py           # YAML設定ロード + 環境変数解決
│
├── nlp/                # NLP処理パイプライン
│   ├── text_processor.py   # テキスト前処理（HTML, ルビ, 正規化）
│   ├── classifier.py       # ルールベースセグメント分類
│   ├── speaker.py          # ルールベース話者識別
│   ├── scene_analyzer.py   # SLMによるシーン・感情分析
│   ├── splitter.py         # 適応型テキスト分割
│   ├── ollama_client.py    # Ollama REST APIクライアント
│   └── translator.py       # 翻訳ユーティリティ
│
├── reader/             # 読み上げエンジン
│   ├── engine.py           # ReadingEngine（リアルタイム）
│   ├── character_db.py     # キャラクターDB（JSON永続化）
│   ├── param_mapper.py     # キャラ×シーン→TTSParams変換
│   └── bookmark.py         # 読み位置ブックマーク
│
├── batch/              # バッチ合成パイプライン
│   ├── engine.py           # BatchEngine（A+B+Cオーケストレーター）
│   ├── analyzer.py         # Phase A: 2段階LLM全文分析
│   ├── manifest.py         # マニフェストデータ構造+JSON永続化
│   ├── synthesizer.py      # 合成インターフェース（ABC）
│   ├── voisona_synth.py    # VoiSona destination:file合成
│   ├── voicevox_synth.py   # VOICEVOX WAVバイト合成
│   └── concatenator.py     # Phase C: ffmpeg結合
│
├── tts/                # TTS プロバイダー層
│   ├── base.py             # TTSProvider ABC, TTSParams, AudioResult
│   ├── manager.py          # TTSManager（フォールバック, キュー）
│   ├── voisona.py          # VoiSona Talk REST APIプロバイダー
│   ├── voicevox.py         # VOICEVOX REST APIプロバイダー
│   └── playback.py         # WAV再生（aplay / sounddevice）
│
├── sources/            # コンテンツソース
│   ├── base.py             # ContentSource ABC
│   ├── registry.py         # URL→ソース自動判定
│   ├── aozora.py           # 青空文庫
│   ├── narou.py            # 小説家になろう
│   └── kakuyomu.py         # カクヨム
│
├── news/               # ニュースモジュール
│   ├── fetcher.py          # RSS取得
│   ├── summarizer.py       # SLM要約
│   ├── urgency.py          # 速報スコアリング
│   └── scheduler.py        # 日次/ポーリングスケジューラ
│
└── slack/              # Slack連携
    ├── monitor.py          # WebSocket監視
    └── scorer.py           # 重要度スコアリング
```

## データフロー

### リアルタイムモード

```
URL → ContentSource.fetch_chapter()
  → TextProcessor.process()        テキスト前処理
  → OllamaClient.romanize()        アルファベット→カタカナ
  → TextSplitter.split()           チャンク分割（最大200文字）
  → [lookahead バッチ]
     → TextClassifier.classify()   DIALOGUE/NARRATION/THOUGHT/SCENE_BREAK
     → SpeakerExtractor.extract()  話者候補付与
     → SceneAnalyzer.analyze_batch()  SLMによるシーン・感情分析
  → ParamMapper.map()              キャラ×シーン→TTSParams
  → TTSManager.enqueue()           合成キューに投入
  → Provider.synthesize()          音声合成
  → play_wav() / SPICE             再生
```

### バッチモード

```
Phase A: 分析
  URL → ContentSource（全チャプター取得）
  → Pass 1: キャラクター発見（LLMウィンドウスキャン）
  → Pass 2: 章ごと詳細分析（話者・シーン・感情・視点）
  → BatchManifest（JSON永続化）

Phase B: 合成
  manifest.json → 文ごとに合成
  → VoiSona: destination:file → virtiofs → ホストWAV
  → VOICEVOX: WAVバイト → ファイル書き出し
  → レジューム対応（pending/synthesized/failed管理）

Phase C: 結合
  連番WAV → ffmpeg concat demuxer
  → chapter_NNN.wav → full.wav
  → mp3/flac変換オプション
```

## 永続化データ

| データ | 保存先 | 形式 |
|--------|--------|------|
| キャラクターDB | `data/characters/{work_id}.json` | JSON |
| ブックマーク | `data/bookmarks/{hash}.json` | JSON |
| バッチマニフェスト | `output/{work_id}/manifest.json` | JSON |
| 合成WAV | `output/{work_id}/NNNN.wav` | WAV |
| 結合出力 | `output/{work_id}/full.wav` | WAV/MP3/FLAC |

## 外部依存サービス

| サービス | 用途 | プロトコル | デフォルトURL |
|---------|------|-----------|-------------|
| VoiSona Talk | 音声合成（高品質） | REST API | `http://192.168.1.173:32766` |
| VOICEVOX | 音声合成（フォールバック） | REST API | `http://localhost:50021` |
| Ollama | SLM推論（NLP分析） | REST API | `http://localhost:11434` |
