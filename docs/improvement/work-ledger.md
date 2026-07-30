# アーキテクチャ改善 作業台帳

この文書を改善作業の実行台帳とする。ロードマップの説明よりも、本台帳の `状態`、`依存`、
`受入条件` を優先する。

## 1. 運用ルール

### 状態

| 状態 | 意味 |
|---|---|
| `backlog` | 依存未完了または詳細化待ち |
| `ready` | 依存解消済みで着手可能 |
| `in_progress` | 担当者が作業中 |
| `blocked` | 外部判断または環境待ち。理由必須 |
| `review` | 実装と自己検証が完了しレビュー待ち |
| `done` | 受入条件と検証が完了 |

### 着手手順

1. `依存` がすべて `done` であることを確認する。
2. 対象タスクの状態を `in_progress` にし、`担当` と `開始日` を記入する。
3. タスクカードに記載した対象ファイルと非対象範囲を確認する。
4. 予定外の公開契約変更が必要なら実装を止め、ADRまたは台帳更新を先に行う。
5. 実装、テスト、Ruffを完了して `review` にする。
6. レビュー後、検証証跡と完了日を記録して `done` にする。
7. 依存が解消した後続タスクを `backlog` から `ready` へ更新する。

### Definition of Done

全タスク共通:

- タスク固有の受入条件を満たす。
- 正常系だけでなく、少なくとも1つの失敗系テストを追加する。
- `uv run python -m pytest -q` が通る。
- `uv run python -m ruff check src/ tests/` が通る。
- 公開動作または設定を変更した場合は対応ドキュメントを更新する。
- unrelatedな整形、名称変更、ファイル移動を混在させない。
- secret、本文、認証付きURLをログ・fixture・成果物へ追加しない。
- 台帳へ検証コマンドと結果を記録する。

### 担当タイプ

`AGENTS.md` のsubagent routingに合わせる。

- `worker`: 実装、修正、テスト、検証
- `explorer`: 読み取り中心の調査・レビュー
- `luna_max`: 複数コンポーネントをまたぐ高難度の調査と実装
- `sol_high`: 重要な状態機械、永続化整合性、サービス設計判断
- `default`: 軽量な文書・一般作業

担当タイプは推奨であり、タスク名ではない。subagentへ委譲する場合は、この列のconfigured
agent typeを指定する。

## 2. 現在の着手候補

WIP上限1の場合の推奨順:

1. `API-002`
2. `OBS-001`

`API-002` と `OBS-001` は変更領域が近いため、先にP0の `API-002` を統合してから
`OBS-001` へ進む。

## 3. タスク一覧

| ID | フェーズ | 優先度 | サイズ | 状態 | 依存 | 並列レーン | 推奨担当 | 担当 | 開始日 | 完了日 |
|---|---|---:|---:|---|---|---|---|---|---|---|
| GOV-001 | R0 | P0 | M | done | — | A | worker | worker | 2026-07-30 | 2026-07-30 |
| ARC-001 | R0 | P1 | M | done | — | D | worker | worker | 2026-07-30 | 2026-07-30 |
| SEC-001 | R0 | P0 | S | done | — | C | worker | worker | 2026-07-30 | 2026-07-30 |
| SEC-002 | R0 | P0 | L | done | — | E | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| REL-001 | R0 | P0 | M | done | — | B | worker | worker | 2026-07-30 | 2026-07-30 |
| REL-002 | R0 | P0 | M | done | — | E | worker | worker | 2026-07-30 | 2026-07-30 |
| CFG-001 | R1 | P1 | L | done | GOV-001, ARC-001 | A | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| CFG-002 | R1 | P1 | M | done | CFG-001 | A | worker | worker | 2026-07-30 | 2026-07-30 |
| RUN-001 | R1 | P1 | L | done | REL-002, ARC-001 | B | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| RUN-002 | R1 | P1 | M | done | RUN-001, CFG-001 | B | worker | worker | 2026-07-30 | 2026-07-30 |
| DOM-001 | R2 | P1 | M | done | ARC-001, GOV-001 | C | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| DOM-002 | R2 | P1 | L | done | DOM-001 | C | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| DOM-003 | R2 | P1 | M | done | DOM-001, RUN-001 | C | worker | worker | 2026-07-30 | 2026-07-30 |
| TST-001 | R2 | P1 | M | done | DOM-002, DOM-003 | D | worker | worker | 2026-07-30 | 2026-07-30 |
| SYN-001 | R3 | P1 | L | done | DOM-001, CFG-001, REL-002 | A | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| SYN-002 | R3 | P1 | L | done | SYN-001, DOM-002 | A | worker | worker | 2026-07-30 | 2026-07-30 |
| SYN-003 | R3 | P1 | L | done | SYN-001, DOM-002 | A | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| SYN-004 | R3 | P0 | L | done | SYN-001, DOM-002, REL-001, RUN-002 | A | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| TST-002 | R3 | P1 | M | done | SYN-002, SYN-003, SYN-004 | D | worker | worker | 2026-07-30 | 2026-07-30 |
| JOB-001 | R4 | P1 | L | done | RUN-002, DOM-003, REL-001 | B | sol_high | root | 2026-07-30 | 2026-07-30 |
| JOB-002 | R4 | P1 | L | done | JOB-001, SYN-004 | B | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| API-001 | R4 | P1 | L | done | CFG-002, SYN-002, JOB-001 | C | luna_max | luna_max | 2026-07-30 | 2026-07-30 |
| API-002 | R4 | P0 | M | ready | API-001, SEC-001, SEC-002, JOB-002 | C | worker | — | — | — |
| OBS-001 | R4 | P1 | M | ready | API-001, JOB-001, SYN-001 | D | worker | — | — | — |
| CLN-001 | R5 | P2 | L | backlog | TST-002, API-002 | A | worker | — | — | — |
| CLN-002 | R5 | P2 | M | backlog | API-001, CLN-001 | C | worker | — | — | — |
| DOC-001 | R5 | P1 | M | backlog | CLN-001, CLN-002, OBS-001 | D | worker | — | — | — |
| OPS-001 | R5 | P1 | M | backlog | DOC-001, TST-002, OBS-001 | D | worker | — | — | — |

