# Runtime finding 修正進捗

- 開始日: 2026-08-30
- 正本: `runtime-findings-consolidated.md`、`runtime-refactoring-guide.md`
- 完了条件: F01〜F20、D01〜D04、unit/contract、Ruff、CLI/REST/SDK smoke、利用対象の実環境E2E
- 完了判断: 2026-08-30のユーザー判断により、保有していないVOICEPEAKのE03と、一旦利用しない
  SlackのE04を今回scopeでは適用外とした。未検証をgreenとは扱わない。
- 現在状態: RT-001〜RT-010完了。E01/E02 green、E03/E04適用外

## 1. 開始時点の保全と基準線

最初の `git status --short` では、今回の作業前から多数の変更が存在した。既存変更は
上書き、巻き戻し、削除せず、今回の変更は本書の変更ファイル欄で追跡する。

- 既存変更: `.env.example`、`README.md`、`config/default.yaml`、各種 `docs/`、
  `pyproject.toml`、`src/yomiage/` の LLM・設定・Reader・Batch・Server 等、既存テスト
- 既存未追跡: 正本2文書、goal prompt、LLM移行文書・実装・テスト、infra script
- 今回追加: `docs/improvement/runtime-refactoring-progress.md`

開始時コマンドと結果:

| 種別 | コマンド | 結果 |
|---|---|---|
| 作業ツリー | `git status --short` | 上記の既存変更を確認。破壊的操作なし |
| 全 pytest | `.venv/bin/python -m pytest -q` | 進捗表示がバッファされたためこちらの実行だけ中断。テスト失敗ではない |
| 全 pytest 再確認 | `timeout 120s .venv/bin/python -m pytest -vv -x` | **656 passed, 37 skipped in 11.70s** |
| Ruff | `.venv/bin/python -m ruff check src/ tests/` | **All checks passed** |

開始時の全 suite には `tests/e2e/` も含まれた。VoiSona の既存 synthesis/list test は
実サービスで green、VOICEVOX 等の利用不能な対象は skip された。E01〜E04の専用条件は
既存E2Eだけでは満たさないため、RT-010で別途記録する。

## 2. RT パッケージ追跡

| RT | finding | 状態 | 変更ファイル | 実行テスト・結果 | 残課題 / 非対象 |
|---|---|---|---|---|---|
| RT-001 | F01〜F20 | red確認 | 本書、下記runtime test群 | 各対象testでfinding一致のred | 実装後green化。実環境項目はRT-010 |
| RT-002 | F02,F03,F10,F15 | green | `application/operations.py`, `application/synthesis.py`, `tts/voicepeak.py`, `tts/voisona.py`, `tts/playback.py`, Batch/video process call sites | 対象10 tests + process実体owner回帰8 tests passed、対象Ruff green | 実binary/device検証はRT-010 |
| RT-003 | F01,D04 | green | `ports/tts.py`, `adapters/tts/legacy.py`, `application/synthesis.py`, `batch/engine.py`, provider runtime contract | F01/D04 4 tests passed、隣接contract green | D04 operation readinessはRT-009 |
| RT-004 | F05,F08,F12 | green | `tts/audio_utils.py`, `tts/cache.py`, `application/synthesis.py`, `adapters/tts/legacy.py`, audio tests | 14 regression + 35 adjacent passed、Ruff green | 非WAV実codec検証はRT-010 |
| RT-005A | F06,D01 | green | `application/voice_parameters.py`, `application/synthesis.py`, port parameter contract, tests | F06/D01 4 passed、adjacent green | 全入口migrationはRT-005C、legacy撤去はRT-010 |
| RT-005B | F07 | green | canonical translator、legacy adapters、3 providers、parameter tests | F07 5 passed、47 adjacent passed | 実provider goldenはRT-010 |
| RT-005C | F16,F18,F19 | green | application request/context、Reader playback/engine、`api/config.py`、Studio engine、入口tests | 4 minima + 82 adjacent passed/1 skipped、Ruff green | 実provider比較はRT-010 |
| RT-006 | F17,D03 | green | `application/synthesis.py`, `tts/cache.py`, synthesis/audio tests | 4 minima + 47 suite passed、Ruff green | 旧schemaは非破壊miss |
| RT-007A | F04,F09,D02 | green | `tts/manager.py`, Slack CLI, `server.py`, lifecycle/server tests | Slack実入口を含むF04 3、F09 2、D02正常/cancel両実装4 cases passed | compatibility lifecycleは期限まで保持 |
| RT-007B | F11,F14 | green | `batch/job_manager.py`, `llm/service.py`, runtime job tests | 7 minima + 35 adjacent + 4 server passed、Ruff green | 実LLMはE2E別marker |
| RT-008 | F13 | green | `server.py`, `test_server_runtime_failures.py` | F13 14 cases passed、対象Ruff green | validation/statusは意図的HTTP互換変更 |
| RT-009 | F20,D04 | green | `application/health.py`, `server.py`, readiness tests | 9 minima + HTTP matrix + 全suite passed、Ruff green | 実codec/provider確認はRT-010 |
| RT-010 | E01〜E04,D01,D02 | 完了 | E2E・architecture tests、API reference、本書 | E01/E02 + VoiSona 2 = 4 passed、15 skipped。全unit/contract 734 passed、Ruff/smoke green | E03/E04はユーザー判断により適用外。将来再開用の条件とcommandは保持 |

