/** ホワイトスペースを正規化する（連続空白→単一スペース＋前後トリム） */
export function normalizeWhitespace(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}
