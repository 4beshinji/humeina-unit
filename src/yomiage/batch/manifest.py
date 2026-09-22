"""Manifest data structures and JSON persistence for batch pipeline."""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from loguru import logger

from ..core.identity import source_fingerprint
from ..core.migration import PlanSerializer, UnknownSchemaVersionError, normalize_schema_version
from ..utils.atomic_io import atomic_write_json
from .paths import BatchPaths

CURRENT_MANIFEST_SCHEMA_VERSION = 3

COMPLETION_STATUSES = frozenset(
    {
        "pending",
        "complete",
        "complete_with_warnings",
        "partial",
        "failed",
    }
)


@dataclass
class ChapterMeta:
    """チャプターメタデータ."""

    index: int
    title: str
    url: str
    sentence_start: int  # このチャプターの最初のsentence index
    sentence_end: int  # このチャプターの最後のsentence index + 1


@dataclass
class SentenceEntry:
    """1文のエントリ."""

    index: int  # グローバル連番 (0001, 0002, ...)
    text: str
    chapter_index: int
    segment_type: str  # dialogue/narration/thought/scene_break
    speaker: str | None = None
    scene: str = "daily"
    emotion: str = "neutral"
    intensity: float = 0.5
    viewpoint_character: str | None = None
    tts_params: dict | None = None
    audio_file: str | None = None  # "0001.wav"
    duration: float | None = None  # WAV duration in seconds
    status: str = "pending"  # pending/synthesized/failed
    text_content: dict | None = None
    source_ref: dict | None = None
    speaker_origin: str = "default"
    unresolved_reason: str | None = None


