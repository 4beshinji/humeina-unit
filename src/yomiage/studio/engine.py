"""Studio orchestrator — ties parsing, synthesis, and output together."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from loguru import logger

from ..application.synthesis import SynthesisService
from ..bootstrap import create_studio_providers, create_synthesis_service
from ..tts.cache import TTSCache
from .models import ScriptLine, SpeakerMapping, StudioProject
from .naming import FileNamer
from .output import StudioOutputWriter
from .script_parser import ScriptParser
from .synthesizer import StudioSynthesizer


class StudioEngine:
    """Studioオーケストレーター."""

    def __init__(
        self,
        config: dict,
        output_dir: Path = Path("output"),
        *,
        synthesis_service: SynthesisService | None = None,
    ):
        self.config = config
        self.output_dir = output_dir
        self._synthesis_service = synthesis_service

    async def synth(
        self,
        script_path: Path,
        speaker_map: dict | Path | None = None,
        provider: str | None = None,
        output_format: str = "ymm4",
        default_pause: float = 0.3,
        project_name: str | None = None,
        no_cache: bool = False,
    ) -> StudioProject:
        """台本から音声素材をバッチ合成."""
        studio_cfg = self.config.get("studio", {})
        fmt = output_format or studio_cfg.get("default_format", "ymm4")
        pause = default_pause or studio_cfg.get("default_pause", 0.3)
        max_slug = studio_cfg.get("max_slug_chars", 15)
        default_provider = provider or studio_cfg.get("default_provider", "voicevox")
        cache_enabled = not no_cache and studio_cfg.get("cache_enabled", True)

        name = project_name or script_path.stem
        project_dir = self.output_dir / name

        # 1. パース
        parser = ScriptParser()
        lines = parser.parse(script_path)
        if not lines:
            raise ValueError(f"No lines found in script: {script_path}")
        logger.info(f"Parsed {len(lines)} lines from {script_path.name}")

        # 2. スピーカーマッピング解決
        mappings = self._resolve_speaker_mappings(
            lines, speaker_map, default_provider
        )
        speakers = sorted(mappings.keys())
        logger.info(f"Speakers: {', '.join(speakers)}")

        # 3. プロバイダー初期化
        # 外部からserviceを注入した場合、Providerはcomposition root側の所有物である。
        # ここでlegacy Providerを生成すると未使用のセッションがリークするため、完全に
        # 構築をスキップする。
        providers = (
            {}
            if self._synthesis_service is not None
            else self._create_providers(mappings, primary=default_provider)
        )

        # 4. ファイル命名 + 共通service cache
        namer = FileNamer(format=fmt, max_slug_chars=max_slug)
        cache = TTSCache(project_dir / "cache") if cache_enabled else None
        service = self._synthesis_service or create_synthesis_service(
            providers,
            primary=default_provider,
            fallback=self._configured_fallback_provider(default_provider),
            cache=cache,
            config=self.config,
        )
        owns_service = self._synthesis_service is None
        synthesizer = StudioSynthesizer(
            providers=providers,
            default_provider=default_provider,
            synthesis_service=service,
        )

        # 5. 合成
        def on_progress(current: int, total: int, line: ScriptLine) -> None:
            pass  # ログはsynthesizer内で出力

        try:
            results = await synthesizer.synthesize_all(
                lines=lines,
                speaker_mappings=mappings,
                namer=namer,
                output_dir=project_dir,
                cache=None,
                on_progress=on_progress,
            )
        finally:
            if owns_service:
                await service.close()

        # 6. プロジェクト構築
        project = StudioProject(
            name=name,
            output_dir=project_dir,
            lines=lines,
            speaker_mappings=mappings,
            results=results,
            default_pause=pause,
            output_format=fmt,
        )

        # 7. 出力ファイル生成
        writer = StudioOutputWriter()

        # YMM4テキストファイル
        if fmt == "ymm4":
            for r in results:
                if r.txt_path:
                    line = next(ln for ln in lines if ln.index == r.line_index)
                    writer.write_ymm4_txt(line, r.txt_path)

        writer.write_metadata_json(project, project_dir / "metadata.json")
        writer.write_srt(results, project_dir / "subtitles.srt", pause)
        writer.write_ass(results, project_dir / "subtitles.ass",
                         default_pause=pause)

        total_dur = sum(r.duration for r in results)
        logger.info(
            f"Done: {len(results)} files, "
            f"{total_dur:.1f}s total → {project_dir}"
        )

        return project

    async def preview(
        self,
        script_path: Path,
        line_number: int = 1,
        speaker_map: dict | Path | None = None,
        provider: str | None = None,
    ) -> None:
        """台本の指定行をプレビュー再生."""
        studio_cfg = self.config.get("studio", {})
        default_provider = provider or studio_cfg.get("default_provider", "voicevox")

        parser = ScriptParser()
        lines = parser.parse(script_path)
        if not lines:
            raise ValueError(f"No lines found in script: {script_path}")

        # 1-based → 0-based
        idx = line_number - 1
        if idx < 0 or idx >= len(lines):
            raise ValueError(
                f"Line {line_number} out of range (1-{len(lines)})"
            )

        line = lines[idx]
        mappings = self._resolve_speaker_mappings(
            lines, speaker_map, default_provider
        )
        mapping = mappings.get(line.speaker, SpeakerMapping(
            speaker=line.speaker, provider=default_provider, voice_id=""
        ))

        providers = (
            {}
            if self._synthesis_service is not None
            else self._create_providers(mappings, primary=default_provider)
        )
        service = self._synthesis_service or create_synthesis_service(
            providers,
            primary=default_provider,
            fallback=self._configured_fallback_provider(default_provider),
            config=self.config,
        )
        owns_service = self._synthesis_service is None
        synthesizer = StudioSynthesizer(
            providers=providers,
            default_provider=default_provider,
            synthesis_service=service,
        )

        typer.echo(f"Preview [{line_number}] {line.speaker}: {line.text}")
        try:
            await synthesizer.preview_line(line, mapping)
        finally:
            if owns_service:
                await service.close()

    def _resolve_speaker_mappings(
        self,
        lines: list[ScriptLine],
        speaker_map: dict | Path | None,
        default_provider: str,
    ) -> dict[str, SpeakerMapping]:
        """話者マッピングを解決."""
        mappings: dict[str, SpeakerMapping] = {}

        # ファイルまたはdict から読み込み
        raw_map: dict = {}
        if isinstance(speaker_map, Path):
            content = speaker_map.read_text(encoding="utf-8")
            if speaker_map.suffix.lower() == ".json":
                raw_map = json.loads(content)
            else:
                import yaml
                raw_map = yaml.safe_load(content) or {}
        elif isinstance(speaker_map, dict):
            raw_map = speaker_map

        for speaker, cfg in raw_map.items():
            if isinstance(cfg, dict):
                mappings[speaker] = SpeakerMapping(
                    speaker=speaker,
                    provider=cfg.get("provider", default_provider),
                    voice_id=str(cfg.get("voice_id", "")),
                    base_params={
                        k: v for k, v in cfg.items()
                        if k not in ("provider", "voice_id", "speaker")
                    },
                )
            elif isinstance(cfg, str):
                # 簡易形式: "speaker: voice_id"
                mappings[speaker] = SpeakerMapping(
                    speaker=speaker,
                    provider=default_provider,
                    voice_id=cfg,
                )

        # スクリプト内の全話者に対しデフォルトマッピングを補完
        all_speakers = {line.speaker for line in lines}
        for speaker in all_speakers:
            if speaker not in mappings:
                mappings[speaker] = SpeakerMapping(
                    speaker=speaker,
                    provider=default_provider,
                    voice_id="",
                )

        return mappings

    def _create_providers(
        self,
        mappings: dict[str, SpeakerMapping],
        *,
        primary: str | None = None,
    ) -> dict:
        """必要なTTSプロバイダーを初期化."""
        needed = {m.provider for m in mappings.values()}
        resolved_primary = primary or self.config.get("studio", {}).get(
            "default_provider", "voicevox"
        )
        needed.add(str(resolved_primary))
        fallback = self._configured_fallback_provider(str(resolved_primary))
        if fallback:
            needed.add(fallback)
        providers = create_studio_providers(self.config, needed)
        for name in needed - providers.keys():
            logger.warning(f"Unknown provider: {name}, skipping")
        return providers

    def _configured_fallback_provider(self, primary: str) -> str | None:
        """Studio/legacy tts設定からfallback名を解決する。"""

        studio_cfg = self.config.get("studio", {})
        tts_cfg = self.config.get("tts", {})
        if not isinstance(studio_cfg, dict):
            studio_cfg = {}
        if not isinstance(tts_cfg, dict):
            tts_cfg = {}
        fallback = studio_cfg.get("fallback_provider") or tts_cfg.get("fallback_provider")
        return str(fallback) if fallback and str(fallback) != primary else None
