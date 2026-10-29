import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import {
  ArrowUp,
  ArrowUpRight,
  BookOpenText,
  Bot,
  Globe2,
  Loader2,
  MessageSquare,
  Pencil,
  Plus,
  Square,
  Trash2,
} from "lucide-react"
import { type FormEvent, useEffect, useRef, useState } from "react"

import { KnowledgeBasesService } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { useAssistantStream } from "@/hooks/useAssistantStream"
import useCustomToast from "@/hooks/useCustomToast"
import {
  AssistantApiError,
  type AssistantConversation,
  type AssistantSource,
  type AssistantTurn,
  assistantApi,
  type ConversationSettings,
} from "@/lib/assistantApi"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/chat")({
  component: ChatPage,
  validateSearch: (
    search: Record<string, unknown>,
  ): { conversation?: string } => ({
    conversation:
      typeof search.conversation === "string" ? search.conversation : undefined,
  }),
  head: () => ({ meta: [{ title: "对话 | Grounded Research" }] }),
})

const emptySettings: ConversationSettings = {
  knowledge_base_ids: [],
  use_web: false,
}
const activeStatus = (turn?: AssistantTurn | null) =>
  turn?.status === "queued" || turn?.status === "running"
const describeError = (error: unknown) =>
  error instanceof AssistantApiError
    ? error.message
    : "请求未完成，请稍后重试。"

function sourceUrl(value?: string | null) {
  if (!value) return null
  try {
    const url = new URL(value)
    return ["https:", "http:"].includes(url.protocol) ? url.href : null
  } catch {
    return null
  }
}

function MessageSources({ sources }: { sources: AssistantSource[] }) {
  if (!sources.length) return null
  return (
    <details className="mt-4 border-t pt-3 text-xs">
      <summary className="cursor-pointer font-medium text-primary">
        查看 {sources.length} 条来源
      </summary>
      <div className="mt-3 space-y-3">
        {sources.map((source, index) => {
          const url = sourceUrl(source.url)
          return (
            <details
              key={`${source.source_id}-${index}`}
              className="rounded-md border bg-background/60 p-3"
            >
              <summary className="cursor-pointer break-words font-medium leading-5">
                [{source.source_id}] {source.title}
              </summary>
              <p className="mt-2 text-muted-foreground">
                {source.kind === "web" ? "网页" : "知识库"}
                {typeof source.chunk_index === "number"
                  ? ` · 片段 ${source.chunk_index + 1}`
                  : ""}
              </p>
              <p className="mt-2 whitespace-pre-wrap break-words leading-6 text-muted-foreground">
                {source.content}
              </p>
              {url ? (
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-flex items-center gap-1 text-primary hover:underline"
                >
                  打开原文 <ArrowUpRight className="size-3" />
                </a>
              ) : null}
            </details>
          )
        })}
      </div>
    </details>
  )
}

