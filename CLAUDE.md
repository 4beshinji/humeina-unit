# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概要

VoiSona Talk / VOICEVOX / VOICEPEAK を切り替えて日本語テキスト（青空文庫・なろう・カクヨム・一般 Web）を
読み上げる Python 3.12+ の CLI / FastAPI / Python SDK。NLP 分析（分類・話者・シーン・感情）→ 発話計画 →
音声合成 → 成果物管理を単一プロセスのモジュラーモノリスとして提供する。Firefox 拡張
（`browser-extension/firefox/`）からページ本文を読み上げる経路もある。

HEMS/MQTT 連携、SPICE 再生、常駐クラウドサービスは現行アーキテクチャに含まれない。

## コマンド

依存管理は `uv`。`.venv/bin/pytest` の shebang が旧パスを指すことがあるため、`uv run pytest` ではなく
`uv run python -m pytest` を使う。

```bash
uv sync --extra dev

# テスト（CI と同じ範囲。e2e は外部サービス、packaging は wheel が必要）
uv run python -m pytest -q -m "not e2e and not packaging"
uv run python -m pytest tests/test_core_plan.py::test_name -q   # 単一テスト

# Lint（CI は src/ と tests/ の両方）
uv run python -m ruff check src/ tests/

# 実行
uv run yomiage --help                       # コマンド一覧の正は実装の help
uv run yomiage read "<URL>" -p voicevox
uv run yomiage batch run "<URL>" --mode voicevox
uv run yomiage serve                        # 既定 127.0.0.1:8030
```

- E2E（`tests/e2e/`, marker `e2e`）は実サービスが無ければ skip。llama.cpp 系は
  `RUN_LLAMA_CPP_E2E=1` / `RUN_LLAMA_CPP_QUALITY_E2E=1`、有料 LLM（marker `paid_llm`）は
  `RUN_PAID_LLM_TESTS=1` と `OPENCODE_GO_API_KEY` が必要。有料テストは明示の許可なしに実行しない。
- VOICEVOX は `infra/` の Docker Compose で起動する。

## アーキテクチャ

詳細は `docs/architecture.md` と `docs/adr/`。要点:

- **依存方向（ADR-0001）**: `interfaces`（`interfaces/cli/`, `server.py`）→ `application`
  （planning / synthesis / jobs / health）→ `core`（`UtterancePlan`, identity, job state）↔ `ports`
  （TTS / source / repository / artifact 契約）← `adapters`。`core` / `application` / `ports` は
  fastapi・typer・具象 TTS Provider（`yomiage.tts.voisona` 等）を import してはならない。
  `tests/test_architecture.py` が AST で検査し、既存違反は所有タスク ID 付きの allowlist でのみ許容する。
- **composition root**: `bootstrap.py`。`nlp`, `reader`, `batch`, `tts`, `sources`, `studio`, `video`,
  `news`, `slack` は legacy feature package で、ports/application の互換実装として配線される。
  `cli.py` は `interfaces/cli/` への互換 facade。
- **発話境界の共有（ADR-0002）**: Reader / Batch / API / SDK はすべて `core.plan.UtterancePlan` に変換し、
  `application.synthesis.SynthesisService` を通して合成する。新しい合成経路を個別に作らない。
- **LLM**: `llm/` が provider 非依存のサービス（llama.cpp が標準、OpenCode Go / Gemini は opt-in、
  Ollama は rollback 用互換のみ）。`nlp/scene_analyzer.py` などはこれを経由する。
- **Batch 成果物**: `output/works/{work_id}/plans/{plan_id}.json` と
  `runs/{run_id}/{manifest.json,artifact-manifest.json,*.wav}`。書き込みは `utils/atomic_io.py` で atomic に
  publish/verify し、欠損があれば concat を拒否（`allow_partial` 明示時のみ partial、partial では動画を作らない）。
  旧 `output/{work_id}/manifest.json` は `batch/manifest_adapter.py` で read-only 互換。
- **ジョブ状態**: SQLite repository（ADR-0003, `adapters/persistence/sqlite.py`）で compare-and-set 遷移。

## 設定とパス

- 標準 YAML は wheel 同梱の `src/yomiage/resources/config/`（`resource_loader.py`）。リポジトリ直下の
  `config/` ではない点に注意。新しい標準 YAML を追加したら `pyproject.toml` の `force-include` も更新する。
- ユーザー編集設定・プロファイル・`.env` は `YOMIAGE_USER_CONFIG_DIR`（既定 `~/.config/humeina-unit`）。
  データ・出力は `YOMIAGE_DATA_DIR` / `YOMIAGE_OUTPUT_DIR`（`runtime_paths.py`, `settings.py`）。
- `tests/conftest.py` が非 e2e テストで XDG / `YOMIAGE_*` を tmp に隔離する。テストで実ユーザー領域に
  依存しないこと。
- API の非 loopback bind は `YOMIAGE_API_TOKEN` 必須（`/health`, `/health/live` 以外は Bearer 認証）。

## ドキュメントと作業管理

- `tests/test_docs.py` が README と `docs/**/*.md` のローカルリンク、および `docs/cli-reference.md` に
  載せた CLI コマンドの `--help` 描画を検査する。CLI やドキュメントを変更したら合わせて更新する。
- 進行中の改善計画と Definition of Done は `docs/improvement/README.md`。完了済みタスクの記録は
  git 履歴に置き、新しい台帳は作らない。公開契約を変える場合はコミットに明記する（大きな境界変更は ADR）。
- Definition of Done には失敗系テストを最低 1 つ追加すること、secret・本文・認証付き URL をログや
  fixture に入れないこと、テスト出力などの生データを docs にコミットしないことが含まれる。
- コメント・docstring は日本語が慣例。ログは `loguru`、行長 100。
