(() => {
  "use strict";

  if (globalThis.__humeinaExtractorInstalled) {
    return;
  }
  globalThis.__humeinaExtractorInstalled = true;

  const BLOCK_TAGS = new Set([
    "ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "BR", "DD", "DIV", "DL", "DT",
    "FIGCAPTION", "FIGURE", "H1", "H2", "H3", "H4", "H5", "H6", "HR", "LI",
    "MAIN", "P", "PRE", "SECTION", "TABLE", "TD", "TH", "TR"
  ]);

  const REMOVE_SELECTORS = [
    "script", "style", "noscript", "template", "iframe", "canvas", "svg",
    "nav", "header", "footer", "aside", "form", "button", "input", "select", "textarea",
    "[hidden]", "[aria-hidden='true']", "[role='navigation']", "[role='banner']",
    "[role='contentinfo']", "[role='complementary']", "[role='dialog']",
    ".advertisement", ".ads", ".ad", "[class*='social']", "[class*='share']",
    "[class*='comment']", "[class*='related']", "[class*='recommend']",
    "[class*='breadcrumb']", "[class*='sidebar']", "[class*='cookie']",
    "[id*='comment']", "[id*='sidebar']"
  ];

  function normalizeText(value) {
    return String(value || "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n[ \t]+/g, "\n")
      .replace(/[ \t]{2,}/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function visibleTextLength(element) {
    const text = normalizeText(element.innerText || element.textContent);
    return text.length;
  }

  function candidateScore(element) {
    const length = visibleTextLength(element);
    if (length < 80) {
      return -1;
    }
    const paragraphs = Array.from(element.querySelectorAll("p"));
    const paragraphText = paragraphs.reduce((sum, node) => sum + visibleTextLength(node), 0);
    const links = Array.from(element.querySelectorAll("a"));
    const linkText = links.reduce((sum, node) => sum + visibleTextLength(node), 0);
    const linkPenalty = Math.min(length * 0.8, linkText * 1.6);
    const semanticBonus = element.matches("article, main, [role='main']") ? 1000 : 0;
    return semanticBonus + length + paragraphText * 0.45 - linkPenalty;
  }

  function findArticleRoot() {
    const selectors = [
      "article", "main", "[role='main']", ".article-body", ".article-content",
      ".entry-content", ".post-content", ".post-body", "#content", ".content"
    ];
    const candidates = new Set();
    for (const selector of selectors) {
      for (const node of document.querySelectorAll(selector)) {
        candidates.add(node);
      }
    }
    if (candidates.size === 0 && document.body) {
      return document.body;
    }
    return Array.from(candidates).sort((left, right) => candidateScore(right) - candidateScore(left))[0];
  }

  function textFromClone(root) {
    const clone = root.cloneNode(true);
    for (const selector of REMOVE_SELECTORS) {
      for (const node of clone.querySelectorAll(selector)) {
        node.remove();
      }
    }

    const chunks = [];
    function visit(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        chunks.push(node.nodeValue || "");
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) {
        return;
      }
      const tag = node.tagName;
      if (tag === "PRE" || tag === "CODE") {
        chunks.push("\nコード部分を省略。\n");
        return;
      }
      const block = BLOCK_TAGS.has(tag);
      if (block) {
        chunks.push("\n");
      }
      for (const child of node.childNodes) {
        visit(child);
      }
      if (block) {
        chunks.push("\n");
      }
    }
    visit(clone);
    return normalizeText(chunks.join(""));
  }

  function documentTitle() {
    const headline = document.querySelector("h1")?.textContent?.trim();
    const ogTitle = document.querySelector("meta[property='og:title']")?.content?.trim();
    return ogTitle || headline || document.title || "";
  }

  function extract(mode) {
    const selection = normalizeText(window.getSelection()?.toString());
    if ((mode === "smart" || mode === "selection") && selection) {
      return {
        text: selection,
        title: documentTitle(),
        url: location.href,
        sourceKind: "selection",
        characterCount: selection.length
      };
    }
    if (mode === "selection") {
      throw new Error("選択範囲がありません。");
    }
    const root = findArticleRoot();
    const text = root ? textFromClone(root) : "";
    return {
      text,
      title: documentTitle(),
      url: location.href,
      sourceKind: "article",
      characterCount: text.length
    };
  }

  browser.runtime.onMessage.addListener((message) => {
    if (message?.type === "humeina:extract") {
      return Promise.resolve(extract(message.mode || "smart"));
    }
    return undefined;
  });
})();
