/**
 * Examples module for Opik Comet
 * Provides usage examples for common tasks with the Opik API
 */

export interface ExampleData {
  title: string;
  description: string;
  steps: string[];
  codeExample: string;
}

// Define the examples for various tasks
const examples: Record<string, ExampleData> = {
  'create-prompt': {
    title: 'Create Prompt',
    description: 'Create a new prompt in Opik to use with your LLM applications.',
    steps: [
      'Initialize the Opik client with your API key',
      'Define a name for your prompt',
      'Call the createPrompt API endpoint',
      'Store the returned promptId for future reference',
    ],
    codeExample: `
// Python Example
import opik

# Initialize the client
client = opik.Client(api_key="YOUR_API_KEY")

# Create a new prompt
prompt = client.create_prompt(name="My Customer Support Prompt")

# Store the prompt ID for future use
prompt_id = prompt["id"]
print(f"Created prompt with ID: {prompt_id}")

// JavaScript/TypeScript Example
import { OpikClient } from '@opik/sdk';

// Initialize the client
const client = new OpikClient({ apiKey: "YOUR_API_KEY" });

// Create a new prompt
const prompt = await client.createPrompt({ name: "My Customer Support Prompt" });

// Store the prompt ID for future use
const promptId = prompt.id;
console.log(\`Created prompt with ID: \${promptId}\`);
`,
  },

  'version-prompt': {
    title: 'Version Prompt',
    description: 'Create a new version of an existing prompt with updated template content.',
    steps: [
      'Initialize the Opik client with your API key',
      'Retrieve the prompt ID of the prompt you want to version',
      'Define the new template content',
      'Add a commit message describing the changes',
      'Call the createPromptVersion API endpoint',
    ],
    codeExample: `
// Python Example
import opik

# Initialize the client
client = opik.Client(api_key="YOUR_API_KEY")

# Define the prompt ID and new template content
prompt_id = "prompt_123456"
template = """
You are a helpful customer support agent for Acme Inc.
Please respond to the customer's query in a friendly and professional manner.

Customer query: {{query}}
"""
commit_message = "Updated template with more specific instructions"

# Create a new version
version = client.create_prompt_version(
    prompt_id=prompt_id,
    template=template,
    commit_message=commit_message
)

print(f"Created version {version['version']} of prompt {prompt_id}")

// JavaScript/TypeScript Example
import { OpikClient } from '@opik/sdk';

// Initialize the client
const client = new OpikClient({ apiKey: "YOUR_API_KEY" });

// Define the prompt ID and new template content
const promptId = "prompt_123456";
const template = \`
You are a helpful customer support agent for Acme Inc.
Please respond to the customer's query in a friendly and professional manner.

Customer query: {{query}}
\`;
const commitMessage = "Updated template with more specific instructions";

// Create a new version
const version = await client.createPromptVersion({
  promptId,
  template,
  commitMessage
});

console.log(\`Created version \${version.version} of prompt \${promptId}\`);
`,
  },

  'create-project': {
    title: 'Create Project',
    description: 'Create a new project in Opik to organize your prompts, traces, and evaluations.',
    steps: [
      'Initialize the Opik client with your API key',
      'Define a name and optional description for your project',
      'Call the createProject API endpoint',
      'Store the returned projectId for future reference',
    ],
    codeExample: `
// Python Example
import opik

# Initialize the client
client = opik.Client(api_key="YOUR_API_KEY")

# Create a new project
project = client.create_project(
    name="Customer Support Bot",
    description="A project for our customer support chatbot application"
)

# Store the project ID for future use
project_id = project["id"]
print(f"Created project with ID: {project_id}")

// JavaScript/TypeScript Example
import { OpikClient } from '@opik/sdk';

// Initialize the client
const client = new OpikClient({ apiKey: "YOUR_API_KEY" });

// Create a new project
const project = await client.createProject({
  name: "Customer Support Bot",
  description: "A project for our customer support chatbot application"
});

// Store the project ID for future use
const projectId = project.id;
console.log(\`Created project with ID: \${projectId}\`);
`,
  },

  'log-trace': {
    title: 'Log Trace',
    description:
      'Log a trace of an LLM interaction to capture inputs, outputs, and metadata for analysis.',
    steps: [
      'Initialize the Opik client with your API key',
      'Prepare the trace data including inputs, outputs, and metadata',
      'Optionally specify a project ID to associate the trace with',
      'Call the logTrace API endpoint',
      'Store the returned traceId for future reference',
    ],
    codeExample: `
// Python Example
import opik
from datetime import datetime

# Initialize the client
client = opik.Client(api_key="YOUR_API_KEY")

# Define the trace data
trace_data = {
    "project_id": "project_123456",  # Optional
    "inputs": {
        "prompt": "What is the capital of France?",
        "model": "gpt-4",
        "temperature": 0.7
    },
    "outputs": {
        "completion": "The capital of France is Paris.",
        "tokens": 8,
        "finish_reason": "stop"
    },
    "metadata": {
        "user_id": "user_789",
        "session_id": "session_456",
        "timestamp": datetime.now().isoformat()
    }
}

# Log the trace
trace = client.log_trace(trace_data)

# Store the trace ID for future use
trace_id = trace["id"]
print(f"Logged trace with ID: {trace_id}")

// JavaScript/TypeScript Example
import { OpikClient } from '@opik/sdk';

// Initialize the client
const client = new OpikClient({ apiKey: "YOUR_API_KEY" });

// Define the trace data
const traceData = {
  projectId: "project_123456",  // Optional
  inputs: {
    prompt: "What is the capital of France?",
    model: "gpt-4",
    temperature: 0.7
  },
  outputs: {
    completion: "The capital of France is Paris.",
    tokens: 8,
    finishReason: "stop"
  },
  metadata: {
    userId: "user_789",
    sessionId: "session_456",
    timestamp: new Date().toISOString()
  }
};

// Log the trace
const trace = await client.logTrace(traceData);

// Store the trace ID for future use
const traceId = trace.id;
console.log(\`Logged trace with ID: \${traceId}\`);
`,
  },

  'analyze-traces': {
    title: 'Analyze Traces',
    description:
      'Search and analyze traces to gain insights into your LLM application performance.',
    steps: [
      'Initialize the Opik client with your API key',
      'Define search criteria such as time range, project ID, or content filters',
      'Call the searchTraces API endpoint',
      'Process the returned traces to extract insights',
      'Optionally use the getTraceStats API for aggregated metrics',
    ],
    codeExample: `
// Python Example
import opik
from datetime import datetime, timedelta

# Initialize the client
client = opik.Client(api_key="YOUR_API_KEY")

# Define search parameters
end_date = datetime.now()
start_date = end_date - timedelta(days=7)

# Search for traces
traces = client.search_traces(
    project_id="project_123456",
    start_date=start_date.isoformat(),
    end_date=end_date.isoformat(),
    query="capital of France",  # Optional text search
    limit=100
)

print(f"Found {len(traces)} traces")

# Get aggregated statistics
stats = client.get_trace_stats(
    project_id="project_123456",
    start_date=start_date.isoformat(),
    end_date=end_date.isoformat()
)

print(f"Average response time: {stats['avg_response_time']}ms")
print(f"Total traces: {stats['total_traces']}")

// JavaScript/TypeScript Example
import { OpikClient } from '@opik/sdk';

// Initialize the client
const client = new OpikClient({ apiKey: "YOUR_API_KEY" });

// Define search parameters
const endDate = new Date();
const startDate = new Date(endDate);
startDate.setDate(startDate.getDate() - 7);

// Search for traces
const traces = await client.searchTraces({
  projectId: "project_123456",
  startDate: startDate.toISOString(),
  endDate: endDate.toISOString(),
  query: "capital of France",  // Optional text search
  limit: 100
});

console.log(\`Found \${traces.length} traces\`);

// Get aggregated statistics
const stats = await client.getTraceStats({
  projectId: "project_123456",
  startDate: startDate.toISOString(),
  endDate: endDate.toISOString()
});

console.log(\`Average response time: \${stats.avgResponseTime}ms\`);
console.log(\`Total traces: \${stats.totalTraces}\`);
`,
  },

  'evaluate-response': {
    title: 'Evaluate Response',
    description:
      "Evaluate LLM responses using Opik's evaluation metrics to measure quality and performance.",
    steps: [
      'Initialize the Opik client with your API key',
      'Select the appropriate evaluation metric',
      'Prepare the parameters for the evaluation',
      'Call the evaluateMetric API endpoint',
      'Process the evaluation results',
    ],
    codeExample: `
// Python Example
import opik

# Initialize the client
client = opik.Client(api_key="YOUR_API_KEY")

# Evaluate for hallucination
hallucination_result = client.evaluate_metric(
    metric="hallucination",
    parameters={
        "answer": "Einstein was born in 1879 in Germany and developed the theory of relativity.",
        "context": "Albert Einstein was born on March 14, 1879, in Ulm, Germany."
    }
)

print(f"Hallucination score: {hallucination_result['score']}")

# Evaluate for answer relevance
relevance_result = client.evaluate_metric(
    metric="answerrelevance",
    parameters={
        "question": "What are the main causes of climate change?",
        "answer": "Climate change is primarily caused by greenhouse gas emissions from human activities."
    }
)

print(f"Relevance score: {relevance_result['score']}")

// JavaScript/TypeScript Example
import { OpikClient } from '@opik/sdk';

// Initialize the client
const client = new OpikClient({ apiKey: "YOUR_API_KEY" });

// Evaluate for hallucination
const hallucinationResult = await client.evaluateMetric({
  metric: "hallucination",
  parameters: {
    answer: "Einstein was born in 1879 in Germany and developed the theory of relativity.",
    context: "Albert Einstein was born on March 14, 1879, in Ulm, Germany."
  }
});

console.log(\`Hallucination score: \${hallucinationResult.score}\`);

// Evaluate for answer relevance
const relevanceResult = await client.evaluateMetric({
  metric: "answerrelevance",
  parameters: {
    question: "What are the main causes of climate change?",
    answer: "Climate change is primarily caused by greenhouse gas emissions from human activities."
  }
});

console.log(\`Relevance score: \${relevanceResult.score}\`);
`,
  },
};

