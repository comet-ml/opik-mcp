// Simple test client for MCP
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

async function main() {
  console.log("Starting MCP test client...");

  // Create a transport that runs our server
  const transport = new StdioClientTransport({
    command: "node",
    args: ["build/index.js", "--debug", "true"]
  });

  // Add event handlers for lifecycle events
  transport.onerror = (error) => {
    console.error("Transport error:", error);
  };

  transport.onclose = () => {
    console.log("Transport connection closed");
  };

  // Create the client
  const client = new Client(
    {
      name: "test-client",
      version: "1.0.0"
    },
    {
      capabilities: {
        tools: {}  // We're interested in tools
      }
    }
  );

  try {
    // Connect to the server
    console.log("Connecting to MCP server...");
    await client.connect(transport);
    console.log("Connected successfully!");

    // List available tools
    console.log("Requesting tool list...");
    const tools = await client.listTools();

    console.log("Available tools:");
    console.log(JSON.stringify(tools, null, 2));

    // Close the connection
    await client.close();
    console.log("Connection closed.");
  } catch (error) {
    console.error("Error:", error);
  }
}

main().catch(error => {
  console.error("Fatal error:", error);
  process.exit(1);
});
