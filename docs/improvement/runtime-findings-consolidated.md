# Runtime finding 統合・再検証結果

- 検証日: 2026-08-30
- 対象: 検証時点の作業ツリー
- 状態: 実装修正前の基準線
- 後続資料: [runtime-refactoring-guide.md](runtime-refactoring-guide.md)

この文書は、`docs/improvement/` にあった4件のruntimeレビューを、原因単位で重複排除し、
現行コードと既存テストで再検証した正本である。単なるスタイル指摘、再現根拠のない推測、
主観的な音質評価は含めない。

分類は次の意味で用いる。

- **確認済みバグ**: 現行コードの制御フロー、局所再現、またはテストで誤動作を確認したもの。
- **設計負債**: 現時点で常に誤結果になるとは限らないが、同じ不具合を複数経路へ再発させる構造。
- **実環境で要検証**: コードだけでは成否を確定できず、実サービス、デバイス、ライセンスが必要なもの。

## 0. 検証結果の要約

- Critical相当はなかった。
- 確認済みバグ: 20件
- 設計負債: 4件
- 実環境で要検証: 4件
- 対象テスト: 212 passed、1 skipped
- 検証環境には `ffmpeg`、設定されたVOICEPEAKバイナリ、`slack_sdk` がなかったため、
  それらを必要とする項目は実環境で成功したとは判定していない。

実行した回帰テスト:

```bash
.venv/bin/python -m pytest -q \
  tests/test_synthesis_service.py \
  tests/test_api_bridge.py \
  tests/test_batch_synthesis_service.py \
  tests/test_health_observability.py \
  tests/test_task_supervisor.py \
  tests/test_server_lifespan_jobs.py \
  tests/test_reader_plan.py \
  tests/test_voice_profile.py \
  tests/test_voicepeak_profile.py \
  tests/test_voicevox_profile.py \
  tests/test_config_settings.py
```

既存テストが通ることは、以下の境界条件が未検査であることと両立する。各findingの
「最小回帰テスト」は、その不足を1本のテストまたは最小のparameterized testで固定する条件である。

## 1. 優先順位付きfinding一覧

### P0: 停止不能、成果物破損、または主要経路の契約破綻

| ID | 分類 | finding | 再検証した根拠 | 最小回帰テスト |
|---|---|---|---|---|
| F01 | 確認済みバグ | VoiSonaの即時再生、file出力、VM共有パスが単一の出力契約になっておらず、1発話を二重合成し得る。Batchではcanonical run pathの親要素を落とし、Windows APIへ`Path`も渡し得る。 | `src/yomiage/adapters/tts/legacy.py:95,164`、`src/yomiage/adapters/persistence/filesystem.py:52`、`docs/virtiofs-setup.md:3,141-145`。局所再現ではVM側が `Z:\\run\\...` となり、`works/{work_id}/runs/{run_id}` を保持しなかった。 | Immediate、Batch、Studioをparameterizeし、provider呼出しが発話ごとに1回、Windows pathがJSON化可能な文字列、host/VM pathが同じ共有root相対位置を指すことを検証する。 |
| F02 | 確認済みバグ | VOICEPEAKのsynthesis、health、voice列挙に共通の排他・deadline・cancel cleanupがない。cancel時に子プロセスをkill/waitしない経路がある。 | `src/yomiage/application/synthesis.py:262`、`src/yomiage/tts/voicepeak.py:112,196`。fake processでcancelしても `kill=0`、`wait=0` を確認した。 | health/list/synthesizeを同時実行し最大子プロセス数が1、timeout/cancelで必ずterminateまたはkill後にwaitされ、子が残らないことを検証する。 |
| F03 | 確認済みバグ | Batchの結合・動画処理が同期 `subprocess.run` でASGI event loopを停止させる。 | `src/yomiage/batch/job_manager.py:193`、`src/yomiage/batch/engine.py:982`、`src/yomiage/tts/audio_utils.py:84`、`src/yomiage/video/composer.py:218`。sleepするfake ffmpeg中にevent loopの最大gap約0.160秒を確認した。 | sleepするfake ffmpegとhealth heartbeatを同時に動かし、heartbeatが継続し、cancel後に子プロセスが残らないことを検証する。 |
| F04 | 確認済みバグ | legacy `TTSManager` のstart/stop/drain所有権が不明確で、Slack経路の未起動、二重start、provider停止時のstop hangを起こす。 | `src/yomiage/tts/manager.py:115,266`、`src/yomiage/interfaces/cli/slack.py:35`。二重startで旧drain taskが完了せず、provider停止中のstopも完了しないことを再現した。 | start-before-use、double-start、stop-before-drain、provider停止中cancel、Slack起動を表にし、合成がちょうど1回で終了後taskが0件になることを検証する。 |
| F05 | 確認済みバグ | WAV bytesの単純連結がヘッダー境界を壊し、入力間のformat差も検証しない。 | `src/yomiage/tts/audio_utils.py:115`、`src/yomiage/api/long_text.py:51`、`src/yomiage/tts/voicepeak.py:88`。100 frame + 100 frameが176 frameになった。 | 100 frameのWAVを2個結合して200 frameになること、sample rate/channel/sample width不一致を拒否または明示的に変換することを検証する。 |

