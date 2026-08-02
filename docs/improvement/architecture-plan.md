# アーキテクチャ改善計画

## 1. 設計方針

目標は、現在の機能別モジュールを一括で書き換えることではない。既存CLIと成果物を維持しながら、
依存方向と実行ポリシーを段階的に一つへ寄せる。

採用する原則:

1. **モジュラーモノリスを維持する**
   プロセス分割より先に、プロセス内の境界と契約を明確にする。
2. **発話計画と実行を分離する**
   NLP結果から「何を、誰が、どう読むか」を確定した後、TTSへ渡す。
3. **入口でポリシーを変えない**
   CLI、REST、SDK、Studioで同じ合成ポリシーを利用する。
4. **部分成功を正常終了にしない**
   欠損、代替Provider使用、再試行、skipを成果物メタデータへ残す。
5. **既定値は安全側に倒す**
   ローカルbind、外向きURL制限、有限キュー、有限入力を既定とする。
6. **移行中はadapterを使う**
   旧manifestや旧Provider実装を直ちに削除せず、新契約への変換層を置く。

## 2. 目標レイヤー

```text
┌─────────────────────────────────────────────────────────┐
│ Interfaces                                               │
│ CLI / FastAPI / Python SDK                               │
└──────────────────────────┬──────────────────────────────┘
                           │ command / query DTO
┌──────────────────────────▼──────────────────────────────┐
│ Application                                              │
│ PlanUseCase / ReadUseCase / BatchUseCase / RenderUseCase │
│ SynthesisService / JobService / TaskSupervisor           │
└──────────────┬─────────────────────────────┬─────────────┘
               │ domain model                │ ports
┌──────────────▼─────────────┐   ┌───────────▼─────────────┐
│ Core                       │   │ Ports                    │
│ UtterancePlan              │   │ TTSProviderPort          │
│ Utterance / VoiceIntent    │   │ LLMPort / SourcePort     │
│ Job / Artifact metadata    │   │ JobRepo / ArtifactStore  │
└────────────────────────────┘   └───────────┬─────────────┘
                                             │
                                 ┌───────────▼─────────────┐
                                 │ Adapters                 │
                                 │ VoiSona / VOICEVOX       │
                                 │ VOICEPEAK / Ollama       │
                                 │ Web sources / SQLite     │
                                 │ Filesystem / ffmpeg      │
                                 └─────────────────────────┘
```

依存方向は常に内向きとする。

- `core` は `application`、`interfaces`、`adapters` をimportしない。
- `application` は具象ProviderやFastAPI/Typerをimportしない。
- `adapters` はportを実装する。
- `interfaces` はapplication use caseを呼び出すだけにする。
- 例外型、メトリクスイベント、設定DTOは `api` パッケージを共有層として使わない。

## 3. 段階的なパッケージ配置

新規コードは次の配置を基準とする。既存モジュールの一括移動は行わず、利用箇所を移行してから
旧コードを削除する。

```text
src/yomiage/
├── core/
│   ├── models.py             # Utterance, VoiceIntent, ArtifactRef
│   ├── plan.py               # UtterancePlan + schema
│   ├── jobs.py               # JobState, Job, transition
│   └── errors.py             # ドメイン/アプリケーション共通例外
├── application/
│   ├── planning.py           # NLP結果→UtterancePlan
│   ├── synthesis.py          # SynthesisService
│   ├── reading.py            # realtime use case
│   ├── batching.py           # batch use case
│   └── jobs.py               # JobService / TaskSupervisor
├── ports/
│   ├── tts.py
│   ├── llm.py
│   ├── sources.py
│   ├── repositories.py
│   └── artifacts.py
├── adapters/
│   ├── tts/
│   ├── llm/
│   ├── sources/
│   ├── persistence/
│   └── media/
└── interfaces/
    ├── cli/
    ├── http/
    └── sdk/
```

既存の `tts/`、`nlp/`、`sources/`、`video/` は当面adapter実装として再利用する。
物理移動よりも、portへの適合と依存逆転を優先する。

## 4. コアモデル

### 4.1 Utterance

`Utterance` は1つの話者・1つの音声意図で連続して読める最小単位とする。

必須フィールド:

| フィールド | 内容 |
|---|---|
| `id` | plan内で安定したID |
| `order` | 再生順 |
| `text` | 正規化済みだがProvider非依存の本文 |
| `segment_type` | narration/dialogue/thought/scene_break |
| `speaker_id` | 正規化された話者ID、地の文はnull可 |
| `scene` | シーン分類 |
| `emotion` | 感情 |
| `intensity` | 0.0〜1.0 |
| `voice_intent` | engine非依存の音声意図 |
| `source_ref` | 章、原文位置、URL等への参照 |

