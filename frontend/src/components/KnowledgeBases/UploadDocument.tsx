import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Upload } from "lucide-react"
import type { FormEvent } from "react"
import { useRef, useState } from "react"

import { KnowledgeBasesService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

interface UploadDocumentProps {
  knowledgeBaseId: string
}

export function UploadDocument({ knowledgeBaseId }: UploadDocumentProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const resetForm = () => {
    setFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
  }

  const mutation = useMutation({
    mutationFn: (documentFile: File) =>
      KnowledgeBasesService.basesUploadKnowledgeDocument({
        path: {
          knowledge_base_id: knowledgeBaseId,
        },
        body: {
          file: documentFile,
        },
      }),
    onSuccess: () => {
      showSuccessToast("Document uploaded")
      queryClient.invalidateQueries({
        queryKey: ["knowledge-documents", knowledgeBaseId],
      })
      resetForm()
      setIsOpen(false)
    },
    onError: handleError.bind(showErrorToast),
  })

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    if (file) {
      mutation.mutate(file)
    }
  }

  const handleOpenChange = (open: boolean) => {
    if (mutation.isPending) return
    setIsOpen(open)
    if (!open) resetForm()
  }

  return (
    <Dialog open={isOpen} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Upload />
          Upload document
        </Button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Upload document</DialogTitle>
          <DialogDescription>
            Select a PDF, TXT, or Markdown file up to 20 MB.
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-5" onSubmit={handleSubmit}>
          <Input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
            }}
            disabled={mutation.isPending}
            required
          />

          <DialogFooter>
            <DialogClose asChild>
              <Button
                type="button"
                variant="outline"
                disabled={mutation.isPending}
              >
                Cancel
              </Button>
            </DialogClose>
            <LoadingButton
              type="submit"
              loading={mutation.isPending}
              disabled={!file}
            >
              Upload
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