### P1: 通常利用で誤った音声・状態・HTTP結果を返す

| ID | 分類 | finding | 再検証した根拠 | 最小回帰テスト |
|---|---|---|---|---|
| F06 | 確認済みバグ | TTSパラメータのabsolute/delta/unsetと合成順が未定義で、既定intonationが2.0、profile speedの上書き、neutralへのreset不能、VoiSona emotion offsetでintensity無視、Pipelineのscene値消失が起きる。 | `src/yomiage/core/models.py:137`、`src/yomiage/adapters/tts/legacy.py:83`、`src/yomiage/tts/voisona.py:74`、`src/yomiage/tts/voicevox.py:52`、`src/yomiage/api/bridge.py:427`、`src/yomiage/tts/manager.py:178`、`src/yomiage/tools/voice_profile.py:103`、`src/yomiage/api/pipeline.py:159`。 | providerごとにdefault、明示neutral、profile、scene、emotion intensityを組み合わせ、最終provider requestをgolden値と比較する。 |
| F07 | 確認済みバグ | provider-native値とvoice IDの翻訳境界がなく、fallback先へ元providerのpitch等を再利用する。VOICEPEAK Batchでは値を二重変換し、narrator指定も失う。 | `src/yomiage/application/synthesis.py:387`、`src/yomiage/batch/engine.py:284`、`src/yomiage/batch/voicepeak_synth.py:48`、`src/yomiage/tts/voicepeak.py:48`。native/narrator指定が最終的に `DEFAULT, 200, -300` となる再現を確認した。 | VoiSona失敗からVOICEVOX fallback時にvoice/paramsを再解決すること、VOICEPEAK native値とnarratorが最終CLI引数へ一度だけ変換されることを検証する。 |
| F08 | 確認済みバグ | 非空なら壊れたprovider/cache音声も成功として受け入れ、cache書込みもatomicでない。 | `src/yomiage/application/synthesis.py:365,588`、`src/yomiage/tts/cache.py:35`。`b"not-a-wav"` のcache hitがsynthesizedとして返った。 | HTML、短いRIFF、truncated cache、宣言format不一致を入力し、fallback対象になりcacheへpublishされないことを検証する。書込み中断後に旧entryまたはmissになることも確認する。 |
| F09 | 確認済みバグ | `/api/read` の同時実行admissionがcheck-then-submitで、2要求がともに受理されるTOCTOUがある。 | `src/yomiage/server.py:96,1137`、`src/yomiage/application/jobs.py:289`。barrier付き再現でstatusが `[200, 200]`、`read_url` が2回だった。 | 2要求をbarrierで同時到達させ、結果が200と409、`read_url` が1回だけであることを検証する。 |
| F10 | 確認済みバグ | VoiSona poll timeoutが論理的なsleep加算で、HTTP待機時間を含むwall-clock deadlineになっていない。 | `src/yomiage/tts/voisona.py:177`、`src/yomiage/bootstrap.py:202`。名目0.03秒のtimeoutが約0.151秒かかった。 | 遅延するGETをfakeし、開始から許容誤差内のwall-clock timeoutで終了することを検証する。 |
| F11 | 確認済みバグ | Batch jobが作成したEngine/LLM sessionを成功、partial、例外、cancelの全経路でcloseしない。 | `src/yomiage/batch/job_manager.py:123`、`src/yomiage/batch/engine.py:1149`。局所再現でengine close回数0だった。 | 4終了経路をparameterizeし、所有するengine/sessionのcloseが各1回だけ呼ばれることを検証する。 |
| F12 | 確認済みバグ | legacy file destination adapterがcancel時に一時ファイルを残す。 | `src/yomiage/adapters/tts/legacy.py:89`。cancel後にtemporary fileが残る再現を確認した。 | success、provider例外、timeout、cancelの各経路でtempが消え、成功時だけfinalが存在することを検証する。 |
| F13 | 確認済みバグ | REST入力validation、error mapping、stream開始前conditionが不足する。空白text、負speed、範囲外intensity、未知formatを受理し、未知modeをVOICEVOX扱いし、非WAV変換は500、存在しないjobのSSEは200空bodyになる。 | `src/yomiage/server.py:967,1078,1307`、`src/yomiage/batch/engine.py:156`。上記入力のmodel受理、非WAV HTTP 500、missing SSE HTTP 200を確認した。 | 不正入力と未知modeが副作用なし4xx、provider障害が分類に応じ502/503/504、missing SSEがstream開始前404になることを検証する。 |
| F14 | 確認済みバグ | LLMの「jobごと」のrequest budgetがservice lifetime counterで、retryによる物理requestも上限を超え得る。 | `src/yomiage/llm/service.py:102,319`、`src/yomiage/bootstrap.py:93`。job Aで上限消費後、job Bの最初のcallもbudget errorになった。 | job AとBが独立budgetを持つこと、retryを含む物理request数が設定上限以下であることを検証する。 |
| F15 | 確認済みバグ | `sounddevice` 再生をexecutor上でcancelしても `sd.stop()` やthread/device終了を保証しない。 | `src/yomiage/tts/playback.py:44`。executor futureのcancelだけではblocking `sd.wait()` を停止できない制御フローを確認した。 | blocking fake `sd.wait()` をcancelし、`sd.stop()`、worker終了、次回再生成功を検証する。 |