状態は `未着手`、`red確認`、`実装中`、`green`、`適用外`、`完了` のいずれかで更新する。

## 3. Finding 証拠対応

| ID | RT | 直接原因 / 変更境界 | 最小回帰テスト | 状態 | 修正箇所・結果 |
|---|---|---|---|---|---|
| F01 | 003 | output capability/typed target/path resolver | Immediate/Batch/Studio 1 synth、host/VM同一相対path | green | typed 4 targets/capabilityを追加。VM pathはoutput root相対をWindows文字列へ一度だけ解決。memory/remote targetは各1 provider operation |
| F02 | 002 | VOICEPEAK共通process owner | 3 operation排他、timeout/cancel cleanup | green | `ExternalProcessRunner`をhealth/list/synthで共有。最大process=1、cancel時terminate/kill後waitをassert |
| F03 | 002 | blocking subprocess境界 | heartbeat継続、cancel後process 0 | green | `ThreadProcessRunner`がPopenを所有し、Batch concat/videoと全ffmpeg call siteへ伝播。fake Popenのcancelでterminate/kill/wait、thread join、registry 0を直接assert |
| F04 | 007A | legacy manager lifecycle | start/double-start/drain/cancel/Slack表 | green | 完了監査でSlackがenqueue直後stopしprovider call 0となる残存bugをred再現。空enqueueを除去しdrain後stopへ修正、provider=1、task=0、close=1を直接assert |
| F05 | 004 | WAV frame concat/descriptor | 100+100=200 frames、不一致拒否 | green | `AudioDescriptor`/validatorと`wave` frame writerへ置換。200 frames、rate/channel/width不一致拒否 |
| F06 | 005A | 4段階parameter契約・一元合成順 | provider別golden matrix | green | `VoiceOverrides`/`ResolvedVoiceParameters`を分離し、profile→scene→emotion→explicitを唯一のresolverで固定 |
| F07 | 005B | native translator/fallback再解決 | fallback voice/params、VOICEPEAK一度だけ変換 | green | 3 translatorをadapter境界に限定。fallback再解決に加え、narrator/speed/pitch/emotionが最終VOICEPEAK CLIへ1 command・一度だけ到達することをassert |
| F08 | 004 | validate-before-publish | corrupt/provider/cache/atomic interruption | green | invalid 3種、宣言format不一致、corrupt cacheを拒否。cache replace中断で旧valid entry保持・temp 0を直接assert |
| F09 | 007A | atomic read admission lease | concurrent 200/409、実行中保持・終了後解放 | green | read専用exclusive leaseをoperation終了まで保持。concurrent結果 `[200,409]`、read_url=1。完了監査でsupervisor submit直後の早期解放をred再現し、terminal release後の次request 200も固定 |
| F10 | 002 | absolute monotonic deadline | delayed GET wall-clock bound | green | VoiSona遅延GETに加え、voice resolution→retry→backoff→fallback全体を0.04秒deadline/0.08秒未満で直接assert |
| F11 | 007B | job-owned engine/session | 4 terminal path close各1回 | green | BatchJobManager finallyがowned engineをsuccess/partial/exception/cancelすべて各1回close |
| F12 | 004 | artifact-store-owned temp | 4 terminal path cleanup、successのみfinal | green | legacy file awaitをBaseException cleanupで囲み、success/exception/timeout/cancel全経路でtemp 0、successのみfinal |
| F13 | 008 | transport validation/mapping/precondition | invalid 4xx、categorized 5xx、missing SSE 404 | green | 空白/範囲外/未知format・modeは副作用前422、timeout/unavailable/invalid responseは504/503/502、missing SSEはstream開始前404。provider詳細は非公開 |
| F14 | 007B | job-local physical request budget | job A/B分離、retry込み上限 | green | context-local job budgetをBatch job scopeへ注入。各transport attempt直前に消費しretryも上限内 |
| F15 | 002/007A | audio device/thread owner | cancel stop/join/次回成功 | green | sounddevice cancel時に`sd.stop()`後executor workerをjoinし、同じbackendの次回play/wait成功までassert |
| F16 | 005C | Reader canonical resolver | daily/neutral と battle/angry のnative差 | green | ReadingEngine実Plan経路でscene/emotion contextを渡し、provider直前のrate/intonation/style差をassert |
| F17 | 006 | complete VoiceIntent cache key | intent差で2 call/2 entry | green | hashへprovider/version/format/VoiceIntentと最終`ResolvedVoiceParameters`を明示格納。intent差・scene resolved差それぞれ2 call/2 entry |
| F18 | 005C | typed provider settings round-trip | extra keyの値・型保持 | green | SDK DTOが既知共通field以外を`extra`へ保持。path/pitch_scale/max_chars/speakersの値・型をround-trip |
| F19 | 005C | Studio provider集合解決 | default/override/fallbackを各1回構築 | green | synth/previewのruntime primaryをprovider集合解決へ渡し、override primary + fallbackを各1回構築 |
| F20 | 009 | injected storage/operation readiness | packaged output pathのwritable/capacity | green | packaged settingsをtmp baseへ実ロードし、その`batch.output_dir`のwritable/free capacityを直接assert |
| D01 | 005A/010 | canonical requestとcompat期限 | 全入口adapter直前request一致 | green | SDK/Reader/Batch/Studioの4入口が同じprovider-boundary値となり、`TTSParams` compatibility adapterが1回だけ実行されることを固定。公開adapterは互換条件に従い保持 |
| D02 | 007A/010 | shared playback lifecycle | 両実装共通lifecycle suite、検証後撤去 | green | legacy managerとPlaybackQueueへcancel/正常drainの同一parameterized suiteを適用し、registry 0、1 synth、外部service非close、明示close各1回を固定 |
| D03 | 006 | cache single-flight | concurrent同一keyで1 call | green | per-service key lock/refcount registry。concurrent missでprovider=1/cache=1、結果一致、registry空をassert |
| D04 | 003/009 | target capability readiness | operation別不足要件matrix | green | healthy typed capabilityからmatrixを算出し、VM fileのmountとSlack SDKも独立判定。`/health/ready`でも4 operationをsafe dependency名だけで返すcontractを固定 |

