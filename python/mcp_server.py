#!/usr/bin/env python3
"""
Knowledge Base LLM MCP Server (FastMCP / stdio)

llama-cpp (Gemma3) をラップした MCP サーバー。
TypeScript 側から StdioClientTransport 経由で呼び出される。

Tools:
  - generate_doc: LLM で Markdown ドキュメントを生成して Vault に保存
  - summarize:    LLM でテキストを要約・応答して返す
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from llama_cpp import Llama
from mcp.server.fastmcp import FastMCP

# ============================================================
# 設定
# ============================================================
MODEL_PATH = os.environ.get("MODEL_PATH", "")
DEFAULT_VAULT_PATH = os.environ.get("VAULT_PATH", "")

mcp = FastMCP("kb-llm-server")

# ============================================================
# 外部辞書ロード（派生語バリエーション & 単漢字許可リスト）
# 再起動時に python/config/*.json を自動読み込み
# ============================================================
_CONFIG_DIR = Path(__file__).parent / "config"


def _load_variants() -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    """variants.json を読み込んで exact / contains_replace / suffix_strip を返す。"""
    path = _CONFIG_DIR / "variants.json"
    try:
        data: dict = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[config] variants.json load failed: {e}", file=sys.stderr)
        data = {}
    return (
        data.get("exact", {}),
        data.get("contains_replace", {}),
        data.get("suffix_strip", []),
    )


def _load_single_kanji_set() -> frozenset[str]:
    """single_kanji_whitelist.json を読み込んで frozenset を返す。"""
    path = _CONFIG_DIR / "single_kanji_whitelist.json"
    try:
        data: list[str] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[config] single_kanji_whitelist.json load failed: {e}", file=sys.stderr)
        data = []
    return frozenset(data)


(_VARIANTS_EXACT, _VARIANTS_CONTAINS_REPLACE, _VARIANTS_SUFFIX_STRIP) = _load_variants()
_SINGLE_KANJI_SET: frozenset[str] = _load_single_kanji_set()

# ============================================================
# コンパイル済み正規表現パターン（パフォーマンス最適化）
# re.compile() はモジュールロード時に一度だけ実行される
# ============================================================
_RE_BULLET_PREFIX = re.compile(r"^[-*・]\s+")
_RE_FRONTMATTER_BLOCK = re.compile(r"(?ms)^---\s*\n(.*?)\n---\s*\n")
_RE_FM_FIELDS = re.compile(
    r"^(id|title|slug|tags|created|updated|summary|image|type|isDraft)\s*:",
    re.MULTILINE,
)
_RE_CODE_FENCE = re.compile(r"^```[a-z]*\s*$", re.MULTILINE)
_RE_BOLD = re.compile(r"\*\*")
_RE_NON_SLUG = re.compile(r"[^a-z0-9\s-]")
_RE_WHITESPACE_UNDER = re.compile(r"[\s_]+")
_RE_MULTI_DASH = re.compile(r"-{2,}")
_RE_FM_SKIP_LINE = re.compile(
    r"^\*{0,2}(id|created|updated|title|slug|tags|summary)", re.IGNORECASE
)
# 「〜をまとめた記事を作って」などの指示文を除去（検索クエリ抽出用）
# 「教えて」単体・「も教えて」等も除去できるよう拡張
_RE_INSTRUCTION_SUFFIX = re.compile(
    r"(?:できるだけ\s*)?(?:詳しく|詳細に|わかりやすく|丁寧に)?\s*[をにについて、も]*(まとめ(て|た記事(を作(ってください|って)?)?)?|記事を作(ってください|って)?|作(ってください|って)|教えて(ください)?|解説して(ください)?|説明して(ください)?)(。)?$"
)
# 文字数指定・詳細度修飾語を除去（例: "1500文字程度で詳しく" "500文字で"）
_RE_DETAIL_SPEC = re.compile(
    r"\d+\s*(?:文字|字)\s*(?:程度|以上|以内|ほど)?\s*(?:で\s*)?(?:できるだけ\s*)?(?:詳しく\s*|詳細に\s*)?|(?:できるだけ\s*)?(?:詳しく|詳細に)\s*$|できるだけ\s*$"
)
_RE_LATIN_TOKEN = re.compile(r"[a-zA-Z]{2,16}")
# フォールバック抽出は漢字/カタカナ中心に限定してノイズを抑える
# ひらがな語は「〜の」「〜について」抽出側で拾う
_RE_JP_KEYWORD = re.compile(r"[\u30a0-\u30ff\u4e00-\u9fff]{1,14}")

_GENERIC_SECONDARY_QUERIES = {
    "歴代",
    "一覧",
    "概要",
    "情報",
    "特徴",
    "説明",
    "解説",
    "方法",
    "種類",
    "歴史",
    "現状",
    "現代",
    "世界",
    "日本",
    "人気",
    "有名",
    "文字程度",  # 「1500文字程度で」の残滓除去用
    "字程度",  # 「1500字程度で」の残滓除去用
    "詳細",
    "それぞれ",
}

# 英字略語を日本語見出し語に展開する辞書
_LATIN_QUERY_VARIANTS = {
    "gdp": ["GDP", "国内総生産"],
    "ai": ["AI", "人工知能"],
    "apple": ["Apple", "アップル"],
    "microsoft": ["Microsoft", "マイクロソフト"],
}

# ============================================================
# LLM シングルトン（遅延初期化）
# ============================================================
_llm: Llama | None = None


def get_llm() -> Llama:
    global _llm
    if _llm is None:
        if not MODEL_PATH:
            raise EnvironmentError(
                "MODEL_PATH environment variable is not set. "
                "Set it to the path of your .gguf model file."
            )
        model_path = Path(MODEL_PATH).expanduser()
        if not model_path.exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")
        if model_path.read_bytes()[:4] != b"GGUF":
            raise ValueError(f"Not a valid GGUF file: {model_path}")
        _llm = Llama(
            model_path=str(model_path),
            n_ctx=32768,
            n_threads=8,
            n_gpu_layers=-1,
            verbose=False,
            use_mmap=True,
            use_mlock=False,
            n_batch=1024,
        )
    return _llm


# ============================================================
# テキスト正規化ユーティリティ
# ============================================================
@lru_cache(maxsize=512)
def normalize_slug_words(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    lowered = normalized.lower().strip()
    cleaned = _RE_NON_SLUG.sub("", lowered)
    dashed = _RE_WHITESPACE_UNDER.sub("-", cleaned)
    compact = _RE_MULTI_DASH.sub("-", dashed).strip("-")
    return compact or "doc"


def generate_slug(title: str, out_dir: Path) -> str:
    base = normalize_slug_words(title)
    while True:
        hex_prefix = secrets.token_hex(2)
        slug = f"{hex_prefix}-{base}"
        if not (out_dir / f"{slug}.md").exists():
            return slug


def normalize_summary(text: str) -> str:
    text = _RE_BULLET_PREFIX.sub("", text.strip())
    return " ".join(text.split()).strip()


def trim_summary(text: str, max_len: int = 300) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    cut = max(
        truncated.rfind("。"),
        truncated.rfind("."),
        truncated.rfind("!"),
        truncated.rfind("?"),
    )
    return truncated[: cut + 1] if cut > 20 else truncated.rstrip()


def build_summary_from_body(body: str) -> str:
    for line in body.splitlines():
        cleaned = line.strip().lstrip("-* ").strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        if _RE_FM_SKIP_LINE.match(cleaned):
            continue
        if len(cleaned) > 10:
            return trim_summary(normalize_summary(cleaned))
    return "自動生成された概要"


# ============================================================
# Markdown / Frontmatter パース
# ============================================================
def split_frontmatter(markdown: str) -> tuple[str, str]:
    pattern = _RE_FRONTMATTER_BLOCK
    matches = list(pattern.finditer(markdown))
    if not matches:
        return "", markdown

    def is_fm_block(text: str) -> bool:
        return bool(
            _RE_FM_FIELDS.search(text)
        )

    candidates = [m for m in matches if is_fm_block(m.group(1))]
    selected = candidates[-1] if candidates else matches[0]
    return selected.group(1), markdown[selected.end() :]


def parse_frontmatter(fm_text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        data[key] = value
    return data


def parse_tags(value: str) -> list[str]:
    text = value.strip().lstrip("[").rstrip("]")
    return [item.strip().strip("\"'") for item in text.split(",") if item.strip()]


def strip_frontmatter_blocks(text: str) -> str:
    lines = text.splitlines()
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "---":
            j = i + 1
            while j < len(lines) and lines[j].strip() != "---":
                j += 1
            if j < len(lines):
                block = "\n".join(lines[i + 1 : j])
                if _RE_FM_FIELDS.search(block):
                    i = j + 1
                    continue
                out_lines.extend(lines[i : j + 1])
                i = j + 1
                continue
        out_lines.append(lines[i])
        i += 1
    return "\n".join(out_lines)


def extract_section(body: str, heading: str) -> tuple[str, str]:
    lines = body.splitlines()
    start_index = next(
        (
            i + 1
            for i, line in enumerate(lines)
            if line.strip() in {f"# {heading}", f"## {heading}"}
        ),
        None,
    )
    if start_index is None:
        return "", body
    end_index = next(
        (i for i in range(start_index, len(lines)) if lines[i].strip().startswith("# ")),
        len(lines),
    )
    section = "\n".join(lines[start_index:end_index]).strip()
    remaining = "\n".join(lines[: start_index - 1] + lines[end_index:]).strip()
    return section, remaining


def extract_related_section(body: str) -> tuple[str, str]:
    lines = body.splitlines()
    start_index = next(
        (i for i, line in enumerate(lines) if line.strip() in {"# 関連", "## 関連"}),
        None,
    )
    if start_index is None:
        return body, ""
    end_index = next(
        (i for i in range(start_index + 1, len(lines)) if lines[i].strip().startswith("# ")),
        len(lines),
    )
    related_text = "\n".join(lines[start_index + 1 : end_index]).strip()
    body_without = "\n".join(lines[:start_index] + lines[end_index:]).strip()
    return body_without, related_text


def ensure_template(body: str, summary: str, related_links: str, title: str) -> str:
    cleaned_body = body.strip() or f"{title}に関する技術ドキュメント。"
    related_section = related_links.strip() or "- なし"
    return "\n".join(
        ["# 概要", summary, "", "# 詳細", cleaned_body, "", "# 関連", related_section, ""]
    ).strip()


# ============================================================
# MCP ツール定義
# ============================================================
@mcp.tool()
def generate_doc(
    prompt: str,
    title: str = "",
    tags: str = "",
    vault_dir: str = "",
) -> str:
    """
    LLM で Markdown ドキュメントを生成して Vault に保存し、ファイルパスを返す。

    Args:
        prompt:    LLM に渡すプロンプト文字列
        title:     ドキュメントのタイトル（省略時はLLM出力から抽出）
        tags:      カンマ区切りのタグ文字列
        vault_dir: 出力先ディレクトリ（省略時は VAULT_PATH 環境変数）
    """
    out_dir = Path(vault_dir or DEFAULT_VAULT_PATH)
    out_dir.mkdir(parents=True, exist_ok=True)

    llm = get_llm()
    result = llm(
        prompt=prompt,
        max_tokens=8192,
        temperature=0.7,
        top_k=50,
        repeat_penalty=1.1,
    )
    text: str = result["choices"][0]["text"]

    # Markdown パース
    fm_text, body_text = split_frontmatter(text)
    fm_data = parse_frontmatter(fm_text)
    body_text = _RE_CODE_FENCE.sub("", body_text)
    body_text = strip_frontmatter_blocks(body_text).strip()

    body_text, related_links = extract_related_section(body_text)
    overview_text, body_text = extract_section(body_text, "概要")
    detail_text, body_text = extract_section(body_text, "詳細")

    # メタデータ生成
    doc_title = title or fm_data.get("title", "").strip() or "Untitled"
    slug = generate_slug(doc_title, out_dir)
    cli_tags = [t.strip() for t in tags.split(",") if t.strip()]
    fm_tags = parse_tags(fm_data.get("tags", ""))
    all_tags = list(dict.fromkeys(cli_tags + fm_tags))

    summary_raw = normalize_summary(_RE_BOLD.sub("", fm_data.get("summary", "").strip()))
    if not summary_raw and overview_text:
        summary_raw = build_summary_from_body(overview_text)
    if not summary_raw:
        summary_raw = build_summary_from_body(detail_text or body_text)
    summary = trim_summary(normalize_summary(summary_raw)) or "自動生成された概要"

    body_text = detail_text or body_text
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    doc_id = now.strftime("%Y%m%d%H%M%S")
    tags_field = ", ".join(all_tags)

    metadata_lines = [
        "---",
        f'id: "{doc_id}"',
        f'title: "{doc_title}"',
        f'slug: "{slug}"',
        f"tags: [{tags_field}]" if tags_field else "tags: []",
        f'created: "{today}"',
        f'updated: "{today}"',
        f'summary: "{summary}"',
        'image: "https://ytzmpefdjnd1ueff.public.blob.vercel-storage.com/blog.webp"',
        'type: "diary"',
        'isDraft: "true"',
        "---",
        "",
    ]

    content = ensure_template(body_text, summary, related_links, doc_title)
    outfile = out_dir / f"{slug}.md"
    outfile.write_text("\n".join(metadata_lines) + content + "\n", encoding="utf-8")
    return str(outfile)


@mcp.tool()
def summarize(prompt: str) -> str:
    """
    プロンプトを LLM に渡してテキストを返す（要約・質問応答用）。

    Args:
        prompt: LLM に渡すプロンプト文字列
    """
    llm = get_llm()
    result = llm(
        prompt=prompt,
        max_tokens=2048,
        temperature=0.7,
        top_k=50,
        repeat_penalty=1.1,
    )
    return result["choices"][0]["text"].strip()


# ============================================================
# DB URL ヘルパー
# ============================================================
def get_db_url() -> str:
    load_dotenv(".env.local")
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:54325/postgres",
    )


def _extract_search_queries(query: str) -> tuple[str, list[str], list[str]]:
    """質問文から検索クエリを抽出する。

    Returns:
        search_base: 指示語除去後の質問（補助クエリ生成用）
        vector_queries: ベクトル検索に使うクエリ（先頭は必ず質問全文そのまま）
        title_queries: タイトル一致検索に使う補助クエリ
    """
    import re as _re

    search_base = _RE_INSTRUCTION_SUFFIX.sub("", query).strip() or query
    search_base = _RE_DETAIL_SPEC.sub("", search_base).strip() or search_base
    # 末尾の助詞（「〜を」「〜に」等）を除去
    search_base = search_base.rstrip("をにはがもや、。\u3000 ").strip() or search_base
    search_base = " ".join(search_base.split())[:300]
    # OCR/入力揺れ対策: カナ文字間の空白を除去（例: イ ベント -> イベント）
    search_base = _re.sub(
        r"(?<=[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff])\s+(?=[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff])",
        "",
        search_base,
    )

    # 先頭クエリはユーザー質問を無加工でそのまま使う（Gemma3 への質問と同一）。
    # 補助クエリのみ search_base から生成する。
    vector_queries: list[str] = [query]
    title_queries: list[str] = []

    nouns_nitsuite = _re.findall(
        r"([\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]{1,14})\u306b\u3064\u3044\u3066",
        search_base,
    )
    nouns_tono = _re.findall(
        r"([\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]{1,14})\u3068\u306e",
        search_base,
    )
    nouns_no = _re.findall(r"([^の\s、。]{1,14})\u306e", search_base)
    phrase_pairs = _re.findall(
        r"([^の\s、。]{1,14})\u306e([^の\s、。]{1,14})",
        search_base,
    )

    seen: set[str] = {vector_queries[0]}

    def _is_single_kanji(term: str) -> bool:
        # 1文字語はノイズになりやすいので許可リストのみ採用
        # python/config/single_kanji_whitelist.json で管理
        return term in _SINGLE_KANJI_SET

    def add_query_term(term: str, with_title: bool = True) -> None:
        if term in seen:
            return
        seen.add(term)
        vector_queries.append(term)
        if with_title:
            title_queries.append(term)

    def add_latin_variants() -> None:
        """英字略語をタイトル検索しやすい語へ展開する。"""
        lower_base = search_base.lower()
        has_japan_context = "日本" in search_base
        latin_tokens = list(dict.fromkeys(_RE_LATIN_TOKEN.findall(search_base)))
        if len(latin_tokens) >= 2:
            # 複数英字語を1クエリに束ねて company 対比質問へ対応
            add_query_term(" ".join(latin_tokens))

        for token in latin_tokens:
            key = token.lower()
            # 一般英字語もそのまま補助クエリとして活用
            add_query_term(token.lower())
            add_query_term(token.capitalize())

            if key not in _LATIN_QUERY_VARIANTS:
                continue
            for variant in _LATIN_QUERY_VARIANTS[key]:
                add_query_term(variant)
                if has_japan_context and " " not in variant and variant not in lower_base:
                    add_query_term(f"日本 {variant}")

    def expand_variants(term: str) -> list[str]:
        """外部辞書 python/config/variants.json に基づくバリエーション展開。"""
        variants: list[str] = [term]

        # 完全一致バリエーション (exact)
        for v in _VARIANTS_EXACT.get(term, []):
            if v not in variants:
                variants.append(v)

        # サフィックス除去 (suffix_strip)
        # 例: "アイヌ民族" → "アイヌ" (suffix="民族")
        for suffix in _VARIANTS_SUFFIX_STRIP:
            if term.endswith(suffix) and len(term) > len(suffix):
                stripped = term[: -len(suffix)]
                if stripped not in variants:
                    variants.append(stripped)

        # 部分文字列置換 (contains_replace)
        # 例: "観光名称" を含む語 → "観光名所"/"観光地" に置換
        for old, replacements in _VARIANTS_CONTAINS_REPLACE.items():
            if old in term:
                for new in replacements:
                    v = term.replace(old, new)
                    if v not in variants:
                        variants.append(v)

        return variants

    # 主題（「〜について」）は汎用語でも保持する
    for noun in nouns_nitsuite:
        if "の" in noun:
            continue
        if any(stop in noun for stop in ("特に", "季節ごと", "季節ごとの")):
            continue
        for variant in expand_variants(noun):
            add_query_term(variant)

    # 文脈語（「〜との」「〜の」）はノイズ語を除外
    for noun in nouns_tono + nouns_no:
        if "の" in noun:
            continue
        if noun in _GENERIC_SECONDARY_QUERIES:
            continue
        if any(stop in noun for stop in ("特に", "季節ごと", "季節ごとの")):
            continue
        for variant in expand_variants(noun):
            add_query_term(variant)

    for left, right in phrase_pairs:
        if "の" in left or "の" in right:
            continue
        if left in _GENERIC_SECONDARY_QUERIES:
            continue
        if right in {"観光名所", "観光地", "名所", "観光名称"}:
            add_query_term(f"{left} 観光地", with_title=False)
            add_query_term(f"{left} 観光", with_title=False)
        if right in {"観光名", "イベント"}:
            add_query_term(f"{left} {right}")
            if right == "観光名":
                add_query_term(f"{left} 観光名所")
                add_query_term(f"{left} 観光地")
            if right == "イベント":
                add_query_term(f"{left} 観光", with_title=False)

    # 「Xの〜観光/イベント/祭り」形式は、地名・主題を組み合わせた補助語を追加する
    has_tourism_intent = any(k in search_base for k in ("観光", "イベント", "祭", "祭り"))
    if has_tourism_intent:
        for head in nouns_no:
            if "の" in head:
                continue
            if head in _GENERIC_SECONDARY_QUERIES:
                continue
            # 「季節ごと」のような時制・汎用フレーズは除外
            if any(stop in head for stop in ("季節", "ごと", "特に")):
                continue
            add_query_term(f"{head} 観光")
            if any(k in search_base for k in ("イベント", "祭", "祭り")):
                add_query_term(f"{head} イベント")

    # 英字略語（GDP など）の補助語を追加
    add_latin_variants()

    # フレーズ抽出の補助（例: 観光名所, 少数民族, アイヌ民族）
    for token in _RE_JP_KEYWORD.findall(search_base):
        if token in _GENERIC_SECONDARY_QUERIES:
            continue
        if token.startswith("特に"):
            continue
        if len(token) >= 2 or _is_single_kanji(token):
            for variant in expand_variants(token):
                add_query_term(variant)
        if len(vector_queries) >= 5:
            break

    location_heads = {
        n
        for n in nouns_no
        if n
        and n not in _GENERIC_SECONDARY_QUERIES
        and not any(stop in n for stop in ("特に", "季節", "ごと"))
    }

    def _priority(term: str) -> tuple[int, int]:
        score = 0
        if any(k in term for k in ("観光", "名所", "観光地", "イベント", "祭", "春", "桜")):
            score += 26
        if _RE_LATIN_TOKEN.fullmatch(term):
            score += 34
        elif _RE_LATIN_TOKEN.search(term):
            score += 10
        if " " in term:
            score += 8
        if term in location_heads:
            score += 42
        if term.startswith("特に"):
            score -= 25
        if term in _GENERIC_SECONDARY_QUERIES:
            score -= 20
        return score, len(term)

    vector_tail = sorted(vector_queries[1:], key=_priority, reverse=True)
    prioritized_vector_queries = [vector_queries[0], *vector_tail][:5]
    prioritized_title_queries = sorted(title_queries, key=_priority, reverse=True)[:4]

    return search_base, prioritized_vector_queries, prioritized_title_queries


def _merge_ranked_docs(
    primary_vector_docs: list[dict],
    secondary_vector_lists: list[list[dict]],
    title_lists: list[list[dict]],
    anchor_terms: list[str],
) -> list[dict]:
    """複数検索結果をスコアリングして統合する。"""
    score_by_id: dict[int, float] = {}
    doc_by_id: dict[int, dict] = {}

    def add_docs(docs: list[dict], base: float, decay: float) -> None:
        for rank, doc in enumerate(docs):
            doc_id = int(doc["id"])
            score = base - rank * decay
            score_by_id[doc_id] = score_by_id.get(doc_id, 0.0) + score
            doc_by_id[doc_id] = doc

    # 質問全文ベクトル検索を主軸にする
    add_docs(primary_vector_docs, base=82.0, decay=4.0)

    # 補助ベクトル検索は加点のみ
    for docs in secondary_vector_lists:
        add_docs(docs, base=38.0, decay=2.0)

    # タイトル一致は補助シグナル
    for docs in title_lists:
        add_docs(docs, base=96.0, decay=8.0)

    # アンカー語に一致する記事へ追加ボーナスを付与
    for doc_id, doc in doc_by_id.items():
        title = doc["title"]
        content_head = doc["content"][:1200]
        bonus = 0.0
        for term in anchor_terms:
            parts = [p for p in term.split(" ") if p]
            for part in parts:
                if part in _GENERIC_SECONDARY_QUERIES:
                    continue
                if part in title:
                    bonus += 16.0
                elif part in content_head:
                    bonus += 3.5
        if bonus:
            score_by_id[doc_id] = score_by_id.get(doc_id, 0.0) + bonus

    ranked_ids = sorted(score_by_id.keys(), key=lambda doc_id: score_by_id[doc_id], reverse=True)
    return [doc_by_id[doc_id] for doc_id in ranked_ids]


async def _retrieve_rag_docs(
    query: str,
    db_url: str,
) -> tuple[list[dict], list[str], list[list[dict]]]:
    """RAG 用の検索を実行し、統合済み候補を返す。"""
    import aiohttp
    import asyncpg

    _, all_vector_queries, all_title_queries = _extract_search_queries(query)
    latin_terms = list(dict.fromkeys([token.lower() for token in _RE_LATIN_TOKEN.findall(query)]))

    # 速度最適化: 検索クエリ本数を最小化
    # - vector: 質問全文 1件
    # - title: 補助 1件（ただし観光/イベント等の意図語を優先）
    vector_queries = all_vector_queries[:1]
    if len(latin_terms) >= 2:
        combined_latin = " ".join(latin_terms[:2])
        if combined_latin in all_vector_queries:
            vector_queries = [all_vector_queries[0], combined_latin]
        else:
            vector_queries = [all_vector_queries[0], latin_terms[0]]
    # 英字略語を含む質問は、日本語同義語を優先して補助ベクトルに採用する
    elif any(_RE_LATIN_TOKEN.search(q) for q in all_vector_queries[:1]) and len(all_vector_queries) > 1:
        extra_query = all_vector_queries[1]
        picked_latin = False
        for candidate in all_vector_queries[1:]:
            has_latin = bool(_RE_LATIN_TOKEN.search(candidate))
            has_japanese = any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in candidate)
            if has_latin and not has_japanese:
                extra_query = candidate
                picked_latin = True
                break
        if not picked_latin:
            for candidate in all_vector_queries[1:]:
                if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in candidate):
                    extra_query = candidate
                    break
        vector_queries = [all_vector_queries[0], extra_query]
    # 観光/イベント系は地名+意図語の補助ベクトルを1本追加してノイズ耐性を上げる
    elif any(k in query for k in ("観光", "イベント", "祭", "祭り", "春", "花見")):
        for candidate in all_vector_queries[1:]:
            if any(k in candidate for k in ("観光", "イベント", "祭", "祭り")):
                vector_queries = [all_vector_queries[0], candidate]
                break
    title_candidates = list(dict.fromkeys(all_title_queries))
    if latin_terms:
        boosted_latin = []
        for token in latin_terms:
            boosted_latin.extend([token, token.capitalize()])
            for variant in _LATIN_QUERY_VARIANTS.get(token, []):
                boosted_latin.append(variant)
        title_candidates = list(dict.fromkeys(boosted_latin + title_candidates))

    if not title_candidates and len(all_vector_queries) > 1:
        # タイトル候補が空のときは補助ベクトル語をフォールバック採用
        title_candidates = [q for q in all_vector_queries[1:] if len(q) <= 24][:2]

    location_heads = set(re.findall(r"([^の\s、。]{1,14})\u306e", query))

    def title_priority(term: str) -> tuple[int, int]:
        score = 0
        if any(k in term for k in ("観光", "名所", "イベント", "祭", "桜", "春")):
            score += 30
        if " " in term:
            score += 8
        if term in location_heads:
            score += 45
        if term.startswith("特に"):
            score -= 20
        if term in _GENERIC_SECONDARY_QUERIES:
            score -= 20
        return score, len(term)

    title_candidates.sort(key=title_priority, reverse=True)
    if len(latin_terms) >= 2:
        title_queries = latin_terms[:2]
    else:
        title_queries = title_candidates[:2]
    # アンカー語は検索に使わない分も保持して再ランキングで活用
    anchor_terms = [q for q in all_vector_queries[1:4] if q]
    for tq in title_queries:
        if tq and tq not in anchor_terms:
            anchor_terms.append(tq)

    async def embed_one(session: "aiohttp.ClientSession", text: str) -> list[float]:
        async with session.post(
            "http://localhost:11434/api/embed",
            json={"model": "nomic-embed-text", "input": "search_query: " + text},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data["embeddings"][0]

    async def search_one(pool: "asyncpg.Pool", emb: list[float]) -> list[dict]:
        emb_str = "[" + ",".join(map(str, emb)) + "]"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, title, content FROM search_documents($1::vector)",
                emb_str,
            )
        return [{"id": r["id"], "title": r["title"], "content": r["content"]} for r in rows]

    async def title_search(pool: "asyncpg.Pool", noun: str) -> list[dict]:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, title, content FROM documents
                   WHERE title = $1 OR title = $2 OR title ILIKE $3
                   ORDER BY
                     CASE WHEN title = $1 THEN 0
                          WHEN title = $2 THEN 1
                          ELSE 2 END,
                     length(title) ASC
                                     LIMIT 10""",
                f"{noun}一覧",
                noun,
                f"%{noun}%",
            )
        return [{"id": r["id"], "title": r["title"], "content": r["content"]} for r in rows]

    async def wrapped_search(task_type: str, coro: "asyncio.Future[list[dict]]") -> tuple[str, list[dict] | None, Exception | None]:
        try:
            result = await coro
            return task_type, result, None
        except Exception as exc:  # noqa: BLE001
            return task_type, None, exc

    async with aiohttp.ClientSession() as session:
        import asyncio as _asyncio

        embs = await _asyncio.gather(*[embed_one(session, q) for q in vector_queries])

    import sys as _sys
    import asyncio as _asyncio

    max_concurrency = int(os.environ.get("RAG_DB_MAX_CONCURRENCY", "4"))
    query_timeout_s = float(os.environ.get("RAG_DB_QUERY_TIMEOUT_SEC", "60"))
    sem = _asyncio.Semaphore(max_concurrency)

    async def limited(coro: "asyncio.Future[list[dict]]") -> list[dict]:
        async with sem:
            return await _asyncio.wait_for(coro, timeout=query_timeout_s)

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=max_concurrency,
        command_timeout=query_timeout_s,
    )
    try:
        tasks: list["asyncio.Future[tuple[str, list[dict] | None, Exception | None]]"] = []
        for emb in embs:
            tasks.append(wrapped_search("vector", limited(search_one(pool, emb))))
        for tq in title_queries:
            tasks.append(wrapped_search("title", limited(title_search(pool, tq))))

        results = await _asyncio.gather(*tasks)

        vector_lists: list[list[dict]] = []
        title_lists: list[list[dict]] = []
        error_count = 0
        for task_type, result, err in results:
            if err is not None:
                error_count += 1
                print(f"[_retrieve_rag_docs] {task_type} search failed: {err}", file=_sys.stderr)
                continue
            if result is None:
                continue
            if task_type == "vector":
                vector_lists.append(result)
            else:
                title_lists.append(result)

        if error_count > 0:
            print(
                f"[_retrieve_rag_docs] completed with errors: {error_count}/{len(results)}",
                file=_sys.stderr,
            )

        # vector が失敗した場合のフォールバック:
        # 2番目候補のタイトル検索を追加実行し、title-only の取りこぼしを減らす
        if not vector_lists and len(title_candidates) > 1:
            fallback_term = title_candidates[1]
            try:
                fallback_docs = await limited(title_search(pool, fallback_term))
                if fallback_docs:
                    title_lists.append(fallback_docs)
                    print(
                        f"[_retrieve_rag_docs] fallback title_search succeeded: {fallback_term}",
                        file=_sys.stderr,
                    )
            except Exception as fallback_err:  # noqa: BLE001
                print(
                    f"[_retrieve_rag_docs] fallback title_search failed: {fallback_err}",
                    file=_sys.stderr,
                )
    finally:
        await pool.close()

    primary_vector_docs = vector_lists[0] if vector_lists else []
    secondary_vector_lists = vector_lists[1:] if len(vector_lists) > 1 else []
    docs = _merge_ranked_docs(primary_vector_docs, secondary_vector_lists, title_lists, anchor_terms)

    is_tourism_query = any(k in query for k in ("観光", "イベント", "祭", "祭り", "春", "花見"))
    if is_tourism_query and docs:
        anchor_parts = [
            part
            for term in anchor_terms
            for part in term.split(" ")
            if part and part not in _GENERIC_SECONDARY_QUERIES
        ]
        if anchor_parts:
            filtered_docs = []
            for doc in docs:
                title = doc["title"]
                if any(part in title for part in anchor_parts):
                    filtered_docs.append(doc)
            if filtered_docs:
                docs = filtered_docs

    # Gemma3 入力は最大 10 件（高精度優先で不足時は件数を減らす）
    return docs[:10], vector_queries, title_lists


