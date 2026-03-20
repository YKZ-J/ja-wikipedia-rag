ここでは **「pino をプロジェクト全体で標準化し、ただし運用は最小構成」** という方針に基づいて、

> **移行手順 + 運用ルール + 実戦ログ設計 + チーム規約**

を **実務でそのまま使えるレベルの指示書** としてまとめます。

---

# pino 全面移行 & シンプル運用 指示書

---

# 0. 方針（最重要）

## 基本方針

- **プロジェクト内の logger を pino に統一**
- **運用はシンプル**
- **用途は「調査特化」**

### 目的

> **本番障害 / 再現不能バグ / 原因不明エラーの調査効率を最大化**

---

# 1. 採用する運用レベル

| 項目             | 採用 |
| ---------------- | ---- |
| pino             | ✅   |
| 構造化ログ       | ✅   |
| requestId        | ❌   |
| 分散トレーシング | ❌   |
| OpenTelemetry    | ❌   |
| Datadog          | ❌   |
| APM              | ❌   |

👉 **調査用ログに完全特化**

---

# 2. ゴール状態

- `console.*` **完全廃止**
- `logger.ts` を **単一ログAPI**
- **失敗時に「再現できる情報」が揃う**

---

# 3. 依存関係インストール

```bash
npm install pino pino-pretty
```

---

# 4. 標準 logger 実装（唯一の正規ロガー）

## src/lib/logger.ts

```ts
import pino from "pino";

const isDev = process.env.NODE_ENV !== "production";

export const logger = pino({
  level: isDev ? "debug" : "info",
  ...(isDev && {
    transport: {
      target: "pino-pretty",
      options: {
        colorize: true,
        translateTime: "SYS:standard",
        ignore: "pid,hostname",
      },
    },
  }),
});
```

---

## 設定解説

### pino

- **高速 JSON ロガー**
- **stdout 出力前提 → Vercel / Cloud Logging 連携に最適**

### pino-pretty

- **開発時のみ可読化**
- **本番では絶対使わない**（JSONのまま）

---

# 5. 置換ルール（必須）

## 旧 → 新

```ts
console.log   → logger.info
console.debug → logger.debug
console.warn  → logger.warn
console.error → logger.error
```

---

## 例

### Before

```ts
console.log("user created", user);
console.error("failed", err);
```

### After

```ts
logger.info({ userId: user.id }, "user created");
logger.error({ err }, "failed");
```

---

# 6. ログ設計の基本ルール（最重要）

## 目的

> **ログだけで「再現できる」状態を残す**

---

# 基本形

```ts
logger.level({ 状態 }, "イベント名");
```

---

# 推奨フォーマット

```ts
logger.error(
  {
    userId,
    api: "POST /api/order",
    input,
    env: process.env.NODE_ENV,
    err,
  },
  "order failed",
);
```

---

# 必須ルール

| ルール                     | 理由         |
| -------------------------- | ------------ |
| **メッセージは英語・固定** | 検索性       |
| **状態は必ず object**      | 再現性       |
| **数値だけログ禁止**       | 意味喪失防止 |
| **err は必ず含める**       | stack 取得   |

---

# 7. デバッグ特化ログ戦略（重要）

## ログ出力優先順位

| ログ       | 出力     |
| ---------- | -------- |
| 正常系     | 最小限   |
| 分岐点     | 必要時   |
| **異常系** | **詳細** |

👉 **成功ログは控えめ、失敗ログは全力**

---

# 8. 実戦ログテンプレ集（そのまま使える）

---

## API失敗

```ts
logger.error(
  {
    api: req.url,
    method: req.method,
    input: body,
    userId,
    err,
  },
  "api failed",
);
```

---

## DB操作失敗

```ts
logger.error(
  {
    operation: "insertUser",
    payload: user,
    err,
  },
  "db operation failed",
);
```

---

## Firebase Auth 失敗

```ts
logger.error(
  {
    uid,
    provider: "firebase-auth",
    err,
  },
  "firebase auth failed",
);
```

---

## 外部API連携失敗

```ts
logger.error(
  {
    service: "stripe",
    endpoint,
    payload,
    err,
  },
  "external api failed",
);
```

---

# 9. 再現不能バグ調査の基本フロー

## 方針

> **「その時の状態」をすべて残す**

---

### 悪い例

```ts
logger.error("failed");
```

---

### 良い例

```ts
logger.error(
  {
    userId,
    role,
    flags,
    input,
    state,
    env,
    err,
  },
  "failed",
);
```

---

