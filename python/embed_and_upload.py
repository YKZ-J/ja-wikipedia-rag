#!/usr/bin/env python3
"""
Wikipedia Parquet → Embedding生成 → ローカル Supabase バッチ投入（非同期高速版）

Usage:
    source .env.local

    # ディレクトリ指定（全 jawiki_*.parquet を処理）
    python3 -u python/embed_and_upload.py \\
        --parquet ./parquet_output \\
        [--chunk-size 64] \\
        [--workers 6] \\
        [--files 2] \\
        [--skip-processed]

    # 単一ファイル
    python3 -u python/embed_and_upload.py \\
        --parquet ./parquet_output/jawiki_00000.parquet \\
        [--chunk-size 64] \\
        [--workers 6]

Features:
    - asyncio + Transformers(Pytorch) でチャンク Embedding を非同期生成
    - asyncpg で Supabase upsert を非同期実行
    - --files で複数ファイルを同時処理（デフォルト 2）
    - --workers でファイル内チャンク数の同時実行数を制御（デフォルト 6）
    - chunk_size=64 で 16GB RAM 安全
    - 再実行セーフ（upsert + .done マーカ）
    - 日時付きログ出力・リトライ対応
"""

import argparse
import asyncio
import itertools
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Tuple

import asyncpg
import pyarrow.parquet as pq
from dotenv import load_dotenv

from gte_embedding import DEFAULT_EMBEDDING_MODEL, GteEmbeddingModel

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
EMBEDDING_MODEL = DEFAULT_EMBEDDING_MODEL
# 各チャンクは通常 500 文字程度。十分余裕を持たせる。
MAX_TEXT_CHARS = 8000
EMBEDDING_BATCH_SIZE = 32
EMBEDDING_MAX_LENGTH = 512
BULK_UPSERT_BATCH_SIZE = 250
PG_POOL_SIZE = 8           # asyncpg コネクションプールサイズ

SECTION_CUTOFF_RE = re.compile(
    r"(?im)^\s*(?:={2,}\s*)?(脚注|参考文献|関連項目|外部リンク)(?:\s*={2,})?\s*$"
)
LINE_NOISE_PATTERNS = [
    re.compile(r"^\s*(thumb|left|right|frame|center|upright)\s*\|.*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*px\s*\|.*$", re.IGNORECASE),
    re.compile(r"^\s*Category:.*$", re.IGNORECASE),
    re.compile(r"^\s*カテゴリ:.*$"),
]


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------
def log(msg: str, error: bool = False) -> None:
    """[日時] プレフィックス付き出力。エラーは stderr に出力する。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    out = f"[{ts}] {msg}"
    if error:
        print(out, file=sys.stderr, flush=True)
    else:
        print(out, flush=True)


def clean_content(text: str) -> str:
    """指定セクション以降を除去し、不要行を削除して本文を返す。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    m = SECTION_CUTOFF_RE.search(normalized)
    if m:
        normalized = normalized[:m.start()]

    kept_lines: List[str] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            if kept_lines and kept_lines[-1] != "":
                kept_lines.append("")
            continue
        if any(p.search(line) for p in LINE_NOISE_PATTERNS):
            continue
        kept_lines.append(line)

    cleaned = "\n".join(kept_lines).strip()
    # 3連続以上の改行を2連続に圧縮
    return re.sub(r"\n{3,}", "\n\n", cleaned)


