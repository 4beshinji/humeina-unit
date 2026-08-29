# Runtime finding 修正のためのリファクタリング調査資料

- 作成日: 2026-08-30
- 対象finding: [runtime-findings-consolidated.md](runtime-findings-consolidated.md)
- 目的: 修正着手前に必要な契約、変更面、依存関係、テスト資産、実環境条件を固定する
- 非対象: この文書作成時点でのソースコード修正、公開APIの即時削除、実環境未確認事項の断定
- 後続実行: [runtime-refactoring-goal-prompt.md](runtime-refactoring-goal-prompt.md)

## 1. 結論

今回のfindingは28件を個別にpatchするより、次の5境界を順に明示する方が再発を抑えられる。

1. 外部operationの所有権: deadline、排他、cancel、process/device cleanup
2. 合成出力のcapability: memory bytes、local artifact、VM共有file、remote playback
3. 音声parameterの意味論: unset、override、resolved value、provider-native value
4. lifecycleとjob scope: task、engine、admission lease、request budget
5. transportと運用: typed validation、error mapping、operation別readiness

既存の `TTSProviderPort`、`SynthesisService`、`FilesystemArtifactStore`、typed settings、
provider/entrypoint contract testsは移行の土台として再利用できる。ただし現行portの
`destination: str | None` と `metadata: Mapping[str, Any]` を拡張点のまま使い続けると、
F01、F06、F07が再発する。

## 2. 現行実装で再利用する境界

### 2.1 Provider portとエラー分類

`src/yomiage/ports/tts.py` には次が既にある。

- `ProviderErrorCategory`: unavailable、timeout、rate_limit、authentication、
  invalid_request、invalid_response、synthesis、cancelled、unknown
- `ProviderHealth`、`ProviderVoice`
- `ProviderSynthesisRequest` と `ProviderSynthesisResult`
- `TTSProviderPort.health/list_voices/synthesize/close`
- legacy例外をapplication用分類へ変換する `classify_provider_error`

この分類はF13のHTTP mappingにも使えるため維持する。追加が必要なのは、providerの
出力capability、typed destination、operation全体のdeadline、resolved parameterである。
provider固有値を `metadata` に追加し続ける形にはしない。

### 2.2 Artifact store

`src/yomiage/ports/artifacts.py` と
`src/yomiage/adapters/persistence/filesystem.py:122,176` には次が既にある。

- canonical layout: `output/works/{work_id}/runs/{run_id}`
- finalと同じdirectoryに置くtemporary artifact
- context終了時のtemporary cleanup
- WAV検証後のatomic publish
- checksum、size、duration、verifiedを持つ `ArtifactRef`
- manifestの状態とintegrity検証

F01、F08、F12の修正ではこの境界を唯一のfile publish経路にする。legacy adapterやcacheが
独自のtemp名、copy、renameを実装しない。in-memory bytesも同等のvalidatorを通してから
cacheまたはartifactへ公開する。

### 2.3 Typed settings

`src/yomiage/settings.py:150` の `ProviderSettings` は追加keyを保持し、
`to_legacy_dict()` も備える。`StorageSettings` は同ファイル:430にあり、data/output/assets/
sqlite pathを持つ。F18の損失点は `src/yomiage/api/config.py:76` の `PipelineConfig` 側である。

したがって新しい設定系を追加せず、typed settingsをcomposition rootから注入する。
readinessもraw YAMLを再解釈せず、実際にappが使用する `StorageSettings` とprovider registryを
入力にする。

### 2.4 既存テスト資産

| 資産 | 現在保証していること | 追加が必要なこと |
|---|---|---|
| `tests/contracts/test_provider_contract.py` | health、voices、bytes/file synth、timeout、cancel、idempotent close | operation全体deadline、process cleanup、capability別target、audio validation |
| `tests/contracts/test_entrypoint_contract.py` | SDK/Reader/Batch/Studioのshared fallback、partial、cancel | 同一parameter解決、同一target意味論、provider override集合 |
| `tests/test_synthesis_service.py` | retry、fallback、cache、timeout、cancel、concurrency、artifact、close | VoiceIntent cache key、corrupt audio、single-flight、全体deadline |
| `tests/test_batch_synthesis_service.py` | provider共通service、resume、retry、skip、一部ownership | success/partial/exception/cancelすべてのengine close、非blocking ffmpeg |
| `tests/test_reader_plan.py` | order、resume、stop、VoiceIntent、複数話者、orphan抑止 | scene/profileを含む最終resolved parameter |
| `tests/test_api_bridge.py` | public引数、file/fallback/long/cache/profile | mockの手前ではなくprovider直前のparameter意味論、非WAV error |
| `tests/test_server_batch.py` / `test_server_lifespan_jobs.py` | job作成、状態、valid SSE、recovery、supervisor | concurrent read admission、invalid request、missing SSE、error status |
| `tests/test_health_observability.py` | liveness、metrics、secret除去 | packaged settingsのstorage、operation capability readiness |

