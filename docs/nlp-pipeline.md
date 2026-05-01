# NLP処理パイプライン

テキストから話者・シーン・感情を判定し、TTSパラメータに変換する4段階パイプライン。

## 処理フロー

```
生テキスト
  │
  ▼
TextProcessor.process()         ── Stage 0: 前処理
  │ Unicode正規化, 青空文庫注記除去, 句読点統一, 空白整理
  ▼
TextClassifier.classify()       ── Stage 1: セグメント分類
  │ ルールベース → DIALOGUE / NARRATION / THOUGHT / SCENE_BREAK
  ▼
SpeakerExtractor.extract()      ── Stage 2: 話者識別
  │ ルールベース → 発話動詞パターンから話者候補を付与
  ▼
SceneAnalyzer.analyze_batch()   ── Stage 3: SLM分析
  │ Ollama (SLM) → 話者確定, シーン, 感情, 強度, 新キャラ検出
  ▼
ParamMapper.map()               ── Stage 4: パラメータ変換
  │ キャラクター × シーン × 感情 → TTSParams
  ▼
TTSParams
```

## Stage 0: テキスト前処理 (`text_processor.py`)

`TextProcessor.process()` が以下を順に適用:

| 処理 | 説明 |
|------|------|
| Unicode正規化 | NFKC で全角英数→半角等を統一 |
| 青空文庫注記除去 | `［＃...］` 形式の注記を除去 |
| 入力者注除去 | `【...】` 形式を除去 |
| マークダウン除去 | `*太字*`, `# 見出し` を除去 |
| 三点リーダー統一 | `...` → `…` |
| ダッシュ統一 | 連続ダッシュ → `――` |
| 字下げ除去 | 行頭全角スペースを除去 |
| 連続空行圧縮 | 3行以上の空行 → 2行 |

`TextProcessor.has_alphabet(text)` でアルファベット有無を判定。含まれる場合は `OllamaClient.romanize()` でカタカナに変換する（バッチモードでは合成直前に1文単位で実行）。

数式記号や演算子は `MathProcessor`（`config/math_dict.yaml` ベースの辞書）で読み下しに置換される。

## Stage 1: セグメント分類 (`classifier.py`)

`TextClassifier.classify()` がテキストを行ごとに解析し、`TextSegment` リストを返す。

### セグメント種別

| 種別 | 判定基準 | 例 |
|------|---------|-----|
| `DIALOGUE` | `「...」` で囲まれた部分 | `「こんにちは」` |
| `THOUGHT` | `（...）` または `『...』` で囲まれた部分 | `（どうしよう）` |
| `NARRATION` | 上記以外のテキスト | `彼は立ち上がった。` |
| `SCENE_BREAK` | `***`, `---`, `□□□` 等のパターン | `＊＊＊` |

1つの行内に会話と地の文が混在する場合は分離して別セグメントにする。

## Stage 2: 話者識別 (`speaker.py`)

`SpeakerExtractor.extract()` が `DIALOGUE` セグメントに話者候補を付与する。

### パターンマッチ

| パターン | 例 | 抽出結果 |
|---------|-----|---------|
| 前方パターン | `太郎は「こんにちは」` | `太郎` |
| 後方パターン | `「こんにちは」と花子が言った` | `花子` |
| 直接パターン | `太郎「こんにちは」` | `太郎` |

発話動詞: 言った、話した、叫んだ、呟いた、囁いた、答えた、尋ねた、怒鳴った、笑った 等

## Stage 3: SLM分析 (`scene_analyzer.py`)

`SceneAnalyzer.analyze_batch()` がOllama経由でSLMにセグメントバッチを投げ、以下を判定:

| フィールド | 値の範囲 | 説明 |
|-----------|---------|------|
| `speaker` | キャラ名 or null | 話者の確定（ルールベース候補を補正） |
| `new_character` | `{name, gender, age_group}` or null | 新キャラクター検出 |
| `scene` | daily/battle/romance/tense/comedy/sad/horror | シーン種別 |
| `emotion` | neutral/happy/angry/sad/surprised/scared/gentle | 感情 |
| `intensity` | 0.0〜1.0 | 感情の強度 |

SLMが利用不可の場合はデフォルト値（daily/neutral/0.5）で返す。Ollama がレートリミット等で失敗した際は、`OllamaClient` に渡された `fallback`（例: `GeminiClient`）に切り替えて再試行できる。

### プロンプト構造

```
System: あなたは小説テキスト分析器です。...
User:
  既知のキャラクター: 太郎, 花子
  以下のテキストセグメントを分析してください:
  [0] (dialogue) 「こんにちは」
  [1] (narration) 太郎は微笑んだ。
  JSON配列で回答: [{"segment_id": 0, "speaker": "太郎", ...}]
```

## Stage 4: パラメータ変換 (`param_mapper.py`)

`ParamMapper.map()` が `AnalyzedSegment` + `CharacterDB` から `TTSParams` を生成。

### 変換レイヤー

1. **キャラクターベースパラメータ**: `CharacterDB` から voice_id, speed, pitch, volume, huskiness, alp を取得
2. **シーン修飾子**: `config/scene_params.yaml` の `scenes` セクションから speed(乗算), volume(加算), intonation を適用
3. **感情スタイルウェイト**: `emotion_styles` セクションから5要素ウェイトを取得し、intensity で中間補間

### 感情補間

intensity < 1.0 の場合、neutral と対象感情の線形補間を行う:

```
style[i] = neutral[i] * (1 - intensity) + emotion[i] * intensity
```

例: emotion=happy, intensity=0.6 の場合
```
neutral = [1.0, 0.0, 0.0, 0.0, 0.0]
happy   = [0.3, 0.7, 0.0, 0.0, 0.0]
result  = [0.58, 0.42, 0.0, 0.0, 0.0]
```

## キャラクターDB (`character_db.py`)

作品ごとにJSON永続化されるキャラクターデータベース。

### CharacterProfile

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `name` | str | キャラクター名 |
| `gender` | str or None | male/female/unknown |
| `age_group` | str or None | child/teen/young_adult/adult/elder |
| `personality` | str or None | 性格の短い説明 |
| `voice_id` | str or None | 割当済みボイスID |
| `voice_locked` | bool | ユーザーによる手動固定 |
| `base_params` | dict | pitch, huskiness, alp, speed のベース値 |

### 自動ボイスパラメータ生成（バッチモード）

VoiSonaモードのバッチ分析時、SLMがキャラクター属性に基づいてパラメータを自動生成:

| キャラ属性 | pitch | huskiness | alp | speed |
|-----------|-------|-----------|-----|-------|
| 男性成人 | -200〜-100 | 5〜10 | -0.3 | 0.9 |
| 女性成人 | 0〜100 | -5〜0 | 0.0 | 1.0 |
| 子供 | 100〜200 | -5 | 0.3 | 1.1 |
| 老人 | -100 | 10〜15 | 0.0 | 0.8 |

## テキスト分割 (`splitter.py`)

`TextSplitter` がテキストをTTS合成に適したチャンクに分割する。

### 分割優先順位

1. シーン区切り（`***`, `---` 等）
2. 段落区切り（空行）
3. 文末（`。！？`）
4. 読点（`、`）
5. 強制分割（`max_chars` 超過時）

### 公開メソッド

| メソッド | 用途 |
|---------|------|
| `split(text) → list[Chunk]` | チャンクに分割（リアルタイム用） |
| `split_sentences(text) → list[str]` | 文単位に分割（バッチ用） |