## 4. タスクカード

### GOV-001 — CI基準線と配布smoke test

- **目的**: 現在のpytest/RuffをpushとPRで再現可能にし、wheelの設定欠落を検出できる基準線を作る。
- **主な対象**: `.github/workflows/ci.yml`, `pyproject.toml`, `tests/smoke/`
- **実装内容**:
  - Python 3.12で `uv sync --extra dev`、pytest、Ruffを実行する。
  - 通常テストと外部サービス依存E2Eを分離する。
  - wheelをbuildし、一時venvへinstallしてリポジトリ外CWDから `import yomiage` と
    `PipelineConfig.from_yaml()` を試すsmoke testを追加する。
  - 現時点でpackaged config smokeが失敗する場合は、既知失敗として隠さず、CFG-002まで
    個別ジョブまたは明示的なxfailにする。
- **受入条件**:
  - 外部TTS/LLMなしで通常CIが完走する。
  - E2Eは明示markerでのみ実行される。
  - CI失敗時にpytest、lint、packagingのどこで失敗したか判別できる。
- **検証**:
  - `uv run python -m pytest -q -m "not e2e"`
  - `uv run python -m ruff check src/ tests/`
  - CI workflowのローカル相当コマンド
- **非対象**: Provider実機接続、CI secret登録、self-hosted runner。

### ARC-001 — ADRと依存方向テスト

- **目的**: モジュラーモノリス、UtterancePlan、SQLite、依存方向を実装上の制約として固定する。
- **主な対象**: `docs/adr/`, `tests/test_architecture.py`
- **実装内容**:
  - ADR-0001: モジュラーモノリスと目標レイヤー。
  - ADR-0002: UtterancePlanとrun/job identity。
  - ADR-0003: SQLite JobRepository。
  - ASTまたはimport解析によるarchitecture testを追加する。
  - 最初は「新規 `core/application/ports` が外層をimportしない」制約だけを強制し、
    既存違反は許容リストへ明記する。
- **受入条件**:
  - 新規内層からFastAPI、Typer、具象TTS Providerへのimportがテストで失敗する。
  - 既存違反の許容リストに所有タスクIDが付いている。
- **検証**: `uv run python -m pytest tests/test_architecture.py -q`
- **非対象**: 既存モジュールの一括移動。

### SEC-001 — 安全なbind既定値と外部公開ガード

- **目的**: 認証なしAPIがLANへ既定公開される状態を止める。
- **主な対象**: `src/yomiage/cli.py`, 新しいserver settings、CLIテスト
- **実装内容**:
  - `serve --host` の既定を `127.0.0.1` に変更する。
  - 非loopback hostを指定した場合、API token設定を必須にする。
  - 起動ログへbind modeを出すがtokenは出さない。
- **受入条件**:
  - 引数なしでloopbackにbindする。
  - `0.0.0.0` + token未設定は起動前に明確なエラーとなる。
  - `0.0.0.0` + token設定はAPI-002実装前であれば「未対応」として拒否してよい。
- **検証**: CLI runnerを使ったserve設定テスト。
- **非対象**: Bearer認証middleware本体（API-002）。

### SEC-002 — 共通URL取得ポリシーとSSRF防止

- **目的**: 任意URL取得から内部ネットワークへ到達できる経路を閉じる。
- **主な対象**: `src/yomiage/ports/sources.py`, 共通HTTP fetcher、
  `src/yomiage/sources/*.py`, URL policy tests
- **実装内容**:
  - scheme、userinfo、port、hostnameを検証する。
  - DNS解決後IPとredirect先を毎回検証する。
  - loopback/private/link-local/multicast/reservedを既定拒否する。
  - response bytes、redirect回数、timeoutを制限する。
  - allowlistと `allow_private_network` は明示設定時のみ有効にする。
  - 特定サイトSourceを含め、共通fetcherを経由させる。
- **受入条件**:
  - `127.0.0.1`、`::1`、RFC1918、link-local、localhost別名、DNS rebinding相当、
    private redirectが拒否される。
  - 青空、なろう、カクヨム、通常の公開HTTPS URLは許可される。
  - サイズ超過は全本文をメモリへ読み込む前に中断する。
- **検証**:
  - fake resolver/fake HTTP transportを用いたunit test
  - 外部ネットワークなしで実行可能なredirect test
- **非対象**: ユーザー認証、サイトスクレイピング精度。

### REL-001 — Batch成果物の完全性と終了状態

- **目的**: 欠損文を含む成果物が `completed` になることを禁止する。
- **主な対象**: `batch/engine.py`, `batch/job_manager.py`, `batch/concatenator.py`,
  manifest/status tests
- **実装内容**:
  - `complete`, `complete_with_warnings`, `partial`, `failed` の判定関数を一元化する。
  - failed/pendingな発話がある場合、通常のconcatを拒否する。
  - 明示的 `allow_partial` の場合のみ部分結合し、出力名・manifest・API状態へpartialを残す。
  - `run_job()` はsynthesize戻り値を確認してから次フェーズへ進む。
- **受入条件**:
  - 1件失敗するfixtureでジョブが `completed` にならない。
  - 完全成功時の現行出力は維持される。
  - `allow_partial` なしでは欠損full.wavを生成しない。
- **検証**:
  - `tests/test_server_batch.py`
  - 新規batch completion invariant tests
- **非対象**: SQLite永続化、run directory変更。

### REL-002 — atomic JSON/WAV書き込み

- **目的**: 中断時の壊れたmanifest、CharacterDB、Bookmark、cacheを防ぐ。
- **主な対象**: 新しいatomic IO utility、`batch/manifest.py`,
  `reader/character_db.py`, `reader/bookmark.py`, `studio/cache.py`
- **実装内容**:
  - UTF-8 JSON atomic writerを共通化する。
  - 一時ファイルを同一ディレクトリへ作成し `replace()` する。
  - WAVは検証後に最終名へreplaceするためのhelperを用意する。
  - 書き込み失敗時に既存正常ファイルを維持する。