function ChatPage() {
  const { conversation: conversationId } = Route.useSearch()
  const navigate = Route.useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [content, setContent] = useState("")
  const [draftSettings, setDraftSettings] =
    useState<ConversationSettings>(emptySettings)
  const [dialog, setDialog] = useState<{
    kind: "rename" | "delete"
    conversation: AssistantConversation
  } | null>(null)
  const [title, setTitle] = useState("")
  const [sendError, setSendError] = useState<{
    conversationId?: string
    message: string
  } | null>(null)
  const [lastTurn, setLastTurn] = useState<AssistantTurn | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const selectConversation = (id?: string) => {
    setContent("")
    setSendError(null)
    setLastTurn(null)
    void navigate({ search: id ? { conversation: id } : {}, replace: true })
  }
  const conversations = useQuery({
    queryKey: ["assistant-conversations"],
    queryFn: ({ signal }) => assistantApi.list(signal),
  })
  const conversation = useQuery({
    queryKey: ["assistant-conversation", conversationId],
    queryFn: ({ signal }) => assistantApi.get(conversationId as string, signal),
    enabled: Boolean(conversationId),
  })
  const messages = useQuery({
    queryKey: ["assistant-messages", conversationId],
    queryFn: ({ signal }) =>
      assistantApi.messages(conversationId as string, signal),
    enabled: Boolean(conversationId),
  })
  const activeTurn = useQuery({
    queryKey: ["assistant-active-turn", conversationId],
    queryFn: ({ signal }) =>
      assistantApi.activeTurn(conversationId as string, signal),
    enabled: Boolean(conversationId),
    refetchInterval: 5000,
  })
  const knowledgeBases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: async () =>
      (
        await KnowledgeBasesService.basesReadKnowledgeBases({
          query: { skip: 0, limit: 100 },
        })
      ).data,
  })
  const running = activeStatus(activeTurn.data)
  const stream = useAssistantStream(
    conversationId,
    running ? activeTurn.data?.id : undefined,
  )
  const settings = conversationId
    ? (conversation.data ?? emptySettings)
    : draftSettings
  const latestTurn =
    stream?.turn ??
    (lastTurn?.conversation_id === conversationId ? lastTurn : null)
  const streamedMessageExists = messages.data?.data.some(
    (message) =>
      message.role === "assistant" && message.turn_id === stream?.turnId,
  )
  const showStream = Boolean(
    stream &&
      !streamedMessageExists &&
      (running || stream.content) &&
      stream.turn?.status !== "cancelled",
  )
  const messageCount = messages.data?.data.length ?? 0
  const streamLength = stream?.content.length ?? 0

  useEffect(() => {
    if (messageCount || streamLength)
      bottomRef.current?.scrollIntoView({
        block: "nearest",
        behavior: "instant",
      })
  }, [messageCount, streamLength])

  const patchMutation = useMutation({
    mutationFn: ({
      id,
      input,
    }: {
      id: string
      input: Partial<ConversationSettings> & { title?: string }
    }) => assistantApi.update(id, input),
    onSuccess: (value) => {
      queryClient.setQueryData(["assistant-conversation", value.id], value)
      void queryClient.invalidateQueries({
        queryKey: ["assistant-conversations"],
      })
      setDialog(null)
    },
    onError: (error) => showErrorToast(describeError(error)),
  })
  const changeSettings = (patch: Partial<ConversationSettings>) => {
    if (conversationId)
      patchMutation.mutate({ id: conversationId, input: patch })
    else setDraftSettings((previous) => ({ ...previous, ...patch }))
  }
  const sendMutation = useMutation({
    mutationFn: async (input: {
      id?: string
      text: string
      settings: ConversationSettings
    }) => {
      let id = input.id
      if (!id) {
        const created = await assistantApi.create({
          ...input.settings,
          title: input.text.slice(0, 70),
        })
        id = created.id
        queryClient.setQueryData(["assistant-conversation", id], created)
        void queryClient.invalidateQueries({
          queryKey: ["assistant-conversations"],
        })
        void navigate({ search: { conversation: id }, replace: true })
      }
      try {
        return await assistantApi.send(id, input.text)
      } catch (error) {
        setSendError({ conversationId: id, message: describeError(error) })
        throw error
      }
    },
    onSuccess: (turn) => {
      setContent("")
      setSendError(null)
      setLastTurn(turn)
      queryClient.setQueryData(
        ["assistant-active-turn", turn.conversation_id],
        turn,
      )
      void queryClient.invalidateQueries({
        queryKey: ["assistant-messages", turn.conversation_id],
      })
      void queryClient.invalidateQueries({
        queryKey: ["assistant-conversations"],
      })
    },
    onError: (error) => showErrorToast(describeError(error)),
  })
  const cancelMutation = useMutation({
    mutationFn: ({ id, turnId }: { id: string; turnId: string }) =>
      assistantApi.cancel(id, turnId),
    onSuccess: (turn) => {
      setLastTurn(turn)
      queryClient.setQueryData(
        ["assistant-turn", turn.conversation_id, turn.id],
        turn,
      )
      queryClient.setQueryData(
        ["assistant-active-turn", turn.conversation_id],
        null,
      )
      void queryClient.invalidateQueries({
        queryKey: ["assistant-messages", turn.conversation_id],
      })
    },
    onError: (error) => showErrorToast(describeError(error)),
  })
  const deleteMutation = useMutation({
    mutationFn: assistantApi.remove,
    onSuccess: (_, id) => {
      if (conversationId === id) selectConversation()
      for (const prefix of [
        "assistant-conversation",
        "assistant-messages",
        "assistant-active-turn",
      ])
        queryClient.removeQueries({ queryKey: [prefix, id] })
      void queryClient.invalidateQueries({
        queryKey: ["assistant-conversations"],
      })
      setDialog(null)
      showSuccessToast("对话已删除")
    },
    onError: (error) => showErrorToast(describeError(error)),
  })
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!content.trim() || running || sendMutation.isPending) return
    setSendError(null)
    setLastTurn(null)
    sendMutation.mutate({
      id: conversationId,
      text: content.trim(),
      settings: {
        knowledge_base_ids: settings.knowledge_base_ids,
        use_web: settings.use_web,
      },
    })
  }
  const configDisabled =
    running ||
    patchMutation.isPending ||
    sendMutation.isPending ||
    Boolean(conversationId && !conversation.data)
  const currentError =
    sendError && sendError.conversationId === conversationId
      ? sendError.message
      : null

  return (
    <div className="grid min-h-[calc(100dvh-14rem)] gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
      <aside className="min-w-0 border-b pb-5 lg:border-r lg:border-b-0 lg:pr-5">
        <Button
          className="mb-4 w-full"
          variant="outline"
          onClick={() => selectConversation()}
          disabled={sendMutation.isPending}
        >
          <Plus /> 新对话
        </Button>
        <h2 className="mb-3 text-xs font-medium text-muted-foreground">
          最近对话
        </h2>
        {conversations.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : conversations.isError ? (
          <div className="text-sm text-muted-foreground">
            对话列表加载失败。
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void conversations.refetch()}
            >
              重试
            </Button>
          </div>
        ) : !conversations.data?.data.length ? (
          <p className="py-4 text-sm leading-6 text-muted-foreground">
            从一个问题开始，之后可以在同一段对话里继续。
          </p>
        ) : (
          <div className="max-h-48 space-y-1 overflow-y-auto lg:max-h-[70dvh]">
            {conversations.data.data.map((item) => (
              <div
                key={item.id}
                className="group relative rounded-md hover:bg-muted/60"
              >
                <button
                  type="button"
                  aria-pressed={conversationId === item.id}
                  onClick={() => selectConversation(item.id)}
                  disabled={sendMutation.isPending}
                  className={cn(
                    "w-full rounded-md border border-transparent px-3 py-3 pr-14 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    conversationId === item.id && "border-border bg-muted/60",
                  )}
                >
                  <span className="line-clamp-2 break-words leading-5">
                    {item.title}
                  </span>
                </button>
                <div className="absolute top-2 right-1 flex opacity-60 group-hover:opacity-100">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`重命名：${item.title}`}
                    disabled={running && conversationId === item.id}
                    onClick={() => {
                      setTitle(item.title)
                      setDialog({ kind: "rename", conversation: item })
                    }}
                  >
                    <Pencil className="size-3" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`删除：${item.title}`}
                    disabled={running && conversationId === item.id}
                    onClick={() =>
                      setDialog({ kind: "delete", conversation: item })
                    }
                  >
                    <Trash2 className="size-3" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
        <p className="mt-5 text-xs leading-5 text-muted-foreground">
          需要独立长篇报告？
          <Link to="/research" className="text-primary hover:underline">
            打开深度研究
          </Link>
        </p>
      </aside>

      <section className="flex min-w-0 flex-col">
        <header className="border-b pb-4">
          <div className="flex items-center gap-2">
            <Bot className="size-5 text-primary" />
            <h1 className="min-w-0 truncate text-lg font-semibold">
              {conversation.data?.title || "和 Grounded 聊聊"}
            </h1>
          </div>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            连续追问、保留上下文。需要资料时，助手会按需查询你选择的知识库。
          </p>
          <details className="mt-3 rounded-md border bg-muted/20 px-3 py-2.5">
            <summary className="cursor-pointer text-xs font-medium">
              资料范围 · {settings.knowledge_base_ids.length} 个知识库 ·{" "}
              {settings.use_web ? "允许联网" : "未开启联网"}
            </summary>
            <div className="mt-4 space-y-3">
              <label
                htmlFor="chat-use-web"
                className="flex cursor-pointer items-center gap-2 text-sm"
              >
                <Checkbox
                  id="chat-use-web"
                  checked={settings.use_web}
                  disabled={configDisabled}
                  onCheckedChange={(checked) =>
                    changeSettings({ use_web: checked === true })
                  }
                />
                <Globe2 className="size-3.5 text-muted-foreground" />
                允许搜索公开网页
              </label>
              {knowledgeBases.isPending ? (
                <Skeleton className="h-8 w-full" />
              ) : knowledgeBases.isError ? (
                <p className="text-xs text-destructive">
                  知识库加载失败，请刷新后重试。
                </p>
              ) : !knowledgeBases.data?.data.length ? (
                <p className="text-xs text-muted-foreground">
                  <Link
                    to="/knowledge-bases"
                    className="text-primary hover:underline"
                  >
                    添加知识库
                  </Link>
                  ，就能让助手在聊天中查询你的文档。
                </p>
              ) : (
                <div className="flex max-h-36 flex-wrap gap-2 overflow-y-auto">
                  {knowledgeBases.data.data.map((base) => (
                    <label
                      htmlFor={`chat-kb-${base.id}`}
                      key={base.id}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded-md border bg-background px-2.5 py-2 text-xs",
                        settings.knowledge_base_ids.includes(base.id) &&
                          "border-primary/35 bg-primary/5",
                      )}
                    >
                      <Checkbox
                        id={`chat-kb-${base.id}`}
                        checked={settings.knowledge_base_ids.includes(base.id)}
                        disabled={configDisabled}
                        onCheckedChange={(checked) =>
                          changeSettings({
                            knowledge_base_ids:
                              checked === true
                                ? [...settings.knowledge_base_ids, base.id]
                                : settings.knowledge_base_ids.filter(
                                    (id) => id !== base.id,
                                  ),
                          })
                        }
                      />
                      <BookOpenText className="size-3.5" />
                      {base.name}
                    </label>
                  ))}
                </div>
              )}
              <p className="text-xs leading-5 text-muted-foreground">
                {running
                  ? "回复完成后可以更改资料范围。"
                  : "不选知识库也可以普通聊天。设置对下一条消息生效。"}
              </p>
            </div>
          </details>
        </header>

        <div className="min-h-72 flex-1 py-5">
          {conversationId && (conversation.isPending || messages.isPending) ? (
            <div className="space-y-5">
              <Skeleton className="ml-auto h-16 w-2/3" />
              <Skeleton className="h-28 w-5/6" />
            </div>
          ) : conversationId && (conversation.isError || messages.isError) ? (
            <div
              role="alert"
              className="rounded-md border border-destructive/20 p-4 text-sm text-destructive"
            >
              对话加载失败，请刷新重试。
            </div>
          ) : !messageCount && !running ? (
            <div className="flex min-h-64 flex-col items-center justify-center px-4 text-center">
              <div className="mb-4 flex size-12 items-center justify-center rounded-xl border bg-primary/5">
                <MessageSquare className="size-6 text-primary" />
              </div>
              <h2 className="text-xl font-semibold">一个问题，可以接着聊。</h2>
              <p className="mt-3 max-w-lg text-sm leading-7 text-muted-foreground">
                可以讨论想法、解释概念，也可以选中知识库，让助手找到依据再回答。之后直接追问，不必重新交代背景。
              </p>
            </div>
          ) : null}
          <div className="space-y-6">
            {messages.data?.data.map((message) => (
              <article
                key={message.id}
                className={cn(
                  "flex",
                  message.role === "user" ? "justify-end" : "justify-start",
                )}
              >
                <div
                  className={cn(
                    "min-w-0 max-w-[94%] rounded-xl px-4 py-3 sm:max-w-[88%]",
                    message.role === "user"
                      ? "bg-muted"
                      : "w-full border bg-card",
                  )}
                >
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                    {message.role === "user" ? (
                      "你"
                    ) : (
                      <>
                        <Bot className="size-3.5 text-primary" /> Grounded
                      </>
                    )}
                  </p>
                  <p className="whitespace-pre-wrap break-words text-sm leading-7">
                    {message.content}
                  </p>
                  {message.role === "assistant" ? (
                    <MessageSources sources={message.sources} />
                  ) : null}
                </div>
              </article>
            ))}
            {showStream ? (
              <article className="max-w-[94%] rounded-xl border bg-card px-4 py-3 sm:max-w-[88%]">
                <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                  <Bot className="size-3.5 text-primary" /> Grounded
                </p>
                <p className="whitespace-pre-wrap break-words text-sm leading-7">
                  {stream?.content || "正在思考…"}
                </p>
              </article>
            ) : running ? (
              <p
                role="status"
                className="flex items-center gap-2 py-2 text-sm text-muted-foreground"
              >
                <Loader2 className="size-4 animate-spin text-primary" />
                正在回复…
              </p>
            ) : null}
            {stream?.logs.length ? (
              <details className="rounded-md border border-dashed px-3 py-2 text-xs">
                <summary className="cursor-pointer text-muted-foreground">
                  查看助手进度 ·{" "}
                  {stream.logs[stream.logs.length - 1].agent || "Grounded"}
                </summary>
                <div className="mt-3 max-h-52 space-y-3 overflow-y-auto">
                  {stream.logs.map((log) => (
                    <div key={log.id}>
                      <p className="font-medium">
                        {log.agent || "Grounded"} · {log.action || "处理中"}
                      </p>
                      {log.detail != null ? (
                        <p className="mt-1 whitespace-pre-wrap break-words leading-5 text-muted-foreground">
                          {typeof log.detail === "string"
                            ? log.detail
                            : JSON.stringify(log.detail)}
                        </p>
                      ) : null}
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
            {stream?.connection === "reconnecting" && running ? (
              <p role="status" className="text-xs text-muted-foreground">
                正在重新连接，回复仍在后台继续。
              </p>
            ) : null}
            {currentError || stream?.error || latestTurn?.error_message ? (
              <div
                role="alert"
                className="rounded-md border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm leading-6 text-destructive"
              >
                {currentError || stream?.error || latestTurn?.error_message}
              </div>
            ) : null}
            {latestTurn?.status === "cancelled" ? (
              <p role="status" className="text-sm text-muted-foreground">
                本次回复已停止。你可以继续发送消息。
              </p>
            ) : null}
            <div ref={bottomRef} />
          </div>
        </div>

        <form
          onSubmit={submit}
          className="mt-4 rounded-xl border bg-background p-3 shadow-sm"
        >
          <Label htmlFor="chat-message" className="sr-only">
            发送消息
          </Label>
          <textarea
            id="chat-message"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="发送消息，或继续追问…"
            rows={2}
            maxLength={10000}
            disabled={sendMutation.isPending}
            className="max-h-48 min-h-16 w-full resize-y bg-transparent px-1 py-1 text-sm leading-6 outline-none placeholder:text-muted-foreground/70"
          />
          <div className="mt-2 flex items-center justify-between gap-3">
            <p className="text-[11px] text-muted-foreground">
              {running
                ? "正在回复，可以随时停止。"
                : "对话会自动保存，可随时回来继续。"}
            </p>
            {running && conversationId && activeTurn.data ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={cancelMutation.isPending}
                onClick={() =>
                  cancelMutation.mutate({
                    id: conversationId,
                    turnId: activeTurn.data!.id,
                  })
                }
              >
                {cancelMutation.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Square className="size-4" />
                )}
                停止回复
              </Button>
            ) : (
              <Button
                type="submit"
                size="sm"
                disabled={
                  !content.trim() ||
                  sendMutation.isPending ||
                  patchMutation.isPending ||
                  Boolean(
                    conversationId &&
                      (!conversation.data || activeTurn.isPending),
                  )
                }
              >
                {sendMutation.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <ArrowUp className="size-4" />
                )}
                发送
              </Button>
            )}
          </div>
        </form>
      </section>

      <Dialog
        open={dialog !== null}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending && !patchMutation.isPending)
            setDialog(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {dialog?.kind === "rename" ? "重命名对话" : "删除这段对话？"}
            </DialogTitle>
            <DialogDescription>
              {dialog?.kind === "rename"
                ? "取一个容易找到的名字。"
                : "这段对话和其中的消息将被永久删除。"}
            </DialogDescription>
          </DialogHeader>
          {dialog?.kind === "rename" ? (
            <div className="space-y-2">
              <Label htmlFor="conversation-title">对话名称</Label>
              <Input
                id="conversation-title"
                value={title}
                maxLength={128}
                onChange={(event) => setTitle(event.target.value)}
              />
            </div>
          ) : (
            <p className="line-clamp-3 text-sm font-medium">
              {dialog?.conversation.title}
            </p>
          )}
          <DialogFooter>
            <DialogClose asChild>
              <Button
                variant="outline"
                disabled={deleteMutation.isPending || patchMutation.isPending}
              >
                取消
              </Button>
            </DialogClose>
            {dialog?.kind === "rename" ? (
              <Button
                disabled={!title.trim() || patchMutation.isPending}
                onClick={() =>
                  patchMutation.mutate({
                    id: dialog.conversation.id,
                    input: { title: title.trim() },
                  })
                }
              >
                保存名称
              </Button>
            ) : (
              <Button
                variant="destructive"
                disabled={deleteMutation.isPending}
                onClick={() => {
                  if (dialog) deleteMutation.mutate(dialog.conversation.id)
                }}
              >
                删除对话
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
