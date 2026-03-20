**実務レベルの「デザインパターン設計ドキュメント（コード例＋ユースケース付き）」**

> 目的：
> **設計意図が読み取れる / 迷わず実装できる / レビュー基準になる**

---

# デザインパターン設計ドキュメント

**対象:** Strategy / Factory / Adapter / Repository / Observer

---

# 全体設計マップ

```
UI (presentation)
  ↓
Factory ──→ UseCase (application)
                ↓
           Repository (interface)
                ↓
        Adapter (Firebase SDK)
```

```
Strategy ──→ 処理切替
Observer ──→ 状態監視 / イベント通知
```

---

# 1. Strategy パターン

## 目的

> **アルゴリズム（処理ロジック）を切り替え可能にする**

---

## ユースケース

- 認証方式切替
- バリデーション方式切替
- 支払いロジック切替

---

## 実装例：認証戦略切替

---

### Interface（戦略の共通契約）

```ts
export interface AuthStrategy {
  login(): Promise<void>;
}
```

#### 解説

| 構文      | 意味               |
| --------- | ------------------ |
| interface | 契約（Contract）   |
| Promise   | 非同期結果         |
| Strategy  | 差し替え可能な戦略 |

---

### Firebase 実装

```ts
export class FirebaseAuthStrategy implements AuthStrategy {
  async login() {
    console.log("Firebase login");
  }
}
```

---

### MagicLink 実装

```ts
export class MagicLinkAuthStrategy implements AuthStrategy {
  async login() {
    console.log("MagicLink login");
  }
}
```

---

### Context（戦略実行側）

```ts
export class AuthService {
  constructor(private readonly strategy: AuthStrategy) {}

  login() {
    return this.strategy.login();
  }
}
```

---

### 利用例

```ts
const service = new AuthService(new FirebaseAuthStrategy());

await service.login();
```

---

## 導入判断基準

| 状況                 | Strategy 導入 |
| -------------------- | ------------- |
| if/switch が増える   | ✅            |
| 処理差分が拡張される | ✅            |
| 単一実装のみ         | ❌            |

---

# 2. Factory パターン

## 目的

> **オブジェクト生成ロジックを一箇所に集約**

---

## ユースケース

- UseCase 組み立て
- Repository 注入
- 環境別切替（prod / test）

---

## 実装例：UseCase Factory

---

### UseCase

```ts
export class UpdateNicknameUseCase {
  constructor(private readonly repo: UserRepository) {}

  async execute(id: string, nickname: string) {
    await this.repo.update({ id, nickname });
  }
}
```

---

### Factory

```ts
export function createUpdateNicknameUseCase() {
  return new UpdateNicknameUseCase(new FirebaseUserRepository());
}
```

---

### Next.js Server Action から利用

```ts
"use server";

export async function updateNicknameAction(formData: FormData) {
  const usecase = createUpdateNicknameUseCase();

  await usecase.execute(
    String(formData.get("id")),
    String(formData.get("nickname")),
  );
}
```

---

## 導入効果

| 効果       | 内容           |
| ---------- | -------------- |
| DI         | 依存注入       |
| 変更局所化 | 実装切替が容易 |
| テスト容易 | mock 差替      |

---

# 3. Adapter パターン

## 目的

> **外部ライブラリ依存をアプリ内部から隔離**

---

## ユースケース

- Firebase SDK
- Stripe
- REST API
- S3

---

## 実装例：Firebase Adapter

---

### Repository Interface（domain）

```ts
export interface UserRepository {
  findById(id: string): Promise<User | null>;
  update(user: User): Promise<void>;
}
```

---

### Firebase Adapter（infrastructure）

```ts
export class FirebaseUserRepository implements UserRepository {
  async findById(id: string) {
    // firebase sdk
    return { id, nickname: "user" };
  }

  async update(user: User) {
    // firebase sdk
  }
}
```

---

## 導入効果

| 効果         | 内容                       |
| ------------ | -------------------------- |
| 技術変更耐性 | Firebase → Supabase も安全 |
| テスト       | SDK をモック可能           |
| 安定性       | SDK変更の影響局所化        |

---

# 4. Repository パターン

## 目的

> **永続化ロジックの抽象化**

---

## ユースケース

- Firestore
- REST API
- キャッシュ
- IndexedDB

---

## 実装例：Repository 抽象

---

### Interface

```ts
export interface ArticleRepository {
  findById(id: string): Promise<Article | null>;
  save(article: Article): Promise<void>;
}
```

---

### Firebase 実装

```ts
export class FirebaseArticleRepository implements ArticleRepository {
  async findById(id: string) {
    return null;
  }

  async save(article: Article) {}
}
```

---

### UseCase

```ts
export class GetArticleUseCase {
  constructor(private readonly repo: ArticleRepository) {}

  execute(id: string) {
    return this.repo.findById(id);
  }
}
```

---

## 設計意義

```
UseCase → Interface → 実装
```

＝ **依存性逆転（DIP）**

---

# 5. Observer パターン

## 目的

> **状態変化を自動通知**

---

## ユースケース

- Firebase Auth 状態監視
- Firestore onSnapshot
- イベント駆動処理

---

## 実装例：Auth 状態監視

---

### Observer Interface

```ts
export interface Observer<T> {
  update(value: T): void;
}
```

---

### Subject（通知元）

```ts
export class AuthSubject {
  private observers: Observer<boolean>[] = [];

  subscribe(o: Observer<boolean>) {
    this.observers.push(o);
  }

  notify(value: boolean) {
    for (const o of this.observers) {
      o.update(value);
    }
  }
}
```

---

### React Hook 連携

```ts
export function useAuthObserver(subject: AuthSubject) {
  const [state, setState] = useState(false);

  useEffect(() => {
    subject.subscribe({
      update: setState,
    });
  }, [subject]);

  return state;
}
```

---

## 実運用例：Firebase Auth

```ts
onAuthStateChanged(auth, (user) => {
  subject.notify(!!user);
});
```

---

# 6. 実務設計マッピング表

| パターン   | 実務用途              |
| ---------- | --------------------- |
| Strategy   | 認証 / バリデーション |
| Factory    | UseCase 組み立て      |
| Adapter    | Firebase SDK          |
| Repository | DB 抽象               |
| Observer   | 認証 / Firestore      |

---

# 7. 導入判断フローチャート

```
if/switch 増殖？
   ↓
Strategy

オブジェクト生成が複雑？
   ↓
Factory

SDK依存が強い？
   ↓
Adapter

DB操作が混在？
   ↓
Repository

状態監視が必要？
   ↓
Observer
```

---

# 8. 設計レビュー チェックリスト

- [ ] SDK import が domain / application にない
- [ ] new が UI に直接書かれていない
- [ ] if/switch が 5 以上 → Strategy 検討
- [ ] Repository interface 経由で永続化
- [ ] 状態監視は Observer 化

---

# 9. コミットメッセージ例（Conventional Commits）

```
feat(strategy): introduce auth strategy pattern
```

```
refactor(factory): centralize usecase creation
```

```
refactor(adapter): decouple firebase sdk via adapter
```

```
feat(repository): introduce article repository abstraction
```

```
feat(observer): add auth state observer pattern
```

---

# 最終まとめ

```
Strategy   = 処理の切替
Factory    = 生成の集約
Adapter    = 技術依存の隔離
Repository = 永続化の抽象
Observer   = 状態変化の通知
```

---
