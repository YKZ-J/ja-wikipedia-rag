/** 36進数ランダム文字列から4文字トークンを生成する */
export function randomToken4(): string {
  return Math.random().toString(36).slice(2, 6).padEnd(4, "0");
}
