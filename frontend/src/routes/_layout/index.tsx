import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { ArrowRight, FileSearch, Library, MessageSquare } from "lucide-react"

import { KnowledgeBasesService } from "@/client"
import { Button } from "@/components/ui/button"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({ meta: [{ title: "Grounded Research" }] }),
})

function Dashboard() {
  const { user: currentUser } = useAuth()
  const knowledgeBasesQuery = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: async () =>
      (
        await KnowledgeBasesService.basesReadKnowledgeBases({
          query: { skip: 0, limit: 100 },
        })
      ).data,
  })
  const count = knowledgeBasesQuery.data?.count ?? 0

  return (
    <div className="space-y-10">
      <header className="border-b pb-8">
        <p className="text-sm font-medium text-primary">
          Research & knowledge workspace
        </p>
        <h1 className="mt-2 text-3xl font-bold">
          Welcome back, {currentUser?.full_name || currentUser?.email}
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
          Keep a conversation going, ask follow-up questions, and let your
          assistant consult your documents when it needs evidence.
        </p>
        <Button asChild className="mt-5">
          <Link to="/chat">
            Start a conversation
            <ArrowRight />
          </Link>
        </Button>
      </header>

      <section>
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">Workspace</h2>
            <p className="text-sm text-muted-foreground">
              {count} {count === 1 ? "knowledge base" : "knowledge bases"}
            </p>
          </div>
        </div>
        <div className="grid border-y md:grid-cols-3 md:divide-x">
          <div className="flex gap-3 border-b py-5 md:border-b-0 md:px-5 md:first:pl-0">
            <Library className="mt-0.5 size-5 shrink-0 text-primary" />
            <div>
              <h3 className="text-sm font-medium">Organize</h3>
              <p className="mt-1 text-sm leading-5 text-muted-foreground">
                Separate documents by product, team, or subject.
              </p>
            </div>
          </div>
          <div className="flex gap-3 border-b py-5 md:border-b-0 md:px-5">
            <FileSearch className="mt-0.5 size-5 shrink-0 text-primary" />
            <div>
              <h3 className="text-sm font-medium">Index</h3>
              <p className="mt-1 text-sm leading-5 text-muted-foreground">
                Turn uploaded files into searchable document chunks.
              </p>
            </div>
          </div>
          <div className="flex gap-3 py-5 md:px-5 md:last:pr-0">
            <MessageSquare className="mt-0.5 size-5 shrink-0 text-primary" />
            <div>
              <h3 className="text-sm font-medium">Ask</h3>
              <p className="mt-1 text-sm leading-5 text-muted-foreground">
                Continue conversations and inspect every cited passage.
              </p>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
