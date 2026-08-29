# アーキテクチャ改善ロードマップ

このディレクトリは、2026-07-30 時点のアーキテクチャレビューを実装作業へ変換するための
管理文書である。機能追加の要望集ではなく、ネットワーク境界、成果物の完全性、実行経路の
一貫性、配布可能性を段階的に改善するための実行計画として扱う。

関連文書:

- [architecture-plan.md](architecture-plan.md):
  目標アーキテクチャ、移行原則、互換性方針
- [work-ledger.md](work-ledger.md):
  タスクID、依存関係、受入条件、推奨担当、作業状況
- [runtime-findings-consolidated.md](runtime-findings-consolidated.md):
  2026-08-30時点の実装で再検証した、優先順位・分類・根拠・最小回帰テストの正本
- [runtime-refactoring-guide.md](runtime-refactoring-guide.md):
  上記findingを修正するための契約、変更面、作業パッケージ、実環境ゲート
- [runtime-refactoring-goal-prompt.md](runtime-refactoring-goal-prompt.md):
  2文書を正本として後続修正を `/goal` で自走させるための貼り付け用プロンプト

## 1. 改善の目的

この改善では、プロジェクトをマイクロサービスへ分割しない。単一ユーザー向けCLIの使い勝手を
維持しつつ、同じコードをREST APIやPython SDKから利用しても、同一の安全性・完全性・
合成ポリシーが適用される「運用可能なモジュラーモノリス」へ移行する。

達成状態は次のとおり。

1. APIは既定でローカル専用であり、外部公開には明示設定と認証が必要。
2. 欠損した音声を含む成果物を `completed` と報告しない。
3. Reader / Batch / Studio / SDK が同じ合成サービスを利用する。
4. 話者や感情の切り替え単位を `Utterance` として共通化する。
5. 設定と標準リソースがwheelインストール後も解決できる。
6. ジョブ状態と成果物がプロセス再起動後も追跡できる。
7. 主要な境界条件がCI上の契約テストで保証される。

## 2. 前提と見積り

見積りはレビュー担当1名を前提とする。実装ワーカー1名では約16〜20週、
競合しないレーンへ実装ワーカー2名を明示的に割り当てる場合は約10〜12週を目安とする。

| サイズ | 目安 | 用途 |
|---|---:|---|
| S | 0.5〜1日 | 局所的変更、テスト追加 |
| M | 2〜3日 | 1コンポーネントの設計・実装・移行 |
| L | 4〜5日 | 複数コンポーネントにまたがる変更 |
| XL | 6日以上 | 原則として分割してから着手 |

外部TTSを用いるE2E検証の日程は、VOICEPEAKライセンスとVoiSona VMの利用可能時間に
依存するため、通常の完了条件とは分離する。

## 3. ロードマップ

### R0: 安全化と基準線（1〜2週）

目的は、現状の危険な成功条件と公開初期値を先に止め、以降の変更をCIで監視できるようにすること。

対象:

- `GOV-001`: CI基準線
- `ARC-001`: ADRと依存方向ルール
- `SEC-001`: localhost既定化と外部公開ガード
- `SEC-002`: URL取得ポリシーとSSRF防止
- `REL-001`: 部分失敗を `completed` にしない
- `REL-002`: JSON永続化のatomic write

完了ゲート G0:

- `yomiage serve` の既定bindがloopbackである。
- 非loopback bindは認証トークンなしでは起動できない。
- loopback/private/link-local等への汎用URL取得が既定で拒否される。
- `failed` な文が1件でもあればジョブは `partial` または `failed` になる。
- push/PRごとにpytestとRuffが実行される。

### R1: 設定・配布・成果物境界（2週）

目的は、リポジトリ直下でしか動かない設定解決と、同一URLの成果物衝突を解消すること。

対象:

- `CFG-001`: 型付きSettingsとパス解決
- `CFG-002`: 標準設定のパッケージ同梱
- `RUN-001`: `work_id` と `run_id` の分離
- `RUN-002`: ArtifactStore導入

完了ゲート G1:

- wheelを一時環境へインストールし、任意のカレントディレクトリから設定をロードできる。
- 必須secretの未設定は起動時に明示的な設定エラーとなる。
- 同一URLの同時ジョブが別のrunディレクトリへ出力される。
- manifestと音声ファイルの対応関係が検証可能である。

### R2: 共通ドメインモデル（2週）

目的は、文字数チャンクではなく発話単位をコアモデルにし、各入口の意味論を一致させること。

対象:

- `DOM-001`: `Utterance` / `UtterancePlan` / `VoiceIntent`
- `DOM-002`: 現行NLP結果から発話計画への変換
- `DOM-003`: plan schema versionとfingerprint
- `TST-001`: 発話計画のgolden/contractテスト

完了ゲート G2:

- 地の文と複数話者が同一入力に含まれても、別Utteranceとして表現される。
- Reader、Batch、SDKで同じ入力から同等の発話境界が得られる。
- planはschema version、入力fingerprint、設定fingerprintを保持する。
- 旧manifestから新planへ変換でき、既存成果物を破壊しない。