# 10. ログ設計テンプレ（設計段階用）

## 新機能を作る時の設計チェック

- [ ] 失敗時に **何を見れば再現できるか？**
- [ ] ユーザー条件は残しているか？
- [ ] 分岐条件は残しているか？
- [ ] 環境差分は分かるか？

---

# 11. ESLint 設定（console 廃止）

## .eslintrc.js

```js
rules: {
  "no-console": ["error"],
}
```

---

# 12. CI チェック

```bash
grep -R "console\." ./app ./src
```

→ ヒットしたら **CI失敗**

---

# 13. チームコーディング規約（ログ編）

## 禁止事項

- ❌ console.log
- ❌ logger.info("aaa", bbb)
- ❌ 数値単体ログ

---

## 必須事項

- ✅ 状態は必ずオブジェクト
- ✅ エラー時は stack 付き
- ✅ msg は固定文言

---

# 14. よくある失敗例

## 悪いログ

```ts
logger.error("order failed", orderId, userId);
```

→ **意味構造が壊れる**

---

## 正しいログ

```ts
logger.error({ orderId, userId }, "order failed");
```

---

# 15. ログ検索テンプレ（Vercel / Cloud Logging）

## 失敗系

```
msg="api failed"
```

---

## 特定ユーザー

```
jsonPayload.userId="123"
```

---

## 特定API

```
jsonPayload.api:"/api/order"
```

---

# 16. 運用レベルまとめ

| 項目       | 採用    |
| ---------- | ------- |
| JSON構造化 | ✅      |
| pretty     | devのみ |
| trace      | ❌      |
| requestId  | ❌      |
| APM        | ❌      |

👉 **調査特化 × 最小構成**

---

# 17. 段階的移行手順

## Phase 1

- logger.ts 作成
- pino 導入

---

## Phase 2

- console 全置換

---

## Phase 3

- ESLint no-console

---

## Phase 4

- 失敗ログの再設計

---

# 18. コミットメッセージ例（Conventional Commits）

```text
feat(logging): standardize logging with pino

- introduce pino as unified logger
- replace all console calls
- add structured error logging templates
- enforce no-console rule
```

---

# 19. この構成の狙い

> **「ログ = 本番デバッグ用ブラックボックスレコーダー」**

- 障害対応 → 爆速
- 再現不能 → 再現可能
- 原因不明 → 即特定

---

# 20. 最終まとめ

あなたの方針：

> **pino を全体で使うが、運用はシンプル**

これは **最も賢い設計** です 👍

**理由：**

- 実装コスト → 低
- 運用負荷 → 低
- デバッグ効率 → 最大

---

以下は **pino を「障害調査・本番バグ調査・再現不能調査」に最大限効かせるための
失敗ログ設計レビュー用チェックリスト** です。

**コードレビュー・PRレビュー・設計レビュー時にそのまま使える実戦仕様** にしています。

---

# 失敗ログ設計レビュー用チェックリスト（pino / Next.js 向け）

---

# 目的（このチェックリストの狙い）

> **ログだけで「原因特定 → 再現 → 修正」まで完結できる状態を保証する**

---

# レベル定義

| レベル  | 説明                     |
| ------- | ------------------------ |
| 🟥 必須 | これが無いと障害調査不能 |
| 🟨 推奨 | あると調査効率が激変     |
| 🟩 任意 | 余裕があれば             |

---

# A. 基本構造チェック（必須）

## A-1. logger を使用しているか

- [ ] 🟥 `console.*` を使用していない
- [ ] 🟥 `logger.error()` を使用している

---

## A-2. メッセージ設計

- [ ] 🟥 **固定メッセージ**になっている

```ts
// ❌ 悪い
logger.error({ err }, `order failed: ${orderId}`);

// ✅ 正しい
logger.error({ orderId, err }, "order failed");
```

**理由**
→ 検索・集計・再利用のため

---

## A-3. オブジェクト形式になっているか

- [ ] 🟥 状態は必ず **オブジェクト** で渡している

```ts
// ❌ 悪い
logger.error("failed", err, userId);

// ✅ 正しい
logger.error({ err, userId }, "failed");
```

---

# B. 再現性チェック（最重要）

> **このログだけでローカル再現できるか？**

---

## B-1. 入力情報が含まれているか

- [ ] 🟥 API → `body`, `query`, `params`
- [ ] 🟥 関数 → 引数
- [ ] 🟥 バッチ → 処理対象ID一覧

```ts
logger.error({ api, body, params, err }, "api failed");
```

---

