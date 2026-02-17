import { runWithRequestContext } from '../utils/request-context.js';
import { ResourceTemplate } from '@modelcontextprotocol/sdk/server/mcp.js';
import config from '../config.js';

const MISSING_API_KEY_MESSAGE = [
  'This Opik MCP request requires an API key.',
  'Set OPIK_API_KEY in the environment where the server runs,',
  'or send Authorization: Bearer <token> with MCP requests.',
  'If you are onboarding in a coding agent or MCP client, start with setup guidance tools',
  'like get-opik-help or get-server-info, then add your key and retry.',
].join(' ');

type ToolRegistrationOptions = {
  requiresApiKey?: boolean;
  title?: string;
  annotations?: {
    readOnlyHint?: boolean;
    destructiveHint?: boolean;
    idempotentHint?: boolean;
    openWorldHint?: boolean;
  };
  outputSchema?: any;
  _meta?: Record<string, unknown>;
};

function inferAnnotations(name: string): ToolRegistrationOptions['annotations'] {
  const readPrefixes = ['get-', 'list-', 'search-', 'read-'];
  const mutatePrefixes = ['create-', 'delete-', 'update-', 'add-', 'save-'];

  if (readPrefixes.some(prefix => name.startsWith(prefix))) {
    return {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    };
  }

  if (mutatePrefixes.some(prefix => name.startsWith(prefix))) {
    return {
      readOnlyHint: false,
      destructiveHint: name.startsWith('delete-'),
      idempotentHint: false,
      openWorldHint: false,
    };
  }

  return undefined;
}

function withRequestContext<T extends (...args: any[]) => any>(handler: T, requiresApiKey = true) {
  return (...args: any[]) => {
    const extra = [...args]
      .reverse()
      .find(arg => arg && typeof arg === 'object' && 'authInfo' in arg);
    const authInfo = extra?.authInfo;
    const context = {
      apiKey: authInfo?.token as string | undefined,
      workspaceName: authInfo?.extra?.workspaceName as string | undefined,
    };

    if (requiresApiKey && !(context.apiKey || config.apiKey)) {
      return {
        content: [
          {
            type: 'text',
            text: MISSING_API_KEY_MESSAGE,
          },
        ],
      };
    }

    return runWithRequestContext(context, () => handler(...args));
  };
}

export function registerTool(
  server: any,
  name: string,
  description: string,
  inputSchema: any,
  handler: any,
  options: ToolRegistrationOptions = {}
): void {
  const wrappedHandler = withRequestContext(handler, options.requiresApiKey !== false);

  if (typeof server.registerTool === 'function') {
    const inferredAnnotations = inferAnnotations(name);
    const mergedAnnotations = {
      ...inferredAnnotations,
      ...options.annotations,
    };

    server.registerTool(
      name,
      {
        ...(options.title && { title: options.title }),
        description,
        inputSchema,
        ...(Object.keys(mergedAnnotations).length > 0 && { annotations: mergedAnnotations }),
        ...(options.outputSchema && { outputSchema: options.outputSchema }),
        ...(options._meta && { _meta: options._meta }),
      },
      wrappedHandler
    );
    return;
  }

  server.tool(name, description, inputSchema, wrappedHandler);
}

export function registerResource(
  server: any,
  name: string,
  uri: string,
  description: string,
  readCallback: any
): void {
  const wrappedReadCallback = withRequestContext(readCallback);

  if (typeof server.registerResource === 'function') {
    server.registerResource(
      name,
      uri,
      {
        description,
      },
      wrappedReadCallback
    );
    return;
  }

  server.resource(name, uri, wrappedReadCallback);
}

export function registerResourceTemplate(
  server: any,
  name: string,
  uriTemplate: string,
  description: string,
  readCallback: any,
  listCallback?: any
): void {
  const wrappedReadCallback = withRequestContext(readCallback);
  const wrappedListCallback = listCallback ? withRequestContext(listCallback) : undefined;
  const template = new ResourceTemplate(uriTemplate, {
    list: wrappedListCallback,
  });

  if (typeof server.registerResource === 'function') {
    server.registerResource(
      name,
      template,
      {
        description,
      },
      wrappedReadCallback
    );
    return;
  }

  server.resource(name, template, wrappedReadCallback);
}

export function registerPrompt(
  server: any,
  name: string,
  description: string,
  argsSchema: any,
  handler: any,
  options: { title?: string } = {}
): void {
  const wrappedHandler = withRequestContext(handler);

  if (typeof server.registerPrompt === 'function') {
    server.registerPrompt(
      name,
      {
        ...(options.title && { title: options.title }),
        description,
        argsSchema,
      },
      wrappedHandler
    );
    return;
  }

  server.prompt(name, description, argsSchema, wrappedHandler);
}
