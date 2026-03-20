#!/usr/bin/env python3
"""
llama-cpp による LLM 実行スクリプト

Usage:
    python3 llama_run.py "<prompt>" "<vault_dir>"

Output:
    生成された Markdown ファイルのパスを stdout に出力
"""

import sys
import os
import re
import secrets
import unicodedata
from llama_cpp import Llama
from datetime import datetime
from pathlib import Path

# モデルパス（環境変数で上書き可能）
MODEL_PATH = os.environ.get("MODEL_PATH", "")

if len(sys.argv) < 3:
    print(
        "Usage: python3 llama_run.py \"<prompt>\" \"<vault_dir>\" [--stdout]",
        file=sys.stderr,
    )
    sys.exit(1)

prompt = sys.argv[1]
vault_dir = sys.argv[2]
args = sys.argv[3:]
stdout_only = "--stdout" in args

title_arg = ""
tags_arg = ""

i = 0
while i < len(args):
    if args[i] == "--title" and i + 1 < len(args):
        title_arg = args[i + 1]
        i += 2
        continue
    if args[i] == "--tags" and i + 1 < len(args):
        tags_arg = args[i + 1]
        i += 2
        continue
    i += 1

def split_frontmatter(markdown: str):
    pattern = re.compile(r"(?ms)^---\s*\n(.*?)\n---\s*\n")
    matches = list(pattern.finditer(markdown))
    if not matches:
        return "", markdown

    def is_frontmatter_block(text: str) -> bool:
        return re.search(
            r"^(id|title|slug|tags|created|updated|summary|image|type|isDraft)\s*:",
            text,
            re.MULTILINE,
        ) is not None

    candidates = [m for m in matches if is_frontmatter_block(m.group(1))]
    selected = candidates[-1] if candidates else matches[0]

    frontmatter = selected.group(1)
    body = markdown[selected.end() :]
    return frontmatter, body


def parse_frontmatter(frontmatter: str) -> dict:
    data = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (
            (value.startswith("\"") and value.endswith("\""))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1]
        data[key] = value
    return data


def parse_tags(value: str) -> list:
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        return []
    return [item.strip().strip("\"'") for item in text.split(",") if item.strip()]


