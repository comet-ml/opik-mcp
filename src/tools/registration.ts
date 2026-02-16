export function registerTool(
  server: any,
  name: string,
  description: string,
  inputSchema: any,
  handler: any
): void {
  if (typeof server.registerTool === 'function') {
    server.registerTool(
      name,
      {
        description,
        inputSchema,
      },
      handler
    );
    return;
  }

  server.tool(name, description, inputSchema, handler);
}

export function registerResource(
  server: any,
  name: string,
  uri: string,
  description: string,
  readCallback: any
): void {
  if (typeof server.registerResource === 'function') {
    server.registerResource(
      name,
      uri,
      {
        description,
      },
      readCallback
    );
    return;
  }

  server.resource(name, uri, readCallback);
}
