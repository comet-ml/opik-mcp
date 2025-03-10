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

export type SingleProjectResponse = ProjectResponse["content"][0];

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

export type SinglePromptResponse = PromptResponse["content"][0];

// Trace types
export type TraceResponse = {
  page: number;
  size: number;
  total: number;
  content: Array<{
    id: string;
    name: string;
    input: Record<string, any>;
    output: Record<string, any>;
    metadata: Record<string, any>;
    tags: string[];
    created_at: string;
    created_by: string;
    project_id: string;
  }>;
};

export type SingleTraceResponse = TraceResponse["content"][0];

// Trace stats types
export type TraceStatsResponse = {
  total_traces: number;
  total_spans: number;
  average_trace_duration_ms: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  stats_by_day: Array<{
    date: string;
    trace_count: number;
    span_count: number;
    total_tokens: number;
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
