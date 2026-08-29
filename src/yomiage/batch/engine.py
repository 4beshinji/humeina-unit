"""BatchEngine — A+B+C orchestrator for batch pipeline."""

import asyncio
import copy
import re
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from loguru import logger

from ..adapters.persistence.filesystem import FilesystemArtifactStore
from ..application.synthesis import SynthesisRequest, SynthesisService
from ..config import get_tts_config
from ..core.identity import new_job_id, new_run_id
from ..core.models import Utterance, VoiceIntent
from ..llm import LLMBackend
from ..llm.factory import create_llm_service, normalize_llm_config
from ..nlp.text_processor import TextProcessor
from ..ports.artifacts import ArtifactRef, ArtifactStore, ArtifactValidationError
from ..ports.sources import URLPolicy
from ..reader.character_db import CharacterDB
from ..reader.param_mapper import ParamMapper
from .analyzer import BatchAnalyzer
from .concatenator import Concatenator
from .manifest import BatchManifest, SentenceEntry
from .synthesizer import BatchSynthesizer


class BatchEngine:
    """バッチパイプライン A+B+C オーケストレーター."""

    def __init__(
        self,
        config: dict,
        mode: str = "voisona",
        output_dir: str = "output",
        output_format: str = "wav",
        cleanup: bool = False,
        artifact_store: ArtifactStore | None = None,
        synthesis_service: SynthesisService | Any | None = None,
        url_policy: URLPolicy | None = None,
        metrics: Any | None = None,
        llm: LLMBackend | None = None,
    ):
        self.config = config
        self.mode = mode
        self.output_dir = Path(output_dir)
        self.output_format = output_format
        self.cleanup = cleanup
        # ArtifactStoreをcomposition rootで差し替え可能にし、既定では既存のcanonical
        # run layoutへ接続する。旧manifestの読み取り互換はBatchManifest側に残す。
        self.artifact_store = artifact_store or FilesystemArtifactStore(self.output_dir)
        # Provider construction remains lazy because character profiles are restored from the
        # analysis manifest.  Tests and composition roots may inject the shared application
        # service directly; in that case no BatchSynthesizer/provider is constructed here.
        self.synthesis_service = synthesis_service
        self.metrics = metrics
        if self.synthesis_service is not None and metrics is not None:
            if getattr(self.synthesis_service, "metrics", None) is None:
                try:
                    self.synthesis_service.metrics = metrics
                except AttributeError:  # pragma: no cover - immutable fake
                    pass
        self._owns_synthesis_service = synthesis_service is None
        self.url_policy = url_policy

        batch_cfg = config.get("batch", {})
        self.llm = llm or create_llm_service(config, metrics=metrics)
        self._owns_llm = llm is None
        llm_cfg = normalize_llm_config(config)
        providers = llm_cfg.get("providers", {})
        local = providers.get("local", {}) if isinstance(providers, Mapping) else {}
        limits = llm_cfg.get("limits", {})

        scene_params = config.get("scene_params")
        self.param_mapper = (
            ParamMapper(scene_params)
            if isinstance(scene_params, dict)
            else ParamMapper.from_config_file()
        )

        self.silence_duration = batch_cfg.get("silence_duration", 1.5)
        self.save_interval = batch_cfg.get("manifest_save_interval", 10)
        self.vm_mount = batch_cfg.get("voisona_vm_mount", "Z:")
        self.analysis_window_chars = batch_cfg.get("analysis_window_chars", 3000)
        self.analysis_window_sentences = batch_cfg.get("analysis_window_sentences", 25)
        self.llm_context_tokens = int(local.get("context_tokens", 32768))
        self.llm_context_safety_ratio = float(limits.get("context_safety_ratio", 0.8))
        self.analysis_strategy = str(batch_cfg.get("analysis_strategy", "single_pass"))

        # Load VoiceProfile if available
        self.voice_profile = None
        if mode == "voisona":
            self.voice_profile = self._load_voice_profile(batch_cfg)
        elif mode == "voicevox":
            self.voice_profile = self._load_voicevox_profile(batch_cfg)
        elif mode == "voicepeak":
            self.voice_profile = self._load_voicepeak_profile(batch_cfg)

    def _load_voice_profile(self, batch_cfg: dict):
        """Load VoiceProfile from configured directory."""
        from ..tools.voice_profile import VoiceProfile

        profile_dir = Path(
            batch_cfg.get("voice_profile_dir", "~/.config/humeina-unit/voice_profiles")
        ).expanduser()
        voice_name = self.config.get("voisona", {}).get(
            "default_voice", "nurse-robot-type-t_ja_JP"
        )
        profile = VoiceProfile.find(voice_name, search_dirs=[profile_dir])
        if profile:
            logger.info(f"Loaded voice profile: {profile.display_name}")
        else:
            logger.debug(f"No voice profile found for {voice_name}")
        return profile

    def _load_voicevox_profile(self, batch_cfg: dict):
        """Load VoicevoxVoiceProfile from configured directory."""
        from ..tools.voicevox_profile import VoicevoxVoiceProfile

        profile_dir = Path(
            batch_cfg.get("voicevox_profile_dir", "~/.config/humeina-unit/voicevox_profiles")
        ).expanduser()
        speaker_name = self.config.get("voicevox", {}).get(
            "default_speaker_name", "ナースロボ_タイプT"
        )
        profile = VoicevoxVoiceProfile.find(speaker_name, search_dirs=[profile_dir])
        if profile:
            logger.info(f"Loaded VOICEVOX profile: {profile.display_name}")
        else:
            logger.debug(f"No VOICEVOX profile found for {speaker_name}")
        return profile

    def _load_voicepeak_profile(self, batch_cfg: dict):
        """Load VoicepeakVoiceProfile from configured directory."""
        from ..tools.voicepeak_profile import VoicepeakVoiceProfile

        profile_dir = Path(
            batch_cfg.get("voicepeak_profile_dir", "~/.config/humeina-unit/voicepeak_profiles")
        ).expanduser()
        narrator_name = self.config.get("voicepeak", {}).get(
            "default_narrator", ""
        )
        if not narrator_name:
            logger.debug("No default narrator configured for VOICEPEAK")
            return None
        profile = VoicepeakVoiceProfile.find(narrator_name, search_dirs=[profile_dir])
        if profile:
            logger.info(f"Loaded VOICEPEAK profile: {profile.display_name}")
        else:
            logger.debug(f"No VOICEPEAK profile found for {narrator_name}")
        return profile

    def _create_synthesizer(
        self, character_db: CharacterDB | None = None
    ) -> BatchSynthesizer:
        if self.mode == "voisona":
            from ..bootstrap import create_tts_provider
            from .voisona_synth import VoisonaBatchSynthesizer

            return VoisonaBatchSynthesizer(
                config=get_tts_config(self.config, "voisona"),
                param_mapper=self.param_mapper,
                character_db=character_db,
                vm_mount=self.vm_mount,
                voice_profile=self.voice_profile,
                provider=create_tts_provider(self.config, "voisona"),
            )
        elif self.mode == "voicepeak":
            from ..bootstrap import create_tts_provider
            from .voicepeak_synth import VoicepeakBatchSynthesizer

            return VoicepeakBatchSynthesizer(
                config=get_tts_config(self.config, "voicepeak"),
                param_mapper=self.param_mapper,
                character_db=character_db,
                voice_profile=self.voice_profile,
                provider=create_tts_provider(self.config, "voicepeak"),
            )
        else:
            from ..bootstrap import create_tts_provider
            from .voicevox_synth import VoicevoxBatchSynthesizer

            return VoicevoxBatchSynthesizer(
                config=get_tts_config(self.config, "voicevox"),
                param_mapper=self.param_mapper,
                character_db=character_db,
                voice_profile=self.voice_profile,
                provider=create_tts_provider(self.config, "voicevox"),
            )

    def _create_synthesis_service(
        self,
        synthesizer: BatchSynthesizer | None,
    ) -> SynthesisService | Any | None:
        """Batch providerを共通 ``SynthesisService`` へ適合する。

        旧 ``BatchSynthesizer`` は移行期間のparameter builderとして残すが、実際のprovider
        呼び出しはここで作るport adapterを通り、retry・artifact公開・結果検証は
        application serviceへ集約する。旧テスト用の最小fake synthesizer（provider属性を
        持たないもの）は、従来経路へ戻せるよう ``None`` を返す。
        """

        if self.synthesis_service is not None:
            return self.synthesis_service
        provider = getattr(synthesizer, "provider", None) if synthesizer is not None else None
        if provider is None:
            # Legacy test doubles may intentionally omit a provider and exercise the old
            # synthesizer fallback below.  Real runs always obtain one through bootstrap in
            # ``_create_synthesizer``.
            return None

        from ..bootstrap import create_synthesis_service

        return create_synthesis_service(
            {self.mode: provider},
            primary=self.mode,
            config=self.config,
            concurrency=(
                self.config.get("synthesis", {}).get(
                    "concurrency",
                    self.config.get("batch", {}).get("provider_concurrency", 1),
                )
                if isinstance(self.config.get("synthesis", {}), Mapping)
                else None
            ),
            artifact_store=self.artifact_store,
            metrics=self.metrics,
        )

    @staticmethod
    def _entry_params(
        entry: SentenceEntry,
        character_db: CharacterDB | None,
        synthesizer: BatchSynthesizer | None,
    ) -> dict[str, Any]:
        """移行中の旧parameter builderをprovider-independent request metadataへ変換する。"""

        builder = getattr(synthesizer, "_build_params", None) if synthesizer else None
        if callable(builder):
            try:
                value = builder(entry)
                return dict(value) if isinstance(value, Mapping) else {}
            except Exception as exc:  # pragma: no cover - malformed legacy profile guard
                logger.warning("legacy parameter builder failed for %s: %s", entry.index, exc)

        params: dict[str, Any] = {}
        if entry.speaker and character_db:
            character = character_db.characters.get(entry.speaker)
            if character and character.base_params:
                params.update(
                    {
                        key: value
                        for key, value in character.base_params.items()
                        if not str(key).startswith("_")
                    }
                )
                if character.voice_id:
                    params["voice_id"] = character.voice_id
        if entry.tts_params:
            params.update(entry.tts_params)
        return params

    def _request_for_entry(
        self,
        entry: SentenceEntry,
        utterance: Utterance,
        *,
        character_db: CharacterDB | None,
        synthesizer: BatchSynthesizer | None,
        work_dir: Path,
        manifest: BatchManifest,
        service: SynthesisService | Any | None = None,
    ) -> SynthesisRequest:
        """Utteranceと旧manifestのparameter情報を1つのservice requestへ束ねる。

        Canonical runs use the configured service primary and its fallback chain.  Older
        callers that do not provide a service retain the explicit mode pin so the legacy
        parameter-builder path remains unchanged.
        """

        params = self._entry_params(entry, character_db, synthesizer)
        speed = params.get("speed", utterance.voice_intent.speaking_rate)
        # VOICEPEAKのlegacy profileは50〜200のnative値、それ以外はratioを使う。
        if self.mode == "voicepeak" and isinstance(speed, (int, float)) and speed > 10:
            speed = float(speed) / 100.0
        try:
            intent = VoiceIntent(
                archetype=(
                    character_db.characters[entry.speaker].base_params.get("_archetype")
                    if entry.speaker
                    and character_db
                    and entry.speaker in character_db.characters
                    and character_db.characters[entry.speaker].base_params
                    else None
                ),
                speaking_rate=float(speed),
                pitch=float(params.get("pitch", utterance.voice_intent.pitch)),
                volume=float(params.get("volume", utterance.voice_intent.volume)),
                intonation=float(params.get("intonation", utterance.voice_intent.intonation)),
                emotion=utterance.emotion,
                intensity=utterance.intensity,
            )
        except (TypeError, ValueError):
            # A malformed optional legacy profile must not erase a valid plan.  Provider
            # adapters receive the raw parameters below, while the domain intent falls back to
            # normalized NLP values.
            intent = utterance.voice_intent

        metadata = dict(params)
        metadata.update(
            {
                "run_dir": str(work_dir),
                "vm_mount": self.vm_mount,
                "entry_index": entry.index,
                "sentence_index": entry.index,
                "utterance_id": utterance.id,
            }
        )
        if entry.speaker and character_db:
            character = character_db.characters.get(entry.speaker)
            if character and character.voice_id and "voice_id" not in metadata:
                metadata["voice_id"] = character.voice_id
        provider_name: str | None = self.mode
        primary_name = getattr(getattr(service, "primary_provider", None), "name", None)
        if primary_name == self.mode:
            provider_name = None
        return SynthesisRequest(
            text=utterance.text,
            voice_intent=intent,
            utterance_id=str(entry.index),
            provider=provider_name,
            work_id=manifest.work_id if manifest.run_id else None,
            run_id=manifest.run_id,
            destination=f"{entry.index:04d}.wav",
            metadata=metadata,
        )

    # --- Phase A ---

    async def analyze(
        self,
        url: str,
        chapter_range: tuple[int, int] | None = None,
    ) -> BatchManifest:
        """Phase A: 全文分析してマニフェスト生成."""
        analyzer = BatchAnalyzer(
            llm=self.llm,
            analysis_window_chars=self.analysis_window_chars,
            analysis_window_sentences=self.analysis_window_sentences,
            context_tokens=self.llm_context_tokens,
            context_safety_ratio=self.llm_context_safety_ratio,
            analysis_strategy=self.analysis_strategy,
            voice_profile=self.voice_profile,
            url_policy=self.url_policy,
        )
        return await analyzer.analyze(
            url,
            mode=self.mode,
            output_dir=str(self.output_dir),
            chapter_range=chapter_range,
        )

    # --- Phase B ---

    async def synthesize(
        self,
        work_id: str,
        retry_failed: bool = False,
        progress_callback=None,
        run_id: str | None = None,
    ) -> BatchManifest:
        """Phase B: マニフェストからバッチ合成.

        Args:
            progress_callback: 進捗通知用コールバック.
                Callable[[status: str, percent: float, message: str], None]
        """
        manifest = BatchManifest.load(self.output_dir, work_id, run_id=run_id)

        # A provider change must never write into a previous run.  The plan/analysis portion is
        # copied into a fresh run and all execution state is reset.  An explicit ``run_id`` means
        # resume that exact run and therefore intentionally bypasses this fork.
        if run_id is None and manifest.run_id and manifest.plan_id and manifest.mode != self.mode:
            manifest = _fork_manifest_for_run(manifest, self.mode)
            manifest.save(self.output_dir)

        if not manifest.analysis_complete:
            raise RuntimeError("Analysis not complete — run analyze first")

        # キャラクターDB復元
        char_db = CharacterDB(work_id)
        for name, char_data in manifest.characters.items():
            char_db.get_or_create(name, profile_hint=char_data)
            if char_data.get("base_params"):
                char = char_db.characters[name]
                char.base_params = char_data["base_params"]
            if char_data.get("voice_id"):
                char = char_db.characters[name]
                char.voice_id = char_data["voice_id"]

        # The old synthesizer remains available as a compatibility parameter builder and
        # fallback for legacy manifests.  Canonical runs use only the shared application service.
        synth = (
            None
            if self.synthesis_service is not None
            else self._create_synthesizer(char_db)
        )
        service = self._create_synthesis_service(synth)
        if service is None:
            if synth is None or not await synth.is_available():
                raise RuntimeError(f"{self.mode} TTS provider is not available")

        work_dir = (
            self.artifact_store.run_dir(manifest.work_id, manifest.run_id)
            if manifest.run_id
            else manifest.output_dir(self.output_dir)
        )
        work_dir.mkdir(parents=True, exist_ok=True)

        if service is not None:
            return await self._synthesize_with_service(
                manifest,
                service=service,
                synthesizer=synth,
                character_db=char_db,
                work_dir=work_dir,
                retry_failed=retry_failed,
                progress_callback=progress_callback,
            )

        # RUN-002: artifact manifestはUtteranceの実行状態を全件保持する。run_idのない
        # legacy manifestでは旧経路を維持し、canonical runだけをArtifactStoreへ接続する。
        artifact_enabled = bool(manifest.run_id)
        if artifact_enabled:
            self.artifact_store.initialize_manifest(
                manifest.work_id,
                manifest.run_id,
                [str(entry.index) for entry in manifest.sentences],
                provider=self.mode,
            )
            self._sync_existing_artifacts(manifest, work_dir)

        # 合成対象を選定
        if retry_failed:
            targets = manifest.failed_sentences
            logger.info(f"Retrying {len(targets)} failed sentences")
        else:
            targets = manifest.pending_sentences
            logger.info(f"Synthesizing {len(targets)} pending sentences")

        total = len(targets) or 1
        count = 0
        await self._romanize_entries(targets)
        for entry in targets:
            # シーンブレーク
            if entry.segment_type == "scene_break":
                silence_file = f"{entry.index:04d}.wav"
                await synth.generate_silence(
                    self.silence_duration, work_dir / silence_file
                )
                try:
                    artifact = self._publish_artifact(
                        manifest,
                        work_dir / silence_file,
                        silence_file,
                        duration=self.silence_duration,
                    )
                except ArtifactValidationError as exc:
                    (work_dir / silence_file).unlink(missing_ok=True)
                    entry.audio_file = None
                    entry.status = "failed"
                    if artifact_enabled:
                        self.artifact_store.record_utterance(
                            manifest.work_id,
                            manifest.run_id,
                            str(entry.index),
                            status="failed",
                            provider=self.mode,
                            error=str(exc),
                        )
                else:
                    entry.audio_file = artifact.relative_path
                    entry.duration = artifact.duration
                    entry.status = "synthesized"
                count += 1
            elif _has_speakable_text(entry.text):
                result = await synth.synthesize_sentence(entry, work_dir)
                if result:
                    source_path = work_dir / result
                    try:
                        artifact = self._publish_artifact(
                            manifest,
                            source_path,
                            result,
                        )
                    except ArtifactValidationError as exc:
                        source_path.unlink(missing_ok=True)
                        entry.audio_file = None
                        entry.status = "failed"
                        if artifact_enabled:
                            self.artifact_store.record_utterance(
                                manifest.work_id,
                                manifest.run_id,
                                str(entry.index),
                                status="failed",
                                provider=self.mode,
                                error=str(exc),
                            )
                    else:
                        entry.audio_file = artifact.relative_path
                        entry.duration = artifact.duration
                        entry.status = "synthesized"
                else:
                    entry.status = "failed"
                    if artifact_enabled:
                        self.artifact_store.record_utterance(
                            manifest.work_id,
                            manifest.run_id,
                            str(entry.index),
                            status="failed",
                            provider=self.mode,
                            error="provider returned no artifact",
                        )
                count += 1
            else:
                entry.status = "synthesized"
                if artifact_enabled:
                    self.artifact_store.record_utterance(
                        manifest.work_id,
                        manifest.run_id,
                        str(entry.index),
                        status="synthesized",
                        provider=self.mode,
                    )
                count += 1

            # scene-break/speakableの成功状態はpublish後のみ記録する。上述の失敗分岐は
            # 既にfailedを保存しているため、ここでは成功だけを反映する。
            if artifact_enabled and entry.status == "synthesized" and entry.audio_file:
                self.artifact_store.record_utterance(
                    manifest.work_id,
                    manifest.run_id,
                    str(entry.index),
                    status="synthesized",
                    artifact=artifact,
                    duration=entry.duration,
                    provider=self.mode,
                )

            # 進捗通知
            percent = round((count / total) * 100, 1)
            if progress_callback and count % self.save_interval == 0:
                await _maybe_callback(
                    progress_callback,
                    "synthesizing",
                    percent,
                    f"{count}/{total} 文を合成しました",
                )

            # 進捗表示 & マニフェスト定期保存
            if count % self.save_interval == 0:
                manifest.save(self.output_dir)
                logger.info(f"Progress: {manifest.progress_str()}")

        # 完了判定（failed/pendingをcompletedへ昇格させない）
        manifest.refresh_completion_status(self.output_dir)

        manifest.save(self.output_dir)
        logger.info(
            f"Synthesis {manifest.completion_status}: "
            f"{manifest.progress_str()}"
        )
        return manifest

    async def _synthesize_with_service(
        self,
        manifest: BatchManifest,
        *,
        service: SynthesisService | Any,
        synthesizer: BatchSynthesizer | None,
        character_db: CharacterDB,
        work_dir: Path,
        retry_failed: bool,
        progress_callback: Any = None,
    ) -> BatchManifest:
        """SynthesisServiceの実行境界とlifecycleを管理する。"""

        if not self._owns_synthesis_service:
            return await self._synthesize_with_service_impl(
                manifest,
                service=service,
                synthesizer=synthesizer,
                character_db=character_db,
                work_dir=work_dir,
                retry_failed=retry_failed,
                progress_callback=progress_callback,
            )
        try:
            return await self._synthesize_with_service_impl(
                manifest,
                service=service,
                synthesizer=synthesizer,
                character_db=character_db,
                work_dir=work_dir,
                retry_failed=retry_failed,
                progress_callback=progress_callback,
            )
        finally:
            await service.close()

    async def _synthesize_with_service_impl(
        self,
        manifest: BatchManifest,
        *,
        service: SynthesisService | Any,
        synthesizer: BatchSynthesizer | None,
        character_db: CharacterDB,
        work_dir: Path,
        retry_failed: bool,
        progress_callback: Any = None,
    ) -> BatchManifest:
        """Canonical runをUtterance→SynthesisService→ArtifactStoreで実行する。"""

        artifact_enabled = bool(manifest.run_id)
        if artifact_enabled:
            self.artifact_store.initialize_manifest(
                manifest.work_id,
                manifest.run_id,
                [str(entry.index) for entry in manifest.sentences],
                provider=self.mode,
            )
            self._restore_artifact_entries(manifest, work_dir)

        await self._romanize_entries(
            manifest.failed_sentences if retry_failed else manifest.pending_sentences
        )

        # merge_adjacent=False is intentional: the old Batch manifest is already sentence
        # segmented, and resume/retry needs a stable 1:1 entry↔utterance↔artifact mapping.
        plan = manifest.to_utterance_plan(config=self.config, merge_adjacent=False)
        plan_utterances = iter(plan.utterances)

        utterance_by_index: dict[int, Utterance] = {}
        for entry in manifest.sentences:
            if entry.segment_type == "scene_break" or not entry.text.strip():
                continue
            utterance_by_index[entry.index] = next(plan_utterances)

        targets = (
            manifest.failed_sentences if retry_failed else manifest.pending_sentences
        )
        logger.info(
            f"{'Retrying' if retry_failed else 'Synthesizing'} {len(targets)} "
            f"{self.mode} sentences through SynthesisService"
        )
        total = len(targets) or 1
        count = 0

        for entry in targets:
            artifact: ArtifactRef | None = None
            result_status = "synthesized"
            try:
                if entry.segment_type == "scene_break":
                    filename = f"{entry.index:04d}.wav"
                    source = work_dir / f".{filename}.silence"
                    from ..tts.audio_utils import write_silence_wav

                    write_silence_wav(source, self.silence_duration)
                    artifact = self._publish_artifact(
                        manifest,
                        source,
                        filename,
                        duration=self.silence_duration,
                    )
                elif not _has_speakable_text(entry.text):
                    # 記号だけの文は明示的skipとしてartifact manifestへ残す。空artifactを
                    # synthesizedとして記録すると、次回resumeでpendingへ戻り続ける。
                    entry.status = "skipped"
                    entry.audio_file = None
                    result_status = "skipped"
                else:
                    utterance = utterance_by_index.get(entry.index)
                    if utterance is None:
                        raise RuntimeError(f"no UtterancePlan item for sentence {entry.index}")
                    request = self._request_for_entry(
                        entry,
                        utterance,
                        character_db=character_db,
                        synthesizer=synthesizer,
                        work_dir=work_dir,
                        manifest=manifest,
                        service=service,
                    )
                    raw_result = await service.synthesize(request)
                    result_status = str(getattr(raw_result, "status", "synthesized"))
                    if result_status not in {"synthesized", "fallback_synthesized"}:
                        result_status = "synthesized"
                    artifact = self._artifact_from_service_result(
                        raw_result,
                        manifest=manifest,
                        work_dir=work_dir,
                        filename=f"{entry.index:04d}.wav",
                        artifact_id=str(entry.index),
                    )
                    if artifact is None:
                        raise ArtifactValidationError(
                            f"provider returned no verified artifact for sentence {entry.index}"
                        )

                if artifact is not None:
                    entry.audio_file = artifact.relative_path
                    entry.duration = artifact.duration
                    entry.status = "synthesized"
                if artifact_enabled:
                    self.artifact_store.record_utterance(
                        manifest.work_id,
                        manifest.run_id,
                        str(entry.index),
                        status=result_status,
                        artifact=artifact,
                        duration=entry.duration,
                        provider=(artifact.provider if artifact else self.mode),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"{self.mode} synthesis failed for {entry.index}: {exc}")
                entry.audio_file = None
                entry.duration = None
                entry.status = "failed"
                if artifact_enabled:
                    self.artifact_store.record_utterance(
                        manifest.work_id,
                        manifest.run_id,
                        str(entry.index),
                        status="failed",
                        provider=self.mode,
                        error=str(exc),
                    )

            count += 1
            percent = round((count / total) * 100, 1)
            if progress_callback and count % max(1, self.save_interval) == 0:
                await _maybe_callback(
                    progress_callback,
                    "synthesizing",
                    percent,
                    f"{count}/{total} 文を合成しました",
                )
            if count % max(1, self.save_interval) == 0:
                manifest.save(self.output_dir)

        manifest.refresh_completion_status(self.output_dir)
        manifest.save(self.output_dir)
        logger.info(f"Synthesis {manifest.completion_status}: {manifest.progress_str()}")
        return manifest

    def _restore_artifact_entries(self, manifest: BatchManifest, work_dir: Path) -> None:
        """artifact manifestをresumeの唯一の成功判定として既存entryへ反映する。"""

        if not manifest.run_id:
            return
        artifact_manifest = self.artifact_store.load_manifest(manifest.work_id, manifest.run_id)
        issues = {
            issue.utterance_id: issue
            for issue in self.artifact_store.verify_manifest(manifest.work_id, manifest.run_id)
        }
        for entry in manifest.sentences:
            record = artifact_manifest.get(str(entry.index))
            if record is not None and record.is_final and str(entry.index) not in issues:
                entry.status = "synthesized"
                entry.audio_file = record.path
                entry.duration = record.duration
                continue
            if record is not None and record.status == "skipped":
                entry.status = "skipped"
                entry.audio_file = None
                continue
            if record is not None and record.status == "failed":
                entry.status = "failed"
                entry.audio_file = None
                entry.duration = None
                continue
            if record is not None:
                # A recorded final artifact with a checksum/path issue is not a migration
                # candidate.  Mark it pending so the provider regenerates it instead of
                # silently blessing a modified-but-valid WAV.
                entry.status = "pending"
                entry.audio_file = None
                entry.duration = None
                continue

            # RUN-002以前にmanifestだけ保存されたcanonical runをread-only importする。
            # 既存音声が検証できない場合はpendingへ戻し、次の通常resumeで再生成する。
            if entry.status == "synthesized" and entry.audio_file:
                source = work_dir / entry.audio_file
                try:
                    artifact = self._publish_artifact(
                        manifest,
                        source,
                        entry.audio_file,
                        duration=entry.duration,
                    )
                except (ArtifactValidationError, OSError, ValueError):
                    entry.status = "pending"
                    entry.audio_file = None
                    entry.duration = None
                else:
                    self.artifact_store.record_utterance(
                        manifest.work_id,
                        manifest.run_id,
                        str(entry.index),
                        status="synthesized",
                        artifact=artifact,
                        duration=entry.duration,
                        provider=self.mode,
                    )
            elif entry.status == "synthesized":
                entry.status = "pending"

    def _artifact_from_service_result(
        self,
        result: Any,
        *,
        manifest: BatchManifest,
        work_dir: Path,
        filename: str,
        artifact_id: str,
    ) -> ArtifactRef | None:
        """SynthesisService結果を検証済みArtifactRefへ正規化する。

        通常のSynthesisServiceは既にArtifactStoreへ公開済みrefを返す。fake serviceや移行
        adapterがbytesだけを返す場合も、同じ検証・atomic publish境界へ通す。
        """

        artifact = getattr(result, "artifact", None)
        if artifact is None:
            artifact = getattr(result, "artifact_ref", None)
        if isinstance(artifact, ArtifactRef):
            return artifact
        audio_data = getattr(result, "audio_data", None)
        if audio_data is None:
            audio_data = getattr(result, "audio", None)
        if not audio_data:
            return None
        temporary = work_dir / f".{filename}.service"
        temporary.write_bytes(bytes(audio_data))
        try:
            if manifest.run_id:
                return self.artifact_store.publish(
                    temporary,
                    filename,
                    work_id=manifest.work_id,
                    run_id=manifest.run_id,
                    artifact_id=artifact_id,
                    provider=getattr(result, "provider", self.mode),
                    duration=getattr(result, "duration", None),
                )
            return self._publish_artifact(
                manifest,
                temporary,
                filename,
                duration=getattr(result, "duration", None),
            )
        finally:
            temporary.unlink(missing_ok=True)

    def _publish_artifact(
        self,
        manifest: BatchManifest,
        source_path: Path,
        relative_path: str,
        *,
        duration: float | None = None,
    ) -> ArtifactRef:
        """providerが生成したWAVを検証済みartifactとして公開する。"""

        if not manifest.run_id:
            # Legacy manifestは従来のoutput/{work_id}を使い続ける。canonical storeの
            # run境界外なので、ここでは同じWAV validation/checksum契約だけを適用する。
            try:
                with wave.open(str(source_path), "rb") as stream:
                    if stream.getframerate() <= 0 or stream.getnchannels() <= 0:
                        raise ValueError("WAV has invalid format")
                    measured_duration = stream.getnframes() / stream.getframerate()
            except (EOFError, OSError, wave.Error, ValueError) as exc:
                raise ArtifactValidationError(f"invalid WAV artifact: {source_path}") from exc
            return ArtifactRef(
                artifact_id=relative_path,
                relative_path=relative_path,
                path=source_path,
                checksum=self.artifact_store.checksum(source_path),
                size_bytes=source_path.stat().st_size,
                duration=duration if duration is not None else measured_duration,
                provider=self.mode,
            )
        return self.artifact_store.publish(
            source_path,
            relative_path,
            work_id=manifest.work_id,
            run_id=manifest.run_id,
            artifact_id=str(relative_path),
            provider=self.mode,
            duration=duration,
        )

    def _sync_existing_artifacts(self, manifest: BatchManifest, work_dir: Path) -> None:
        """RUN-002以前に生成された音声をartifact manifestへ取り込む。

        既存canonical runをresumeする場合、BatchManifestにはsynthesized状態だけが残り
        artifact-manifest.jsonが存在しないことがある。実体を再検証できた場合のみfinal
        artifactとして記録し、欠損/破損なら次の完了判定へ失敗を伝える。
        """

        if not manifest.run_id:
            return
        for entry in manifest.sentences:
            if entry.status == "synthesized" and entry.audio_file:
                source = work_dir / entry.audio_file
                try:
                    artifact = self._publish_artifact(
                        manifest,
                        source,
                        entry.audio_file,
                        duration=entry.duration,
                    )
                except (ArtifactValidationError, OSError, ValueError) as exc:
                    entry.status = "failed"
                    entry.audio_file = None
                    entry.duration = None
                    self.artifact_store.record_utterance(
                        manifest.work_id,
                        manifest.run_id,
                        str(entry.index),
                        status="failed",
                        provider=self.mode,
                        error=str(exc),
                    )
                else:
                    self.artifact_store.record_utterance(
                        manifest.work_id,
                        manifest.run_id,
                        str(entry.index),
                        status="synthesized",
                        artifact=artifact,
                        duration=entry.duration,
                        provider=self.mode,
                    )
            elif entry.status == "synthesized":
                entry.status = "failed"
                self.artifact_store.record_utterance(
                    manifest.work_id,
                    manifest.run_id,
                    str(entry.index),
                    status="failed",
                    provider=self.mode,
                    error="synthesized entry has no audio path",
                )
            elif entry.status == "failed":
                self.artifact_store.record_utterance(
                    manifest.work_id,
                    manifest.run_id,
                    str(entry.index),
                    status="failed",
                    provider=self.mode,
                    error="manifest marked sentence as failed",
                )
            elif entry.status == "skipped":
                self.artifact_store.record_utterance(
                    manifest.work_id,
                    manifest.run_id,
                    str(entry.index),
                    status="skipped",
                    provider=self.mode,
                )

    # --- Phase C ---

    def concat(
        self,
        work_id: str,
        *,
        allow_partial: bool = False,
        run_id: str | None = None,
    ) -> Path | None:
        """Phase C: WAVファイル結合."""
        manifest = BatchManifest.load(self.output_dir, work_id, run_id=run_id)

        concatenator = Concatenator(
            output_format=self.output_format,
            cleanup=self.cleanup,
        )

        return concatenator.concat_chapters_then_full(
            manifest,
            self.output_dir,
            allow_partial=allow_partial,
        )

    # --- Phase D ---

    def video(
        self,
        work_id: str,
        style: str | None = None,
        *,
        run_id: str | None = None,
    ) -> Path | None:
        """Phase D: 動画生成."""
        from ..video.composer import VideoComposer
        from ..video.config import VideoConfig

        manifest = BatchManifest.load(self.output_dir, work_id, run_id=run_id)
        completion_status = manifest.compute_completion_status(self.output_dir)
        if completion_status in {"partial", "failed"}:
            logger.warning(
                f"Skipping video generation for incomplete {completion_status} run"
            )
            return None
        work_dir = manifest.output_dir(self.output_dir)

        video_cfg = VideoConfig.from_dict(self.config.get("video", {}))
        if style:
            video_cfg.style = style
        composer = VideoComposer(video_cfg, work_dir)
        return composer.compose_all(manifest)

    def subtitle(
        self,
        work_id: str,
        fmt: str = "ass",
        *,
        run_id: str | None = None,
    ) -> dict[int, Path]:
        """字幕ファイル生成."""
        from ..video.config import VideoConfig
        from ..video.subtitle import SubtitleGenerator
        from ..video.timeline import TimelineBuilder

        manifest = BatchManifest.load(self.output_dir, work_id, run_id=run_id)
        work_dir = manifest.output_dir(self.output_dir)

        video_cfg = VideoConfig.from_dict(self.config.get("video", {}))
        builder = TimelineBuilder(manifest, work_dir)
        sub_gen = SubtitleGenerator(video_cfg)

        timelines = builder.build_all()
        outputs: dict[int, Path] = {}

        sub_dir = work_dir / "video"
        sub_dir.mkdir(parents=True, exist_ok=True)

        for ch_index, events in sorted(timelines.items()):
            ch_title = ""
            for ch in manifest.chapters:
                if ch.index == ch_index:
                    ch_title = ch.title
                    break

            out_name = f"chapter_{ch_index + 1:03d}.{fmt}"
            out_path = sub_dir / out_name

            if fmt == "srt":
                sub_gen.generate_srt(events, out_path)
            else:
                sub_gen.generate_ass(
                    events, out_path,
                    title=f"{manifest.work_title} - {ch_title}",
                    chapter_title=ch_title,
                )
            outputs[ch_index] = out_path

        # durationをmanifestに書き戻し
        manifest.save(self.output_dir)
        return outputs

    # --- Full pipeline ---

    async def run(
        self,
        url: str,
        chapter_range: tuple[int, int] | None = None,
        video: bool = False,
        style: str | None = None,
        allow_partial: bool = False,
    ) -> Path | None:
        """A + B + C (+ D) フルパイプライン."""
        # Phase A
        logger.info("=== Phase A: Analysis ===")
        manifest = await self.analyze(url, chapter_range)

        # Phase B
        logger.info("=== Phase B: Synthesis ===")
        manifest = await self.synthesize(manifest.work_id, run_id=manifest.run_id)

        completion_status = manifest.refresh_completion_status(self.output_dir)
        if completion_status in {"partial", "failed"} and not allow_partial:
            logger.warning(
                "Skipping concatenation for %s synthesis without allow_partial",
                completion_status,
            )
            manifest.save(self.output_dir)
            return None
        if completion_status == "failed":
            manifest.save(self.output_dir)
            return None

        # Phase C
        logger.info("=== Phase C: Concatenation ===")
        audio_output = self.concat(
            manifest.work_id,
            allow_partial=allow_partial,
            run_id=manifest.run_id,
        )
        if audio_output is None:
            return None

        # Phase D (optional)
        # partial音声を通常名の動画として公開しないため、partial時は音声のみ返す。
        if video and completion_status != "partial":
            logger.info("=== Phase D: Video Generation ===")
            video_output = self.video(
                manifest.work_id,
                style=style,
                run_id=manifest.run_id,
            )
            if video_output:
                return video_output

        return audio_output

    async def _romanize_entries(self, entries: list[SentenceEntry]) -> None:
        """英字を含む全対象文を一回の重複排除済みbatchで変換する。"""

        targets = [
            entry
            for entry in entries
            if _has_speakable_text(entry.text) and TextProcessor.has_alphabet(entry.text)
        ]
        if not targets:
            return
        converted = await self.llm.romanize_many([entry.text for entry in targets])
        for entry, text in zip(targets, converted, strict=True):
            entry.text = text

    async def close(self) -> None:
        """このengineが所有するLLM資源を閉じる。"""

        if self._owns_llm:
            await self.llm.close()
            self._owns_llm = False

    # --- Status ---

    def status(self, work_id: str, *, run_id: str | None = None) -> dict:
        """進捗状況を返す."""
        manifest = BatchManifest.load(self.output_dir, work_id, run_id=run_id)
        completion_status = manifest.compute_completion_status(self.output_dir)
        return {
            "work_id": manifest.work_id,
            "plan_id": manifest.plan_id,
            "run_id": manifest.run_id,
            "job_id": manifest.job_id,
            "work_title": manifest.work_title,
            "mode": manifest.mode,
            "chapters": len(manifest.chapters),
            "total_sentences": manifest.total_count,
            "synthesized": manifest.synthesized_count,
            "pending": len(manifest.pending_sentences),
            "failed": len(manifest.failed_sentences),
            "analysis_complete": manifest.analysis_complete,
            "synthesis_complete": manifest.synthesis_complete,
            "completion_status": completion_status,
            "status": completion_status,
            "progress": manifest.progress_str(),
        }