## 3. 追加する設計契約

以下は型名の候補であり、実装時に名前を変えてよい。ただし不変条件は維持する。

### 3.1 Parameterの4段階

```text
API/CLI input
    -> VoiceOverrides          # 指定なしと明示neutralを区別
    -> VoiceIntent             # provider非依存の意図
    -> ResolvedVoiceParameters # profile/scene/emotion適用後の絶対値
    -> ProviderParameterSet    # adapter内だけに存在するnative値
```

必要な不変条件:

- 「未指定」は値0や既定値と同一にしない。
- profile、scene、emotion、明示overrideの適用順を1か所で定義する。
- fully resolvedな値に `None` やprovider名を混在させない。
- provider-native pitch、speed、style ID、narrator IDはadapter外へ戻さない。
- fallback時は `VoiceIntent` からfallback provider用に再解決する。
- intensityの適用規則とclamp範囲をprovider translatorごとにテストする。

公開互換性のため、現行 `TTSParams` は当面compatibility DTOとして残し、入口で一度だけ
`VoiceOverrides` へ変換する。`TTSBridge.synthesize` の現在の公開引数を即時削除せず、
新request型へのcompatibility adapterとdeprecation期間を設ける。

### 3.2 Provider capabilityとtarget

候補:

```text
ProviderCapabilities
  - output_modes: audio_bytes | local_file | vm_file | remote_playback
  - audio_formats
  - voice_listing
  - cancellation

SynthesisTarget
  - MemoryTarget(format)
  - ArtifactTarget(work_id, run_id, relative_path, format)
  - SharedFileTarget(artifact target, provider-visible relative path)
  - RemotePlaybackTarget()
```

必要な不変条件:

- remote playbackの成功を空bytesで表さない。
- file targetはcanonical run rootからの相対pathを保持し、host/VMの各rootへ一度だけ解決する。
- Windows APIへ渡す直前にWindows文字列へ変換し、JSON payloadへ`Path`を入れない。
- 1 requestは1 provider operationで完結し、結果取得のために同じtextを再合成しない。
- callerが要求するtargetをproviderが満たさない場合、実行前にtyped errorにする。
- readinessは「provider healthy」ではなく、要求targetを満たせるかも判定する。

VoiSonaの `Z:` mappingは `output` rootからの相対pathだけを変換し、
`works/{work_id}/runs/{run_id}` を省略しない。既存artifact layoutは変更しない。

### 3.3 Operation deadlineと外部resource owner

候補:

```text
OperationDeadline
  - absolute monotonic deadline
  - remaining()
  - child(max_duration)

ExternalProcessRunner
  - async spawn
  - process-wide/provider-wide lock
  - bounded stdout/stderr capture
  - cancel: terminate -> grace -> kill -> wait
```

必要な不変条件:

- timeoutはhealth、voice解決、retry backoff、fallback、poll、実処理を含むrequest全体に適用する。
- 各層が同じ秒数のtimeoutを最初から再開しない。残時間だけを子へ渡す。
- VOICEPEAKのhealth/list/synthesizeは同じlockとrunnerを使う。
- cancel後にprocess、pipe reader task、executor worker、audio deviceを残さない。
- ffmpeg/VOICEPEAKをASGI event loop上で同期実行しない。
- blocking libraryをthreadへ逃がす場合も、cancel hookとthread完了を所有する。

`SynthesisService` は現在 `src/yomiage/application/synthesis.py:505` でprovider call単位に
`asyncio.wait_for` を適用している。preflightのhealth/voice解決やretry/fallbackを含む
絶対deadlineへ変更する必要がある。

### 3.4 Audio descriptorと検証

候補 `AudioDescriptor`:

- format
- sample rate
- channels
- sample widthまたはsample format
- frames、duration

必要な不変条件:

- provider result、cache hit、artifact publishの前に同じvalidatorを通す。
- `format="wav"` はRIFF/WAVE headerと読み切れるframe dataを要求する。
- 結合は各WAVのframeを読み、出力headerを再生成する。
- 異なるdescriptorは暗黙連結せず、明示変換またはtyped rejectionにする。
- cacheはtemporaryへ書き、fsync/atomic replace後だけhit対象にする。
- cache keyはtext、provider、resolved voice/params、format、model/profile versionを含む。

### 3.5 Lifecycle、admission、budget

必要なscope:

| resource | owner | 終了条件 |
|---|---|---|
| provider registry / shared `SynthesisService` | app lifespan | shutdownで1回close |
| Batch engineと直接生成したLLM session | 1 job | success/partial/failure/cancelで1回close |
| Reader playback task | 1 read job | drainまたはcancel後に0 task |
| read admission lease | supervisor/app | submitと同時にatomic取得、terminalで解放 |
| LLM request budget | 1 job context | job終了で破棄、物理requestを計数 |
| subprocess/audio device | 1 operation | return/raise/cancel前に回収 |

legacy `TTSManager` を残す期間は `NEW -> RUNNING -> DRAINING -> CLOSED` のような明示stateを
持たせる。ただし最終的には `UtterancePlaybackQueue` へ寄せ、2種類のlifecycleを維持しない。

### 3.6 HTTPとreadiness

HTTP境界で確定させる事項:

- whitespace-only text、非有限数、範囲外speed/pitch/intonation/intensity、未知format/modeを
  schemaで拒否する。
- validation/admissionはtask作成やprovider呼出しより前に完了する。
- `ProviderErrorCategory` からHTTP statusへの対応表を1か所で定義する。
- SSEはjob存在と閲覧権限をstream response生成前に確認する。
- APIのdefault providerはtyped app settingsから取得し、別のliteralを持たない。

推奨するerror mappingの初期案:

| category | HTTP | 備考 |
|---|---:|---|
| invalid_request | 400または422 | schema違反は422、domain上の不正は400に統一してもよい |
| authentication | 502 | upstream認証不備をclient認証失敗と混同しない |
| rate_limit | 503 | `Retry-After` を取得できる場合は転送 |
| unavailable | 503 | fallback枯渇後のみ返す |
| timeout | 504 | operation全体deadline超過 |
| invalid_response / synthesis / unknown | 502 | provider応答またはupstream処理失敗 |
| cancelled | 409またはclient disconnect | endpoint種別ごとに明示する |

readinessはoperation別に返す。最低でも `immediate_playback`、`batch_audio`、`video`、
`slack` を分け、provider health、target capability、storage、VM mount、ffmpeg codec、必要SDKを
依存として列挙する。livenessは外部依存の失敗で落とさない。

## 4. 変更面と移行時の注意

| 変更面 | 主な対象 | 互換性・注意点 |
|---|---|---|
| Domain model | `core/models.py`、profile/scene mapper | `TTSParams` とmanifest読取り互換を維持する。新値を旧manifestへ必須化しない。 |
| Provider port | `ports/tts.py`、`adapters/tts/` | 一度に全providerを変えず、compat adapterで旧実装を包む。 |
| Synthesis orchestration | `application/synthesis.py`、`bootstrap.py` | retry回数、fallback順、metrics labelの外部挙動をcharacterization testで固定する。 |
| Artifact/cache | `ports/artifacts.py`、filesystem adapter、`tts/cache.py` | canonical run layoutを変更しない。cache schema/versionをkeyへ入れ、旧entryはmiss扱い可能にする。 |
| Entry points | Reader、Batch、Studio、SDK/Bridge | CLI command名とpublic methodを維持し、内部requestだけ段階移行する。 |
| Composition | `bootstrap.py`、`interfaces/cli/composition.py`、Studio | provider構築規則を1つにし、個別入口で直接LLM/providerを作らない。 |
| Server | request models、routes、exception handler、health | validation強化とstatus変更は意図的なAPI behavior changeとしてrelease note対象にする。 |
| External tools | VOICEPEAK、ffmpeg、sounddevice、VoiSona | unit fakeでprocess protocolを固定後、実環境ゲートを別に通す。 |