- **受入条件**:
  - replace前の例外を注入しても旧JSONが読み取れる。
  - 一時ファイルが通常終了後に残らない。
  - concurrent write時の方針がテストまたはdocstringで明示される。
- **検証**: 新規 `tests/test_atomic_io.py` と既存永続化テスト。
- **非対象**: 複数プロセスロック、SQLite移行。

### CFG-001 — 型付きSettingsとパス分類

- **目的**: raw dict、CWD依存、空secretを排除する。
- **主な対象**: `src/yomiage/config.py`, `src/yomiage/api/config.py`,
  新しいsettingsモデル、configuration tests
- **実装内容**:
  - tts/provider/llm/batch/server/storage設定をPydanticモデル化する。
  - packaged config、user config、data、output、assets、external binaryのパスを区別する。
  - 設定優先順位を固定する。
  - Providerが有効なときだけ必要secret/実行ファイルを検証する。
  - 旧dictを必要とする箇所には一時adapterを置く。
- **受入条件**:
  - 不正型、未知Provider、無効concurrency、必要secret欠落が起動前エラーになる。
  - application層の新規コードへraw dictを渡さない。
  - 任意CWDで絶対パスへ正規化される。
- **検証**: config precedenceとvalidationのparameterized tests。
- **非対象**: 全既存呼び出しの即時移行、packaged resource同梱（CFG-002）。

### CFG-002 — 標準設定とリソースのパッケージ同梱

- **目的**: wheelインストール後も標準設定、scene params、math dictを利用可能にする。
- **主な対象**: package data、`pyproject.toml`, `importlib.resources` loader、packaging smoke
- **実装内容**:
  - immutableな標準設定をpackage resourceへ配置する。
  - ユーザー編集対象profileとの境界を分ける。
  - wheel内容を検証するテストをGOV-001のsmokeへ統合する。
- **受入条件**:
  - wheel install後、空の一時CWDでconfigをロードできる。
  - scene paramsとmath dictが利用できる。
  - 個人絶対パスがpackaged defaultに存在しない。
- **検証**: build/install smoke、`importlib.resources` unit test。
- **非対象**: ユーザーprofile自動移行。

### RUN-001 — work/plan/run identity分離

- **目的**: 同じURL、異なるProvider、同時ジョブによる成果物衝突を防ぐ。
- **主な対象**: ID生成utility、manifest schema adapter、batch paths
- **実装内容**:
  - `work_id`, `plan_id`, `run_id`, `job_id` の生成規則を実装する。
  - run IDは実行ごとに一意、plan IDは入力と解析設定から安定生成する。
  - 旧URL MD5 work directoryを読み取るcompatibility adapterを追加する。
- **受入条件**:
  - 同じURLの同時2runが異なるディレクトリを使う。
  - 同じ入力・同じ解析設定のplan fingerprintは安定する。
  - Provider変更はplanを再利用できるがrunは分離される。
- **検証**: concurrencyを含むidentity/path tests。
- **非対象**: ArtifactStore抽象、SQLite job。

### RUN-002 — ArtifactStoreとartifact manifest

- **目的**: ファイル配置、検証、公開を一つの境界へ集約する。
- **主な対象**: `ports/artifacts.py`, filesystem adapter, batch/studio integration
- **実装内容**:
  - run directory、temporary artifact、publish、exists、checksumをport化する。
  - artifact manifestへ各Utteranceの状態、checksum、duration、providerを保存する。
  - output pathの直接組み立てを新規applicationコードから排除する。
- **受入条件**:
  - 未検証WAVがfinal artifactとして列挙されない。
  - manifestから欠損/改変ファイルを検出できる。
  - BatchとStudioの少なくとも一方がArtifactStore経由になる。
- **検証**: filesystem adapter integration tests。
- **非対象**: S3等のremote store。

### DOM-001 — UtterancePlanコアモデル

- **目的**: 話者単位の共通ドメイン契約を導入する。
- **主な対象**: `core/models.py`, `core/plan.py`, unit tests
- **実装内容**:
  - `Utterance`, `UtterancePlan`, `VoiceIntent`, `SourceRef`, warning型を定義する。
  - immutableまたは変更箇所が明確なモデルにする。
  - segment type、emotion、intensityのvalidationを実装する。
- **受入条件**:
  - Provider、FastAPI、Typer、filesystem型に依存しない。
  - 直列化して同値に復元できる。
  - schema versionが必須である。
- **検証**: round-trip、invalid value、ordering tests。
- **非対象**: NLP変換、合成。

### DOM-002 — NLP結果からUtterancePlanへの変換

- **目的**: Reader/API/Batchで共通の話者境界を生成する。
- **主な対象**: `application/planning.py`, `nlp/`, legacy adapters
- **実装内容**:
  - TextProcessor、classifier、speaker、scene analyzerの結果をUtteranceへ変換する。
  - 話者またはvoice intentが変わる境界でUtteranceを分割する。
  - TTS文字数制限による分割はその後段へ移す。
  - 現行Batchの2-pass解析結果を同じplanへ変換するadapterを追加する。
- **受入条件**:
  - 「地の文→Aの台詞→Bの台詞」が3つ以上の適切なUtteranceになる。
  - Reader/API/Batch adapterでorderとspeakerが一致する。
  - LLM unavailable時もrule-based planを生成できる。
- **検証**: deterministic fake LLMを使うcross-entry tests。
- **非対象**: NLP精度向上、prompt変更。

### DOM-003 — Plan fingerprintとschema migration

- **目的**: plan再利用の可否を機械的に判定し、旧manifestを安全に読む。
- **主な対象**: plan serializer/migrator、manifest compatibility tests
- **実装内容**:
  - source/config fingerprintをcanonical JSONから生成する。
  - schema migration registryを用意する。
  - 旧BatchManifestをplanへimportする。
- **受入条件**:
  - dict順序や無関係な実行設定でplan IDが変化しない。
  - 解析結果に影響する設定変更ではplan IDが変わる。
  - 未知の将来schemaは黙って読み飛ばさず明示エラーになる。
- **検証**: migration fixtures、fingerprint property tests。
- **非対象**: 旧成果物の一括書き換え。

### TST-001 — UtterancePlan golden/contract test

