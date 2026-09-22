# virtiofs セットアップガイド

VoiSona Talk の Windows パスと Linux の最終出力先を分離する。
VM が共有するのは音声受け渡し専用の永続ディレクトリであり、チェックアウトの
`output/` は共有しない。

```text
Windows API: Z:\yomiage-<uuid>.wav
                 │ virtiofs
Linux: <voisona.shared_dir>/yomiage-<uuid>.wav
                 │ 読み取り・検証・一時ファイル削除
       SDK の bytes / 任意の --output / canonical run の artifact
```

## ホスト側

共有元の既定値は `${XDG_DATA_HOME:-$HOME/.local/share}/humeina-unit/voisona`。
`voisona.shared_dir` / `VOISONA_SHARED_DIR` で変更できる。ソースの移動や別の CWD からの
実行では変わらない。共有元そのものを変更する場合だけ、libvirt とアプリの両方を変更する。
チェックアウトへのシンボリックリンクを共有元として使わない。

```bash
shared_dir="${XDG_DATA_HOME:-$HOME/.local/share}/humeina-unit/voisona"
mkdir -p "$shared_dir"
```

libvirt の `<devices>` 内に次を設定する。`source dir` には上で作成したディレクトリの
**絶対パス**を記入する（XML は `$HOME` や `~` を展開しない）。`target dir` は Windows の
ドライブ文字ではなく、ゲストが共有を識別する mount tag。

```xml
<filesystem type='mount' accessmode='passthrough'>
  <driver type='virtiofs'/>
  <source dir='/absolute/persistent/shared-directory'/>
  <target dir='voisona_output'/>
</filesystem>
```

`<domain>` の直下には共有メモリ設定も必要。

```xml
<memoryBacking>
  <source type='memfd'/>
  <access mode='shared'/>
</memoryBacking>
```

設定の意味とホストの対応条件は [libvirt の virtiofs ガイド](https://libvirt.org/kbase/virtiofs.html)
を参照。ホストの実行ユーザー・DAC・AppArmor/SELinux によっては共有元と親ディレクトリの
アクセス設定が必要。`relabel='yes'` だけで常にアクセスできるとは限らない。

### 旧チェックアウトを参照する既存 VM の修復

VM を停止してから、リポジトリの `infra/voisona-vm.py` を実行する。
このスクリプトは mount tag が `voisona_output` の共有元だけを変更する。ディスク、NIC、
他の共有は保持する。通常実行は差分表示、`--apply` で適用する。

```bash
python3 infra/voisona-vm.py --shared-dir "$shared_dir"
python3 infra/voisona-vm.py --shared-dir "$shared_dir" \
  --apply --backup /tmp/win11-voisona-before-share-change.xml
virsh -c qemu:///system start win11-voisona
```

`--domain` / `--uri` / `--tag` は変更可能。既存のバックアップは上書きせず、稼働中 VM への
適用は拒否する。バックアップから戻す場合は、VM を停止してから
`virsh -c qemu:///system define /tmp/win11-voisona-before-share-change.xml` を実行する。

## Windows 側

1. [WinFsp](https://github.com/winfsp/winfsp/releases) と virtio-win の viofs ドライバを導入する。
2. 管理者 PowerShell でサービスを起動し、自動起動にする。

```powershell
Start-Service VirtioFsSvc
Set-Service VirtioFsSvc -StartupType Automatic
Get-Service VirtioFsSvc
```

実際に割り当てられたドライブを確認し、ユーザー設定に記入する。

```yaml
voisona:
  vm_mount: 'Z:'
```

`Z:` は `Z:\` として扱う。`Y:\audio` や UNC の `\\server\share` も指定可能。
旧 `batch.voisona_vm_mount` は互換入力として受け付ける。

## 動作確認

共有領域が Windows から見え、アプリ実行ユーザーで読み書きできることを確認する。
VoiSona API の認証情報はユーザー設定ディレクトリの `.env` または環境変数で指定する。

```python
import asyncio
from pathlib import Path
from yomiage.api.bridge import TTSBridge
from yomiage.bootstrap import create_tts_provider
from yomiage.config import load_config

async def main():
    bridge = TTSBridge(create_tts_provider(load_config(), "voisona"))
    try:
        await bridge.synthesize_to_file("共有領域の確認です。", Path("/tmp/voisona-test.wav"))
    finally:
        await bridge.close()

asyncio.run(main())
```

SDK、Batch、Reader のファイル合成、Studio、VoiSona tuner は同じ受け渡し方式を使う。
`Z:\` 以下に canonical run の構造を再現する必要はない。低水準の
`VoisonaProvider.synthesize_to_file()` だけは **Windows の絶対パス**を受け取る。
Linux の保存先は Bridge / SynthesisService に渡す。

| 症状 | 確認する設定 |
| --- | --- |
| VM が起動しない | libvirt `source dir` の存在、共有メモリ、ホスト側アクセス権 |
| 共有ドライブが見えない | WinFsp、viofs、VirtioFsSvc、mount tag |
| ファイル合成で即座に共有元エラー | `voisona.shared_dir` と libvirt `source dir` の一致 |
| API は成功するが WAV が取得できない | Windows のドライブと `voisona.vm_mount` の一致、ホストからの読み取り権限 |
| 出力先を変えた後だけ失敗する | Linux の最終出力先に書き込み権限があるか |

共有ディレクトリは事前に用意する。アプリは存在しない共有元を自動作成しない。