既に同等テストがある場合はここへ test path と主要 assertion を記録し、重複追加しない。
再現しないfindingは現行コード、call site、実行コマンド、観測結果を記録してから除外候補とする。

### 3.1 最小回帰テスト索引

以下のnode IDをfindingから直接実行できる。parameterized testは各caseを含めてgreenである。

| ID | test path / node | 固定した主要assertion |
|---|---|---|
| F01 | `tests/contracts/test_provider_runtime_contract.py::test_f01_voisona_shared_path_preserves_canonical_run_relative_path`、`::test_f01_voisona_target_uses_exactly_one_provider_operation` | host/VM同一相対path、memory/remoteともprovider operation 1回 |
| F02 | `tests/contracts/test_provider_runtime_contract.py::test_f02_voicepeak_health_list_and_synthesis_share_one_process_lock`、`::test_f02_voicepeak_cancel_reaps_child_process` | active process最大1、cancel後terminate/kill/wait、owner registry 0 |
| F03 | `tests/test_runtime_jobs.py::test_f03_batch_blocking_concat_does_not_stop_event_loop`、`::test_f03_batch_cancel_reaps_ffmpeg_and_joins_worker` | heartbeat継続、fake Popen/thread/process owner全回収 |
| F04 | `tests/test_tts_manager_lifecycle.py::test_f04_double_start_is_idempotent_and_drain_leaves_no_worker`、`::test_f04_stop_cancels_blocked_provider_and_returns`、`::test_f04_slack_entrypoint_starts_manager_and_synthesizes_once` | 二重workerなし、blocked task回収、Slack provider 1回/close 1回 |
| F05 | `tests/test_audio_validation.py::test_f05_concat_wav_bytes_rebuilds_header_from_frames`、`::test_f05_concat_wav_bytes_rejects_descriptor_mismatch` | 100+100=200 frames、descriptor不一致拒否 |
| F06 | `tests/test_parameter_resolution.py::test_f06_unset_and_explicit_neutral_are_distinct`、`::test_f06_resolution_order_is_profile_scene_emotion_explicit_override` | unsetとneutralを区別、4段階合成順を固定 |
| F07 | `tests/test_parameter_resolution.py::test_f07_provider_translation_happens_once_at_adapter_boundary`、`::test_f07_fallback_re_resolves_voice_and_native_parameters`、`::test_f07_voicepeak_native_values_and_narrator_reach_cli_exactly_once` | adapter境界のみnative化、fallback再解決、最終CLI 1 command |
| F08 | `tests/test_audio_validation.py::test_f08_invalid_provider_audio_is_rejected_and_not_cached`、`::test_f08_declared_format_mismatch_is_rejected_before_cache_publish`、`::test_f08_corrupt_cache_entry_is_a_miss_and_is_replaced_atomically`、`::test_f08_cache_replace_interruption_keeps_previous_valid_entry` | 検証前publishなし、旧valid entry保持、temp 0 |
| F09 | `tests/test_server_runtime_failures.py::test_f09_concurrent_read_admission_accepts_exactly_one`、`::test_f09_read_admission_is_held_until_operation_finishes_then_released` | concurrent result `[200, 409]`、実行中409、terminal release後200、`read_url`合計2回 |
| F10 | `tests/contracts/test_provider_runtime_contract.py::test_f10_voisona_poll_uses_wall_clock_deadline`、`tests/test_synthesis_service.py::test_f10_single_deadline_includes_voice_resolution_retry_and_fallback` | monotonic絶対deadlineがpoll/retry/backoff/fallback全体を制限 |
| F11 | `tests/test_runtime_jobs.py::test_f11_batch_job_closes_owned_engine_once_on_every_terminal_path` | success/partial/exception/cancelでowned engine close各1回 |
| F12 | `tests/test_audio_validation.py::test_f12_legacy_file_temp_is_removed_when_cancelled`、`::test_f12_legacy_file_temp_cleanup_on_all_service_terminal_paths` | 全terminal pathでtemp 0、successだけfinal publish |
| F13 | `tests/test_server_runtime_failures.py::test_f13_invalid_immediate_request_is_4xx_before_tts`、`::test_f13_unknown_batch_option_is_4xx_before_job_creation`、`::test_f13_provider_errors_map_to_stable_http_status`、`::test_f13_missing_sse_job_is_rejected_before_stream_start` | validation/admissionが副作用前、安定した422/502/503/504/404 |
| F14 | `tests/test_runtime_jobs.py::test_f14_llm_budget_is_job_local`、`::test_f14_retry_never_exceeds_physical_request_budget` | job間counter分離、retry込みtransport call上限 |
| F15 | `tests/test_tts_manager_lifecycle.py::test_f15_sounddevice_cancel_stops_device_and_joins_worker` | stop/join後owner 0、同じbackendで次回再生成功 |
| F16 | `tests/test_parameter_resolution.py::test_f16_synthesis_request_preserves_scene_for_canonical_resolution`、`tests/test_reader_plan.py::test_f16_reader_scene_and_emotion_change_provider_boundary_parameters` | Reader Planのscene/emotionがprovider直前parameter差になる |
| F17 | `tests/test_synthesis_service.py::test_f17_file_cache_key_includes_voice_intent`、`::test_f17_file_cache_key_includes_resolved_scene_parameters` | intent/resolved値差でprovider 2回、cache 2 entry |
| F18 | `tests/test_config_settings.py::test_f18_pipeline_config_preserves_provider_extra_settings_round_trip` | provider追加keyの値と型をround-trip |
| F19 | `tests/test_studio_engine.py::test_f19_runtime_provider_override_builds_primary_and_fallback_once` | runtime primary/override/fallbackを必要分だけ各1回構築 |
| F20 | `tests/test_readiness_capabilities.py::test_f20_packaged_batch_output_is_used_as_storage_dependency`、`::test_f20_packaged_settings_report_the_injected_runtime_output` | 実注入されたoutput pathのwritable/capacityを判定 |
| D01 | `tests/test_parameter_resolution.py::test_d01_tts_params_compatibility_adapter_runs_exactly_once`、`::test_d01_all_entrypoints_reach_provider_with_same_canonical_parameters` | compatibility adapter 1回、4入口のprovider-boundary値一致 |
| D02 | `tests/test_tts_manager_lifecycle.py::test_d02_common_playback_lifecycle_cancel_and_close_contract`、`::test_d02_common_playback_lifecycle_drains_normally_and_closes_once` | legacy/new両実装でcancel/normal drain後registry 0、close各1回 |
| D03 | `tests/test_synthesis_service.py::test_d03_concurrent_identical_cache_miss_is_single_flight` | concurrency barrier下でprovider/cache各1回、flight registry空 |
| D04 | `tests/contracts/test_provider_runtime_contract.py::test_d04_provider_contract_declares_typed_capabilities_and_targets`、`tests/test_readiness_capabilities.py::test_d04_provider_target_capability_matrix`、`::test_d04_format_and_health_are_part_of_provider_readiness`、`::test_d04_vm_file_and_slack_require_mount_and_sdk`、`::test_d04_readiness_reports_each_operation_and_missing_capabilities`、`::test_d04_http_readiness_returns_operation_capability_matrix` | typed target/capability、VM mount、Slack SDK、operation別readinessを内部/HTTP両契約で固定 |

