import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock3,
  FileText,
  LoaderCircle,
  MessageSquare,
} from "lucide-react"
import { Suspense } from "react"

import { KnowledgeBasesService } from "@/client"
import { ConversationWorkspace } from "@/components/KnowledgeBases/ConversationWorkspace"
import { DocumentActions } from "@/components/KnowledgeBases/DocumentActions"
import { KnowledgeBaseActions } from "@/components/KnowledgeBases/KnowledgeBaseActions"
import { UploadDocument } from "@/components/KnowledgeBases/UploadDocument"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export const Route = createFileRoute(
  "/_layout/knowledge-bases/$knowledgeBaseId",
)({
  component: KnowledgeBasePage,
  head: () => ({ meta: [{ title: "Knowledge base | Grounded Research" }] }),
})

const formatBytes = (bytes = 0) => {
  if (bytes === 0) return "0 B"
  const units = ["B", "KB", "MB", "GB"]
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3)
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

const statusDetails = {
  uploaded: { label: "Queued", icon: Clock3, variant: "secondary" as const },
  processing: {
    label: "Processing",
    icon: LoaderCircle,
    variant: "secondary" as const,
  },
  ready: { label: "Ready", icon: CheckCircle2, variant: "default" as const },
  failed: {
    label: "Failed",
    icon: AlertCircle,
    variant: "destructive" as const,
  },
}

function KnowledgeBaseContent() {
  const { knowledgeBaseId } = Route.useParams()

  const { data: documents } = useSuspenseQuery({
    queryKey: ["knowledge-documents", knowledgeBaseId],
    queryFn: async () =>
      (
        await KnowledgeBasesService.basesReadKnowledgeDocuments({
          path: { knowledge_base_id: knowledgeBaseId },
          query: { skip: 0, limit: 100 },
        })
      ).data,
    refetchInterval: (query) =>
      query.state.data?.data.some((document) =>
        ["uploaded", "processing"].includes(document.status),
      )
        ? 2000
        : false,
  })

  const { data: knowledgeBase } = useSuspenseQuery({
    queryKey: ["knowledge-base", knowledgeBaseId],
    queryFn: async () =>
      (
        await KnowledgeBasesService.basesReadKnowledgeBase({
          path: { knowledge_base_id: knowledgeBaseId },
        })
      ).data,
  })

  const hasReadyDocuments = documents.data.some(
    (document) => document.status === "ready",
  )

  return (
    <div className="space-y-6">
      <Link
        to="/knowledge-bases"
        className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        Knowledge bases
      </Link>

      <header className="flex items-start justify-between gap-4 border-b pb-6">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-bold">{knowledgeBase.name}</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            {knowledgeBase.description || "No description provided"}
          </p>
        </div>
        <KnowledgeBaseActions knowledgeBase={knowledgeBase} />
      </header>

      <Tabs defaultValue="documents" className="gap-5">
        <TabsList>
          <TabsTrigger value="documents">
            <FileText />
            Documents
          </TabsTrigger>
          <TabsTrigger value="chat">
            <MessageSquare />
            Chat
          </TabsTrigger>
        </TabsList>

        <TabsContent value="documents" className="space-y-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-lg font-semibold">Documents</h2>
              <p className="text-sm text-muted-foreground">
                Files indexed for retrieval and grounded answers.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-sm text-muted-foreground">
                {documents.count} {documents.count === 1 ? "file" : "files"}
              </span>
              <UploadDocument knowledgeBaseId={knowledgeBaseId} />
            </div>
          </div>

          {documents.data.length === 0 ? (
            <div className="flex min-h-48 flex-col items-center justify-center border-y text-center">
              <FileText className="mb-3 size-5 text-muted-foreground" />
              <p className="font-medium">No documents yet</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Upload a PDF, TXT, or Markdown file to begin indexing.
              </p>
            </div>
          ) : (
            <div className="divide-y border-y">
              {documents.data.map((document) => {
                const details =
                  statusDetails[
                    document.status as keyof typeof statusDetails
                  ] ?? statusDetails.uploaded
                const StatusIcon = details.icon

                return (
                  <div
                    key={document.id}
                    className="grid min-h-20 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 py-3 md:grid-cols-[auto_minmax(0,1fr)_auto_auto]"
                  >
                    <div className="row-span-2 flex size-9 items-center justify-center rounded-md border bg-muted/40 md:row-span-1">
                      <FileText className="size-4" />
                    </div>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {document.original_filename}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {formatBytes(document.size_bytes)} ·{" "}
                        {document.chunk_count}{" "}
                        {document.chunk_count === 1 ? "chunk" : "chunks"} ·{" "}
                        {new Intl.DateTimeFormat(undefined, {
                          dateStyle: "medium",
                        }).format(new Date(document.created_at))}
                      </p>
                      {document.error_message && (
                        <p className="mt-1 line-clamp-2 text-xs text-destructive">
                          {document.error_message}
                        </p>
                      )}
                    </div>
                    <Badge
                      variant={details.variant}
                      className="col-start-2 row-start-2 md:col-start-3 md:row-start-1"
                    >
                      <StatusIcon
                        className={
                          document.status === "processing" ? "animate-spin" : ""
                        }
                      />
                      {details.label}
                    </Badge>
                    <div className="col-start-3 row-span-2 row-start-1 md:col-start-4 md:row-span-1">
                      <DocumentActions document={document} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </TabsContent>

        <TabsContent value="chat">
          <div className="mb-3">
            <h2 className="text-lg font-semibold">Chat</h2>
            <p className="text-sm text-muted-foreground">
              Ask questions and inspect the document passages used in each
              answer.
            </p>
          </div>
          <ConversationWorkspace
            knowledgeBaseId={knowledgeBaseId}
            hasReadyDocuments={hasReadyDocuments}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function KnowledgeBasePage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-4">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-4 w-96 max-w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <KnowledgeBaseContent />
    </Suspense>
  )
}