# 発話可能なテキストか判定（記号・括弧のみはスキップ）
_SPEAKABLE_RE = re.compile(r"[\w\u3040-\u9fff]")


def _has_speakable_text(text: str) -> bool:
    return bool(text and _SPEAKABLE_RE.search(text))


async def _maybe_callback(callback, status: str, percent: float, message: str) -> None:
    """コールバックがあれば非同期/同期どちらにも対応して呼び出す."""
    if callback is None:
        return
    import inspect

    if inspect.iscoroutinefunction(callback):
        await callback(status, percent, message)
    else:
        callback(status, percent, message)


def _fork_manifest_for_run(manifest: BatchManifest, mode: str) -> BatchManifest:
    """Copy analysis metadata into a fresh provider-specific run."""

    sentences: list[SentenceEntry] = []
    for source in manifest.sentences:
        entry = copy.deepcopy(source)
        entry.audio_file = None
        entry.duration = None
        entry.status = "pending"
        sentences.append(entry)
    return BatchManifest(
        work_id=manifest.work_id,
        work_title=manifest.work_title,
        source_url=manifest.source_url,
        mode=mode,
        chapters=copy.deepcopy(manifest.chapters),
        characters=copy.deepcopy(manifest.characters),
        sentences=sentences,
        analysis_complete=manifest.analysis_complete,
        synthesis_complete=False,
        completion_status="pending",
        schema_version=manifest.schema_version,
        plan_id=manifest.plan_id,
        run_id=new_run_id(),
        job_id=new_job_id(),
        source_fingerprint=manifest.source_fingerprint,
        config_fingerprint=manifest.config_fingerprint,
        # Keep the plan creation timestamp; run identity is carried by ``run_id``.
        created_at=manifest.created_at,
    )
