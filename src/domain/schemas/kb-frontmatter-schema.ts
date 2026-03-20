import { z } from "zod";

export const kbFrontmatterSchema = z.object({
  id: z
    .string()
    .regex(/^\d{14}$/, "id must be YYYYMMDDHHMMSS")
    .optional(),
  title: z.string().min(1, "title is required"),
  slug: z
    .string()
    .regex(/^[a-f0-9]{4}-[a-z0-9]+(?:-[a-z0-9]+)*$/, "slug must be 4-hex prefix and kebab-case"),
  tags: z.array(z.string()).optional(),
  created: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  updated: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .optional(),
  summary: z.string().max(300).optional(),
  image: z.string().url().optional(),
  type: z.string().min(1).optional(),
  isDraft: z.string().min(1).optional(),
});

export type KbFrontmatterSchema = z.infer<typeof kbFrontmatterSchema>;
