#!/usr/bin/env python3
"""
Knowledge Base LLM MCP Server (FastMCP / stdio)

llama-cpp (Gemma) をラップした MCP サーバー。
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
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from gte_embedding import DEFAULT_EMBEDDING_MODEL, GteEmbeddingModel
from llama_cpp import Llama
from mcp.server.fastmcp import FastMCP

# ============================================================
# 設定
# ============================================================
# .env.local を先に読み込み、既存環境変数より優先して解決する。
load_dotenv(".env.local", override=True)

_DEFAULT_MODEL_PATH = "/Users/ykz/programming/models/gemma-4-E2B-it-Q4_0.gguf"
MODEL_PATH = os.environ.get("MODEL_PATH", "").strip() or _DEFAULT_MODEL_PATH
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
_RE_GROUNDED_TOKEN = re.compile(r"[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff]{2,30}")
_RE_TITLE_BOUNDARY_TEMPLATE = r"(^|[\s\-・/()\[\]{}（）「」『』])%s($|[\s\-・/()\[\]{}（）「」『』])"
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
    "red": ["Red", "Red Bull", "Red Bull GmbH", "レッドブル"],
}

# Wikipedia見出しでヒットしやすい正式名称への正規化辞書
_JP_QUERY_CANONICAL_VARIANTS = {
    "首相": ["内閣総理大臣", "総理大臣", "内閣総理大臣の一覧"],
    "総理": ["内閣総理大臣", "総理大臣"],
    "狸小路": ["狸小路商店街", "札幌狸小路商店街"],
    "コンサドーレ": ["北海道コンサドーレ札幌", "コンサドーレ札幌"],
}

_EMBED_CACHE_MAX = int(os.environ.get("RAG_EMBED_CACHE_MAX", "512"))
_QUERY_NORMALIZATION_TIMEOUT_SEC = float(
    os.environ.get("RAG_QUERY_NORMALIZATION_TIMEOUT_SEC", "25")
)
_QUERY_NORMALIZATION_NUM_PREDICT = int(
    os.environ.get("RAG_QUERY_NORMALIZATION_NUM_PREDICT", "192")
)
_EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
_EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "64"))
_EMBEDDING_MAX_LENGTH = int(os.environ.get("EMBEDDING_MAX_LENGTH", "512"))
_embed_cache: "OrderedDict[str, list[float]]" = OrderedDict()

_LLAMA_PRESETS: dict[str, dict[str, float | int]] = {
    # ドキュメント生成は本文品質優先
    "doc_generation": {
        "max_tokens": 8192,
        "temperature": 0.7,
        "top_k": 50,
        "repeat_penalty": 1.1,
    },
    # 非RAGの短い要約・回答は最小限パラメータで高速化
    "non_rag_minimal": {
        "max_tokens": 1024,
        "temperature": 0.3,
        "top_k": 20,
        "repeat_penalty": 1.05,
    },
    # 検索結果の短文要約向け
    "search_summary": {
        "max_tokens": 700,
        "temperature": 0.2,
        "top_k": 20,
        "repeat_penalty": 1.05,
    },
    # 非RAGの質問応答向け（compare-wiki の RAGなし回答、question など）
    "qa_non_rag": {
        "max_tokens": 2600,
        "temperature": 0.5,
        "top_k": 40,
        "repeat_penalty": 1.08,
    },
    # RAG回答向け（コンテキスト前提）
    "qa_rag": {
        "max_tokens": 3000,
        "temperature": 0.5,
        "top_k": 20,
        "repeat_penalty": 1.08,
    },
    # compare-wiki 専用の軽量非RAG回答（フリーズ抑制）
    "compare_non_rag_light": {
        "max_tokens": 1200,
        "temperature": 0.4,
        "top_k": 30,
        "repeat_penalty": 1.06,
    },
    # ニュース記事生成向け（長文）
    "news_article": {
        "max_tokens": 8192,
        "temperature": 0.7,
        "top_k": 50,
        "repeat_penalty": 1.1,
    },
}

_SUMMARIZE_MODE_DEFAULT = "non_rag_minimal"
_SUMMARIZE_MODE_MAP: dict[str, tuple[str, bool]] = {
    # mode: (preset, wrap_non_rag_prompt)
    "non_rag_minimal": ("non_rag_minimal", True),
    "search_summary": ("search_summary", False),
    "qa_non_rag": ("qa_non_rag", False),
    "compare_non_rag_light": ("compare_non_rag_light", False),
    "news_article": ("news_article", False),
}


def _lru_get(cache: "OrderedDict[str, object]", key: str):
    value = cache.get(key)
    if value is None:
        return None
    cache.move_to_end(key)
    return value


def _lru_set(cache: "OrderedDict[str, object]", key: str, value: object, max_size: int) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > max_size:
        cache.popitem(last=False)


def _build_non_rag_prompt(user_prompt: str) -> str:
    """Wikipedia RAG を使わない通常要約・応答向けプロンプト。"""
    return (
        "あなたは日本語の要約・応答アシスタントです。\n"
        "与えられた指示に対して、簡潔で読みやすい日本語で回答してください。\n"
        "不要な前置きは省き、要求された内容に直接答えてください。\n"
        "出力言語は日本語のみです（固有名詞を除き英語の文は禁止）。\n"
        "逆質問はしないでください。\n\n"
        f"質問：\n{user_prompt}\n\n"
        "回答：\n"
    )


def _run_llama_with_preset(prompt: str, preset: str) -> str:
    """llama.cpp をプリセット設定で実行してテキストを返す。"""
    llm = get_llm()
    params = _LLAMA_PRESETS[preset]
    result = llm(
        prompt=prompt,
        max_tokens=int(params["max_tokens"]),
        temperature=float(params["temperature"]),
        top_k=int(params["top_k"]),
        repeat_penalty=float(params["repeat_penalty"]),
    )
    return str(result["choices"][0]["text"]).strip()


# ============================================================
# LLM シングルトン（遅延初期化）
# ============================================================
_llm: Llama | None = None
_embedder: GteEmbeddingModel | None = None


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
            n_ctx=8192,
            n_threads=8,
            n_gpu_layers=-1,
            verbose=False,
            use_mmap=True,
            use_mlock=False,
            n_batch=512,
        )
    return _llm


def get_embedder() -> GteEmbeddingModel:
    global _embedder
    if _embedder is None:
        _embedder = GteEmbeddingModel(
            model_name=_EMBEDDING_MODEL,
            batch_size=_EMBEDDING_BATCH_SIZE,
            max_length=_EMBEDDING_MAX_LENGTH,
        )
    return _embedder


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


def _sanitize_rag_answer_text(text: str) -> str:
    """RAG回答テキストのノイズ（EOS以降・重複段落）を除去する。"""
    cleaned = text.strip()
    if not cleaned:
        return ""

    eos_markers = ("<eos>", "</s>", "<|eot_id|>", "[END]")
    lower_cleaned = cleaned.lower()
    cut_index = len(cleaned)
    for marker in eos_markers:
        pos = lower_cleaned.find(marker.lower())
        if pos >= 0:
            cut_index = min(cut_index, pos)
    cleaned = cleaned[:cut_index].strip()

    if not cleaned:
        return ""

    # 冒頭に混入しやすい「質問文 + 回答」ヘッダを除去する。
    cleaned = re.sub(r"^.+?[？?]\s*\n+\s*回答\s*\n+", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"^回答\s*\n+", "", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return ""

    blocks = [b.strip() for b in re.split(r"\n{2,}", cleaned) if b.strip()]
    deduped_blocks: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        normalized = " ".join(block.split())
        if normalized in {"---", "*", "**"}:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped_blocks.append(block)

    return "\n\n".join(deduped_blocks).strip()


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

    text = _run_llama_with_preset(prompt, "doc_generation")

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
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
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
def summarize(prompt: str, mode: str = _SUMMARIZE_MODE_DEFAULT) -> str:
    """
    プロンプトを LLM に渡してテキストを返す（要約・質問応答用）。

    Args:
        prompt: LLM に渡すプロンプト文字列
        mode:   プロンプト/パラメータのプリセット
    """
    preset, wrap_prompt = _SUMMARIZE_MODE_MAP.get(
        mode, _SUMMARIZE_MODE_MAP[_SUMMARIZE_MODE_DEFAULT]
    )
    llm_prompt = _build_non_rag_prompt(prompt) if wrap_prompt else prompt
    return _run_llama_with_preset(llm_prompt, preset)


# ============================================================
# DB URL ヘルパー
# ============================================================
def get_db_url() -> str:
    load_dotenv(".env.local")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL 環境変数が設定されていません。"
            ".env.local を作成して DATABASE_URL を設定してください。"
        )
    return url


async def _is_documents_v2_empty(db_url: str) -> bool:
    """documents_v2 が空かどうかを返す。確認失敗時は False 扱い。"""
    import asyncpg

    try:
        conn = await asyncpg.connect(db_url, command_timeout=5)
        try:
            count = await conn.fetchval("SELECT COUNT(*) FROM documents_v2")
            return int(count or 0) == 0
        finally:
            await conn.close()
    except Exception:
        return False


def _extract_search_queries_rule_based(query: str) -> tuple[str, list[str], list[str]]:
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

    # 先頭クエリはユーザー質問を無加工でそのまま使う（Gemma への質問と同一）。
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


def _normalize_query_term(term: str) -> str:
    """検索クエリ用の語を正規化する。"""
    cleaned = " ".join(term.strip().split())
    return cleaned[:300]


def _apply_canonical_replacements(term: str) -> str:
    """通称をWikipedia見出しに寄せた正式名称へ置換する。"""
    normalized = term
    for short, variants in _JP_QUERY_CANONICAL_VARIANTS.items():
        # 置換後語の再置換（例: 内閣総理大臣 -> 内閣内閣総理大臣大臣）を防ぐ
        if not variants:
            continue
        if any(variant in term for variant in variants):
            continue
        if short in term:
            normalized = normalized.replace(short, variants[0])
    return _normalize_query_term(normalized)


def _expand_canonical_variants(terms: list[str]) -> list[str]:
    """クエリ候補へ正式名称バリエーションを追加する。"""
    expanded: list[str] = []
    seen: set[str] = set()

    def add_term(value: str) -> None:
        cleaned = _normalize_query_term(value)
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        expanded.append(cleaned)

    for term in terms:
        add_term(term)
        for short, variants in _JP_QUERY_CANONICAL_VARIANTS.items():
            if not variants:
                continue
            if short not in term:
                continue
            if any(variant in term for variant in variants):
                continue
            for variant in variants:
                add_term(term.replace(short, variant))

    return expanded


def _parse_json_object_from_text(text: str) -> dict | None:
    """モデル出力テキストからJSONオブジェクトを抽出する。"""
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _collect_grounding_tokens(query: str, seed_terms: list[str] | None = None) -> set[str]:
    """ユーザー質問に根拠のある語（+安全な展開語）を収集する。"""
    cleaned = _RE_INSTRUCTION_SUFFIX.sub("", query).strip() or query
    cleaned = _RE_DETAIL_SPEC.sub("", cleaned).strip() or cleaned
    lowered_cleaned = cleaned.lower()

    tokens: set[str] = set()

    for token in _RE_JP_KEYWORD.findall(cleaned):
        if len(token) >= 2 and token not in _GENERIC_SECONDARY_QUERIES:
            tokens.add(token)

    for token in _RE_LATIN_TOKEN.findall(cleaned):
        tokens.add(token.lower())
        tokens.add(token.capitalize())

    # 安全な正式名称展開（質問内に短縮語があるときのみ）
    for short, variants in _JP_QUERY_CANONICAL_VARIANTS.items():
        if short in cleaned:
            tokens.add(short)
            for variant in variants:
                tokens.add(variant)
                for part in _RE_GROUNDED_TOKEN.findall(variant):
                    tokens.add(part)

    for latin, variants in _LATIN_QUERY_VARIANTS.items():
        if latin in lowered_cleaned:
            tokens.add(latin)
            tokens.add(latin.capitalize())
            for variant in variants:
                tokens.add(variant)

    if seed_terms:
        for term in seed_terms:
            normalized = _normalize_query_term(term)
            if not normalized:
                continue
            tokens.add(normalized)
            for part in _RE_GROUNDED_TOKEN.findall(normalized):
                if len(part) >= 2 and part not in _GENERIC_SECONDARY_QUERIES:
                    tokens.add(part)

    return {token for token in tokens if token and len(token) >= 2}


def _is_grounded_term(term: str, grounding_tokens: set[str]) -> bool:
    """語が質問由来トークンに十分アンカーされているかを判定する。"""
    normalized = _normalize_query_term(term)
    if not normalized:
        return False

    lower_term = normalized.lower()
    if normalized in grounding_tokens or lower_term in grounding_tokens:
        return True

    parts = [p for p in _RE_GROUNDED_TOKEN.findall(normalized) if p not in _GENERIC_SECONDARY_QUERIES]
    if not parts:
        return False

    matched = 0
    for part in parts:
        lower_part = part.lower()
        if (
            part in grounding_tokens
            or lower_part in grounding_tokens
            or any(token in part for token in grounding_tokens)
        ):
            matched += 1

    # 一部一致のみで全体が乖離するケース（幻覚固有名詞の混入）を抑制
    return matched >= 1 and (matched / len(parts)) >= 0.5


def _sanitize_grounded_phrase(term: str, grounding_tokens: set[str]) -> str:
    """空白区切り語を質問根拠ベースで間引きし、不適切な尾部追加を除去する。"""
    normalized = _normalize_query_term(term)
    if not normalized:
        return ""

    chunks = [chunk for chunk in normalized.split(" ") if chunk]
    if len(chunks) <= 1:
        return normalized if _is_grounded_term(normalized, grounding_tokens) else ""

    kept = [chunk for chunk in chunks if _is_grounded_term(chunk, grounding_tokens)]
    if not kept:
        return ""
    return _normalize_query_term(" ".join(kept))


def _ensure_primary_query_first(
    query: str,
    vector_queries: list[str],
    search_base: str,
) -> list[str]:
    """主軸ベクトル検索は必ずユーザー原文を先頭にする。"""
    normalized_query = _normalize_query_term(query)
    out: list[str] = []
    seen: set[str] = set()

    def add_term(value: str) -> None:
        term = _normalize_query_term(value)
        if not term or term in seen:
            return
        seen.add(term)
        out.append(term)

    add_term(normalized_query)
    add_term(search_base)
    for term in vector_queries:
        add_term(term)

    return out[:6]


def _title_boundary_match(title: str, noun: str) -> bool:
    """タイトル内で noun が単語境界に近い形で一致するかを判定する。"""
    if not noun:
        return False
    pattern = _RE_TITLE_BOUNDARY_TEMPLATE % re.escape(noun)
    return bool(re.search(pattern, title))


def _contains_noisy_affix(title: str, noun: str) -> bool:
    """`noun` が他語の一部として連結されるノイズ一致を検出する。"""
    if not noun or noun not in title:
        return False
    for idx in range(len(title)):
        pos = title.find(noun, idx)
        if pos == -1:
            break
        end = pos + len(noun)
        prev_char = title[pos - 1] if pos > 0 else ""
        next_char = title[end] if end < len(title) else ""
        prev_is_boundary = (not prev_char) or bool(
            re.match(r"[\s\-・/()\[\]{}（）「」『』]", prev_char)
        )
        next_is_boundary = (not next_char) or bool(
            re.match(r"[\s\-・/()\[\]{}（）「」『』]", next_char)
        )
        if not (prev_is_boundary or next_is_boundary):
            return True
        idx = pos + 1
    return False


def _score_title_match(title: str, noun: str) -> float:
    """タイトル一致の質をスコア化する。部分一致ノイズを下げる。"""
    if not noun:
        return 0.0

    score = 0.0
    if title == noun:
        score += 200.0
    elif title == f"{noun}一覧":
        score += 190.0
    elif _title_boundary_match(title, noun):
        score += 150.0
    elif noun in title:
        score += 85.0

    if title.startswith(noun):
        score += 16.0
    if title.endswith(noun):
        score += 16.0

    if _contains_noisy_affix(title, noun):
        score -= 55.0

    if any(sym in title for sym in ("!", "！", "?", "？")):
        score -= 12.0

    return score


async def _extract_search_queries_with_gemma(query: str) -> tuple[str, list[str], list[str]]:
    """Gemma で質問整形を行い、検索クエリ候補を返す。"""
    prompt = (
        "あなたはWikipedia検索用のクエリ整形アシスタントです。\n"
        "ユーザー質問(下記の今回のWikipedia検索用のクエリ整形対象の質問:の部分)を、意味を変えずに検索しやすい形へ整形してください。\n"
        "出力は必ずJSONオブジェクトのみで返してください。\n\n"
        "要件:\n"
        "- search_base: 質問の要点を保った短い検索文（日本語、120文字以内）\n"
        "- vector_queries: ベクトル検索用クエリ配列\n"
        "- title_queries: タイトル一致検索向け配列\n"
        "- vector_queries/title_queriesに質問に含まれる固有名詞と、その同義語・正式名称をできるだけ入れる。通称・略称は正式名称へ正規化したものも入れる（例: 首相→内閣総理大臣）\n"
        "- 文字数指定や『詳しく』『教えて』などの依頼語は除去する\n"
        "JSONスキーマ:\n"
        '{"search_base":"string","vector_queries":["string"],"title_queries":["string"]}\n\n'
        f"今回のWikipedia検索用のクエリ整形対象の質問: {query}\n"
    )

    async def call_normalizer(num_predict: int) -> tuple[str, str]:
        def _run() -> tuple[str, str]:
            llm = get_llm()
            result = llm(
                prompt=prompt,
                max_tokens=num_predict,
                temperature=0.0,
                top_k=20,
                repeat_penalty=1.0,
            )
            choice = result["choices"][0]
            raw_text = str(choice.get("text", "")).strip()
            done_reason = str(choice.get("finish_reason", ""))
            return raw_text, done_reason

        return await asyncio.wait_for(
            asyncio.to_thread(_run),
            timeout=_QUERY_NORMALIZATION_TIMEOUT_SEC,
        )

    raw, done_reason = await call_normalizer(_QUERY_NORMALIZATION_NUM_PREDICT)
    parsed = _parse_json_object_from_text(raw)
    if not parsed and done_reason in {"length", "max_tokens"}:
        # 生成打ち切り時のみ1回だけ拡張リトライする
        retry_num_predict = max(_QUERY_NORMALIZATION_NUM_PREDICT * 2, 256)
        print(
            f"[_extract_search_queries_with_gemma] retry due to truncated output: "
            f"done_reason={done_reason} num_predict={_QUERY_NORMALIZATION_NUM_PREDICT} -> {retry_num_predict}",
            file=sys.stderr,
        )
        raw, done_reason = await call_normalizer(retry_num_predict)
        parsed = _parse_json_object_from_text(raw)

    if not parsed:
        preview = raw[:220].replace("\n", "\\n")
        raise ValueError(
            "Gemma query normalization returned non-JSON output "
            f"(done_reason={done_reason}, preview={preview!r})"
        )

    rb_search_base, rb_vector_queries, rb_title_queries = _extract_search_queries_rule_based(query)
    grounding_tokens = _collect_grounding_tokens(query, [rb_search_base, *rb_vector_queries, *rb_title_queries])

    search_base_raw = str(parsed.get("search_base", "")).strip()
    search_base = _normalize_query_term(search_base_raw) or _normalize_query_term(query)
    search_base = _apply_canonical_replacements(search_base)
    search_base = _sanitize_grounded_phrase(search_base, grounding_tokens) or _normalize_query_term(
        rb_search_base
    )

    vector_raw = parsed.get("vector_queries")
    title_raw = parsed.get("title_queries")
    vector_candidates = vector_raw if isinstance(vector_raw, list) else []
    title_candidates = title_raw if isinstance(title_raw, list) else []

    vector_queries: list[str] = []
    vector_seed = _expand_canonical_variants([search_base, *vector_candidates, query])
    for candidate in vector_seed:
        if not isinstance(candidate, str):
            continue
        term = _sanitize_grounded_phrase(candidate, grounding_tokens)
        if not term:
            continue
        if not _is_grounded_term(term, grounding_tokens):
            continue
        if term not in vector_queries:
            vector_queries.append(term)
        if len(vector_queries) >= 6:
            break

    for candidate in rb_vector_queries:
        if len(vector_queries) >= 6:
            break
        term = _normalize_query_term(candidate)
        if not term or term in vector_queries:
            continue
        vector_queries.append(term)

    title_queries: list[str] = []
    title_seed = _expand_canonical_variants([search_base, *title_candidates])
    for candidate in title_seed:
        if not isinstance(candidate, str):
            continue
        term = _sanitize_grounded_phrase(candidate, grounding_tokens)
        if not term:
            continue
        if not _is_grounded_term(term, grounding_tokens):
            continue
        if term in title_queries:
            continue
        title_queries.append(term)
        if len(title_queries) >= 6:
            break

    for candidate in rb_title_queries:
        if len(title_queries) >= 6:
            break
        term = _normalize_query_term(candidate)
        if not term or term in title_queries:
            continue
        title_queries.append(term)

    if not vector_queries:
        vector_queries = [search_base or _normalize_query_term(rb_search_base)]

    vector_queries = _ensure_primary_query_first(query, vector_queries, search_base)

    return search_base, vector_queries, title_queries


async def _extract_search_queries(query: str) -> tuple[str, list[str], list[str], str]:
    """検索クエリ抽出。Gemmaを優先し、失敗時はルールベースへフォールバックする。"""
    try:
        extracted = await _extract_search_queries_with_gemma(query)
        print("[_extract_search_queries] mode=gemma_json", file=sys.stderr)
        return extracted[0], extracted[1], extracted[2], "gemma_json"
    except Exception as exc:  # noqa: BLE001
        print(f"[_extract_search_queries] fallback to rule-based: {exc}", file=sys.stderr)
        extracted = _extract_search_queries_rule_based(query)
        print("[_extract_search_queries] mode=rule_based_fallback", file=sys.stderr)
        return extracted[0], extracted[1], extracted[2], "rule_based_fallback"


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
        add_docs(docs, base=72.0, decay=8.0)

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


def _collect_relevance_terms(query: str, sub_queries: list[str]) -> list[str]:
    """質問との語彙一致で再ランキングするための語を抽出する。"""
    terms: list[str] = []
    seen: set[str] = set()
    sources = [query, *sub_queries]

    for source in sources:
        normalized = _normalize_query_term(source)
        if not normalized:
            continue

        # 日本語の連結語（例: 北海道の観光名所）を短い検索語へ分解する。
        for token in _RE_JP_KEYWORD.findall(normalized):
            if token in _GENERIC_SECONDARY_QUERIES:
                continue
            if len(token) < 2 and token not in _SINGLE_KANJI_SET:
                continue
            if token in seen:
                continue
            seen.add(token)
            terms.append(token)
            if len(terms) >= 18:
                return terms

        for token in _RE_GROUNDED_TOKEN.findall(normalized):
            if token in _GENERIC_SECONDARY_QUERIES:
                continue
            if len(token) < 2 and token not in _SINGLE_KANJI_SET:
                continue
            if token in seen:
                continue
            seen.add(token)
            terms.append(token)
            if len(terms) >= 18:
                return terms
    return terms


def _is_tourism_intent_query(text: str) -> bool:
    """観光スポット系の質問かどうかを判定する。"""
    return any(
        keyword in text
        for keyword in ("観光", "名所", "観光地", "イベント", "祭", "祭り", "春", "花見")
    )


def _select_context_snippet(body: str, max_chars: int = 220) -> str:
    """本文から短い説明スニペットを抽出する。"""
    normalized = " ".join(body.replace("\n", " ").split()).strip()
    if not normalized:
        return ""

    for sentence in re.split(r"(?<=[。！？!?])\s+", normalized):
        candidate = sentence.strip()
        if len(candidate) >= 24:
            return candidate[:max_chars].rstrip("。") + "。"

    return normalized[:max_chars].rstrip("。") + "。"


def _answer_has_context_signals(answer: str, context_sources: list[tuple[str, str]]) -> bool:
    """回答文が参照コンテキスト由来の語を十分に含むかを判定する。"""
    if len(answer.strip()) < 80:
        return False

    title_terms: set[str] = set()
    for title, _ in context_sources:
        for token in _RE_JP_KEYWORD.findall(title):
            if len(token) >= 2 and token not in _GENERIC_SECONDARY_QUERIES:
                title_terms.add(token)
        for token in _RE_LATIN_TOKEN.findall(title):
            if len(token) >= 2:
                title_terms.add(token)

    if not title_terms:
        return False

    matched_terms = [term for term in title_terms if term in answer]
    return len(matched_terms) >= 2


def _build_grounded_fallback_answer(query: str, context_sources: list[tuple[str, str]]) -> str:
    """モデル出力が弱い場合に、参照コンテキストベースで回答を再構成する。"""
    subject = _extract_subject(query)
    sections: list[str] = [f"{subject}について、参照資料から確認できる要点は次のとおりです。"]

    for title, body in context_sources:
        snippet = _select_context_snippet(body)
        if not snippet:
            continue
        sections.append(f"{title}では、{snippet}")

    return "\n\n".join(sections).strip()


def _build_prime_minister_tenure_answer(
    query: str,
    context_sources: list[tuple[str, str]],
) -> str:
    """首相の在任期間ランキング質問に対し、参照本文から候補を抽出して回答を構築する。"""
    if not (
        "首相" in query
        and "任期" in query
        and "長い順" in query
    ):
        return ""

    top_n = 5
    m = re.search(r"(\d{1,2})人", query)
    if m:
        top_n = max(1, min(10, int(m.group(1))))

    # 例: 森喜朗2000年 ... 2001年 のような表記を抽出する
    pattern = re.compile(
        r"([\u4e00-\u9fff々〆ヵヶぁ-んァ-ヴー]{2,20})\s*([12][0-9]{3})年[\s\S]{0,64}?([12][0-9]{3})年"
    )
    candidates: dict[str, tuple[int, int, int]] = {}

    def normalize_pm_name(raw: str) -> str:
        name = raw.strip()
        # 画像サイズやテンプレート断片、代数表記を先頭から除去する。
        name = re.sub(r"^(?:[0-9]{2,4}x[0-9]{2,4}|[0-9]{2,4}px|[0-9]+ピクセル|frameless|upright=0\.[0-9]+|第[0-9\-]+代)+", "", name)
        name = re.sub(r"^(?:第?[0-9一二三四五六七八九十\-]*次)?内閣", "", name)
        name = re.sub(r"^次内閣", "", name)
        name = re.sub(r"^(?:第[0-9\-]+代)", "", name)
        name = re.sub(r"^(?:ピクセル|px)+", "", name)
        name = name.strip()
        if not re.fullmatch(r"[\u4e00-\u9fff々〆ヵヶぁ-んァ-ヴー]{2,20}", name):
            return ""
        return name

    for _, body in context_sources:
        for raw_name, start_s, end_s in pattern.findall(body):
            name = normalize_pm_name(raw_name)
            if not name:
                continue
            start_y = int(start_s)
            end_y = int(end_s)
            if end_y < start_y:
                continue
            duration = end_y - start_y + 1
            previous = candidates.get(name)
            if previous is None or duration > previous[0]:
                candidates[name] = (duration, start_y, end_y)

    if not candidates:
        return ""

    ranked = sorted(
        candidates.items(),
        key=lambda item: (item[1][0], item[1][2], item[1][1]),
        reverse=True,
    )[:top_n]

    lines = [
        f"参照資料から抽出できた範囲で、日本の歴代首相を任期が長い順に{top_n}人示します。",
        "",
    ]
    for idx, (name, (duration, start_y, end_y)) in enumerate(ranked, start=1):
        lines.append(f"{idx}. {name}（{start_y}年 - {end_y}年、約{duration}年）")

    lines.append("")
    lines.append("注記: 上記は今回取得した参照本文から機械抽出した結果です。")
    return "\n".join(lines).strip()


def _rerank_docs_by_query_relevance(docs: list[dict], query: str, sub_queries: list[str]) -> list[dict]:
    """ベクトル順位に語彙一致シグナルを加味して文脈候補を再整列する。"""
    if not docs:
        return docs

    terms = _collect_relevance_terms(query, sub_queries)
    if not terms:
        return docs

    def lexical_score(doc: dict) -> float:
        title = str(doc.get("title", ""))
        content_head = str(doc.get("content", ""))[:2400]
        score = 0.0
        for term in terms:
            if term in title:
                score += 26.0 + min(len(term), 10)
            elif term in content_head:
                score += 6.0
        return score

    scored: list[tuple[float, int, dict]] = []
    for rank, doc in enumerate(docs):
        score = max(0.0, 24.0 - rank * 1.2) + lexical_score(doc)

        scored.append((score, -rank, doc))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored]


def _is_low_relevance_top_doc(doc: dict | None, relevance_terms: list[str], term_limit: int = 12) -> bool:
    """上位ドキュメントが質問に対して低関連かを判定する。"""
    if doc is None:
        return True

    title = str(doc.get("title", ""))
    content_head = str(doc.get("content", ""))[:2400]
    title_hits = sum(1 for term in relevance_terms[:term_limit] if term in title)
    content_hits = sum(1 for term in relevance_terms[:term_limit] if term in content_head)

    # タイトルに1語でも一致すれば関連ありとみなす。
    if title_hits >= 1:
        return False
    # タイトル一致がなくても本文に十分な一致があれば関連あり。
    if content_hits >= 3:
        return False

    return True


async def _retrieve_rag_docs(
    query: str,
    db_url: str,
    force_rule_based: bool = False,
    vector_query_limit: int | None = None,
) -> tuple[list[dict], list[str], list[list[dict]], str, list[dict]]:
    """RAG 用の検索を実行し、統合済み候補を返す。"""
    import asyncpg

    if force_rule_based:
        _, all_vector_queries, all_title_queries = _extract_search_queries_rule_based(query)
        extraction_mode = "rule_based_fast"
    else:
        _, all_vector_queries, all_title_queries, extraction_mode = await _extract_search_queries(query)
    latin_terms = list(dict.fromkeys([token.lower() for token in _RE_LATIN_TOKEN.findall(query)]))
    is_tourism_query = _is_tourism_intent_query(query)
    subject_terms = set(re.findall(r"の([^の\s、。]{1,20})について", query))

    def extract_parallel_subject_terms(text: str) -> list[str]:
        cleaned = _RE_DETAIL_SPEC.sub("", text).strip("。 \t\n")
        m = re.search(r"(.+?)について", cleaned)
        base = m.group(1) if m else cleaned
        pieces = re.split(r"(?:と|及び|および|・|/|／)", base)
        terms: list[str] = []
        seen: set[str] = set()
        for piece in pieces:
            token = piece.strip()
            token = _RE_DETAIL_SPEC.sub("", token)
            token = re.sub(r"の違い(?:.*)$", "", token)
            token = re.sub(r"(?:を|は|が|について)$", "", token).strip()
            token = re.sub(r"(?:を)?(?:説明|解説|比較|紹介|教えて)(?:して|してください)?$", "", token).strip()
            if len(token) < 2:
                continue
            if token in _GENERIC_SECONDARY_QUERIES:
                continue
            if token in seen:
                continue
            seen.add(token)
            terms.append(token)
        return terms[:4]

    parallel_subject_terms = extract_parallel_subject_terms(query)

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
    elif is_tourism_query:
        for candidate in all_vector_queries[1:]:
            if any(k in candidate for k in ("観光", "イベント", "祭", "祭り")):
                vector_queries = [all_vector_queries[0], candidate]
                break

    if parallel_subject_terms:
        for term in _expand_canonical_variants(parallel_subject_terms):
            if term and term not in vector_queries:
                vector_queries.append(term)
            if len(vector_queries) >= 4:
                break

    if vector_query_limit is not None:
        limited = max(1, int(vector_query_limit))
        if is_tourism_query:
            # 観光系は地名だけだと誤近傍になりやすいため、補助クエリを最低1本追加する。
            limited = max(2, limited)
        vector_queries = vector_queries[:limited]

    title_candidates = list(dict.fromkeys(all_title_queries))

    # ユーザー質問の「XのYについて」から Y を主題語として抽出し、
    # 正式名称バリエーション（例: 狸小路 -> 狸小路商店街）を優先候補へ追加する。
    query_title_terms = []
    for term in re.findall(r"の([^の\s、。]{1,20})について", query):
        query_title_terms.append(term)
    for term in re.findall(r"([^の\s、。]{1,20})の", query):
        query_title_terms.append(term)
    query_title_terms = [
        t for t in query_title_terms if t and t not in _GENERIC_SECONDARY_QUERIES and len(t) >= 2
    ]
    query_title_terms = list(dict.fromkeys(query_title_terms + parallel_subject_terms))
    query_title_terms = _expand_canonical_variants(query_title_terms)
    title_candidates = list(dict.fromkeys(query_title_terms + title_candidates))

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
    parallel_subject_terms_prioritized = _expand_canonical_variants(parallel_subject_terms)
    subject_terms_prioritized = _expand_canonical_variants(
        list(dict.fromkeys(sorted(subject_terms) + parallel_subject_terms))
    )
    subject_terms_expanded = set(subject_terms_prioritized)

    def title_priority(term: str) -> tuple[int, int]:
        score = 0
        if any(k in term for k in ("観光", "名所", "イベント", "祭", "桜", "春")):
            score += 30
        if subject_terms_expanded and any(st and st in term for st in subject_terms_expanded):
            score += 70
        if len(term) > 18:
            score -= 35
        if any(k in term for k in ("概要", "歴史", "機能", "使い方", "解説")):
            score -= 30
        if " " in term:
            score += 8
        if term in location_heads:
            score += 45
        if term.startswith("特に"):
            score -= 20
        if term in _GENERIC_SECONDARY_QUERIES:
            score -= 20
        # 同点時は短い見出し語を優先する。
        return score, -len(term)

    title_candidates.sort(key=title_priority, reverse=True)

    # 英字固有名詞はタイトル一致検索の強シグナルなので、優先候補から落とさない。
    # 例: "firebaseを説明して" では Firebase を必ず title_search に流す。
    title_queries: list[str] = []
    if latin_terms:
        for token in latin_terms[:2]:
            for variant in (token, token.capitalize()):
                if variant not in title_queries:
                    title_queries.append(variant)

    preferred_subject_candidates = [
        term for term in subject_terms_prioritized if term and term not in _GENERIC_SECONDARY_QUERIES
    ]
    for term in parallel_subject_terms_prioritized:
        if term and term not in _GENERIC_SECONDARY_QUERIES and term not in preferred_subject_candidates:
            preferred_subject_candidates.append(term)
    merged_title_candidates = list(dict.fromkeys(preferred_subject_candidates + title_candidates))

    for candidate in merged_title_candidates:
        if candidate in title_queries:
            continue
        title_queries.append(candidate)

    title_queries = title_queries[:4] if (latin_terms or parallel_subject_terms) else title_queries[:2]

    # 観光系クエリでは地名単体の前方一致（例: 北海道%）がノイズ化しやすいため除外する。
    if is_tourism_query and title_queries:
        filtered_title_queries: list[str] = []
        for candidate in title_queries:
            has_intent_token = any(
                keyword in candidate
                for keyword in ("観光", "名所", "観光地", "イベント", "祭", "祭り")
            )
            if candidate in location_heads and not has_intent_token:
                continue
            filtered_title_queries.append(candidate)
        title_queries = filtered_title_queries

    # アンカー語は検索に使わない分も保持して再ランキングで活用
    anchor_terms = [q for q in all_vector_queries[1:4] if q]
    for tq in title_queries:
        if tq and tq not in anchor_terms:
            anchor_terms.append(tq)

    if is_tourism_query and anchor_terms:
        filtered_anchor_terms = []
        for term in anchor_terms:
            has_intent_token = any(
                keyword in term
                for keyword in ("観光", "名所", "観光地", "イベント", "祭", "祭り")
            )
            if term in location_heads and not has_intent_token:
                continue
            filtered_anchor_terms.append(term)
        if filtered_anchor_terms:
            anchor_terms = filtered_anchor_terms

    async def embed_one(text: str) -> list[float]:
        cached = _lru_get(_embed_cache, text)
        if cached is not None:
            return cached

        def _run_gte() -> list[float]:
            return get_embedder().embed_sync([text])[0]

        embedding = await asyncio.to_thread(_run_gte)
        _lru_set(_embed_cache, text, embedding, _EMBED_CACHE_MAX)
        return embedding

    async def search_one(pool: "asyncpg.Pool", emb: list[float]) -> list[dict]:
        emb_str = "[" + ",".join(map(str, emb)) + "]"
        match_count = int(os.environ.get("RAG_VECTOR_MATCH_COUNT", "8"))
        oversampling = int(os.environ.get("RAG_VECTOR_OVERSAMPLING", "24"))
        if match_count < 1:
            match_count = 1
        if oversampling < match_count:
            oversampling = match_count
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH ann AS (
                    SELECT
                        d.article_id,
                        d.chunk_index,
                        d.title,
                        d.content,
                        (d.embedding <=> $1::vector) AS dist
                    FROM documents_v2 d
                    ORDER BY d.embedding <=> $1::vector
                    LIMIT $2::int
                )
                SELECT picked.article_id AS id, picked.title, picked.content
                FROM (
                    SELECT DISTINCT ON (ann.article_id)
                        ann.article_id,
                        ann.title,
                        ann.content,
                        ann.dist
                    FROM ann
                    ORDER BY ann.article_id, ann.dist ASC
                ) AS picked
                ORDER BY picked.dist ASC
                LIMIT $3::int
                """,
                emb_str,
                oversampling,
                match_count,
            )
        return [{"id": r["id"], "title": r["title"], "content": r["content"]} for r in rows]

    async def title_search(pool: "asyncpg.Pool", noun: str) -> list[dict]:
        normalized_noun = _normalize_query_term(noun)
        if not normalized_noun:
            return []

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                                WITH candidates AS (
                                    SELECT article_id, title, content, chunk_index
                                    FROM (
                                        SELECT article_id, title, content, chunk_index
                                        FROM documents_v2
                                        WHERE title = $1
                                        LIMIT 16
                                    ) t1

                                    UNION ALL

                                    SELECT article_id, title, content, chunk_index
                                    FROM (
                                        SELECT article_id, title, content, chunk_index
                                        FROM documents_v2
                                        WHERE title = $2
                                        LIMIT 16
                                    ) t2

                                    UNION ALL

                                    SELECT article_id, title, content, chunk_index
                                    FROM (
                                        SELECT article_id, title, content, chunk_index
                                        FROM documents_v2
                                        WHERE title LIKE $3
                                        LIMIT 32
                                    ) t3
                                )
                                SELECT DISTINCT ON (article_id)
                                    article_id AS id,
                                    title,
                                    content
                                FROM candidates
                                ORDER BY article_id, chunk_index ASC
                                LIMIT 48
                """,
                f"{normalized_noun}一覧",
                normalized_noun,
                f"{normalized_noun}%",
            )
        docs = [{"id": r["id"], "title": r["title"], "content": r["content"]} for r in rows]
        scored_docs: list[tuple[float, int, dict]] = []
        for doc in docs:
            title = str(doc["title"])
            score = _score_title_match(title, normalized_noun)
            scored_docs.append((score, -len(title), doc))

        scored_docs.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored_docs[:10]]

    async def tourism_title_search(pool: "asyncpg.Pool", location: str) -> list[dict]:
        normalized_location = _normalize_query_term(location)
        if not normalized_location:
            return []

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH candidates AS (
                    SELECT article_id, title, content, chunk_index
                    FROM documents_v2
                    WHERE title LIKE $1
                      AND (
                        title LIKE '%観光%'
                        OR title LIKE '%名所%'
                        OR title LIKE '%イベント%'
                        OR title LIKE '%祭%'
                      )
                    LIMIT 96
                )
                SELECT DISTINCT ON (article_id)
                    article_id AS id,
                    title,
                    content
                FROM candidates
                ORDER BY article_id, chunk_index ASC
                LIMIT 24
                """,
                f"{normalized_location}%",
            )

        return [{"id": r["id"], "title": r["title"], "content": r["content"]} for r in rows]

    async def wrapped_search(task_type: str, coro: "asyncio.Future[list[dict]]") -> tuple[str, list[dict] | None, Exception | None]:
        try:
            result = await coro
            return task_type, result, None
        except Exception as exc:  # noqa: BLE001
            return task_type, None, exc

    import asyncio as _asyncio
    embs = await _asyncio.gather(*[embed_one(q) for q in vector_queries])

    import sys as _sys
    import asyncio as _asyncio

    max_concurrency = int(os.environ.get("RAG_DB_MAX_CONCURRENCY", "4"))
    query_timeout_s = float(os.environ.get("RAG_DB_QUERY_TIMEOUT_SEC", "25"))
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
        if is_tourism_query:
            for location in list(location_heads)[:2]:
                tasks.append(wrapped_search("tourism_title", limited(tourism_title_search(pool, location))))

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

    rerank_queries = list(dict.fromkeys([*all_vector_queries, *title_queries]))
    docs = _rerank_docs_by_query_relevance(docs, query, rerank_queries)

    relevance_terms = _collect_relevance_terms(query, rerank_queries)
    if relevance_terms:
        top_doc = docs[0] if docs else None
        low_relevance = _is_low_relevance_top_doc(top_doc, relevance_terms)

        # 追加の重いDB検索は行わず、既存候補だけで再ランキングする。
        if low_relevance and docs:
            docs = _rerank_docs_by_query_relevance(docs, query, rerank_queries)

    ranked_top20_docs = docs[:20]

    # Gemma 入力は最大 3 件（ランキング上位を優先）
    return docs[:3], vector_queries, title_lists, extraction_mode, ranked_top20_docs


