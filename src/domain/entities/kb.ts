import type { KbFrontmatterSchema } from "../schemas/kb-frontmatter-schema";

/** バリデーション前/失敗時も含む緩い型（全フィールドOptional） */
export type KbFrontmatter = Partial<KbFrontmatterSchema>;
