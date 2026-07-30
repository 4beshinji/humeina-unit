# Legacy manifest / artifact 移行手順

この手順は、RUN-001〜003 で導入した canonical plan/run/artifact layout へ、旧
output/{work_id}/manifest.json と連番 WAV を安全に移行するためのものです。現行 CLI に自動
migration command はありません。旧データを直接上書きせず、read-only import と copy-on-write
を使います。

## 対象と canonical layout

旧:

```text
output/{work_id}/
├── manifest.json
├── 0000.wav ...
├── chapter_001.wav
└── full.wav
```

新:

```text
output/works/{work_id}/
├── plans/{plan_id}.json
└── runs/{run_id}/
    ├── manifest.json
    ├── artifact-manifest.json
    └── verified audio/video
```

BatchPaths/ManifestSchemaAdapter は旧 manifest を読み取れますが、旧ファイルを書き換えません。
旧 ID（MD5 12 文字）と新 work_id（normalized source fingerprint）は同一とは限りません。

## 0. 実行を止め、対象を確定する

1. Reader/Batch/API の実行を停止し、対象 work に対する書き込みを止める。
2. output/{work_id}/manifest.json の work_id、source_url、status、audio_file を確認する。
3. data/jobs.sqlite3 に pending/running job があれば完了または失敗確定させる。
4. Provider の接続先、output root、実行日時を migration log（secret を含めない）へ記録する。

未完了 job を移行先で再開しないでください。現在の run を特定できない場合は backup から
復元して新規 run として再実行します。

## 1. Backup（必須）

output と Job DB を同一時点で保存します。保存先は repository/output の内側に置かず、
アクセス制御された別ディスクを指定してください。

```bash
BACKUP_DIR=/secure/backup/humeina-unit/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$BACKUP_DIR"
cp -a output/{work_id} "$BACKUP_DIR/legacy-work"
cp -a data/jobs.sqlite3 "$BACKUP_DIR/jobs.sqlite3"
sha256sum "$BACKUP_DIR/legacy-work/manifest.json" "$BACKUP_DIR/jobs.sqlite3" \
  > "$BACKUP_DIR/SHA256SUMS"
```

実際の {work_id} は対象名へ置換します。secret、本文全文、認証 URL を migration log や
backup のファイル名へ追加しないでください。backup の checksum を検証してから次へ進みます。

## 2. Read-only import と plan 保存

legacy manifest をメモリへ読み込み、canonical plan だけを新規作成します。次のコードは旧
manifest と音声を変更しません。

```python
from pathlib import Path

from yomiage.batch.manifest import BatchManifest

base = Path("output")
work_id = "replace-with-work-id"
legacy = BatchManifest.load_legacy(base, work_id)
print(legacy.source_url, legacy.completion_status, legacy.progress_str())
plan_path = legacy.save_plan(base)
print("new plan:", plan_path)
```

save_plan() が失敗した場合は backup の manifest を使って原因を調査します。未知の future
schema は安全側へ倒してエラーになります。legacy manifest.json の mtime と checksum が
変わっていないことを確認してください。

## 3. 新しい run へ copy-on-write

Provider/format を変更する場合は旧 run と同じ directory を再利用せず、新しい run_id を割り
当てます。通常は canonical plan を入力に yomiage batch analyze URL を実行して新 run を
作成し、同じ source/config fingerprint が得られることを確認してから synthesize します。

```bash
yomiage batch analyze "https://example.com/the-same-source" --mode voicevox --output output
yomiage batch synthesize <new-work-id> --mode voicevox --output output
```

既存 plan をアプリケーションから再利用する場合は BatchEngine/ArtifactStore の composition
root を使い、run_id を省略して旧 run を上書きしないでください。run の manifest と
artifact-manifest は atomic write で作成し、旧連番 WAV を新 run へ hard link せず、検証後に
copy/publish します。手作業で manifest の ID や status だけを書き換える方法はサポートしません。

## 4. 検証

切り替え前に次を確認します。

- plan の source_fingerprint/config_fingerprint が期待した入力と一致する。
- 新 run の全 utterance が synthesized/skipped のいずれかである。
- artifact-manifest の final artifact が verified=true、checksum 一致、run directory 内にある。
- BatchManifest.refresh_completion_status(output) が complete または
  complete_with_warnings を返す（partial/failed のまま公開しない）。
- chapter/full WAV を ffprobe/wave reader で開け、任意動画の音声長が期待値と合う。
- API job の work_id/plan_id/run_id とファイル layout が一致する。

```bash
yomiage batch status <new-work-id> --output output
python - <<'PY'
from pathlib import Path
from yomiage.batch.manifest import BatchManifest

base = Path("output")
manifest = BatchManifest.load(base, "replace-with-new-work-id")
print(manifest.refresh_completion_status(base))
print(manifest.output_dir(base))
PY
```

partial で残る場合は allow_partial を明示しない限り concat しません。allow_partial を使用
した成果物は filename、manifest、API/SSE status に partial を残し、動画生成へ渡さないでください。

## 5. 切り替えと rollback

検証済み new run を参照する運用へ切り替えます。旧 directory は削除・rename せず、保持期間と
backup checksum を記録します。

問題が見つかった場合の rollback:

1. 新 run を参照する job/alias を停止する。
2. backup の SHA256SUMS を検証する。
3. backup/legacy-work を元の output/{work_id}/ へ copy（既存を上書きする前に現状を別 backup）。
4. 旧 manifest.json を BatchManifest.load_legacy() で読めること、status と WAV checksum が
   移行前と一致することを確認する。
5. API/CLI を旧 output root へ戻し、new run は quarantine directory に保全する。

rollback は旧データを復元するだけで、新 plan/run を削除しません。new run を再び公開する
場合は原因を修正して新しい run_id でやり直してください。

## 制約と残存リスク

- 自動 migration CLI、複数プロセス lock、外部 object storage へのコピーはありません。
- legacy manifest に欠損/破損 WAV がある場合、import はできても completion は partial/failed になります。
- backup の取得時点と SQLite の transaction 時点がずれる可能性があります。停止時間を確保し、
  checksum と job 状態を同時に記録してください。