`bootstrap.py` をcanonical composition rootにする。現在の
`interfaces/cli/composition.py` とStudio内部のprovider解決は、同じtyped settingsから
provider registryとSynthesisServiceを受け取る形へ段階移行する。

## 5. 実装パッケージと依存関係

1パッケージが5日を超えないように分割する。各パッケージは先に失敗するcharacterization/
regression testを追加し、実装後にgreenにする。

| ID | サイズ | 内容 | 対象 | 依存 |
|---|---:|---|---|---|
| RT-001 | M | 全findingのunit characterization、fake process、audio fixture、HTTP concurrency barrierを追加 | F01〜F20 | なし |
| RT-002 | L | `OperationDeadline` とasync external process runner、process/audio cleanup protocol | F02、F03、F10、F15 | RT-001 |
| RT-003 | L | `ProviderCapabilities`、typed target、VoiSona host/VM path resolver、二重合成除去 | F01、D04 | RT-001、RT-002 |
| RT-004 | L | `AudioDescriptor`/validator、frame concat、cache atomic publish、legacy temp統合 | F05、F08、F12 | RT-001、RT-003 |
| RT-005A | L | unset/override/resolved/nativeのparameter契約と適用順 | F06、D01 | RT-001 |
| RT-005B | M | VoiSona/VOICEVOX/VOICEPEAK translatorとfallback再解決 | F07 | RT-005A |
| RT-005C | L | Reader/Bridge/Pipeline/Studioをcanonical resolverへ移行、設定round-trip修正 | F16、F18、F19 | RT-005A、RT-005B |
| RT-006 | M | 完全なcache key、schema version、single-flight | F17、D03 | RT-004、RT-005A |
| RT-007A | M | playback lifecycle統合、read admission lease | F04、F09、D02 | RT-001、RT-002 |
| RT-007B | M | Batch engine/session scopeとjob-local LLM budget | F11、F14 | RT-001 |
| RT-008 | M | HTTP schema、provider error mapping、SSE precondition、default provider統一 | F13 | RT-003、RT-005C、RT-007A |
| RT-009 | M | typed storage注入とoperation別readiness | F20、D04 | RT-003、RT-004、RT-008 |
| RT-010 | L | 実環境E2E、結果反映、compat/legacy撤去 | E01〜E04、D01、D02 | RT-002〜RT-009 |

推奨依存グラフ:

```text
RT-001 ─┬─ RT-002 ─ RT-003 ─ RT-004 ─ RT-006 ─┐
        │     └────────────── RT-007A ────────┤
        ├─ RT-005A ─ RT-005B ─ RT-005C ─ RT-008 ─ RT-009
        └───────────────────── RT-007B ────────┤
                                                └─ RT-010
```

RT-002とRT-005Aは競合範囲が小さいため並行可能である。RT-003/004とRT-005B/005Cも、
port requestの変更点をRT-001終了時に固定できれば別laneで進められる。

## 6. テスト追加計画

### 6.1 Unit/contract

| 推奨配置 | 主な対象 | 必須fixture |
|---|---|---|
| `tests/contracts/test_provider_runtime_contract.py` | F01、F02、F10、D04 | clock制御、fake HTTP、fake process、capability matrix |
| `tests/test_parameter_resolution.py` | F06、F07、F16、F18 | provider別golden、profile/scene/emotion matrix |
| `tests/test_audio_validation.py` | F05、F08、F12 | 小さいvalid WAV、truncated RIFF、HTML bytes、descriptor mismatch |
| 既存 `test_synthesis_service.py` | F08、F10、F17、D03 | corrupt cache、concurrent miss、absolute deadline |
| `tests/test_tts_manager_lifecycle.py` | F04、F15、D02 | blocking provider、blocking sounddevice、task leak detector |
| 既存Batch/Studio tests | F03、F11、F19 | heartbeat、owned close counter、provider registry spy |
| `tests/test_server_runtime_failures.py` | F09、F13、F14 | barrier、provider categorized error、missing job、job contexts |
| `tests/test_readiness_capabilities.py` | F20、D04 | packaged settings、temp storage、provider/tool capability matrix |

task/process leak検査では「一定時間待ってtask一覧が空」を主判定にせず、所有したtask/processを
記録するfake registryを使う。時間依存テストはmonotonic clock注入を優先する。

