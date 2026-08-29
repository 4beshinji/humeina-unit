# Ollama → llama.cpp 移行設計書

> **後継文書あり:** 本文書の専用`LlamaCppClient`案は採用せず、OpenCode Goの3 API形式対応と
> コスト最適化を含む[LLM基盤刷新・llama.cpp移行計画](llm-platform-migration-plan.md)へ
> 統合しました。以後の実装判断には後継文書を使用してください。

本ドキュメントは voisona-yomiage プロジェクト内の Ollama 依存箇所を llama.cpp（または同等のローカル LLM サーバ）へ置き換えるためのリファクタ計画です。**完全ローカルホスティング前提**で記述しています。

> **状態: 廃止済み（後継計画を実装済み）** — 本文は当時の調査記録であり、現在の
> CLI/APIの起動手順ではありません。以前存在した
> `nlp/translator.py` は削除済みで、翻訳専用モジュールを前提にしないでください。

本 PJ は既に `LLMBackend` というプロバイダ抽象を持っていますが、`OllamaClient` を直接利用している箇所も多く、**新しい `LlamaCppClient`（または既存の `OpenAIBackend` の URL 指定）への切替 + 直接利用箇所のリファクタ**が必要です。

---

## 1. 現状調査サマリ

### 1.1 利用箇所一覧

| # | ファイル | 行 | 用途 | クライアント | エンドポイント | モデル |
|---|---|---|---|---|---|---|
| 1 | `src/yomiage/nlp/ollama_client.py` | 全体 | Ollama 直接クライアント | `aiohttp` | `POST /api/chat`, `GET /api/tags` | `qwen3.5:9b` |
| 2 | `src/yomiage/nlp/llm_backend.py` | 71–113, 274–304 | `OllamaBackend` + factory | — | 上を委譲 | — |
| 3 | `src/yomiage/nlp/scene_analyzer.py` | 57–109 | シーン/感情解析（バッチ） | LLMBackend or OllamaClient | — | — |
| 4 | `src/yomiage/news/summarizer.py` | 89–137 | ニュース要約 | OllamaClient 直接 | `/api/chat` | — |
| 5 | `src/yomiage/news/urgency.py` | 14–55 | 緊急度スコアリング | OllamaClient 直接 | `/api/chat` | — |
| 6 | `src/yomiage/news/scheduler.py` | 29–106 | スケジューラ + romanize | OllamaClient 直接 | `/api/chat` | — |
| 7 | `src/yomiage/batch/analyzer.py` | 86–512 | バッチ解析（3 段 LLM、JSON 出力） | OllamaClient 直接 | `/api/chat` | — |
| 8 | `src/yomiage/batch/engine.py` | 36–42 | OllamaClient 初期化 | — | — | — |
| 9 | `src/yomiage/slack/scorer.py` | 17–57 | Slack 重要度スコアリング | OllamaClient 直接 | `/api/chat` | — |
| 10 | `src/yomiage/api/analyzer.py` | 41–109 | TextAnalyzer (LLMBackend factory) | — | — | デフォルト `"ollama"` |
| 11 | `src/yomiage/api/config.py` | 42–117 | LLMConfig (`backend="ollama"` デフォ) | — | — | — |
| 12 | `src/yomiage/api/pipeline.py` | 54–65 | バックエンド構築 | — | — | — |
| 13 | `src/yomiage/cli.py` | 54–106 | CLI 起動時 OllamaClient 生成 | — | — | `qwen3.5:9b` |
| 14 | `src/yomiage/server.py` | 25–65 | FastAPI 起動時 OllamaClient 生成 | — | — | `qwen3.5:9b` |
| 15 | `config/default.yaml` | 24–27 | `ollama:` 設定セクション | — | `${OLLAMA_URL}` | `qwen3.5:9b` / `gemma4:12b` |
| 16 | `.env.example` | 全体 | 環境変数 | — | `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_SUMMARY_MODEL` | — |
| 17 | `infra/docker-compose.yml` | 全体 | VOICEVOX のみ（Ollama 定義なし） | — | — | — |
| 18 | `docs/installation.md`, `architecture.md`, `configuration.md`, `README.md` | 各所 | セットアップ・設定説明 | — | — | — |

### 1.2 重要な前提

- **Ollama 固有 API は `/api/chat` と `/api/tags` のみ**（`/api/generate`、`/api/pull`、`/api/embed` は不使用）。
- 埋め込みなし、VLM なし。
- Docker Compose 上に Ollama サービスは無い（**ホストの Ollama を呼ぶ前提**）。これは llama.cpp 移行でも同様にホスト稼働 or compose 追加の選択肢あり。
- `LLMBackend` 抽象（`OllamaBackend` / `OpenAIBackend` / `AnthropicBackend`）は既にあり、`OpenAIBackend` は llama.cpp の `/v1/chat/completions` をそのまま叩ける構造。
- 一方で **多くのコンポーネント（news / batch / slack / cli / server）が `OllamaClient` を直接型付けで受けている**ため、llama.cpp 切替には型のリファクタが必要。
- 分析用のデフォルトは `qwen3.5:9b`、要約用のデフォルトは `gemma4:12b` に統一する。