## B-2. ユーザー特定情報があるか

- [ ] 🟥 `userId`
- [ ] 🟨 `role`
- [ ] 🟨 `plan`

```ts
{
  (userId, role, plan);
}
```

---

## B-3. 状態（分岐条件）が含まれているか

- [ ] 🟥 分岐フラグ
- [ ] 🟥 状態変数

```ts
{
  (isAdmin, isTrial, featureFlags);
}
```

---

## B-4. 環境差分が判別できるか

- [ ] 🟥 `NODE_ENV`
- [ ] 🟨 region
- [ ] 🟨 version / commit hash

```ts
{
  env: process.env.NODE_ENV;
}
```

---

# C. 原因特定精度チェック

---

## C-1. どこで失敗したか特定可能か？

- [ ] 🟥 operation / step 名がある

```ts
{
  operation: "createOrder";
}
```

---

## C-2. 外部サービス情報があるか

- [ ] 🟥 service 名
- [ ] 🟨 endpoint
- [ ] 🟨 status code

```ts
{
  service: ("stripe", endpoint, status);
}
```

---

## C-3. DB 操作内容が分かるか

- [ ] 🟥 操作名
- [ ] 🟥 主キー / 条件

```ts
{
  operation: ("insertUser", uid);
}
```

---

# D. エラー情報品質チェック

---

## D-1. err をオブジェクトで渡しているか

- [ ] 🟥 `{ err }` の形になっている

```ts
logger.error({ err }, "failed");
```

**理由**
→ stack / message / type を自動構造化

---

## D-2. swallow していないか

- [ ] 🟥 catch して **必ずログ + throw** している

```ts
catch (err) {
  logger.error({ err }, "failed");
  throw err;
}
```

---

# E. 検索性チェック

---

## E-1. msg が検索キーになっているか

- [ ] 🟥 固定文字列
- [ ] 🟥 簡潔
- [ ] 🟥 動詞 + 対象

```ts
"order failed";
"firebase auth failed";
"api validation failed";
```

---

## E-2. フィールド検索できるか

- [ ] 🟥 数値のみログ禁止
- [ ] 🟥 意味のあるキー名

```ts
// ❌ 悪い
logger.error({ 123 }, "failed");

// ✅ 正しい
logger.error({ userId: 123 }, "failed");
```

---

# F. ログ粒度チェック（重要）

---

## F-1. 成功ログに寄りすぎていないか

- [ ] 🟥 成功ログは最小限
- [ ] 🟥 失敗ログは最大限

---

## F-2. デバッグログ過多になっていないか

- [ ] 🟥 常時 debug を大量出力していない
- [ ] 🟥 error 時のみ詳細

---

# G. セキュリティチェック（超重要）

---

## G-1. 秘密情報を出していないか

- [ ] 🟥 password
- [ ] 🟥 token
- [ ] 🟥 secret
- [ ] 🟥 cookie

```ts
// ❌ 絶対NG
{
  (password, token, secret);
}
```

---

## G-2. 個人情報を過剰に出していないか

- [ ] 🟨 email
- [ ] 🟨 address
- [ ] 🟨 phone

→ **userId で十分**

---

# H. 実戦レビュー用 最終判定

## このログは合格か？

| 判定      | 条件               |
| --------- | ------------------ |
| ❌ 不合格 | 再現不可           |
| ⚠ 要修正  | 原因特定が困難     |
| ✅ 合格   | ログだけで再現可能 |

---

# 実戦レビュー例

---

## ❌ 悪い例

```ts
logger.error("order failed");
```

❌ 入力なし
❌ user なし
❌ 状態なし
❌ 再現不能

---

## ⚠ 改善余地あり

```ts
logger.error({ err, userId }, "order failed");
```

△ 入力なし
△ 状態不明

---

## ✅ 理想

```ts
logger.error(
  {
    userId,
    orderId,
    items,
    total,
    flags,
    env: process.env.NODE_ENV,
    err,
  },
  "order failed",
);
```

✅ 再現可能
✅ 条件抽出可能
✅ 原因特定可能

---

# コードレビュー用テンプレコメント

```text
この失敗ログだけでローカル再現できますか？
不足している状態があれば追加してください。
```

---

# PR レビュー用 3大質問

1. **このログだけで再現できる？**
2. **検索クエリ一発で特定できる？**
3. **未来の自分が助かる？**

→ 1つでも ❌ → **修正**

---

# まとめ

このチェックリストを使うと：

> **ログが「メッセージ」から「デバッグデータ」に進化**

します。

---