- **目的**: コアの商品価値である話者切替を回帰テストとして固定する。
- **主な対象**: `tests/fixtures/plans/`, cross-entry planning tests
- **実装内容**:
  - narration、dialogue、thought、scene break、複数話者、長文を含むfixtureを用意する。
  - Reader/API/Batch adapterが期待planを生成することを比較する。
- **受入条件**:
  - 旧「dominant segmentをチャンク全体へ適用」方式へ戻すとテストが失敗する。
  - fixtureは外部LLMへ依存しない。
- **検証**: planning/golden test suite。
- **非対象**: 音声波形の主観評価。

### SYN-001 — 統一SynthesisServiceとProvider port

- **目的**: retry、fallback、cache、metrics、artifact検証を一元化する。
- **主な対象**: `ports/tts.py`, `application/synthesis.py`, provider adapters
- **実装内容**:
  - typed request/result/health/voice契約を定義する。
  - fallback時のVoiceIntent再解決を実装する。
  - retry対象エラーと非retryエラーを分類する。
  - timeout、cancellation、Provider concurrencyを実装する。
  - bytes出力とfile artifact出力を同一result型で扱う。
- **受入条件**:
  - fake primary failure時にfallbackでvoiceを再解決する。
  - fallback、retry、cache hitが結果メタデータへ残る。
  - Provider lifecycleが明示的にcloseされる。
- **検証**: fake Providerを使うservice unit/integration tests。
- **非対象**: 各入口の移行。

### SYN-002 — SDK/TTSBridge/Studio移行

- **目的**: 公開SDKとStudioをSynthesisServiceへ移行する。
- **主な対象**: `api/bridge.py`, `api/pipeline.py`, `studio/`
- **実装内容**:
  - 公開メソッド署名を維持するcompatibility facadeを置く。
  - Studioのdirect Provider constructionをbootstrap経由へ変更する。
  - Studio cacheを共通cache/artifact metadataへ接続する。
- **受入条件**:
  - 既存SDK/Studioテストが維持される。
  - 新規コードから具象Providerを直接constructしない。
  - SDKとStudioで同じfallback/error semanticsになる。
- **検証**: `tests/test_api_bridge.py`, `tests/test_api_pipeline.py`,
  `tests/test_studio_engine.py`
- **非対象**: Reader、Batch移行。

### SYN-003 — ReadingEngineのUtterance/SynthesisService移行

- **目的**: dominant segment方式を廃止し、発話単位でリアルタイム再生する。
- **主な対象**: `reader/engine.py`, playback queue、bookmark adapter
- **実装内容**:
  - UtterancePlanを順に合成・再生する。
  - lookaheadはUtterance順序を維持する。
  - bookmarkをchunk indexからsource/utterance参照へ拡張する。
  - VoiSona slow/file modeもSynthesisService契約へ寄せる。
- **受入条件**:
  - 1入力内の複数話者が別パラメータで合成される。
  - pause/stop/resumeで孤児taskが残らない。
  - lookaheadしても再生順が変わらない。
- **検証**: fake providerによるasync ordering/cancellation tests。
- **非対象**: UI、再生デバイス追加。

### SYN-004 — BatchEngineのSynthesisService移行

- **目的**: Batch専用Provider実装を共通サービスへ寄せ、完全性保証を統合する。
- **主な対象**: `batch/engine.py`, `batch/*_synth.py`, ArtifactStore
- **実装内容**:
  - plan内Utteranceをrun artifactへ合成する。
  - resume/retryをartifact manifest基準にする。
  - VoiSona file destinationをProvider adapterで扱う。
  - REL-001のpartial判定をJob/Artifact結果へ接続する。
- **受入条件**:
  - 3 Providerすべてが同じapplication service経由になる。
  - failed Utteranceをretryしても成功済みartifactを再生成しない。
  - completeでないrunは通常concat/videoへ進まない。
- **検証**: Provider fake別batch integration tests、resume tests。
- **非対象**: 旧synthesizer削除（CLN-001）。

### TST-002 — Provider契約と全入口の合成契約テスト

- **目的**: Provider追加・修正で入口ごとの挙動が分岐しないよう保証する。
- **主な対象**: shared contract test suite、fake adapters、entrypoint matrix
- **実装内容**:
  - health/list/synthesize/file/timeout/cancel/closeの共通contractを作る。
  - CLI相当、SDK、Reader、Batch、Studioをfake Providerで実行する。
- **受入条件**:
  - 各Provider adapterが同じcontract suiteを通る。
  - fallback、partial、cancellationの期待値が入口間で一致する。
- **検証**: `tests/contracts/` 全体。
- **非対象**: 実機の音質比較。

### JOB-001 — Job状態機械とSQLite JobRepository

- **目的**: ジョブ状態を任意文字列とプロセスメモリから切り離す。
- **主な対象**: `core/jobs.py`, `ports/repositories.py`,
  SQLite adapter、migration tests
- **実装内容**:
  - 状態、許可遷移、progress、error categoryをモデル化する。
  - SQLite schemaとmigration versionを作る。
  - create/get/list/compare-and-set transitionを実装する。
  - event-loop monotonic timeではなく永続可能なUTC時刻を保存する。
- **受入条件**:
  - 不正遷移が拒否される。
  - 2実行主体の同時transitionでlost updateが起きない。
  - repository再生成後もジョブを取得できる。
- **検証**: transition unit tests、SQLite concurrency/reopen tests。
- **非対象**: 分散DB、Redis、ユーザー管理。

### JOB-002 — TaskSupervisor、キャンセル、再起動回収

- **目的**: detached `create_task` を廃止し、停止・再開・shutdownを管理する。
- **主な対象**: `application/jobs.py`, FastAPI lifespan integration
- **実装内容**:
  - active task registryとbounded queueを実装する。
  - cancel requestをJobRepositoryへ記録する。
  - shutdown時に受付停止、猶予、cancel、状態保存を行う。
  - 起動時に実行中だったjobを `interrupted` として回収する。
- **受入条件**:
  - task例外が未回収にならない。
  - cancel後にProvider/HTTP session/一時fileが残らない。
  - restart fixtureでinterrupted jobを検出できる。
