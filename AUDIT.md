# AUDIT — 2026-05-16

> Source: `/home/sin/code/claude/jisei-roku/codebase-patterns-and-gaps.md`

## 状況サマリ
- 直近30日 commit: 2 (低活動、機能完成寄り)
- CI: ❌ / Tests: ✅
- ADR: ❌ / CLAUDE.md: 46 行 (短め)
- **Python 3.12 + uv** (workspace 内で先進的な構成)

## 推定: voisona-yomiage の再構成
旧 portfolio に載っていた `voisona-yomiage` が消え、本プロジェクトが VoiSona + VOICEVOX + NLP話者識別という同等機能を持つ。**改名 + 再設計の可能性が高い**が、確証なし。改名なら git の `git log --all --oneline | head` で初期コミットメッセージから経緯が読み取れるかも。

## 強み
- **logging が loguru 単一** (53 ファイル全て) — 統一が取れている workspace 内の希少例
- **uv + Python 3.12** — 依存解決と環境構築が高速
- **テスト存在** (`tests/` あり)
- **CLAUDE.md が短く要点だけ** — 大型化していない

## プロジェクト固有の問題
1. **CI なし、tests ある** — 書いてあるが回っていない。`.github/workflows/` をシンプルに `pytest + ruff` で構成
2. **`os.getenv` は 4 hits のみ** — 設定ばらまきは少ないが pydantic-settings 0。**Settings クラスを今のうちに 1 個立てておくと将来コスト最小**
3. **`except Exception` 59 箇所** — 中規模。VoiSona / VOICEVOX / VOICEPEAK の外部プロセス起動エラーが含まれていそう → カスタム例外で原因分類
4. **VOICEPEAK は商用ライセンス** — CI で実行する場合のライセンス境界を ADR で記録

## ワークスペース横断
- 静的型付けなし
- 観測性ゼロ
- ADR ゼロ

## 推奨対応 (ROI 順)
1. ✅ CI 通電 (pytest + ruff)
2. ✅ pydantic-settings 雛形 (小規模なので Settings 1 クラスで足りる)
3. 外部 TTS プロセスのカスタム例外階層 (`VoiSonaTimeout`, `VoicepeakLicenseError` 等)
4. ADR でライセンス境界 (VOICEPEAK 商用利用条件)
5. mypy/pyright 設定 (Python 3.12 なので strict mode の恩恵大)

## 検証情報 (2026-05-16)
- loguru: 53 / stdlib: 0 / bare except: 59 / pydantic-settings: 0 / os.getenv: 4
- CLAUDE.md: 46 lines / ADR: 0
