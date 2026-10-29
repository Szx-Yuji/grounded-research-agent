const fieldNames: Record<string, string> = {
  content: "消息",
  query: "问题",
  knowledge_base_ids: "知识库选择",
  title: "对话名称",
}

/** Read public validation messages without echoing Pydantic input values. */
export function apiErrorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object" || !("detail" in payload))
    return fallback
  const detail = payload.detail
  if (typeof detail === "string" && detail.trim()) return detail.slice(0, 2000)
  if (!Array.isArray(detail)) return fallback
  const messages = detail.slice(0, 3).flatMap((item: unknown) => {
    if (
      !item ||
      typeof item !== "object" ||
      !("msg" in item) ||
      typeof item.msg !== "string"
    )
      return []
    const location = "loc" in item && Array.isArray(item.loc) ? item.loc : []
    const field = location.find(
      (part: unknown) =>
        typeof part === "string" && part !== "body" && part !== "query",
    )
    const label =
      typeof field === "string" ? fieldNames[field] || field : "输入"
    return [`${label}：${item.msg.slice(0, 500)}`]
  })
  return messages.length ? messages.join("；") : fallback
}
