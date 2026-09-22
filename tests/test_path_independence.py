"""設定・共有領域・最終出力先の配置を独立に変更できる契約。"""

from __future__ import annotations

import asyncio
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath

import pytest

from yomiage.adapters.tts import VoisonaFileProviderAdapter
from yomiage.application.synthesis import SynthesisRequest, SynthesisService
from yomiage.config import load_config, load_settings
from yomiage.tts.base import AudioResult
from yomiage.tts.shared_directory import VoisonaSharedDirectory, windows_mount

from .contracts.fakes import wav_bytes


def test_defaults_are_independent_of_checkout_and_cwd(tmp_path: Path, monkeypatch):
    first = load_config()
    elsewhere = tmp_path / "different checkout"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    second = load_config()
    assert first == second
    assert Path(first["batch"]["output_dir"]).is_absolute()
    assert first["batch"]["output_dir"] != first["voisona"]["shared_dir"]
    assert first["batch"]["voice_profile_dir"] == str(
        Path(first["storage"]["user_config_dir"]) / "voice_profiles"
    )


def test_each_config_source_anchors_its_own_paths(tmp_path: Path, monkeypatch):
    standard = tmp_path / "standard"
    standard.mkdir()
    (standard / "default.yaml").write_text(
        "tts: {primary_provider: voicevox}\nvideo: {assets_dir: pictures}\n",
    )
    user = tmp_path / "user"
    user.mkdir()
    (user / "settings.yaml").write_text(
        "batch: {output_dir: results, voice_profile_dir: profiles}\n"
        "voisona: {shared_dir: share, vm_mount: 'Y:\\audio'}\n"
        "ex_voice: {wav_dir: clips}\nvoicepeak: {path: bin/voicepeak}\n",
    )
    monkeypatch.chdir(tmp_path)
    settings = load_settings(standard, user_config_dir=user, env={})
    raw = settings.to_legacy_dict()
    assert settings.storage.assets_dir == standard / "pictures"
    assert settings.batch.output_dir == user / "results"
    assert settings.batch.voice_profile_dir == user / "profiles"
    assert Path(raw["voisona"]["shared_dir"]) == user / "share"
    assert Path(raw["ex_voice"]["wav_dir"]) == user / "clips"
    assert raw["voisona"]["vm_mount"] == "Y:\\audio"
    assert settings.providers.voicepeak.path == user / "bin/voicepeak"


def test_environment_file_is_explicit_and_process_values_win(tmp_path: Path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / ".env").write_text("VOISONA_URL=http://configured:32766\n")
    monkeypatch.setenv("YOMIAGE_USER_CONFIG_DIR", str(user))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("VOISONA_URL=http://unrelated:32766\n")
    monkeypatch.delenv("VOISONA_URL", raising=False)
    assert load_config()["voisona"]["url"] == "http://configured:32766"
    monkeypatch.setenv("VOISONA_URL", "http://environment:32766")
    assert load_config()["voisona"]["url"] == "http://environment:32766"


def test_provider_path_overrides_reach_raw_and_typed_consumers(tmp_path):
    user = tmp_path / "settings.yaml"
    user.write_text(
        "providers:\n  voisona: {shared_dir: original, vm_mount: 'Y:'}\n"
    )
    env = {"VOISONA_SHARED_DIR": "env-share", "VOISONA_VM_MOUNT": "X:"}
    raw = load_config(user_config_dir=user, env=env)
    settings = load_settings(user_config_dir=user, env=env, validate_active=False)
    assert Path(raw["voisona"]["shared_dir"]) == tmp_path / "env-share"
    assert Path(settings.providers.voisona.shared_dir) == tmp_path / "env-share"
    assert raw["voisona"]["vm_mount"] == settings.providers.voisona.vm_mount == "X:"
    assert settings.storage.user_config_dir == tmp_path
    explicit = load_config(
        user_config_dir=user, env=env, overrides={"voisona": {"shared_dir": "explicit"}},
    )
    assert Path(explicit["voisona"]["shared_dir"]) == tmp_path / "explicit"