def chunk_text(
    text: str,
    chunk_chars: int,
    overlap_chars: int,
    min_chunk_chars: int,
) -> List[str]:
    """文境界を優先しつつ文字数ベースでチャンク分割する。"""
    s = text.strip()
    if not s:
        return []
    if len(s) <= chunk_chars:
        return [s]

    separators = "。！？\n"
    chunks: List[str] = []
    start = 0
    n = len(s)

    while start < n:
        hard_end = min(start + chunk_chars, n)
        if hard_end >= n:
            end = n
        else:
            search_start = min(start + min_chunk_chars, hard_end)
            window = s[search_start:hard_end]
            boundary_pos = max(window.rfind(sep) for sep in separators)
            if boundary_pos >= 0:
                end = search_start + boundary_pos + 1
            else:
                end = hard_end

        chunk = s[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= n:
            break
        next_start = end - overlap_chars
        start = next_start if next_start > start else end

    if len(chunks) >= 2 and len(chunks[-1]) < min_chunk_chars:
        chunks[-2] = (chunks[-2] + "\n" + chunks[-1]).strip()
        chunks.pop()

    return chunks


# ---------------------------------------------------------------------------
# 非同期 Embedding 生成
# ---------------------------------------------------------------------------
async def create_batch_embeddings(
    texts: List[str],
    embedding_manager: "GteEmbeddingModel",
) -> List[List[float]]:
    """チャンク内テキストを GTE(Transformers) でバッチ埋め込みする。"""
    truncated = [t[:MAX_TEXT_CHARS] for t in texts]
    return await embedding_manager.embed(truncated)


# ---------------------------------------------------------------------------
# 非同期 Supabase upsert
# ---------------------------------------------------------------------------
async def upsert_documents(
    pool: asyncpg.Pool,
    batch_data: List[dict],
    target_table: str,
    db_batch_size: int,
    use_copy_bulk: bool,
    db_tuning: bool,
    conflict_mode: str,
) -> None:
    """asyncpg で PostgreSQL に直接 upsert する（PostgREST バイパス）。

    INSERT ... ON CONFLICT (id) DO UPDATE で JSON エンコード不要、高速。
    pgvector は Python list[float] → vector キャスト文字列で渡す。
    """
    if target_table == "documents":
        records = [
            (
                row["id"],
                row["title"],
                row["content"],
                "[" + ",".join(map(str, row["embedding"])) + "]",
            )
            for row in batch_data
        ]
        sql = """
            INSERT INTO documents (id, title, content, embedding)
            VALUES ($1, $2, $3, $4::vector)
            ON CONFLICT (id) DO UPDATE
                SET title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding
        """
    elif target_table == "documents_v2":
        records = [
            (
                row["article_id"],
                row["chunk_index"],
                row["title"],
                row["content"],
                "[" + ",".join(map(str, row["embedding"])) + "]",
            )
            for row in batch_data
        ]
        if conflict_mode == "ignore":
            sql = """
                INSERT INTO documents_v2 (article_id, chunk_index, title, content, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                ON CONFLICT (article_id, chunk_index) DO NOTHING
            """
        else:
            sql = """
                INSERT INTO documents_v2 (article_id, chunk_index, title, content, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                ON CONFLICT (article_id, chunk_index) DO UPDATE
                    SET title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding
            """
    else:
        raise ValueError(
            f"unsupported target_table={target_table}. Use documents or documents_v2"
        )

    async with pool.acquire() as conn:
        if db_tuning:
            # バルク投入時の同期コミットを緩和してWAL待ちを減らす
            await conn.execute("SET LOCAL synchronous_commit = off")

        if use_copy_bulk and target_table == "documents_v2":
            await conn.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS documents_v2_stage (
                    article_id BIGINT,
                    chunk_index INTEGER,
                    title TEXT,
                    content TEXT,
                    embedding TEXT
                ) ON COMMIT PRESERVE ROWS
                """
            )
            # DB往復を減らすため、1チャンク分を一括COPYして1回でMERGEする
            await conn.copy_records_to_table(
                "documents_v2_stage",
                records=records,
                columns=[
                    "article_id",
                    "chunk_index",
                    "title",
                    "content",
                    "embedding",
                ],
            )
            if conflict_mode == "ignore":
                await conn.execute(
                    """
                    INSERT INTO documents_v2 (article_id, chunk_index, title, content, embedding)
                    SELECT article_id, chunk_index, title, content, embedding::vector(256)
                    FROM documents_v2_stage
                    ON CONFLICT (article_id, chunk_index) DO NOTHING
                    """
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO documents_v2 (article_id, chunk_index, title, content, embedding)
                    SELECT article_id, chunk_index, title, content, embedding::vector(256)
                    FROM documents_v2_stage
                    ON CONFLICT (article_id, chunk_index) DO UPDATE
                        SET title = EXCLUDED.title,
                            content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding
                    """
                )
            await conn.execute("TRUNCATE documents_v2_stage")
            return

        for i in range(0, len(records), db_batch_size):
            await conn.executemany(sql, records[i:i + db_batch_size])