---

## 2. 移行先候補比較（ローカルホスティング前提）

| 候補 | API 互換 | GPU | JSON モード | 推奨度（本 PJ） | 備考 |
|---|---|---|---|---|---|
| **llama.cpp `llama-server`** | OpenAI 互換 + 独自 | CUDA/ROCm/Vulkan/Metal | `response_format: json_object` 対応 | ★★★ | GGUF ネイティブ、最小依存 |
| **vLLM** | OpenAI 互換 | CUDA/ROCm | guided JSON / outlines 統合 | ★ | 単一ユーザ用途には過剰 |
| **SGLang** | OpenAI 互換 | CUDA/ROCm | DSL の constrained decoding 強い | ★★ | バッチ解析の構造化出力（生成→JSON）に有利 |
| **LocalAI** | OpenAI 互換 | llama.cpp | grammars 対応 | ★★ | 設定 1 つで動かしたいなら |
| **Cortex.cpp** | OpenAI 互換 | llama.cpp | — | ★ | Ollama UX クローン |
| **mistral.rs** | OpenAI 互換 | CUDA/Metal | grammars 対応 | ★ | Rust 単一バイナリ |
| **MLX-LM** | OpenAI 互換 | Apple Silicon | — | M シリーズ Mac 限定（ローカル開発に有用） | |

**第一候補**：`llama.cpp` の `llama-server`。本 PJ の用途（小〜中サイズモデル、CLI/API での単一ユーザ呼出、JSON 出力）と整合。

**サブ候補**：
- batch 解析の JSON 構造化精度を高めたい → **SGLang**（constrained decoding）または llama.cpp の GBNF grammar 利用
- Mac 開発者の手元で速く動かしたい → **MLX-LM** / **LM Studio**（OpenAI 互換）

---

## 3. ファイル別 移行手順

### 3.1 戦略

**段階移行**を推奨：

1. **フェーズ 1**：`OpenAIBackend` を活かし、外部設定で `LLM_API_URL=http://localhost:8080/v1` を指定して llama.cpp を呼ぶ。`OllamaClient` 直接利用箇所は残置（ホスト Ollama 互換のままでも当面動作）。
2. **フェーズ 2**：直接利用箇所を `LLMBackend` 抽象に書換。`OllamaClient` を非推奨化、最終的に削除。

このドキュメントでは両フェーズの作業を記述します。

### 3.2 フェーズ 1：抽象側の整備

#### 3.2.1 新規追加：`src/yomiage/nlp/llamacpp_client.py`

`OllamaClient` と同 IF（`generate`, `generate_json`, `is_available`, `romanize`）を持つ OpenAI 互換クライアントを新設：

```python
import json
import logging
import re
import aiohttp

logger = logging.getLogger(__name__)


class LlamaCppClient:
    """OpenAI 互換 (`/v1/chat/completions`) のローカル LLM クライアント。
    llama.cpp / vLLM / LocalAI などで動作。OllamaClient と互換 IF。"""

    def __init__(
        self,
        url: str = "http://localhost:8080/v1",
        model: str = "qwen2.5-3b-instruct",
        api_key: str = "EMPTY",
        fallback=None,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.fallback = fallback

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{self.url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as r:
                    r.raise_for_status()
                    data = await r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            if self.fallback is not None:
                logger.warning("LlamaCpp failed, fallback: %s", e)
                return await self.fallback.generate(
                    prompt, system, temperature=temperature, max_tokens=max_tokens
                )
            raise

    async def generate_json(self, prompt: str, system: str | None = None, **kw):
        text = await self.generate(prompt, system, **kw)
        return self._extract_json(text)

    async def is_available(self) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.url}/models", timeout=5) as r:
                    return r.status == 200
        except Exception:
            return False

    async def romanize(self, text: str) -> str:
        prompt = (
            "次の英字混じりの日本語のうち、英字部分のみカタカナに変換してください。"
            "他の文字はそのまま返してください。\n\n" + text
        )
        return (await self.generate(prompt, temperature=0.1, max_tokens=1024)).strip()

    @staticmethod
    def _extract_json(text: str):
        # OllamaClient._extract_json と同じ実装を移植
        ...
```

#### 3.2.2 `src/yomiage/nlp/llm_backend.py`

