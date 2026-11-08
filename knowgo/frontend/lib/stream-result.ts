/** 新旧错误帧兼容；错误追加到已收到的正文，不能覆盖部分结果。 */
export function streamErrorMessage(content: unknown): string {
  if (typeof content === 'string' && content) return content;
  if (content && typeof content === 'object' && 'message' in content &&
      typeof content.message === 'string') return content.message;
  return '服务异常，请稍后重试。';
}

export function streamResultText(text: string, error: string): string {
  return error ? [text.trimEnd(), error].filter(Boolean).join('\n\n') : text;
}