- **検証**: async cancellation、shutdown、restart integration tests。
- **非対象**: 複数ホストworker。

### API-001 — FastAPI app factoryとdependency injection

- **目的**: モジュールグローバルEngine/JobManager/Configを除去する。
- **主な対象**: `server.py`, `interfaces/http/`, `bootstrap.py`
- **実装内容**:
  - `create_app(settings, dependencies)` を導入する。
  - lifespanでbootstrap/closeする。
  - endpointはUseCase/JobServiceへ依存する。
  - `ReadRequest.provider` を実際に扱うか、公開DTOから削除する。
- **受入条件**:
  - テストがmonkeypatchしたmodule globalに依存しない。
  - 2つのapp instanceを別設定で同一プロセスに生成できる。
  - shutdownでProvider、session、TaskSupervisorをcloseする。
- **検証**: app factory/lifespan tests。
- **非対象**: 認証middleware、quota（API-002）。

### API-002 — API認証、quota、入力制限

- **目的**: 非loopback公開を最小限安全にする。
- **主な対象**: HTTP middleware/dependencies、server settings、API tests
- **実装内容**:
  - Bearer token認証を実装する。
  - constant-time token比較を使う。
  - body/text/queue/active jobs/client rateの上限を適用する。
  - URL endpointへSEC-002 policyを必須適用する。
  - 413/401/403/429を一貫したerror DTOで返す。
- **受入条件**:
  - 非loopback bind + tokenでのみ外部公開できる。
  - protected endpointはtokenなしで拒否される。
  - quota超過時にジョブや一時成果物を作らない。
- **検証**: FastAPI TestClient/ASGI transportによるauth・limit tests。
- **非対象**: OAuth、複数ユーザー、課金。

### OBS-001 — Liveness、Readiness、Metrics

- **目的**: プロセス生存と外部依存の利用可否を区別する。
- **主な対象**: health use case、HTTP endpoints、metrics collector
- **実装内容**:
  - `/health/live` と `/health/ready` を分ける。
  - Provider、LLM、ffmpeg、storage、SQLiteの状態をreadinessへ含める。
  - job state、queue、fallback、partial、error categoryを計測する。
  - 本文やsecretをlabel/payloadへ含めない。
- **受入条件**:
  - TTS停止中もlivenessは200、readinessは非readyを返す。
  - metricsが全入口のSynthesisServiceを反映する。
  - restart後の永続job countとprocess metricsの意味が文書化される。
- **検証**: fake health dependencies、metrics integration tests。
- **非対象**: Prometheus serverの別プロセス化、分散tracing。

### CLN-001 — 重複TTS/NLP経路の撤去

- **目的**: 移行期間中のcompatibility codeを整理し、再分岐を防ぐ。
- **主な対象**: `tts/manager.py`, `api/bridge.py`, `batch/*_synth.py`,
  重複factory/constructor
- **実装内容**:
  - 使用箇所ゼロを確認して旧実装を削除または薄いfacadeにする。
  - `hasattr(synthesize_to_file)` 等の暗黙capability判定を除去する。
  - architecture testの許容リストを縮小する。
- **受入条件**:
  - Provider構築はbootstrap/adapter factoryに一元化される。
  - fallback/retry/cache実装が1箇所だけになる。
  - `rg`で禁止construct/importが検出されない。
- **検証**: full tests、architecture tests、CLI/SDK smoke。
- **非対象**: CLIファイル分割。

### CLN-002 — CLIモジュール分割

- **目的**: 1000行超のcomposition/command混在をinterface層へ整理する。
- **主な対象**: `interfaces/cli/`, `cli.py`
- **実装内容**:
  - reader、batch、studio、tuning、news/slack、server commandを分割する。
  - commandはbootstrapしたUseCaseを呼ぶだけにする。
  - entrypoint `yomiage.cli:app` は互換維持する。
- **受入条件**:
  - 既存コマンド名と主要optionが維持される。
  - CLI command moduleが具象Providerをconstructしない。
  - `--help` smokeが全subcommandで通る。
- **検証**: Typer CliRunner command matrix。
- **非対象**: CLI名称再設計、新機能。

### DOC-001 — ドキュメント同期と移行手順

- **目的**: 実装済み構造を唯一の現行説明として文書化する。
- **主な対象**: README、architecture、configuration、API、batch、SDK docs、ADR index
- **実装内容**:
  - 存在しないモジュール、古いコマンド、未実装HEMS/MQTT記述を整理する。
  - local-onlyとpublic serverの起動手順を分ける。
  - 旧manifest/artifactからの移行、rollback、backupを記載する。
  - 実装済みタスクIDとADRを相互リンクする。
- **受入条件**:
  - 文書内のコマンドをsmoke testまたはhelp出力と照合できる。
  - architecture treeが実ファイルと一致する。
  - security defaultとpartial semanticsがAPI/CLI文書に明記される。
- **検証**: markdown link check、command snippet smoke。
- **非対象**: ユーザー向けチュートリアル全面改稿。

### OPS-001 — 外部E2Eとリリースチェックリスト

- **目的**: fakeだけでは検出できないProvider・ffmpeg・VM境界をリリース前に確認する。
- **主な対象**: `tests/e2e/`, `docs/release-checklist.md`
- **実装内容**:
  - VOICEVOX、VoiSona、VOICEPEAK、Ollamaを個別markerへ分ける。
  - ライセンス、必要secret、接続先、成果物削除方針を明記する。
  - 代表入力でplan→synthesis→concat→任意videoを確認する。
  - partial/fallback/cancelの手動障害注入手順を用意する。
- **受入条件**:
  - 利用可能なProviderだけ選択してE2E実行できる。
  - VOICEPEAKを通常public CIで無断実行しない。
  - リリース判定にconfig packaging、migration backup、security defaultが含まれる。
- **検証**: 各markerのcollect確認と、利用可能環境での実行証跡。
- **非対象**: 常設self-hosted runner構築。

## 5. 検証証跡

