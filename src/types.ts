/**
 * Type definitions for the Opik API
 */

// Project types
export type ProjectResponse = {
  page: number;
  size: number;
  total: number;
  content: Array<{
    id: string;
    name: string;
    description: string;
    created_at: string;
    created_by: string;
    last_updated_at: string;
    last_updated_by: string;
    workspace: string;
  }>;
};

export type SingleProjectResponse = ProjectResponse['content'][0];

// Prompt types
export type PromptResponse = {
  page: number;
  size: number;
  total: number;
  content: Array<{
    name: string;
    id: string;
    description: string;
    created_at: string;
    created_by: string;
    last_updated_at: string;
    last_updated_by: string;
    version_count: number;
  }>;
};

export type SinglePromptResponse = PromptResponse['content'][0];

// Trace types
export type TraceResponse = {
  page: number;
  size: number;
  total: number;
  content: Array<{
    id: string;
    project_id: string;
    name: string;
    start_time?: string;
    end_time?: string;
    input: Record<string, any>;
    output: Record<string, any>;
    metadata?: Record<string, any>;
    usage?: {
      completion_tokens?: number;
      prompt_tokens?: number;
      total_tokens?: number;
    };
    created_at: string;
    last_updated_at?: string;
    created_by: string;
    last_updated_by?: string;
    total_estimated_cost?: number;
    duration?: number;
    tags?: string[];
  }>;
  sortable_by?: string[];
};

export type SingleTraceResponse = {
  id: string;
  project_id: string;
  name: string;
  start_time?: string;
  end_time?: string;
  input: Record<string, any>;
  output: Record<string, any>;
  metadata?: Record<string, any>;
  usage?: {
    completion_tokens?: number;
    prompt_tokens?: number;
    total_tokens?: number;
  };
  created_at: string;
  last_updated_at?: string;
  created_by: string;
  last_updated_by?: string;
  total_estimated_cost?: number;
  duration?: number;
  tags?: string[];
};

// Trace stats types
export type TraceStatsResponse = {
  stats: Array<{
    date?: string;
    trace_count?: number;
    span_count?: number;
    total_tokens?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    cost?: number;
  }>;
};

// Metrics types
export type MetricsResponse = {
  metrics: Array<{
    name: string;
    description: string;
    value: number;
    unit: string;
    timestamp: string;
  }>;
};