@dataclass
class BatchManifest:
    """バッチパイプライン全体のマニフェスト."""

    work_id: str
    work_title: str
    source_url: str
    mode: str  # "voisona" or "voicevox"
    chapters: list[ChapterMeta] = field(default_factory=list)
    characters: dict[str, dict] = field(default_factory=dict)
    sentences: list[SentenceEntry] = field(default_factory=list)
    analysis_complete: bool = False
    synthesis_complete: bool = False
    source_documents: list[dict] = field(default_factory=list)
    # 旧manifestには存在しないため、load時はデフォルト値で後方互換にする。
    completion_status: str = "pending"
    # RUN-001 identity fields.  ``None`` denotes a legacy manifest and keeps its old path.
    schema_version: int = CURRENT_MANIFEST_SCHEMA_VERSION
    plan_id: str | None = None
    run_id: str | None = None
    job_id: str | None = None
    source_fingerprint: str | None = None
    config_fingerprint: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def output_dir(self, base_dir: Path) -> Path:
        """Return the run directory, or the legacy work directory when no run exists."""

        base_dir = Path(base_dir)
        if self.run_id:
            return BatchPaths.canonical(base_dir, self.work_id, self.run_id).run_dir
        return base_dir / self.work_id

    def plan_path(self, base_dir: Path) -> Path | None:
        """Return this manifest's canonical plan path, if it has a plan ID."""

        if not self.plan_id:
            return None
        return BatchPaths.for_plan(base_dir, self.work_id, self.plan_id).plan_path

    def save(self, base_dir: Path) -> Path:
        """マニフェストをJSONに保存."""
        base_dir = Path(base_dir)
        out_dir = self.output_dir(base_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "manifest.json"

        data = asdict(self)
        atomic_write_json(path, data)
        if self.run_id and self.plan_id:
            paths = BatchPaths.for_plan(
                base_dir,
                self.work_id,
                self.plan_id,
                run_id=self.run_id,
            )
            paths.plans_dir.mkdir(parents=True, exist_ok=True)
            # A plan excludes provider/run-specific paths so the same plan can be reused by
            # multiple providers and concurrent runs.
            plan_characters = {
                name: {
                    key: value
                    for key, value in character.items()
                    if key not in {"voice_id", "base_params", "provider", "provider_id"}
                }
                for name, character in self.characters.items()
            }
            plan_data = {
                "schema_version": self.schema_version,
                "plan_id": self.plan_id,
                "source_fingerprint": self.source_fingerprint
                or source_fingerprint(self.source_url),
                "config_fingerprint": self.config_fingerprint or "",
                "created_at": self.created_at,
                "work_id": self.work_id,
                "source_url": self.source_url,
                "work_title": self.work_title,
                "chapters": [asdict(chapter) for chapter in self.chapters],
                "source_documents": self.source_documents,
                "characters": plan_characters,
                "sentences": [asdict(sentence) for sentence in self.sentences],
            }
            # Plans are immutable identities.  A provider change or a concurrent run should not
            # replace the original analysis metadata with run-specific timestamps.
            if not paths.plan_path.exists():
                atomic_write_json(paths.plan_path, plan_data)
        logger.debug(f"Manifest saved: {path}")
        return path

    @classmethod
    def load(
        cls,
        base_dir: Path,
        work_id: str,
        run_id: str | None = None,
    ) -> "BatchManifest":
        """JSONからマニフェストを読み込み."""
        path = BatchPaths.find_manifest(base_dir, work_id, run_id=run_id)

        data = json.loads(path.read_text())
        return ManifestSchemaAdapter.from_dict(data)

    @classmethod
    def load_legacy(cls, base_dir: Path, work_id: str) -> "BatchManifest":
        """Explicitly load the historic ``output/{work_id}/manifest.json`` path."""

        path = BatchPaths.legacy(base_dir, work_id).legacy_manifest_path
        if not path.is_file():
            raise FileNotFoundError(f"Manifest not found: {path}")
        return ManifestSchemaAdapter.from_dict(json.loads(path.read_text()))

    @classmethod
    def load_for_url(cls, base_dir: Path, url: str) -> "BatchManifest":
        """Read a manifest addressed by URL, including the historic MD5 directory."""

        path = BatchPaths.find_manifest_for_url(base_dir, url)
        return ManifestSchemaAdapter.from_dict(json.loads(path.read_text()))

    @property
    def pending_sentences(self) -> list[SentenceEntry]:
        return [s for s in self.sentences if s.status == "pending"]

    @property
    def failed_sentences(self) -> list[SentenceEntry]:
        return [s for s in self.sentences if s.status == "failed"]

    @property
    def skipped_sentences(self) -> list[SentenceEntry]:
        """明示的にスキップされた文."""
        return [s for s in self.sentences if s.status == "skipped"]

    @property
    def unresolved_sentences(self) -> list[SentenceEntry]:
        """合成済み成果物として扱えない文.

        ``skipped`` は明示的な許可がある状態として扱う。それ以外の未知状態は
        将来の状態追加時に安全側へ倒すため未解決として扱う。
        """
        terminal = {"synthesized", "skipped"}
        return [s for s in self.sentences if s.status not in terminal]

    def missing_audio_files(self, base_dir: Path) -> list[SentenceEntry]:
        """合成済みと記録されたが実体が存在しない音声を返す."""
        work_dir = self.output_dir(base_dir)
        return [
            entry
            for entry in self.sentences
            if entry.status == "synthesized"
            and entry.audio_file
            and not (work_dir / entry.audio_file).is_file()
        ]

    def compute_completion_status(self, base_dir: Path | None = None) -> str:
        """manifestの内容から合成完了状態を一元判定する.

        ``pending``/``failed``/未知状態、またはmanifestが指す音声の欠損がある場合は
        ``complete`` を返さない。成果物が1件も確定していない場合は ``failed``、
        一部だけ確定している場合は ``partial`` とする。
        """
        unresolved = self.unresolved_sentences
        if base_dir is not None:
            unresolved = [*unresolved, *self.missing_audio_files(base_dir)]

        if unresolved:
            return "partial" if self.synthesized_count else "failed"
        if self.skipped_sentences:
            return "complete_with_warnings"
        if not self.sentences and not self.analysis_complete:
            return "pending"
        return "complete"

    def refresh_completion_status(self, base_dir: Path | None = None) -> str:
        """完了状態と旧互換の ``synthesis_complete`` を同期する."""
        status = self.compute_completion_status(base_dir)
        self.completion_status = status
        self.synthesis_complete = status in {"complete", "complete_with_warnings"}
        return status

    @property
    def synthesized_count(self) -> int:
        return sum(1 for s in self.sentences if s.status == "synthesized")

    @property
    def total_count(self) -> int:
        return len(self.sentences)

    def progress_str(self) -> str:
        return f"{self.synthesized_count}/{self.total_count}"

    def to_utterance_plan(
        self,
        *,
        config: object = None,
        merge_adjacent: bool = True,
    ):
        """旧 Batch の2-pass分析結果を共通 UtterancePlan へ変換する。

        import は遅延させ、manifest の読み書きだけを利用する既存 Batch 呼び出しが
        application 層を必須依存にしないようにする。
        """

        from ..application.planning import BatchPlanningAdapter

        return BatchPlanningAdapter().plan(
            self,
            config=config,
            merge_adjacent=merge_adjacent,
        )

    def save_plan(
        self,
        base_dir: Path,
        *,
        config: object = None,
        merge_adjacent: bool = True,
        overwrite: bool = False,
    ) -> Path:
        """旧 BatchManifest を新しい canonical UtterancePlan として保存する。

        これは明示的な import 操作であり、旧 ``manifest.json`` や音声成果物を変更しない。
        ``plan_id`` がない legacy manifest は入力と解析設定から新しい IDを計算する。
        """

        plan = self.to_utterance_plan(config=config, merge_adjacent=merge_adjacent)
        plan_id = self.plan_id or plan.plan_id
        path = BatchPaths.for_plan(base_dir, self.work_id, plan_id).plan_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if overwrite or not path.exists():
            atomic_write_json(path, plan.to_dict())
        return path

    @classmethod
    def load_plan(cls, base_dir: Path, work_id: str, plan_id: str):
        """canonical plan JSONを読み込む。future schemaはPlanSerializerが拒否する。"""

        path = BatchPaths.for_plan(base_dir, work_id, plan_id).plan_path
        if not path.is_file():
            raise FileNotFoundError(f"Plan not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, Mapping) and "utterances" not in data:
            # RUN-001 initially wrote Batch-shaped plan metadata.  Treat that shape as a
            # read-only compatibility input and import it through the same planner as a legacy
            # manifest; future versions still fail in ManifestSchemaAdapter rather than being
            # silently accepted.
            manifest = ManifestSchemaAdapter.from_dict(data)
            return manifest.to_utterance_plan()
        return PlanSerializer.loads(json.dumps(data, ensure_ascii=False))


class ManifestSchemaAdapter:
    """Read old and current BatchManifest JSON without rewriting legacy artifacts."""

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> BatchManifest:
        if not isinstance(data, Mapping):
            raise TypeError("manifest must be a mapping")
        values = dict(data)
        chapter_fields = {
            "index",
            "title",
            "url",
            "sentence_start",
            "sentence_end",
        }
        sentence_fields = {
            "index",
            "text",
            "chapter_index",
            "segment_type",
            "speaker",
            "scene",
            "emotion",
            "intensity",
            "viewpoint_character",
            "tts_params",
            "text_content", "source_ref", "speaker_origin", "unresolved_reason",
            "audio_file",
            "duration",
            "status",
        }
        raw_chapters = values.pop("chapters", [])
        raw_sentences = values.pop("sentences", [])
        if isinstance(raw_chapters, (str, bytes, bytearray)) or not isinstance(raw_chapters, list):
            raise TypeError("manifest chapters must be a list")
        if isinstance(raw_sentences, (str, bytes, bytearray)) or not isinstance(
            raw_sentences, list
        ):
            raise TypeError("manifest sentences must be a list")
        chapters = [
            ChapterMeta(**{key: value for key, value in chapter.items() if key in chapter_fields})
            for chapter in raw_chapters
        ]
        sentences = [
            SentenceEntry(
                **{key: value for key, value in sentence.items() if key in sentence_fields}
            )
            for sentence in raw_sentences
        ]

        # Old manifests had no schema/identity fields.  Keep them marked as schema 1 and leave
        # IDs unset so saving them continues to target the old path.
        is_legacy = not any(
            values.get(name)
            for name in ("plan_id", "run_id", "job_id", "source_fingerprint")
        )
        values.setdefault("schema_version", 1 if is_legacy else CURRENT_MANIFEST_SCHEMA_VERSION)
        try:
            schema_version = normalize_schema_version(values["schema_version"])
        except (TypeError, ValueError, UnknownSchemaVersionError) as exc:
            raise ValueError(
                f"unsupported manifest schema_version: {values['schema_version']!r}"
            ) from exc
        schema_key = tuple(int(part) for part in str(schema_version).split("."))
        if schema_key > (CURRENT_MANIFEST_SCHEMA_VERSION,):
            raise ValueError(f"unsupported manifest schema_version: {schema_version}")
        values["schema_version"] = schema_version
        values.setdefault("completion_status", "pending")
        values.setdefault("synthesis_complete", False)
        values.setdefault("analysis_complete", False)
        values.setdefault("characters", {})
        values.setdefault("work_title", "")
        values.setdefault("source_url", "")
        values.setdefault("mode", "voisona")
        values.setdefault("created_at", datetime.now(UTC).isoformat())
        # Unknown fields from future schemas must not silently alter the old dataclass contract.
        allowed = {
            "work_id",
            "work_title",
            "source_url",
            "mode",
            "analysis_complete",
            "source_documents",
            "synthesis_complete",
            "completion_status",
            "schema_version",
            "plan_id",
            "run_id",
            "job_id",
            "source_fingerprint",
            "config_fingerprint",
            "created_at",
            "characters",
        }
        values = {key: value for key, value in values.items() if key in allowed}
        values.setdefault("config_fingerprint", None)
        values.setdefault("plan_id", None)
        values.setdefault("run_id", None)
        values.setdefault("job_id", None)
        values.setdefault("source_fingerprint", None)
        manifest = BatchManifest(**values)
        manifest.chapters = chapters
        manifest.sentences = sentences
        return manifest

    from_legacy = from_dict
    adapt = from_dict

    @classmethod
    def load(
        cls,
        base_dir: Path,
        work_id: str,
        run_id: str | None = None,
    ) -> BatchManifest:
        """Adapter facade matching :meth:`BatchManifest.load`."""

        path = BatchPaths.find_manifest(base_dir, work_id, run_id=run_id)
        return cls.from_dict(json.loads(path.read_text()))

    @classmethod
    def load_for_url(cls, base_dir: Path, url: str) -> BatchManifest:
        path = BatchPaths.find_manifest_for_url(base_dir, url)
        return cls.from_dict(json.loads(path.read_text()))


# Migration code in later phases can import this shorter name without changing old callers.
ManifestAdapter = ManifestSchemaAdapter