## 4. パッケージ作業記録

### RT-001

- 対象: F01〜F20の現行再検証と最小回帰テスト
- 変更する境界: 原則テスト・fixtureのみ（本書を除く）
- 非対象: 実装修正、公開API削除、E01〜E04をmock成功で代替すること
- 受入: 各確認済みfindingが意図した理由でred、または同等の既存assertionを特定
- 現在結果: 全IDのredまたは既存同等assertionを照合済み。主な実行コマンド:
  - `.venv/bin/python -m pytest -q tests/test_audio_validation.py` → 9 failed（F05/F08/F12）
  - `.venv/bin/python -m pytest -q tests/contracts/test_provider_runtime_contract.py` → 5 failed（F01/F02/F10/D04）
  - parameter/config/studio対象test → 9 failed（F06/F07/F16/F18/F19/D01）
  - synthesis/runtime jobs対象test → 9 failed（F03/F11/F14/F17/D03）
  - server/readiness対象test → 12 failed（F09/F13/F20/D04）
  - lifecycle個別test → double-start/blocked stop/sounddevice cancelがfinding一致でfailed
  - 新規test群へのRuffは未使用import 1件を除去後 **All checks passed**

以後、各RT開始前に対象finding、直接原因、変更境界、非対象、受入テストを追記する。