### CFG-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: luna_max（実装）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_config_settings.py -q` — 14 passed
  - `PYTHONPATH=src .venv/bin/python -m pytest -q -m 'not e2e' --ignore=tests/test_server_batch.py --ignore=tests/test_server_metrics.py` — 462 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` — pass
  - `git diff --check` — pass
- Result: 型付きSettings、固定した設定優先順位、パス分類とlegacy dict adapterを導入した。有効Providerのみsecret/実行ファイルを検証する。
- Remaining risks: wheelへ標準設定を同梱する処理はCFG-002で実装する。

### DOM-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: luna_max（実装）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_core_models.py tests/test_core_plan.py tests/test_architecture.py -q` — 16 passed
  - 統合非E2E suite — 462 passed, 2 skipped, 6 deselected
  - Ruff / `git diff --check` — pass
- Result: Provider非依存かつimmutableなUtterancePlanコアモデルと直列化・validation契約を導入した。
- Remaining risks: NLP結果からの変換はDOM-002で実装する。

### RUN-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: luna_max（実装）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_batch_identity.py tests/test_batch_completion.py tests/test_architecture.py -q` — 15 passed
  - 統合非E2E suite — 462 passed, 2 skipped, 6 deselected
  - Ruff / `git diff --check` — pass
- Result: work/plan/run/job identity、new runディレクトリ、旧manifest/path読み取り互換を導入した。
- Remaining risks: ArtifactStore抽象と検証済みartifact公開はRUN-002で実装する。

### ARC-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装）、root（レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_architecture.py -q` — 4 passed
  - `.venv/bin/ruff check src/ tests/` — pass
  - `git diff --check` — pass
- Result: ADR-0001〜0003を追加し、新規`core`/`application`/`ports`からframework・具象TTS Providerへの依存をAST検査で禁止した。既存外層の例外には移行担当タスクIDを付与した。
- Manual verification: 不正なinner layer importを一時fixtureへ投入し、検出される失敗系を確認済み。
- Remaining risks: legacy allowlistの縮小はSYN/API/CLI移行タスクで行う。

### SEC-002 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: luna_max（実装）、root（レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_url_policy.py tests/test_sources.py tests/test_generic_web.py -q` — 29 passed
  - `.venv/bin/ruff check src/ tests/` — pass
  - `git diff --check` — pass
- Result: 共通URL policy/fetcherを導入し、scheme、userinfo、port、hostname、DNS解決IPおよびredirect先を都度検証する。内部・予約済みネットワークは既定拒否し、応答サイズ、redirect回数、timeoutを制限する。
- Manual verification: fake resolver/transportによりlocalhost、private IP、DNS rebinding、private redirectを外部ネットワークなしで検証済み。
- Remaining risks: allowlist/`allow_private_network` は明示構成時のみ有効であり、構成モデルへの統合はCFG-001で型付きSettingsへ移行する。

### SEC-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装）、root（レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_cli_serve.py -q` — 3 passed
  - `.venv/bin/ruff check src/ tests/` — pass
  - `git diff --check` — pass
- Result: `yomiage serve` は既定で `127.0.0.1` にbindする。非loopback hostはtoken未設定時に明瞭に拒否し、tokenが設定済みでも認証middleware未実装（API-002）のため公開を拒否する。token値をログまたはエラーへ出さない。
- Manual verification: loopback/非loopbackのCLI runner経路とbind modeログをレビュー済み。
- Remaining risks: Bearer認証、quota、入力制限はAPI-002で実装する。

### REL-002 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装）、root（レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_atomic_io.py tests/test_character_db.py tests/test_studio_cache.py -q` — 15 passed
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_atomic_io.py tests/test_character_db.py tests/test_studio_cache.py tests/test_studio_engine.py tests/test_api_pipeline.py tests/test_api_bridge.py tests/test_batch_manifest_duration.py -q` — 68 passed
  - `.venv/bin/ruff check src/ tests/` — pass
  - `git diff --check` — pass
- Result: JSONは同一ディレクトリの一時ファイル、flush/fsync、`Path.replace()`で保存し、既存の正常ファイルを失敗時も保持する。WAV等の音声は検証成功後に公開名へatomic replaceする。並行書込はlast-writer-winsであることをutility docstringに明記した。
- Manual verification: ffmpegによるWAV/MP3/FLAC出力の公開順序を確認済み。
- Remaining risks: プロセス間ロックは非対象であり、最終ライターが勝つ方針である。

### REL-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装）、root（レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_batch_completion.py -q` — 6 passed
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_batch_completion.py tests/test_server_batch.py -q` — `httpx` 未導入の既存ローカルvenvによりserver test収集不可
  - `.venv/bin/ruff check src/ tests/` — pass
  - `git diff --check` — pass
- Result: 合成状態を一元判定し、failed/pending/欠損音声を含む通常の結合を拒否する。`allow_partial=True` 時だけ `.partial.wav` を出力し、manifest、API結果、SSE終端状態へ `partial` を残す。
- Manual verification: JobManagerが `partial` と `failed` を `completed` へ変換しないこと、動画生成をpartial出力で抑止することをレビュー済み。
- Remaining risks: 2026-07-30の統合検証でserver batchを含む非E2E全pytest（491 passed）を再実行済み。実Provider接続は非対象。

### GOV-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装）、root（レビュー）
- Commands:
  - `.venv/bin/ruff check src/ tests/` — pass
  - `.venv/bin/python -m pytest tests/smoke -q -m packaging` — pass（wheel用Python未指定のため2 skipped）
  - `git diff --check` — pass
  - `UV_CACHE_DIR=/tmp/yomiage-uv-cache uv run python -m pytest -q -m 'not e2e'` — 依存取得時のDNS制限により未実行
- Result: push/PRではunit、Ruff、wheel packaging smokeを実行し、外部サービスE2Eは手動dispatchかつ明示入力時のみ実行するCIを追加した。wheelの標準設定ロードはCFG-002完了までstrict xfailとして可視化した。
- Manual verification: CI workflow、packaging marker、隔離Python（`-I`）とリポジトリ外CWDをレビュー済み。
- Remaining risks: 2026-07-30に依存同期後の非E2E全pytestと実wheel build/install smokeをローカル実行済み。GitHub Actions上の初回実行結果は引き続き確認が必要。

### CFG-002 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装・レビュー）、root（統合レビュー）
- Commands:
  - `UV_CACHE_DIR=/tmp/yomiage-uv-cache uv build --wheel` — pass
  - `YOMIAGE_SMOKE_PYTHON=/tmp/yomiage-fulltest-venv/bin/python PYTHONPATH=src .venv/bin/python -m pytest tests/smoke/test_packaged_install.py -q -m packaging` — 2 passed
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_packaged_resources.py tests/test_config_settings.py -q` — 23 passed
  - 非E2E全pytest — 491 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: 標準設定4 YAMLをwheelへ同梱し、`importlib.resources`からCWD非依存で読み込む。ユーザー設定・編集対象profileはpackage resourceから分離した。
