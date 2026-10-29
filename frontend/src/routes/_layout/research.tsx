import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import {
  AlertCircle,
  ArrowUpRight,
  BookOpenText,
  CheckCircle2,
  Clock3,
  Copy,
  FileSearch,
  Globe2,
  Loader2,
  Plus,
  Search,
  Square,
  Trash2,
} from "lucide-react"
import { type FormEvent, useState } from "react"

import { KnowledgeBasesService } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
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
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useCustomToast from "@/hooks/useCustomToast"
import { useResearchStream } from "@/hooks/useResearchStream"
import {
  type Evidence,
  ResearchApiError,
  type ResearchRun,
  type ResearchStatus,
  researchApi,
} from "@/lib/researchApi"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/research")({
  component: ResearchPage,
  validateSearch: (search: Record<string, unknown>): { run?: string } => ({
    run: typeof search.run === "string" ? search.run : undefined,
  }),
  head: () => ({
    meta: [{ title: "Research | Grounded Research" }],
  }),
})

const statusLabels: Record<ResearchStatus, string> = {
  queued: "Queued",
  running: "Researching",
  succeeded: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
}

const isActive = (status: ResearchStatus) =>
  status === "queued" || status === "running"

function StatusBadge({ status }: { status: ResearchStatus }) {
  return (
    <Badge
      variant={status === "failed" ? "destructive" : "outline"}
      className={cn(
        status === "succeeded" &&
          "border-emerald-600/20 bg-emerald-600/5 text-emerald-700 dark:text-emerald-400",
      )}
    >
      {isActive(status) ? (
        <Loader2 className="animate-spin" />
      ) : status === "succeeded" ? (
        <CheckCircle2 />
      ) : (
        <Clock3 />
      )}
      {statusLabels[status]}
    </Badge>
  )
}

function errorMessage(error: unknown) {
  if (error instanceof ResearchApiError) return error.message
  return "The request could not be completed. Please try again."
}

function safeWebUrl(value?: string | null) {
  if (!value) return null
  try {
    const url = new URL(value)
    return ["http:", "https:"].includes(url.protocol) ? url.href : null
  } catch {
    return null
  }
}

function SourceList({ sources }: { sources: Evidence[] }) {
  if (!sources.length) {
    return (
      <p className="py-8 text-sm text-muted-foreground">
        Sources will appear here when the research is complete.
      </p>
    )
  }
  return (
    <div className="divide-y">
      {sources.map((source, index) => {
        const url = safeWebUrl(source.url)
        return (
          <details key={`${source.source_id}-${index}`} className="group py-4">
            <summary className="flex cursor-pointer list-none items-start gap-3 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <span className="mt-0.5 rounded border px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                {source.source_id}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{source.title}</p>
                <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                  {source.kind === "web" ? (
                    <Globe2 className="size-3" />
                  ) : (
                    <BookOpenText className="size-3" />
                  )}
                  {source.kind === "web" ? "Web source" : "Knowledge base"}
                  {typeof source.chunk_index === "number"
                    ? ` · passage ${source.chunk_index + 1}`
                    : ""}
                  <span className="ml-auto group-open:hidden">
                    View passage
                  </span>
                </p>
              </div>
            </summary>
            <div className="mt-3 border-l-2 pl-4 text-sm leading-6">
              <p className="whitespace-pre-wrap break-words text-muted-foreground">
                {source.content}
              </p>
              {url ? (
                <a
                  className="mt-3 inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open original source <ArrowUpRight className="size-3.5" />
                </a>
              ) : null}
            </div>
          </details>
        )
      })}
    </div>
  )
}

function formatDetail(detail: unknown) {
  if (typeof detail === "string") return detail
  return detail == null ? "" : JSON.stringify(detail, null, 2)
}