/**
 * Get an example for a specific task
 * @param task The task to get an example for
 * @returns Example data for the specified task or null if not found
 */
export function getExampleForTask(task?: string): ExampleData | null {
  if (!task) return null;

  // Normalize the task string
  const normalizedTask = task.toLowerCase().trim();

  // Direct match first
  for (const [key, example] of Object.entries(examples)) {
    if (key.replace('-', ' ') === normalizedTask) {
      return example;
    }
  }

  // Fuzzy match if direct match fails
  for (const [key, example] of Object.entries(examples)) {
    const keyWords = key.split('-');
    const taskWords = normalizedTask.split(/\s+/);

    // Check if all key words are in the task
    const allWordsMatch = keyWords.every(word =>
      taskWords.some(taskWord => taskWord.includes(word) || word.includes(taskWord))
    );

    if (allWordsMatch) {
      return example;
    }
  }

  // Check if the task contains any of the example keys
  for (const [key, example] of Object.entries(examples)) {
    if (
      normalizedTask.includes(key.replace('-', ' ')) ||
      key.replace('-', ' ').includes(normalizedTask)
    ) {
      return example;
    }
  }

  return null;
}

/**
 * Get a list of all available example tasks
 * @returns Array of task titles
 */
export function getAllExampleTasks(): string[] {
  return Object.values(examples).map(example => example.title);
}
