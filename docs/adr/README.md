# ADR 索引

このディレクトリは、実装上の境界と移行方針を短い Decision Record として固定します。
タスクの状態・受入条件は [work-ledger](../improvement/work-ledger.md) を正とし、ADR は公開契約を
変更する前の設計根拠として参照します。

| ADR | 決定 | 実装/移行タスク |
| --- | --- | --- |
| [ADR-0001: modular monolith と layered dependencies](ADR-0001-modular-monolith-layered-dependencies.md) | interfaces → application → core/ports → adapters の依存方向、composition root | ARC-001、CLN-001/002、SYN-001〜004 |
| [ADR-0002: UtterancePlan と identity](ADR-0002-utterance-plan-and-identities.md) | Provider 非依存 plan、work/plan/run/job の分離、旧 manifest read-only 互換 | DOM-001〜003、RUN-001/002、SYN-004 |
| [ADR-0003: SQLite JobRepository](ADR-0003-sqlite-job-repository.md) | 単一ホストの状態永続化、compare-and-set、artifact は DB 外 | JOB-001/002、API-001/002、OBS-001 |

## 現行ドキュメントとの対応

- [実装アーキテクチャ](../architecture.md): 実ファイル tree、依存方向、canonical layout
- [設定](../configuration.md): typed Settings、immutable package resource、secret 境界
- [REST API](../api-reference.md): loopback default、Bearer、admission、partial semantics
- [Batch](../batch-pipeline.md): plan/run/artifact と completion invariant
- [Python SDK](../sdk-reference.md): TTSBridge、TextAnalyzer、Pipeline facade
- [移行手順](../migration.md): backup、read-only import、検証、rollback

ADR 作成時は状態（Proposed/Accepted/Superseded）、日付、所有タスク、却下案を記載し、実装・
テスト・公開文書の task ID を相互リンクします。新しい ADR を追加したらこの索引を更新します。