def normalize_slug_words(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    lowered = normalized.lower().strip()
    cleaned = re.sub(r"[^a-z0-9\s-]", "", lowered)
    dashed = re.sub(r"[\s_]+", "-", cleaned)
    compact = re.sub(r"-{2,}", "-", dashed).strip("-")
    return compact or "doc"


def generate_slug(title: str, out_dir: Path) -> str:
    base = normalize_slug_words(title)
    while True:
        hex_prefix = secrets.token_hex(2)
        slug = f"{hex_prefix}-{base}"
        if not (out_dir / f"{slug}.md").exists():
            return slug


def normalize_summary(text: str) -> str:
    text = re.sub(r"^[-*・]\s+", "", text.strip())
    return " ".join(text.split()).strip()


def trim_summary(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    cut = max(
        truncated.rfind("。"),
        truncated.rfind("."),
        truncated.rfind("!"),
        truncated.rfind("?"),
    )
    if cut > 20:
        return truncated[: cut + 1]
    return truncated.rstrip()


def build_summary_from_body(body: str) -> str:
    placeholders = re.compile(r"(箇条書き|ここに|プレースホルダー|本文未生成|関連リンク|URL)")
    for line in body.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        if cleaned.startswith("-") or cleaned.startswith("*"):
            cleaned = cleaned.lstrip("-* ").strip()
        # 日付や無意味な文字列をスキップ
        if re.match(r"^\*{0,2}(id|created|updated|title|slug|tags|summary)", cleaned, re.IGNORECASE):
            continue
        if placeholders.search(cleaned):
            continue
        if len(cleaned) > 10:  # 最低10文字以上
            return trim_summary(normalize_summary(cleaned), 300) or "自動生成された概要"
    return "自動生成された概要"


def is_placeholder_text(text: str) -> bool:
    return re.search(r"(箇条書き|ここに|プレースホルダー|本文未生成|関連リンク|URL)", text) is not None


def has_heading(body: str, heading: str) -> bool:
    targets = {f"# {heading}", f"## {heading}"}
    return any(line.strip() in targets for line in body.splitlines())


def extract_related_section(body: str):
    lines = body.splitlines()
    start_index = None
    for i, line in enumerate(lines):
        if line.strip() in {"# 関連", "## 関連"}:
            start_index = i
            break

    if start_index is None:
        return body, ""

    end_index = len(lines)
    for i in range(start_index + 1, len(lines)):
        if lines[i].strip().startswith("# "):
            end_index = i
            break

    related_lines = lines[start_index + 1 : end_index]
    related_text = "\n".join(related_lines).strip()
    body_without_related = "\n".join(lines[:start_index] + lines[end_index:])
    return body_without_related.strip(), related_text


def extract_section(body: str, heading: str):
    lines = body.splitlines()
    start_index = None
    for i, line in enumerate(lines):
        if line.strip() in {f"# {heading}", f"## {heading}"}:
            start_index = i + 1
            break

    if start_index is None:
        return "", body

    end_index = len(lines)
    for i in range(start_index, len(lines)):
        if lines[i].strip().startswith("# "):
            end_index = i
            break

    section_lines = lines[start_index:end_index]
    remaining_lines = lines[: start_index - 1] + lines[end_index:]
    return "\n".join(section_lines).strip(), "\n".join(remaining_lines).strip()


def strip_frontmatter_blocks(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text

    out_lines = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "---":
            j = i + 1
            while j < len(lines) and lines[j].strip() != "---":
                j += 1
            if j < len(lines):
                block = "\n".join(lines[i + 1 : j])
                if re.search(
                    r"^(id|title|slug|tags|created|updated|summary|image|type|isDraft)\s*:",
                    block,
                    re.MULTILINE,
                ):
                    i = j + 1
                    continue
                out_lines.extend(lines[i : j + 1])
                i = j + 1
                continue
        out_lines.append(lines[i])
        i += 1

    return "\n".join(out_lines)


def ensure_template(body: str, summary: str, related_links: str, title: str) -> str:
    cleaned_body = body.strip()
    if not cleaned_body:
        cleaned_body = f"{title}に関する技術ドキュメント。\n\n詳細な情報は関連リンクを参照してください。"

    related_section = related_links.strip() or "- なし"

    return "\n".join(
        [
            "# 概要",
            summary,
            "",
            "# 詳細",
            cleaned_body,
            "",
            "# 関連",
            related_section,
            "",
        ]
    ).strip()


def build_detail_prompt(title: str, tags: list, related_links: str) -> str:
    tag_text = ", ".join(tags) if tags else "general"
    labels = re.findall(r"\[([^\]]+)\]", related_links or "")
    label_text = "、".join(labels[:8]) if labels else "なし"

    return (
        "以下のテーマについて詳細セクションのみ作成してください。\n"
        "- 見出しやfrontmatterは不要\n"
        "- 箇条書きで5-8項目\n"
        "- 各項目は「ライブラリ名: 1-2文の説明」\n"
        "- Markdownコードブロックは禁止\n"
        "- 具体的な用途や利点を書く\n\n"
        f"テーマ: {title}\n"
        f"タグ: {tag_text}\n"
        f"参考候補: {label_text}"
    )


def generate_detail_fallback(llm: Llama, title: str, tags: list, related_links: str) -> str:
    prompt_text = build_detail_prompt(title, tags, related_links)
    result = llm(
        prompt=prompt_text,
        max_tokens=700,
        temperature=0.6,
        top_k=50,
        repeat_penalty=1.1,
    )
    text = result["choices"][0]["text"]
    text = re.sub(r"^```[a-z]*\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*$", "", text, flags=re.MULTILINE)
    text = strip_frontmatter_blocks(text).strip()
    detail_section, remainder = extract_section(text, "詳細")
    cleaned = detail_section or text or remainder
    return cleaned.strip()


# 出力先ディレクトリ
out_dir = Path(vault_dir)
out_dir.mkdir(parents=True, exist_ok=True)

# モデルファイル存在確認
model_path = Path(MODEL_PATH).expanduser()
if not model_path.exists() or not model_path.is_file():
    print(f"Error: Model file not found: {model_path}", file=sys.stderr)
    print("Set MODEL_PATH environment variable to the correct path.", file=sys.stderr)
    sys.exit(1)

if not os.access(model_path, os.R_OK):
    print(f"Error: Model file is not readable: {model_path}", file=sys.stderr)
    sys.exit(1)

try:
    model_size = model_path.stat().st_size
except OSError as e:
    print(f"Error: Failed to stat model file: {e}", file=sys.stderr)
    sys.exit(1)

try:
    with open(model_path, "rb") as f:
        header = f.read(4)
    if header != b"GGUF":
        print(
            f"Error: Model file is not a valid GGUF file (header={header})",
            file=sys.stderr,
        )
        sys.exit(1)
except OSError as e:
    print(f"Error: Failed to read model file: {e}", file=sys.stderr)
    sys.exit(1)

llm = Llama(
    model_path=str(model_path),
    n_ctx=32768,      # 実用最大
    n_threads=8,
    n_gpu_layers=-1, # Metal全層
    verbose=False,
    use_mmap=True,
    use_mlock=False,
    n_batch=1024,
)

# LLM 実行
result = llm(
    prompt=prompt,
    max_tokens=8192,
    temperature=0.7,
    top_k=50,
    repeat_penalty=1.1,
)

# テキスト抽出
text = result["choices"][0]["text"]

if stdout_only:
    print(text)
else:
    # デバッグ: LLM生成テキストをログ出力
    print(f"[DEBUG] LLM output (first 500 chars):\n{text[:500]}", file=sys.stderr)
    
    frontmatter_text, body_text = split_frontmatter(text)
    frontmatter_data = parse_frontmatter(frontmatter_text)

    # 本文内のコードブロックマーカーと frontmatter を削除
    body_text = re.sub(r"^```[a-z]*\s*$", "", body_text, flags=re.MULTILINE)
    body_text = re.sub(r"^```\s*$", "", body_text, flags=re.MULTILINE)
    body_text = strip_frontmatter_blocks(body_text)
    body_text = body_text.strip()
    
    print(f"[DEBUG] Frontmatter data: {frontmatter_data}", file=sys.stderr)
    print(f"[DEBUG] Body text (first 300 chars): {body_text[:300]}", file=sys.stderr)

    body_text, related_links = extract_related_section(body_text)
    overview_text, body_text = extract_section(body_text, "概要")
    detail_text, body_text = extract_section(body_text, "詳細")
    
    print(f"[DEBUG] Body after related extraction: {body_text[:300]}", file=sys.stderr)
    print(f"[DEBUG] Related links: {related_links[:200] if related_links else 'None'}", file=sys.stderr)

    title = title_arg or frontmatter_data.get("title", "").strip() or "Untitled"
    slug = generate_slug(title, out_dir)
    cli_tags = [tag.strip() for tag in tags_arg.split(",") if tag.strip()]
    fm_tags = parse_tags(frontmatter_data.get("tags", ""))
    tags = list(dict.fromkeys(cli_tags + fm_tags))
    
    summary_raw = frontmatter_data.get("summary", "").strip()
    print(f"[DEBUG] Summary raw: {summary_raw}", file=sys.stderr)
    
    # コードブロックマーカーや太字記号を除去
    summary_raw = re.sub(r"^```.*|```$", "", summary_raw)
    summary_raw = re.sub(r"\*\*", "", summary_raw).strip()
    summary_raw = normalize_summary(summary_raw)

    if (not summary_raw or is_placeholder_text(summary_raw)) and overview_text:
        summary_raw = build_summary_from_body(overview_text)
    
    if (
        not summary_raw
        or is_placeholder_text(summary_raw)
        or re.match(r"^\*{0,2}created", summary_raw, re.IGNORECASE)
    ):
        print(f"[DEBUG] Building summary from body_text", file=sys.stderr)
        summary = build_summary_from_body(detail_text or body_text)
    else:
        summary = summary_raw
    
    print(f"[DEBUG] Final summary: {summary}", file=sys.stderr)

    summary = trim_summary(normalize_summary(summary), 300) or "自動生成された概要"
    
    # body_textが無意味な場合のフォールバック
    body_text = detail_text or body_text
    meaningful_body = False
    for line in body_text.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        if re.match(r"^\*{0,2}(id|created|updated|title|slug|tags|summary)", cleaned, re.IGNORECASE):
            continue
        if len(cleaned) > 20:  # 意味のある行が20文字以上
            meaningful_body = True
            break
    
    if not meaningful_body:
        print(f"[DEBUG] Body text is meaningless, using fallback", file=sys.stderr)
        generated_detail = generate_detail_fallback(llm, title, tags, related_links)
        if generated_detail and len(generated_detail) > 40:
            body_text = generated_detail
            meaningful_body = True
        else:
            body_text = f"{title}に関する技術ドキュメント。"

    if not summary or is_placeholder_text(summary) or summary == "自動生成された概要":
        summary = build_summary_from_body(body_text)
    
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y%m%d%H%M%S")
    doc_id = timestamp
    tags_field = ", ".join(tags)

    metadata = "\n".join(
        [
            "---",
            f"id: \"{doc_id}\"",
            f"title: \"{title}\"",
            f"slug: \"{slug}\"",
            f"tags: [{tags_field}]" if tags_field else "tags: []",
            f"created: \"{today}\"",
            f"updated: \"{today}\"",
            f"summary: \"{summary}\"",
            "image: \"https://ytzmpefdjnd1ueff.public.blob.vercel-storage.com/blog.webp\"",
            "type: \"diary\"",
            "isDraft: \"true\"",
            "---",
            "",
        ]
    )

    content = ensure_template(body_text, summary, related_links, title)
    final_markdown = f"{metadata}{content}\n"

    outfile = out_dir / f"{slug}.md"

    # Markdown として保存
    outfile.write_text(final_markdown, encoding="utf-8")

    # 生成されたファイルパスを出力
    print(str(outfile))
