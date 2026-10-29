import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Bot,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plus,
  Send,
  Trash2,
  User,
} from "lucide-react"
import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react"

import {
  type ChatMessagePublic,
  type ConversationPublic,
  ConversationsService,
  type KnowledgeAnswerSource,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import { Skeleton } from "@/components/ui/skeleton"
import useCustomToast from "@/hooks/useCustomToast"
import { streamConversationAnswer } from "@/lib/streamConversation"
import { handleError } from "@/utils"

interface ConversationWorkspaceProps {
  knowledgeBaseId: string
  hasReadyDocuments: boolean
}

type ConversationDialog =
  | { type: "rename"; conversation: ConversationPublic }
  | { type: "delete"; conversation: ConversationPublic }
  | null

const formatTime = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value))

function parseSources(sources: Array<Record<string, unknown>>) {
  return sources
    .filter(
      (source) =>
        typeof source.citation === "number" &&
        typeof source.page_content === "string" &&
        typeof source.source_metadata === "object" &&
        source.source_metadata !== null,
    )
    .map((source) => source as unknown as KnowledgeAnswerSource)
}

function SourceList({ sources }: { sources: KnowledgeAnswerSource[] }) {
  if (sources.length === 0) return null

  return (
    <div className="mt-4 border-t pt-3">
      <p className="mb-2 text-xs font-medium text-muted-foreground">Sources</p>
      <div className="space-y-2">
        {sources.map((source) => {
          const name = String(source.source_metadata.source ?? "Document")
          const chunk = source.source_metadata.chunk_index
          return (
            <details
              key={`${source.citation}-${name}-${String(chunk)}`}
              className="group rounded-md border bg-background/60 px-3 py-2 text-xs"
            >
              <summary className="cursor-pointer font-medium">
                [{source.citation}] {name}
                {typeof chunk === "number" ? ` · chunk ${chunk + 1}` : ""}
              </summary>
              <p className="mt-2 whitespace-pre-wrap leading-5 text-muted-foreground">
                {source.page_content}
              </p>
            </details>
          )
        })}
      </div>
    </div>
  )
}

