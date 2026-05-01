# virtiofs セットアップガイド

VoiSona Talk（Windows VM）からホスト（Linux）の `output/` ディレクトリへ直接WAVファイルを書き出すための共有ファイルシステム設定。

## 構成概要

```
┌─────────────────────────┐     virtiofs      ┌──────────────────────────┐
│  Linux Host             │◄═════════════════►│  Windows 11 VM           │
│                         │                    │                          │
│  output/                │  ←──  Z:\          │  VoiSona Talk API        │
│   ├── {work_id}/        │       Z:\{work_id} │   destination: "file"    │
│   │   ├── 0001.wav      │                    │   output_file_path:      │
│   │   ├── 0002.wav      │                    │     "Z:\{work_id}\…"     │
│   │   └── manifest.json │                    │                          │
│   └── …                 │                    │                          │
└─────────────────────────┘                    └──────────────────────────┘
```

## 前提条件

- QEMU/KVM + libvirt
- ホストに `virtiofsd` がインストール済み（`/usr/lib/qemu/virtiofsd`）
- virtio-win.iso にviofs ドライバが含まれていること

## ホスト側セットアップ

### 1. 出力ディレクトリ作成

```bash
mkdir -p /home/sin/code/claude/humeina-unit/output
```

### 2. libvirt VM XML 編集

```bash
virsh shutdown win11-voisona
virsh edit win11-voisona
```

以下の2箇所を追加:

#### memoryBacking（`<vcpu>` の近くに追加）

```xml
<memoryBacking>
  <source type='memfd'/>
  <access mode='shared'/>
</memoryBacking>
```

#### filesystem（`<devices>` セクション内に追加）

```xml
<filesystem type='mount' accessmode='passthrough'>
  <driver type='virtiofs'/>
  <source dir='/home/sin/code/claude/humeina-unit/output'/>
  <target dir='voisona_output'/>
</filesystem>
```

### 3. VM起動

```bash
virsh start win11-voisona
```

libvirt が `virtiofsd` を自動起動する。AppArmor は `relabel='yes'` により自動許可。

## Windows VM 側セットアップ

### 1. WinFsp インストール

https://github.com/winfsp/winfsp/releases/latest から `.msi` をダウンロードしてインストール。

virtiofs の Windows 実装は WinFsp に依存しているため必須。

### 2. virtio-win-guest-tools インストール

virtio-win.iso（VM内でCDドライブとしてマウント済み）を開き、`virtio-win-guest-tools-xxx.exe` を実行。viofs ドライバを含む全ドライバが一括インストールされる。

### 3. VirtIO FS サービス起動

管理者 PowerShell で:

```powershell
Start-Service VirtioFsSvc
```

確認:

```powershell
Get-Service VirtioFsSvc
```

`Z:` ドライブとしてマウントされる。

### 4. 自動起動設定

```powershell
Set-Service VirtioFsSvc -StartupType Automatic
```

## 動作確認

### ホスト → VM

```bash
echo "hello" > output/test.txt
# VM側で Z:\test.txt が見えること
```

### VoiSona API → ホスト

```bash
curl -X POST \
  -u "${VOISONA_USERNAME}:${VOISONA_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '{
    "language": "ja_JP",
    "text": "テスト",
    "voice_name": "nurse-robot-type-t_ja_JP",
    "destination": "file",
    "output_file_path": "Z:\\test.wav",
    "force_enqueue": true
  }' \
  http://192.168.1.173:32766/api/talk/v1/speech-syntheses
```

数秒後に `output/test.wav` がホスト側に出現すれば成功。

## バッチパイプラインでの使用

`config/default.yaml` の `batch.voisona_vm_mount` が VM 内のマウントポイントに対応:

```yaml
batch:
  voisona_vm_mount: "Z:"
```

バッチ合成時、各文の WAV は以下のパスで VoiSona API に渡される:

```
Z:\{work_id}\0001.wav  →  ホスト側 output/{work_id}/0001.wav
```

## トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| VirtioFsSvc が起動しない | WinFsp 未インストール | WinFsp をインストール |
| Z: ドライブが見えない | viofs ドライバ未インストール | virtio-win-guest-tools を実行 |
| VM起動時エラー | memoryBacking 未設定 | XML に `<memoryBacking>` を追加 |
| WAV が出力されない | パス区切り文字 | `\\` (バックスラッシュ) を使用 |
| Permission denied | AppArmor | `virsh edit` で再定義するか、AppArmor プロファイルに output/ を追加 |