function ResearchPage() {
  const { run: selectedId } = Route.useSearch()
  const navigate = Route.useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [question, setQuestion] = useState("")
  const [selectedKnowledgeBases, setSelectedKnowledgeBases] = useState<
    string[]
  >([])
  const [useWeb, setUseWeb] = useState(true)
  const [deleteTarget, setDeleteTarget] = useState<ResearchRun | null>(null)
  const [tab, setTab] = useState("report")

  const selectRun = (id?: string) => {
    setTab("report")
    void navigate({ search: id ? { run: id } : {}, replace: true })
  }

  const knowledgeBasesQuery = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: async () =>
      (
        await KnowledgeBasesService.basesReadKnowledgeBases({
          query: { skip: 0, limit: 100 },
        })
      ).data,
  })
  const historyQuery = useQuery({
    queryKey: ["research-runs"],
    queryFn: ({ signal }) => researchApi.list(signal),
    refetchInterval: (query) =>
      query.state.data?.data.some((run) => isActive(run.status)) ? 5000 : false,
  })
  const runQuery = useQuery({
    queryKey: ["research-run", selectedId],
    queryFn: ({ signal }) => researchApi.get(selectedId as string, signal),
    enabled: Boolean(selectedId),
    refetchInterval: (query) =>
      query.state.data && isActive(query.state.data.status) ? 5000 : false,
  })
  const selectedRun = runQuery.data
  const active = Boolean(selectedRun && isActive(selectedRun.status))
  const stream = useResearchStream(selectedId, active)
  const report = selectedRun?.final_report || stream?.report || ""

  const createMutation = useMutation({
    mutationFn: researchApi.create,
    onSuccess: (run) => {
      queryClient.setQueryData(["research-run", run.id], run)
      void queryClient.invalidateQueries({ queryKey: ["research-runs"] })
      selectRun(run.id)
    },
    onError: (error) => showErrorToast(errorMessage(error)),
  })
  const cancelMutation = useMutation({
    mutationFn: researchApi.cancel,
    onSuccess: (run) => {
      queryClient.setQueryData(["research-run", run.id], run)
      void queryClient.invalidateQueries({ queryKey: ["research-runs"] })
    },
    onError: (error) => showErrorToast(errorMessage(error)),
  })
  const deleteMutation = useMutation({
    mutationFn: researchApi.remove,
    onSuccess: (_, id) => {
      if (selectedId === id) selectRun()
      queryClient.removeQueries({ queryKey: ["research-run", id] })
      void queryClient.invalidateQueries({ queryKey: ["research-runs"] })
      setDeleteTarget(null)
      showSuccessToast("Research deleted")
    },
    onError: (error) => showErrorToast(errorMessage(error)),
  })

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!question.trim() || (!useWeb && !selectedKnowledgeBases.length)) return
    createMutation.mutate({
      query: question.trim(),
      knowledge_base_ids: selectedKnowledgeBases,
      use_web: useWeb,
    })
  }

  return (
    <div className="space-y-7">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b pb-6">
        <div>
          <p className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-widest text-primary">
            <FileSearch className="size-4" /> Research workspace
          </p>
          <h1 className="text-2xl font-bold">Follow the evidence.</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Research a question across your documents and the web, then inspect
            the report and the passages behind it.
          </p>
        </div>
        {selectedId ? (
          <Button variant="outline" onClick={() => selectRun()}>
            <Plus /> New research
          </Button>
        ) : null}
      </header>

      <div className="grid gap-7 lg:grid-cols-[230px_minmax(0,1fr)]">
        <aside className="min-w-0">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Research history</h2>
            <span className="text-xs text-muted-foreground">
              {historyQuery.data?.count ?? 0}
            </span>
          </div>
          {historyQuery.isPending ? (
            <div className="space-y-3">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : historyQuery.isError ? (
            <div className="space-y-2 text-sm text-muted-foreground">
              <p>History could not be loaded.</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void historyQuery.refetch()}
              >
                Try again
              </Button>
            </div>
          ) : !historyQuery.data?.data.length ? (
            <div className="border-y py-6 text-sm leading-6 text-muted-foreground">
              Your research will be saved here. Start with a specific question.
            </div>
          ) : (
            <div className="max-h-64 space-y-1 overflow-y-auto pr-1 lg:max-h-[75vh]">
              {historyQuery.data.data.map((run) => (
                <div key={run.id} className="group relative">
                  <button
                    type="button"
                    aria-pressed={selectedId === run.id}
                    onClick={() => selectRun(run.id)}
                    className={cn(
                      "w-full rounded-md border border-transparent px-3 py-3 text-left transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      selectedId === run.id && "border-border bg-muted/60",
                    )}
                  >
                    <p className="line-clamp-2 pr-5 text-sm font-medium leading-5">
                      {run.query}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
                      <span
                        className={cn(
                          run.status === "running" && "text-primary",
                        )}
                      >
                        {statusLabels[run.status]}
                      </span>
                      <span aria-hidden="true">·</span>
                      <time dateTime={run.created_at}>
                        {new Intl.DateTimeFormat(undefined, {
                          month: "short",
                          day: "numeric",
                        }).format(new Date(run.created_at))}
                      </time>
                    </div>
                  </button>
                  {!isActive(run.status) ? (
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      className="absolute top-2 right-1 text-muted-foreground opacity-60 hover:text-destructive focus:opacity-100 lg:opacity-0 lg:group-hover:opacity-100"
                      aria-label={`Delete research: ${run.query}`}
                      onClick={() => setDeleteTarget(run)}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </aside>

        <div className="min-w-0 space-y-6">
          <form onSubmit={submit} className="rounded-lg border bg-card p-5">
            <Label
              htmlFor="research-question"
              className="text-sm font-semibold"
            >
              What would you like to investigate?
            </Label>
            <textarea
              id="research-question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="For example: Compare our product's data retention policy with current industry practices."
              rows={3}
              maxLength={2000}
              required
              className="mt-3 min-h-24 w-full resize-y rounded-md border bg-background px-3 py-2 text-sm leading-6 outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
            />

            <div className="mt-4 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs font-medium text-muted-foreground">
                  Sources to consult
                </p>
                <label
                  htmlFor="research-use-web"
                  className="flex cursor-pointer items-center gap-2 text-sm"
                >
                  <Checkbox
                    id="research-use-web"
                    checked={useWeb}
                    onCheckedChange={(checked) => setUseWeb(checked === true)}
                  />
                  <Globe2 className="size-3.5 text-muted-foreground" /> Search
                  the web
                </label>
              </div>
              {knowledgeBasesQuery.isPending ? (
                <Skeleton className="h-9 w-full" />
              ) : knowledgeBasesQuery.isError ? (
                <p className="text-sm text-destructive">
                  Knowledge bases could not be loaded. Refresh to try again.
                </p>
              ) : !knowledgeBasesQuery.data?.data.length ? (
                <p className="text-sm text-muted-foreground">
                  <Link
                    to="/knowledge-bases"
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    Add a knowledge base
                  </Link>{" "}
                  to include your own documents.
                </p>
              ) : (
                <div className="flex max-h-36 flex-wrap gap-2 overflow-y-auto">
                  {knowledgeBasesQuery.data.data.map((base) => (
                    <label
                      key={base.id}
                      htmlFor={`research-base-${base.id}`}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-2 text-xs transition-colors hover:bg-muted/50",
                        selectedKnowledgeBases.includes(base.id) &&
                          "border-primary/35 bg-primary/5",
                      )}
                    >
                      <Checkbox
                        id={`research-base-${base.id}`}
                        checked={selectedKnowledgeBases.includes(base.id)}
                        onCheckedChange={(checked) =>
                          setSelectedKnowledgeBases((previous) =>
                            checked === true
                              ? [...previous, base.id]
                              : previous.filter((id) => id !== base.id),
                          )
                        }
                      />
                      <BookOpenText className="size-3.5 text-muted-foreground" />
                      {base.name}
                    </label>
                  ))}
                </div>
              )}
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
              <p className="text-xs text-muted-foreground">
                {!useWeb && !selectedKnowledgeBases.length
                  ? "Select a knowledge base or enable web search."
                  : useWeb
                    ? "Uses your selected documents and public web results."
                    : "Uses only the selected knowledge bases."}
              </p>
              <Button
                type="submit"
                disabled={
                  createMutation.isPending ||
                  !question.trim() ||
                  (!useWeb && !selectedKnowledgeBases.length)
                }
              >
                {createMutation.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Search />
                )}
                {createMutation.isPending ? "Starting…" : "Start research"}
              </Button>
            </div>
          </form>

          {!selectedId ? (
            <div className="flex min-h-64 flex-col items-center justify-center border-y px-4 py-10 text-center">
              <FileSearch className="mb-4 size-7 text-muted-foreground/60" />
              <h2 className="text-base font-semibold">
                A question, a trail of sources, a report.
              </h2>
              <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                Start a research task above or open one from your history. You
                can leave and return while it runs.
              </p>
            </div>
          ) : runQuery.isPending ? (
            <div className="space-y-3">
              <Skeleton className="h-9 w-2/3" />
              <Skeleton className="h-52 w-full" />
            </div>
          ) : runQuery.isError ? (
            <Alert variant="destructive">
              <AlertCircle />
              <AlertTitle>Research could not be loaded</AlertTitle>
              <AlertDescription>
                {errorMessage(runQuery.error)}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void runQuery.refetch()}
                >
                  Try again
                </Button>
              </AlertDescription>
            </Alert>
          ) : selectedRun ? (
            <section className="rounded-lg border bg-card">
              <div className="flex flex-wrap items-start justify-between gap-4 border-b p-5">
                <div className="min-w-0 flex-1 space-y-3">
                  <StatusBadge status={selectedRun.status} />
                  <h2 className="break-words text-lg font-semibold leading-7">
                    {selectedRun.query}
                  </h2>
                  <p className="text-xs text-muted-foreground">
                    {selectedRun.knowledge_base_ids.length} knowledge{" "}
                    {selectedRun.knowledge_base_ids.length === 1
                      ? "base"
                      : "bases"}
                    {selectedRun.use_web
                      ? " · Web search enabled"
                      : " · Documents only"}
                  </p>
                </div>
                {active ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={cancelMutation.isPending}
                    onClick={() => cancelMutation.mutate(selectedRun.id)}
                  >
                    {cancelMutation.isPending ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <Square />
                    )}
                    {cancelMutation.isPending ? "Stopping…" : "Stop research"}
                  </Button>
                ) : report ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      void navigator.clipboard.writeText(report).then(
                        () => showSuccessToast("Report copied"),
                        () => showErrorToast("The report could not be copied"),
                      )
                    }}
                  >
                    <Copy /> Copy report
                  </Button>
                ) : null}
              </div>
              {selectedRun.error_message || stream?.error ? (
                <div
                  role="alert"
                  className="border-b bg-destructive/5 px-5 py-3 text-sm text-destructive"
                >
                  {selectedRun.error_message || stream?.error}
                </div>
              ) : null}
              {selectedRun.status === "cancelled" ? (
                <p className="border-b bg-muted/30 px-5 py-3 text-sm text-muted-foreground">
                  Research stopped. Any available results are shown below.
                </p>
              ) : null}
              {active && stream?.connection === "reconnecting" ? (
                <p
                  role="status"
                  className="border-b px-5 py-3 text-sm text-muted-foreground"
                >
                  Reconnecting to live progress. Your research continues in the
                  background.
                </p>
              ) : null}
              <Tabs value={tab} onValueChange={setTab} className="gap-0">
                <div className="overflow-x-auto border-b px-5 py-3">
                  <TabsList>
                    <TabsTrigger value="report">Report</TabsTrigger>
                    <TabsTrigger value="sources">
                      Sources ({selectedRun.sources.length})
                    </TabsTrigger>
                    <TabsTrigger value="progress">Progress</TabsTrigger>
                  </TabsList>
                </div>
                <TabsContent value="report" className="p-5">
                  {report ? (
                    <div className="whitespace-pre-wrap break-words text-sm leading-7">
                      {report}
                    </div>
                  ) : (
                    <div className="flex min-h-44 flex-col items-center justify-center gap-3 text-center text-sm text-muted-foreground">
                      {active ? (
                        <Loader2 className="size-5 animate-spin text-primary" />
                      ) : (
                        <FileSearch className="size-5" />
                      )}
                      <p>
                        {active
                          ? "Collecting evidence and preparing your report…"
                          : "No report was produced for this task."}
                      </p>
                      {active ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setTab("progress")}
                        >
                          View progress
                        </Button>
                      ) : null}
                    </div>
                  )}
                </TabsContent>
                <TabsContent value="sources" className="px-5">
                  <SourceList sources={selectedRun.sources} />
                </TabsContent>
                <TabsContent value="progress" className="space-y-5 p-5">
                  {selectedRun.sub_tasks.length ? (
                    <div>
                      <h3 className="mb-2 text-sm font-semibold">
                        Research plan
                      </h3>
                      <ol className="list-decimal space-y-1.5 pl-5 text-sm leading-6 text-muted-foreground">
                        {selectedRun.sub_tasks.map((task, index) => (
                          <li key={`${index}-${task}`}>{task}</li>
                        ))}
                      </ol>
                    </div>
                  ) : null}
                  {stream?.logs.length ? (
                    <div className="divide-y border-y">
                      {stream.logs.map((log) => (
                        <div key={log.id} className="py-3">
                          <div className="flex flex-wrap items-center gap-2 text-xs">
                            <span className="font-medium text-primary">
                              {log.agent || "Research"}
                            </span>
                            <span className="text-muted-foreground">
                              {log.action || "Progress update"}
                            </span>
                          </div>
                          {log.detail != null ? (
                            <p className="mt-1.5 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">
                              {formatDetail(log.detail)}
                            </p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="py-3 text-sm text-muted-foreground">
                      {active
                        ? "Waiting for the first progress update…"
                        : "This task has finished. Its report and sources are saved."}
                    </p>
                  )}
                  {stream?.analysis ? (
                    <details className="rounded-md border p-3">
                      <summary className="cursor-pointer text-sm font-medium">
                        Evidence analysis
                      </summary>
                      <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">
                        {stream.analysis}
                      </p>
                    </details>
                  ) : null}
                </TabsContent>
              </Tabs>
            </section>
          ) : null}
        </div>
      </div>

      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) setDeleteTarget(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this research?</DialogTitle>
            <DialogDescription>
              The saved report, sources, and progress for this task will be
              permanently removed.
            </DialogDescription>
          </DialogHeader>
          <p className="line-clamp-3 text-sm font-medium">
            {deleteTarget?.query}
          </p>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={deleteMutation.isPending}>
                Keep research
              </Button>
            </DialogClose>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (deleteTarget) deleteMutation.mutate(deleteTarget.id)
              }}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <Trash2 />
              )}
              Delete research
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
