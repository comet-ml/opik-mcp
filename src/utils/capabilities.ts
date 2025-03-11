/**
 * Opik Comet API Capabilities
 *
 * This file defines the capabilities of the Opik Comet API to provide
 * better context to the MCP about what it can and cannot do.
 * Based on official Opik documentation: https://www.comet.com/docs/opik/
 */

export interface ApiCapability {
  available: boolean;
  features: string[];
  limitations: string[];
  examples?: string[];
  schema?: Record<string, any>;
}

export interface OpikCapabilities {
  prompts: ApiCapability & {
    versionControl: boolean;
    templateFormat: string;
  };
  projects: ApiCapability & {
    hierarchySupport: boolean;
    sharingSupport: boolean;
  };
  traces: ApiCapability & {
    dataRetention: string;
    searchCapabilities: string[];
    filterOptions: string[];
  };
  metrics: ApiCapability & {
    availableMetrics: string[];
    customMetricsSupport: boolean;
    visualizationSupport: boolean;
  };
  general: {
    apiVersion: string;
    authentication: string;
    rateLimit: string;
    supportedFormats: string[];
  };
}

/**
 * Detailed capabilities of the Opik Comet API
 * Based on official documentation: https://www.comet.com/docs/opik/
 */
export const opikCapabilities: OpikCapabilities = {
  prompts: {
    available: true,
    features: [
      "Create and manage prompt templates",
      "Version control for prompts",
      "Prompt playground for testing different prompts and models",
      "Managing prompts in code with the Python SDK",
      "Retrieve prompt history",
      "Update existing prompts",
      "Delete prompts"
    ],
    limitations: [
      "Limited prompt template variables support",
      "No automatic prompt optimization",
      "No A/B testing capabilities built-in",
      "No automatic prompt performance metrics"
    ],
    examples: [
      "Creating a prompt template for a specific use case",
      "Versioning prompts to track changes over time",
      "Testing different prompt variations in the playground",
      "Managing prompts programmatically with the SDK"
    ],
    versionControl: true,
    templateFormat: "String with variable placeholders using {{variable}} syntax",
    schema: {
      prompt: {
        id: "string",
        name: "string",
        description: "string",
        created_at: "timestamp",
        created_by: "string",
        last_updated_at: "timestamp",
        last_updated_by: "string",
        version_count: "number"
      },
      promptVersion: {
        id: "string",
        prompt_id: "string",
        version: "number",
        template: "string",
        created_at: "timestamp",
        created_by: "string",
        commit_message: "string"
      }
    }
  },

  projects: {
    available: true,
    features: [
      "Create and manage projects/workspaces",
      "Organize traces by project",
      "Project-level metrics and statistics",
      "Update project metadata",
      "Delete projects",
      "Monitoring dashboards for projects"
    ],
    limitations: [
      "No nested project hierarchies",
      "Limited project sharing or collaboration features",
      "No project templates or cloning",
      "Limited metadata customization"
    ],
    examples: [
      "Creating a project for a specific AI application",
      "Organizing traces by customer or use case",
      "Tracking metrics across different projects",
      "Setting up monitoring dashboards for a project"
    ],
    hierarchySupport: false,
    sharingSupport: false,
    schema: {
      project: {
        id: "string",
        name: "string",
        description: "string",
        created_at: "timestamp",
        created_by: "string",
        last_updated_at: "timestamp",
        last_updated_by: "string",
        workspace: "string"
      }
    }
  },

  traces: {
    available: true,
    features: [
      "Record and retrieve LLM interactions (traces)",
      "Log conversations and agent interactions",
      "Log multimodal traces",
      "Log distributed traces",
      "Annotate traces with feedback scores",
      "Track token usage and costs",
      "Filter traces by project",
      "Aggregate trace statistics",
      "View trace metadata",
      "Track latency and performance",
      "Export trace data",
      "OpenTelemetry integration"
    ],
    limitations: [
      "No real-time trace streaming",
      "Limited search capabilities within trace content",
      "No built-in trace comparison tools",
      "Limited custom trace tagging system"
    ],
    examples: [
      "Recording a conversation with an AI assistant",
      "Logging agent interactions in a complex workflow",
      "Tracking distributed traces across multiple services",
      "Annotating traces with feedback scores",
      "Analyzing token usage patterns",
      "Tracking costs across different models"
    ],
    dataRetention: "Configurable, default varies by deployment",
    searchCapabilities: [
      "Filter by project",
      "Filter by date range",
      "Filter by trace name",
      "Filter by trace type",
      "Basic text search in trace names"
    ],
    filterOptions: [
      "project_id",
      "project_name",
      "start_date",
      "end_date",
      "name",
      "type"
    ],
    schema: {
      trace: {
        id: "string",
        project_id: "string",
        name: "string",
        start_time: "timestamp",
        end_time: "timestamp",
        input: "object",
        output: "object",
        metadata: "object",
        usage: {
          completion_tokens: "number",
          prompt_tokens: "number",
          total_tokens: "number"
        },
        created_at: "timestamp",
        last_updated_at: "timestamp",
        created_by: "string",
        last_updated_by: "string",
        total_estimated_cost: "number",
        duration: "number",
        tags: "string[]",
        spans: "array"
      },
      span: {
        id: "string",
        trace_id: "string",
        name: "string",
        type: "string",
        start_time: "timestamp",
        end_time: "timestamp",
        input: "object",
        output: "object",
        metadata: "object",
        usage: "object",
        duration: "number"
      }
    }
  },

  metrics: {
    available: true,
    features: [
      "Track token usage over time",
      "Monitor costs and usage patterns",
      "Filter metrics by project",
      "Date range filtering",
      "Aggregate metrics by day/week/month",
      "LLM evaluation metrics (heuristic and LLM-as-judge)",
      "Evaluation metrics for hallucination detection",
      "Evaluation metrics for RAG evaluation",
      "Evaluation metrics for moderation",
      "Production monitoring dashboards",
      "Rules and alerts for metrics"
    ],
    limitations: [
      "Limited custom metric definitions",
      "Limited visualization options through API",
      "No real-time metrics streaming",
      "Limited alerting capabilities",
      "Limited export functionality"
    ],
    examples: [
      "Tracking monthly token usage",
      "Monitoring cost trends over time",
      "Evaluating LLM outputs for hallucinations",
      "Measuring RAG performance with context precision/recall",
      "Setting up production monitoring dashboards",
      "Creating rules for metric thresholds"
    ],
    availableMetrics: [
      "total_tokens",
      "prompt_tokens",
      "completion_tokens",
      "cost",
      "latency",
      "requests_count",
      "error_rate",
      "hallucination_score",
      "answer_relevance",
      "context_precision",
      "context_recall",
      "moderation_score"
    ],
    customMetricsSupport: true,
    visualizationSupport: true,
    schema: {
      metric: {
        name: "string",
        description: "string",
        value: "number",
        unit: "string",
        timestamp: "timestamp",
        project_id: "string"
      },
      evaluation: {
        id: "string",
        name: "string",
        metric: "string",
        score: "number",
        details: "object",
        timestamp: "timestamp"
      }
    }
  },

  general: {
    apiVersion: "v1",
    authentication: "API Key via authorization header",
    rateLimit: "Configurable, high volume support (40+ million traces per day)",
    supportedFormats: ["JSON"]
  }
};