def _build_rag_prompt(context: str, raw_query: str) -> str:
    """Gemma3 へ渡す RAG プロンプトを構築する。

    raw_query はユーザー入力を一切加工せず、そのまま質問欄へ入れる。
    """
    return (
        "あなたは日本語のWikipedia情報を正確に要約するアシスタントです。\n"
        "以下のWikipedia本文のみを根拠として、質問に答えてください。\n"
        "【重要なルール】\n"
        "- 提供されたWikipedia本文に書かれている事実のみを使用してください\n"
        "- Wikipedia本文に記載のない人名・日付・数字は一切書かないでください\n"
        "- リストを作成する場合は、Wikipedia本文に明記された項目のみを含めてください\n"
        "- 不明な情報は「Wikipediaに記載がありません」と記してください\n\n"
        f"Wikipedia Context:\n{context}\n\n"
        f"Question (verbatim):\n{raw_query}\n\n"
        "Answer:"
    )


@mcp.tool()
async def rag_ask(
    query: str,
    vault_dir: str = "",
    tags: str = "",
) -> str:
    """
    ローカル Wikipedia vectorDB を検索し Gemma3(Ollama) で回答を生成して Vault に保存する。

    Args:
        query:     質問文字列
        vault_dir: 出力先ディレクトリ（省略時は VAULT_PATH 環境変数）
        tags:      カンマ区切りのタグ文字列
    """
    import aiohttp

    raw_query = query
    db_url: str = get_db_url()
    docs, sub_queries, title_lists = await _retrieve_rag_docs(raw_query, db_url)

    # --- Ollama Gemma3 で回答生成（非同期） ---
    if not docs:
        answer = "関連する情報が見つかりませんでした。"
        sources_list_md = ""
        sources_body_md = ""
    else:
        # 取得した Wikipedia は 10 件固定で、全件を Gemma3 入力へ含める
        TARGET_DOCS_FOR_GEMMA = 10
        docs = docs[:TARGET_DOCS_FOR_GEMMA]
        retrieved_docs_count = len(docs)

        title_matched_ids = set()
        for tl in title_lists:
            for d in tl:
                title_matched_ids.add(d["id"])

        def _ctx_len(d: dict) -> int:
            title = d["title"]
            if "一覧" in title:
                return 1200
            if d["id"] in title_matched_ids:
                return 1000
            return 800

        # 先頭優先は維持（一致記事・一覧記事を前側へ）
        docs.sort(
            key=lambda d: (
                0 if d["id"] in title_matched_ids else 1,
                0 if "一覧" in d["title"] else 1,
            )
        )

        context_parts: list[str] = []
        context_sources: list[tuple[str, str]] = []
        total_ctx_chars = 0
        for d in docs:
            chunk = d["content"][: _ctx_len(d)]
            if not chunk:
                chunk = d["content"][:1]
            context_parts.append(f"【{d['title']}】\n{chunk}")
            context_sources.append((d["title"], chunk))
            total_ctx_chars += len(chunk)

        included_docs_count = len(context_sources)
        if included_docs_count != retrieved_docs_count:
            raise RuntimeError(
                f"Context build mismatch: retrieved={retrieved_docs_count}, included={included_docs_count}"
            )

        context = "\n\n".join(context_parts)
        rag_prompt = _build_rag_prompt(context, raw_query)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "gemma3:4b",
                    "prompt": rag_prompt,
                    "stream": False,
                    "options": {
                        # 速度最適化: 10件入力は維持しつつ、出力長と探索幅をさらに圧縮
                        "num_ctx": 30000,
                        "num_predict": 10000,
                        "temperature": 0.15,
                        "top_k": 15,
                        "top_p": 0.85,
                        "repeat_penalty": 1.03,
                    },
                },
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                resp.raise_for_status()
                gen_data = await resp.json()
        answer = gen_data["response"].strip()
        # 実際のトークン使用量をログ（チューニング用）
        prompt_eval_count = gen_data.get("prompt_eval_count", "?")
        eval_count = gen_data.get("eval_count", "?")
        eval_duration_s = gen_data.get("eval_duration", 0) / 1e9
        import sys as _sys
        print(
            f"[rag_ask] context={total_ctx_chars:,}文字"
            f" retrieved_docs={retrieved_docs_count} included_docs={included_docs_count}"
            f" prompt_tokens={prompt_eval_count} eval_tokens={eval_count}"
            f" eval_time={eval_duration_s:.1f}s",
            file=_sys.stderr,
        )
        # 参照元タイトル一覧（Gemma3へ渡した順序）
        sources_list_md = "\n".join([f"- 【{title}】" for title, _ in context_sources])
        # 参照元Wikipedia本文（Gemma3へ実際に渡した本文をそのまま保存）
        sources_body_md = "\n\n---\n\n".join(
            [f"### 【{title}】\n\n{body}" for title, body in context_sources]
        )

    # --- Vault に Markdown 保存 ---
    out_dir = Path(vault_dir or DEFAULT_VAULT_PATH).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] or ["wikipedia", "rag", "qa"]
    tags_field = ", ".join(tag_list)
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    today = now.strftime("%Y-%m-%d")
    doc_id = now.strftime("%Y%m%d%H%M%S")
    slug = generate_slug(raw_query, out_dir)
    summary = trim_summary(normalize_summary(answer)) or "Wikipedia RAG による回答"

    metadata_lines = [
        "---",
        f'id: "{doc_id}"',
        f'title: "{raw_query}"',
        f'slug: "{slug}"',
        f"tags: [{tags_field}]",
        f'created: "{today}"',
        f'updated: "{today}"',
        f'summary: "{summary}"',
        'image: ""',
        'type: "doc"',
        'isDraft: "false"',
        "---",
        "",
    ]

    body = (
        f"# 質問\n{raw_query}\n\n"
        f"# 回答\n{answer}\n\n"
        f"# 検索クエリ (実際に使用)\n"
        + "\n".join([f"- `{sq}`" for sq in sub_queries])
        + "\n\n"
        f"# 参照元 Wikipedia 一覧\n{sources_list_md}\n\n"
        f"# 参照元 Wikipedia 本文\n\n{sources_body_md}\n"
    )

    outfile = out_dir / f"{slug}.md"
    outfile.write_text("\n".join(metadata_lines) + body, encoding="utf-8")
    return str(outfile)


# ============================================================
# エントリーポイント
# ============================================================
if __name__ == "__main__":
    mcp.run()
