import { Transport, SSETransportOptions, HealthResponse, MessageResponse } from './types.js';
import { JSONRPCMessage } from '@modelcontextprotocol/sdk/types.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { createMcpExpressApp } from '@modelcontextprotocol/sdk/server/express.js';
import express from 'express';
import http, { IncomingMessage } from 'http';
import fs from 'fs';
import cors from 'cors';
import {
  authenticateRemoteRequest,
  isSseAuthRequired,
  validateRemoteAuth,
} from '../utils/remote-auth.js';

// Setup file-based logging
const logFile = '/tmp/opik-mcp-sse.log';

function logToFile(message: string): void {
  try {
    const timestamp = new Date().toISOString();
    fs.appendFileSync(logFile, `[${timestamp}] ${message}\n`);
  } catch {
    // Silently fail if we can't write to the log file
  }
}

function parseCsvEnv(value: string | undefined): string[] {
  if (!value) {
    return [];
  }

  return value
    .split(',')
    .map(part => part.trim())
    .filter(Boolean);
}

function createRateLimiter() {
  const windowMs = Number(process.env.SSE_RATE_LIMIT_WINDOW_MS || 60_000);
  const maxRequests = Number(process.env.SSE_RATE_LIMIT_MAX || 120);
  const buckets = new Map<string, { count: number; resetAt: number }>();

  return (req: express.Request, res: express.Response, next: express.NextFunction) => {
    const token =
      (req.headers['x-api-key'] as string) ||
      (req.headers.authorization as string) ||
      req.ip ||
      'unknown';
    const key = `${token}:${req.path}`;
    const now = Date.now();
    const existing = buckets.get(key);

    if (!existing || existing.resetAt <= now) {
      buckets.set(key, { count: 1, resetAt: now + windowMs });
      next();
      return;
    }

    if (existing.count >= maxRequests) {
      const response: MessageResponse = {
        status: 'error',
        message: 'Too many requests. Retry later.',
      };
      res.status(429).json(response);
      return;
    }

    existing.count += 1;
    next();
  };
}

type NodeRequestWithAuth = IncomingMessage & {
  auth?: {
    token: string;
    clientId: string;
    scopes: string[];
    expiresAt?: number;
    extra?: Record<string, unknown>;
  };
};

/**
 * Streamable HTTP transport hosted on Express.
 *
 * Kept under the original class name for backward compatibility with existing
 * imports, but this now serves MCP on `/mcp` using the official transport.
 */
export class SSEServerTransport implements Transport {
  private app: express.Express;
  private server: http.Server | null = null;
  private port: number;
  private host: string;
  private started = false;
  private mcpTransport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined, // stateless mode for simple remote deployments
  });

  constructor(options: SSETransportOptions = {}) {
    this.port = options.port || 3001;
    this.host = options.host || process.env.SSE_HOST || 'localhost';
    this.app = createMcpExpressApp({ host: this.host });

    const allowedOrigins = parseCsvEnv(process.env.SSE_CORS_ORIGINS);
    if (allowedOrigins.length > 0) {
      this.app.use(
        cors({
          origin: allowedOrigins,
          methods: ['GET', 'POST', 'DELETE', 'OPTIONS'],
          allowedHeaders: ['content-type', 'authorization', 'x-api-key', 'comet-workspace'],
          credentials: false,
        })
      );
    }

    this.app.use(createRateLimiter());
    this.app.use(express.json({ limit: '1mb' }));

    this.app.get('/health', (_req, res) => {
      const response: HealthResponse = { status: 'ok' };
      res.json(response);
    });

    this.app.all('/mcp', async (req, res) => {
      try {
        if (isSseAuthRequired()) {
          const auth = authenticateRemoteRequest(
            req.headers as Record<string, string | string[] | undefined>
          );

          if (!auth.ok) {
            const errorResponse: MessageResponse = {
              status: 'error',
              message: auth.message,
            };
            res.status(auth.status).json(errorResponse);
            return;
          }

          const validation = await validateRemoteAuth(auth.context);
          if (!validation.ok) {
            const errorResponse: MessageResponse = {
              status: 'error',
              message: validation.message || 'Unauthorized',
            };
            res.status(validation.status).json(errorResponse);
            return;
          }

          const reqWithAuth = req as NodeRequestWithAuth;
          reqWithAuth.auth = {
            token: auth.context.apiKey || '',
            clientId: 'opik-mcp-remote',
            scopes: ['mcp'],
            extra: {
              workspaceName: auth.context.workspaceName,
            },
          };
        }

        await this.mcpTransport.handleRequest(req as NodeRequestWithAuth, res, req.body);
      } catch (error) {
        logToFile(`Error handling /mcp request: ${error}`);
        res.status(500).json({
          status: 'error',
          message: 'Internal server error',
        } satisfies MessageResponse);
      }
    });

    // Keep explicit response for previous non-standard endpoints.
    this.app.all(['/events', '/send'], (_req, res) => {
      res.status(410).json({
        status: 'error',
        message: 'Legacy endpoints removed. Use /mcp (Streamable HTTP).',
      } satisfies MessageResponse);
    });
  }

  set onclose(handler: (() => void) | undefined) {
    this.mcpTransport.onclose = handler;
  }

  get onclose(): (() => void) | undefined {
    return this.mcpTransport.onclose;
  }

  set onerror(handler: ((error: Error) => void) | undefined) {
    this.mcpTransport.onerror = handler;
  }

  get onerror(): ((error: Error) => void) | undefined {
    return this.mcpTransport.onerror;
  }

  set onmessage(handler: ((message: JSONRPCMessage) => void) | undefined) {
    this.mcpTransport.onmessage = handler as any;
  }

  get onmessage(): ((message: JSONRPCMessage) => void) | undefined {
    return this.mcpTransport.onmessage as any;
  }

  async start(): Promise<void> {
    if (this.started) {
      return;
    }

    this.started = true;
    await this.mcpTransport.start();
    this.server = http.createServer(this.app);

    return new Promise(resolve => {
      this.server?.listen(this.port, this.host, () => {
        logToFile(`Streamable HTTP transport listening on ${this.host}:${this.port}`);
        resolve();
      });
    });
  }

  async send(message: JSONRPCMessage): Promise<void> {
    await this.mcpTransport.send(message);
  }

  async close(): Promise<void> {
    if (!this.started) {
      return;
    }

    this.started = false;
    await this.mcpTransport.close();

    return new Promise((resolve, reject) => {
      if (this.server) {
        this.server.close(err => {
          if (err) {
            reject(err);
            return;
          }
          this.server = null;
          resolve();
        });
        return;
      }

      resolve();
    });
  }
}
