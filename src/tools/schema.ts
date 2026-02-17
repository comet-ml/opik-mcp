import { z } from 'zod';

export const pageSchema = z.number().int().min(1).default(1).describe('1-based page number.');

export const sizeSchema = (defaultSize: number, max: number = 100) =>
  z.number().int().min(1).max(max).default(defaultSize).describe(`Page size (1-${max}).`);

export const workspaceNameSchema = z
  .string()
  .min(1)
  .optional()
  .describe(
    'Workspace override for local/stdio mode. Ignored when remote token-to-workspace mapping is enforced.'
  );

export const isoDateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/)
  .optional()
  .describe('Date in YYYY-MM-DD format.');
