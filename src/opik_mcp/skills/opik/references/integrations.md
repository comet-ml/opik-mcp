# Opik Integrations Reference

Comprehensive guide to Opik integrations organized by integration mechanism.

> **Using integration callbacks inside `@opik.track`:** Some integrations (like LiteLLM's `OpikLogger`) need the current span context passed explicitly to nest under an existing trace. See each integration's section for the correct pattern. Without this, the callback creates orphaned top-level traces.

## A. Python — Direct Opik SDK Integrations

These use modules that exist in `opik.integrations.*`. They provide the richest tracing with automatic token/cost capture.

### OpenAI

```python
from opik.integrations.openai import track_openai
from openai import OpenAI

client = track_openai(OpenAI())

response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

Also works with `AsyncOpenAI` and `AzureOpenAI`:

```python
from openai import AsyncOpenAI, AzureOpenAI

async_client = track_openai(AsyncOpenAI())
azure_client = track_openai(AzureOpenAI())
```

### OpenAI Agents SDK

```python
from opik.integrations.openai.agents import OpikTracingProcessor
from agents import set_trace_processors

set_trace_processors([OpikTracingProcessor()])

# All OpenAI Agents/Swarm operations are now traced
```

### Anthropic

```python
from opik.integrations.anthropic import track_anthropic
import anthropic

client = track_anthropic(anthropic.Anthropic())

response = client.messages.create(
    model="claude-3-sonnet-20240229",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}]
)
```

### AWS Bedrock

```python
from opik.integrations.bedrock import track_bedrock
import boto3
import json

bedrock = boto3.client("bedrock-runtime")
tracked_client = track_bedrock(bedrock)

response = tracked_client.invoke_model(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    body=json.dumps({"prompt": "Hello"})
)
```

### AWS SageMaker

```python
from opik.integrations.sagemaker import track_sagemaker
import boto3

sagemaker = boto3.client("sagemaker-runtime")
tracked_client = track_sagemaker(sagemaker)
```

### Google Gemini (GenAI)

Note: The module is `genai`, not `gemini`.

```python
from opik.integrations.genai import track_genai
from google import genai

client = track_genai(genai.Client())

response = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="Hello!"
)
```

### LangChain

```python
from opik.integrations.langchain import OpikTracer
from langchain_openai import ChatOpenAI

tracer = OpikTracer()
llm = ChatOpenAI()
response = llm.invoke("Hello!", config={"callbacks": [tracer]})
```

### LangGraph

**Option 1: Callback approach**

```python
from opik.integrations.langchain import OpikTracer

graph = ...  # Your LangGraph
app = graph.compile()

tracer = OpikTracer(graph=app.get_graph(xray=True))
result = app.invoke(
    {"messages": [HumanMessage(content="Hello")]},
    config={"callbacks": [tracer]}
)
```

**Option 2: Wrapper approach**

```python
from opik.integrations.langchain import OpikTracer, track_langgraph

graph = ...
app = graph.compile()

opik_tracer = OpikTracer()
tracked_app = track_langgraph(app, opik_tracer)
result = tracked_app.invoke({"messages": [HumanMessage(content="Hello")]})
```

### LlamaIndex

**Option 1: Global handler** (recommended)

```bash
pip install llama-index-callbacks-opik
```

```python
from llama_index.core import set_global_handler

set_global_handler("opik")

# All LlamaIndex operations are now traced
```

**Option 2: Manual callback handler**

```python
from opik.integrations.llama_index import LlamaIndexCallbackHandler
from llama_index.core import Settings

Settings.callback_manager.add_handler(LlamaIndexCallbackHandler())
```

### CrewAI

```python
from opik.integrations.crewai import track_crewai
from crewai import Agent, Task, Crew

agent = Agent(role="Researcher", goal="Research topics", backstory="Expert researcher")
task = Task(description="Research ML", agent=agent)
crew = Crew(agents=[agent], tasks=[task])

# crew= parameter is required for v1.0.0+
track_crewai(project_name="my-project", crew=crew)

result = crew.kickoff()
```

### DSPy

```python
from opik.integrations.dspy import OpikCallback
import dspy

dspy.configure(callbacks=[OpikCallback()])

# DSPy modules and optimizers are now traced
```

### Google ADK (Agent Development Kit)

```python
from opik.integrations.adk import OpikTracer, track_adk_agent_recursive

opik_tracer = OpikTracer()
agent = ...  # Your ADK agent
track_adk_agent_recursive(agent, opik_tracer)
```

### Haystack

Requires the `HAYSTACK_CONTENT_TRACING_ENABLED` environment variable:

```python
import os
os.environ["HAYSTACK_CONTENT_TRACING_ENABLED"] = "true"

from opik.integrations.haystack import OpikConnector
from haystack import Pipeline

pipeline = Pipeline()
pipeline.add_component("opik", OpikConnector("pipeline-name"))
# Add other components...
```

### AI Suite

```python
from opik.integrations.aisuite import track_aisuite
import aisuite as ai

client = track_aisuite(ai.Client())
```

### Guardrails AI

```python
from opik.integrations.guardrails import OpikTracer as GuardrailsTracer

