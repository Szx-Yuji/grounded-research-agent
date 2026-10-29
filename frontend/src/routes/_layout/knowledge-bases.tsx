import { createFileRoute, Outlet } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/knowledge-bases")({
  component: KnowledgeBasesLayout,
})

function KnowledgeBasesLayout() {
  return <Outlet />
}