### P2: 経路差、設定欠落、運用上の誤判定

| ID | 分類 | finding | 再検証した根拠 | 最小回帰テスト |
|---|---|---|---|---|
| F16 | 確認済みバグ | Readerはemotion/intensityタグを保持するが、通常のPlan経路にscene/profileを数値・styleへ解決する処理がなく、他入口と同じ効果にならない。 | `src/yomiage/reader/engine.py:328,464`、`src/yomiage/application/planning.py:276`、`src/yomiage/reader/playback.py:177`。元レビューの「emotionが完全に消える」は過大であり、この表現へ修正した。 | 同じ話者でdaily/neutralとbattle/angryを読み、最終provider parameter/styleが異なることを検証する。 |
| F17 | 確認済みバグ | 組込みTTS cache keyが `VoiceIntent` を含まず、同一textとmetadataなら異なる感情・pitchでも誤hitする。 | `src/yomiage/application/synthesis.py:712`、`src/yomiage/tts/cache.py:22`。pitch/emotionだけ変えた2回目がcache hit、provider call 1回だった。 | 同一text/provider/metadataでVoiceIntentだけ変え、2 provider callsと2 entriesになることを検証する。 |
| F18 | 確認済みバグ | `PipelineConfig` がprovider固有の追加設定をround-trip時に落とす。 | `src/yomiage/api/config.py:105`。path、pitch、max_chars等を含むprovider mappingが空dictになった。 | 複数providerの未知だが許可された追加keyをfrom/to変換し、値と型を保持することを検証する。 |
| F19 | 確認済みバグ | Studioのprovider override/fallbackと、起動時に構築するprovider集合の解決規則が一致しない。 | `src/yomiage/studio/engine.py:50,248`。 | default=voicevox、utterance override=voicepeak、fallbackありのplanで必要providerが全て1回だけ構築されることを検証する。 |
| F20 | 確認済みバグ | readinessが実際の `batch.output_dir` を見ず、配布時既定設定でもstorageを `not_configured` と報告し得る。 | `src/yomiage/application/health.py:351`、`src/yomiage/server.py:589`、`config/default.yaml:148`。既定構成のstorage entriesが空だった。 | packaged default settingsからappを構築し、実際のoutput pathについてwritable/capacityを報告することを検証する。 |

