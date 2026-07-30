# システムアーキテクチャ

`humeina-unit` は、CLI・FastAPI・Python SDK が同じ application service と成果物境界を使う
モジュラーモノリスです。Provider ごとの常駐サービスをこのリポジトリ内で分割運用する構成では
ありません。

## 実行時の依存方向

```text
interfaces (Typer CLI / FastAPI composition root)
        ↓
application (planning / synthesis / jobs / health)
        ↓
core (UtterancePlan / identity / job state)
        ↕
ports (TTS / source / repository / artifact contracts)
        ↑
adapters (filesystem / SQLite / legacy TTS)

legacy feature packages (nlp, reader, batch, tts, sources, studio, video, news, slack)
    └─ composition root から ports/application の互換実装として利用
```

新しい `core` と `application` は FastAPI、Typer、具象 TTS Provider を import しません。
依存方向の契約は [`tests/test_architecture.py`](../tests/test_architecture.py) で検査します。
ADR の背景と移行タスクは [ADR 索引](adr/README.md) と
[改善作業台帳](improvement/work-ledger.md) を参照してください。

## 実装パッケージ（2026-07-30 の source tree）

以下は `src/yomiage/` の実ファイルに対応するパッケージ一覧です。`__pycache__` は含めません。

```text
src/yomiage/
├── __init__.py              # 公開 SDK facade
├── bootstrap.py             # composition root
├── cli.py                   # Typer app の互換 facade
├── config.py                # YAML + 環境変数の legacy loader
├── resource_loader.py       # wheel 内標準 YAML
├── settings.py              # 型付き Settings とパス解決
├── server.py                # FastAPI app factory
├── adapters/
│   ├── filesystem.py
│   ├── persistence/{filesystem.py,sqlite.py}
│   └── tts/legacy.py
├── api/                     # SDK facade / DTO / metrics / hooks
│   ├── analyzer.py, bridge.py, pipeline.py
│   ├── config.py, models.py, audio_format.py, long_text.py
│   ├── exceptions.py, hooks.py, metrics.py, profile_resolver.py
├── application/             # use cases
│   ├── health.py, jobs.py, planning.py, synthesis.py
├── core/                     # domain models and migration serializers
│   ├── identity.py, jobs.py, migration.py, models.py, plan.py
├── ports/                    # provider/source/repository/artifact contracts
│   ├── artifacts.py, repositories.py, sources.py, tts.py
├── interfaces/cli/            # command modules
│   ├── batch.py, common.py, composition.py, news.py, reader.py
│   ├── server.py, slack.py, studio.py, tuning.py
├── batch/                    # BatchEngine and legacy manifest adapter
│   ├── analyzer.py, concatenator.py, engine.py, identity.py
│   ├── job_manager.py, manifest.py, manifest_adapter.py, paths.py
│   ├── synthesizer.py, voisona_synth.py, voicevox_synth.py, voicepeak_synth.py
├── nlp/                      # text/classifier/speaker/scene/LLM adapters
│   ├── classifier.py, gemini_client.py, legacy.py, llm_backend.py, llm_utils.py
│   ├── math_processor.py, ollama_client.py, pipeline.py, scene_analyzer.py
│   ├── speaker.py, splitter.py, text_processor.py
├── reader/                   # realtime engine and local persistence
│   ├── engine.py, bookmark.py, character_db.py, param_mapper.py, playback.py
├── tts/                      # provider clients and shared manager
│   ├── base.py, cache.py, factory.py, manager.py, retry.py, session.py
│   ├── audio_utils.py, playback.py, voisona.py, voicevox.py, voicepeak.py
├── sources/                  # URL policy/fetcher and site adapters
│   ├── base.py, fetcher.py, http.py, registry.py
│   ├── aozora.py, narou.py, kakuyomu.py, generic_web.py
├── exvoice/                  # optional fixed WAV insertion
│   ├── catalog.py, manager.py, selector.py
├── studio/                   # script → audio/YMM4/plain artifacts
│   ├── engine.py, models.py, naming.py, output.py, script_parser.py
│   ├── cache.py, synthesizer.py
├── video/                    # optional ffmpeg composition
│   ├── asset_manager.py, audio_mixer.py, composer.py, config.py
│   ├── frame_builder.py, subtitle.py, timeline.py
├── news/                     # RSS fetch, summary, urgency, scheduler
│   ├── fetcher.py, summarizer.py, urgency.py, scheduler.py
├── slack/                    # optional Slack monitor/scorer
│   ├── monitor.py, scorer.py
├── tools/                    # profile/tuning helpers
│   ├── tuner.py, voice_profile.py, voicevox_profile.py
│   ├── voicepeak_profile.py, voicevox_tuner.py, voicepeak_tuner.py
└── utils/atomic_io.py        # atomic JSON/WAV publication helpers
```

