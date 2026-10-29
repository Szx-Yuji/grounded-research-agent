import { z } from "zod"

const evidenceSchema = z.object({
  source_id: z.string(),
  kind: z.enum(["knowledge", "web"]),
  content: z.string(),
  title: z.string(),
  url: z.string().nullable().optional(),
  document_id: z.string().nullable().optional(),
  chunk_index: z.number().nullable().optional(),
})

const researchRunSchema = z.object({
  id: z.string(),
  query: z.string(),
  status: z.enum(["queued", "running", "succeeded", "failed", "cancelled"]),
  knowledge_base_ids: z.array(z.string()),
  use_web: z.boolean(),
  final_report: z
    .string()
    .nullish()
    .transform((value) => value ?? ""),
  error_message: z.string().nullable().optional(),
  sub_tasks: z.array(z.string()).default([]),
  sources: z.array(evidenceSchema).default([]),
  created_at: z.string(),
  updated_at: z.string(),
})

export type Evidence = z.infer<typeof evidenceSchema>
export type ResearchRun = z.infer<typeof researchRunSchema>
export type ResearchStatus = ResearchRun["status"]
export type ResearchInput = {
  query: string
  knowledge_base_ids: string[]
  use_web: boolean
}
export type ResearchLog = {
  agent?: string
  action?: string
  detail?: unknown
  timestamp?: string
}

export type ResearchStreamEvent = { id: number } & (
  | { event: "agent_update"; data: ResearchLog }
  | { event: "analysis_token" | "report_token"; data: { text: string } }
  | { event: "complete" | "cancelled"; data: ResearchRun }
  | { event: "error"; data: { message: string } }
)

export class ResearchApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message)
    this.name = "ResearchApiError"
  }
}

const apiBase = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "")
const runsPath = "/api/v1/research/runs"

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
    if (response.status === 401 || response.status === 403) {
      localStorage.removeItem("access_token")
      window.location.assign("/login")
    }
    const payload: unknown = await response.json().catch(() => null)
    const detail = z.object({ detail: z.string() }).safeParse(payload)
    throw new ResearchApiError(
      detail.success
        ? detail.data.detail
        : `Request failed (${response.status})`,
      response.status,
    )
  }

  return response
}

export const researchApi = {
  async list(signal?: AbortSignal) {
    const response = await request(`${runsPath}?limit=100`, { signal })
    return z
      .object({ data: z.array(researchRunSchema), count: z.number() })
      .parse(await response.json())
  },
  async get(id: string, signal?: AbortSignal) {
    const response = await request(`${runsPath}/${encodeURIComponent(id)}`, {
      signal,
    })
    return researchRunSchema.parse(await response.json())
  },
  async create(input: ResearchInput) {
    const response = await request(runsPath, {
      method: "POST",
      body: JSON.stringify(input),
    })
    return researchRunSchema.parse(await response.json())
  },
  async cancel(id: string) {
    const response = await request(
      `${runsPath}/${encodeURIComponent(id)}/cancel`,
      { method: "POST" },
    )
    return researchRunSchema.parse(await response.json())
  },
  async remove(id: string) {
    await request(`${runsPath}/${encodeURIComponent(id)}`, { method: "DELETE" })
  },
}

function parseEvent(block: string): ResearchStreamEvent | null {
  let event = "message"
  let id = Number.NaN
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim()
    if (line.startsWith("id:")) id = Number(line.slice(3).trim())
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart())
  }
  if (!data.length || !Number.isSafeInteger(id) || id < 0) return null
  const payload: unknown = JSON.parse(data.join("\n"))
  if (event === "complete" || event === "cancelled") {
    return { id, event, data: researchRunSchema.parse(payload) }
  }
  if (event === "analysis_token" || event === "report_token") {
    return {
      id,
      event,
      data: z.object({ text: z.string() }).parse(payload),
    }
  }
  if (event === "error") {
    return {
      id,
      event,
      data: z.object({ message: z.string() }).parse(payload),
    }
  }
  if (event === "agent_update") {
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
  }
  return null
}

export async function streamResearchEvents({
  id,
  after,
  signal,
  onEvent,
}: {
  id: string
  after: number
  signal: AbortSignal
  onEvent: (event: ResearchStreamEvent) => void
}) {
  const response = await request(
    `${runsPath}/${encodeURIComponent(id)}/events?after=${after}`,
    { signal, headers: { Accept: "text/event-stream" } },
  )
  if (!response.body) throw new Error("The server returned an empty stream")
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let lastEventId = after
  let terminal = false

  const consume = (block: string) => {
    const parsed = parseEvent(block)
    if (!parsed || parsed.id <= lastEventId || signal.aborted) return
    onEvent(parsed)
    lastEventId = parsed.id
    terminal = ["complete", "cancelled", "error"].includes(parsed.event)
  }

  try {
    while (!terminal) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let boundary = /\r?\n\r?\n/.exec(buffer)
      while (boundary) {
        const block = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        consume(block)
        if (terminal) break
        boundary = /\r?\n\r?\n/.exec(buffer)
      }
      if (terminal || done) break
    }
    if (!terminal && buffer.trim()) consume(buffer)
    return { lastEventId, terminal }
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}
