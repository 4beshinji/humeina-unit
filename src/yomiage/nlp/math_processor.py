"""数式・数学記号のTTS向け日本語変換プロセッサ."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from loguru import logger

if TYPE_CHECKING:
    from .llm_backend import LLMBackend

# デフォルト辞書パス
_DEFAULT_DICT = Path(__file__).parent.parent.parent.parent / "config" / "math_dict.yaml"

# LaTeX ブロックの正規表現
_INLINE_MATH_RE = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_SINGLE_DOLLAR_RE = re.compile(r"(?<!\$)\$([^$\n]+?)\$(?!\$)")

# Pandoc が出力する math span の正規表現（HTML前処理用）
_MATH_SPAN_RE = re.compile(
    r'<span\s+class="math\s+(inline|display)"[^>]*>(.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)

# LaTeX コマンド: \command{arg} または \command
_LATEX_CMD_RE = re.compile(r"\\([a-zA-Z]+)\{([^}]*)\}|\\([a-zA-Z]+)")

# 上付き・下付き
_SUBSCRIPT_RE = re.compile(r"_\{([^}]+)\}")
_SUBSCRIPT_SINGLE_RE = re.compile(r"_([a-zA-Z0-9])")
_SUPERSCRIPT_RE = re.compile(r"\^\{([^}]+)\}")
_SUPERSCRIPT_SINGLE_RE = re.compile(r"\^([a-zA-Z0-9])")

# 複雑な数式の判定（LLMフォールバック用）
_COMPLEX_LATEX_RE: list[re.Pattern] = []  # 辞書ロード時に構築


class MathProcessor:
    """数式をTTS向け日本語テキストに変換する.

    1. HTML の Pandoc math span を前処理（process_html_math）
    2. LaTeX ブロック \\(...\\) / $$...$$ を変換（process_latex）
    3. Unicode 数学記号を辞書置換（process_unicode_symbols）
    4. 複雑な数式は LLM にフォールバック（async 版のみ）
    """

    def __init__(
        self,
        dict_path: Path | None = None,
        llm: LLMBackend | None = None,
    ) -> None:
        self._llm = llm
        self._dict = self._load_dict(dict_path or _DEFAULT_DICT)
        self._build_complex_patterns()

    # ──────────────────────────────────────────
    # パブリック API
    # ──────────────────────────────────────────

    def process_html_math(self, html: str) -> str:
        """HTML中の Pandoc math span を変換してから返す.

        generic_web.py の BS4 処理前に呼ぶことで、
        LaTeX が素のテキストとして残るのを防ぐ。
        """
        def replace_span(m: re.Match) -> str:
            kind = m.group(1).lower()  # "inline" or "display"
            latex = m.group(2).strip()
            display = kind == "display"
            reading = self._process_latex_sync(latex, display=display)
            if display:
                return f"\n{reading}\n"
            return reading

        return _MATH_SPAN_RE.sub(replace_span, html)

    def process_text(self, text: str) -> str:
        """テキスト中の数式・記号を辞書ベースで変換（同期）."""
        text = self._process_latex_blocks_sync(text)
        text = self._apply_bare_latex_commands(text)
        text = self._apply_unicode_dict(text)
        return text

    async def process_text_async(self, text: str) -> str:
        """テキスト中の数式・記号を変換（LLMフォールバックあり）."""
        text = await self._process_latex_blocks_async(text)
        text = self._apply_unicode_dict(text)
        return text

    def process_haskell_code(self, code: str) -> str:
        """Haskellコードブロックを読み上げ用テキストに変換."""
        ops = self._dict.get("haskell_operators", {})
        # 型シグネチャのみ処理（関数名 :: 型 の形式）
        lines = []
        for line in code.splitlines():
            line = line.strip()
            if not line:
                continue
            # 型シグネチャ行
            if "::" in line:
                line = self._convert_haskell_signature(line, ops)
                lines.append(line)
            # data/type 宣言
            elif line.startswith(("data ", "type ", "newtype ")):
                line = self._convert_haskell_decl(line, ops)
                lines.append(line)
            # それ以外は省略
        return "。".join(lines) + "。" if lines else ""

    # ──────────────────────────────────────────
    # 内部: LaTeX ブロック処理
    # ──────────────────────────────────────────

    def _process_latex_blocks_sync(self, text: str) -> str:
        text = _DISPLAY_MATH_RE.sub(
            lambda m: f"\n{self._process_latex_sync(m.group(1), display=True)}\n",
            text,
        )
        text = _INLINE_MATH_RE.sub(
            lambda m: self._process_latex_sync(m.group(1)),
            text,
        )
        text = _SINGLE_DOLLAR_RE.sub(
            lambda m: self._process_latex_sync(m.group(1)),
            text,
        )
        return text

    async def _process_latex_blocks_async(self, text: str) -> str:
        """LaTeXブロックを非同期変換（LLMフォールバックあり）."""
        # display math
        matches = list(_DISPLAY_MATH_RE.finditer(text))
        for m in reversed(matches):
            reading = await self._process_latex_async(m.group(1), display=True)
            text = text[: m.start()] + f"\n{reading}\n" + text[m.end() :]

        # inline math
        matches = list(_INLINE_MATH_RE.finditer(text))
        for m in reversed(matches):
            reading = await self._process_latex_async(m.group(1))
            text = text[: m.start()] + reading + text[m.end() :]

        matches = list(_SINGLE_DOLLAR_RE.finditer(text))
        for m in reversed(matches):
            reading = await self._process_latex_async(m.group(1))
            text = text[: m.start()] + reading + text[m.end() :]

        return text

    def _process_latex_sync(self, latex: str, *, display: bool = False) -> str:
        """単一LaTeX式を辞書ベースで変換."""
        try:
            return self._convert_latex(latex.strip())
        except Exception as e:
            logger.debug(f"LaTeX conversion failed for '{latex[:40]}': {e}")
            return latex

    async def _process_latex_async(
        self, latex: str, *, display: bool = False
    ) -> str:
        """単一LaTeX式を変換。複雑なものはLLMに委譲。"""
        latex = latex.strip()
        if self._is_complex(latex) and self._llm:
            try:
                return await self._llm_convert_latex(latex)
            except Exception as e:
                logger.warning(f"LLM math conversion failed: {e}")
        return self._process_latex_sync(latex, display=display)

    # ──────────────────────────────────────────
    # 内部: LaTeX 変換ロジック
    # ──────────────────────────────────────────

    def _convert_latex(self, latex: str) -> str:
        """LaTeX式をテキストに変換（辞書＋ルールベース）."""
        cmds = self._dict.get("latex_commands", {})
        result = latex

        # \frac{a}{b} → aのbぶんの
        result = re.sub(
            r"\\frac\{([^}]*)\}\{([^}]*)\}",
            lambda m: f"{self._convert_latex(m.group(1))}の"
                      f"{self._convert_latex(m.group(2))}ぶんの",
            result,
        )

        # \sqrt{a} → aの平方根
        result = re.sub(
            r"\\sqrt\{([^}]*)\}",
            lambda m: f"{self._convert_latex(m.group(1))}の平方根",
            result,
        )

        # \sqrt[n]{a} → aのn乗根
        result = re.sub(
            r"\\sqrt\[([^\]]+)\]\{([^}]*)\}",
            lambda m: f"{self._convert_latex(m.group(2))}の"
                      f"{self._convert_latex(m.group(1))}乗根",
            result,
        )

        # \mathbf{X}, \mathcal{C} などフォント修飾（ラベルを残す）
        result = re.sub(
            r"\\math(?:bf|bb|cal|it|rm|sf|tt)\{([^}]*)\}",
            lambda m: m.group(1),
            result,
        )

        # \text{...} → そのまま
        result = re.sub(r"\\(?:text|textrm|textit|textbf)\{([^}]*)\}", r"\1", result)

        # \hat{a} → aハット
        result = re.sub(
            r"\\(?:hat|widehat)\{([^}]*)\}",
            lambda m: f"{self._convert_latex(m.group(1))}ハット",
            result,
        )

        # \bar{a} → aバー
        result = re.sub(
            r"\\(?:bar|overline)\{([^}]*)\}",
            lambda m: f"{self._convert_latex(m.group(1))}バー",
            result,
        )

        # \tilde{a} → aチルダ
        result = re.sub(
            r"\\(?:tilde|widetilde)\{([^}]*)\}",
            lambda m: f"{self._convert_latex(m.group(1))}チルダ",
            result,
        )

        # \vec{a} → aベクトル
        result = re.sub(
            r"\\vec\{([^}]*)\}",
            lambda m: f"{self._convert_latex(m.group(1))}ベクトル",
            result,
        )

        # a^{n} → aのn乗 / a^n → aのn乗
        result = _SUPERSCRIPT_RE.sub(
            lambda m: f"の{self._convert_latex(m.group(1))}乗", result
        )
        result = _SUPERSCRIPT_SINGLE_RE.sub(
            lambda m: f"の{m.group(1)}乗", result
        )

        # a_{i} → aのi / a_i → aのi
        result = _SUBSCRIPT_RE.sub(
            lambda m: f"の{self._convert_latex(m.group(1))}", result
        )
        result = _SUBSCRIPT_SINGLE_RE.sub(
            lambda m: f"の{m.group(1)}", result
        )

        # \command{arg} → コマンド読み + arg
        def replace_cmd_with_arg(m: re.Match) -> str:
            cmd = f"\\{m.group(1)}"
            arg = m.group(2)
            reading = cmds.get(cmd, "")
            if reading:
                return f"{reading}{self._convert_latex(arg)}"
            return self._convert_latex(arg)  # 不明コマンドはargを保持

        result = re.sub(r"\\([a-zA-Z]+)\{([^}]*)\}", replace_cmd_with_arg, result)

        # \command（引数なし）
        def replace_cmd(m: re.Match) -> str:
            cmd = f"\\{m.group(1)}"
            return cmds.get(cmd, m.group(0))

        result = re.sub(r"\\([a-zA-Z]+)", replace_cmd, result)

        # 残った LaTeX 制御文字を除去
        result = re.sub(r"[{}]", "", result)
        result = result.strip()
        return result

    # ──────────────────────────────────────────
    # 内部: Unicode 記号変換
    # ──────────────────────────────────────────

    def _apply_bare_latex_commands(self, text: str) -> str:
        """デリミタなしで残った \\command を辞書置換.

        Pandocがaccessibility用に生LaTeXを本文テキストに残す場合に対処する。
        例: "h \\circ g" → "h 合成 g"
        フォント修飾系（\\mathbf等）と空白系は除去のみ。
        """
        cmds = self._dict.get("latex_commands", {})

        def replace(m: re.Match) -> str:
            cmd = f"\\{m.group(1)}"
            reading = cmds.get(cmd)
            if reading is None:
                return m.group(0)  # 未知コマンドは残す
            return f" {reading} " if reading else ""

        text = re.sub(r"\\([a-zA-Z]+)", replace, text)
        # 残った単独の { } を除去
        text = re.sub(r"[{}]", "", text)
        return text

    def _apply_unicode_dict(self, text: str) -> str:
        syms = self._dict.get("unicode_symbols", {})
        for sym, reading in syms.items():
            if sym in text:
                text = text.replace(sym, reading)
        return text

    # ──────────────────────────────────────────
    # 内部: Haskell 変換
    # ──────────────────────────────────────────

    def _convert_haskell_signature(self, line: str, ops: dict) -> str:
        """型シグネチャ行を読み上げ用テキストに変換."""
        # 例: "fmap :: (a -> b) -> f a -> f b"
        # → "ファンクターマップの型はaからbへの関数からエフaへのエフbへの関数"
        result = line
        for op, reading in ops.items():
            if op == "\\":
                continue  # ラムダはそのまま
            result = result.replace(op, f" {reading} ")
        # 余分なスペース整理
        result = re.sub(r"\s+", " ", result).strip()
        return result

    def _convert_haskell_decl(self, line: str, ops: dict) -> str:
        """data/type 宣言を読み上げ用テキストに変換."""
        result = line
        for op, reading in ops.items():
            if op in ("::", "\\"):
                continue
            result = result.replace(op, f" {reading} ")
        result = re.sub(r"\s+", " ", result).strip()
        return result

    # ──────────────────────────────────────────
    # 内部: LLM フォールバック
    # ──────────────────────────────────────────

    async def _llm_convert_latex(self, latex: str) -> str:
        """LLMを使ってLaTeX式を日本語読み上げテキストに変換."""
        assert self._llm is not None
        prompt = (
            f"次のLaTeX数式を、日本語の音声読み上げ用テキストに変換してください。\n"
            f"数式: {latex}\n\n"
            f"変換後のテキストのみを出力してください。"
            f"例: `f \\circ g` → `エフ合成ジー`、"
            f"`\\frac{{a}}{{b}}` → `aのbぶんの`"
        )
        result = await self._llm.generate(
            prompt,
            system=(
                "あなたは数学のテキスト変換ツールです。"
                "LaTeX数式を日本語の音声読み上げに適した自然なテキストに変換します。"
                "記号・式の意味を保ちながら、読み上げて意味が通るように変換してください。"
                "変換後のテキストのみを出力し、説明や補足は不要です。"
            ),
            temperature=0.1,
            max_tokens=256,
        )
        return result.strip()

    # ──────────────────────────────────────────
    # 内部: ユーティリティ
    # ──────────────────────────────────────────

    def _is_complex(self, latex: str) -> bool:
        """LLMフォールバックが必要な複雑な数式か判定."""
        return any(p.search(latex) for p in _COMPLEX_LATEX_RE)

    def _build_complex_patterns(self) -> None:
        global _COMPLEX_LATEX_RE
        patterns = self._dict.get("llm_trigger_patterns", [])
        _COMPLEX_LATEX_RE = [re.compile(p) for p in patterns]

    @staticmethod
    def _load_dict(path: Path) -> dict:
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning(f"Math dictionary not found: {path}")
            return {}
        except Exception as e:
            logger.error(f"Failed to load math dictionary: {e}")
            return {}
