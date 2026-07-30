# ADR-0002: UtterancePlan と run/job identity

- **状態**: Accepted
- **日付**: 2026-07-30
- **所有タスク**: ARC-001

## コンテキスト

現在の Reader と Batch は、解析結果を文字数チャンクや入口固有の manifest として扱う。
そのため、話者・感情の境界、Provider変更時の再利用、同一作品の同時実行を共通に追跡できない。
音声ファイルのパスを解析計画へ直接書き込むと、Providerや出力形式の変更で計画を再利用できなくなる。

## 決定

解析結果と合成実行を `UtterancePlan` で分離する。`Utterance` は一人の話者と一つの
`VoiceIntent` で連続して読める最小単位とし、少なくとも次を持つ。

- plan内で安定した `id` と再生順 `order`
- 正規化済み本文 `text`
- `segment_type`、`speaker_id`、`scene`、`emotion`、`intensity`
- Provider非依存の `voice_intent`
- 原文位置等を参照する `source_ref`

`UtterancePlan` は `schema_version`、`plan_id`、`source_fingerprint`、
`config_fingerprint`、`created_at`、`utterances`、`warnings` を保持する。PlanはProvider固有
voice ID、音声ファイルパス、durationなどの実行成果物を持たない。TTSの文字数制限による分割が
必要な場合も、分割されたUtteranceは元の話者と `voice_intent` を継承する。

識別子の責務を次のように固定する。

| ID | 意味 | 安定性 |
|---|---|---|
| `work_id` | 正規化したsource identityで表す作品 | 同一作品で安定 |
| `plan_id` | source/config fingerprintから作る解析計画 | 同じ入力・解析設定で安定 |
| `run_id` | 1回のProvider/出力実行 | 実行ごとに一意 |
| `job_id` | CLI/APIが追跡するジョブ | 受付ごとに一意 |

推奨配置は `output/works/{work_id}/plans/{plan_id}.json` と
`output/works/{work_id}/runs/{run_id}/` である。run側の artifact manifest にUtteranceごとの
状態、Provider、checksum、durationを記録する。旧 `output/{work_id}` と旧manifestは読み取り
互換を維持し、移行時に新plan/runへ変換する。

## 理由

- Reader、Batch、SDK、Studioが同一の話者境界と合成ポリシーを共有できる。
- Planを再利用したままProviderや出力形式だけを変えられる。
- `run_id` を分けることで同一URLの同時実行による成果物上書きを防げる。
- `job_id` を永続JobRepositoryの外部キーにでき、再起動後の追跡を可能にする。

## 影響と移行

DOM-001でモデルとvalidation、DOM-002/003で既存NLP結果・manifestとの変換とfingerprintを
実装する。SYN-* はPlanを入力として合成し、RUN-* はrunディレクトリとartifact manifestを
導入する。旧manifestを即時書き換えず、読み取りadapterとcopy-on-writeを使ってロールバック
可能にする。

## 却下した選択肢

- **文字数チャンクを共通モデルにする**: 話者・voice intentの境界が失われ、入口間の意味が一致しない。
- **音声パスをPlanへ保存する**: Provider変更・再合成・並行runの分離を妨げる。
- **URLだけをjob identityにする**: 同一作品の異なる設定・Provider・実行を区別できない。