`TextSplitter` の最大文字数はUtterance生成後に適用する。長いUtteranceをTTS制限で分割しても、
同じ `speaker_id` と `voice_intent` を維持する。

### 4.2 VoiceIntent

`VoiceIntent` はProvider固有voice IDを含めない。

例:

- archetype
- gender/age hint
- speaking rate ratio
- pitch/volume等の正規化された相対値
- emotion/intensity
- preferred voice profile

Provider固有のvoice ID、style ID、VoiSonaのstyle weights、VOICEPEAK narratorはadapter側の
`VoiceResolver` が解決する。fallback時にprimaryのvoice IDを別Providerへ渡してはならない。

### 4.3 UtterancePlan

必須メタデータ:

- `schema_version`
- `plan_id`
- `source_fingerprint`
- `config_fingerprint`
- `created_at`
- `utterances`
- `warnings`

planはProviderを変更して再合成できる。合成結果やファイルパスはplan本体へ直接書き込まず、
run単位のartifact manifestへ分離する。

## 5. 合成サービス

### 5.1 責務

`SynthesisService` は全入口が共有する唯一の合成ポリシー実装とする。

- Provider選択
- voice解決
- retry/backoff
- fallback
- cache
- concurrency制御
- timeout/cancellation
- hooks/metrics
- 結果検証
- artifact書き込み

Providerは「1要求を合成する」責務だけを持つ。再生キュー、バッチ状態、HTTP DTOを持たせない。

### 5.2 Provider port

最小契約:

```python
class TTSProviderPort(Protocol):
    name: str

    async def health(self) -> ProviderHealth: ...

    async def list_voices(self) -> list[ProviderVoice]: ...

    async def synthesize(
        self,
        request: ProviderSynthesisRequest,
    ) -> ProviderSynthesisResult: ...
```

VoiSonaのfile destinationは、空の `audio_data` を成功として返すのではなく、
`ArtifactRef` を結果に含める。呼び出し側が `hasattr` で機能判定する方式を廃止する。

### 5.3 結果の完全性

各Utteranceについて次を記録する。

- `pending`
- `synthesizing`
- `synthesized`
- `fallback_synthesized`
- `skipped`（理由必須）
- `failed`（分類済みエラー必須）

全体成果物の状態:

- 全件成功: `complete`
- 許可されたskipのみ: `complete_with_warnings`
- 1件以上のfailed: `partial`
- 成果物生成不能: `failed`

`partial` を `completed` としてAPI返却してはならない。

## 6. ジョブと永続化

### 6.1 識別子

- `work_id`: 正規化された作品を表す。URLだけでなくsource identityを含める。
- `plan_id`: 入力fingerprintと解析設定から生成する。
- `run_id`: 1回の実行を表すUUID/ULID。Providerや出力形式変更ごとに新規採番する。
- `job_id`: API/CLIから追跡するジョブID。

推奨成果物配置:

```text
output/
└── works/{work_id}/
    ├── plans/{plan_id}.json
    └── runs/{run_id}/
        ├── artifact-manifest.json
        ├── audio/
        ├── chapters/
        ├── subtitles/
        └── video/
```

旧 `output/{work_id}` は読み取り互換を維持し、新runへのmigration/importを提供する。

### 6.2 JobRepository

初期実装はSQLiteとし、音声バイナリはDBへ格納しない。

保存対象:

- job identity
- state
- request options
- plan/run参照
- progress
- timestamps
- error category/message
- cancellation request
- owner/client identity（API認証導入後）

許可する状態遷移は `core.jobs` に一元化し、任意文字列代入を禁止する。

### 6.3 atomic write

JSON/manifestは次の順序で保存する。

1. 同一ディレクトリへ一時ファイルを書き込む。
2. flushと必要に応じてfsyncする。
3. `Path.replace()` で置換する。

音声は一時名へ出力し、WAV検証成功後に最終名へ置換する。manifest更新より先に壊れた最終名を
公開しない。

## 7. 設定とcomposition root

### 7.1 設定優先順位

```text
組み込み標準値
  < packaged default YAML
  < ユーザー指定config directory
  < .env / environment variables
  < CLIまたはAPI起動引数
```

設定はPydanticモデルで検証し、application層へraw dictを渡さない。

パス分類:

