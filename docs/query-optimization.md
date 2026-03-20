# 質問クエリ最適化フロー

`kb ask-wiki` で使われる `_extract_search_queries()` のチューニング・デバッグ手順。

---

## 概要

質問文から vector_queries（ベクター検索用）と title_queries（タイトル検索用）を生成するロジックを最適化する。

```
質問文
 │
 ▼
_extract_search_queries()
 ├── 指示語除去・OCR 正規化
 ├── 「〜について」「〜の」等の名詞抽出
 ├── 派生語展開 (expand_variants)
 └── vector_queries[:5], title_queries[:4] を返す
```

---

## 1. 外部辞書の編集

ハードコーディングなしで派生語・単漢字を管理できる。

### 1-1. 派生語バリエーション辞書

**ファイル**: `python/config/variants.json`

```json
{
  "exact": {
    "さくら": ["桜", "サクラ"],
    "観光名": ["観光名所", "観光地", "観光"],
    "外国人観光客": ["インバウンド", "外国人観光"]
  },
  "contains_replace": {
    "観光名称": ["観光名所", "観光地"]
  },
  "suffix_strip": ["民族", "名称"]
}
```

| セクション         | 動作                                                                                  |
| ------------------ | ------------------------------------------------------------------------------------- |
| `exact`            | キーと完全一致したとき、値のリストのプラスとして展開                                  |
| `contains_replace` | キーを含む場合、置換語のリストに変換（部分置換）                                      |
| `suffix_strip`     | 語尾がサフィックスと一致したとき、除去した形を追加（例: `"アイヌ民族"` → `"アイヌ"`） |

**変更後はサーバー再起動**で反映される（起動時に一度だけ読み込む）。

### 1-2. 単漢字許可リスト

**ファイル**: `python/config/single_kanji_whitelist.json`

```json
["禅", "山", "川"]
```

1文字の漢字はノイズになりやすいため、許可リストにある場合のみ検索クエリに追加する。

---

## 2. 抽出精度テスト

**テストスクリプト**: `python/seq_test.py`

### 基本テスト（40問）

```bash
cd /path/to/mcp-sever
source .venv/bin/activate
python python/seq_test.py
```

出力例:

```
=== Fast Extraction Test (40 cases) ===
[PASS] 北海道の観光名と春のイベントを詳しく解説して
  vq: ['北海道の観光名と春のイベントを詳しく解説して', '北海道', '観光名', ...]
  tq: ['北海道', '観光名', ...]
...
40/40 (100.0%)
```

### 観光・イベント30問チューニングテスト

```bash
python python/seq_test.py --tune30
```

30問のテストケース（`TUNING_CASES_30`）で、`tq` に期待語が含まれるかを確認する。

### DB レイテンシベンチマーク

```bash
python python/seq_test.py --bench30
```

`_retrieve_rag_docs()` を実際に呼び出してレイテンシを測定する（DB・Ollama 起動が必要）。

---

## 3. テストケースの追加方法

`python/seq_test.py` の `TUNING_CASES_30` リストに追加する。

```python
TUNING_CASES_30 = [
    # (質問文, 期待するvq語リスト, 期待するtq語リスト)
    (
        "北海道の観光名と春のイベントを1500文字程度で詳しく解説して",
        ["北海道", "観光名"],
        ["北海道"],
    ),
    # 新しいケースを追加:
    (
        "京都の伝統行事について詳しく教えて",
        ["京都", "伝統行事"],
        ["京都"],
    ),
]
```

---

## 4. デバッグ: 抽出結果の確認

テストスクリプトを使わずに直接確認する場合:

```python
# python3 -c "..." で簡易確認
import sys
sys.path.insert(0, "python")
import mcp_server as s

query = "北海道の観光名と春のイベントを詳しく解説して"
base, vq, tq = s._extract_search_queries(query)
print("base:", base)
print("vq:", vq)
print("tq:", tq)
```

---

## 5. チューニングポイント

### 5-1. 指示語除去パターン (`_RE_INSTRUCTION_SUFFIX`)

`mcp_server.py` の正規表現パターン。「教えてください」「解説してください」等を除去する。

```python
_RE_INSTRUCTION_SUFFIX = re.compile(
    r"[をにについて、も]*(まとめ(て|た記事(を作(ってください|って)?)?)?|..."
)
```

除去されすぎる/されなさすぎる場合はパターンを調整する。

### 5-2. 汎用語フィルター (`_GENERIC_SECONDARY_QUERIES`)

「概要」「説明」「方法」等のノイズ語を検索クエリから除外するセット。  
過剰フィルタリングの場合はセットから削除、不足の場合は追加する。

```python
_GENERIC_SECONDARY_QUERIES = {
    "歴代", "一覧", "概要", "情報", "特徴",
    "説明", "解説", "方法", "種類", "歴史", ...
}
```

### 5-3. title_priority() 関数

タイトル検索候補のソートに使うスコアリング。  
観光・イベント関連語を優先するチューニングが入っている。

```python
def title_priority(term: str) -> tuple[int, int]:
    score = 0
    if any(k in term for k in ("観光", "名所", "イベント", "祭", "桜", "春")):
        score += 30
    if " " in term:
        score += 8
    if term in _GENERIC_SECONDARY_QUERIES:
        score -= 20
    return score, len(term)
```

新しいテーマを優先したい場合は `k in term for k in (...)` にキーワードを追加する。

### 5-4. クエリ数の制限

```python
vector_queries = all_vector_queries[:1]   # ベクター検索は質問全文 1件のみ
title_queries = title_candidates[:1]       # タイトル検索は上位 1件のみ
```

精度を上げたい場合は `[:2]` 等に変更するが、DB 負荷と速度のトレードオフに注意する。

---

## 6. EXPLAIN ANALYZE でのパフォーマンス確認

```bash
source .env.local
psql "$DATABASE_URL" -c "
EXPLAIN ANALYZE
SELECT id, title, content
FROM documents
ORDER BY embedding <=>
  (SELECT embedding FROM documents LIMIT 1)
LIMIT 10;
"
```

期待される出力:

```
Index Scan using documents_embedding_ivfflat ...
  (actual time=XX.XXX..YYY.YYY rows=10 ...)
Planning time: ...
Execution time: ~95ms
```

`Seq Scan` が出る場合はインデックスが効いていない（演算子のミス等）。
