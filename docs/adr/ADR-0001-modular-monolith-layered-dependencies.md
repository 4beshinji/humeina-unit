# ADR-0001: モジュラーモノリスと目標レイヤー

- **状態**: Accepted
- **日付**: 2026-07-30
- **所有タスク**: ARC-001

## コンテキスト

`humeina-unit` は CLI、FastAPI、SDK、Reader、Batch、Studio から同じ音声処理を利用する。
現在は機能別に分かれた既存モジュールがそれぞれ TTS や設定を参照しており、入口ごとに
ポリシーが分岐しやすい。一方、利用者は単一プロセスの CLI としての使い勝手を期待しており、
この改善を理由にマイクロサービス化する必要はない。

## 決定

プロセス分割を行わず、境界を持つモジュラーモノリスとして段階的に移行する。新規コードの
依存方向は次の一方向とする。

```text
interfaces (CLI / FastAPI / SDK)
        ↓
application (use case / service)
        ↓
core (domain model) ←→ ports (protocol / DTO)
        ↑                         ↑
        └──── adapters (TTS / LLM / source / persistence / media)
```

実装上は `src/yomiage/` 以下に次のパッケージを追加する。

| レイヤー | 責務 | 許可する依存 |
|---|---|---|
| `core/` | `UtterancePlan`、Job状態、ドメインエラー | 標準ライブラリ、同じcore内の型 |
| `ports/` | Provider、Source、Repository、Artifactの契約 | 標準ライブラリ、型定義 |
| `application/` | 計画・合成・読上げ・Batch・Jobのユースケース | `core`、`ports` |
| `adapters/` | 具象TTS/LLM、Web、SQLite、ffmpeg | `ports` と外部ライブラリ |
| `interfaces/` | CLI/FastAPI/SDK DTOとcomposition root | `application`、adapterの組立 |

`core` と `application` は FastAPI、Typer、具象TTS Providerを直接 import してはならない。
`ports` はProvider固有の実装・識別子を公開しない。依存方向は AST/import の契約テストで監視し、
既存モジュールを一括移動しない。既存経路は所有タスクID付きの許容リストへ記録し、対応する
移行タスクで段階的に縮小する。

ARC-001時点での既存例外の所有先は次のとおりである（import単位の一覧は
`tests/test_architecture.py` の `LEGACY_IMPORT_ALLOWLIST` を正とする）。

| 既存経路 | 所有タスク |
|---|---|
| CLI とServerのframework/provider直接参照 | `CLN-002` / `API-001` |
| Batch/Studioの具象Provider参照 | `SYN-004` / `SYN-002` |
| Provider factory と tuning tool | `SYN-001` / `CLN-001` |

Provider、Repository、Service、UseCaseの実体生成はcomposition root（候補:
`src/yomiage/bootstrap.py`）へ集約する。CLI、FastAPI lifespan、SDK factory は同じ組立処理を
利用し、入口が具象Providerを直接生成しない。

## 理由

- モジュラーモノリスなら既存の CLI と成果物互換性を保ったまま境界を検証できる。
- 内側の契約を先に安定させることで、Reader、Batch、Studio、APIの移行順を独立して選べる。
- ASTテストは実行時 import や外部サービスを必要とせず、禁止依存の再導入を早期に検出できる。

## 影響

新規機能は上記レイヤーへ配置し、旧 `tts/`、`sources/`、`nlp/`、`video/` は当面 adapter として
再利用する。既存コードの直接依存は直ちに削除せず、SYN-*、DOM-*、JOB-*、API-*、CLN-* の移行
タスクが対応する。レイヤー境界を越える変更は、公開契約を変える前に追加ADRを作成する。

## 却下した選択肢

- **マイクロサービス化**: 単一ユーザー向け運用に対してデプロイ・障害・データ整合性の負担が
  大きく、今回の依存方向問題を解決しない。
- **既存パッケージの一括移動**: import互換性とロールバックを壊し、回帰の原因を特定しにくい。
- **入口ごとの個別実装維持**: retry、fallback、partial成果物の意味が再び分岐する。