tracer = GuardrailsTracer()
# Use with Guardrails AI validation
```

### Harbor

```python
from opik.integrations.harbor import track_harbor
```

## B. Python — Via OpenAI-Compatible API

Many providers expose OpenAI-compatible endpoints. Use `track_openai` with a custom `base_url`:

```python
from opik.integrations.openai import track_openai
from openai import OpenAI

# Generic pattern — change base_url and api_key per provider
client = track_openai(OpenAI(
    base_url="https://api.provider.com/v1",
    api_key="your-provider-api-key"
))
```

### Groq

```python
client = track_openai(OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.environ["GROQ_API_KEY"]
))

response = client.chat.completions.create(
    model="llama-3.1-70b-versatile",
    messages=[{"role": "user", "content": "Hello"}]
)
```

### DeepSeek

```python
client = track_openai(OpenAI(
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ["DEEPSEEK_API_KEY"]
))
```

### Fireworks AI

```python
client = track_openai(OpenAI(
    base_url="https://api.fireworks.ai/inference/v1",
    api_key=os.environ["FIREWORKS_API_KEY"]
))
```

### OpenRouter

```python
client = track_openai(OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"]
))
```

### Portkey

```python
client = track_openai(OpenAI(
    base_url="https://api.portkey.ai/v1",
    api_key=os.environ["PORTKEY_API_KEY"]
))
```

### Cohere

```python
client = track_openai(OpenAI(
    base_url="https://api.cohere.com/compatibility/v1",
    api_key=os.environ["COHERE_API_KEY"]
))
```

### BytePlus

```python
client = track_openai(OpenAI(
    base_url="https://api.byteplus.com/v1",
    api_key=os.environ["BYTEPLUS_API_KEY"]
))
```

### Ollama (OpenAI-compatible mode)

```python
client = track_openai(OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"  # Ollama doesn't require a real key
))

response = client.chat.completions.create(
    model="llama3",
    messages=[{"role": "user", "content": "Hello"}]
)
```

## C. Python — Via LiteLLM

LiteLLM provides a unified interface to 100+ LLM providers. The Opik integration is on the LiteLLM side:

**Standalone usage** (no `@opik.track`):

```python
from litellm.integrations.opik.opik import OpikLogger
import litellm

litellm.callbacks = [OpikLogger()]

response = litellm.completion(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello"}]
)
```

**Inside `@opik.track`** — pass `current_span_data` via metadata so the callback nests under the active trace:

```python
from opik import track
from opik.opik_context import get_current_span_data
from litellm.integrations.opik.opik import OpikLogger
import litellm

litellm.callbacks = [OpikLogger()]

@track
def call_llm(messages, model="gpt-4"):
    return litellm.completion(
        model=model,
        messages=messages,
        metadata={
            "opik": {
                "current_span_data": get_current_span_data(),
                "tags": ["litellm"],
            },
        },
    )

```

Use this for providers like Together AI, Novita AI, IBM WatsonX, xAI Grok, Mistral AI, and any other LiteLLM-supported model:

```python
# Together AI via LiteLLM
response = litellm.completion(model="together_ai/meta-llama/Llama-3-70b", messages=[...])

# Mistral via LiteLLM
response = litellm.completion(model="mistral/mistral-large-latest", messages=[...])

# xAI Grok via LiteLLM
response = litellm.completion(model="xai/grok-beta", messages=[...])
```

## D. Python — Via Logfire/OTLP Bridge

### Pydantic AI

Pydantic AI uses Logfire for observability. Bridge to Opik via OTLP:

```python
import logfire

logfire.configure(send_to_logfire=False)
logfire.instrument_pydantic_ai()

# Pydantic AI agent runs are now traced via OTLP to Opik
from pydantic_ai import Agent
agent = Agent("openai:gpt-4")
```

## E. Python — Via OpenTelemetry OTLP Export

Frameworks that support OpenTelemetry can send traces to Opik's OTLP endpoint. Configure the OTLP exporter to point at your Opik instance:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://www.comet.com/opik/api/v1/private/otel"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=your-api-key,Comet-Workspace=your-workspace"
```

Frameworks that use this approach:
- **Autogen / AG2** — configure OTLP in Autogen's telemetry settings
- **Agno** — uses OpenInference instrumentor with OTLP export
- **LiveKit Agents** — configure OTLP export in LiveKit settings
- **Smolagents** — uses OpenInference instrumentor with OTLP export
- **Semantic Kernel** — configure OTLP in Semantic Kernel's telemetry
- **Strands Agents** — configure OTLP export
- **Pipecat** — configure OTLP in Pipecat settings
- **Microsoft Agent Framework** — configure OTLP export
- **VoltAgent** — configure OTLP export

## F. Python — Manual with @opik.track

For providers or frameworks without a direct integration, use `@opik.track` to manually instrument:

### Instructor

Track the underlying provider first, then patch with Instructor:

```python
from opik.integrations.openai import track_openai
from openai import OpenAI
import instructor

tracked_client = track_openai(OpenAI())
client = instructor.from_openai(tracked_client)

# Instructor calls are traced through the tracked OpenAI client
```

