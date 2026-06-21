"""LLM クライアント間で共通するユーティリティ.

JSON 抽出・romanize プロンプトなど、複数バックエンドでコピーされていた
処理を 1 箇所に集約する.
"""

from __future__ import annotations

import json

from loguru import logger

ROMANIZE_SYSTEM_PROMPT = """\
あなたはテキスト変換ツールです。\
テキスト中のアルファベット（英単語・略語・固有名詞）を\
すべて正しい日本語の読み（カタカナ）に書き換えてください。\
略語も必ずカタカナにしてください\
（例: BBC→ビービーシー, AI→エーアイ, UK→ユーケー）。\
アルファベットが一文字も残らないようにしてください。\
それ以外の部分は一切変更しないでください。変換後のテキストのみ出力してください。"""


def extract_json(response: str) -> list | dict:
    """LLM レスポンスから JSON オブジェクトを抽出.

    - コードブロック（```json ... ```）に対応
    - 先頭の [ または { から解析を開始
    - パース失敗時は空 dict を返す
    """
    text = response.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]

    for i, c in enumerate(text):
        if c in ("[", "{"):
            text = text[i:]
            break

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(
            f"Failed to parse JSON from LLM: {e}\nResponse: {response[:200]}"
        )
        return {}
