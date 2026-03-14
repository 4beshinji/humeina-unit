"""Translation via Ollama."""

from loguru import logger

from .ollama_client import OllamaClient

TRANSLATE_SYSTEM = """\
あなたは翻訳者です。原文の意味を正確に保ちつつ、自然な{target_lang}に翻訳してください。\
翻訳結果のみを出力してください。"""

LANG_NAMES = {
    "ja": "日本語",
    "en": "英語",
    "zh": "中国語",
    "ko": "韓国語",
}


class Translator:
    """Ollamaベースの翻訳."""

    def __init__(self, ollama: OllamaClient):
        self.ollama = ollama

    async def translate(
        self, text: str, target_lang: str = "ja", source_lang: str | None = None
    ) -> str:
        if not await self.ollama.is_available():
            logger.warning("Ollama unavailable for translation")
            return text

        target_name = LANG_NAMES.get(target_lang, target_lang)
        system = TRANSLATE_SYSTEM.format(target_lang=target_name)

        prompt = text
        if source_lang:
            source_name = LANG_NAMES.get(source_lang, source_lang)
            prompt = f"[{source_name}→{target_name}]\n{text}"

        try:
            return await self.ollama.generate(prompt, system=system)
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return text