`OllamaBackend` の隣に `LlamaCppBackend` を追加し、`create_llm_backend` factory に `"llamacpp"` 分岐を追加：

```python
class LlamaCppBackend(LLMBackend):
    name = "llamacpp"
    def __init__(self, url, model, api_key="EMPTY"):
        self._client = LlamaCppClient(url=url, model=model, api_key=api_key)

    async def generate(self, prompt, system=None, **kw):
        return await self._client.generate(prompt, system, **kw)

    async def generate_json(self, prompt, system=None, **kw):
        return await self._client.generate_json(prompt, system, **kw)

    async def is_available(self):
        return await self._client.is_available()

    async def romanize(self, text):
        return await self._client.romanize(text)


def create_llm_backend(backend="llamacpp", *, url=None, api_key=None, model=None, **_):
    backend = (backend or "llamacpp").lower()
    if backend in ("llamacpp", "openai"):
        return LlamaCppBackend(
            url=url or "http://localhost:8080/v1",
            model=model or "qwen2.5-3b-instruct",
            api_key=api_key or "EMPTY",
        )
    if backend == "ollama":
        return OllamaBackend(
            url=url or "http://localhost:11434",
            model=model or "qwen3.5:9b",
        )
    if backend == "anthropic":
        return AnthropicBackend(api_key=api_key, model=model)
    raise ValueError(f"unknown backend: {backend}")
```

> NOTE: 既存の `OpenAIBackend` (lines 116–188) と統合する案もあるが、`LlamaCppBackend` として明示する方が呼び元での可読性が高い。`OpenAIBackend` を rename / 統合する場合は呼出先全置換でも可。

#### 3.2.3 `src/yomiage/api/config.py`

```python
class LLMConfig(BaseModel):
    backend: str = "llamacpp"  # was "ollama"
    url: str | None = None
    api_key: str | None = None
    model: str | None = None
```

YAML ローダ（108–117 行）も `llamacpp:` セクションを優先で読み、`ollama:` は後方互換のために残す。

#### 3.2.4 `config/default.yaml`

```diff
- ollama:
-   url: ${OLLAMA_URL:-http://localhost:11434}
-   model: ${OLLAMA_MODEL:-qwen3.5:9b}
-   summary_model: ${OLLAMA_SUMMARY_MODEL}
+ llamacpp:
+   url: ${LLM_API_URL:-http://localhost:8080/v1}
+   model: ${LLM_MODEL:-qwen2.5-7b-instruct}
+   summary_model: ${LLM_SUMMARY_MODEL}
```

#### 3.2.5 `.env.example`

```diff
- OLLAMA_URL=http://localhost:11434
- OLLAMA_MODEL=qwen3.5:9b
- OLLAMA_SUMMARY_MODEL=gemma4:12b
+ LLM_API_URL=http://localhost:8080/v1
+ LLM_MODEL=qwen2.5-7b-instruct
+ LLM_SUMMARY_MODEL=qwen2.5-14b-instruct
```

### 3.3 フェーズ 2：直接利用箇所の `LLMBackend` 化

`OllamaClient` を直接受けているクラスを `LLMBackend` 受けに書換。受入は緩和的に `LLMBackend | OllamaClient | LlamaCppClient` を一時受けして、auto-wrap してもよい。

#### 対象

| ファイル | 現在の引数 | 変更後 |
|---|---|---|
| `news/summarizer.py` (94) | `OllamaClient` | `LLMBackend` |
| `news/urgency.py` (17) | `OllamaClient` | `LLMBackend` |
| `news/scheduler.py` (29) | `OllamaClient` | `LLMBackend` |
| `batch/analyzer.py` (86) | `OllamaClient` | `LLMBackend` |
| `batch/engine.py` (36–42) | `OllamaClient` 生成 | `create_llm_backend()` 利用 |
| `slack/scorer.py` (17) | `OllamaClient` | `LLMBackend` |
| `cli.py` (54–106) | `OllamaClient` 生成 | `create_llm_backend()` 利用 |
| `server.py` (25–65) | `OllamaClient` 生成 | `create_llm_backend()` 利用 |

書換例：

```python
# news/scheduler.py (before)
def __init__(self, ollama: OllamaClient, ...):
    self.ollama = ollama
    self._summarizer = NewsSummarizer(ollama)
    self._urgency = UrgencyDetector(ollama, ...)

# after
def __init__(self, backend: LLMBackend, ...):
    self.backend = backend
    self._summarizer = NewsSummarizer(backend)
    self._urgency = UrgencyDetector(backend, ...)

async def _romanize_loop(self, ...):
    # await self.ollama.romanize(text)
    await self.backend.romanize(text)
```