# ---------------------------------------------------------------------------
# 1チャンク処理
# ---------------------------------------------------------------------------
async def process_chunk(
    upsert_client: asyncpg.Pool,
    chunk_idx: int,
    df_dict: dict,
    total_rows: int,
    embedding_manager: "GteEmbeddingModel",
    target_table: str,
    chunk_chars: int,
    overlap_chars: int,
    min_chunk_chars: int,
    db_batch_size: int,
    use_copy_bulk: bool,
    db_tuning: bool,
    conflict_mode: str,
) -> int:
    """1チャンク分の非同期 Embedding 生成 + Supabase upsert を行い投入件数を返す。

    create_batch_embeddings() がポートの acquire/release を管理しているため、
    upsert はポート解放後に実行され、他チャンクの Embedding と並行して動作する。
    """
    valid_articles: List[Tuple[int, str, str]] = [
        (int(doc_id), str(title), str(text))
        for doc_id, title, text in zip(df_dict["id"], df_dict["title"], df_dict["text"])
        if text and str(text).strip()
    ]
    if not valid_articles:
        return 0

    batch_data: List[dict] = []
    for article_id, title, raw_text in valid_articles:
        cleaned = clean_content(raw_text)
        if not cleaned:
            continue

        if target_table == "documents":
            batch_data.append({
                "id": article_id,
                "title": title,
                "content": cleaned,
            })
            continue

        chunks = chunk_text(
            cleaned,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
            min_chunk_chars=min_chunk_chars,
        )
        for chunk_index, chunk in enumerate(chunks):
            batch_data.append({
                "article_id": article_id,
                "chunk_index": chunk_index,
                "title": title,
                "content": chunk,
            })

    if not batch_data:
        return 0

    texts_only = [row["content"] for row in batch_data]
    embeddings = await create_batch_embeddings(
        texts_only,
        embedding_manager,
    )
    for row, emb in zip(batch_data, embeddings):
        row["embedding"] = emb

    await upsert_documents(
        upsert_client,
        batch_data,
        target_table,
        db_batch_size,
        use_copy_bulk,
        db_tuning,
        conflict_mode,
    )
    log(f"[chunk {chunk_idx}] upserted {len(batch_data)} records -> {target_table} (/{total_rows})")
    return len(batch_data)


