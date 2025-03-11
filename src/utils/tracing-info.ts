/**
 * Tracing information module for Opik Comet
 * Provides detailed information about Opik's tracing capabilities
 */

interface TracingTopicInfo {
  title: string;
  description: string;
  keyFeatures: string[];
  useCases: string[];
}

interface TracingOverviewInfo {
  title: string;
  description: string;
  availableTopics: string[];
}

type TracingInfo = TracingTopicInfo | TracingOverviewInfo;

/**
 * Get information about Opik's tracing capabilities
 * @param topic Optional topic to get specific information about
 * @returns Information about the specified topic or an overview if no topic is provided
 */
export function getTracingInfo(topic?: string): TracingInfo | null {
  // If no topic is provided, return an overview
  if (!topic) {
    return {
      title: 'Opik Tracing Capabilities',
      description:
        'Opik provides comprehensive tracing capabilities to help you understand and analyze your LLM applications. Traces capture the full context of LLM interactions, including inputs, outputs, and metadata.',
      availableTopics: ['traces', 'spans', 'feedback', 'search', 'visualization'],
    };
  }

  // Return information based on the requested topic
  switch (topic.toLowerCase()) {
    case 'traces':
      return {
        title: 'Traces',
        description:
          'Traces are complete records of LLM interactions, capturing the full context of a request including inputs, outputs, and metadata. They provide a comprehensive view of how your LLM application is performing.',
        keyFeatures: [
          'Automatic capture of inputs and outputs',
          'Metadata collection for context',
          'Hierarchical structure with spans',
          'Long-term storage and retrieval',
          'Integration with evaluation metrics',
        ],
        useCases: [
          'Debugging LLM application issues',
          'Auditing LLM responses',
          'Compliance and governance',
          'Performance analysis',
          'Training data collection',
        ],
      };

    case 'spans':
      return {
        title: 'Spans',
        description:
          'Spans are individual units within a trace that represent discrete operations or steps in your LLM application. They help break down complex interactions into manageable pieces for analysis.',
        keyFeatures: [
          'Hierarchical relationship (parent-child)',
          'Timing information for performance analysis',
          'Custom attributes for context',
          'Support for nested operations',
          'Automatic correlation with parent traces',
        ],
        useCases: [
          'Performance bottleneck identification',
          'Detailed step-by-step analysis',
          'Tracking complex multi-step LLM workflows',
          'Measuring time spent in different components',
          'Correlating errors with specific operations',
        ],
      };

    case 'feedback':
      return {
        title: 'Feedback',
        description:
          'Feedback allows you to annotate traces with human or automated evaluations. This helps you build a dataset of evaluated responses that can be used for model improvement and analysis.',
        keyFeatures: [
          'Binary (thumbs up/down) feedback',
          'Structured feedback with categories',
          'Free-form text comments',
          'Support for multiple feedback sources',
          'Integration with evaluation metrics',
        ],
        useCases: [
          'Building labeled datasets for fine-tuning',
          'Identifying patterns in problematic responses',
          'Measuring user satisfaction',
          'Comparing model versions',
          'Creating ground truth for automated evaluations',
        ],
      };

    case 'search':
      return {
        title: 'Search',
        description:
          'Opik provides powerful search capabilities to help you find specific traces based on content, metadata, or performance characteristics. This makes it easy to analyze patterns and identify issues.',
        keyFeatures: [
          'Full-text search across inputs and outputs',
          'Metadata filtering',
          'Time-based queries',
          'Performance metric filtering',
          'Support for complex boolean queries',
        ],
        useCases: [
          'Finding examples of specific behaviors',
          'Identifying patterns in errors',
          'Analyzing performance trends',
          'Auditing responses for compliance',
          'Building targeted datasets for evaluation',
        ],
      };

    case 'visualization':
      return {
        title: 'Visualization',
        description:
          'Opik provides visualization tools to help you understand your traces and spans. These visualizations make it easier to identify patterns, bottlenecks, and issues in your LLM applications.',
        keyFeatures: [
          'Timeline views of traces and spans',
          'Performance dashboards',
          'Metric trend analysis',
          'Comparison views for A/B testing',
          'Custom visualization options',
        ],
        useCases: [
          'Performance monitoring over time',
          'Comparing model versions',
          'Identifying usage patterns',
          'Tracking evaluation metrics',
          'Presenting insights to stakeholders',
        ],
      };

    default:
      return null;
  }
}