- Manual verification: wheelを一時venvへ再インストールし、隔離Python・リポジトリ外CWDからimport、標準設定、scene params、math dictを読み込んだ。
- Remaining risks: ユーザーprofileの自動移行はタスクの非対象。

### RUN-002 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装・レビュー）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_artifact_store.py -q` — 4 passed
  - 非E2E全pytest — 491 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: filesystem ArtifactStore、atomic publish、artifact manifest、checksum・duration・provider記録を導入し、Batchをcanonical run storeへ接続した。未検証WAVはfinal artifactとして列挙しない。
- Manual verification: 欠損・改変成果物の検出、invalid WAVの公開拒否、一時成果物の後始末、run directory外へのpath traversal拒否をレビューした。
- Remaining risks: Studio統合は受入条件上必須ではなく、後続の合成入口移行で共通storeへ接続する。

### DOM-002 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: luna_max（実装・レビュー）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_planning.py tests/test_api_analyzer.py -q` — 20 passed
  - planning/API/config/artifact関連統合テスト — 59 passed
  - 非E2E全pytest — 491 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: Reader/API/旧Batch結果を共通UtterancePlanへ変換するadapterを導入した。計画用NLP経路ではTTS文字数分割を後段へ送り、長い引用発話でも話者境界を維持する。LLM利用不可・生成失敗時はrule-based planと警告を返す。
- Manual verification: 地の文・太郎・花子の順序と話者一致、500文字の引用発話、旧AnalysisResult、2-pass manifest変換をレビューした。
- Remaining risks: ReadingEngine本体のUtterancePlan実行はSYN-003、NLP精度とprompt変更は非対象。

### DOM-003 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装・レビュー）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_dom003_migration.py tests/test_core_plan.py tests/test_batch_identity.py tests/test_planning.py tests/test_architecture.py -q` — 24 passed
  - 非E2E全pytest — 506 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: canonical JSONによる解析設定fingerprint、明示登録型schema migration registry、Plan serializer、旧BatchManifestのread-only importを追加した。未知future schemaは明示エラーにする。
- Manual verification: mapping順序・実行設定変更ではplan IDが不変、解析設定変更では変化すること、旧manifestを破壊せずcanonical planへ保存・復元できることを確認した。
- Remaining risks: 現在のPlan schemaはv1のみ。将来version導入時はmigration stepの登録とfixture追加が必要。

### SYN-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: luna_max（実装・レビュー）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_synthesis_service.py tests/test_artifact_store.py tests/test_architecture.py -q` — 17 passed
  - 非E2E全pytest — 506 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: typed Provider portと統一SynthesisServiceを導入し、retry分類、fallback時voice再解決、cache metadata、timeout/cancellation、Provider concurrency、ArtifactStore公開、明示closeを一元化した。
- Manual verification: primaryを2回失敗させたfallback、cache hit、timeout、task cancel、同時実行上限、bytes/file-mode artifactの検証済み公開をfake Providerで確認した。
- Remaining risks: SDK/Studio、Reader、Batchの入口移行はSYN-002〜SYN-004で実施する。

### TST-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装・レビュー）、root（統合レビュー）
- Commands:
  - planning golden/DOM-003関連テスト — 15 passed
  - 非E2E全pytest — 525 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: 複数話者・scene break・thoughtを含む混在fixtureと長い引用発話fixtureを追加し、Reader/API/Batch adapterのcanonical plan投影を固定した。
- Manual verification: 外部LLMなしで全入口の発話順・種別・話者・source spanが一致し、旧dominant segment方式では失敗する境界をレビューした。
- Remaining risks: 音声波形の主観評価と実Provider E2Eは対象外。

### SYN-004 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: luna_max（実装・レビュー）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_batch_synthesis_service.py tests/test_batch_completion.py tests/test_batch_identity.py tests/test_artifact_store.py tests/test_architecture.py tests/test_video.py -q` — 73 passed
  - 非E2E全pytest — 525 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: canonical Batch runを3 Provider共通のSynthesisServiceへ移行し、artifact manifest基準のresume/retry、VoiSona file destination変換、partial時のconcat/video抑止を実装した。
- Manual verification: 成功済みartifactを再生成しない再開、失敗分のみのretry、欠損・改変artifactのpending復帰、非発話項目のskipped、所有serviceのcloseをfake Providerで確認した。
- Remaining risks: 実Provider/Windows VM E2EはOPS-001、旧専用synthesizer削除はCLN-001で扱う。

### JOB-001 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: root（実装・統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_job_repository.py tests/test_architecture.py -q` — 11 passed
  - 非E2E全pytest — 525 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: Job状態機械、progress/error category、repository port、schema version付きSQLite永続化、revisionとstateによるcompare-and-set transitionを追加した。
- Manual verification: 不正遷移拒否、UTC時刻の永続化、repository再生成後の取得、2接続の競合で一方だけが成功すること、保存値からの秘密・全文URL除外を確認した。
- Remaining risks: active taskの監督・shutdown・再起動回収はJOB-002で扱う。SQLiteは単一ホスト運用を前提とする。

### SYN-002 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: worker（実装・レビュー）、root（統合レビュー）
- Commands:
  - SDK/API/Studio/architecture対象テスト — worker 74 passed、root 56 passed
  - 非E2E全pytest — 536 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: TTSBridge、Pipeline、Studioを共通SynthesisServiceへ移行し、Provider構築をcomposition rootへ集約した。公開SDK署名、fallback/error/cache/hooks/metrics/file synthesis互換を維持した。