### P1〜P3: 設計負債

| ID | 優先度 | 分類 | finding | 最小回帰テスト |
|---|---|---|---|---|
| D01 | P1 | 設計負債 | `TTSParams`、`VoiceIntent`、metadata、provider kwargs、入口別builderが同じ概念を別表現で持つ。F06/F07/F16/F18の共通原因。 | 全入口から同じcanonical requestを与え、provider adapter直前のrequestが一致するcontract testを置く。 |
| D02 | P2 | 設計負債 | `UtterancePlaybackQueue` とlegacy `TTSManager` が別のlifecycleを実装する。F04/F15の修正を二重化する。 | 共通lifecycle suiteを両実装へ適用し、start、cancel、drain、closeの同じ不変条件を検証する。 |
| D03 | P3 | 設計負債 | cacheにsingle-flightがなく、同一keyの同時missでproviderを重複実行する。現時点では結果の正しさより費用・負荷の問題。 | 同一keyを同時要求し、provider callが1回、全waiterが同じ検証済み結果を受け取ることを検証する。 |
| D04 | P2 | 設計負債 | readinessが「どれか1 providerがhealthy」までで、file出力、VM mount、codec等の要求capabilityを判定しない。 | bytes再生、batch file、video等のcapability matrixごとにreadinessが不足要件を列挙することを検証する。 |

### 実環境で要検証

| ID | 優先度 | 分類 | 確認事項 | 最小実環境テスト |
|---|---|---|---|---|
| E01 | P0 | 実環境で要検証 | VoiSonaのlocal timeout/cancel後もremote queueが処理を継続するか。 | 長い発話を投入してtimeout/cancelし、APIのjob状態、後続queue、remote playback/file生成を追跡する。 |
| E02 | P0 | 実環境で要検証 | VoiSonaが成功を返した時点でvirtiofs上のWAVがLinux側から完全に読めるか。 | API成功直後からhost pathをopen/RIFF検証し、visibility latencyと部分ファイル有無を記録する。 |
| E03 | P0 | 実環境で要検証 | 実VOICEPEAKの同時起動、license制約、cancel、narrator/CLI引数の受理。 | 実binaryでhealth/list/synth競合、timeout/cancel、narrator切替を行い、process treeと終了codeを記録する。 |
| E04 | P1 | 実環境で要検証 | Slack Socket Modeのdisconnect/close時にSDK側task/socketが残らないか。 | test workspaceでconnect、message、network断、SIGTERMを実行し、socket/taskと再接続状態を確認する。 |

## 2. 同一原因として統合したfinding

| 統合後 | 統合した論点 | 同一原因と判断した理由 |
|---|---|---|
| F01 | VoiSona Windows path、bytes/file/remote出力、二重合成 | providerの出力capabilityとdestinationが型で区別されず、入口とadapterが個別に補正している。 |
| F02 | VOICEPEAK synthesis、health/list、preflight、cancel | 同一CLI binaryの全operationに共通process ownerがない。 |
| F03 | Batch concat、動画ffmpeg、同期subprocess | ASGI taskからblocking processを直接呼ぶ境界が共通。 |
| F04 | Slack未起動、double-start、stop hang | legacy managerのlifecycle stateとtask所有権が共通原因。 |
| F06 | intonation、unset/default、profile/scene順、VoiSona intensity | canonical parameter意味論と合成順がない。 |
| F07 | fallback native値、VOICEPEAK二重変換、narrator消失 | provider非依存値とnative値の翻訳境界がない。 |
| F08 | providerの壊れたbytes、壊れたcache、非atomic cache | 検証済みaudioだけをpublishする境界がcache前後で欠けている。 |
| F10 | VoiSona poll timeout、HTTP待機のdeadline超過 | operation全体の絶対deadlineではなく局所sleep/call timeoutを使う。 |
| F13 | REST validation、未知mode、error mapping、SSE precondition | transport境界でtyped validationとdomain error変換を完結していない。 |
| F14 | job budget持越し、retryで上限超過 | budgetのownerと「論理call/物理request」の計数対象が未定義。 |
| F20 | storage readiness誤判定、provider capability不足 | 実際に実行するoperationの設定・capabilityからreadinessを組み立てていない。後者は構造問題なのでD04にも分離した。 |

