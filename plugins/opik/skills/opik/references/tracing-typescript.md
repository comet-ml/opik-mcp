# TypeScript SDK Tracing Guide

Complete guide to tracing LLM applications with the Opik TypeScript SDK.

## Setup Note

Use [SKILL.md](../SKILL.md) for the canonical setup policy.

TypeScript default:

- **Check for existing `.env` / `.env.local` first.** If the project already has one with other API keys, append `OPIK_API_KEY` and `OPIK_WORKSPACE` to that same file. Also update `.env.example` / `.env.sample` if one exists.
- **If no env file exists:** create `.env.local` for Next.js/Vite projects, `.env` for everything else.
- **Never overwrite** existing values — only add missing vars.
- Prefer setting `projectName` in code instead of shared environment config.

If you use environment variables, the workspace variable is `OPIK_WORKSPACE`.

## Basic Usage

### Creating the Client

```typescript
import { Opik } from "opik";

// Using environment variables (recommended)
const client = new Opik();

// Or with explicit configuration
const client = new Opik({
  apiKey: "your-api-key",
  apiUrl: "https://www.comet.com/opik/api",
  projectName: "my-project",
  workspaceName: "my-workspace",
});
```

### Creating Traces and Spans

```typescript
import { Opik } from "opik";

const client = new Opik();

// Create a trace
const trace = client.trace({
  name: "my-agent",
  input: { query: "What is machine learning?" },
});

// Add a span for retrieval
const retrievalSpan = trace.span({
  name: "retrieve-context",
  type: "tool",
  input: { query: "machine learning" },
});
// ... retrieval logic
retrievalSpan.end({ output: { documents: ["doc1", "doc2"] } });

// Add a span for LLM call
const llmSpan = trace.span({
  name: "generate-response",
  type: "llm",
  input: { prompt: "Explain ML" },
});
// ... LLM call
llmSpan.end({
  output: { response: "Machine learning is..." },
  usage: { promptTokens: 10, completionTokens: 50 }
});

// End the trace
trace.end({ output: { response: "Machine learning is..." } });

// Ensure data is sent
await client.flush();
```

## Using Decorators

TypeScript 5+ supports decorators (experimental):

```typescript
import { track } from "opik";

class TranslationService {
  @track({ type: "llm" })
  async generateText(): Promise<string> {
    // Your LLM call
    return "Generated text";
  }

  @track({ name: "translate" })
  async translate(text: string): Promise<string> {
    // Translation logic
    return `Translated: ${text}`;
  }

  @track({ name: "process", projectName: "translation-service" })
  async process(): Promise<string> {
    const text = await this.generateText();
    return this.translate(text);
  }
}
```

### Decorator Options

```typescript
@track({
  name: "custom-name",           // Override method name
  type: "llm",                   // Span type
  projectName: "my-project",     // Project name
  tags: ["production"],          // Tags
  metadata: { version: "1.0" }   // Metadata
})
```

## Framework Integrations

### OpenAI Integration

```bash
npm install opik-openai
```

```typescript
import OpenAI from "openai";
import { trackOpenAI } from "opik-openai";

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const trackedOpenAI = trackOpenAI(openai);

// All calls are automatically traced
const completion = await trackedOpenAI.chat.completions.create({
  model: "gpt-4",
  messages: [{ role: "user", content: "Hello!" }],
});

// Flush before exit
await trackedOpenAI.flush();
```

### Vercel AI SDK Integration

```bash
npm install opik-vercel @opentelemetry/sdk-node @opentelemetry/auto-instrumentations-node
```

```typescript
import { openai } from "@ai-sdk/openai";
import { generateText } from "ai";
import { NodeSDK } from "@opentelemetry/sdk-node";
import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";
import { OpikExporter } from "opik-vercel";

// Set up OpenTelemetry with Opik
const sdk = new NodeSDK({
  traceExporter: new OpikExporter(),
  instrumentations: [getNodeAutoInstrumentations()],
});
sdk.start();

// AI SDK calls with telemetry enabled
const result = await generateText({
  model: openai("gpt-4o"),
  prompt: "What is love?",
  experimental_telemetry: { isEnabled: true },
});

console.log(result.text);
```

### LangChain.js Integration

```typescript
import { ChatOpenAI } from "@langchain/openai";
import { OpikTracer } from "opik";

const tracer = new OpikTracer();

const llm = new ChatOpenAI();
const response = await llm.invoke("Hello!", {
  callbacks: [tracer],
});
```

## Trace and Span Properties

### Trace Properties

```typescript
const trace = client.trace({
  name: string;              // Required: trace name
  input?: object;            // Input data
  output?: object;           // Output data
  metadata?: object;         // Custom metadata
  tags?: string[];           // Tags for filtering
  threadId?: string;         // Group related traces
  projectName?: string;      // Override project
});
```

### Span Properties

```typescript
const span = trace.span({
  name: string;              // Required: span name
  type?: "general" | "llm" | "tool" | "guardrail";
  input?: object;            // Input data
  output?: object;           // Output data
  metadata?: object;         // Custom metadata
  tags?: string[];           // Tags for filtering
  model?: string;            // Model name (for LLM spans)
  provider?: string;         // Provider name (for LLM spans)
  usage?: {                  // Token usage (for LLM spans)
    promptTokens?: number;
    completionTokens?: number;
    totalTokens?: number;
  };
});
```