def _parse_selected_doc_ids(raw: str) -> list[int]:
    """CLIから渡された選択ID文字列を数値配列へ変換する。"""
    tokens = [token for token in re.split(r"[\s,]+", (raw or "").strip()) if token]
    selected: list[int] = []
    seen: set[int] = set()
    for token in tokens:
        if not token.isdigit():
            continue
        value = int(token)
        if value in seen:
            continue
        seen.add(value)
        selected.append(value)
    return selected


@mcp.tool()
async def rag_rankings(query: str) -> str:
    """質問に対するRAG検索ランキング上位20件をJSONで返す。"""
    db_url: str = get_db_url()
    use_rule_based = os.environ.get("RAG_REPORT_USE_RULE_BASED_EXTRACTOR", "0") != "0"
    report_vector_query_limit = int(os.environ.get("RAG_REPORT_VECTOR_QUERY_LIMIT", "1"))
    _, sub_queries, _, extraction_mode, ranked_top20_docs = await _retrieve_rag_docs(
        query,
        db_url,
        force_rule_based=use_rule_based,
        vector_query_limit=report_vector_query_limit,
    )
    payload = {
        "query": query,
        "extraction_mode": extraction_mode,
        "search_queries": sub_queries,
        "rankings": [
            {
                "rank": idx,
                "id": int(doc["id"]),
                "title": str(doc["title"]),
                "content_length": len(str(doc.get("content", ""))),
            }
            for idx, doc in enumerate(ranked_top20_docs, start=1)
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
async def rag_answer_report(query: str, top_k: int = 3) -> str:
    """質問に対するRAG回答と計測情報をJSONで返す（top_kは最大3）。"""
    db_url: str = get_db_url()
    effective_top_k = 3 if top_k <= 0 else min(int(top_k), 3)
    use_rule_based = os.environ.get("RAG_REPORT_USE_RULE_BASED_EXTRACTOR", "0") != "0"
    report_vector_query_limit = int(os.environ.get("RAG_REPORT_VECTOR_QUERY_LIMIT", "1"))

    search_started = time.perf_counter()
    docs, sub_queries, _, extraction_mode, ranked_top20_docs = await _retrieve_rag_docs(
        query,
        db_url,
        force_rule_based=use_rule_based,
        vector_query_limit=report_vector_query_limit,
    )
    relevance_terms = _collect_relevance_terms(query, sub_queries)

    docs = _rerank_docs_by_query_relevance(docs, query, sub_queries)
    ranked_top20_docs = _rerank_docs_by_query_relevance(ranked_top20_docs, query, sub_queries)

    # 質問との語彙一致が弱い場合でも、重い追加DB検索は行わず既存候補で再整列する。
    low_relevance = False
    if relevance_terms:
        top_doc = docs[0] if docs else None
        low_relevance = _is_low_relevance_top_doc(top_doc, relevance_terms, term_limit=10)

        if low_relevance and docs:
            docs = _rerank_docs_by_query_relevance(docs, query, sub_queries)
            ranked_top20_docs = docs[:20]

    search_time_ms = int((time.perf_counter() - search_started) * 1000)

    docs = docs[:effective_top_k]
    content_preview_chars = int(os.environ.get("RAG_REPORT_CONTENT_PREVIEW_CHARS", "2000"))
    top_docs_payload = [
        {
            "rank": idx,
            "id": int(doc["id"]),
            "title": str(doc["title"]),
            "content": str(doc.get("content", ""))[:content_preview_chars],
            "content_length": len(str(doc.get("content", ""))),
        }
        for idx, doc in enumerate(docs, start=1)
    ]

    answer_started = time.perf_counter()
    answer_error = ""
    context_chunk_sizes: list[dict[str, int | str]] = []
    max_context_chars = int(os.environ.get("RAG_REPORT_MAX_CONTEXT_CHARS", "10000"))
    llm_preset_name = "qa_rag"
    llm_preset = _LLAMA_PRESETS.get(llm_preset_name, {})
    db_empty = False
    if not docs:
        db_empty = await _is_documents_v2_empty(db_url)

    if not docs:
        if db_empty:
            answer = (
                "関連する情報が見つかりませんでした。"
                "原因候補: documents_v2 テーブルが空です。"
                "データ投入または復元を実施してください。"
            )
            answer_error = "documents_v2 is empty"
        else:
            answer = "関連する情報が見つかりませんでした。"
    elif low_relevance:
        answer = (
            "検索結果に質問と直接対応する根拠が不足しているため、回答生成をスキップしました。"
            "検索語やデータ品質を見直してください。"
        )
    else:
        used = 0
        context_sources: list[tuple[str, str]] = []
        for doc in docs:
            title = str(doc["title"])
            body = str(doc.get("content", ""))
            if not body:
                continue
            remain = max_context_chars - used
            if remain <= 0:
                break
            piece = body[:remain]
            if not piece:
                continue
            context_sources.append((title, piece))
            used += len(piece)

        if not context_sources:
            answer = "関連する情報が見つかりませんでした。"
        else:
            context = "\n\n".join([f"【{title}】\n{body}" for title, body in context_sources])
            context_chunk_sizes = [
                {
                    "rank": idx,
                    "title": title,
                    "chunk_chars": len(body),
                }
                for idx, (title, body) in enumerate(context_sources, start=1)
            ]
            rag_messages = _build_rag_messages(context, query)
            prefill = rag_messages[2]["content"]
            llm_prompt = (
                f"{rag_messages[0]['content']}\n\n"
                f"{rag_messages[1]['content']}\n\n"
                f"{prefill}"
            )
            try:
                llm_text = await asyncio.to_thread(_run_llama_with_preset, llm_prompt, llm_preset_name)
                answer = _sanitize_rag_answer_text(prefill + llm_text.strip())
                if not answer:
                    answer = "回答を生成できませんでした。"
                elif not _answer_has_context_signals(answer, context_sources):
                    fallback_answer = _build_grounded_fallback_answer(query, context_sources)
                    if fallback_answer:
                        answer = fallback_answer
            except Exception as exc:  # noqa: BLE001
                answer_error = str(exc)
                answer = (
                    "回答生成に失敗しました。"
                    "モデルパスや実行パラメータを確認してください。"
                )

    answer_time_ms = int((time.perf_counter() - answer_started) * 1000)
    total_time_ms = search_time_ms + answer_time_ms
    now_jst = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")

    payload = {
        "query": query,
        "top_k": effective_top_k,
        "generated_at": now_jst,
        "extraction_mode": extraction_mode,
        "search_queries": sub_queries,
        "search_time_ms": search_time_ms,
        "answer_time_ms": answer_time_ms,
        "total_time_ms": total_time_ms,
        "answer_error": answer_error,
        "answer": answer,
        "runtime_parameters": {
            "model_path": MODEL_PATH,
            "llm_preset": llm_preset_name,
            "max_context_chars": max_context_chars,
            "content_preview_chars": content_preview_chars,
            "effective_top_k": effective_top_k,
            "llm_params": {
                "max_tokens": int(llm_preset.get("max_tokens", 0)),
                "temperature": float(llm_preset.get("temperature", 0.0)),
                "top_k": int(llm_preset.get("top_k", 0)),
                "repeat_penalty": float(llm_preset.get("repeat_penalty", 0.0)),
            },
        },
        "context_chunk_sizes": context_chunk_sizes,
        "top_docs": top_docs_payload,
        "ranked_sources": [
            {
                "rank": idx,
                "id": int(doc["id"]),
                "title": str(doc["title"]),
                "content_length": len(str(doc.get("content", ""))),
            }
            for idx, doc in enumerate(ranked_top20_docs, start=1)
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _build_rag_messages(context: str, user_prompt: str) -> list[dict[str, str]]:
    """Gemma chat API 用の system/user/assistant(先行注入) メッセージを構築する。"""
    system_msg = (
        "あなたは日本語の解説ライターです。"
        "ユーザーの質問に対して、提供された参照資料の情報を十分に活用し、"
        "詳しく読みやすい日本語の解説文を作成してください。\n"
        "ルール:\n"
        "- 出力は解説本文のみ。採点・講評・称賛・批評・メタコメントは一切禁止。\n"
        "- 出力言語は日本語のみ（固有名詞を除き英語文禁止）。\n"
        "- 参照資料の情報を最大限引用・活用して回答すること。資料に豊富な情報がある場合は省略せず詳細に記述。\n"
        "- 文字数指定がある場合は必ずその文字数を目安に記述すること。沿革・組織・特色・研究など複数の観点から網羅的に解説。\n"
        "- 箇条書き中心ではなく、段落形式の説明文で記述。\n"
        "- 参照資料のタイトルに含まれる固有名詞を、回答本文に明示的に含めること。\n"
        "- 逆質問や前置きは不要。\n"
        "- 数学に関しての回答は参照資料にある公式と用語を使うこと。\n"
        "- 参照資料に含まれない事実を創作しないこと。"
    )
    # コンテキストを先に配置し、質問を末尾に置く。
    # 質問が生成開始位置に近いほど Gemma の注意が質問に向きやすい。
    user_msg = (
        f"以下は参照資料です。\n\n"
        f"{context}\n\n"
        f"上記の参照資料の情報を十分に使い、次の質問に詳しく回答してください。\n\n"
        f"質問: {user_prompt}"
    )
    # 質問から主題を抽出して assistant prefill を生成する。
    # 解説文の書き出しを先行注入することで、モデルを「解説文の続きを書く」モードに固定し、
    # 講評・採点モードへの遷移を構造的に防ぐ。
    subject = _extract_subject(user_prompt)
    assistant_prefill = f"{subject}は、"
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": assistant_prefill},
    ]


def _extract_subject(prompt: str) -> str:
    """質問文から主題語を抽出する。「～について」「～とは」「～を」などのパターンに対応。"""
    import re as _re_mod
    # 「～について」パターン
    m = _re_mod.search(r"^(.+?)について", prompt)
    if m:
        return m.group(1).strip()
    # 「～とは」パターン
    m = _re_mod.search(r"^(.+?)とは", prompt)
    if m:
        return m.group(1).strip()
    # 「～を」パターン
    m = _re_mod.search(r"^(.+?)を", prompt)
    if m:
        return m.group(1).strip()
    # フォールバック: 最初の20文字
    return prompt[:20].rstrip("。、 ")




@mcp.tool()
async def rag_ask(
    query: str,
    vault_dir: str = "",
    tags: str = "",
    selected_doc_ids: str = "",
    mode: str = "default",
) -> str:
    """
    ローカル Wikipedia vectorDB を検索し llama.cpp で回答を生成して Vault に保存する。

    Args:
        query:     質問文字列
        vault_dir: 出力先ディレクトリ（省略時は VAULT_PATH 環境変数）
        tags:      カンマ区切りのタグ文字列
    """
    db_url: str = get_db_url()
    is_compare_mode = (mode or "").lower() == "compare"
    if is_compare_mode:
        docs, sub_queries, title_lists, extraction_mode, ranked_top20_docs = await _retrieve_rag_docs(
            query,
            db_url,
        )
    else:
        # ask-wiki は ask-wiki-report と同じ検索条件を使う。
        use_rule_based = os.environ.get("RAG_REPORT_USE_RULE_BASED_EXTRACTOR", "0") != "0"
        report_vector_query_limit = int(os.environ.get("RAG_REPORT_VECTOR_QUERY_LIMIT", "1"))
        docs, sub_queries, title_lists, extraction_mode, ranked_top20_docs = await _retrieve_rag_docs(
            query,
            db_url,
            force_rule_based=use_rule_based,
            vector_query_limit=report_vector_query_limit,
        )
    selected_ids = _parse_selected_doc_ids(selected_doc_ids)

    if selected_ids and ranked_top20_docs:
        ranked_by_id = {int(doc["id"]): doc for doc in ranked_top20_docs}
        selected_docs: list[dict] = []
        selected_seen: set[int] = set()
        for selected_id in selected_ids:
            if selected_id in selected_seen:
                continue
            doc = ranked_by_id.get(selected_id)
            if not doc:
                continue
            selected_seen.add(selected_id)
            selected_docs.append(doc)

        if selected_docs:
            docs = selected_docs
    print(f"[rag_ask] query_extraction_mode={extraction_mode}", file=sys.stderr)

    ranking_lines = [
        f"{index}. 【{doc['title']}】 (id={doc['id']})"
        for index, doc in enumerate(ranked_top20_docs, start=1)
    ]
    ranking_text = "\n".join(ranking_lines) if ranking_lines else "（取得なし）"

    # --- llama.cpp で回答生成 ---
    context_stats_md = ""

    if not docs:
        answer = "関連する情報が見つかりませんでした。"
        sources_list_md = ""
        sources_body_md = ""
        context_stats_md = (
            "- 実使用コンテキスト長: 0文字\n"
            "- Wikipedia本文合計: 0文字\n"
            "- 参照記事数: 0\n"
            "- num_ctx: 12000\n"
            "- num_predict: 1400\n"
            "- 記事別本文文字数:\n"
            "  - （なし）"
        )
    else:
        compare_max_docs = max(1, int(os.environ.get("KB_COMPARE_RAG_MAX_DOCS", "4")))
        # 通常ask-wikiはreportと同じ上位3件を使用。compareでは選択順を尊重しつつ上限を低めにする。
        if is_compare_mode:
            # compareでも既定2件に固定せず、上位候補を十分に使ってランキング抽出を安定化する。
            target_docs_for_gemma = min(compare_max_docs, len(docs))
            llm_preset_name = "qa_rag"
            max_context_chars = int(os.environ.get("KB_COMPARE_RAG_MAX_CONTEXT_CHARS", "9000"))
        else:
            target_docs_for_gemma = min(3, len(docs))
            llm_preset_name = "qa_rag"
            max_context_chars = int(os.environ.get("RAG_REPORT_MAX_CONTEXT_CHARS", "10000"))

        llm_preset = _LLAMA_PRESETS.get(llm_preset_name, {})
        docs = docs[:target_docs_for_gemma]
        retrieved_docs_count = len(docs)

        title_matched_ids = set()
        for tl in title_lists:
            for d in tl:
                title_matched_ids.add(d["id"])

        # 先頭優先は維持（一致記事・一覧記事を前側へ）
        docs.sort(
            key=lambda d: (
                0 if d["id"] in title_matched_ids else 1,
                0 if "一覧" in d["title"] else 1,
            )
        )

        # 入力コンテキストを安全側で制限する。
        # さらにAPI側で context overflow が発生した場合は、本文を段階的に縮小して再試行する。
        MAX_CONTEXT_CHARS = max_context_chars
        MIN_CONTEXT_CHARS = int(os.environ.get("RAG_MIN_CONTEXT_CHARS", "1200"))

        base_sources: list[tuple[str, str]] = []
        for d in docs:
            chunk = d["content"]
            if not chunk:
                chunk = d["content"][:1]
            base_sources.append((d["title"], chunk))

        def build_context_sources(char_limit: int) -> list[tuple[str, str]]:
            built: list[tuple[str, str]] = []
            used = 0
            for title, body in base_sources:
                remaining = char_limit - used
                if remaining <= 0:
                    break
                piece = body[:remaining]
                if not piece:
                    continue
                built.append((title, piece))
                used += len(piece)
            if not built and base_sources:
                title, body = base_sources[0]
                built.append((title, body[: max(1, MIN_CONTEXT_CHARS)]))
            return built

        import sys as _sys

        current_limit = MAX_CONTEXT_CHARS
        context_sources = build_context_sources(current_limit)
        source_char_stats: list[tuple[str, int]] = []
        total_ctx_chars = 0
        llm_text: str | None = None
        prefill = ""

        for attempt in range(3):
            context_parts = [f"【{title}】\n{body}" for title, body in context_sources]
            source_char_stats = [(title, len(body)) for title, body in context_sources]
            total_ctx_chars = sum(size for _, size in source_char_stats)

            context = "\n\n".join(context_parts)
            rag_messages = _build_rag_messages(context, query)

            user_content = rag_messages[1]["content"]
            if query not in user_content:
                raise RuntimeError(
                    "RAG prompt integrity check failed: original query is not embedded verbatim in user message"
                )

            prefill = rag_messages[2]["content"]
            llm_prompt = (
                f"{rag_messages[0]['content']}\n\n"
                f"{rag_messages[1]['content']}\n\n"
                f"{prefill}"
            )

            print(
                "[rag_ask] prompt_check"
                f" query_in_user_msg=True"
                f" assistant_prefill={prefill!r}"
                f" query_preview={query[:80]!r}",
                file=_sys.stderr,
            )

            try:
                llm_text = await asyncio.to_thread(
                    _run_llama_with_preset,
                    llm_prompt,
                    llm_preset_name,
                )
                break
            except Exception as exc:  # noqa: BLE001
                error_text = str(exc).lower()
                likely_context_overflow = (
                    "context" in error_text
                    or "token" in error_text
                    or "n_ctx" in error_text
                    or "length" in error_text
                )
                can_shrink_more = current_limit > MIN_CONTEXT_CHARS
                is_last_attempt = attempt >= 2
                if not likely_context_overflow or not can_shrink_more or is_last_attempt:
                    raise

                next_limit = max(int(current_limit * 0.7), MIN_CONTEXT_CHARS)
                print(
                    "[rag_ask] context_shrink_retry"
                    f" attempt={attempt + 1}"
                    f" char_limit={current_limit} -> {next_limit}"
                    f" reason={exc}",
                    file=_sys.stderr,
                )
                current_limit = next_limit
                context_sources = build_context_sources(current_limit)

        if llm_text is None:
            raise RuntimeError("llama.cpp response is empty")

        included_docs_count = len(context_sources)
        answer = llm_text.strip()
        # assistant prefill + 生成結果を結合して完全な回答にする
        answer = prefill + answer

        # compareモードでは「〜教えてください」の問い返しを抑止し、
        # 根拠語が不足する回答は参照コンテキストベースへフォールバックする。
        if is_compare_mode:
            ranked_pm_answer = _build_prime_minister_tenure_answer(query, context_sources)
            if ranked_pm_answer:
                answer = ranked_pm_answer
            else:
                normalized_answer = answer.strip()
                looks_like_instruction = bool(
                    re.match(r"^.{0,80}(?:教えてください|示してください|回答してください)[。.!！?？]*$", normalized_answer)
                )
                if looks_like_instruction or (not _answer_has_context_signals(normalized_answer, context_sources)):
                    fallback_answer = _build_grounded_fallback_answer(query, context_sources)
                    if fallback_answer:
                        answer = fallback_answer

        # llama-cpp Python API では詳細トークン計測値を常に取得できないため、件数中心でログ出力する。
        print(
            f"[rag_ask] context={total_ctx_chars:,}文字"
            f" retrieved_docs={retrieved_docs_count} included_docs={included_docs_count}"
            f" engine=llama.cpp",
            file=_sys.stderr,
        )
        for title, size in source_char_stats:
            print(f"[rag_ask] source_chars title=【{title}】 chars={size:,}", file=_sys.stderr)
        # 参照元タイトル一覧（Gemmaへ渡した順序）
        sources_list_md = "\n".join([f"- 【{title}】" for title, _ in context_sources])
        # 参照元Wikipedia本文（Gemmaへ実際に渡した本文をそのまま保存）
        sources_body_md = "\n\n---\n\n".join(
            [f"### 【{title}】\n\n{body}" for title, body in context_sources]
        )

        source_char_lines = (
            "\n".join([f"  - 【{title}】: {size:,}文字" for title, size in source_char_stats])
            if source_char_stats
            else "  - （なし）"
        )
        context_stats_md = (
            f"- 実使用コンテキスト長: {total_ctx_chars:,}文字\n"
            f"- Wikipedia本文合計: {total_ctx_chars:,}文字\n"
            f"- 参照記事数: {included_docs_count}\n"
            f"- num_ctx: {int(llm_preset.get('num_ctx', 12000))}\n"
            f"- num_predict: {int(llm_preset.get('max_tokens', 1400))}\n"
            "- 記事別本文文字数:\n"
            f"{source_char_lines}"
        )

    # --- Vault に Markdown 保存 ---
    out_dir = Path(vault_dir or DEFAULT_VAULT_PATH).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] or ["wikipedia", "rag", "qa"]
    tags_field = ", ".join(tag_list)
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    today = now.strftime("%Y-%m-%d")
    doc_id = now.strftime("%Y%m%d%H%M%S")
    slug = generate_slug(query, out_dir)
    summary = trim_summary(normalize_summary(answer)) or "Wikipedia RAG による回答"

    metadata_lines = [
        "---",
        f'id: "{doc_id}"',
        f'title: "{query}"',
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
        f"# 質問\n{query}\n\n"
        f"# 回答\n{answer}\n\n"
        f"# 検索クエリ抽出モード\n{extraction_mode}\n\n"
        f"# 検索ランキング (取得上位20件)\n{ranking_text}\n\n"
        f"# 検索クエリ (実際に使用)\n"
        + "\n".join([f"- `{sq}`" for sq in sub_queries])
        + "\n\n"
        f"# コンテキスト統計 (実測)\n{context_stats_md}\n\n"
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
