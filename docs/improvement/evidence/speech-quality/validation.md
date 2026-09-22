# 音声品質リファクタリングの検証

2026-09-14。既存の未コミット変更を含む作業開始時点を基準とする。

- 変更前: 外部サービス不要の773件成功・24件スキップ。復元漏れの計画書によるリンク失敗は、
  計画書を復元後に文書テスト32件を再実行して解消。
- 変更後: **817件成功・24件スキップ**（23.01秒）。Ruffは指摘0件、`git diff --check`も成功。
  [最終pytest出力](pytest.txt)を保存した。
- 24例の構造・読み・話者fixtureをoff/selective/fullで測定。指定読み・話者fixture違反はいずれも0。
- モックLLM要求数: off 0、selective 3、full 24。実モデルの品質・費用・GPU時間は未測定。
- VOICEVOX話者47で比較音声48本を生成。初回・cache再利用を別測定し、再利用は56要求すべてhit。
- VoiSonaは接続確認のみ。VOICEPEAKと設定済みローカルLLMは利用不能。人の試聴結果は未収集。

```bash
.venv/bin/python -m pytest -q --ignore=tests/e2e --disable-warnings --tb=short
.venv/bin/python -m ruff check src/ tests/ scripts/evaluate_speech_quality.py
.venv/bin/python scripts/evaluate_speech_quality.py --output docs/improvement/evidence/speech-quality
.venv/bin/python scripts/evaluate_speech_quality.py --audio --output output/speech-quality
```

`tests/e2e`は外部サービス・連携先を実際に利用するため一括実行対象から除いた。
通常テスト内のスキップは既存の外部依存・任意機能・packaging条件による。
今回の実音声生成は上記の専用スクリプトで検証した。localhostのモックHTTP待受を含む全テストと
実TTSへの接続はsandbox外で実行した。

- [変更前の再現](before.json)
- [変更後の構造・要求数](structure.json)
- [実音声の時間・cache計測](audio-measurements.json)
- [計画書と未測定事項](../../speech-quality-refactoring-plan.md#9-実装と検証の記録)
