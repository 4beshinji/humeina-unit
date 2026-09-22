# 配置依存の変更と移行

## 原因と修正範囲

VoiSona VM の libvirt `source dir` がチェックアウト内の `output/` を指していたため、
チェックアウト移動後に共有元が存在せず起動できなくなっていた。共有元は VM が起動時に
必要とするホスト資源であり、アプリのソースや成果物ディレクトリから独立させる。

| 依存箇所 | 以前の前提 | 修正後 |
| --- | --- | --- |
| libvirt virtiofs | チェックアウトの絶対パス | 永続的な専用共有元。mount tag を指定して移行するスクリプトを追加 |
| VoiSona Batch / Reader | `run_dir` の末尾や `works` の位置から VM パスを推測 | `voisona.shared_dir` と `voisona.vm_mount` の対応で一時 WAV を取得 |
| VoiSona SDK / Studio | Linux の一時パスを Windows API に渡す場合がある | ファイル出力は共通の共有領域を経由。最終出力先は任意 |
| VoiSona tuner | 出力ディレクトリ名が共有直下にある | 共有領域で合成した後、tuner の出力先へ保存 |
| YAML の相対パス | 起動時 CWD、raw 設定と Settings で解決が異なる | 値を定義した設定ファイルのディレクトリで絶対化 |
| プロファイル | `config/voice*_profiles` を CWD から探索・保存 | ユーザー設定の同一ディレクトリで探索・保存 |
| `.env` | Python モジュールの配置から親ディレクトリを探索 | ユーザー設定内 `.env` または `YOMIAGE_ENV_FILE` |
| data / output / assets | 既定の相対パスが CWD に追従 | XDG データディレクトリ配下。明示設定・CLI 引数で変更可能 |
| Studio 出力 | CLI の既定 `output` が設定を上書き | `--output` 未指定時は設定を使用 |
| VOICEPEAK / EX ボイス | 特定ユーザーのホーム配下をハードコード | 設定・環境変数で明示。EX ボイス未設定は起動時にエラー |
| 長文 SDK の無音ファイル | CWD に固定名で作成 | OS の一時ディレクトリ内で呼び出しごとに分離 |
| Settings の標準設定メタデータ | `__file__` からリポジトリ階層を逆算 | インストールされたパッケージのリソース位置を使用 |

## 既定保存先

`XDG_CONFIG_HOME` の既定は `~/.config`、`XDG_DATA_HOME` の既定は `~/.local/share`。

| 用途 | ディレクトリ / 設定 |
| --- | --- |
| ユーザー YAML・`.env` | `$XDG_CONFIG_HOME/humeina-unit` |
| プロファイル | ユーザー設定内 `voice_profiles` / `voicevox_profiles` / `voicepeak_profiles` |
| ブックマーク・キャラクター・DB | `$XDG_DATA_HOME/humeina-unit` |
| 成果物 | データディレクトリ内 `output/` |
| アセット | データディレクトリ内 `assets/` |
| VoiSona 共有領域 | データディレクトリ内 `voisona/` |

既存の `VOISONA_VM_MOUNT` / `batch.voisona_vm_mount` は互換入力として利用可能。
新規の YAML では `voisona.vm_mount` と `voisona.shared_dir` に接続の対応をまとめる。
SDK の低水準 Provider へ辞書を直接渡す場合、共有元はホストの絶対パスを指定する。

## 旧環境の移行

VM の共有元は [virtiofs セットアップ](virtiofs-setup.md) に従って変更する。
この操作は既存の音声成果物の移動を必要としない。共有領域は空の専用ディレクトリでよい。
VM 定義のバックアップを保持し、変更後に VM 起動とファイル合成を確認する。

ユーザー設定ディレクトリには旧 `.env` と `config/voice*_profiles` をコピーする。
プロセス環境変数は `.env` より優先される。外部バイナリや音声素材がある場合は
`VOICEPEAK_PATH` / `EX_VOICE_WAV_DIR` をユーザー設定で明示する。

**既存の `data/` と `output/` は自動移動・削除しない。** 新しい既定値だけでは旧データを
探索しないため、継続利用する保存先を明示するか、停止中にバックアップしてから永続領域へ
移す。`data/` の中身はデータディレクトリ直下、`output/` の中身はその `output/` へ対応する。
`assets/` の画像・音声素材も新しい `assets_dir` へコピーするか、既存の保存先を明示する。

既存ディレクトリを移さず利用する設定例（パスは実際の保存先へ置き換える）:

```yaml
storage:
  data_dir: /absolute/path/to/existing-data
  output_dir: /absolute/path/to/existing-output
  assets_dir: /absolute/path/to/existing-assets
voisona:
  shared_dir: /absolute/path/to/persistent-voisona-share
  vm_mount: 'Z:'
```

共有元への参照はチェックアウト移動後も存在する場所に置く。旧パスを指すリンクを
新しいチェックアウトへ張るだけの修復では、次の移動で再発する。

## 明示的に残すパス契約

- CLI の入力ファイル・明示 `--output` は通常のシェルと同じく呼び出し時 CWD が基準。
- `Settings.from_dict(..., base_dir=...)` はファイル起点のない辞書入力の基準を明示できる。
- VM ディスク、外部バイナリ、GGUF、外部素材の設置場所は運用設定。ソース内には固定しない。
- パッケージの `__file__` 参照は同梱リソースの診断用。設定の読み込みは `importlib.resources`。
- `.venv` の shebang、editable install、バイトコードキャッシュはインストール時の生成物。
  チェックアウトを移動したら仮想環境を作り直す。パスをソースで推測して補修しない。

## 検証

```bash
uv run python -m pytest -q --ignore=tests/e2e
uv run python -m ruff check src/ tests/ infra/voisona-vm.py
```

`tests/test_path_independence.py` は異なる CWD、複数の設定元、環境変数の優先順位、共有領域外の
出力先、同時合成、ドライブ/UNC パス、一時ファイルの後始末、VM XML の限定変更を検証する。
外部サービスを使う検証では、実行 CWD と最終出力先をチェックアウト外にして WAV の完全性を
確認する。