### RT-002

- 対象: F02、F03、F10、F15
- 直接原因: operation全体で共有する絶対deadlineと、process/deviceの単一ownerがなかった。
- 変更境界: application operation helper、VOICEPEAK全CLI operation、VoiSona HTTP poll、
  SynthesisService、sounddevice playback、Batch managerの同期Phase C/D呼出し。
- 非対象: provider capability、audio内容検証、legacy lifecycle統合、実binary/device成功の断定。
- 受入: process最大1、cancel後reap、遅延HTTPを含むwall-clock timeout、heartbeat継続、
  sounddevice stop/join。
- 実行証拠:
  - provider runtime F02/F10、Batch F03、playback F15、service timeoutの10 test
    → **10 passed in 0.43s**
  - 完了監査でSynthesisServiceのvoice resolution/retry/backoff/fallback全体deadlineと、
    sounddevice cancel後の次回再生を直接assertしてgreen。
  - 対象source/testへのRuff → **All checks passed**

### RT-003

- 対象: F01、D04のcapability/target部分
- 直接原因: `destination: str | None` がbytes、artifact、VM共有file、remote playbackを
  区別せず、VM pathをrun directory名だけから組み立てていた。
- 変更境界: provider port、legacy/VoiSona adapter、application request/result、Batch metadata。
- 非対象: audio validation/publish、parameter resolver、operation readiness。
- 受入: 4 typed targetとcapability型が存在し、canonical run相対pathがVM側にも保存され、
  memory/remote targetとも1 requestが1 provider operationで完結する。
- 実行証拠:
  - `tests/contracts/test_provider_runtime_contract.py -k 'f01 or d04'`
    → **4 passed**
  - provider/Batch隣接contractは後続findingの意図的red（F17/D03）を除きgreen。
  - 対象source/testへのRuff → **All checks passed**

### RT-004

- 対象: F05、F08、F12
- 直接原因: WAV chunk境界を手計算し、provider/cacheの非空bytesを構造検証せず、legacy
  file awaitのcancel pathがtemporaryを回収しなかった。
- 変更境界: audio validator/bytes concat、SynthesisService finish/publish、TTSCache、legacy adapter。
- 非対象: cache key/single-flight（RT-006）、provider-native parameter、実codec品質。
- 受入: 100+100 frames、descriptor不一致拒否、invalid provider/cache miss、検証後atomic publish、
  success/exception/timeout/cancelのtemp 0。
- 実行証拠:
  - `.venv/bin/python -m pytest -q tests/test_audio_validation.py`
    → **14 passed**
  - F17/D03の意図的redを除くsynthesis/provider/artifact/atomic隣接test
    → **35 passed, 2 deselected**
  - 対象source/testへのRuff → **All checks passed**

### RT-005A

- 対象: F06、D01のcanonical contract部分
- 直接原因: unsetをlegacy DTOのneutral既定値で潰し、複数入口が異なる順でprofile/scene/emotionを合成した。
- 変更境界: provider非依存parameter value objectsとresolver、port requestのresolved field。
- 非対象: provider-native clamp/変換、入口移行、公開`TTSParams`削除。
- 受入: unset≠明示neutral、profile→scene→emotion→explicitのgolden、resolved値にprovider名なし、
  `TTSParams` adapterを一度だけ利用。
- 実行証拠: parameter F06/D01 → **4 passed**、対象Ruff green。

### RT-005B

- 対象: F07
- 直接原因: provider非依存値とnative値がmetadata/入口へ混在し、fallbackにprimary ID/値が流れた。
- 変更境界: VoiSona/VOICEVOX/VOICEPEAK translator、legacy adapter、provider native受入口、
  SynthesisService fallback request。
- 非対象: profile/scene設定を各入口から渡す移行、実音声品質。
- 受入: provider別golden、fallback voice/native再解決、primary-only ID非流用。
- 実行証拠:
  - parameter F07（3 provider golden + fallback integration + 最終VOICEPEAK CLI）→ **5 passed**
  - F17/D03の意図的redを除くsynthesis/provider/Batch/audio隣接test → **47 passed, 2 deselected**
  - 対象source/testへのRuff → import整列後 **All checks passed**

### RT-005C

- 対象: F16、F18、F19、D01入口移行部分
- 直接原因: `SynthesisRequest`がscene/resolution contextを保持せず、SDK DTOがprovider追加keyを
  捨て、Studio provider集合が設定defaultだけでfallbackを決めていた。
- 変更境界: canonical request/context、Reader queue/engine、PipelineConfig adapter、Studio composition。
- 非対象: public CLI/method名、run layout、legacy DTOの即時削除。
- 受入: scene/emotion contextがprovider直前resolved値へ到達、provider追加keyの値・型保持、
  runtime primary/override/fallbackの必要集合を各1回構築。
- 実行証拠:
  - F16/F18/F19 minima → **4 passed**（F16はReader実Planからprovider直前まで追加）
  - parameter/config/Studio/Bridge/Pipeline/Reader suites → **82 passed, 1 skipped**
  - 対象source/testへのRuff → **All checks passed**

