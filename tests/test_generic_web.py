"""Tests for generic web content source."""

from bs4 import BeautifulSoup

from yomiage.sources.generic_web import GenericWebSource


def _make_source():
    return GenericWebSource()


def test_can_handle():
    assert GenericWebSource.can_handle("https://example.com/article")
    assert GenericWebSource.can_handle("http://example.com/page")
    assert not GenericWebSource.can_handle("ftp://example.com/file")
    assert not GenericWebSource.can_handle("/local/path")


def test_extract_title_og_priority():
    """og:title が h1 より優先される."""
    src = _make_source()
    soup = BeautifulSoup(
        '<html><head><meta property="og:title" content="OGタイトル"></head>'
        "<body><h1>サイト名</h1><p>本文</p></body></html>",
        "lxml",
    )
    assert src._extract_title(soup) == "OGタイトル"


def test_extract_title_h1_fallback():
    """og:title がない場合は h1 にフォールバック."""
    src = _make_source()
    soup = BeautifulSoup("<html><h1>記事タイトル</h1><p>本文</p></html>", "lxml")
    assert src._extract_title(soup) == "記事タイトル"


def test_extract_title_fallback():
    src = _make_source()
    soup = BeautifulSoup(
        "<html><head><title>ページタイトル</title></head><body></body></html>",
        "lxml",
    )
    assert src._extract_title(soup) == "ページタイトル"


def test_extract_title_none():
    src = _make_source()
    soup = BeautifulSoup("<html><body></body></html>", "lxml")
    assert src._extract_title(soup) == "（タイトル不明）"


def test_remove_noise():
    src = _make_source()
    html = """
    <html><body>
        <nav>ナビ</nav>
        <article>
            <p>本文テキスト</p>
            <aside>サイドバー</aside>
        </article>
        <footer>フッター</footer>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article")
    src._remove_noise(article)
    text = article.get_text(strip=True)
    assert "本文テキスト" in text
    assert "サイドバー" not in text


def test_replace_code_blocks():
    src = _make_source()
    html = """
    <div>
        <p>説明文</p>
        <pre><code>long code block that exceeds fifty characters
in total length for testing purposes here</code></pre>
        <pre>short</pre>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    container = soup.find("div")
    src._replace_code_blocks(container)
    text = container.get_text()
    assert "コードブロック省略" in text
    assert "short" in text
    assert "long code block" not in text


def test_linearize_small_table():
    src = _make_source()
    html = """
    <div>
        <table>
            <tr><th>名前</th><th>値</th></tr>
            <tr><td>速度</td><td>100</td></tr>
            <tr><td>容量</td><td>200</td></tr>
        </table>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    container = soup.find("div")
    src._linearize_tables(container)
    text = container.get_text()
    assert "名前は速度" in text
    assert "値は100" in text


def test_linearize_large_table():
    src = _make_source()
    rows = "<tr><th>A</th><th>B</th></tr>"
    for i in range(6):
        rows += f"<tr><td>x{i}</td><td>y{i}</td></tr>"
    html = f"<div><table>{rows}</table></div>"
    soup = BeautifulSoup(html, "lxml")
    container = soup.find("div")
    src._linearize_tables(container)
    text = container.get_text()
    assert "表省略" in text
    assert "7行" in text


def test_handle_headings():
    src = _make_source()
    html = "<div><h2>セクション1</h2><p>内容</p></div>"
    soup = BeautifulSoup(html, "lxml")
    container = soup.find("div")
    src._handle_headings(container)
    text = container.get_text()
    assert "セクション1" in text
    # h2 タグは除去されている
    assert container.find("h2") is None


def test_handle_lists():
    src = _make_source()
    html = "<div><ul><li>項目1</li><li>項目2。</li></ul></div>"
    soup = BeautifulSoup(html, "lxml")
    container = soup.find("div")
    src._handle_lists(container)
    text = container.get_text()
    assert "項目1。" in text
    # 既に句点があるものは二重にならない
    assert "項目2。。" not in text
    assert "項目2。" in text


def test_handle_ruby():
    src = _make_source()
    html = "<div><ruby><rb>漢字</rb><rp>(</rp><rt>かんじ</rt><rp>)</rp></ruby></div>"
    soup = BeautifulSoup(html, "lxml")
    container = soup.find("div")
    src._handle_ruby(container)
    text = container.get_text(strip=True)
    assert text == "漢字"


def test_clean_for_tts_integration():
    """統合テスト: 複雑なHTMLを通す."""
    src = _make_source()
    html = """
    <article>
        <h2>はじめに</h2>
        <p>Linuxカーネルの起動手順を解説します。</p>
        <pre><code>// This is a long code block that should be
replaced with a placeholder text for TTS output</code></pre>
        <h2>詳細</h2>
        <ul>
            <li>ステップ1の説明</li>
            <li>ステップ2の説明</li>
        </ul>
        <table>
            <tr><th>機能</th><th>説明</th></tr>
            <tr><td>MMU</td><td>メモリ管理</td></tr>
        </table>
        <aside>関連記事</aside>
    </article>
    """
    soup = BeautifulSoup(html, "lxml")
    container = soup.find("article")
    text = src._clean_for_tts(container)

    assert "はじめに" in text
    assert "Linuxカーネルの起動手順" in text
    assert "コードブロック省略" in text
    assert "ステップ1の説明" in text
    assert "機能はMMU" in text
    assert "関連記事" not in text


def test_extract_section_by_anchor():
    """#anchor 指定でそのセクションだけ抽出できる."""
    src = _make_source()
    html = """
    <html><body>
        <h1 id="intro">はじめに</h1><p>導入テキスト。</p>
        <h1 id="chapter1">第1章</h1><p>第1章の内容。</p>
        <h2 id="sec1-1">1.1節</h2><p>1.1節の内容。</p>
        <h1 id="chapter2">第2章</h1><p>第2章の内容。</p>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    title, text = src._extract_section(soup, "chapter1")
    assert title == "第1章"
    assert "第1章の内容" in text
    assert "1.1節の内容" in text
    assert "第2章の内容" not in text  # 同レベル見出しで打ち切り


def test_extract_section_not_found_fallbacks_to_full():
    """存在しないアンカーは全文にフォールバック."""
    src = _make_source()
    html = "<html><body><h1>タイトル</h1><p>本文テキスト。</p></body></html>"
    soup = BeautifulSoup(html, "lxml")
    _, text = src._extract_section(soup, "nonexistent")
    assert "本文テキスト" in text


def test_extract_article_bs4():
    """BS4 フォールバックの統合テスト."""
    src = _make_source()
    html = """
    <html>
    <body>
        <nav>メニュー</nav>
        <article>
            <p>記事の本文です。これは十分な長さのテキストです。</p>
        </article>
        <footer>フッター情報</footer>
    </body>
    </html>
    """
    soup = BeautifulSoup(html, "lxml")
    text = src._extract_article_bs4(soup)
    assert "記事の本文です" in text
    assert "メニュー" not in text
    assert "フッター情報" not in text
