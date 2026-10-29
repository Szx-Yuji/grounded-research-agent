import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { ChevronRight, Library } from "lucide-react"
import { Suspense } from "react"

import { KnowledgeBasesService } from "@/client"
import { AddKnowledgeBase } from "@/components/KnowledgeBases/AddKnowledgeBase"
import { Skeleton } from "@/components/ui/skeleton"

const knowledgeBasesQueryOptions = {
  queryKey: ["knowledge-bases"],
  queryFn: async () =>
    (
      await KnowledgeBasesService.basesReadKnowledgeBases({
        query: { skip: 0, limit: 100 },
      })
    ).data,
}

export const Route = createFileRoute("/_layout/knowledge-bases/")({
  component: KnowledgeBasesPage,
  head: () => ({
    meta: [{ title: "Knowledge bases | Grounded Research" }],
  }),
})

function KnowledgeBasesContent() {
  const { data: knowledgeBases } = useSuspenseQuery(knowledgeBasesQueryOptions)

  if (knowledgeBases.data.length === 0) {
    return (
      <div className="flex min-h-72 flex-col items-center justify-center border-y text-center">
        <div className="mb-4 flex size-11 items-center justify-center rounded-md bg-muted">
          <Library className="size-5 text-muted-foreground" />
        </div>
        <h2 className="text-base font-semibold">No knowledge bases yet</h2>
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">
          Create one to organize documents and start grounded conversations.
        </p>
      </div>
    )
  }

  return (
    <div className="divide-y border-y">
      {knowledgeBases.data.map((knowledgeBase) => (
        <Link
          key={knowledgeBase.id}
          to="/knowledge-bases/$knowledgeBaseId"
          params={{ knowledgeBaseId: knowledgeBase.id }}
          className="grid min-h-20 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-4 py-4 transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[auto_minmax(0,1fr)_auto_auto]"
        >
          <div className="flex size-9 items-center justify-center rounded-md border bg-muted/40">
            <Library className="size-4 text-primary" />
          </div>
          <div className="min-w-0">
            <h2 className="truncate font-medium">{knowledgeBase.name}</h2>
            <p className="mt-1 truncate text-sm text-muted-foreground">
              {knowledgeBase.description || "No description provided"}
            </p>
          </div>
          <time
            className="col-start-2 row-start-2 text-xs text-muted-foreground md:col-start-3 md:row-start-1"
            dateTime={knowledgeBase.created_at}
          >
            {new Intl.DateTimeFormat(undefined, {
              dateStyle: "medium",
            }).format(new Date(knowledgeBase.created_at))}
          </time>
          <ChevronRight className="col-start-3 row-span-2 row-start-1 size-4 text-muted-foreground md:col-start-4 md:row-span-1" />
        </Link>
      ))}
    </div>
  )
}

function KnowledgeBasesPending() {
  return (
    <div className="divide-y border-y">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="flex min-h-20 items-center gap-4 py-4">
          <Skeleton className="size-9 rounded-md" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-3 w-72 max-w-full" />
          </div>
        </div>
      ))}
    </div>
  )
}

function KnowledgeBasesPage() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <h1 className="text-2xl font-bold">Knowledge bases</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Organize documents and conversations by subject.
          </p>
        </div>
        <AddKnowledgeBase />
      </header>
      <Suspense fallback={<KnowledgeBasesPending />}>
        <KnowledgeBasesContent />
      </Suspense>
    </div>
  )
}
