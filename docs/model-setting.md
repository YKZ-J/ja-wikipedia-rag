### モデル設定

現在の実装は `MODEL_PATH` を参照して GGUF モデルを読み込みます。

### 推奨 `.env.local` 設定

```bash
MODEL_DIR=/Users/ykz/programming/models
MODEL_PATH_GEMMA_2B=${MODEL_DIR}/gemma-2b-it-q4_0.gguf
MODEL_PATH_GEMMA_4B=${MODEL_DIR}/gemma-4b-it-q4_0.gguf

# 既定: 2B を使用
MODEL_PATH=${MODEL_PATH_GEMMA_2B}
```

### 切り替え方（2B ↔ 4B）

`MODEL_PATH` の右辺を変更するだけで切り替えできます。

```bash
# 2B を使う
MODEL_PATH=${MODEL_PATH_GEMMA_2B}

# 4B を使う
MODEL_PATH=${MODEL_PATH_GEMMA_4B}
```

### 反映手順

環境変数を再読み込みし、MCP サーバーを再起動してください。

```bash
set -a && source .env.local && set +a
bun run src/interface/http/mcp-server.ts
```

すでにサーバー起動中の場合は停止してから再起動してください。

### モデル配置場所

2B モデル:

`/Users/ykz/programming/models/gemma-2b-it-q4_0.gguf`

4B モデル:

`/Users/ykz/programming/models/gemma-4b-it-q4_0.gguf`
