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
    - asyncio + aiohttp でチャンク Embedding を非同期生成
    - httpx で Supabase upsert を非同期実行
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
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional, Tuple

import aiohttp
import asyncpg
import pyarrow.parquet as pq
from dotenv import load_dotenv
from supabase import create_client, Client

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
OLLAMA_PORTS = [11434, 11435, 11436, 11437, 11438, 11439]  # ラウンドロビンで使用するポートリスト
EMBED_MODEL = "nomic-embed-text"
DOCUMENT_PREFIX = "search_document: "
# nomic-embed-text のコンテキスト長上限に合わせて先頭から切り捨てる
MAX_TEXT_CHARS = 100   # 6ポート並列で126t/s → 約3.2時間（実測値）
                       # 100文字=126/秒(3.2h), 150文字=93/秒(4.3h), 500文字=30/秒(13.4h)
MAX_RETRIES = 3
HTTP_CONNECTION_LIMIT = 32  # aiohttp 接続プール上限
PG_POOL_SIZE = 8           # asyncpg コネクションプールサイズ


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


# ---------------------------------------------------------------------------
# Supabase クライアント初期化
# ---------------------------------------------------------------------------
def init_supabase() -> Client:
    load_dotenv(".env.local")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        log("ERROR: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が未設定です", error=True)
        sys.exit(1)
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Ollama ポートマネージャ（ラウンドロビン分散）
# ---------------------------------------------------------------------------
class OllamaPortManager:
    """起動中の Ollama ポートをプールとして管理し、1ポート1リクエストを保証する。

    asyncio.Queue をポートプールとして使用することで:
    - 常に n_ports 個のリクエストのみが並行して Ollama に送信される
    - ポートが処理完了次第すぐ次のタスクに割り当てられる
    - ラウンドロビンと異なりポート過負荷が発生しない
    """

    def __init__(self, active_ports: List[int]) -> None:
        self._ports = active_ports
        # 利用可能なポートをキューに積む（1スロット = 1ポート）
        self._pool: asyncio.Queue = asyncio.Queue()
        for p in active_ports:
            self._pool.put_nowait(p)

    @classmethod
    async def create(cls, candidates: List[int]) -> "OllamaPortManager":
        """candidates の各ポートに接続テストし、応答するポートのみを使用する。"""
        alive: List[int] = []
        async with aiohttp.ClientSession() as session:
            for port in candidates:
                try:
                    async with session.get(
                        f"http://localhost:{port}/api/tags",
                        timeout=aiohttp.ClientTimeout(total=3),
                    ) as resp:
                        if resp.status == 200:
                            alive.append(port)
                except Exception:
                    pass
        if not alive:
            log("ERROR: 起動中の Ollama インスタンスが見つかりません", error=True)
            sys.exit(1)
        log(f"Ollama 起動ポート: {alive}")
        return cls(alive)

    @asynccontextmanager
    async def acquire(self):
        """空きポートを取得し、処理完了後に自動返却するコンテキストマネージャ。"""
        port = await self._pool.get()
        try:
            yield f"http://localhost:{port}/api/embed"
        finally:
            self._pool.put_nowait(port)