### RT-006

- 対象: F17、D03
- 直接原因: legacy `TTSCache`分岐が完全keyを捨て、同一missを同期するownerがなかった。
- 変更境界: SynthesisService cache key/flight registry、TTSCache schema v2 canonical API。
- 非対象: 旧entryの破壊的変換、process間single-flight、外部distributed cache。
- 受入: resolved intent/provider/format/versionを含むkey、旧schema miss、VoiceIntent差は別entry、
  concurrent同一missはprovider 1回、registry/counter 0へ回収。
- 実行証拠:
  - F17/D03/corrupt canonical cache minima → **4 passed**
  - synthesis/audio/provider/cache隣接suite → **47 passed**
  - 対象source/testへのRuff → **All checks passed**

### RT-007A

- 対象: F04、F09、D02 lifecycle部分
- 直接原因: legacy managerのtask状態とread admissionのoperation leaseが永続しなかった。
- 変更境界: TTSManager worker/lookahead registry、server admission lease。
- 非対象: public manager/CLI削除、HTTP schema/error mapping（RT-008）。
- 受入: double-start同一task、drain/stop finite、blocked provider cancel、read同時200/409かつ1実行。
- 実行証拠: F04 **3 passed**、F09 **2 passed**、D02共通normal/cancel **4 cases passed**、
  Readerの既存cancel/orphan assertion green、Ruff green。完了監査でSlack provider call 0のredを
  再現し、`enqueue → drain → stop`へ修正後provider call 1/task 0/close 1でgreen。さらにread leaseが
  supervisor submit直後に解放されるred（実行中2件目も200）を再現し、guarded operationの全terminal
  pathで解放するownerへ修正後、実行中409・終了後200でgreen。

### RT-007B

- 対象: F11、F14
- 直接原因: Batch jobがengineをfinallyで所有せず、LLM counterがservice lifetimeに属し、
  retry前にbudgetを再検査しなかった。
- 変更境界: BatchJobManager resource scope、LLMService context-local physical budget。
- 非対象: provider/sessionの外部注入owner、実LLM serviceの可用性。
- 受入: 4 terminal path close各1、job A/B独立、retryを含むtransport callが上限以下。
- 実行証拠:
  - `tests/test_runtime_jobs.py` → **7 passed**
  - LLM/Batch隣接suite → **35 passed**、server lifespan → **4 passed**
  - `test_llm_platform.py` のlocalhost bind型contractはsandboxがsocket bindを拒否したため、
    実装failureではなく最終suite時にsandbox外承認で再実行する。
  - 対象Ruff → **All checks passed**

### RT-008

- 対象: F13
- 直接原因: transport modelが許容範囲を型で制約せず、provider domain errorとHTTP statusの
  対応、およびSSE job存在確認がresponse開始前に確定していなかった。
- 変更境界: FastAPI request DTO、`ProviderError` exception handler、batch event route。
- 非対象: provider内部retry、非WAV codec追加、既存CLI command/public SDK。
- 受入: 不正input/未知modeを副作用前4xx、分類済み障害を502/503/504、missing SSEを
  stream開始前404、上流のURL/資格情報/error本文をresponseへ出さない。
- 意図的HTTP互換変更: `/synthesize` は空白text、`speed <= 0`/`speed > 5`、
  `intonation`範囲外、`intensity`範囲外、`output_format != wav` を422にする。
  `/batch/synthesize` は未知provider modeと未知formatを422にする。provider timeoutは504、
  unavailable/rate limitは503、その他の上流合成障害は原則502となる。
- 実行証拠:
  - `tests/test_server_runtime_failures.py -k f13 -vv` → **14 passed, 2 deselected**
  - 対象source/testへのRuff → **All checks passed**
  - 隣接suiteの最初の指定path `tests/test_server.py` は現存せず収集errorとなったため、
    現行server test pathを列挙し直してRT-009後のcontract/full suiteで実行する。

### RT-009

- 対象: F20、D04
- 直接原因: health checkerが`storage` sectionしか参照せず実際の`batch.output_dir`を落とし、
  provider healthのORだけでoperationが必要とするtarget/format/toolを表せなかった。
- 変更境界: `HealthChecker`のstorage解決と、typed provider capabilityに基づくoperation別report。
- 非対象: dependencyの起動、directory作成、codec/provider実サービスの成功断定。
- 受入: 注入済み設定の実output pathを報告し、immediate playback、batch audio、video、Slackごとに
  不足するprovider target/format、storage、ffmpeg/codec、Slack設定を副作用なしで列挙する。
- 実行証拠:
  - `tests/test_readiness_capabilities.py -vv` → **9 passed**。audio bytesはimmediateのみ、
    local/VM fileはbatch/videoのみready、format不一致・unhealthy providerは不足と判定。
    VM fileはmount、Slackはtoken/SDKを別dependencyとして判定し、packaged settingsの実outputと
    `/health/ready`のoperation matrixも直接固定。
  - health/readiness/server隣接suite → **29 passed**、全unit/contract suiteもgreen
  - 対象source/testへのRuff → **All checks passed**
