"""モデルcontextと出力予算に基づく動的batch分割。"""

from __future__ import annotations

from dataclasses import dataclass


def estimate_tokens(text: str) -> int:
    """token count endpointがない場合の保守的な推定値。

    日本語は1文字を概ね1 token、ASCIIは4文字を1 tokenとして数え、
    JSON記号や混在文の余白を15%追加する。
    """

    japanese = sum(1 for char in text if ord(char) > 0x7F)
    ascii_chars = len(text) - japanese
    return max(1, int((japanese + (ascii_chars + 3) // 4) * 1.15) + 1)


@dataclass(frozen=True)
class TokenBudget:
    context_tokens: int = 32_768
    safety_ratio: float = 0.8
    max_items: int = 100

    def input_limit(self, *, output_tokens: int, overhead_tokens: int = 512) -> int:
        safe_context = int(self.context_tokens * self.safety_ratio)
        return max(1, safe_context - output_tokens - overhead_tokens)


def split_by_token_budget(
    items: list[str],
    budget: TokenBudget,
    *,
    output_tokens_per_item: int = 64,
    fixed_output_tokens: int = 128,
) -> list[list[str]]:
    """入力・予想出力・最大項目数の小さい制約でbatchを分割する。"""

    if not items:
        return []
    batches: list[list[str]] = []
    current: list[str] = []
    input_tokens = 0
    for item in items:
        item_tokens = estimate_tokens(item)
        prospective_count = len(current) + 1
        output_tokens = fixed_output_tokens + prospective_count * output_tokens_per_item
        limit = budget.input_limit(output_tokens=output_tokens)
        if current and (
            input_tokens + item_tokens > limit or prospective_count > budget.max_items
        ):
            batches.append(current)
            current = []
            input_tokens = 0
        current.append(item)
        input_tokens += item_tokens
    if current:
        batches.append(current)
    return batches
