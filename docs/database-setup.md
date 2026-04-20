# データベース初期設定

ローカル Supabase (PostgreSQL + pgvector) の初回セットアップ手順。

---

## 1. 前提確認

```bash
# Supabase CLI
supabase --version  # 1.x 以上

# OrbStack または Docker Desktop が起動していること
docker ps
```

---

## 2. Supabase ローカル起動

```bash
cd /path/to/mcp-sever
supabase start
```

初回起動時に Docker イメージが取得される（数分かかる場合あり）。  
起動後に表示されるキー類を `.env.local` に記録する。

```
Started supabase local development setup.

         API URL: http://localhost:54324
     GraphQL URL: http://localhost:54324/graphql/v1
  S3 Storage URL: https://...
          DB URL: postgresql://postgres:<db-password>@127.0.0.1:54325/postgres
      Studio URL: http://localhost:54323
    Inbucket URL: http://localhost:54324/mail/api
      JWT secret: super-secret-jwt-token...
        anon key: eyJ...
service_role key: eyJ...
```

---

## 3. 環境変数設定

```bash
# .env.local を作成（.gitignore に追加済み）
# DATABASE_URL のパスワードは上記 supabase start 出力の DB URL で確認してください
cat > .env.local << 'EOF'
SUPABASE_URL=http://localhost:54324
SUPABASE_ANON_KEY=<上記 anon key>
SUPABASE_SERVICE_ROLE_KEY=<上記 service_role key>
DATABASE_URL=postgresql://postgres:<上記 DB URL のパスワード>@127.0.0.1:54325/postgres
EOF

source .env.local
```

---

## 4. マイグレーション適用

### 4-1. documents テーブル作成

```bash
psql "$DATABASE_URL" -f supabase/migrations/20260311000001_create_documents.sql
```

```sql
-- 作成される内容
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
  id BIGSERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  embedding VECTOR(768) NOT NULL
);
```

### 4-2. IVFFlat インデックス & 検索 RPC 作成

**※ データ投入完了後に実行すること（空テーブルにインデックスを貼っても精度が出ない）**

```bash
psql "$DATABASE_URL" -f supabase/migrations/20260311000002_create_ivfflat_and_rpc.sql
```

```sql
-- 作成される内容
-- IVFFlat インデックス (lists=300, 135万件 ÷ 300 ≈ 4,500件/クラスタ)
CREATE INDEX IF NOT EXISTS documents_embedding_ivfflat
  ON documents
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 300);

-- 検索 RPC (probes=20: 上位20クラスタを探索)
CREATE OR REPLACE FUNCTION search_documents(query_embedding vector(768))
RETURNS TABLE (id bigint, title text, content text)
LANGUAGE sql AS $$
  SET LOCAL ivfflat.probes = 20;
  SELECT id, title, content
  FROM documents
  ORDER BY embedding <=> query_embedding   -- vector_cosine_ops には <=> を使う
  LIMIT 10;
$$;
```

> **重要**: `ORDER BY embedding <=> query_embedding`  
> `vector_cosine_ops` インデックスに対して `<->` (L2距離) を使うとインデックスが無効になり ~32秒のフルスキャンになる。必ず `<=>` (コサイン距離) を使うこと。

---

## 5. 動作確認

```bash
# テーブル存在確認
psql "$DATABASE_URL" -c "\dt documents"

# インデックス確認
psql "$DATABASE_URL" -c "\di documents_embedding_ivfflat"

# RPC 関数確認
psql "$DATABASE_URL" -c "\df search_documents"

# レコード数確認
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM documents;"
```

---

## 6. Supabase 停止・再起動

```bash
# 停止
supabase stop

# 再起動
supabase start

# データを保持したまま再起動（--no-backup で高速化）
supabase stop --no-backup && supabase start
```

---

## 7. ボリューム永続化確認

OrbStack / Docker Desktop を再起動してもデータが消えないことを確認する。

```bash
# コンテナのボリューム確認
docker ps --format '{{.Names}}'
docker inspect supabase_db_mcp-sever --format '{{json .Mounts}}' | jq

# 再起動後のレコード数が変わらないことを確認
supabase stop && supabase start
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM documents;"
```

Named Volume または Bind Mount が設定されていれば再起動後もデータは保持される。  
`supabase/config.toml` の `db.data_dir` で永続化先を設定できる。

---

## 8. スキーマ変更手順

| 手順 | 内容                                                                  |
| ---- | --------------------------------------------------------------------- |
| 1    | `supabase/migrations/` に新しい `.sql` ファイルを追加                 |
| 2    | `psql "$DATABASE_URL" -f supabase/migrations/<新ファイル>.sql` で適用 |
| 3    | リモート DB を直接変更しない（ローカル DB が Single Source of Truth） |

---

## 9. パフォーマンスチューニング（参考）

| パラメータ       | 現在値 | 説明                                   |
| ---------------- | ------ | -------------------------------------- |
| `ivfflat.lists`  | 300    | クラスタ数（データ件数 ÷ 1000 が目安） |
| `ivfflat.probes` | 20     | 探索クラスタ数（増やすと精度↑/速度↓）  |
| `LIMIT`          | 10     | 返却件数                               |

probes を上げる場合は RPC 関数の `SET LOCAL ivfflat.probes = 20;` を変更して再適用する。
