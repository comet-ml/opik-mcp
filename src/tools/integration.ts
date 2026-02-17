import { registerTool } from './registration.js';

const integrationDocs = `
# OPIK Agentic Onboarding

## Goals

You must help me:
1. Integrate the Opik client with my existing LLM application
2. Set up tracing for my LLM calls and chains

## Rules

Before you begin, you must understand and strictly adhere to these core principles:
1. Code Preservation & Integration Guidelines:
   - Existing business logic must remain untouched and unmodified
   - Only add Opik-specific code (decorators, imports, handlers, env vars)
   - Integration must be non-invasive and backwards compatible
2. Process Requirements:
   - Follow the workflow steps sequentially without deviation
   - Validate completion of each step before proceeding
   - Request explicit approval for any workflow modifications
3. Documentation & Resources:
   - Reference official Opik documentation at https://www.comet.com/docs/opik/quickstart.md
   - Follow Opik best practices and recommended patterns
   - Maintain detailed integration notes and configuration details
4. Testing & Validation:
   - Verify Opik integration without impacting existing functionality
   - Validate tracing works correctly for all LLM interactions
   - Ensure proper error handling and logging

## Integration Workflow
### Step 1: Language and Compatibility Check

First, analyze the codebase to identify:
1. Primary programming language and frameworks
2. Existing LLM integrations and patterns
Compatibility Requirements:
- Supported Languages: Python, JavaScript/TypeScript
If the codebase uses unsupported languages:
- Stop immediately
- Inform me that the codebase is unsupported for AI integration
Only proceed to Step 2 if:
- Language is Python or JavaScript/TypeScript

### Step 2: Codebase Discovery & Entrypoint Confirmation

After verifying language compatibility, perform a full codebase scan with the following objectives:
- LLM Touchpoints: Locate all files and functions that invoke or interface with LLMs or can be a candidates for tracing.
- Entrypoint Detection: Identify the primary application entry point(s) (e.g., main script, API route, CLI handler). If ambiguous, pause and request clarification on which component(s) are most important to trace before proceeding.
  ⚠️ Do not proceed to Step 3 without explicit confirmation if the entrypoint is unclear.
- Return the LLM Touchpoints to me

### Step 3: Discover Available Integrations

After I confirm the LLM Touchpoints and entry point, find the list of supported integrations at https://www.comet.com/docs/opik/tracing/integrations/overview.md

### Step 4: Deep Analysis Confirmed files for LLM Frameworks & SDKs

Using the files confirmed in Step 2, perform targeted inspection to detect specific LLM-related technologies in use, such as:
SDKs: openai, anthropic, huggingface, etc.
Frameworks: LangChain, LlamaIndex, Haystack, etc.

### Step 5: Pre-Implementation Development Plan (Approval Required)

Do not write or modify code yet. You must propose me a step-by-step plan including:
- Opik packages to install
- Files to be modified
- Code snippets for insertion, clearly scoped and annotated
- Where to place Opik API keys, with placeholder comments (Visit https://comet.com/opik/your-workspace-name/get-started to copy your API key)
  Wait for approval before proceeding!

### Step 6: Execute the Integration Plan

  After approval:
- Run the package installation command via terminal (pip install opik, npm install opik, etc.).
- Apply code modifications exactly as described in Step 5.
- Keep all additions minimal and non-invasive.
  Upon completion, review the changes made and confirm installation success.

### Step 7: Request User Review and Wait

  Notify me that all integration steps are complete.
"Please run the application and verify if Opik is capturing traces as expected. Let me know if you need adjustments."

### Step 8: Debugging Loop (If Needed)

If issues are reported:
1. Parse the error or unexpected behavior from feedback.
2. Re-query the Opik docs using https://www.comet.com/docs/opik/quickstart.md if needed.
3. Propose a minimal fix and await approval.
4. Apply and revalidate.
`;

export const loadIntegrationTools = (server: any) => {
  registerTool(
    server,
    'opik-integration-docs',
    'Provides detailed documentation on how to integrate Opik with your LLM application',
    {},
    async (_args: any) => {
      return {
        content: [{ type: 'text', text: integrationDocs }],
      };
    },
    { requiresApiKey: false }
  );
  return server;
};