async def test_readiness_detects_missing_host_share(tmp_path):
    from yomiage.application.health import HealthService

    service = HealthService(config={
        "voisona": {"shared_dir": str(tmp_path / "share"), "vm_mount": "Z:"},
    })
    assert not (await service._vm_mount_check()).healthy
    (tmp_path / "share").mkdir()
    assert (await service._vm_mount_check()).healthy


@pytest.mark.parametrize("mount", ["Z:", "Y:\\audio", "\\\\server\\share"])
async def test_shared_transfer_ignores_cwd_output_layout_and_concurrency(
    tmp_path: Path, monkeypatch, mount: str,
):
    shared = tmp_path / "VM shared audio"
    shared.mkdir()
    mapping = VoisonaSharedDirectory.from_config({"shared_dir": shared, "vm_mount": mount})
    destinations = []

    class Provider:
        name = "voisona"

        async def synthesize_to_file(self, text, destination, **kwargs):
            guest = PureWindowsPath(destination)
            assert guest.parent == mapping.guest_root
            destinations.append(destination)
            await asyncio.sleep(0)
            (shared / guest.name).write_bytes(wav_bytes())
            return AudioResult(b"")

    adapter = VoisonaFileProviderAdapter(Provider(), shared_directory=mapping)
    service = SynthesisService(adapter, concurrency=2)
    moved = tmp_path / "moved checkout"
    moved.mkdir()
    monkeypatch.chdir(moved)
    paths = [tmp_path / "exports" / str(i) / "speech.wav" for i in range(2)]
    try:
        results = await asyncio.gather(*(
            service.synthesize(SynthesisRequest("本文", destination=str(path))) for path in paths
        ))
        assert all(path.read_bytes() == result.audio_data for path, result in zip(paths, results))
        assert len(set(destinations)) == 2
        assert list(shared.iterdir()) == []
        assert list(moved.iterdir()) == []
    finally:
        await service.close()


@pytest.mark.parametrize("mount", ["", "relative", "Z:relative", "Z:\\..\\outside"])
def test_guest_mount_must_be_absolute(mount):
    with pytest.raises(ValueError, match="absolute Windows"):
        windows_mount(mount)


def test_missing_share_fails_without_creating_an_unshared_directory(tmp_path):
    root = tmp_path / "missing"
    mapping = VoisonaSharedDirectory.from_config({"shared_dir": root})
    with pytest.raises(FileNotFoundError, match="libvirt source dir"):
        with mapping.temporary_wav():
            pytest.fail("must fail before calling the provider")
    assert not root.exists()


def test_shared_temporary_file_is_removed_on_cancellation(tmp_path):
    mapping = VoisonaSharedDirectory.from_config({"shared_dir": tmp_path})
    with pytest.raises(asyncio.CancelledError):
        with mapping.temporary_wav() as (path, _):
            path.write_bytes(wav_bytes())
            raise asyncio.CancelledError
    assert not list(tmp_path.iterdir())


def test_vm_migration_changes_only_named_share_and_is_idempotent(tmp_path):
    script = Path(__file__).resolve().parents[1] / "infra" / "voisona-vm.py"
    spec = importlib.util.spec_from_file_location("voisona_vm", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    xml = """<domain><name>vm</name><devices>
      <disk><source file='/var/lib/libvirt/images/system.qcow2'/></disk>
      <filesystem><driver type='virtiofs'/><source dir='/removed/checkout/output'/>
        <target dir='voisona_output'/></filesystem>
      <filesystem><source dir='/other'/><target dir='other'/></filesystem>
    </devices></domain>"""
    result = module.update_shared_directory(xml, tmp_path, "voisona_output")
    assert module.update_shared_directory(result, tmp_path, "voisona_output") == result
    before, after = ET.fromstring(xml), ET.fromstring(result)
    before.find("./devices/filesystem/source").set("dir", str(tmp_path))
    assert ET.tostring(before) == ET.tostring(after)
    with pytest.raises(ValueError, match="exactly one"):
        module.update_shared_directory(xml, tmp_path, "unknown")
