# プロジェクト命名規則ガイドライン

---

## 1. 目的

本ドキュメントは、プロジェクト全体で一貫性・可読性・保守性・拡張性を高めるための**命名規則（Naming Conventions）**を定義する。

命名規則を統一することで以下の効果を狙う：

- 読みやすさ（Readability）
- 意図の明確化（Clarity of Intent）
- 保守性（Maintainability）
- 拡張性（Scalability）
- チーム開発時の衝突低減（Conflict Reduction）

---

## 2. 基本原則

### 2.1 一貫性（Consistency）

- 同じ概念には**常に同じ単語**を使用する
- 同種の対象には**同じ命名パターン**を使う

例:

```ts
isLoading;
isFetching;
isSubmitting;
```

### 2.2 意味駆動命名（Semantic Naming）

**見た目ではなく役割・意味で命名する。**

❌ 悪い例:

```ts
redButton;
bigBox;
```

✅ 良い例:

```ts
submitButton;
profileContainer;
```

### 2.3 省略しない（Avoid Abbreviation）

- 極端な略語は禁止
- 一般的略語は許容（id, url, api など）

❌

```ts
(cfg, tmp, calcFlg);
```

✅

```ts
(config, temp, isCalculated);
```

### 2.4 単語区切り規則

| 対象        | 命名形式         | 例                     |
| ----------- | ---------------- | ---------------------- |
| 変数 / 関数 | camelCase        | userName, fetchUser    |
| クラス      | PascalCase       | UserService            |
| 定数        | UPPER_SNAKE_CASE | MAX_RETRY_COUNT        |
| CSS (BEM)   | kebab-case       | card\_\_header--active |
| ファイル    | kebab-case       | user-card.tsx          |
| フォルダ    | kebab-case       | auth-layout            |

---

## 3. ディレクトリ & ファイル命名規則

### 3.1 ディレクトリ構成ルール

- すべて **kebab-case**
- 機能単位（feature-based）を基本

例:

```
app/
  (auth)/
    login/
      page.tsx
      components/
  dashboard/
    components/
    hooks/
    services/
```

### 3.2 ファイル命名

| 種類            | 命名例          |
| --------------- | --------------- |
| React Component | user-card.tsx   |
| Hook            | use-user.ts     |
| Service         | user-service.ts |
| Util            | format-date.ts  |
| 定数            | constants.ts    |

---

## 4. React コンポーネント命名

### 4.1 コンポーネント

- **PascalCase**
- 名詞 + 役割

```tsx
export function UserCard() {}
export function LoginForm() {}
```

### 4.2 Props

- camelCase
- boolean は `is`, `has`, `can` プレフィックス

```ts
isOpen;
hasError;
canSubmit;
```

### 4.3 イベントハンドラ

```ts
onClick;
onSubmit;
onChange;
```

### 4.4 コンポーネントファイル構成

```
user-card/
  user-card.tsx
  user-card.module.css
  index.ts
```

---

## 5. Hook 命名規則

### 5.1 基本

- `use` プレフィックス必須

```ts
useUser();
useAuth();
useTheme();
```

### 5.2 非同期系

```ts
useFetchUser();
useLoadProfile();
```

---

## 6. 関数命名規則

### 6.1 一般関数

- 動詞 + 目的語

```ts
fetchUser;
createPost;
updateProfile;
```

### 6.2 真偽値判定

- is / has / can / should

```ts
isLoggedIn;
hasPermission;
canAccess;
```

---

## 7. 変数 & 定数命名

### 7.1 変数

```ts
let userName: string;
let retryCount: number;
```

### 7.2 定数

```ts
const MAX_RETRY_COUNT = 3;
const API_TIMEOUT_MS = 3000;
```

---

## 8. TypeScript 型・インターフェース命名

### 8.1 型（type）

```ts
type User = {};
type AuthState = {};
```

### 8.2 Props

```ts
type UserCardProps = {};
```

### 8.3 API レスポンス

```ts
type GetUserResponse = {};
type CreatePostRequest = {};
```

---

## 9. CSS / Tailwind / BEM 命名規則

### 9.1 Tailwind 優先ルール

- **原則: Tailwind Utility First**
- 複雑な状態管理・構造表現は BEM 併用

---

## 9.2 BEM 記法（Block Element Modifier）

### 基本構造

```
.block {}
.block__element {}
.block--modifier {}
```

### 命名ルール

- **すべて kebab-case**
- Block は UI コンポーネント単位

```css
.card {
}
.card__header {
}
.card__body {
}
.card--active {
}
```

### Tailwind + BEM 併用例

```tsx
<div className="card card--active rounded-xl p-4">
  <h2 className="card__header text-lg font-bold">Title</h2>
</div>
```

---

## 10. Firebase / API 命名

### 10.1 Firestore Collection

- **複数形 / kebab-case**

```
users
blog-posts
chat-rooms
```

### 10.2 Document ID

- UUID / ULID / nanoid

---

## 11. 環境変数命名

```env
NEXT_PUBLIC_FIREBASE_API_KEY=
FIREBASE_ADMIN_PRIVATE_KEY=
```

ルール:

- 公開: `NEXT_PUBLIC_`
- サーバ専用: prefix なし

---

## 12. Git ブランチ命名

### 12.1 ブランチ

```
feature/login-form
fix/auth-redirect
refactor/user-service
chore/update-deps
```

### 12.2 Conventional Commits

```bash
feat(auth): add login form UI
fix(router): correct redirect loop
refactor(user): simplify domain logic
chore(deps): update next.js
```

---

## 13. テスト命名規則（Vitest）

```ts
user - service.test.ts;
use - auth.test.ts;
```

describe / it:

```ts
describe("useAuth", () => {
  it("should return authenticated user", () => {});
});
```

---

## 14. 命名アンチパターン集

| 悪い例 | 問題点       | 改善例         |
| ------ | ------------ | -------------- |
| data   | 意味不明     | userList       |
| flg    | 略語         | isEnabled      |
| tmp    | 一時用途不明 | tempUser       |
| func1  | 役割不明     | calculateTotal |

---

## 15. 命名チェックリスト

- [ ] 発音できるか？
- [ ] 役割が即座に想像できるか？
- [ ] 略しすぎていないか？
- [ ] 既存の命名と一貫しているか？

---

## 16. まとめ

この命名規則は以下を重視して設計されている：

- **意味駆動設計（Semantic Driven Design）**
- **可読性最優先（Readability First）**
- **Tailwind + BEM ハイブリッド設計**

---

## 付録: 用語解説

- **BEM**: Block Element Modifier
  - Yandex により提唱された CSS 設計手法
  - 再利用性と衝突回避が目的

- **kebab-case**: 単語を `-` で区切る形式

- **camelCase**: 先頭小文字 + 単語境界大文字

- **PascalCase**: すべての単語先頭大文字

---