# ---------------------------------------------------------------------------
# 非同期 Embedding 生成
# ---------------------------------------------------------------------------
async def create_batch_embeddings(
    session: aiohttp.ClientSession,
    texts: List[str],
    port_manager: OllamaPortManager,
) -> List[List[float]]:
    """チャンク内のテキストを aiohttp で非同期に Embedding API へ一括送信する。

    - port_manager.acquire() でポートを確保し、レスポンス受信後すぐ解放する
    - upsert はポート解放後に実行されるためポートを無駄に占有しない
    - MAX_TEXT_CHARS で先頭切り捨て（コンテキスト長超過防止）
    - 最大 MAX_RETRIES 回リトライ（各試行で別ポートを取得する可能性あり）
    """
    truncated = [DOCUMENT_PREFIX + t[:MAX_TEXT_CHARS] for t in texts]
    timeout = aiohttp.ClientTimeout(total=max(60, 10 * len(truncated)))
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            async with port_manager.acquire() as url:
                async with session.post(
                    url,
                    json={"model": EMBED_MODEL, "input": truncated},
                    timeout=timeout,
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    return data["embeddings"]
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                log(
                    f"Embedding API エラー (attempt {attempt + 1}/{MAX_RETRIES}), "
                    f"{wait}s待機: {exc}",
                    error=True,
                )
                await asyncio.sleep(wait)
    raise RuntimeError(f"Embedding生成失敗: {last_exc}")


# ---------------------------------------------------------------------------
# 非同期 Supabase upsert
# ---------------------------------------------------------------------------
async def upsert_documents(
    pool: asyncpg.Pool,
    batch_data: List[dict],
) -> None:
    """asyncpg で PostgreSQL に直接 upsert する（PostgREST バイパス）。

    INSERT ... ON CONFLICT (id) DO UPDATE で JSON エンコード不要、高速。
    pgvector は Python list[float] → vector キャスト文字列で渡す。
    """
    # pgvector は '[f1,f2,...]' 形式の文字列で INSERT できる
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
    async with pool.acquire() as conn:
        await conn.executemany(sql, records)


# ---------------------------------------------------------------------------
# 1チャンク処理
# ---------------------------------------------------------------------------
async def process_chunk(
    session: aiohttp.ClientSession,
    upsert_client: asyncpg.Pool,
    chunk_idx: int,
    df_dict: dict,
    total_rows: int,
    port_manager: OllamaPortManager,
) -> int:
    """1チャンク分の非同期 Embedding 生成 + Supabase upsert を行い投入件数を返す。

    create_batch_embeddings() がポートの acquire/release を管理しているため、
    upsert はポート解放後に実行され、他チャンクの Embedding と並行して動作する。
    """
    valid: List[Tuple[int, str, str]] = [
        (int(doc_id), str(title), str(text))
        for doc_id, title, text in zip(df_dict["id"], df_dict["title"], df_dict["text"])
        if text and str(text).strip()
    ]
    if not valid:
        return 0

    texts_only = [text for _, _, text in valid]
    # ポートは embed 中のみ保持。この行が返った時点でポートは解放済み。
    embeddings = await create_batch_embeddings(session, texts_only, port_manager)

    batch_data = [
        {"id": doc_id, "title": title, "content": text, "embedding": emb}
        for (doc_id, title, text), emb in zip(valid, embeddings)
    ]
    # ポート解放後に upsert → Ollama ポートを最大限活用
    await upsert_documents(upsert_client, batch_data)
    log(f"[chunk {chunk_idx}] upserted {len(batch_data)} records (/{total_rows})")
    return len(batch_data)


# ---------------------------------------------------------------------------
# ファイル処理（チャンクを非同期並列実行）
# ---------------------------------------------------------------------------
async def process_file(
    file_path: Path,
    chunk_size: int,
    workers: int,
    max_rows: int = 0,
    port_manager: "OllamaPortManager | None" = None,
    embed_session: "aiohttp.ClientSession | None" = None,
    upsert_client: "asyncpg.Pool | None" = None,
) -> int:
    """ファイルを iter_batches でチャンク化し、asyncio.Semaphore により指定並列数で処理する。

    - embed_session / upsert_client を外部から受け取り接続プールを再利用する
    - 渡されない場合はここで作成（単体ファイル実行時の後方互換）
    - as_completed で完了順に結果を収集し、max_rows 到達時は残タスクをキャンセル
    """
    pf = pq.ParquetFile(file_path)
    total_rows = pf.metadata.num_rows
    total_inserted = 0
    error_count = 0
    sem = asyncio.Semaphore(workers)
    if port_manager is None:
        port_manager = OllamaPortManager(OLLAMA_PORTS)

    # セッションが渡されていない場合は自分で作成（単体ファイル実行時）
    _own_session = embed_session is None
    _own_client = upsert_client is None

    async def _run_tasks(es: aiohttp.ClientSession, uc: asyncpg.Pool) -> int:
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
                    es, uc, cidx, batch.to_pydict(), total_rows, port_manager
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

    if _own_session or _own_client:
        connector = aiohttp.TCPConnector(limit=HTTP_CONNECTION_LIMIT)
        db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54325/postgres")
        pool = await asyncpg.create_pool(db_url, min_size=2, max_size=PG_POOL_SIZE)
        try:
            async with aiohttp.ClientSession(connector=connector) as es:
                await _run_tasks(es, pool)
        finally:
            await pool.close()
    else:
        await _run_tasks(embed_session, upsert_client)

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
    ollama_ports: "List[int] | None" = None,
) -> None:
    """ディレクトリ内の jawiki_*.parquet を処理する。

    files_concurrency 件までのファイルを同時に処理し、各ファイル内のチャンクも
    workers 数の Semaphore で非同期実行する。
    skip_processed=True の場合、<file>.done があればスキップする。
    """
    files = sorted(Path(parquet_dir).glob("jawiki_*.parquet"))
    if not files:
        log(f"ERROR: {parquet_dir} に jawiki_*.parquet が見つかりません", error=True)
        sys.exit(1)

    log(f"{len(files)} ファイルを処理します（同時処理ファイル数: {files_concurrency}）")
    file_sem = asyncio.Semaphore(files_concurrency)
    port_manager = await OllamaPortManager.create(ollama_ports or OLLAMA_PORTS)

    # セッション・プールをプロセス全体で共有 → 接続再利用
    db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54325/postgres")
    connector = aiohttp.TCPConnector(limit=HTTP_CONNECTION_LIMIT)
    pg_pool = await asyncpg.create_pool(db_url, min_size=2, max_size=PG_POOL_SIZE)
    try:
        async with aiohttp.ClientSession(connector=connector) as embed_session:

            async def handle_file(f: Path, idx: int) -> None:
                async with file_sem:
                    done_marker = f.with_suffix(".parquet.done")
                    if skip_processed and done_marker.exists():
                        log(f"[{idx}/{len(files)}] {f.name} スキップ（処理済マーカあり）")
                        return
                    log(f"\n=== [{idx}/{len(files)}] {f.name} ===")
                    total = await process_file(
                        f, chunk_size, workers, max_rows,
                        port_manager, embed_session, pg_pool,
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
        default=12,
        help="ファイル内チャンクの非同期並列数（デフォルト: 12、ポート数×2が目安）",
    )
    parser.add_argument(
        "--files",
        type=int,
        default=6,
        help="同時処理ファイル数（デフォルト: 6）",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="最大処理件数（テスト・部分投入用。0=無制限）",
    )
    parser.add_argument(
        "--ollama-ports",
        type=lambda s: [int(p) for p in s.split(",")],
        default=None,
        help="Ollama ポートリスト カンマ区切り（デフォルト: 11434,11435,11436,11437,11438,11439）",
    )
    parser.add_argument(
        "--skip-processed",
        action="store_true",
        help=".done マーカがあるファイルをスキップ（停止後の再開用）",
    )
    args = parser.parse_args()

    load_dotenv(".env.local")
    db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54325/postgres")
    supabase_url = os.environ.get("SUPABASE_URL", "(不明)")

    log(f"接続先DB: {db_url}")
    log(f"接続先Supabase: {supabase_url}")
    log(
        f"Parquet: {args.parquet}  chunk_size={args.chunk_size}  "
        f"workers={args.workers}  files={args.files}  "
        f"max_rows={args.max_rows or '無制限'}  "
        f"ollama_ports={args.ollama_ports or OLLAMA_PORTS}"
    )
    ports = args.ollama_ports or OLLAMA_PORTS

    target = Path(args.parquet)

    async def _run_file() -> int:
        pm = await OllamaPortManager.create(ports)
        connector = aiohttp.TCPConnector(limit=HTTP_CONNECTION_LIMIT)
        db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54325/postgres")
        pg_pool = await asyncpg.create_pool(db_url, min_size=2, max_size=PG_POOL_SIZE)
        try:
            async with aiohttp.ClientSession(connector=connector) as embed_session:
                return await process_file(
                    target, args.chunk_size, args.workers, args.max_rows,
                    pm, embed_session, pg_pool,
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
                ports,
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
