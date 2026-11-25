# 軽量化リファクタ計画（optional extra 分離）

2026-11-25 策定・P0〜P5 実施済み（各フェーズ 1 コミット）。読み上げ本体（取得 → 発話計画 → 合成 → 成果物）を中心に据え、周辺機能
（news / slack / video / studio / exvoice）とプロセス文書の重さが本体へ波及している状態を解消する。

## 1. 前提（策定時点の実コード）

- news / slack / studio の生成は `interfaces/cli/composition.py` 内で既に遅延 import 済み。
  強い結合は次の 4 点に限られる。
  - `feedparser` が必須依存（`pyproject.toml`）。利用は `news/fetcher.py` のみ。
  - health が slack / slack_sdk を常時検査（`application/health.py`, `server.py` の readiness 名一覧）。
  - API に `POST /api/yomiage/news` がある（`server.py`）。ブラウザ拡張・テストからの利用はない。
  - reader が exvoice を module 先頭で import（`reader/engine.py`）。
- video は `batch/engine.py` 内で遅延 import 済み。Pillow は既に `video` extra。
- `mqtt` extra は src から参照されていない。
- `SegmentType` が `core/models.py`（`str, Enum`）と `nlp/classifier.py`（`Enum`）に同値で二重定義。
- `SynthesisResult` が公開 SDK 用（`api/models.py`）と application 内部用
  （`application/synthesis.py`）で同名。`yomiage.api` と `yomiage.application` がそれぞれ export している。
- `docs/improvement/evidence/` に pytest 生出力などをコミットしている。ただし
  `scripts/evaluate_speech_quality.py` は `before.json` を入力として読む。

## 2. 方針

- extra は依存ライブラリの分離に限る。CLI コマンドは常に登録し、extra 不足時は
  `pip install 'humeina-unit[<extra>]'` を案内して非 0 で終了する。条件付き登録はしない
  （`tests/test_docs.py` の `--help` 描画検査と衝突するため）。
- health / readiness と REST API は読み上げ本体だけを対象にする。周辺機能は各 CLI コマンド内で
  自己診断する。
- exvoice は外部依存がないため extra にせず、`ex_voice` 設定で切り替える機能のまま遅延 import する。
- 新しい台帳は作らない。本計画の各フェーズは 1 コミットとし、記録は git log とする。
  全フェーズ完了後、本書は削除してよい。
- 非対象: LLM transport の整理、`cli.py` ↔ `interfaces/cli/common.py` の互換 facade 撤去、
  `Settings` と legacy dict の統一。

## 3. フェーズ

各フェーズの完了条件（共通）:

- `uv run python -m pytest -q -m "not e2e and not packaging"` と
  `uv run python -m ruff check src/ tests/` が通る。
- 失敗系テストを最低 1 つ追加する（文書のみのフェーズを除く）。

### P0 生ログと文書の整理

- `before.json` を `tests/fixtures/speech_quality/` へ移し、評価スクリプトの参照を変更する。
- `evidence/` の残りを削除し、`speech-quality-refactoring-plan.md` §9 のリンクを外す
  （数値表は本文に残っている）。
- `docs/improvement/` を `README.md`（現状・残課題・Definition of Done）、本書、
  `speech-quality-refactoring-plan.md` に絞る。work-ledger / goal-prompt / findings / guide /
  progress / architecture-plan は削除する（git 履歴で参照可能）。
- 受入: `tests/test_docs.py` が通る。`git grep -nE "improvement/(work-ledger|evidence)"` が
  `CLAUDE.md` / `AGENTS.md` 以外で 0 件。

### P1 モデル重複の解消

- `nlp/classifier.py` の `SegmentType` を削除し `core.models.SegmentType` を使う。
- `application.synthesis.SynthesisResult` を `UtteranceSynthesisResult` に改名する。
  公開 package（`yomiage`, `api`, `application`, `core`）の `__all__` に同名・別実体の型が
  無いことをテストで固定する。
- `tests/test_architecture.py` の allowlist で、完了済みタスク ID を理由に残る項目を再確認し、
  解消するか恒久例外として理由を書き直す。実在しない import を指す規則は失敗させる。
- テスト: 両 `SegmentType` が同一であること、トップレベル export が公開 SDK 型であること、
  トップレベル `__all__` に同名異物がないこと。

### P2 依存の整理と extra 定義

- `pyproject.toml`: `mqtt` を削除、`feedparser` を `news` extra へ移動、`all = news + slack + video`、
  `dev` は `all` を含む。
- `yomiage/features.py` に `require_extra(extra, module)` を置く。不足時は案内付きの
  `MissingExtraError` を送出し、CLI では非 0 終了に変換する。
- `news` / `slack` コマンドの入口で呼ぶ。video は Pillow 不在時にタイトルカードだけを skip する既存の
  劣化動作があり（`video/frame_builder.py`）、本体は ffmpeg に依存するため入口では要求しない。
- テスト（失敗系）: `find_spec` を `None` にして `yomiage news daily` が非 0 で案内を出す。

### P3 health と API から周辺機能を外す（BREAKING）

- `HealthChecker` / `HealthDependencies` / readiness 名一覧から slack・slack_sdk を削除する。
- `POST /api/yomiage/news` を削除し、`docs/api-reference.md` を更新する。
- テスト: `slack_sdk` がなくても readiness が slack を理由に劣化しない。

### P4 遅延 import

- `reader/engine.py` の exvoice import を `ex_voice=True` 時の遅延 import にする。
- `bootstrap.create_studio_providers` は studio を import しない汎用 helper のため移動しない
  （実測で本体 import 時に読み込まれる周辺 package は exvoice のみだった）。
- テスト: 別プロセスで `import yomiage.reader.engine` / `import yomiage.server` 後、
  `sys.modules` に `yomiage.{exvoice,news,slack,video,studio}` が無い。

### P5 extra 無し起動の保証

- CI に extra 無し `uv sync` で `yomiage --help`、`yomiage serve --help`、`import yomiage` を
  実行するジョブを追加する。
- README と `docs/installation.md` に extra 一覧を記載する。

## 4. 順序とリスク

- P0 と P1 は独立。P2 以降は `pyproject.toml` / `uv.lock` が衝突するため順に進める。
- 公開契約の変更は `yomiage.application.SynthesisResult` の改名（P1）と `/api/yomiage/news` 削除（P3）。
  利用者は単独のため ADR は作らず、コミットに `BREAKING CHANGE` を明記する。
- P2 は `uv.lock` が変わるため packaging テスト（`-m packaging`）も実行する。