# ---------------------------------------------------------------------------
# ファイル処理（チャンクを非同期並列実行）
# ---------------------------------------------------------------------------
async def process_file(
    file_path: Path,
    chunk_size: int,
    workers: int,
    max_rows: int = 0,
    target_table: str = "documents_v2",
    chunk_chars: int = 500,
    overlap_chars: int = 50,
    min_chunk_chars: int = 100,
    db_batch_size: int = BULK_UPSERT_BATCH_SIZE,
    use_copy_bulk: bool = True,
    db_tuning: bool = True,
    conflict_mode: str = "update",
    embedding_manager: "GteEmbeddingModel | None" = None,
    upsert_client: "asyncpg.Pool | None" = None,
) -> int:
    """ファイルを iter_batches でチャンク化し、asyncio.Semaphore により指定並列数で処理する。

    - upsert_client を外部から受け取り接続プールを再利用する
    - 渡されない場合はここで作成（単体ファイル実行時の後方互換）
    - as_completed で完了順に結果を収集し、max_rows 到達時は残タスクをキャンセル
    """
    pf = pq.ParquetFile(file_path)
    total_rows = pf.metadata.num_rows
    total_inserted = 0
    error_count = 0
    sem = asyncio.Semaphore(workers)
    if embedding_manager is None:
        embedding_manager = GteEmbeddingModel(
            model_name=EMBEDDING_MODEL,
            batch_size=EMBEDDING_BATCH_SIZE,
            max_length=EMBEDDING_MAX_LENGTH,
        )

    _own_client = upsert_client is None

    async def _run_tasks(uc: asyncpg.Pool) -> int:
        """チャンクを workers 単位で遅延生成し、to_pydict() をセマフォ内で実行。

        【改善点】
        - 旧実装: iter_batches を全件消費し 781 tasks を一括作成(~12秒ブロック)
        - 新実装: asyncio.wait で WINDOW=workers 件ずつ生成・処理(ブロックなし)
          → embed 1波目(6チャンク)が 2.5秒で完了するようになる
        """
        nonlocal total_inserted, error_count

        WINDOW = workers  # 同時保持タスク数の上限

        async def process_one(cidx: int, batch) -> int:
            async with sem:
                # to_pydict() は semaphore 内で遅延実行 → 一括変換ブロックを回避
                return await process_chunk(
                    uc,
                    cidx,
                    batch.to_pydict(),
                    total_rows,
                    embedding_manager,
                    target_table,
                    chunk_chars,
                    overlap_chars,
                    min_chunk_chars,
                    db_batch_size,
                    use_copy_bulk,
                    db_tuning,
                    conflict_mode,
                )

        pending: set = set()
        batch_iter = enumerate(
            pf.iter_batches(batch_size=chunk_size, columns=["id", "title", "text"]), 1
        )

        # WINDOW 件分だけ先行して投入
        for i, batch in itertools.islice(batch_iter, WINDOW):
            pending.add(asyncio.create_task(process_one(i, batch)))
            await asyncio.sleep(0)  # イベントループに制御を返す

        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    n = await task
                    total_inserted += n
                    if max_rows > 0 and total_inserted >= max_rows:
                        for t in pending:
                            t.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        return total_inserted
                    # 完了した分だけ新しいタスクを補充
                    try:
                        idx, nxt = next(batch_iter)
                        pending.add(asyncio.create_task(process_one(idx, nxt)))
                        await asyncio.sleep(0)
                    except StopIteration:
                        pass
                except asyncio.CancelledError:
                    return total_inserted
                except Exception as exc:
                    log(f"チャンクエラー (スキップ): {exc}", error=True)
                    error_count += 1
                    # エラー時も補充
                    try:
                        idx, nxt = next(batch_iter)
                        pending.add(asyncio.create_task(process_one(idx, nxt)))
                        await asyncio.sleep(0)
                    except StopIteration:
                        pass

        return total_inserted

    if _own_client:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise RuntimeError(
                "DATABASE_URL 環境変数が設定されていません。"
                ".env.local を作成して DATABASE_URL を設定してください。"
            )
        pool = await asyncpg.create_pool(db_url, min_size=2, max_size=PG_POOL_SIZE)
        try:
            await _run_tasks(pool)
        finally:
            await pool.close()
    else:
        await _run_tasks(upsert_client)

    if error_count:
        log(f"警告: {error_count} チャンクでエラーが発生しました", error=True)

    return total_inserted


