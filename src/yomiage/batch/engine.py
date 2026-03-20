"""BatchEngine — A+B+C orchestrator for batch pipeline."""

import re
from pathlib import Path

from loguru import logger

from ..config import get_tts_config
from ..nlp.ollama_client import OllamaClient
from ..nlp.text_processor import TextProcessor
from ..reader.character_db import CharacterDB
from ..reader.param_mapper import ParamMapper
from .analyzer import BatchAnalyzer
from .concatenator import Concatenator
from .manifest import BatchManifest
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
    ):
        self.config = config
        self.mode = mode
        self.output_dir = Path(output_dir)
        self.output_format = output_format
        self.cleanup = cleanup

        ollama_cfg = config.get("ollama", {})
        batch_cfg = config.get("batch", {})

        self.ollama = OllamaClient(
            url=ollama_cfg.get("url", "http://localhost:11434"),
            model=ollama_cfg.get("batch_model") or ollama_cfg.get("model", "qwen3.5:3b"),
        )

        self.param_mapper = ParamMapper.from_config_file(Path("config/scene_params.yaml"))

        self.silence_duration = batch_cfg.get("silence_duration", 1.5)
        self.save_interval = batch_cfg.get("manifest_save_interval", 10)
        self.vm_mount = batch_cfg.get("voisona_vm_mount", "Z:")
        self.analysis_window_chars = batch_cfg.get("analysis_window_chars", 3000)
        self.analysis_window_sentences = batch_cfg.get("analysis_window_sentences", 25)

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

        profile_dir = Path(batch_cfg.get("voice_profile_dir", "config/voice_profiles"))
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
            batch_cfg.get("voicevox_profile_dir", "config/voicevox_profiles")
        )
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
            batch_cfg.get("voicepeak_profile_dir", "config/voicepeak_profiles")
        )
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
            from .voisona_synth import VoisonaBatchSynthesizer

            return VoisonaBatchSynthesizer(
                config=get_tts_config(self.config, "voisona"),
                param_mapper=self.param_mapper,
                character_db=character_db,
                vm_mount=self.vm_mount,
                voice_profile=self.voice_profile,
            )
        elif self.mode == "voicepeak":
            from .voicepeak_synth import VoicepeakBatchSynthesizer

            return VoicepeakBatchSynthesizer(
                config=get_tts_config(self.config, "voicepeak"),
                param_mapper=self.param_mapper,
                character_db=character_db,
                voice_profile=self.voice_profile,
            )
        else:
            from .voicevox_synth import VoicevoxBatchSynthesizer

            return VoicevoxBatchSynthesizer(
                config=get_tts_config(self.config, "voicevox"),
                param_mapper=self.param_mapper,
                character_db=character_db,
                voice_profile=self.voice_profile,
            )

    # --- Phase A ---

    async def analyze(
        self,
        url: str,
        chapter_range: tuple[int, int] | None = None,
    ) -> BatchManifest:
        """Phase A: 全文分析してマニフェスト生成."""
        analyzer = BatchAnalyzer(
            ollama=self.ollama,
            analysis_window_chars=self.analysis_window_chars,
            analysis_window_sentences=self.analysis_window_sentences,
            voice_profile=self.voice_profile,
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
    ) -> BatchManifest:
        """Phase B: マニフェストからバッチ合成."""
        manifest = BatchManifest.load(self.output_dir, work_id)

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

        synth = self._create_synthesizer(char_db)

        if not await synth.is_available():
            raise RuntimeError(f"{self.mode} TTS provider is not available")

        work_dir = manifest.output_dir(self.output_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        # 合成対象を選定
        if retry_failed:
            targets = manifest.failed_sentences
            logger.info(f"Retrying {len(targets)} failed sentences")
        else:
            targets = manifest.pending_sentences
            logger.info(f"Synthesizing {len(targets)} pending sentences")

        count = 0
        for entry in targets:
            # シーンブレーク
            if entry.segment_type == "scene_break":
                silence_file = f"{entry.index:04d}.wav"
                await synth.generate_silence(
                    self.silence_duration, work_dir / silence_file
                )
                entry.audio_file = silence_file
                entry.status = "synthesized"
                count += 1
            elif _has_speakable_text(entry.text):
                # アルファベット→カタカナ変換（1文単位）
                if TextProcessor.has_alphabet(entry.text):
                    entry.text = await self.ollama.romanize(entry.text)
                result = await synth.synthesize_sentence(entry, work_dir)
                if result:
                    entry.audio_file = result
                    entry.status = "synthesized"
                else:
                    entry.status = "failed"
                count += 1
            else:
                entry.status = "synthesized"
                count += 1

            # 進捗表示 & マニフェスト定期保存
            if count % self.save_interval == 0:
                manifest.save(self.output_dir)
                logger.info(f"Progress: {manifest.progress_str()}")

        # 完了判定
        if not manifest.pending_sentences and not manifest.failed_sentences:
            manifest.synthesis_complete = True

        manifest.save(self.output_dir)
        logger.info(
            f"Synthesis {'complete' if manifest.synthesis_complete else 'partial'}: "
            f"{manifest.progress_str()}"
        )
        return manifest

    # --- Phase C ---

    def concat(self, work_id: str) -> Path | None:
        """Phase C: WAVファイル結合."""
        manifest = BatchManifest.load(self.output_dir, work_id)

        concatenator = Concatenator(
            output_format=self.output_format,
            cleanup=self.cleanup,
        )

        return concatenator.concat_chapters_then_full(manifest, self.output_dir)

    # --- Phase D ---

    def video(self, work_id: str) -> Path | None:
        """Phase D: 動画生成."""
        from ..video.composer import VideoComposer
        from ..video.config import VideoConfig

        manifest = BatchManifest.load(self.output_dir, work_id)
        work_dir = manifest.output_dir(self.output_dir)

        video_cfg = VideoConfig.from_dict(self.config.get("video", {}))
        composer = VideoComposer(video_cfg, work_dir)
        return composer.compose_all(manifest)

    def subtitle(
        self, work_id: str, fmt: str = "ass"
    ) -> dict[int, Path]:
        """字幕ファイル生成."""
        from ..video.config import VideoConfig
        from ..video.subtitle import SubtitleGenerator
        from ..video.timeline import TimelineBuilder

        manifest = BatchManifest.load(self.output_dir, work_id)
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
    ) -> Path | None:
        """A + B + C (+ D) フルパイプライン."""
        # Phase A
        logger.info("=== Phase A: Analysis ===")
        manifest = await self.analyze(url, chapter_range)

        # Phase B
        logger.info("=== Phase B: Synthesis ===")
        manifest = await self.synthesize(manifest.work_id)

        # Phase C
        logger.info("=== Phase C: Concatenation ===")
        audio_output = self.concat(manifest.work_id)

        # Phase D (optional)
        if video:
            logger.info("=== Phase D: Video Generation ===")
            video_output = self.video(manifest.work_id)
            if video_output:
                return video_output

        return audio_output

    # --- Status ---

    def status(self, work_id: str) -> dict:
        """進捗状況を返す."""
        manifest = BatchManifest.load(self.output_dir, work_id)
        return {
            "work_id": manifest.work_id,
            "work_title": manifest.work_title,
            "mode": manifest.mode,
            "chapters": len(manifest.chapters),
            "total_sentences": manifest.total_count,
            "synthesized": manifest.synthesized_count,
            "pending": len(manifest.pending_sentences),
            "failed": len(manifest.failed_sentences),
            "analysis_complete": manifest.analysis_complete,
            "synthesis_complete": manifest.synthesis_complete,
            "progress": manifest.progress_str(),
        }


# 発話可能なテキストか判定（記号・括弧のみはスキップ）
_SPEAKABLE_RE = re.compile(r"[\w\u3040-\u9fff]")


def _has_speakable_text(text: str) -> bool:
    return bool(text and _SPEAKABLE_RE.search(text))
