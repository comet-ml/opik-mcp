---
title: Using Opik MCP with AI Coding Assistants
description: Connect Opik to Cursor, VS Code GitHub Copilot, and Claude Desktop via the Model Context Protocol (MCP) for seamless LLM trace inspection inside your IDE.
---

# Using Opik MCP with AI Coding Assistants

The Opik MCP server exposes your Opik traces, prompts, projects, and metrics directly inside any MCP-compatible AI assistant. Instead of switching between your IDE and the Opik dashboard, you can inspect traces, query experiments, and manage prompts without leaving your editor.

This guide covers setup for **Cursor**, **VS Code with GitHub Copilot**, and **Claude Desktop**.

---

## Prerequisites

- An Opik account ([cloud](https://www.comet.com/signup?from=llm) or [self-hosted](https://www.comet.com/docs/opik/self-host/overview))
- Your Opik API key (Settings → API Keys in the Opik dashboard)
- Node.js >= 18

---

## How It Works

The `opik-mcp` package runs as a local MCP server. Your AI assistant connects to it and gains access to tools like:

- `list_projects` — browse your Opik projects
- `get_traces` — fetch recent traces from a project
- `get_trace_stats` — query aggregated trace metrics
- `list_prompts` — view and search your prompt library
- `get_experiment` — retrieve experiment details and scores

You can then ask your AI assistant natural language questions like:

> "Show me the last 10 traces from my chatbot project where the hallucination score is above 0.8"

---

## Option 1 — Cursor

### Setup

1. Open Cursor and go to **Settings** → **Cursor Settings** → **MCP**
2. Click **"Add new MCP server"**
3. Fill in the form:

```json
{
  "mcpServers": {
    "opik": {
      "command": "npx",
      "args": ["-y", "opik-mcp", "--apiKey", "YOUR_OPIK_API_KEY"]
    }
  }
}
```

Replace `YOUR_OPIK_API_KEY` with your actual API key.

4. Click **Save** and restart Cursor.

### Verify

Open Cursor Chat (`Cmd+L` / `Ctrl+L`) and type:

> "List my Opik projects"

Cursor will call the `list_projects` tool and return your project names.

### For Self-Hosted Opik

Add the `--apiUrl` flag pointing to your instance:

```json
{
  "mcpServers": {
    "opik": {
      "command": "npx",
      "args": [
        "-y",
        "opik-mcp",
        "--apiKey", "YOUR_API_KEY",
        "--apiUrl", "http://localhost:5173/api"
      ]
    }
  }
}
```

---

## Option 2 — VS Code with GitHub Copilot

### Setup

1. Open VS Code and press `Cmd+Shift+P` / `Ctrl+Shift+P`
2. Search for **"Open MCP Configuration"** and select it
3. Add the following to your `mcp.json`:

```json
{
  "servers": {
    "opik": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "opik-mcp", "--apiKey", "YOUR_OPIK_API_KEY"]
    }
  }
}
```

4. Save the file. VS Code will automatically connect to the MCP server.

### Verify

Open GitHub Copilot Chat (`Cmd+Ctrl+I` / `Ctrl+Alt+I`) and switch to **Agent mode**. Type:

> "Get the traces from my production project from the last hour"

Copilot Agent will call the Opik MCP tools and return the results inline.

---

## Option 3 — Claude Desktop

### Setup

1. Open Claude Desktop
2. Go to **Settings** → **Developer** → **Edit Config**
3. Add the Opik server to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "opik": {
      "command": "npx",
      "args": ["-y", "opik-mcp", "--apiKey", "YOUR_OPIK_API_KEY"]
    }
  }
}
```

4. Save and restart Claude Desktop.

### Verify

In Claude Desktop, start a new conversation and type:

> "List my Opik projects and show me the trace count for each"

You should see a tool call followed by your project data.

---

## Example Workflows

### Debugging a failing RAG pipeline

```
You: Get the last 20 traces from my "rag-chatbot" project where 
     the answer_relevancy score is below 0.5

Opik MCP: [returns 20 traces with low relevancy scores]

You: What's the most common input pattern in these failing traces?

[Claude/Cursor/Copilot analyzes the returned trace data and identifies patterns]
```

### Comparing prompt versions

```
You: List all prompts in my library that contain the word "summarize"

Opik MCP: [returns matching prompts with version history]

You: Compare version 3 and version 7 of the "document-summarizer" prompt

[AI assistant shows a diff and explains the changes]
```

### Monitoring production quality

```
You: Show me the average hallucination score for my "production" 
     project over the last 7 days

Opik MCP: [returns aggregated metrics]

You: Is the trend improving or getting worse?

[AI assistant analyzes the time-series data]
```

---

## Troubleshooting

**Server doesn't appear in the assistant**

Make sure Node.js >= 18 is installed:
```bash
node --version
```

Try running the server manually to check for errors:
```bash
npx -y opik-mcp --apiKey YOUR_API_KEY
```

**Authentication errors (HTTP 401)**

- Verify your API key is correct in the Opik dashboard under Settings → API Keys.
- If using a self-hosted instance, confirm the `--apiUrl` matches your deployment URL exactly (no trailing slash).

**No projects returned**

Check that your API key has access to at least one project. Log in to the Opik dashboard and verify projects exist under your account.

**Slow responses**

The MCP server makes live API calls to Opik on every tool invocation. If your Opik instance is self-hosted on a slow network, responses may take a few seconds. This is expected.

---

## Available Tools Reference

| Tool | Description |
|------|-------------|
| `list_projects` | List all projects in your workspace |
| `get_project` | Get details of a specific project |
| `get_traces` | Fetch traces with optional filters |
| `get_trace` | Get a single trace by ID |
| `get_trace_stats` | Aggregated metrics for a project |
| `list_prompts` | List prompts in your library |
| `get_prompt` | Get a prompt with version history |
| `list_experiments` | List experiments in a project |
| `get_experiment` | Get experiment details and scores |

---

## Related Resources

- [Opik MCP GitHub repository](https://github.com/comet-ml/opik-mcp)
- [Opik documentation](https://www.comet.com/docs/opik/)
- [MCP specification](https://modelcontextprotocol.io)
- [Opik bounty program](https://www.comet.com/docs/opik/contributing/developer-programs/bounties)