export function ConversationWorkspace({
  knowledgeBaseId,
  hasReadyDocuments,
}: ConversationWorkspaceProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessagePublic[]>([])
  const [question, setQuestion] = useState("")
  const [isStreaming, setIsStreaming] = useState(false)
  const [dialog, setDialog] = useState<ConversationDialog>(null)
  const [conversationTitle, setConversationTitle] = useState("")
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const conversationsQuery = useQuery({
    queryKey: ["conversations", knowledgeBaseId],
    queryFn: async () =>
      (
        await ConversationsService.readConversations({
          path: { knowledge_base_id: knowledgeBaseId },
          query: { skip: 0, limit: 100 },
        })
      ).data,
  })

  const selectedConversation = conversationsQuery.data?.data.find(
    (conversation) => conversation.id === selectedId,
  )

  useEffect(() => {
    const conversations = conversationsQuery.data?.data ?? []
    if (conversations.length === 0) {
      setSelectedId(null)
    } else if (!conversations.some(({ id }) => id === selectedId)) {
      setSelectedId(conversations[0].id)
    }
  }, [conversationsQuery.data, selectedId])

  const messagesQuery = useQuery({
    queryKey: ["conversation-messages", knowledgeBaseId, selectedId],
    enabled: selectedId !== null,
    queryFn: async () =>
      (
        await ConversationsService.readConversationMessages({
          path: {
            knowledge_base_id: knowledgeBaseId,
            conversation_id: selectedId as string,
          },
          query: { skip: 0, limit: 100 },
        })
      ).data,
  })

  useEffect(() => {
    if (!isStreaming) setMessages(messagesQuery.data?.data ?? [])
  }, [messagesQuery.data, isStreaming])

  const latestMessage = messages[messages.length - 1]

  useEffect(() => {
    if (!latestMessage) return

    messagesEndRef.current?.scrollIntoView({
      behavior: isStreaming ? "auto" : "smooth",
    })
  }, [latestMessage, isStreaming])

  const createMutation = useMutation({
    mutationFn: () =>
      ConversationsService.createConversation({
        path: { knowledge_base_id: knowledgeBaseId },
        body: { title: "New conversation" },
      }),
    onSuccess: async (response) => {
      await queryClient.invalidateQueries({
        queryKey: ["conversations", knowledgeBaseId],
      })
      setSelectedId(response.data.id)
      setMessages([])
    },
    onError: handleError.bind(showErrorToast),
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      ConversationsService.updateConversation({
        path: {
          knowledge_base_id: knowledgeBaseId,
          conversation_id: id,
        },
        body: { title },
      }),
    onSuccess: async () => {
      setDialog(null)
      showSuccessToast("Conversation renamed")
      await queryClient.invalidateQueries({
        queryKey: ["conversations", knowledgeBaseId],
      })
    },
    onError: handleError.bind(showErrorToast),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      ConversationsService.deleteConversation({
        path: {
          knowledge_base_id: knowledgeBaseId,
          conversation_id: id,
        },
      }),
    onSuccess: async () => {
      setDialog(null)
      setSelectedId(null)
      showSuccessToast("Conversation deleted")
      await queryClient.invalidateQueries({
        queryKey: ["conversations", knowledgeBaseId],
      })
    },
    onError: handleError.bind(showErrorToast),
  })

  const sendQuestion = async (event?: FormEvent) => {
    event?.preventDefault()
    const content = question.trim()
    if (!content || !selectedId || isStreaming) return

    const now = new Date().toISOString()
    const userMessage: ChatMessagePublic = {
      id: `pending-user-${Date.now()}`,
      conversation_id: selectedId,
      role: "user",
      content,
      sources: [],
      created_at: now,
    }
    const assistantId = `pending-assistant-${Date.now()}`
    const assistantMessage: ChatMessagePublic = {
      id: assistantId,
      conversation_id: selectedId,
      role: "assistant",
      content: "",
      sources: [],
      created_at: now,
    }

    setQuestion("")
    setIsStreaming(true)
    setMessages((current) => [...current, userMessage, assistantMessage])

    try {
      await streamConversationAnswer({
        knowledgeBaseId,
        conversationId: selectedId,
        question: content,
        onToken: (token) => {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? { ...message, content: message.content + token }
                : message,
            ),
          )
        },
        onSources: (sources) => {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId ? { ...message, sources } : message,
            ),
          )
        },
      })

      if (selectedConversation?.title === "New conversation") {
        await ConversationsService.updateConversation({
          path: {
            knowledge_base_id: knowledgeBaseId,
            conversation_id: selectedId,
          },
          body: { title: content.slice(0, 60) },
        })
      }

      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["conversation-messages", knowledgeBaseId, selectedId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["conversations", knowledgeBaseId],
        }),
      ])
    } catch (error) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content:
                  error instanceof Error
                    ? `Unable to answer: ${error.message}`
                    : "Unable to answer this question.",
              }
            : message,
        ),
      )
      showErrorToast(
        error instanceof Error
          ? error.message
          : "Failed to generate the answer",
      )
    } finally {
      setIsStreaming(false)
    }
  }

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      void sendQuestion()
    }
  }

  const openRenameDialog = (conversation: ConversationPublic) => {
    setConversationTitle(conversation.title || "New conversation")
    setDialog({ type: "rename", conversation })
  }

  return (
    <div className="grid min-h-[38rem] overflow-hidden border md:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="border-b bg-muted/20 md:border-r md:border-b-0">
        <div className="flex items-center justify-between border-b px-3 py-3">
          <span className="text-sm font-semibold">Conversations</span>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="New conversation"
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
          >
            <Plus />
          </Button>
        </div>

        <div className="max-h-48 overflow-y-auto p-2 md:max-h-[34rem]">
          {conversationsQuery.isPending ? (
            <div className="space-y-2 p-1">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : conversationsQuery.data?.data.length === 0 ? (
            <div className="px-2 py-8 text-center text-xs text-muted-foreground">
              Start a conversation to ask about your documents.
            </div>
          ) : (
            <div className="space-y-1">
              {conversationsQuery.data?.data.map((conversation) => (
                <div
                  key={conversation.id}
                  className={`group flex items-center rounded-md ${
                    selectedId === conversation.id
                      ? "bg-accent text-accent-foreground"
                      : "hover:bg-accent/60"
                  }`}
                >
                  <button
                    type="button"
                    className="min-w-0 flex-1 px-2 py-2 text-left"
                    onClick={() => setSelectedId(conversation.id)}
                  >
                    <span className="block truncate text-sm font-medium">
                      {conversation.title || "New conversation"}
                    </span>
                    <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                      {formatTime(conversation.updated_at)}
                    </span>
                  </button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        className="mr-1 opacity-70 md:opacity-0 md:group-hover:opacity-100"
                        aria-label="Conversation menu"
                      >
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        onSelect={() => openRenameDialog(conversation)}
                      >
                        <Pencil />
                        Rename
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() =>
                          setDialog({ type: "delete", conversation })
                        }
                      >
                        <Trash2 />
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>

      <div className="flex min-h-[32rem] min-w-0 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          {!selectedId ? (
            <div className="flex h-full min-h-72 flex-col items-center justify-center text-center">
              <MessageSquare className="mb-3 size-6 text-muted-foreground" />
              <p className="font-medium">No conversation selected</p>
              <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                Create a conversation, then ask a question about your indexed
                documents.
              </p>
              <Button
                className="mt-4"
                size="sm"
                onClick={() => createMutation.mutate()}
                disabled={createMutation.isPending}
              >
                <Plus />
                New conversation
              </Button>
            </div>
          ) : messagesQuery.isPending ? (
            <div className="space-y-5">
              <Skeleton className="h-20 w-3/4" />
              <Skeleton className="ml-auto h-16 w-2/3" />
            </div>
          ) : messages.length === 0 ? (
            <div className="flex h-full min-h-72 flex-col items-center justify-center text-center">
              <Bot className="mb-3 size-6 text-primary" />
              <p className="font-medium">Ask your knowledge base</p>
              <p className="mt-1 max-w-md text-sm text-muted-foreground">
                Answers use only the documents in this knowledge base and show
                the matching sources.
              </p>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((message) => {
                const isUser = message.role === "user"
                const sources = isUser ? [] : parseSources(message.sources)
                return (
                  <article
                    key={message.id}
                    className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}
                  >
                    <div
                      className={`flex size-8 shrink-0 items-center justify-center rounded-md border ${
                        isUser ? "bg-muted" : "bg-primary/10 text-primary"
                      }`}
                    >
                      {isUser ? (
                        <User className="size-4" />
                      ) : (
                        <Bot className="size-4" />
                      )}
                    </div>
                    <div
                      className={`min-w-0 max-w-[85%] rounded-md px-4 py-3 text-sm leading-6 ${
                        isUser
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted/60"
                      }`}
                    >
                      {message.content ? (
                        <p className="whitespace-pre-wrap">{message.content}</p>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-muted-foreground">
                          <span className="size-1.5 animate-pulse rounded-full bg-current" />
                          <span className="size-1.5 animate-pulse rounded-full bg-current [animation-delay:150ms]" />
                          <span className="size-1.5 animate-pulse rounded-full bg-current [animation-delay:300ms]" />
                        </span>
                      )}
                      <SourceList sources={sources} />
                    </div>
                  </article>
                )
              })}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        <form className="border-t p-3 sm:p-4" onSubmit={sendQuestion}>
          {!hasReadyDocuments && (
            <p className="mb-2 text-xs text-muted-foreground">
              Upload a document and wait for it to be ready before asking.
            </p>
          )}
          <div className="flex items-end gap-2 rounded-md border bg-background p-2 focus-within:ring-2 focus-within:ring-ring/40">
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="Ask a question about these documents"
              rows={2}
              className="max-h-32 min-h-12 flex-1 resize-none bg-transparent px-2 py-1 text-sm outline-none placeholder:text-muted-foreground"
              disabled={!selectedId || !hasReadyDocuments || isStreaming}
            />
            <Button
              type="submit"
              size="icon"
              aria-label="Send question"
              disabled={
                !selectedId ||
                !hasReadyDocuments ||
                !question.trim() ||
                isStreaming
              }
            >
              <Send />
            </Button>
          </div>
        </form>
      </div>

      <Dialog
        open={dialog?.type === "rename"}
        onOpenChange={(open) => !open && setDialog(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename conversation</DialogTitle>
            <DialogDescription>
              Use a short title that makes this conversation easy to find.
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              if (dialog?.type === "rename" && conversationTitle.trim()) {
                renameMutation.mutate({
                  id: dialog.conversation.id,
                  title: conversationTitle.trim(),
                })
              }
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="conversation-title">Title</Label>
              <Input
                id="conversation-title"
                value={conversationTitle}
                onChange={(event) => setConversationTitle(event.target.value)}
                maxLength={200}
                required
              />
            </div>
            <DialogFooter>
              <DialogClose asChild>
                <Button type="button" variant="outline">
                  Cancel
                </Button>
              </DialogClose>
              <LoadingButton
                type="submit"
                loading={renameMutation.isPending}
                disabled={!conversationTitle.trim()}
              >
                Save title
              </LoadingButton>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={dialog?.type === "delete"}
        onOpenChange={(open) => !open && setDialog(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete conversation?</DialogTitle>
            <DialogDescription>
              This permanently removes the conversation and its message history.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <LoadingButton
              variant="destructive"
              loading={deleteMutation.isPending}
              onClick={() => {
                if (dialog?.type === "delete") {
                  deleteMutation.mutate(dialog.conversation.id)
                }
              }}
            >
              Delete conversation
            </LoadingButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