## 3. 誤検知または根拠不足として除外したfinding

| 除外した主張 | 判定と理由 |
|---|---|
| Immediate RESTの非WAV変換がevent loopをffmpegで停止する | 誤検知。現行コードは `AudioResult.convert()` がないためffmpeg到達前に500となる。500自体はF13へ残した。 |
| provider基底speedへratioを乗算すること自体が誤り | 根拠不足。比率合成としては整合しており、誤りはabsolute/delta/unsetが混在するF06である。 |
| VoiSona/VOICEVOXのstyle配列順またはclampが音質を必ず損なう | 実音声とprovider仕様での比較がなく、コードだけではバグ確定不能。 |
| checkout用設定とpackaged defaultの差は常にバグ | 誤検知。環境別既定値の差は意図的に存在し得る。実行時に参照した設定とreadinessの不一致だけをF20とした。 |
| localhost endpointは常に誤設定 | 誤検知。host networkまたはpublished port構成では正しい。 |
| `TaskSupervisor`、`UtterancePlaybackQueue`、`HTTPFetcher` は現在close漏れする | 現行の所有権テストとコード上、指摘された経路ではcleanupされる。新しい反例がないため除外した。 |
| `SynthesisService` のcoroutine cancel/retry/fallback自体が壊れている | 現行テストでは成立する。provider process、cache、parameter等の外側の副作用だけを個別findingにした。 |
| fallback voiceの主観的な不一致・音質差 | 期待値が仕様化されておらず再現可能な正誤判定ができない。native ID流用だけをF07とした。 |

## 4. 依存関係を考慮した修正順序

1. **外部operation境界**: F02、F03、F10、F15。monotonic deadline、排他、cancel時の
   terminate/kill/waitまたはdevice stopを先に共通化する。
2. **provider capability・成果物・audio境界**: F01、F05、F08、F12。bytes、remote playback、
   local/VM fileを区別し、WAV検証後だけatomic publishする。
3. **canonical parameter契約**: F06を定義してからF07のprovider translatorを実装する。
   逆順ではnative補正が再び入口へ漏れる。
4. **入口・cache・設定の移行**: F16、F17、F18、F19。3で確定したrequestへReader、Studio、
   Bridge、Pipelineを寄せる。
5. **所有権とadmission**: F04、F09、F11、F14。task、engine、budget、leaseのownerを
   application scopeまたはjob scopeへ固定する。
6. **HTTP境界**: F13。確定したprovider error categoryとcapabilityをstatusへ変換する。
7. **readiness**: F20、D04。実際のstorage設定とoperation capabilityを利用する。
8. **実環境ゲートと旧経路撤去**: E01〜E04を実施し、結果を得てからD01/D02のcompatibility
   adapterとlegacy lifecycleを撤去する。

この順序を実装単位へ分割した依存グラフ、変更面、互換性条件は
[runtime-refactoring-guide.md](runtime-refactoring-guide.md) を正とする。

## 5. findingごとの最小回帰テスト

最小回帰テストは各一覧表の最終列を受入条件とする。配置は次を推奨する。

- provider process、deadline、cancel、capability: `tests/contracts/`
- parameter解決とtranslator: `tests/test_parameter_resolution.py`
- WAV/cache/artifact: `tests/test_audio_validation.py` と既存artifact contract
- lifecycle、job ownership、admission: 対応するapplication/server単体テスト
- HTTP schema/error/SSE: `tests/test_server_runtime_failures.py`
- readiness: `tests/test_readiness_capabilities.py`
- E01〜E04: `tests/e2e/` の明示marker付きテストまたは手動記録

修正着手前に、各テストを現行実装へ追加して意図した理由で失敗することを確認する。
ただし外部binaryや実サービスを必要とするテストを通常のunit test完了条件へ混在させない。

## 6. 置換したレビュー文書

次の4文書の内容を本書へ統合し、重複する原本は削除した。

- `runtime-async-lifecycle-review.md`
- `runtime-bug-review-2026-08-30.md`
- `runtime-bug-review-api-failure-handling.md`
- `runtime-parameter-review.md`

以後、findingのID、優先順位、分類、根拠、最小回帰テストは本書を正とする。
