# ADR-0003: SQLite JobRepository

- **状態**: Accepted
- **日付**: 2026-07-30
- **所有タスク**: ARC-001

## コンテキスト

現行のジョブ状態はプロセス内のBatch/Serverオブジェクトにも分散しており、再起動すると受付中・
実行中・失敗の履歴を追跡できない。音声バイナリを同じ保存先へ置く必要はなく、まずは単一
プロセスのCLI/APIが再起動とキャンセルを安全に扱える永続状態が必要である。

## 決定

初期の `JobRepository` 実装にSQLiteを採用する。Repositoryの契約は
`ports/repositories.py` に置き、具象実装は `adapters/persistence/sqlite.py`（または同等の
adapterパッケージ）へ隔離する。DBには音声や動画のバイナリを格納せず、次のメタデータだけを
保存する。

- `job_id`、`work_id`、`plan_id`、`run_id`
- 状態、進捗、request options
- `created_at`、`updated_at`、`started_at`、`finished_at`（UTC）
- error category/message（secret・本文・完全URLを含めない）
- cancellation request と、認証導入後の owner/client identity

状態と許可遷移は `core.jobs` に一元化し、Repositoryが任意文字列を受け付けないようにする。
遷移は短いSQLiteトランザクション内でcompare-and-set（期待する現在状態を条件にした更新）を
行い、競合時はlost updateではなく明示的な競合エラーを返す。接続はmigration versionを確認し、
必要に応じてWAL・busy timeout等の単一ホスト向け設定をcomposition rootで適用する。

既定の保存先はmutable data領域（例: `data/jobs.sqlite3`）とし、設定で明示的に変更できる。
Repositoryの再生成後も同じDBを開けばジョブを取得できる。起動時に`synthesizing`等のまま残った
ジョブはJOB-002が`interrupted`として回収し、再開または失敗確定を選択する。

## 理由

- 外部DBを要求せず、単一ユーザー・単一ホストで原子性と再起動耐性を得られる。
- SQLの制約とトランザクションで不正遷移・lost updateを検出できる。
- Repository portを先に固定すれば、将来の別ストレージへの差し替えがアプリケーション層に波及しない。
- 大きな音声データは既存のArtifactStore/ファイル境界に残し、DB肥大化とバックアップ負荷を避けられる。

## 影響と移行

JOB-001で状態モデル、schema migration、create/get/list/compare-and-setを実装する。JOB-002で
TaskSupervisorとFastAPI lifespanをRepositoryへ接続し、起動時回収・キャンセル・shutdownを
追加する。旧プロセス内managerは移行期間中adapterとして残し、SQLite書き込みが確認できてから
既定経路を切り替える。SQLiteファイルはバックアップ可能な単一成果物として扱い、schema変更時は
migration versionを更新する。

## 却下した選択肢

- **メモリのみ**: 再起動で状態が消え、APIの追跡契約を満たさない。
- **JSONファイルをRepositoryにする**: compare-and-setと同時遷移の原子性を一貫して保証しにくい。
- **PostgreSQL/Redisを初期採用する**: 外部運用依存を増やし、単一ホストの目的に対して過剰である。
- **音声バイナリをSQLiteへ保存する**: DBサイズ・I/O・バックアップが膨張し、ArtifactStoreの責務と重複する。
