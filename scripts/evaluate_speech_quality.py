"""固定fixtureの構造・LLM要求を測定し、任意でVOICEVOXの盲検試聴素材を生成する。

.venv/bin/python scripts/evaluate_speech_quality.py --audio --output output/speech-quality
LLMはモックを使用する。実LLMの品質・費用や人の聴感を測った結果とは扱わない。
"""

from __future__ import annotations

import argparse
import asyncio
import html
import io
import json
import random
import re
import tempfile
import wave
from pathlib import Path
from time import perf_counter

from yomiage.application.planning import UtterancePlanner
from yomiage.application.speech_text import render_speech_text
from yomiage.application.synthesis import SynthesisRequest
from yomiage.bootstrap import create_synthesis_service, create_tts_provider
from yomiage.config import load_config
from yomiage.llm.budgeting import estimate_tokens
from yomiage.nlp.pipeline import NLPAnalyzer
from yomiage.nlp.text_processor import extract_html_document
from yomiage.settings import AnalysisSettings
from yomiage.tts.audio_utils import concat_wav_bytes
from yomiage.tts.cache import TTSCache

ROOT = Path(__file__).resolve().parents[1]


class CountingBackend:
    """応答欠落と保留を区別する、決定的なLLMモック。"""

    def __init__(self):
        self.requests = []

    def effective_output_limit(self, task, requested):
        return min(requested, 4096)

    async def generate_json(self, prompt, **kwargs):
        self.requests.append((prompt, kwargs))
        return [
            {"segment_id": int(i), "status": "deferred"}
            for i in re.findall(r"^\[(\d+)\]", prompt, re.M)
        ]


async def evaluate(output: Path, audio: bool):
    cases = json.loads((ROOT / "tests/fixtures/speech_quality/cases.json").read_text())
    baseline = json.loads(
        (ROOT / "tests/fixtures/speech_quality/before.json").read_text()
    )
    before = {c["id"]: c for c in baseline["cases"]}
    output.mkdir(parents=True, exist_ok=True)
    records = []
    audio_tasks = []
    for case in cases:
        value = extract_html_document(case["html"]) if "html" in case else case["text"]
        for policy in ("off", "selective", "full"):
            backend = CountingBackend()
            settings = AnalysisSettings(content_kind=case["kind"], llm_policy=policy)
            started = perf_counter()
            plan = await UtterancePlanner(
                NLPAnalyzer(llm=backend, analysis_settings=settings)
            ).from_text(value)
            analysis_seconds = perf_counter() - started
            spoken = "".join(u.synthesis_text for u in plan.utterances)
            speakers = [u.speaker_id for u in plan.utterances]
            record = {
                "case": case["id"],
                "policy": policy,
                "llm_backend": "mock_deferred",
                "utterances": [
                    {
                        "text": u.text,
                        "speaker": u.speaker_id,
                        "type": u.segment_type.value,
                        "synthesis_text": u.synthesis_text,
                        "original_range": [u.source_ref.start, u.source_ref.end],
                    }
                    for u in plan.utterances
                ],
                "reading_violations": int(spoken != case["spoken"]) if "spoken" in case else None,
                "speaker_violations": int(speakers != case["speakers"])
                if "speakers" in case
                else None,
                "requests": len(backend.requests),
                "estimated_input_tokens": sum(
                    estimate_tokens(p + k.get("system", "")) for p, k in backend.requests
                ),
                "requested_output_tokens": sum(k["max_tokens"] for _, k in backend.requests),
                "analysis_seconds_mock": analysis_seconds,
            }
            records.append(record)
            if policy == "off":
                audio_tasks.append(
                    (case["id"], "off", [render_speech_text(u) for u in plan.utterances])
                )
        audio_tasks.append((case["id"], "before", [before[case["id"]]["tts_text"]]))
    report = {
        "fixtures": len(cases),
        "conditions": records,
        "real_llm": "not_measured",
        "listening": "not_evaluated",
    }
    (output / "structure.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not audio:
        return
    config = load_config()
    voice = create_tts_provider(config, "voicevox")
    service = create_synthesis_service({"voicevox": voice}, primary="voicevox", timeout=60)
    random.Random(913).shuffle(audio_tasks)
    labels = []
    listening_rows = []
    try:
        for index, (case, condition, texts) in enumerate(audio_tasks):
            filename = f"sample-{index + 1:03d}.wav"
            with tempfile.TemporaryDirectory(prefix="speech-quality-cache-") as directory:
                service.cache = TTSCache(Path(directory))
                started = perf_counter()
                parts = []
                first_audio = None
                duration = 0.0
                for text in texts:
                    result = await service.synthesize(SynthesisRequest(text=text))
                    if first_audio is None:
                        first_audio = perf_counter() - started
                    parts.append(result.audio_data)
                    with wave.open(io.BytesIO(result.audio_data), "rb") as wav:
                        duration += wav.getnframes() / wav.getframerate()
                cold_seconds = perf_counter() - started
                (output / filename).write_bytes(
                    concat_wav_bytes(parts) if len(parts) > 1 else parts[0]
                )
                started = perf_counter()
                hits = 0
                for text in texts:
                    result = await service.synthesize(SynthesisRequest(text=text))
                    hits += int(result.cache_hit)
                labels.append(
                    {
                        "file": filename,
                        "case": case,
                        "condition": condition,
                        "provider": "voicevox",
                        "speaker": config["voicevox"].get("default_speaker"),
                        "first_audio_seconds": first_audio,
                        "cold_seconds": cold_seconds,
                        "warm_seconds": perf_counter() - started,
                        "warm_cache_hits": hits,
                        "utterances": len(texts),
                        "duration_seconds": duration,
                    }
                )
                listening_rows.append(
                    f"<tr><td>{index + 1}</td><td>{html.escape(case)}</td>"
                    f'<td><audio controls src="{filename}"></audio></td>'
                    '<td><input placeholder="誤読・不自然な変化・選好"></td></tr>'
                )
    finally:
        await service.close()
    (output / "audio-labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "index.html").write_text(
        '<!doctype html><html lang="ja"><meta charset="utf-8"><title>読み上げ比較試聴</title>'
        "<style>body{font:16px sans-serif;max-width:1100px;margin:2em auto}td{padding:1em}"
        "input{width:22em}audio{width:22em}</style><h1>読み上げ比較試聴</h1>"
        "<p>条件名を隠して順序を入れ替えています。同じcaseの音声を比較してください。"
        "読みの正しさ、話者、声色・速度の不要な変化、好みを記録してください。"
        "記入欄はブラウザーを閉じると消えます。判定は別途保存してください。</p>"
        "<table>" + "".join(listening_rows) + "</table></html>",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "output/speech-quality")
    args = parser.parse_args()
    asyncio.run(evaluate(args.output, args.audio))