### R3: 合成サービス統一（3週）

目的は、入口ごとに異なるリトライ、フォールバック、キャッシュ、計測を単一サービスへ集約すること。

対象:

- `SYN-001`: `SynthesisService` とProvider契約
- `SYN-002`: 公開SDK / TTSBridge / Studio移行
- `SYN-003`: ReadingEngine移行
- `SYN-004`: BatchEngine移行
- `TST-002`: Provider契約テスト

完了ゲート G3:

- 全入口が同じ `SynthesisService` を経由する。
- Provider固有voice IDを別Providerへ無条件に渡さない。
- fallback、retry、cache、metricsの適用有無が設定で明示される。
- 旧Batch専用synthesizerは互換ラッパーまたは削除対象として明示される。
- VoiSonaのfile destinationを共通契約で扱える。

### R4: ジョブ実行基盤とHTTP境界（2週）

目的は、FastAPIを単一プロセス内デモから、再起動とキャンセルを扱えるサービス境界へ引き上げること。

対象:

- `JOB-001`: Job状態機械とSQLite JobRepository
- `JOB-002`: TaskSupervisor、キャンセル、再開
- `API-001`: app factoryとdependency injection
- `API-002`: 認証、quota、入力制限
- `OBS-001`: liveness/readiness/metrics

完了ゲート G4:

- サーバー再起動後もジョブと最終状態を取得できる。
- 実行中に停止したジョブは `interrupted` として回収され、再開または失敗確定できる。
- APIのグローバルEngine/JobManagerがapp stateまたは依存注入へ置き換わる。
- 同時実行数、キュー長、入力文字数、取得サイズに上限がある。
- livenessとreadinessが区別される。

### R5: 旧経路撤去と運用完成（1〜2週）

目的は、移行用重複を撤去し、設計と実装と運用手順を同期すること。

対象:

- `CLN-001`: 重複TTS/NLP経路の撤去
- `CLN-002`: CLIモジュール分割
- `DOC-001`: アーキテクチャ・設定・API文書更新
- `OPS-001`: 外部サービスE2Eとリリースチェックリスト

完了ゲート G5:

- 禁止した旧経路へのimportがarchitecture testで検出される。
- CLI、REST、SDKの代表smoke testがCIまたは明示的な手動ゲートで通る。
- 存在しないモジュールや未実装機能が設計書に残っていない。
- ロールバック、データ移行、成果物保全手順が文書化される。

## 4. 依存関係の概要

```text
GOV-001 + ARC-001 ─ CFG-001 ─ CFG-002 ──────────────┐
ARC-001 + REL-002 ─ RUN-001 ─ RUN-002 ─ JOB-001 ─ JOB-002
GOV-001 + ARC-001 ─ DOM-001 ─ DOM-002 ──────────────┤
                         └─── DOM-003 ───────────────┤
DOM-001 + CFG-001 + REL-002 ─ SYN-001 ─┬─ SYN-002 ─┤
                                       ├─ SYN-003  │
REL-001 + RUN-002 ─────────────────────└─ SYN-004 ─┤
                                                   │
SEC-001 + SEC-002 + API-001 + JOB-002 ─ API-002 ──┤
                                                   │
SYN-002/003/004 ─ TST-002 ─ CLN-001 ─ DOC-001 ─ OPS-001
```

厳密な依存関係と着手可能状態は
[work-ledger.md](work-ledger.md) を正とする。

## 5. 並列実行方針

既定のWIP上限は実装作業1件とする。複数ワーカーを使う場合は、次の条件を満たす作業だけを
並列化する。

- 変更予定ファイルが重ならない。
- 共通モデルまたは公開インターフェースを同時に変更しない。
- 先行タスクの受入条件が完了している。
- 台帳の `並列レーン` が異なる。

初期に比較的安全に並行できる組み合わせ:

- Lane A: `GOV-001`
- Lane B: `REL-001`
- Lane C: `SEC-001`
- Lane D: `ARC-001`

`DOM-*` と `SYN-*` は公開契約に触れるため、同時に複数ワーカーへ割り当てない。

## 6. スコープ外

このロードマップでは以下を扱わない。

- マイクロサービス化、Kubernetes、分散キューの導入
- UIや管理画面の新規開発
- 新しいTTS Providerの追加
- Ollamaからllama.cppへの移行そのもの
- NLPモデルの精度改善やプロンプトチューニング
- 動画表現やアセット機能の追加

上記はG4以降に再評価する。基盤移行中に同時実施すると、回帰原因と成果判定が不明瞭になる。

## 7. ロードマップ変更ルール

- 重大なセキュリティまたはデータ欠損問題は、現在のフェーズに関係なくR0へ繰り上げる。
- 3日を超えて完了見込みが立たないタスクは分割する。
- 公開API、manifest schema、出力ディレクトリ互換性を変える場合はADRを追加する。
- 完了ゲートを満たさず次フェーズの旧実装を削除してはならない。
- ロードマップ変更時は作業台帳の依存関係と `次の着手候補` も同時に更新する。