## Ending Traces and Spans

Always end traces and spans to capture duration:

```typescript
// End with additional data
span.end({
  output: { result: "success" },
  metadata: { processingTime: 150 }
});

trace.end({
  output: { finalResult: "Complete response" }
});
```

## Nested Spans

Create hierarchical span structures:

```typescript
const trace = client.trace({ name: "pipeline" });

const parentSpan = trace.span({ name: "parent-operation" });

// Nested span
const childSpan = parentSpan.span({ name: "child-operation" });
childSpan.end({ output: { result: "child done" } });

parentSpan.end({ output: { result: "parent done" } });

trace.end({ output: { result: "pipeline done" } });
```

## Async Operations

Handle async operations properly:

```typescript
const trace = client.trace({ name: "async-pipeline" });

const span = trace.span({ name: "async-operation" });

try {
  const result = await someAsyncOperation();
  span.end({ output: { result } });
} catch (error) {
  span.end({
    output: { error: error.message },
    metadata: { errorType: error.name }
  });
  throw error;
}

trace.end({ output: { status: "complete" } });
await client.flush();
```

## Flushing Data

The SDK batches data for performance. Always flush before exit:

```typescript
// For scripts
await client.flush();

// For servers, flush on shutdown
process.on("SIGTERM", async () => {
  await client.flush();
  process.exit(0);
});
```

## Error Handling

Capture errors in spans:

```typescript
const span = trace.span({ name: "risky-operation" });

try {
  const result = await riskyOperation();
  span.end({ output: { result } });
} catch (error) {
  span.end({
    output: { error: error.message },
    metadata: {
      errorType: error.name,
      stack: error.stack
    }
  });
}
```

## Thread Management

Group related traces:

```typescript
// First interaction
const trace1 = client.trace({
  name: "chat-turn-1",
  threadId: "session-123",
  input: { message: "Hello" }
});
trace1.end({ output: { response: "Hi!" } });

// Second interaction (same thread)
const trace2 = client.trace({
  name: "chat-turn-2",
  threadId: "session-123",
  input: { message: "What's the weather?" }
});
trace2.end({ output: { response: "It's sunny!" } });
```

## Logging to Specific Projects

```typescript
// Via client
const client = new Opik({ projectName: "my-project" });

// Via trace
const trace = client.trace({
  name: "my-trace",
  projectName: "different-project"
});
```

## Entrypoint Functions & Local Runner

Mark functions as entrypoints so they can be triggered from the Opik UI via `opik connect`. Use the `track()` function (not the decorator form) with `entrypoint: true`.

### Basic Entrypoint

```typescript
import { track } from "opik";

const myAgent = track(
  {
    name: "my-agent",
    entrypoint: true,
    params: [{ name: "query", type: "string" }],
  },
  async (query: string) => {
    // agent logic
    return `Response to: ${query}`;
  }
);
```

> **Important:** Due to TypeScript compilation stripping parameter names and types at runtime, you **must** explicitly declare `params` with `[{ name: "...", type: "..." }]`. If `params` is omitted, all parameters are assumed to be `string` type.

### Express Server Integration

```typescript
import express from "express";
import { track } from "opik";

const summarize = track(
  {
    name: "summarize",
    entrypoint: true,
    params: [{ name: "message", type: "string" }],
  },
  async (message: string) => {
    // call your LLM here
    return `Summary of: ${message}`;
  }
);

const app = express();

app.get("/summarize", async (req, res) => {
  const result = await summarize(String(req.query.message ?? ""));
  res.send(result);
});

app.listen(3000, () => console.log("Listening on port 3000"));
```

Then pair with the Opik UI:

```bash
opik connect --pair <CODE> npx tsx summarise.ts
```

### Multiple Parameters

```typescript
const research = track(
  {
    name: "research-agent",
    entrypoint: true,
    params: [
      { name: "topic", type: "string" },
      { name: "depth", type: "number" },
      { name: "include_sources", type: "boolean" },
    ],
  },
  async (topic: string, depth: number, includeSources: boolean) => {
    // research logic
    return result;
  }
);
```

### What Happens After Pairing

1. The runner registers the entrypoint function(s) with Opik
2. The Opik UI shows the agent with an input form derived from `params`
3. User types input in the browser, clicks Run, agent executes locally
4. Full trace appears in Opik with spans, token usage, and cost
5. Jobs from the UI or Optimizer trigger agent runs

## Best Practices

1. **Always flush**: Call `client.flush()` before your application exits
2. **End spans explicitly**: Call `.end()` to capture accurate duration
3. **Use appropriate types**: Set `type` for proper categorization
4. **Handle errors gracefully**: Capture error info in spans before re-throwing
5. **Use threads for conversations**: Group related traces with `threadId`
6. **Add meaningful metadata**: Include user IDs, versions, environment info
7. **Avoid sensitive data**: Don't log PII, secrets, or credentials
8. **Always provide params for entrypoints**: TypeScript strips param names/types at runtime