標準設定リソースは `src/yomiage/resources/config/{default,voices,scene_params,math_dict}.yaml`
です。編集可能な profile は package resource に含めず、ユーザー設定ディレクトリから読みます。

## データフロー

### Reader / API

```text
URL or text
  → sources.URLPolicy + ContentSource
  → nlp.TextProcessor / classifier / speaker / SceneAnalyzer
  → application.planning.UtterancePlanner
  → core.UtterancePlan (speaker/scene/emotion/VoiceIntent)
  → application.synthesis.SynthesisService
  → ports.tts.Provider adapter
  → verified ArtifactStore or playback
```

API の `/read` は background job として Reader を起動します。SDK の `TextAnalyzer.plan()` と
`Pipeline` も同じ発話境界モデルへ変換します。

### Batch

```text
URL
  → BatchEngine.analyze()              # plan + run identity を付与
  → output/works/{work}/plans/{plan}.json
  → BatchEngine.synthesize()            # provider → atomic publish/verify
  → output/works/{work}/runs/{run}/artifact-manifest.json
  → concat (欠損時は拒否、明示 allow_partial のみ partial)
  → 任意の subtitle/video (partial では video を作らない)
```

旧 `output/{work_id}/manifest.json` は `ManifestSchemaAdapter` で read-only 読み込みできます。
移行方法は [移行手順](migration.md) を参照してください。

## Canonical identity と永続化

| ID | 役割 | 保存例 |
| --- | --- | --- |
| `work_id` | 正規化した作品 identity | `output/works/{work_id}/` |
| `plan_id` | source/config fingerprint による解析計画 | `plans/{plan_id}.json` |
| `run_id` | Provider/format ごとの実行 | `runs/{run_id}/` |
| `job_id` | CLI/API 受付ジョブ | SQLite `jobs.sqlite3` と API DTO |

run ごとの `manifest.json` は発話状態を、`artifact-manifest.json` は checksum、duration、
provider、検証済み path を保持します。未検証 WAV は final artifact として列挙しません。
ジョブ状態と compare-and-set 遷移は SQLite repository に保存されます（[ADR-0003](adr/ADR-0003-sqlite-job-repository.md)）。

## 外部依存と境界

| 依存 | 用途 | 既定接続先/実体 | 備考 |
| --- | --- | --- | --- |
| VoiSona Talk | 音声合成 | `http://localhost:32766` | Windows VM の場合あり。Basic 認証/virtiofs は任意 |
| VOICEVOX Engine | 音声合成 | `http://localhost:50021` | Docker または別プロセス |
| VOICEPEAK | 音声合成 | ローカル executable | ライセンスと path が必要 |
| Ollama | 分析/要約 | `http://localhost:11434` | ルールベースのみなら省略可能 |
| Gemini API | LLM fallback | API key 指定時のみ | `news` の `--gemini-key` または env |
| ffmpeg | concat/video/format | executable `ffmpeg` | Batch 結合・動画時に必要 |
| Slack API | 任意監視 | token 指定時のみ | `slack-sdk` extra が必要 |

内部 HTTP API は `yomiage serve` が提供します。既定 bind は loopback (`127.0.0.1:8030`) で、
非 loopback bind には `--api-token`/`YOMIAGE_API_TOKEN` が必要です。詳しい制限と Bearer 認証は
[REST API リファレンス](api-reference.md)を参照してください。

HEMS/MQTT、SPICE 再生、未実装の翻訳モジュールは現行アーキテクチャの構成要素ではありません。
