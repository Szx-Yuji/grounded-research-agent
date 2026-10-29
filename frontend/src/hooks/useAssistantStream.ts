import { useQueryClient } from "@tanstack/react-query"
import { startTransition, useEffect, useState } from "react"

import {
  AssistantApiError,
  type AssistantTurn,
  assistantApi,
  type MessagePage,
  streamAssistantEvents,
} from "@/lib/assistantApi"
import type { ResearchLog } from "@/lib/researchApi"

type StreamState = {
  conversationId: string
  turnId: string
  content: string
  logs: Array<ResearchLog & { id: number }>
  connection: "connecting" | "live" | "reconnecting" | "finished"
  error: string | null
  turn: AssistantTurn | null
}

const newState = (conversationId: string, turnId: string): StreamState => ({
  conversationId,
  turnId,
  content: "",
  logs: [],
  connection: "connecting",
  error: null,
  turn: null,
})

function retryDelay(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason)
      return
    }
    const abort = () => {
      clearTimeout(timer)
      reject(signal.reason)
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort)
      resolve()
    }, milliseconds)
    signal.addEventListener("abort", abort, { once: true })
  })
}

export function useAssistantStream(conversationId?: string, turnId?: string) {
  const queryClient = useQueryClient()
  const [state, setState] = useState<StreamState | null>(null)

  useEffect(() => {
    if (!conversationId || !turnId) return
    const controller = new AbortController()
    const { signal } = controller
    setState(newState(conversationId, turnId))
    let cursor = 0
    const update = (fn: (previous: StreamState) => StreamState) => {
      if (signal.aborted) return
      startTransition(() =>
        setState((previous) => {
          if (signal.aborted) return previous
          return fn(
            previous?.turnId === turnId &&
              previous.conversationId === conversationId
              ? previous
              : newState(conversationId, turnId),
          )
        }),
      )
    }
    const invalidate = () => {
      void queryClient.invalidateQueries({
        queryKey: ["assistant-messages", conversationId],
      })
      void queryClient.invalidateQueries({
        queryKey: ["assistant-conversations"],
      })
    }
    const finish = (turn: AssistantTurn) => {
      queryClient.setQueryData(["assistant-turn", conversationId, turnId], turn)
      // Commit before clearing activeTurn tears down this subscription.
      setState((previous) => {
        const current =
          previous?.turnId === turnId &&
          previous.conversationId === conversationId
            ? previous
            : newState(conversationId, turnId)
        return {
          ...current,
          turn,
          error: turn.error_message || current.error,
          connection: "finished",
        }
      })
      queryClient.setQueryData(["assistant-active-turn", conversationId], null)
      invalidate()
    }
    const run = async () => {
      let failures = 0
      while (!signal.aborted) {
        try {
          const result = await streamAssistantEvents({
            id: conversationId,
            turnId,
            after: cursor,
            signal,
            onEvent(event) {
              cursor = event.id
              failures = 0
              update((previous) => {
                const next = { ...previous, connection: "live" as const }
                if (event.event === "token")
                  return {
                    ...next,
                    content: previous.content + event.data.content,
                  }
                if (event.event === "agent_update")
                  return {
                    ...next,
                    logs: [
                      ...previous.logs,
                      { ...event.data, id: event.id },
                    ].slice(-80),
                  }
                if (event.event === "error")
                  return { ...next, error: event.data.message }
                return { ...next, connection: "finished" }
              })
              if (event.event === "complete") {
                queryClient.setQueryData<MessagePage>(
                  ["assistant-messages", conversationId],
                  (previous) => {
                    if (!previous) return undefined
                    if (
                      previous.data.some(
                        (message) => message.id === event.data.message.id,
                      )
                    )
                      return previous
                    return {
                      data: [...previous.data, event.data.message],
                      count: previous.count + 1,
                    }
                  },
                )
                finish(event.data.turn)
              } else if (event.event === "cancelled") finish(event.data.turn)
            },
          })
          if (signal.aborted) return
          cursor = result.cursor
          const turn = await assistantApi.turn(conversationId, turnId, signal)
          if (signal.aborted) return
          if (turn.status !== "queued" && turn.status !== "running") {
            finish(turn)
            return
          }
        } catch (error) {
          if (signal.aborted) return
          if (
            error instanceof AssistantApiError &&
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
        await retryDelay(Math.min(1000 * 2 ** failures, 15000), signal)
      }
    }
    void run().catch((error: unknown) => {
      if (!signal.aborted)
        update((previous) => ({
          ...previous,
          error:
            error instanceof Error ? error.message : "回复连接失败，请重试。",
          connection: "finished",
        }))
    })
    return () => controller.abort()
  }, [conversationId, turnId, queryClient])

  return state &&
    state.conversationId === conversationId &&
    (!turnId || state.turnId === turnId)
    ? state
    : null
}
