"""Slack message importance scoring."""

from loguru import logger

from ..nlp.ollama_client import OllamaClient
from .monitor import SlackMessage

SCORE_SYSTEM = """\
あなたはSlackメッセージの重要度判定器です。\
メッセージの重要度を0.0〜1.0で評価してください。\
0.0は雑談、1.0は即座に確認すべき重要メッセージです。\
数値のみ回答してください。"""


class ImportanceScorer:
    """Slackメッセージの重要度スコアリング."""

    def __init__(
        self,
        ollama: OllamaClient,
        mention_boost: float = 0.5,
        threshold: float = 0.6,
        priority_channels: list[str] | None = None,
    ):
        self.ollama = ollama
        self.mention_boost = mention_boost
        self.threshold = threshold
        self.priority_channels = priority_channels or []

    async def score(self, message: SlackMessage) -> float:
        """メッセージの重要度を0.0-1.0でスコアリング."""
        base_score = 0.0

        # ルールベース加点
        if message.is_mention:
            base_score += self.mention_boost
        if message.channel in self.priority_channels:
            base_score += 0.3
        if message.reaction_count > 5:
            base_score += 0.2

        # LLM補正
        llm_score = await self._llm_score(message)
        final_score = min(1.0, base_score + llm_score * 0.5)

        return final_score

    def is_important(self, score: float) -> bool:
        return score >= self.threshold

    async def _llm_score(self, message: SlackMessage) -> float:
        if not await self.ollama.is_available():
            return 0.3

        prompt = f"チャンネル: {message.channel}\nメッセージ: {message.text[:500]}\n\n重要度:"
        try:
            response = await self.ollama.generate(
                prompt, system=SCORE_SYSTEM, temperature=0.1, max_tokens=10
            )
            return float(response.strip().split()[0])
        except (ValueError, IndexError):
            return 0.3
        except Exception as e:
            logger.warning(f"LLM scoring failed: {e}")
            return 0.3