/**
 * Get capabilities information based on configuration
 * @param config The current configuration
 * @returns Filtered capabilities based on what's enabled
 */
export function getEnabledCapabilities(config: any): Partial<OpikCapabilities> {
  return {
    prompts: config.mcpEnablePromptTools ? opikCapabilities.prompts : { available: false, features: [], limitations: [] } as any,
    projects: config.mcpEnableProjectTools ? opikCapabilities.projects : { available: false, features: [], limitations: [] } as any,
    traces: config.mcpEnableTraceTools ? opikCapabilities.traces : { available: false, features: [], limitations: [] } as any,
    metrics: config.mcpEnableMetricTools ? opikCapabilities.metrics : { available: false, features: [], limitations: [] } as any,
    general: opikCapabilities.general
  };
}

/**
 * Get a description of what Opik Comet can and cannot do
 * @param config The current configuration
 * @returns A string description of capabilities
 */
export function getCapabilitiesDescription(config: any): string {
  const capabilities = getEnabledCapabilities(config);

  let description = "Opik Comet Capabilities:\n\n";

  // General capabilities
  description += "General:\n";
  description += `- API Version: ${capabilities.general?.apiVersion || 'v1'}\n`;
  description += `- Authentication: ${capabilities.general?.authentication || 'API Key'}\n`;
  description += `- Rate Limit: ${capabilities.general?.rateLimit || 'Default'}\n\n`;

  // Add each capability section
  for (const [key, capability] of Object.entries(capabilities)) {
    if (key === 'general') continue;

    const cap = capability as ApiCapability;
    if (!cap.available) {
      description += `${key.charAt(0).toUpperCase() + key.slice(1)}: Not available\n\n`;
      continue;
    }

    description += `${key.charAt(0).toUpperCase() + key.slice(1)}:\n`;
    description += "Features:\n";
    cap.features.forEach(feature => {
      description += `- ${feature}\n`;
    });

    description += "\nLimitations:\n";
    cap.limitations.forEach(limitation => {
      description += `- ${limitation}\n`;
    });

    description += "\n";
  }

  return description;
}