# ---------------------------------------------------------------------------
# ディレクトリ処理（複数ファイル同時処理）
# ---------------------------------------------------------------------------
async def process_directory(
    parquet_dir: str,
    chunk_size: int,
    workers: int,
    files_concurrency: int,
    max_rows: int = 0,
    skip_processed: bool = False,
    target_table: str = "documents_v2",
    chunk_chars: int = 500,
    overlap_chars: int = 50,
    min_chunk_chars: int = 100,
    db_batch_size: int = BULK_UPSERT_BATCH_SIZE,
    use_copy_bulk: bool = True,
    db_tuning: bool = True,
    conflict_mode: str = "update",
    embedding_model: str = EMBEDDING_MODEL,
    embedding_batch_size: int = EMBEDDING_BATCH_SIZE,
    embedding_max_length: int = EMBEDDING_MAX_LENGTH,
    shard_index: int = 0,
    shard_count: int = 1,
) -> None:
    """ディレクトリ内の jawiki_*.parquet を処理する。

    files_concurrency 件までのファイルを同時に処理し、各ファイル内のチャンクも
    workers 数の Semaphore で非同期実行する。
    skip_processed=True の場合、<file>.done があればスキップする。
    """
    all_files = sorted(Path(parquet_dir).glob("jawiki_*.parquet"))
    files = [
        f for i, f in enumerate(all_files)
        if (i % shard_count) == shard_index
    ]
    if not files:
        log(
            f"ERROR: shard {shard_index}/{shard_count} の対象 parquet が見つかりません",
            error=True,
        )
        sys.exit(1)

    log(
        f"全 {len(all_files)} ファイル中 shard {shard_index}/{shard_count} の "
        f"{len(files)} ファイルを処理します（同時処理ファイル数: {files_concurrency}）"
    )
    file_sem = asyncio.Semaphore(files_concurrency)
    embedding_manager = GteEmbeddingModel(
        model_name=embedding_model,
        batch_size=embedding_batch_size,
        max_length=embedding_max_length,
    )

    # セッション・プールをプロセス全体で共有 → 接続再利用
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL 環境変数が設定されていません。"
            ".env.local を作成して DATABASE_URL を設定してください。"
        )
    pg_pool = await asyncpg.create_pool(db_url, min_size=2, max_size=PG_POOL_SIZE)
    try:
        async def handle_file(f: Path, idx: int) -> None:
            async with file_sem:
                done_marker = f.with_suffix(".parquet.done")
                if skip_processed and done_marker.exists():
                    log(f"[{idx}/{len(files)}] {f.name} スキップ（処理済マーカあり）")
                    return
                log(f"\n=== [{idx}/{len(files)}] {f.name} ===")
                total = await process_file(
                    f,
                    chunk_size,
                    workers,
                    max_rows,
                    target_table,
                    chunk_chars,
                    overlap_chars,
                    min_chunk_chars,
                    db_batch_size,
                    use_copy_bulk,
                    db_tuning,
                    conflict_mode,
                    embedding_manager,
                    pg_pool,
                )
                log(f"{f.name} の処理完了、合計 {total} 件投入")
                if skip_processed:
                    done_marker.touch()
                    log(f"{f.name} の完了マーカ作成: {done_marker.name}")

        await asyncio.gather(
            *[handle_file(f, i + 1) for i, f in enumerate(files)]
        )
    finally:
        await pg_pool.close()


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wikipedia Parquet → Supabase 非同期バッチ投入"
    )
    parser.add_argument(
        "--parquet",
        required=True,
        help="入力 Parquet ファイルパス、またはディレクトリ（jawiki_*.parquet を一括処理）",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=256,
        help="チャンクサイズ（デフォルト: 256）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=24,
        help="ファイル内チャンクの非同期並列数（デフォルト: 24、20〜30推奨）",
    )
    parser.add_argument(
        "--files",
        type=int,
        default=8,
        help="同時処理ファイル数（デフォルト: 8）",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="最大処理件数（テスト・部分投入用。0=無制限）",
    )
    parser.add_argument(
        "--skip-processed",
        action="store_true",
        help=".done マーカがあるファイルをスキップ（停止後の再開用）",
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default=EMBEDDING_MODEL,
        help="Embeddingモデル名（デフォルト: cl-nagoya/ruri-v3-30m）",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=EMBEDDING_BATCH_SIZE,
        help="Embedding内部バッチサイズ（デフォルト: 64）",
    )
    parser.add_argument(
        "--embedding-max-length",
        type=int,
        default=EMBEDDING_MAX_LENGTH,
        help="Embedding時の最大トークン長（デフォルト: 512）",
    )
    parser.add_argument(
        "--target-table",
        choices=["documents", "documents_v2"],
        default="documents_v2",
        help="投入先テーブル（デフォルト: documents_v2）",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=500,
        help="1チャンクの目標文字数（デフォルト: 500）",
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=50,
        help="チャンク間オーバーラップ文字数（デフォルト: 50）",
    )
    parser.add_argument(
        "--min-chunk-chars",
        type=int,
        default=100,
        help="最小チャンク文字数（デフォルト: 100）",
    )
    parser.add_argument(
        "--db-batch-size",
        type=int,
        default=BULK_UPSERT_BATCH_SIZE,
        help="DB書き込みのバッチサイズ（デフォルト: 250、100〜500推奨）",
    )
    parser.add_argument(
        "--no-copy-bulk",
        action="store_true",
        help="COPYベースの一括投入を無効化し、通常のバッチUPSERTを使う",
    )
    parser.add_argument(
        "--no-db-tuning",
        action="store_true",
        help="DB投入時の session tuning（synchronous_commit=off）を無効化する",
    )
    parser.add_argument(
        "--conflict-mode",
        choices=["update", "ignore"],
        default="update",
        help="(article_id, chunk_index) 衝突時の動作（default: update）",
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        default=0,
        help="処理する shard 番号（0始まり、デフォルト: 0）",
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        default=1,
        help="全 shard 数（デフォルト: 1）",
    )
    args = parser.parse_args()

    if args.shard_count <= 0:
        print("ERROR: --shard-count は 1 以上を指定してください", file=sys.stderr)
        sys.exit(1)
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        print(
            f"ERROR: --shard-index は 0 以上 {args.shard_count - 1} 以下で指定してください",
            file=sys.stderr,
        )
        sys.exit(1)

    load_dotenv(".env.local")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print(
            "ERROR: DATABASE_URL 環境変数が設定されていません。"
            ".env.local を作成して DATABASE_URL を設定してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    supabase_url = os.environ.get("SUPABASE_URL", "(不明)")

    log(f"接続先DB: {db_url}")
    log(f"接続先Supabase: {supabase_url}")
    log(
        f"Parquet: {args.parquet}  chunk_size={args.chunk_size}  "
        f"workers={args.workers}  files={args.files}  "
        f"target_table={args.target_table}  "
        f"chunk_chars={args.chunk_chars} overlap_chars={args.overlap_chars} "
        f"min_chunk_chars={args.min_chunk_chars}  "
        f"db_batch_size={args.db_batch_size} copy_bulk={'no' if args.no_copy_bulk else 'yes'} "
        f"db_tuning={'no' if args.no_db_tuning else 'yes'}  "
        f"conflict_mode={args.conflict_mode}  "
        f"shard={args.shard_index}/{args.shard_count}  "
        f"max_rows={args.max_rows or '無制限'}  "
        f"embedding_model={args.embedding_model} embedding_batch={args.embedding_batch_size} "
        f"embedding_max_length={args.embedding_max_length}"
    )

    target = Path(args.parquet)

    async def _run_file() -> int:
        embedding_manager = GteEmbeddingModel(
            model_name=args.embedding_model,
            batch_size=args.embedding_batch_size,
            max_length=args.embedding_max_length,
        )
        db_url = os.environ.get("DATABASE_URL")  # main() で検証済み
        pg_pool = await asyncpg.create_pool(db_url, min_size=2, max_size=PG_POOL_SIZE)
        try:
            return await process_file(
                target,
                args.chunk_size,
                args.workers,
                args.max_rows,
                args.target_table,
                args.chunk_chars,
                args.overlap_chars,
                args.min_chunk_chars,
                args.db_batch_size,
                (not args.no_copy_bulk),
                (not args.no_db_tuning),
                args.conflict_mode,
                embedding_manager,
                pg_pool,
            )
        finally:
            await pg_pool.close()

    if target.is_dir():
        asyncio.run(
            process_directory(
                str(target),
                args.chunk_size,
                args.workers,
                args.files,
                args.max_rows,
                args.skip_processed,
                args.target_table,
                args.chunk_chars,
                args.overlap_chars,
                args.min_chunk_chars,
                args.db_batch_size,
                (not args.no_copy_bulk),
                (not args.no_db_tuning),
                args.conflict_mode,
                args.embedding_model,
                args.embedding_batch_size,
                args.embedding_max_length,
                args.shard_index,
                args.shard_count,
            )
        )
    elif target.is_file():
        total = asyncio.run(_run_file())
        log(f"{target.name} の処理完了、合計 {total} 件投入")
    else:
        log(f"ERROR: {args.parquet} が見つかりません", error=True)
        sys.exit(1)

    log("完了")


if __name__ == "__main__":
    main()
