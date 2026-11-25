# 改善作業

## 文書

- [slimming-plan.md](slimming-plan.md): 周辺機能の optional extra 分離と軽量化（実施済み。記録として残す）
- [speech-quality-refactoring-plan.md](speech-quality-refactoring-plan.md):
  読み補正・合成品質・LLM 計算コストの所見と SQ-001〜007 の記録

2026-07〜09 のアーキテクチャ改善（GOV/SEC/CFG/DOM/SYN/JOB/API/CLN/DOC 系タスク、
RT-001〜010）は完了済み。計画書・台帳・検証記録は git 履歴
（commit `a1f3202` 時点の `docs/improvement/`）を参照する。
設計の決定は [ADR](../adr/README.md)、現在の構造は [architecture.md](../architecture.md) が正。

## 残課題

- **外部 E2E とリリースチェックリスト（旧 OPS-001）**: VOICEVOX / VoiSona / VOICEPEAK / LLM を
  個別 marker で選択実行できるようにし、plan → synthesis → concat → 任意 video を代表入力で確認する。
  VOICEPEAK を公開 CI で実行しない。
- **SQ-006 の聴感評価と実 LLM 測定**: 人による誤読数・自然さの評価、実モデルの精度・tokens・GPU
  時間は未測定（speech-quality-refactoring-plan.md §9）。

## Definition of Done

- 変更の受入条件を満たす。
- 正常系に加え、失敗系テストを最低 1 つ追加する。
- `uv run python -m pytest -q -m "not e2e and not packaging"` と
  `uv run python -m ruff check src/ tests/` が通る。
- 公開動作・設定を変えたら対応ドキュメントを更新する。公開契約の変更はコミットに明記する。
- unrelated な整形・改名・移動を混在させない。
- secret、本文、認証付き URL をログ・fixture・成果物に入れない。
- テスト出力や計測の生データを docs にコミットしない。再現手順（コマンド）だけを残す。