- 自己レビュー: probeはdirectory/job/processを作成せず、path文字列やprovider exceptionを返さない。
  実ffmpeg codecは宣言済みcapabilityがある場合だけreadinessで判定し、path-only環境はRT-010の
  実binary smokeで確認する。

### RT-010

- 対象: E01〜E04、D01/D02の最終architecture固定、全suite/smoke/compat確認。
- 変更境界: 明示marker付き実環境testまたは再現可能な実行記録、compat/architecture test、進捗文書。
- 非対象: 利用不能serviceをmock成功で代替、credentialの表示・保存、実環境green前のlegacy削除。
- 受入: 利用可能なmatrixのversion/config/time/exit/artifact/checksum/cancel後ownerを記録し、
  利用不能条件はbinary/service/credential/license/device、command、期待結果、採取証拠、再開地点を残す。
- scope決定: ユーザーがVOICEPEAKを保有しておらず、Slackも一旦対象外とする判断をしたため、
  E03/E04は未検証のまま適用外とし、E01/E02と全非依存検証をRT-010の完了証拠とする。
- architecture/compat証拠:
  - `tests/test_parameter_resolution.py` → **10 passed**。D01は`TTSParams` adapter 1回と
    SDK/Reader/Batch/Studioのprovider直前parameter完全一致をassertする。
  - `tests/test_tts_manager_lifecycle.py` → **8 passed**。D02はlegacy manager/PlaybackQueueへ
    cancel/正常drainの同じparameterized lifecycle contractを適用する。
  - `tests/test_runtime_jobs.py` → **8 passed**。F03はfake Popenのterminate/kill/wait、worker join、
    owned process registry 0を直接assertする。
  - 公開`TTSBridge.synthesize`、`TTSParams`、既存CLI command、run layout、既存manifest readerは保持。
    新旧経路を同一requestで二重実行する変更はない。
- E04非依存cleanup:
  - 現行`SlackMonitor`がowned `SocketModeClient`をcloseしないことをコードとred test
    (`close_calls == 0`) で再現した。
  - `start()`の`finally`でSDKのasync `close()`を1回awaitし、cancel時もsocket/session/SDK taskを
    SDK ownerへ回収させる。fake client testは **1 passed**。これは実workspace E04の代替ではない。

## 5. 実環境 E01〜E04

検証hostは Linux `6.8.0-138-generic` x86_64、Python `3.12.3`。E2E実行時は未commitの
worktreeだったが、最終source/testはcommit `214f29c`へ固定した。本書の実測artifact IDと
checksumを合わせて検証時点の識別子とする。
credential値と完全endpointは記録していない。

| ID | 状態 | 条件 / 実行コマンド | 結果・証拠 | 再開地点 |
|---|---|---|---|---|
| E01 | **green** | VoiSona実API/VM共有。`pytest tests/e2e/test_runtime_refactoring_real.py::test_e01_voisona_local_cancel_tracks_remote_job_and_followup_queue -vv -s` | 最終再実行job `ff5010d4-322b-49e3-a65c-0f62a1ec700d`のlocal poll cancel後、remoteは `running → succeeded`、remote fileあり。後続queueも成功。artifact `output/runtime-e2e/e01-followup-e15a49ec56fd42e6be4dfab98bfad6ec.wav`、124,560 frames、2.595 s、SHA-256 `b4f8c3f95d65cfcf131c3777dd5d12b9fd59305a509dbcfacd881466891ec2f0`。remote cancel methodのOpenAPI/OPTIONS probeはtimeoutし、存在を推測しない | 再検証時は同test。期待する現行仕様はlocal pollだけcancel、remote job terminalと後続queueを必ず観測 |
| E02 | **green** | 同じ共有を指すLinux `output/` と絶対VM `Z:\runtime-e2e\...`。全E2E command内で実行 | 最終再実行artifact `output/runtime-e2e/e02-a1ce2f94692b4ebeba286f1927c506f0.wav`、278,924 bytes、48 kHz/mono/139,440 frames/2.905 s、SHA-256 `f4852e68ab60b9f2e50e0a54d84d7f2505156e12c37a16bcaf9ab885c30ba8b6`。成功後visibility 0.765 ms、観測sizeは完成値1個だけ | 同testでVM絶対path、descriptor、checksum、`observed_sizes[0] == final size`を再採取 |
| E03 | **適用外（ユーザー判断）** | `voicepeak.path`がnull、binaryなし、license/narrator未提供。user configへ実path/default narratorを設定後、`.venv/bin/python -m pytest tests/e2e/test_runtime_refactoring_real.py::test_e03_voicepeak_serializes_narrators_and_reaps_cancelled_process -vv -s` | 現在は `voicepeak is not available` で明示skip。VOICEPEAKを保有していないため今回の完了条件から除外し、実検証済みとは表記しない | 将来導入する場合だけ、executable binary、license、2 narratorを用意して上記testを再開 |
| E04 | **適用外（ユーザー判断）** | `slack_sdk`なし、bot/app tokenなし、test workspace/network断操作権限なし。`uv sync --extra dev --extra slack`後にtokenを環境変数だけで注入し、`.venv/bin/python -m pytest tests/e2e/test_runtime_refactoring_real.py::test_e04_slack_cancel_closes_real_socket_and_sdk_tasks -vv -s`。SIGTERMは `timeout --signal=TERM --kill-after=10s 60s uv run yomiage slack start` | 現在はoptional dependency不足で明示skip。Slackを一旦利用対象外としたため今回の完了条件から除外し、fake cleanup contractを実成功の代替にはしない | 将来再開する場合だけ、Socket Mode test workspace、token、SDK、network断可能hostを用意して実test→CLI SIGTERMの順に実行 |

