"""Tests for MathProcessor."""

import pytest

from yomiage.nlp.math_processor import MathProcessor


@pytest.fixture
def mp():
    return MathProcessor()


# ──────────────────────────────────────────
# Unicode 記号変換
# ──────────────────────────────────────────

def test_unicode_composition(mp):
    result = mp.process_text("g ∘ f は合成射")
    assert "合成" in result
    assert "∘" not in result


def test_unicode_arrow(mp):
    result = mp.process_text("f : a → b")
    assert "から" in result
    assert "→" not in result


def test_unicode_forall(mp):
    result = mp.process_text("∀ x ∈ A")
    assert "任意の" in result
    assert "属する" in result


def test_unicode_bot(mp):
    result = mp.process_text("⊥ はボトム型")
    assert "ボトム" in result


# ──────────────────────────────────────────
# LaTeX ブロック変換
# ──────────────────────────────────────────

def test_inline_latex_composition(mp):
    result = mp.process_text(r"射 \(g \circ f\) を考える")
    assert "合成" in result
    assert r"\(" not in result


def test_display_math(mp):
    result = mp.process_text("$$F(g \\circ f) = F(g) \\circ F(f)$$")
    assert "合成" in result
    assert "$$" not in result


def test_latex_frac(mp):
    result = mp.process_text(r"\(\frac{a}{b}\)")
    assert "ぶんの" in result
    assert r"\frac" not in result


def test_latex_subscript(mp):
    result = mp.process_text(r"\(f \circ id_A = f\)")
    assert "合成" in result
    # 添字が変換されること
    assert r"id_A" not in result


def test_latex_sqrt(mp):
    result = mp.process_text(r"\(\sqrt{x}\)")
    assert "平方根" in result


def test_latex_greek(mp):
    result = mp.process_text(r"\(\lambda x . x\)")
    assert "ラムダ" in result


def test_latex_mathbf(mp):
    # \mathbf{Set} → "Set" （フォント修飾は除去、内容は残す）
    result = mp.process_text(r"\(\mathbf{Set}\)")
    assert "Set" in result or "セット" in result
    assert r"\mathbf" not in result


# ──────────────────────────────────────────
# HTML math span 変換
# ──────────────────────────────────────────

def test_html_math_inline_span(mp):
    html = '<p>射 <span class="math inline">\\(g \\circ f\\)</span> を考える。</p>'
    result = mp.process_html_math(html)
    assert "合成" in result
    assert r"\circ" not in result


def test_html_math_display_span(mp):
    # Pandoc は display math も <span class="math display"> を使う
    html = '<span class="math display">$$F(g \\circ f) = F(g) \\circ F(f)$$</span>'
    result = mp.process_html_math(html)
    assert "合成" in result


# ──────────────────────────────────────────
# 複雑な数式の辞書変換
# ──────────────────────────────────────────

def test_hom_set(mp):
    result = mp.process_text(r"\(\Hom(a, b)\)")
    assert "ホム集合" in result


def test_latex_to_arrow(mp):
    result = mp.process_text(r"\(f \to g\)")
    assert "から" in result


# ──────────────────────────────────────────
# Haskell コードブロック変換
# ──────────────────────────────────────────

def test_haskell_type_signature(mp):
    code = "fmap :: (a -> b) -> f a -> f b"
    result = mp.process_haskell_code(code)
    assert "の型は" in result
    assert "から" in result


def test_haskell_data_decl(mp):
    code = "data Maybe a = Nothing | Just a"
    result = mp.process_haskell_code(code)
    assert "または" in result


# ──────────────────────────────────────────
# 複雑かどうかの判定
# ──────────────────────────────────────────

def test_simple_not_complex(mp):
    assert not mp._is_complex(r"f \circ g")


def test_frac_is_complex(mp):
    assert mp._is_complex(r"\frac{a}{b}")


def test_sum_with_subscript_is_complex(mp):
    assert mp._is_complex(r"\sum_{i=0}^{n} x_i")
