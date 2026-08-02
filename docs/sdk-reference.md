# Python SDK リファレンス

公開 import は yomiage package から提供されます。SDK は FastAPI や CLI を起動せず、
同じ SynthesisService/UtterancePlan 境界をプロセス内で利用します。

## 公開型

主な公開型:

- TTSBridge: Provider を選択し、合成・file 出力・voice 一覧・health を行う facade
- TextAnalyzer: 前処理、rule-based classify、LLM 分析、UtterancePlan 生成
- Pipeline: 分析→parameter mapping→合成を組み合わせた facade
- SynthesisResult、VoiceInfo、AnalysisResult、PipelineChunk: 結果 DTO
- PipelineConfig、TTSEngineConfig、Settings と Provider/Server/Storage settings

詳細な型シグネチャは source の docstring と help(yomiage.TTSBridge) を正とします。

## TTSBridge

### 作成

```python
from yomiage import TTSBridge

bridge = TTSBridge.create(
    "voicevox",
    url="http://localhost:50021",
    default_voice="47",
)
```

利用可能な engine は voisona、voicevox、voicepeak です。fallback、cache、retry、metrics
を設定から組み立てる場合は TTSBridge.from_config() または composition root から生成した
service を TTSBridge.from_service() に渡します。Provider 固有 voice ID は意図モデルへ混入
させず、選択した Provider の metadata としてのみ渡されます。

### 合成と lifecycle

```python
import asyncio

from yomiage import TTSBridge


async def main() -> None:
    async with TTSBridge.create("voicevox", url="http://localhost:50021") as bridge:
        result = await bridge.synthesize(
            "こんにちは",
            voice_id="47",
            emotion="happy",
            intensity=0.7,
        )
        result.save("output/hello.wav")
        print(result.format, result.duration)

        long_result = await bridge.synthesize_long(
            "長い本文…",
            max_chars=200,
            pause_between_chunks=0.2,
        )
        long_result.save("output/long.wav")


asyncio.run(main())
```

synthesize_to_file(text, path, ...) は Provider の destination=file を含めた共通経路です。
VoiSona など audio bytes を直接返さない Provider も一時ファイルから SynthesisResult に変換
されます。list_voices()、is_available() と同期 wrapper（*_sync）も利用できます。
Bridge が所有する service/provider は close() または async context manager で一度だけ解放します。

## TextAnalyzer

```python
from yomiage import TextAnalyzer

analyzer = TextAnalyzer.rule_based(max_chunk_chars=200)
segments = analyzer.classify("太郎は「おはよう」と言った。")

for segment in segments:
    print(segment.segment_type, segment.speaker, segment.emotion)

plan = analyzer.plan_sync(
    "太郎は「おはよう」と言った。",
    source="example.txt",
)
```

LLM を使う場合は TextAnalyzer.create("ollama", url=..., model=...) または
TextAnalyzer.from_config(AnalyzerConfig(...)) を使います。analyze() は分類・話者・シーン・
感情を AnalysisResult の list で返し、plan()/create_plan() は共通 UtterancePlan を返します。
旧 analyze 結果は plan_results() で import できます。

## Pipeline

```python
import asyncio

from yomiage import Pipeline


async def main() -> None:
    async with Pipeline.create(
        "voicevox",
        url="http://localhost:50021",
    ) as pipeline:
        chunks = await pipeline.process("地の文。太郎は「こんにちは」と言った。")
        for chunk in chunks:
            chunk.audio.save(f"output/{chunk.analysis.segment_type}.wav")


asyncio.run(main())
```

Pipeline.process() は全結果を list、stream() は PipelineChunk の async iterator を返します。
各 chunk は dominant analysis（segment type/speaker/scene/emotion）と SynthesisResult を持ちます。
characters mapping を渡すと ParamMapper/VoiceProfile に反映されます。Provider が利用できない
場合は初期化時に rule-based 分析へ fallback しますが、合成自体は選択 Provider の availability
に依存します。

## 設定と安全な利用

### 型付き Settings

```python
from yomiage import PipelineConfig, Settings

settings = Settings.from_yaml(user_config_dir="~/.config/humeina-unit")
pipeline_config = PipelineConfig.from_settings(settings)
```

新規 application 境界では Settings を利用し、legacy Provider が必要な箇所だけ
settings.to_legacy_dict() adapter を通します。Provider secret は環境変数/SecretStr に置き、
ログや SynthesisResult metadata へコピーしません。

### URL と API

SDK の Provider URL は任意の private LAN endpoint を許可できますが、URL を取得する Reader/Batch/
API は共通 URLPolicy（loopback/private/link-local/reserved/private redirect を既定拒否）を通ります。
API を外部公開する場合は yomiage serve --host 0.0.0.0 --api-token ... とし、TLS、Firewall、
rate limit は運用層で設定してください。

## 互換性・成果物

SDK facade は legacy BatchManifest を直接公開しません。共通 plan は Provider/音声パスから
独立し、Batch の検証済み artifact は output/works/{work_id}/runs/{run_id}/ に保存されます。
旧 output/{work_id}/manifest.json の read-only import と rollback は [移行手順](migration.md)、
設計の根拠は [ADR 索引](adr/README.md) を参照してください。