- Manual verification: fallback先でProvider固有voice IDを再利用せず再解決すること、注入serviceをcloseしないこと、注入時にStudioが未使用Providerを構築しないことをfake Providerで確認した。
- Remaining risks: 実VOICEVOX/VoiSona/VOICEPEAKとVM mountのE2EはOPS-001。旧SynthCache/provider factoryは互換層として残る。

### SYN-003 検証証跡

- Commit/PR: 未作成（作業ツリー上の変更）
- 実行日: 2026-07-30
- 担当: luna_max（実装・レビュー）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/test_reader_plan.py tests/test_plan_golden.py tests/test_planning.py tests/test_architecture.py -q` — 21 passed
  - 非E2E全pytest — 536 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: ReadingEngineの通常入口をUtterancePlan/SynthesisServiceへ移行し、順序保持lookahead queue、発話参照bookmark、pause/stop/resume時のtask回収を実装した。
- Manual verification: 複数話者の別request化、合成完了順に依存しない再生順、同一loop・別threadからのstop、未知utterance IDのorder fallback、旧bookmark JSON互換、service所有権を確認した。
- Remaining risks: 実Provider/Windows VM E2EはOPS-001。長い単一UtteranceのTTS制限向け後段分割は今後の検討事項。

### TST-002 検証証跡

- Commit/PR: 本waveのローカルコミット
- 実行日: 2026-07-30
- 担当: worker（実装・レビュー）、root（統合レビュー）
- Commands:
  - `PYTHONPATH=src .venv/bin/python -m pytest tests/contracts -q` — 25 passed
  - Provider/Batch/Reader/SDK/Studio関連テスト — worker 61 passed、root 53 passed
  - 非E2E全pytest — 569 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: 新port、legacy bytes、VoiSona file-only adapterへ共通Provider契約を適用し、CLI・SDK・Reader・Batch・Studioのfallback/partial/cancel matrixを追加した。
- Manual verification: canonical BatchでProvider pinがfallbackを無効化する不整合を修正し、partial時のconcat/video抑止、cancel時のfallback未実行・task回収・外部service非closeを確認した。
- Remaining risks: 実Provider接続と音質比較はOPS-001で扱う。

### JOB-002 検証証跡

- Commit/PR: 本waveのローカルコミット
- 実行日: 2026-07-30
- 担当: luna_max（実装・レビュー）、root（統合レビュー）
- Commands:
  - JOB/repository/server/batch/architecture関連テスト — 44 passed
  - 非E2E全pytest — 569 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: 有限queueとactive task registryを持つTaskSupervisorを追加し、CAS遷移、cancel cleanup待機、shutdown、再起動回収・resume、FastAPI lifespanとread/batch入口を接続した。
- Manual verification: task例外回収、queue上限、RUNNING→INTERRUPTED、QUEUED→CANCELLED、failed Batch→FAILED、shutdown時のcancel request・Provider・repository close順を確認した。
- Remaining risks: SQLite単一ホスト前提。lifespan外compat taskとReadingEngine内部の旧task生成は後続API/CLNタスクで整理する。

### API-001 検証証跡

- Commit/PR: 本waveのローカルコミット
- 実行日: 2026-07-30
- 担当: luna_max（実装・レビュー）、root（統合レビュー）
- Commands:
  - factory/server/lifespan/architecture関連テスト — root 30 passed
  - 非E2E全pytest — 573 passed, 2 skipped, 6 deselected
  - `.venv/bin/ruff check src/ tests/` / `git diff --check` — pass
- Result: `create_app(settings, dependencies)`、ServerDependencies、per-app runtime/lifespanを導入し、全routeをapp state依存へ移行した。具象Reader Provider構築はbootstrapへ移した。
- Manual verification: 別設定の2 app同時分離、resourceの一度だけのclose、startup途中失敗のcleanup、既存serverテストのglobal monkeypatch除去、legacy `app`互換を確認した。
- Remaining risks: 既存route/SSE互換のためendpointはReadingEngine/BatchJobManager facadeへ直接依存する。専用UseCase化は後続整理で扱う。

タスク完了時に以下の形式で追記する。

```text
### <TASK-ID> 検証証跡

- Commit/PR:
- 実行日:
- 担当:
- Commands:
  - `uv run python -m pytest ...`
  - `uv run python -m ruff check ...`
- Result:
- Manual verification:
- Remaining risks:
```

## 6. ブロッカー記録

ブロック時はタスク表を `blocked` に更新し、ここへ追記する。

```text
### <TASK-ID> blocker

- 発生日:
- 条件:
- 3回以上再現した確認内容:
- 必要なユーザー判断または外部状態:
- 安全に継続できる代替作業:
```

現在のブロッカー: なし。

## 7. ワーカー引き渡しテンプレート

タスクをsubagentまたは別担当者へ渡すときは、次の内容をそのまま使用する。

```text
Task: <TASK-ID> — <title>

Authoritative instructions:
- AGENTS.md
- docs/improvement/architecture-plan.md
- docs/improvement/work-ledger.md の <TASK-ID> タスクカード

Scope:
- タスクカード記載の主な対象だけを変更する。
- 非対象に記載されたリファクタや機能追加を行わない。
- unrelatedな既存変更を保持する。

Required outcome:
- タスク固有の受入条件をすべて満たす。
- 正常系と失敗系のテストを追加する。
- 対象テスト、全pytest、Ruffを実行する。
- 実行コマンドと結果、残存リスクを報告する。
- 作業台帳の状態や担当欄は、root担当者から更新指示がある場合だけ変更する。

Stop conditions:
- 公開API、manifest schema、出力互換性について計画外の判断が必要。
- secret、外部ライセンス、公開ネットワークへの新しい権限が必要。
- 依存タスクの成果が存在しない。

Do not:
- マイクロサービス、Redis/Celery、新Provider等を追加しない。
- 既存テストを理由なく削除・skipしない。
- 欠損や例外を成功として握りつぶさない。
```