追加matrix inventory:

- `ffmpeg`: `command -v ffmpeg`、versionとも不在。mp3/ogg/video codec実検証は未実施。
- VOICEVOX: 全E2E時に4件がservice unavailableで明示skip。
- sounddevice: Python module検出後のimportが `OSError: PortAudio library not found`。実output device、
  cancel後再生は未検証。`/usr/bin/aplay`だけ存在する。
- Slack: optional dependency、両tokenとも不在。token値は表示していない。

2026-08-30のblocker再監査:

- typed settingsの`providers.voicepeak.path`は未設定。raw YAMLの候補pathも
  `exists=False / executable=False`で、`PATH`上にもbinaryなし。
- `slack_sdk`、`SLACK_BOT_TOKEN`、`SLACK_APP_TOKEN`、検証channelはいずれも利用不能
  （credential値は参照・表示していない）。
- E03/E04の2 nodeを`-vv -rs -s`で再実行し、E03は`voicepeak is not available`、E04は
  `E04 requires the slack optional dependency`により **2 skipped in 0.03s**。再開条件と一致した。

## 6. 最終検証

| 種別 | コマンド | 結果 |
|---|---|---|
| unit/contract全件 | `timeout 1800s .venv/bin/python -m pytest -m 'not e2e' -q --tb=short` | **734 passed, 24 skipped, 19 deselected in 9.19s** |
| 実環境E2E全件 | `timeout 600s .venv/bin/python -m pytest tests/e2e/ -vv -s --tb=short` | **4 passed, 15 skipped in 7.82s** |
| CLI smoke | `.venv/bin/python -m pytest tests/test_cli_matrix.py -q` | **40 passed**。root/Reader/Batch/Studio/News/Slack/tuner/serveの全helpと互換patch point |
| REST smoke | `.venv/bin/python -m pytest tests/test_server_runtime_failures.py tests/test_readiness_capabilities.py::test_d04_http_readiness_returns_operation_capability_matrix -vv -s --tb=short` | **17 passed in 1.55s**。validation/error/SSE/admission/operation readiness |
| SDK smoke | `.venv/bin/python -m pytest tests/test_api_bridge.py tests/test_api_pipeline.py -q --tb=short` | **40 passed, 1 skipped in 0.35s**。SDK synth、Pipeline |
| D01/D02/F03 | parameter/lifecycle/runtime job 3 filesを個別実行 | **10 + 8 + 8 passed** |
| docs contract | `.venv/bin/python -m pytest tests/test_docs.py -q --tb=short` | **32 passed** |
| Ruff | `.venv/bin/python -m ruff check src/ tests/` | **All checks passed** |
| diff hygiene | `git diff --check` | green。reset/revert/deleteなし。source/testはユーザー依頼後に`214f29c`へcommit |

focused REST smokeをcapture有効の`-q`で反復した1回は、10秒後のfaulthandlerでroute/lifespan内ではなく
Starlette TestClientのblocking portal `__enter__`待ち（portal loopはidle）を観測した。同じ17 nodeを
`-vv -s`で直後に再実行してgreen、さらにcapture有効の全734件でもgreenであり、runtime failureとしては
再現しなかった。再発時は上記`-vv -s -o faulthandler_timeout=10`でthread stackを採取する。

## 7. 変更面と完了判断

- canonical contract: `ports/tts.py`、`application/voice_parameters.py`、`application/synthesis.py`、
  legacy adapter、Bridge/Reader/Batch/Studio/provider translators。
- runtime ownership: `application/operations.py`、VOICEPEAK/VoiSona/playback、Batch/job manager、
  ffmpeg call sites、Slack monitor。
- artifact/readiness/HTTP: audio utils/cache、health、server、API reference。
- 回帰test: provider runtime、audio、parameter、job、readiness、server、playback/Slack lifecycle、
  E01〜E04 marker test、および既存entrypoint suitesの期待値。
- 互換変更: HTTPの意図的422/502/503/504、missing SSE 404、`/health/ready`のadditiveな
  operation matrixを`api-reference.md`へ記録。CLI command名、`output/works/{work_id}/runs/{run_id}`、
  既存manifest読取、公開Bridge/`TTSParams`は維持。
- 必須scopeの残課題はない。E03/E04と追加matrixの実binary/service/deviceはmock greenに置換せず、
  ユーザー判断による適用外または将来の任意検証として記録を保持する。
