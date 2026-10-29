import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Download, MoreHorizontal, RefreshCw, Trash2 } from "lucide-react"
import { useState } from "react"

import { KnowledgeBasesService, type KnowledgeDocumentPublic } from "@/client"
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
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

interface DocumentActionsProps {
  document: KnowledgeDocumentPublic
}

export function DocumentActions({ document }: DocumentActionsProps) {
  const [deleteOpen, setDeleteOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const queryKey = ["knowledge-documents", document.knowledge_base_id]

  const retryMutation = useMutation({
    mutationFn: () =>
      KnowledgeBasesService.basesRetryKnowledgeDocument({
        path: {
          knowledge_base_id: document.knowledge_base_id,
          document_id: document.id,
        },
      }),
    onSuccess: () => showSuccessToast("Document processing restarted"),
    onError: handleError.bind(showErrorToast),
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })

  const deleteMutation = useMutation({
    mutationFn: () =>
      KnowledgeBasesService.basesDeleteKnowledgeDocument({
        path: {
          knowledge_base_id: document.knowledge_base_id,
          document_id: document.id,
        },
      }),
    onSuccess: () => {
      showSuccessToast("Document deleted")
      setDeleteOpen(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })

  const downloadMutation = useMutation({
    mutationFn: async () => {
      const response =
        await KnowledgeBasesService.basesDownloadKnowledgeDocument({
          path: {
            knowledge_base_id: document.knowledge_base_id,
            document_id: document.id,
          },
          responseType: "blob",
        })
      const url = URL.createObjectURL(response.data as Blob)
      const link = window.document.createElement("a")
      link.href = url
      link.download = document.original_filename
      link.click()
      URL.revokeObjectURL(url)
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label="Document menu">
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onSelect={() => downloadMutation.mutate()}
            disabled={downloadMutation.isPending}
          >
            <Download />
            Download
          </DropdownMenuItem>
          {document.status === "failed" && (
            <DropdownMenuItem
              onSelect={() => retryMutation.mutate()}
              disabled={retryMutation.isPending}
            >
              <RefreshCw />
              Retry processing
            </DropdownMenuItem>
          )}
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => setDeleteOpen(true)}
          >
            <Trash2 />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete document?</DialogTitle>
            <DialogDescription>
              {document.original_filename} and its indexed chunks will be
              permanently removed.
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
              Delete document
            </LoadingButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