### 6.2 実環境E2E

既存 `tests/e2e/` のmarker方針を使い、unit suiteから分離する。実行時に次を記録する。

- git revisionまたは作業ツリー識別子
- OS、Python、ffmpeg/VOICEPEAK/VoiSona/VOICEVOXのversion
- 使用したprovider、voice/narrator、要求target
- timeout、実測duration、process exit code
- 出力artifactのpath、size、WAV descriptor、checksum
- cancel後のremote job/process/socket状態

必要な環境:

| 対象 | 必要条件 | 合格条件 |
|---|---|---|
| VoiSona | Windows VM、API資格情報、Linux `output/` と同一内容を指す `Z:` 共有 | 1 request/1 synth、成功直後に完全WAV可視、timeout/cancel後のremote状態が仕様どおり |
| VOICEPEAK | 実binary、利用可能license、複数narrator | 同時operationが直列化、narrator/値が正しい、cancel/timeout後process 0 |
| VOICEVOX | Engineと利用speaker | fallback再解決、bytes/file descriptor、error分類が一致 |
| ffmpeg | binaryと必要codec（mp3/ogg/video） | event loop非停止、cancel後process 0、format変換結果をvalidatorが受理 |
| sounddevice | 出力deviceまたは専用検証host | cancelでdevice解放、次回再生可能 |
| Slack | test workspace、Socket Mode token、`slack_sdk` | network断/SIGTERM後にsocket/taskが残らず再接続または終了 |

検証時点のローカル環境では `ffmpeg`、設定されたVOICEPEAK binary、`slack_sdk` が利用できない。
RT-010の完了をunit testだけで代替しない。

## 7. 互換性とロールバック

- CLIの既存command名は維持する。
- `output/works/{work_id}/runs/{run_id}` と既存manifestの読取り互換を維持する。
- 新しいparameter requestは既存public APIの内側にcompat adapterを置いて導入する。
- 新cache key/schemaは旧cacheを破壊的変換せず、旧entryをmissとして再生成できるようにする。
- providerごとの移行中はregistryでlegacy/new adapterを切替可能にするが、1 request内で両方を
  呼んで二重合成しない。
- HTTP validation/statusの変更はfeature flagで曖昧にせず、version/release noteとcontract testで
  意図的なbehavior changeとして扱う。
- 各RTパッケージは独立revert可能にし、parameter model変更とlegacy adapter撤去を同一変更にしない。
- 実環境ゲートに失敗した場合はlegacy pathを残したまま戻し、新artifact/manifestを削除しない。

## 8. 実装前に決定する事項

以下はコードから一意に決められないため、RT-001または該当パッケージ開始時にADRまたは
test expectationとして確定する。

1. speed、pitch、intonation、volumeのcanonical単位・許容範囲・clamp位置。
2. profile、scene、emotion、明示overrideの正確な優先順と合成式。
3. `RemotePlaybackTarget` の成功resultが持つべきreceipt/job ID。
4. fallbackでvoice候補がない場合にdefault voiceを使うか、明示失敗にするか。
5. cancelをHTTP 409、499相当、job statusのみのどれで表すか。
6. immediate、batch audio、video、Slackの各readinessで必須とするcapability。
7. LLM budgetが数える物理requestに、retry、fallback model、summary callをすべて含めるか。
8. cache互換期間と最大容量・eviction policy。

決定後は文章だけでなく、provider/entrypoint contract testの期待値として固定する。

## 9. 完了の判定

全findingの修正完了は、次をすべて満たす状態とする。

- F01〜F20の最小回帰テストが意図した境界を直接検査してgreen。
- D01/D02の旧重複経路がcompatibility期限後に撤去され、architecture testが逆戻りを防ぐ。
- D03のsingle-flightとD04のoperation別readinessがcontract testで保証される。
- E01〜E04の実環境結果がversion・条件付きで記録され、未検証表示が残っていない。
- unit/contract suite、Ruff、代表CLI/REST/SDK smoke testが通る。
- public API、HTTP behavior、設定、運用手順、rollback手順が実装と同期する。

既存 [work-ledger.md](work-ledger.md) の完了済みSYN/API/OBS項目を再オープンするのではなく、
本書のRT-IDを残存runtime findingの修正パッケージとして追跡する。実装開始時に担当、状態、
変更範囲、実環境予約を追加する。
