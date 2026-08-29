# Runtime refactoring `/goal` 実行プロンプト

この文書のコードブロック全体を、repository rootを開いたCodexの新しいchatへ貼り付ける。
`/goal` は、明確な成功条件と検証ループを持つ長期作業を複数turnにわたって継続する用途である。
状態確認は `/goal`、一時停止と再開は `/goal pause` と `/goal resume`、破棄は
`/goal clear` を使う。

公式資料:

- [Follow a goal](https://learn.chatgpt.com/use-cases/follow-goals)
- [Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

## 貼り付け用プロンプト

```text
/goal docs/improvement/runtime-findings-consolidated.md のF01〜F20とD01〜D04を、docs/improvement/runtime-refactoring-guide.md の契約・RT依存順・互換条件に従って修正し、各findingの最小回帰テスト、全unit/contract suite、Ruff、代表CLI/REST/SDK smoke testを通す。E01〜E04は利用可能な実環境で検証し、利用不能なら非依存作業をすべて完了したうえで、不足条件と再開手順を再現可能に記録する。全成果をコード、テスト、進捗文書の証拠で検証できる状態にするまで継続する。

作業開始時に、repository rootのAGENTS.mdと次の2文書を全文読んで正本として扱うこと。

- docs/improvement/runtime-findings-consolidated.md
- docs/improvement/runtime-refactoring-guide.md

行番号はレビュー時点の参照なので、修正直前に現行コード、呼出し元、既存テストを再確認する。
レビューの結論を無条件に信じるのではなく、再現しないfindingは、再検証の証拠と理由を進捗文書へ
残してから除外候補にする。推測、スタイル変更、無関係な機能追加へscopeを広げない。

最初に次を行うこと。

1. `git status --short` で既存のユーザー変更を把握し、上書き・巻き戻し・削除しない。
2. 基準テストとRuffを実行し、開始時点の失敗を記録する。
3. `docs/improvement/runtime-refactoring-progress.md` を作成し、RT-001〜RT-010、finding ID、状態、
   変更ファイル、実行テスト、結果、残課題、実環境条件を追跡する。
4. RT-001で各確認済みバグの最小回帰テストを追加し、修正前に意図した理由で失敗することを確認する。
   既に同等のテストがある場合は重複させず、そのpathとassertionを進捗文書へ記録する。

以後はruntime-refactoring-guide.mdの依存順に、原則1つのRTパッケージずつ進めること。

- RT-002: operation deadlineと外部process/device cleanup
- RT-003: provider capability、typed target、VoiSona host/VM path
- RT-004: audio検証、WAV結合、atomic cache/artifact publish
- RT-005A: canonical parameter契約と合成順
- RT-005B: provider translatorとfallback再解決
- RT-005C: Reader/Bridge/Pipeline/Studio移行と設定round-trip
- RT-006: 完全なcache keyとsingle-flight
- RT-007A: playback lifecycleとread admission
- RT-007B: Batch resource ownershipとjob-local LLM budget
- RT-008: HTTP validation、error mapping、SSE precondition
- RT-009: operation別readiness
- RT-010: 実環境E2Eと検証後のcompat/legacy撤去

各パッケージのループは次のとおり。

1. 対象finding、直接原因、変更する境界、非対象、受入テストを進捗文書へ記す。
2. 最小回帰テストをredにし、失敗理由がfindingと一致することを確認する。
3. 既存の `TTSProviderPort`、`SynthesisService`、typed settings、`FilesystemArtifactStore`、
   provider/entrypoint contract testsを優先して拡張する。
4. 最小の実装でgreenにする。入口別の補正、provider固有値のmetadata追加、独自temp処理、
   同期subprocess、timeoutの再開始を新設しない。
5. 対象テスト、隣接contract test、Ruffを実行する。
6. diffを自己レビューし、task/process/session/temp fileの所有権、cancel/error path、public互換、
   secret/log漏えい、manifest/artifact完全性を確認する。
7. 進捗文書へ実コマンドと結果を記録してから次の依存可能なパッケージへ進む。

必ず守る設計条件:

- unset、明示neutral、resolved parameter、provider-native valueを区別する。
- profile、scene、emotion、explicit overrideの適用順を一か所で定義する。
- fallback先のvoiceとnative parameterはVoiceIntentから再解決する。
- memory bytes、local artifact、VM shared file、remote playbackをtyped target/capabilityで区別する。
- remote playback成功を空bytesで表さず、同じ発話を結果取得のため二重合成しない。
- timeoutはmonotonicな絶対deadlineとし、preflight、poll、retry、fallback、backoffを含める。
- cancel/timeout/例外/正常終了のすべてでprocess、task、thread、device、session、temp fileを回収する。
- provider/cache音声はformatとWAV構造を検証した後だけatomic publishする。
- cache keyへresolved VoiceIntent/parameter、provider、format、versionを含める。
- lifecycle resourceはapp、job、operationのownerとclose回数を明示する。
- validationとadmissionを副作用より前に行い、SSE preconditionをstream開始前に確定する。
- readinessは実際に注入されたsettingsとoperation別capabilityから算出する。

互換性条件:

- 既存CLI command名を変更しない。
- `output/works/{work_id}/runs/{run_id}` を変更しない。
- 既存manifestを読み取れる状態を維持する。
- `TTSBridge.synthesize` と `TTSParams` はcompatibility adapterを経由して段階移行し、
  同じ変更で即時削除しない。
- HTTP validation/statusの意図的変更はcontract testと文書へ記録する。
- legacy/new adapterの移行中も、1 requestで一方だけを実行する。
- ユーザーの既存変更をrevert、reset、無断削除しない。commit、push、releaseは明示依頼がない限り行わない。

検証方針:

- fake process、fake clock、小さいWAV fixture、concurrency barrierでunit testを決定的にする。
- sleep後のtask一覧だけに頼らず、所有registry/counterでleakとclose回数をassertする。
- 外部serviceを必要とするtestは明示markerでunit suiteから分離する。
- VoiSona、VOICEPEAK、VOICEVOX、ffmpeg、sounddevice、Slackが利用可能なら、
  runtime-refactoring-guide.mdのE2E matrixを実行し、version、設定、時間、exit code、artifact descriptor、
  checksum、cancel後状態を記録する。
- install、credential利用、VM/service起動、sandbox外操作など新しい権限が必要なら、目的と最小scopeを示して
  approvalを求める。利用不能な環境をmock成功で代替しない。

途中で1項目が外部環境待ちになっても停止せず、依存しないRTパッケージを進める。不明点はrepository内の
コード、設定、テスト、文書から先に解決する。仕様選択で外部挙動が変わりコードから一意に決められない場合だけ、
選択肢、既存挙動、推奨案、影響を進捗文書へ記録し、ユーザー判断を求める。

完了と報告してよいのは次をすべて満たす場合だけ。

- F01〜F20の各IDに、修正箇所とgreenの最小回帰テストが対応している。
- D01〜D04の受入条件がcontract/architecture testで固定されている。
- 全unit/contract suite、Ruff、代表CLI/REST/SDK smoke testがgreenである。
- E01〜E04を実環境で検証済みである。
- source、tests、docs、進捗文書が同期し、未追跡の既知failureがない。
- 最終diffを確認し、無関係なユーザー変更を含めていない。

E01〜E04に必要な環境だけが得られない場合、全体を完了扱いにしない。RT-001〜RT-009と利用可能な
検証をすべて終え、進捗文書へ次を残すこと: 未検証ID、必要binary/service/credential/license/device、
実行予定command、期待結果、失敗時に採取する証拠、再開地点。それ以外に進められる作業がない状態でのみ、
正確なblockerとして報告する。

最終報告には、修正したfinding、設計上の主要変更、追加テスト、全検証結果、互換性変更、実環境結果、
残存blocker、変更ファイルを含める。単に「テストが通った」ではなく、finding IDごとの証拠を示すこと。
```

## 運用メモ

- 目標の途中状態は `/goal` で確認する。
- 一時的に止める場合は `/goal pause`、同じ目的を続ける場合は `/goal resume` を使う。
- 目的を取り消す場合だけ `/goal clear` を使う。
- `/goal` が候補にない環境では、Codex設定の `features.goals` を有効化する必要がある。
- 実環境をすぐ用意できない場合は、E01〜E04を別goalへ分離する短縮も可能だが、元のgoalで
  「すべて完了」とは扱わない。
