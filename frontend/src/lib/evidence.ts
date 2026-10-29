import { z } from "zod"

export const evidenceSchema = z.object({
  source_id: z.string(),
  kind: z.enum(["knowledge", "web"]),
  content: z.string(),
  title: z.string(),
  url: z.string().nullable().optional(),
  document_id: z.string().nullable().optional(),
  chunk_index: z.number().nullable().optional(),
  retrieval: z
    .object({
      method: z.enum(["dense", "lexical", "hybrid", "dense_legacy"]),
      rank: z.number(),
      fusion_score: z.number().nullable(),
      lexical_score: z.number().nullable(),
      dense_rank: z.number().nullable(),
      lexical_rank: z.number().nullable(),
      index_version: z.string().nullable(),
      warnings: z.array(z.string()),
    })
    .nullish(),
})
