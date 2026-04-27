# documents_v2 Recovery Runbook

このドキュメントは、documents_v2 の復旧を安全に行うための運用手順です。

## 目的

- 破壊的マイグレーションの誤実行を防止する
- 復旧が必要なときに再作成方式で確実に戻せるようにする

## 1. 破壊的マイグレーションの安全ガード

`supabase/migrations/20260420000002_migrate_to_ruri_v3_30m.sql` には、以下のガードを追加済みです。

- documents / documents_v2 に1件でもデータがある
- かつ `kb.allow_destructive_migration=on` が設定されていない

この条件ではマイグレーションは例外を投げて停止します。

### 意図的に実行する場合のみ

以下のように `PGOPTIONS` を付与して明示実行します。
（`kb.allow_destructive_migration=on` と `kb.confirm_destructive_migration=CONFIRM` の二重指定が必須）

```bash
PGOPTIONS="-c kb.allow_destructive_migration=on -c kb.confirm_destructive_migration=CONFIRM" \
  supabase db push --db-url "$DATABASE_URL"
```

## 2. 復旧方式（再作成方式）

再作成方式は、dump 内のテーブル/制約/インデックス定義をそのまま再構築します。
HNSW を含む dump を使うと、HNSW も復元時に再作成されます。

ただし `pg_restore --clean` は dump に含まれない補助オブジェクト
（例: `documents_v2_title_pattern_idx` や tuned RPC）も消すため、
復元直後に対象マイグレーションの再適用が必須です。

また、dump 由来で `UNLOGGED` テーブル/インデックスが復元されるケースがあり、
この場合は DB/コンテナ再起動でデータ消失が再発します。
復旧後は必ず `LOGGED` 化チェックを実施してください。

### 標準コマンド

```bash
scripts/restore-documents-v2-from-dump.sh backups/hnsw/documents_v2_hnsw_YYYYMMDD_HHMMSS.dump
```

### dump を明示する場合

```bash
scripts/restore-documents-v2-from-dump.sh backups/hnsw/documents_v2_hnsw_20260425_180212.dump
```

## 3. 再発防止（必須）

### 3-1. LOGGED / UNLOGGED を必ず確認する

```bash
set -a && source .env.local && set +a
psql "$DATABASE_URL" -Atc "
SELECT c.relname || '|' || c.relpersistence::text
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname='public'
  AND c.relname IN ('documents_v2', 'documents_v2_embedding_hnsw_cosine_idx')
ORDER BY c.relname;
"
```

- `p` = LOGGED（永続化対象）
- `u` = UNLOGGED（再起動で消失しうるため NG）

### 3-2. `u` が出たら即座に修正する

```bash
psql "$DATABASE_URL" -c "ALTER TABLE public.documents_v2 SET LOGGED;"
```

上記が完了したら、`3-1` を再実行して `documents_v2` / `documents_v2_embedding_hnsw_cosine_idx` がともに `p` であることを確認します。

### 3-3. 永続化テストは `docker restart` で行う

`supabase stop/start` はローカル環境初期化の影響を受ける場合があるため、
永続化検証では DB コンテナ再起動を使います。

```bash
# 再起動前の基準値
psql "$DATABASE_URL" -Atc "SELECT COUNT(*) || '|' || COALESCE(MIN(id),0) || '|' || COALESCE(MAX(id),0) FROM documents_v2;"
psql "$DATABASE_URL" -Atc "
WITH s AS (
  SELECT id, article_id, chunk_index FROM documents_v2 ORDER BY id ASC LIMIT 1000
), t AS (
  SELECT id, article_id, chunk_index FROM documents_v2 ORDER BY id DESC LIMIT 1000
)
SELECT
  (SELECT md5(string_agg(id::text || ':' || article_id || ':' || chunk_index::text, ',' ORDER BY id)) FROM s)
  || '|' ||
  (SELECT md5(string_agg(id::text || ':' || article_id || ':' || chunk_index::text, ',' ORDER BY id DESC)) FROM t);
"

# DB コンテナ再起動
docker restart supabase_db_mcp-sever

# 再起動後に同じ値が一致すること
psql "$DATABASE_URL" -Atc "SELECT COUNT(*) || '|' || COALESCE(MIN(id),0) || '|' || COALESCE(MAX(id),0) FROM documents_v2;"
psql "$DATABASE_URL" -Atc "
WITH s AS (
  SELECT id, article_id, chunk_index FROM documents_v2 ORDER BY id ASC LIMIT 1000
), t AS (
  SELECT id, article_id, chunk_index FROM documents_v2 ORDER BY id DESC LIMIT 1000
)
SELECT
  (SELECT md5(string_agg(id::text || ':' || article_id || ':' || chunk_index::text, ',' ORDER BY id)) FROM s)
  || '|' ||
  (SELECT md5(string_agg(id::text || ':' || article_id || ':' || chunk_index::text, ',' ORDER BY id DESC)) FROM t);
"
```

一致しない場合は復旧を完了扱いにしないこと。

## 4. 復旧後チェック

```bash
set -a && source .env.local && set +a
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM documents_v2;"
psql "$DATABASE_URL" -c "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='documents_v2' ORDER BY indexname;"
```

必要に応じて kb の実行確認:

```bash
MCP_SERVER_URL=http://localhost:3338 kb ask-wiki-report "北海道の観光名所を教えて"
```

## 5. 備考

- 再作成方式は復元の再現性が高い反面、HNSW 作成時間が必要です。
- 迅速復旧が優先なら data-only 方式も選べますが、運用の標準は再作成方式とします。