### Ollama (native client)

```python
import opik
import ollama

@opik.track(type="llm")
def chat_with_ollama(prompt: str) -> str:
    response = ollama.chat(
        model="llama3",
        messages=[{"role": "user", "content": prompt}]
    )

    opik.opik_context.update_current_span(
        model="llama3",
        provider="ollama",
        metadata={"source": "ollama-native"}
    )

    return response["message"]["content"]
```

## G. TypeScript Integrations

### OpenAI

```typescript
import { trackOpenAI } from "opik-openai";
import OpenAI from "openai";

const openai = trackOpenAI(new OpenAI());

const response = await openai.chat.completions.create({
  model: "gpt-4",
  messages: [{ role: "user", content: "Hello!" }],
});
```

### LangChain.js

```typescript
import { OpikCallbackHandler } from "opik-langchain";
import { ChatOpenAI } from "@langchain/openai";

const handler = new OpikCallbackHandler();

const llm = new ChatOpenAI();
await llm.invoke("Hello", { callbacks: [handler] });
```

### Vercel AI SDK

```typescript
import { OpikExporter } from "opik-vercel";
import { NodeSDK } from "@opentelemetry/sdk-node";
import { generateText } from "ai";
import { openai } from "@ai-sdk/openai";

const sdk = new NodeSDK({
  traceExporter: new OpikExporter(),
});
sdk.start();

const { text } = await generateText({
  model: openai("gpt-4"),
  prompt: "Hello",
  experimental_telemetry: {
    isEnabled: true,
    functionId: "my-function",
  },
});
```

### Google Gemini

```typescript
import { trackGemini } from "opik-gemini";
```

### Mastra

Mastra uses OTLP — no direct Opik wrapper. Configure Mastra's telemetry to export via OTLP:

```typescript
import { Mastra } from "mastra";

const mastra = new Mastra({
  // ... your config
  telemetry: {
    export: {
      type: "otlp",
      endpoint: "https://www.comet.com/opik/api/v1/private/otel",
      headers: {
        Authorization: "your-api-key",
        "Comet-Workspace": "your-workspace",
      },
    },
  },
});
```

### Cloudflare Workers AI

Use the Opik client directly for manual tracing:

```typescript
import { Opik } from "opik";

const client = new Opik({ projectName: "workers-ai" });

const trace = client.trace({ name: "worker-request", input: { prompt } });
const span = trace.span({ name: "ai-call", type: "llm" });
// ... Workers AI call
span.end({ output: { response } });
trace.end({ output: { response } });

await client.flush();
```

## H. No-Code Platforms

### Cursor

Enable Opik in Cursor settings:
1. Open Cursor Settings
2. Navigate to AI settings
3. Add Opik API key
4. Enable trace logging

### Dify

1. Go to Dify workspace settings
2. Add Opik as an observability provider
3. Configure API key and project name
4. All Dify workflows are automatically traced

### Flowise

1. Open Flowise admin panel
2. Go to Integrations > Observability
3. Add Opik configuration
4. Enable tracing for selected flows

### Langflow

1. Access Langflow settings
2. Configure Opik integration
3. Add API credentials
4. Flows automatically send traces

### n8n

Use the Opik node in n8n:
1. Add "Opik" node to workflow
2. Configure credentials
3. Connect to LLM nodes for automatic tracing

### OpenWebUI

1. Go to OpenWebUI admin settings
2. Navigate to Integrations
3. Enable Opik tracing
4. Configure API key

## Integration Best Practices

### Choosing an Integration

| Scenario | Recommended Approach |
|----------|---------------------|
| Single LLM provider with direct SDK support | Provider-specific (OpenAI, Anthropic, etc.) |
| OpenAI-compatible provider | `track_openai` with custom `base_url` |
| Multiple providers via single interface | LiteLLM with `OpikLogger` callback |
| Agent framework with direct support | Framework-specific (LangChain, CrewAI, etc.) |
| OTLP-compatible framework | OTLP export to Opik endpoint |
| No integration available | Manual `@opik.track` decorator |
| No-code platform | Platform-specific configuration |

### Layering Integrations

You can combine framework-level integrations (e.g., LangChain tracer + tracked OpenAI client). When combining with `@opik.track` decorators, pass the current span context via metadata so callbacks nest correctly — see the LiteLLM section above for the pattern.

```python
from opik.integrations.openai import track_openai
from opik.integrations.langchain import OpikTracer

# Track both raw OpenAI calls and LangChain operations
openai_client = track_openai(OpenAI())
langchain_tracer = OpikTracer()

# LangChain with traced OpenAI underneath
llm = ChatOpenAI(client=openai_client)
response = llm.invoke("Hello", config={"callbacks": [langchain_tracer]})
```

### Environment Variables

All integrations respect these environment variables:

```bash
export OPIK_API_KEY="your-api-key"
export OPIK_URL_OVERRIDE="https://www.comet.com/opik/api"
export OPIK_PROJECT_NAME="my-project"
export OPIK_WORKSPACE="my-workspace"
export OPIK_TRACK_DISABLE="false"  # Set to "true" to disable
```
