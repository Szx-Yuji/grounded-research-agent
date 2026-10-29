import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { z } from "zod"

const callSchema = z.object({
  id: z.string(),
  parent_id: z.string().nullable(),
  kind: z.string(),
  operation: z.string(),
  model: z.string().nullable(),
  status: z.string(),
  duration_ms: z.number().nullable(),
  first_token_ms: z.number().nullable(),
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  error_type: z.string().nullable(),
})
const executionSchema = z.object({
  job_id: z.string(),
  status: z.string(),
  attempt_count: z.number(),
  calls: z.array(callSchema),
  calls_truncated: z.boolean(),
  usage: z.object({
    call_counts: z.record(z.string(), z.number()),
    known_input_tokens: z.number(),
    known_output_tokens: z.number(),
    usage_complete: z.boolean(),
    reserved_output_tokens: z.number(),
  }),
})
const statusText: Record<string, string> = {
  queued: "等待执行",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "执行中断，需重新提交",
  started: "已开始，结果待确认",
  unknown: "结果未知",
}

async function getExecution(turnId: string, signal: AbortSignal) {
  const token = localStorage.getItem("access_token")
  const base = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "")
  const response = await fetch(
    `${base}/api/v1/integrations/turns/${encodeURIComponent(turnId)}/execution`,
    {
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      signal,
    },
  )
  if (response.status === 404) return null
  if (!response.ok) throw new Error("执行记录暂时无法读取")
  return executionSchema.parse(await response.json())
}

export function ExecutionDetails({ turnId }: { turnId: string }) {
  const [open, setOpen] = useState(false)
  const execution = useQuery({
    queryKey: ["execution-details", turnId],
    queryFn: ({ signal }) => getExecution(turnId, signal),
    enabled: open,
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.status ?? "")
        ? 2000
        : false,
    retry: 1,
  })
  const value = execution.data
  return (
    <details
      className="mt-3 border-t pt-2 text-xs"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer text-muted-foreground">
        执行详情{value ? ` · ${statusText[value.status] ?? value.status}` : ""}
      </summary>
      {open && execution.isPending ? (
        <p className="mt-3">正在读取执行记录…</p>
      ) : null}
      {execution.isError ? (
        <p className="mt-3 text-destructive">
          执行记录暂时无法读取，请稍后重新展开。
        </p>
      ) : null}
      {value === null ? (
        <p className="mt-3 text-muted-foreground">
          这条历史消息没有新版执行记录。
        </p>
      ) : null}
      {value ? (
        <div className="mt-3 space-y-3">
          <p>
            第 {value.attempt_count} 次执行 · {value.calls.length} 条调用记录
          </p>
          <p className="text-muted-foreground">
            已知输入 {value.usage.known_input_tokens} / 输出{" "}
            {value.usage.known_output_tokens} tokens
            {value.usage.usage_complete ? "" : "（部分用量未知）"}
          </p>
          {value.calls.map((call) => (
            <div
              key={call.id}
              className="rounded-md border bg-background/50 p-2"
            >
              <p className="break-all font-medium">
                {call.parent_id ? "↳ " : ""}
                {call.operation} · {statusText[call.status] ?? call.status}
              </p>
              <p className="mt-1 text-muted-foreground">
                {call.model ?? call.kind}
                {call.duration_ms !== null
                  ? ` · ${(call.duration_ms / 1000).toFixed(2)} 秒`
                  : ""}
                {call.first_token_ms !== null
                  ? ` · 首片段 ${(call.first_token_ms / 1000).toFixed(2)} 秒`
                  : ""}
              </p>
            </div>
          ))}
          {value.calls_truncated ? <p>记录过多，仅显示前 200 条。</p> : null}
          <p className="break-all text-[10px] text-muted-foreground">
            运行编号 {value.job_id}
          </p>
        </div>
      ) : null}
    </details>
  )
}
