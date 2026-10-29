import { useQueryClient } from "@tanstack/react-query"
import { startTransition, useEffect, useState } from "react"

import {
  ResearchApiError,
  type ResearchLog,
  researchApi,
  streamResearchEvents,
} from "@/lib/researchApi"

type StreamState = {
  runId: string
  analysis: string
  report: string
  logs: Array<ResearchLog & { id: number }>
  connection: "connecting" | "live" | "reconnecting" | "finished"
  error: string | null
}

const newState = (runId: string): StreamState => ({
  runId,
  analysis: "",
  report: "",
  logs: [],
  connection: "connecting",
  error: null,
})

function waitForRetry(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason)
      return
    }
    const abort = () => {
      clearTimeout(timeout)
      reject(signal.reason)
    }
    const timeout = setTimeout(() => {
      signal.removeEventListener("abort", abort)
      resolve()
    }, milliseconds)
    signal.addEventListener("abort", abort, { once: true })
  })
}

export function useResearchStream(runId: string | undefined, active: boolean) {
  const [state, setState] = useState<StreamState | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!runId || !active) return
    const controller = new AbortController()
    const { signal } = controller
    setState(newState(runId))
    let cursor = 0

    const update = (fn: (previous: StreamState) => StreamState) => {
      if (signal.aborted) return
      startTransition(() => {
        setState((previous) => {
          if (signal.aborted) return previous
          return fn(previous?.runId === runId ? previous : newState(runId))
        })
      })
    }

    const run = async () => {
      let failures = 0
      while (!signal.aborted) {
        try {
          const result = await streamResearchEvents({
            id: runId,
            after: cursor,
            signal,
            onEvent(event) {
              cursor = event.id
              failures = 0
              update((previous) => {
                const next = { ...previous, connection: "live" as const }
                if (event.event === "agent_update") {
                  return {
                    ...next,
                    logs: [
                      ...previous.logs,
                      { ...event.data, id: event.id },
                    ].slice(-100),
                  }
                }
                if (event.event === "analysis_token") {
                  return {
                    ...next,
                    analysis: previous.analysis + event.data.text,
                  }
                }
                if (event.event === "report_token") {
                  return { ...next, report: previous.report + event.data.text }
                }
                if (event.event === "error") {
                  return { ...next, error: event.data.message }
                }
                return { ...next, connection: "finished" }
              })
              if (event.event === "complete" || event.event === "cancelled") {
                queryClient.setQueryData(["research-run", runId], event.data)
                void queryClient.invalidateQueries({
                  queryKey: ["research-runs"],
                })
              }
            },
          })
          if (signal.aborted) return
          cursor = result.lastEventId
          // A closed connection is not proof of completion. Read the stored state.
          const latest = await researchApi.get(runId, signal)
          if (signal.aborted) return
          queryClient.setQueryData(["research-run", runId], latest)
          if (latest.status !== "queued" && latest.status !== "running") {
            update((previous) => ({ ...previous, connection: "finished" }))
            void queryClient.invalidateQueries({ queryKey: ["research-runs"] })
            return
          }
        } catch (error) {
          if (signal.aborted) return
          if (
            error instanceof ResearchApiError &&
            [401, 403, 404].includes(error.status)
          ) {
            update((previous) => ({
              ...previous,
              error: error.message,
              connection: "finished",
            }))
            return
          }
          failures += 1
        }
        update((previous) => ({ ...previous, connection: "reconnecting" }))
        await waitForRetry(Math.min(1000 * 2 ** failures, 15000), signal)
      }
    }

    void run().catch((error: unknown) => {
      if (!signal.aborted) {
        update((previous) => ({
          ...previous,
          error: error instanceof Error ? error.message : "Connection failed",
          connection: "finished",
        }))
      }
    })
    return () => controller.abort()
  }, [runId, active, queryClient])

  return state?.runId === runId ? state : null
}