`scene_analyzer.py` の `LLMBackend | OllamaClient` 受け（57–69 行）は既に互換実装。`LlamaCppClient` も同 IF なので、必要なら同様に auto-wrap 追加：

```python
if isinstance(backend, OllamaClient):
    self._backend = OllamaBackend.from_client(backend)
elif isinstance(backend, LlamaCppClient):
    self._backend = LlamaCppBackend.from_client(backend)
else:
    self._backend = backend
```

### 3.4 起動構成

#### 3.4.1 ホスト直接起動（既存運用継続パス）

```bash
# Linux + CUDA
docker run --rm --gpus all -p 8080:8080 \
  -v "$HOME/models:/models:ro" \
  ghcr.io/ggml-org/llama.cpp:server-cuda \
  --model /models/Qwen2.5-7B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 --n-gpu-layers 99 \
  --ctx-size 32768 --parallel 2 --cont-batching --flash-attn on
```

`docs/installation.md` の「Ollama インストール」節を「llama.cpp 起動」節に差し替え。

#### 3.4.2 docker-compose に追加する場合

```yaml
# infra/docker-compose.yml
services:
  voicevox: ...

  llm:
    image: ghcr.io/ggml-org/llama.cpp:server-cuda
    container_name: yomiage-llm
    ports: ["8080:8080"]
    volumes: ["./models:/models:ro"]
    command: >
      --model /models/Qwen2.5-7B-Instruct-Q4_K_M.gguf
      --host 0.0.0.0 --port 8080 --n-gpu-layers 99
      --ctx-size 32768 --parallel 2 --cont-batching
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]
```

### 3.5 ドキュメント更新

| ファイル | 修正内容 |
|---|---|
| `README.md` (43–44) | 必須依存を「llama.cpp サーバ + GGUF モデル」に更新 |
| `docs/installation.md` (29–47) | Ollama インストール手順を llama.cpp 起動手順 + GGUF 取得手順に置換 |
| `docs/architecture.md` (144–145) | 外部依存表で「Ollama」→「llama.cpp HTTP server」 |
| `docs/configuration.md` (47–54) | `ollama:` セクションを `llamacpp:` に |
| `docs/nlp-pipeline.md` | バックエンド名の表記更新 |

### 3.6 GGUF 取得手順

```bash
mkdir -p ~/models
huggingface-cli download bartowski/Qwen2.5-7B-Instruct-GGUF \
  Qwen2.5-7B-Instruct-Q4_K_M.gguf --local-dir ~/models

# サマリ用大きめモデル
huggingface-cli download bartowski/Qwen2.5-14B-Instruct-GGUF \
  Qwen2.5-14B-Instruct-Q4_K_M.gguf --local-dir ~/models
```

---

## 4. 移行リスクと検証項目

| リスク | 対応 |
|---|---|
| `OllamaClient` 直接利用箇所の型変更で広範囲影響 | `LLMBackend | OllamaClient | LlamaCppClient` の auto-wrap で段階移行 |
| バッチ解析の JSON 出力安定性 | `response_format: {"type": "json_object"}` 指定 + 既存の `_extract_json` を保持 |
| `romanize` のクオリティ差 | qwen2.5 系で同等以上の出力か検証 |
| `OLLAMA_SUMMARY_MODEL` の役割 | 別モデル常駐サービス (`llm-summary`) として併設可 |
| ホスト稼働 → docker-compose 統合の運用変化 | `docs/installation.md` で両パスを案内 |
| デフォルトモデル名の表記揺れ | 分析用 `qwen3.5:9b` / 要約用 `gemma4:12b` で統一 |

検証：

```bash
# 起動
docker run --rm --gpus all -p 8080:8080 -v ~/models:/models:ro \
  ghcr.io/ggml-org/llama.cpp:server-cuda \
  --model /models/Qwen2.5-7B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 --n-gpu-layers 99

# 動作確認
curl http://localhost:8080/v1/models
pytest tests/
```

---

## 5. 移行ステップ（推奨順序）

1. `LlamaCppClient` / `LlamaCppBackend` 新設、ユニットテスト
2. `create_llm_backend` factory に `"llamacpp"` 分岐追加、デフォルト変更
3. `LLMConfig` のデフォルトを `"llamacpp"` に
4. `config/default.yaml` / `.env.example` を更新（`ollama:` セクションは後方互換で残置）
5. `cli.py` / `server.py` の起動コードを `create_llm_backend()` 利用に書換
6. `news/*` / `batch/*` / `slack/*` を `LLMBackend` 受けに書換
7. `scene_analyzer.py` の auto-wrap に `LlamaCppClient` 追加
8. ドキュメント (`installation.md`, `architecture.md`, `configuration.md`, `README.md`) 更新
9. `OllamaClient` を非推奨マーク → 次メジャーで削除
