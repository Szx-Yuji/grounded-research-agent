import { z } from "zod"

import type { ResearchLog } from "@/lib/researchApi"

const evidenceSchema = z.object({
  source_id: z.string(),
  kind: z.enum(["knowledge", "web"]),
  content: z.string(),
  title: z.string(),
  url: z.string().nullable().optional(),
  document_id: z.string().nullable().optional(),
  chunk_index: z.number().nullable().optional(),
})

const conversationSchema = z.object({
  id: z.string(),
  title: z.string(),
  knowledge_base_ids: z.array(z.string()),
  use_web: z.boolean(),
  summary: z
    .string()
    .nullish()
    .transform((value) => value ?? ""),
  created_at: z.string(),
  updated_at: z.string(),
})

const messageSchema = z.object({
  id: z.string(),
  conversation_id: z.string(),
  role: z.enum(["user", "assistant"]),
  content: z.string(),
  sources: z.array(evidenceSchema).default([]),
  turn_id: z.string().nullable(),
  created_at: z.string(),
})

const turnSchema = z.object({
  id: z.string(),
  conversation_id: z.string(),
  status: z.enum(["queued", "running", "succeeded", "failed", "cancelled"]),
  answer: z
    .string()
    .nullish()
    .transform((value) => value ?? ""),
  error_message: z.string().nullable(),
  sources: z.array(evidenceSchema).default([]),
  created_at: z.string(),
  updated_at: z.string(),
})

export type AssistantConversation = z.infer<typeof conversationSchema>
export type AssistantMessage = z.infer<typeof messageSchema>
export type AssistantTurn = z.infer<typeof turnSchema>
export type AssistantSource = z.infer<typeof evidenceSchema>
export type ConversationSettings = {
  knowledge_base_ids: string[]
  use_web: boolean
}
export type MessagePage = { data: AssistantMessage[]; count: number }

export type AssistantEvent = { id: number } & (
  | { event: "token"; data: { content: string } }
  | { event: "agent_update"; data: ResearchLog }
  | {
      event: "complete"
      data: { turn: AssistantTurn; message: AssistantMessage }
    }
  | { event: "cancelled"; data: { turn: AssistantTurn } }
  | { event: "error"; data: { message: string } }
)

export class AssistantApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message)
    this.name = "AssistantApiError"
  }
}

const apiBase = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "")
const conversationsPath = "/api/v1/assistant/conversations"
const conversationPath = (id: string) =>
  `${conversationsPath}/${encodeURIComponent(id)}`
const turnPath = (id: string, turnId: string) =>
  `${conversationPath(id)}/turns/${encodeURIComponent(turnId)}`

async function request(path: string, options: RequestInit = {}) {
  const token = localStorage.getItem("access_token")
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })
  if (!response.ok) {
    if ([401, 403].includes(response.status)) {
      localStorage.removeItem("access_token")
      window.location.assign("/login")
    }
    const payload = z
      .object({ detail: z.string() })
      .safeParse(await response.json().catch(() => null))
    throw new AssistantApiError(
      payload.success
        ? payload.data.detail
        : `请求未完成（${response.status}），请重试。`,
      response.status,
    )
  }
  return response
}

export const assistantApi = {
  async list(signal?: AbortSignal) {
    const response = await request(`${conversationsPath}?limit=100`, { signal })
    return z
      .object({ data: z.array(conversationSchema), count: z.number() })
      .parse(await response.json())
  },
  async create(input: ConversationSettings & { title?: string }) {
    const response = await request(conversationsPath, {
      method: "POST",
      body: JSON.stringify(input),
    })
    return conversationSchema.parse(await response.json())
  },
  async get(id: string, signal?: AbortSignal) {
    const response = await request(conversationPath(id), { signal })
    return conversationSchema.parse(await response.json())
  },
  async update(
    id: string,
    input: Partial<ConversationSettings> & { title?: string },
  ) {
    const response = await request(conversationPath(id), {
      method: "PATCH",
      body: JSON.stringify(input),
    })
    return conversationSchema.parse(await response.json())
  },
  async remove(id: string) {
    await request(conversationPath(id), { method: "DELETE" })
  },
  async messages(id: string, signal?: AbortSignal) {
    const response = await request(`${conversationPath(id)}/messages`, {
      signal,
    })
    return z
      .object({ data: z.array(messageSchema), count: z.number() })
      .parse(await response.json())
  },
  async send(id: string, content: string) {
    const response = await request(`${conversationPath(id)}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    })
    return turnSchema.parse(await response.json())
  },
  async activeTurn(id: string, signal?: AbortSignal) {
    const response = await request(`${conversationPath(id)}/turns/active`, {
      signal,
    })
    return turnSchema.nullable().parse(await response.json())
  },
  async turn(id: string, turnId: string, signal?: AbortSignal) {
    const response = await request(turnPath(id, turnId), { signal })
    return turnSchema.parse(await response.json())
  },
  async cancel(id: string, turnId: string) {
    const response = await request(`${turnPath(id, turnId)}/cancel`, {
      method: "POST",
    })
    return turnSchema.parse(await response.json())
  },
}

function parseEvent(block: string): AssistantEvent | null {
  let event = "message"
  let id = Number.NaN
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("id:")) id = Number(line.slice(3).trim())
    if (line.startsWith("event:")) event = line.slice(6).trim()
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart())
  }
  if (!Number.isSafeInteger(id) || id < 0 || !data.length) return null
  const payload: unknown = JSON.parse(data.join("\n"))
  if (event === "token")
    return { id, event, data: z.object({ content: z.string() }).parse(payload) }
  if (event === "complete")
    return {
      id,
      event,
      data: z
        .object({ turn: turnSchema, message: messageSchema })
        .parse(payload),
    }
  if (event === "cancelled")
    return { id, event, data: z.object({ turn: turnSchema }).parse(payload) }
  if (event === "error")
    return { id, event, data: z.object({ message: z.string() }).parse(payload) }
  if (event === "agent_update")
    return {
      id,
      event,
      data: z
        .object({
          agent: z.string().optional(),
          action: z.string().optional(),
          detail: z.unknown().optional(),
          timestamp: z.string().optional(),
        })
        .parse(payload),
    }
  return null
}

export async function streamAssistantEvents({
  id,
  turnId,
  after,
  signal,
  onEvent,
}: {
  id: string
  turnId: string
  after: number
  signal: AbortSignal
  onEvent: (event: AssistantEvent) => void
}) {
  const response = await request(
    `${turnPath(id, turnId)}/events?after=${after}`,
    { signal, headers: { Accept: "text/event-stream" } },
  )
  if (!response.body) throw new Error("回复连接为空，请重试。")
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let cursor = after
  let terminal = false
  const consume = (block: string) => {
    const event = parseEvent(block)
    if (!event || event.id <= cursor || signal.aborted) return
    onEvent(event)
    cursor = event.id
    terminal = ["complete", "error", "cancelled"].includes(event.event)
  }
  try {
    while (!terminal) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let boundary = /\r?\n\r?\n/.exec(buffer)
      while (boundary) {
        consume(buffer.slice(0, boundary.index))
        buffer = buffer.slice(boundary.index + boundary[0].length)
        if (terminal) break
        boundary = /\r?\n\r?\n/.exec(buffer)
      }
      if (done || terminal) break
    }
    if (!terminal && buffer.trim()) consume(buffer)
    return { cursor, terminal }
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
