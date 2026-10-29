import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { MoreHorizontal, Pencil, Trash2 } from "lucide-react"
import { type FormEvent, useState } from "react"

import { type KnowledgeBasePublic, KnowledgeBasesService } from "@/client"
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
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

interface KnowledgeBaseActionsProps {
  knowledgeBase: KnowledgeBasePublic
}

export function KnowledgeBaseActions({
  knowledgeBase,
}: KnowledgeBaseActionsProps) {
  const [dialog, setDialog] = useState<"edit" | "delete" | null>(null)
  const [name, setName] = useState(knowledgeBase.name)
  const [description, setDescription] = useState(
    knowledgeBase.description ?? "",
  )
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const updateMutation = useMutation({
    mutationFn: () =>
      KnowledgeBasesService.basesUpdateKnowledgeBase({
        path: { knowledge_base_id: knowledgeBase.id },
        body: {
          name: name.trim(),
          description: description.trim() || null,
        },
      }),
    onSuccess: async () => {
      showSuccessToast("Knowledge base updated")
      setDialog(null)
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["knowledge-base", knowledgeBase.id],
        }),
        queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] }),
      ])
    },
    onError: handleError.bind(showErrorToast),
  })

  const deleteMutation = useMutation({
    mutationFn: () =>
      KnowledgeBasesService.basesDeleteKnowledgeBase({
        path: { knowledge_base_id: knowledgeBase.id },
      }),
    onSuccess: async () => {
      showSuccessToast("Knowledge base deleted")
      await queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] })
      navigate({ to: "/knowledge-bases" })
    },
    onError: handleError.bind(showErrorToast),
  })

  const submitEdit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (name.trim()) updateMutation.mutate()
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="icon"
            aria-label="Knowledge base menu"
          >
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => setDialog("edit")}>
            <Pencil />
            Edit details
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => setDialog("delete")}
          >
            <Trash2 />
            Delete knowledge base
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog
        open={dialog === "edit"}
        onOpenChange={(open) => !open && setDialog(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Edit knowledge base</DialogTitle>
            <DialogDescription>
              Change the name or description shown in your workspace.
            </DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={submitEdit}>
            <div className="space-y-2">
              <Label htmlFor="knowledge-base-name">Name</Label>
              <Input
                id="knowledge-base-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={128}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="knowledge-base-description">Description</Label>
              <Input
                id="knowledge-base-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={1000}
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
                loading={updateMutation.isPending}
                disabled={!name.trim()}
              >
                Save changes
              </LoadingButton>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={dialog === "delete"}
        onOpenChange={(open) => !open && setDialog(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete knowledge base?</DialogTitle>
            <DialogDescription>
              This permanently deletes {knowledgeBase.name}, its documents,
              vectors, and conversations.
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
              onClick={() => deleteMutation.mutate()}
            >
              Delete knowledge base
            </LoadingButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
