#!/usr/bin/env python3
"""
Wikipedia 日本語ダンプ XML → Parquet 変換スクリプト

Usage:
    python3 xml_to_parquet.py \
        --input  jawiki-latest-pages-articles.xml.bz2 \
        --outdir ./parquet_output \
        [--chunk-size 50000] \
        [--min-text-len 200]

Features:
    - bz2 圧縮のまま逐次読み込み（メモリ効率）
    - リダイレクトページを除外
    - mwparserfromhell で wiki マークアップを除去
    - 指定行数ごとに分割 Parquet ファイルへ保存
    - tqdm によるリアルタイム進捗表示
"""

import argparse
import bz2
import re
import sys
import os
from pathlib import Path

import mwxml
import mwparserfromhell
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


# ---------------------------------------------------------------------------
# テキスト正規化
# ---------------------------------------------------------------------------
_SECTION_RE = re.compile(r"==+[^=]+==+")
_WHITESPACE_RE = re.compile(r"\s{3,}")


def clean_wikitext(raw: str | None) -> str:
    """wiki マークアップを除去してプレーンテキストに変換する。"""
    if not raw:
        return ""
    try:
        parsed = mwparserfromhell.parse(raw)
        text = parsed.strip_code()
    except Exception:
        # パース失敗時はそのまま返す
        text = raw
    text = _SECTION_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def is_redirect(text: str | None) -> bool:
    """リダイレクトページを検出する。"""
    if not text:
        return True
    return text.lstrip().lower().startswith("#redirect") or \
           text.lstrip().lower().startswith("#転送")


# ---------------------------------------------------------------------------
# PyArrow スキーマ
# ---------------------------------------------------------------------------
SCHEMA = pa.schema([
    pa.field("id", pa.int64()),
    pa.field("title", pa.string()),
    pa.field("text", pa.string()),
])


# ---------------------------------------------------------------------------
# メイン変換処理
# ---------------------------------------------------------------------------
def convert(
    input_path: str,
    outdir: str,
    chunk_size: int,
    min_text_len: int,
) -> None:
    out_path = Path(outdir)
    out_path.mkdir(parents=True, exist_ok=True)

    batch: list[dict] = []
    file_index = 0
    total_written = 0
    total_skipped = 0
    total_pages = 0

    def flush(batch: list[dict], index: int) -> str:
        df = pd.DataFrame(batch, columns=["id", "title", "text"])
        table = pa.Table.from_pandas(df, schema=SCHEMA, preserve_index=False)
        file_path = out_path / f"jawiki_{index:05d}.parquet"
        pq.write_table(table, file_path, compression="snappy")
        return str(file_path)

    print(f"[INFO] 入力ファイル  : {input_path}")
    print(f"[INFO] 出力ディレクトリ: {outdir}")
    print(f"[INFO] チャンクサイズ : {chunk_size:,} 件/ファイル")
    print(f"[INFO] 最小テキスト長 : {min_text_len} 文字")
    print("[INFO] 変換開始 ...", flush=True)

    opener = bz2.open if input_path.endswith(".bz2") else open

    with opener(input_path) as f:
        dump = mwxml.Dump.from_file(f)
        for page in dump.pages:
            total_pages += 1

            # 進捗表示（1000件ごと）
            if total_pages % 1000 == 0:
                print(
                    f"\r[PROGRESS] pages={total_pages:,}  "
                    f"written={total_written:,}  skipped={total_skipped:,}  "
                    f"files={file_index}",
                    end="",
                    flush=True,
                )

            # 記事名前空間 (0) のみ対象
            if page.namespace != 0:
                total_skipped += 1
                continue

            # 最新リビジョンを取得
            revision = next(iter(page), None)
            if revision is None or revision.text is None:
                total_skipped += 1
                continue

            # リダイレクト除外
            if is_redirect(revision.text):
                total_skipped += 1
                continue

            # wiki マークアップ除去
            clean_text = clean_wikitext(revision.text)

            # テキスト長フィルタ
            if len(clean_text) < min_text_len:
                total_skipped += 1
                continue

            batch.append({
                "id": int(page.id),
                "title": str(page.title),
                "text": clean_text,
            })
            total_written += 1

            # チャンクが溜まったら保存
            if len(batch) >= chunk_size:
                saved_path = flush(batch, file_index)
                print(
                    f"\n[SAVE] {saved_path}  ({len(batch):,} 件)",
                    flush=True,
                )
                batch = []
                file_index += 1

    # 残りを保存
    if batch:
        saved_path = flush(batch, file_index)
        print(f"\n[SAVE] {saved_path}  ({len(batch):,} 件)", flush=True)
        file_index += 1

    print(
        f"\n[DONE] 合計ページ数={total_pages:,}  "
        f"書き込み={total_written:,}  スキップ={total_skipped:,}  "
        f"ファイル数={file_index}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wikipedia XML ダンプ → Parquet 変換"
    )
    parser.add_argument(
        "--input",
        default="jawiki-latest-pages-articles.xml.bz2",
        help="入力 bz2 ファイルパス",
    )
    parser.add_argument(
        "--outdir",
        default="parquet_output",
        help="Parquet 出力ディレクトリ",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=50_000,
        help="1 Parquet ファイルあたりの最大記事数 (default: 50000)",
    )
    parser.add_argument(
        "--min-text-len",
        type=int,
        default=200,
        help="本文の最小文字数フィルタ (default: 200)",
    )
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"[ERROR] 入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        sys.exit(1)

    convert(
        input_path=args.input,
        outdir=args.outdir,
        chunk_size=args.chunk_size,
        min_text_len=args.min_text_len,
    )


if __name__ == "__main__":
    main()
