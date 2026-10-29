import type { KnowledgeAnswerSource } from "@/client"

interface StreamConversationOptions {
  knowledgeBaseId: string
  conversationId: string
  question: string
  onToken: (content: string) => void
  onSources: (sources: KnowledgeAnswerSource[]) => void
}

interface StreamEvent {
  event: string
  data: Record<string, unknown>
}

function parseEvent(block: string): StreamEvent | null {
  let event = "message"
  const dataLines: string[] = []

  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim()
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart())
  }

  if (dataLines.length === 0) return null

  return {
    event,
    data: JSON.parse(dataLines.join("\n")) as Record<string, unknown>,
  }
}

export async function streamConversationAnswer({
  knowledgeBaseId,
  conversationId,
  question,
  onToken,
  onSources,
}: StreamConversationOptions) {
  const apiUrl = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "")
  const token = localStorage.getItem("access_token")
  const response = await fetch(
    `${apiUrl}/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ question, k: 4 }),
    },
  )

  if (!response.ok) {
    if ([401, 403].includes(response.status)) {
      localStorage.removeItem("access_token")
      window.location.href = "/login"
    }

    const payload = (await response.json().catch(() => null)) as {
      detail?: string
    } | null
    throw new Error(payload?.detail || `Request failed (${response.status})`)
  }

  if (!response.body) throw new Error("The server returned an empty stream")

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  const handleEvent = (parsed: StreamEvent) => {
    if (parsed.event === "token" && typeof parsed.data.content === "string") {
      onToken(parsed.data.content)
    }

    if (parsed.event === "sources" && Array.isArray(parsed.data.sources)) {
      onSources(parsed.data.sources as KnowledgeAnswerSource[])
    }

    if (parsed.event === "error") {
      throw new Error(
        typeof parsed.data.detail === "string"
          ? parsed.data.detail
          : "Failed to generate the answer",
      )
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n")

    let boundary = buffer.indexOf("\n\n")
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary).trim()
      buffer = buffer.slice(boundary + 2)
      if (block) {
        const parsed = parseEvent(block)
        if (parsed) handleEvent(parsed)
      }
      boundary = buffer.indexOf("\n\n")
    }

    if (done) break
  }

  const trailingEvent = parseEvent(buffer.trim())
  if (trailingEvent) handleEvent(trailingEvent)
}