- packaged resources: `importlib.resources`
- user config: 明示パスまたはXDG相当
- mutable data: `data_dir`
- generated artifacts: `output_dir`
- external executable: 明示設定または`PATH`探索

secret未設定を空文字へ変換して続行してはならない。該当Providerが有効な場合だけ必須検証する。

### 7.2 composition root

Provider、Repository、SynthesisService、UseCaseの生成は1箇所へ集約する。

候補:

```text
src/yomiage/bootstrap.py
```

CLI、FastAPI lifespan、SDK factoryは同じbootstrap関数を使用する。各入口がProviderを直接
constructしない。

## 8. HTTPセキュリティ境界

### 8.1 bindと認証

- 既定hostは `127.0.0.1`。
- 非loopback bind時はAPI token必須。
- token比較はconstant-time比較を使用。
- health livenessのみ認証除外可。
- metrics、readiness、job、synthesisは認証対象。

将来の多ユーザー認証は別スコープとし、初期は単一Bearer tokenでよい。

### 8.2 URL取得ポリシー

汎用URL取得は以下を既定拒否する。

- loopback
- private network
- link-local
- multicast/reserved/unspecified
- localhost系ホスト名
- 非HTTP(S) scheme
- userinfo付きURL
- 許可外port

DNS解決後のIPとredirect先を毎回検証する。レスポンス最大サイズ、redirect回数、timeoutを
設定する。専用ContentSourceも共通HTTP fetcherを利用し、サイト別実装で検証を迂回しない。

ローカルURL利用が必要なユーザー向けに、明示的なallowlist設定を用意する。

### 8.3 admission control

- API request body上限
- `text` 最大文字数
- 1 URLあたり取得最大bytes
- 全体queue長
- active batch jobs
- Provider別concurrency
- client単位の投入頻度

上限超過は429または413として分類し、ジョブを作成しない。

## 9. 観測性

### Liveness

プロセスがイベントループを処理できるかだけを返す。外部サービス障害で失敗させない。

### Readiness

要求された機能ごとに次を返す。

- 設定の妥当性
- output/dataディレクトリの書き込み可否
- Primary/Fallback TTS health
- LLM health
- ffmpeg存在
- SQLite接続

### Metrics

最低限:

- job count by state
- queue depth / active jobs
- synthesis latency by provider
- synthesis error by category/provider
- fallback count
- partial artifact count
- cache hit ratio
- URL fetch reject/timeout/size-limit count

メトリクスへ本文、token、password、完全URLのqueryを含めない。

## 10. テスト戦略

### Unit

- Job状態遷移
- URL policy
- config validation
- voice intent mapping
- plan fingerprint
- completion invariant

### Contract

全Provider adapterへ同じテストを適用する。

- health
- voice listing
- normal synthesis
- timeout/cancellation
- invalid voice
- file artifact
- close/lifecycle

### Integration

- SQLite JobRepository
- atomic ArtifactStore
- FastAPI + fake TTS/LLM/source
- 同一workの同時run
- restart recovery

### Golden

固定小説断片に対し、話者境界、segment type、Utterance順序をfixture化する。
LLM結果は固定レスポンスを注入し、モデル更新で不安定にならないようにする。

### External E2E

VOICEVOX / VoiSona / VOICEPEAK / Ollamaは通常CIから分離する。手動またはself-hosted runnerで
実行し、ライセンスと接続先を明示する。

## 11. 互換性とロールバック

- CLIコマンド名はR5まで維持する。
- 既存manifestは読み取り互換を維持する。
- 新schemaへ書き換える前にバックアップまたはcopy-on-writeを行う。
- 各移行タスクは旧経路へ戻せるfeature flagまたはadapterを持つ。
- 旧経路削除は新経路のcontract testと代表smoke test通過後に限定する。
- fallbackや部分成功の厳格化で終了コードが変わる場合、CLI/APIリファレンスへ明記する。

## 12. 完了の定義

アーキテクチャ改善全体は、次を満たした時点で完了とする。

1. 全入口がUtterancePlanとSynthesisServiceを利用する。
2. 同一入力に対する話者境界が入口間で一致する。
3. partial artifactをcompletedとして返す経路がない。
4. API公開時の認証とURL安全性がテストされている。
5. wheelインストール後のsmoke testがCIで通る。
6. ジョブ状態が再起動後も取得できる。
7. architecture testが禁止依存と旧実行経路の再導入を防ぐ。
8. README、architecture、configuration、API文書が実装と同期している。
