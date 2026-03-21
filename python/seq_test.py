#!/usr/bin/env python3
"""
40件の高速検索テスト（Gemma3 / ドキュメント保存なし）

デフォルト: 抽出品質 + embedding速度計測（DBなし）
    python python/seq_test.py

DBスモークも実施:
    python python/seq_test.py --db
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

os.chdir(str(Path(__file__).parent.parent))
sys.path.insert(0, ".")

import aiohttp

from python.mcp_server import _build_rag_prompt, _extract_search_queries, get_db_url, _retrieve_rag_docs

GENERIC = {
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
}

# (質問文, 抽出クエリに含まれてほしい語)
TEST_CASES: list[tuple[str, list[str]]] = [
    ("富士山の標高は？", ["富士山", "標高"]),
    ("北海道の観光名所を教えて", ["北海道", "観光"]),
    ("京都の観光地を教えて", ["京都", "観光"]),
    ("沖縄の気候について教えて", ["沖縄", "気候"]),
    ("東京の人口はどのくらいですか", ["東京", "人口"]),
    ("日本海の面積と特徴を教えて", ["日本海", "面積"]),
    ("琵琶湖の大きさを教えて", ["琵琶湖"]),
    ("エベレストの標高は？", ["エベレスト", "標高"]),
    ("江戸時代の文化と風俗を教えて", ["江戸時代", "文化"]),
    ("明治維新とは何ですか", ["明治維新"]),
    ("戦国時代の有名な武将を教えて", ["戦国時代", "武将"]),
    ("フランス革命について教えて", ["フランス革命"]),
    ("日本の歴代総理大臣を教えて", ["総理大臣"]),
    ("織田信長の生涯について教えて", ["織田信長"]),
    ("徳川家康はどういう人物ですか", ["徳川家康"]),
    ("ナポレオンについて教えて", ["ナポレオン"]),
    ("相撲の歴史と歴代の横綱を教えて", ["相撲", "横綱"]),
    ("サッカーワールドカップの歴史を教えて", ["ワールドカップ"]),
    ("オリンピックの起源について教えて", ["オリンピック"]),
    ("柔道の技の種類を教えて", ["柔道"]),
    ("歌舞伎とはどんな芸能ですか", ["歌舞伎"]),
    ("茶道の文化について教えて", ["茶道"]),
    ("浮世絵の歴史を教えて", ["浮世絵"]),
    ("琉球の文化について教えて", ["琉球"]),
    ("寿司の歴史を教えて", ["寿司"]),
    ("天ぷらの歴史について教えて", ["天ぷら"]),
    ("ラーメンの種類を教えて", ["ラーメン"]),
    ("カレーの歴史について教えて", ["カレー"]),
    ("相対性理論について教えて", ["相対性理論"]),
    ("量子力学とは何ですか", ["量子力学"]),
    ("人工知能の歴史を教えて", ["人工知能"]),
    ("ブラックホールとは何ですか", ["ブラックホール"]),
    ("地球温暖化の原因を教えて", ["地球温暖化"]),
    ("仏教の基本的な教えを教えて", ["仏教"]),
    ("神道とはどんな宗教ですか", ["神道"]),
    ("禅とはどういう思想ですか", ["禅"]),
    ("にほんわみのアイヌ民族について教えて。世界の少数民族との共通点も教えて", ["アイヌ", "少数民族"]),
    ("外国人観光客に人気の観光名称を教えて", ["外国人観光客", "インバウンド", "観光"]),
    ("訪日外国人に人気な日本の観光地はどこですか", ["訪日外国人", "観光地"]),
    ("さくらの種類について教えて", ["さくら", "桜", "サクラ"]),
    # 文字数指定 + 詳しくパターン
    ("日本の絶滅危惧種について教えて", ["絶滅危惧種"]),
    ("日本の絶滅危惧種の生物を詳しく教えて", ["絶滅危惧種", "生物"]),
    ("日本の絶滅危惧種の生物を1500文字程度で詳しく教えて", ["絶滅危惧種", "生物"]),
    ("日本のgdpの推移を1500文字程度で詳しく教えて", ["gdp", "推移", "国内総生産"]),
    (
        "appleとmicrosoftについてそれぞれ1500字程度で説明して",
        ["apple", "microsoft", "Apple", "Microsoft", "アップル", "マイクロソフト"],
    ),
    (
        "AppleとMicrosoftの違いを詳しく教えて",
        ["Apple", "Microsoft", "アップル", "マイクロソフト"],
    ),
    (
        "アイヌ民族について教えて。世界の少数民族との共通点も教えて。3000文字程度でできるだけ詳しく解説して",
        ["アイヌ民族", "少数民族", "共通点"],
    ),
    ("京都の伝統文化を1000文字程度でまとめて", ["京都", "伝統文化"]),
]

# 精度調整用30問（観光/イベント系を中心）
TUNING_CASES_30: list[tuple[str, list[str]]] = [
    ("北海道の観光名所を教えて", ["北海道", "観光", "観光地", "観光名所"]),
    ("北海道の春のイベントを教えて", ["北海道", "春", "イベント"]),
    ("北海道の観光名と春のイベントを詳しく", ["北海道", "観光", "イベント"]),
    ("北海道の観光名と春のイ ベントを詳しく", ["北海道", "観光", "イベント"]),
    ("札幌の春イベントと観光地を教えて", ["札幌", "春", "イベント", "観光"]),
    ("小樽の観光名所を紹介して", ["小樽", "観光", "観光名所"]),
    ("函館の観光地と祭りを教えて", ["函館", "観光", "祭"]),
    ("旭川の観光スポットを教えて", ["旭川", "観光"]),
    ("富良野の春イベントを教えて", ["富良野", "春", "イベント"]),
    ("美瑛の観光名所を教えて", ["美瑛", "観光", "観光名所"]),
    ("知床の観光地を解説して", ["知床", "観光", "観光地"]),
    ("帯広の観光名と祭りを教えて", ["帯広", "観光", "祭"]),
    ("釧路の春の観光イベントを教えて", ["釧路", "春", "観光", "イベント"]),
    ("網走の観光名所と歴史を教えて", ["網走", "観光", "観光名所"]),
    ("登別の観光スポットと春の催しを教えて", ["登別", "観光", "春"]),
    ("洞爺湖の観光名所を教えて", ["洞爺湖", "観光", "観光名所"]),
    ("阿寒湖の観光地とイベントを教えて", ["阿寒湖", "観光", "イベント"]),
    ("ニセコの観光名所を教えて", ["ニセコ", "観光", "観光名所"]),
    ("稚内の春イベントを教えて", ["稚内", "春", "イベント"]),
    ("室蘭の観光地を教えて", ["室蘭", "観光", "観光地"]),
    ("苫小牧の観光名所を教えて", ["苫小牧", "観光", "観光名所"]),
    ("千歳の観光地とイベントを教えて", ["千歳", "観光", "イベント"]),
    ("北見の観光名所を教えて", ["北見", "観光", "観光名所"]),
    ("岩見沢の春の催しと観光を教えて", ["岩見沢", "春", "観光"]),
    ("江別の観光スポットを教えて", ["江別", "観光"]),
    ("北海道で外国人観光客に人気の春イベントは？", ["北海道", "外国人観光客", "観光", "春", "イベント"]),
    ("訪日外国人向けに北海道の観光名所を教えて", ["訪日外国人", "北海道", "観光", "観光名所"]),
    ("アイヌ文化と北海道の観光地の関係を教えて", ["アイヌ", "北海道", "観光"]),
    ("北海道の花見イベントと観光名所を教えて", ["北海道", "花見", "イベント", "観光"]),
    ("北海道の春祭りと観光地を教えて", ["北海道", "春", "祭", "観光地"]),
    (
        "札幌の季節ごとの観光イベントを教えて。特に春について詳しく教えて",
        ["札幌", "観光", "イベント", "春"],
    ),
    ("札幌の春の観光イベントを詳しく教えて", ["札幌", "春", "観光", "イベント"]),
]

LATIN_DB_REGRESSION_CASES: list[tuple[str, list[str]]] = [
    (
        "appleとmicrosoftについてそれぞれ1500字程度で説明して",
        ["apple", "microsoft", "アップル", "マイクロソフト"],
    ),
]


def run_fast_extraction_test() -> tuple[int, list[tuple[str, list[str], list[str]]]]:
    passed = 0
    failed: list[tuple[str, list[str], list[str]]] = []

    print(f"=== FAST EXTRACTION TEST ({len(TEST_CASES)} cases) ===")
    for i, (query, expected_terms) in enumerate(TEST_CASES, 1):
        _, vq, _ = _extract_search_queries(query)
        has_expected = any(any(term in q for q in vq) for term in expected_terms)
        generic_only = all((q in GENERIC) for q in vq)
        ok = has_expected and not generic_only
        mark = "✓" if ok else "✗"
        print(f"{i:>2}. {mark} {query[:42]:<43} vq={str(vq)[:66]}")

        if ok:
            passed += 1
        else:
            failed.append((query, expected_terms, vq))

    return passed, failed


def run_tuning_extraction_test() -> tuple[int, list[tuple[str, list[str], list[str]]]]:
    passed = 0
    failed: list[tuple[str, list[str], list[str]]] = []

    print(f"\n=== TUNING EXTRACTION TEST ({len(TUNING_CASES_30)} cases) ===")
    for i, (query, expected_terms) in enumerate(TUNING_CASES_30, 1):
        _, vq, tq = _extract_search_queries(query)
        joined = " ".join(vq + tq)
        has_expected = any(term in joined for term in expected_terms)
        generic_only = all((q in GENERIC) for q in vq)
        ok = has_expected and not generic_only
        mark = "✓" if ok else "✗"
        print(f"{i:>2}. {mark} {query[:42]:<43} vq={str(vq)[:54]} tq={str(tq)[:32]}")

        if ok:
            passed += 1
        else:
            failed.append((query, expected_terms, vq + tq))

    return passed, failed


def run_prompt_verbatim_test() -> tuple[int, list[str]]:
    """Gemma3 プロンプトに質問全文が verbatim で入ることを検証する。"""
    failed: list[str] = []
    cases = [
        "アイヌ民族について 教えて。世界の少数民族との共通点も教えて。3000文字程度でできるだけ詳しく解説して",
        "日本のgdpの推移を1500文字程度で詳しく教えて",
    ]

    print("\n=== PROMPT VERBATIM TEST (2 cases) ===")
    for i, query in enumerate(cases, 1):
        prompt = _build_rag_prompt("【dummy】\n本文", query)
        ok = f"Question (verbatim):\n{query}\n\n" in prompt
        mark = "✓" if ok else "✗"
        print(f"{i:>2}. {mark} {query[:52]}")
        if not ok:
            failed.append(query)

    return len(cases) - len(failed), failed


async def run_db_smoke_test(
    cases: int = 5,
    per_query_timeout_sec: float = 12.0,
    suite: list[tuple[str, list[str]]] | None = None,
) -> tuple[int, list[tuple[str, list[str], list[str]]]]:
    passed = 0
    failed: list[tuple[str, list[str], list[str]]] = []
    db_url = get_db_url()

    source_cases = suite if suite is not None else TEST_CASES
    smoke_cases = source_cases[: max(1, min(cases, len(source_cases)))]
    print(f"\n=== OPTIONAL DB SMOKE TEST ({len(smoke_cases)} cases) ===")
    for i, (query, expected_terms) in enumerate(smoke_cases, 1):
        try:
            docs, vq, _ = await asyncio.wait_for(
                _retrieve_rag_docs(query, db_url),
                timeout=per_query_timeout_sec,
            )
        except TimeoutError:
            failed.append((query, expected_terms, ["TIMEOUT"]))
            print(f"{i:>2}. ✗ {query[:35]:<36} top1={'TIMEOUT':<28} vq0=")
            continue
        titles = [d["title"] for d in docs[:10]]
        ok = any(any(term in t for t in titles) for term in expected_terms)
        mark = "✓" if ok else "✗"
        top1 = titles[0] if titles else "NONE"
        print(f"{i:>2}. {mark} {query[:35]:<36} top1={top1[:28]:<28} vq0={vq[0][:24]}")
        if ok:
            passed += 1
        else:
            failed.append((query, expected_terms, titles))

    return passed, failed


async def run_latin_db_regression_test(
    per_query_timeout_sec: float = 15.0,
) -> tuple[int, list[tuple[str, list[str], list[str]]]]:
    passed = 0
    failed: list[tuple[str, list[str], list[str]]] = []
    db_url = get_db_url()

    print(f"\n=== LATIN DB REGRESSION TEST ({len(LATIN_DB_REGRESSION_CASES)} cases) ===")
    for i, (query, expected_terms) in enumerate(LATIN_DB_REGRESSION_CASES, 1):
        try:
            docs, vq, _ = await asyncio.wait_for(
                _retrieve_rag_docs(query, db_url),
                timeout=per_query_timeout_sec,
            )
        except TimeoutError:
            failed.append((query, expected_terms, ["TIMEOUT"]))
            print(f"{i:>2}. ✗ {query[:35]:<36} top1={'TIMEOUT':<28} vq={vq if 'vq' in locals() else []}")
            continue

        titles = [d["title"] for d in docs[:10]]
        joined = " ".join(titles).lower()
        ok = "apple" in joined and "microsoft" in joined
        mark = "✓" if ok else "✗"
        top1 = titles[0] if titles else "NONE"
        print(f"{i:>2}. {mark} {query[:35]:<36} top1={top1[:28]:<28} vq={vq[:2]}")
        if ok:
            passed += 1
        else:
            failed.append((query, expected_terms, titles))

    return passed, failed


async def run_db_latency_benchmark(
    cases: int = 30,
    per_query_timeout_sec: float = 18.0,
) -> None:
    db_url = get_db_url()
    bench_cases = TUNING_CASES_30[: max(1, min(cases, len(TUNING_CASES_30)))]
    latencies_ms: list[float] = []
    timeouts = 0

    print(f"\n=== DB LATENCY BENCH ({len(bench_cases)} tuning cases) ===")
    for i, (query, _) in enumerate(bench_cases, 1):
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(_retrieve_rag_docs(query, db_url), timeout=per_query_timeout_sec)
            latencies_ms.append((time.perf_counter() - t0) * 1000)
            print(f"{i:>2}. ✓ {query[:35]:<36} {latencies_ms[-1]:7.1f} ms")
        except TimeoutError:
            timeouts += 1
            print(f"{i:>2}. ✗ {query[:35]:<36} TIMEOUT")

    latencies_ms.sort()
    n = len(latencies_ms)
    p50 = latencies_ms[n // 2] if n else 0
    p90 = latencies_ms[int(n * 0.9)] if n else 0
    p99 = latencies_ms[int(n * 0.99) - 1] if n >= 2 else p90
    avg = sum(latencies_ms) / n if n else 0
    print(f"done: {n}/{len(bench_cases)} timeouts={timeouts}")
    print(f"avg/p50/p90/p99: {avg:.1f}/{p50:.1f}/{p90:.1f}/{p99:.1f} ms")


async def run_embedding_benchmark() -> None:
    """40件の主クエリ embedding を計測（DB接続なし）。"""
    embed_inputs = []
    for query, _ in TEST_CASES:
        _, vq, _ = _extract_search_queries(query)
        # 主軸クエリ + 先頭補助1件までを対象にして短時間化
        embed_inputs.append(vq[0])
        if len(vq) > 1:
            embed_inputs.append(vq[1])

    # 重複を除いて計測件数を抑える
    unique_inputs = list(dict.fromkeys(embed_inputs))
    sem = asyncio.Semaphore(8)
    durations_ms: list[float] = []

    async def embed_one(session: aiohttp.ClientSession, text: str) -> None:
        async with sem:
            t0 = time.perf_counter()
            async with session.post(
                "http://localhost:11434/api/embed",
                json={"model": "nomic-embed-text", "input": "search_query: " + text},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                resp.raise_for_status()
                await resp.json()
            durations_ms.append((time.perf_counter() - t0) * 1000)

    t_all = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*[embed_one(session, text) for text in unique_inputs])
    total_ms = (time.perf_counter() - t_all) * 1000

    durations_ms.sort()
    n = len(durations_ms)
    p50 = durations_ms[n // 2] if n else 0
    p90 = durations_ms[int(n * 0.9)] if n else 0
    p99 = durations_ms[int(n * 0.99) - 1] if n >= 2 else p90

    print("\n=== EMBEDDING BENCH (DBなし) ===")
    print(f"requests: {n}")
    print(f"total: {total_ms:.1f} ms")
    print(f"avg: {(sum(durations_ms) / n if n else 0):.1f} ms")
    print(f"p50/p90/p99: {p50:.1f}/{p90:.1f}/{p99:.1f} ms")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", action="store_true", help="DBスモークテストも実行")
    parser.add_argument("--db-cases", type=int, default=5, help="DBスモーク件数（既定:5）")
    parser.add_argument(
        "--db-timeout",
        type=float,
        default=12.0,
        help="DBスモークの1クエリあたりタイムアウト秒（既定:12）",
    )
    parser.add_argument(
        "--tune30",
        action="store_true",
        help="観光/イベント中心の新規30問テストを実行",
    )
    parser.add_argument(
        "--bench30",
        action="store_true",
        help="新規30問でDB検索レイテンシを計測",
    )
    args = parser.parse_args()

    passed, failed = run_fast_extraction_test()
    total = len(TEST_CASES)
    rate = passed / total
    print(f"\nFAST RESULT: {passed}/{total} ({rate:.1%})")

    if failed:
        print("\nFAST FAILED CASES:")
        for q, exp, vq in failed:
            print(f"- Q: {q}")
            print(f"  expected: {exp}")
            print(f"  vq: {vq}")

    p_passed, p_failed = run_prompt_verbatim_test()
    p_total = 2
    p_rate = p_passed / p_total
    print(f"\nPROMPT VERBATIM RESULT: {p_passed}/{p_total} ({p_rate:.1%})")
    if p_failed:
        print("\nPROMPT VERBATIM FAILED CASES:")
        for q in p_failed:
            print(f"- Q: {q}")

    await run_embedding_benchmark()

    if args.tune30:
        t_passed, t_failed = run_tuning_extraction_test()
        t_total = len(TUNING_CASES_30)
        t_rate = t_passed / t_total
        print(f"\nTUNING30 RESULT: {t_passed}/{t_total} ({t_rate:.1%})")
        if t_failed:
            print("\nTUNING30 FAILED CASES:")
            for q, exp, out in t_failed:
                print(f"- Q: {q}")
                print(f"  expected: {exp}")
                print(f"  out: {out[:6]}")

    if args.bench30:
        await run_db_latency_benchmark(cases=30, per_query_timeout_sec=max(12.0, args.db_timeout))

    if args.db:
        db_passed, db_failed = await run_db_smoke_test(
            cases=args.db_cases,
            per_query_timeout_sec=args.db_timeout,
        )
        db_total = min(args.db_cases, len(TEST_CASES))
        db_rate = db_passed / db_total
        print(f"\nDB SMOKE RESULT: {db_passed}/{db_total} ({db_rate:.1%})")
        if db_failed:
            print("\nDB FAILED CASES:")
            for q, exp, tops in db_failed:
                print(f"- Q: {q}")
                print(f"  expected: {exp}")
                print(f"  top: {tops[:5]}")

    if args.db and args.tune30:
        t_db_passed, t_db_failed = await run_db_smoke_test(
            cases=30,
            per_query_timeout_sec=args.db_timeout,
            suite=TUNING_CASES_30,
        )
        t_db_total = min(30, len(TUNING_CASES_30))
        t_db_rate = t_db_passed / t_db_total
        print(f"\nTUNING30 DB RESULT: {t_db_passed}/{t_db_total} ({t_db_rate:.1%})")
        if t_db_failed:
            print("\nTUNING30 DB FAILED CASES:")
            for q, exp, tops in t_db_failed:
                print(f"- Q: {q}")
                print(f"  expected: {exp}")
                print(f"  top: {tops[:5]}")

    if args.db:
        latin_passed, latin_failed = await run_latin_db_regression_test(
            per_query_timeout_sec=max(12.0, args.db_timeout),
        )
        latin_total = len(LATIN_DB_REGRESSION_CASES)
        latin_rate = latin_passed / latin_total if latin_total else 1.0
        print(f"\nLATIN DB REGRESSION RESULT: {latin_passed}/{latin_total} ({latin_rate:.1%})")
        if latin_failed:
            print("\nLATIN DB REGRESSION FAILED CASES:")
            for q, exp, tops in latin_failed:
                print(f"- Q: {q}")
                print(f"  expected: {exp}")
                print(f"  top: {tops[:5]}")


if __name__ == "__main__":
    asyncio.run(main())
