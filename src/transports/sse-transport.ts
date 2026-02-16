import { Transport, SSETransportOptions, HealthResponse, MessageResponse } from './types.js';
import { JSONRPCMessage } from '@modelcontextprotocol/sdk/types.js';
import express from 'express';
import http from 'http';
import fs from 'fs';
import cors from 'cors';
import { extractContextFromHeaders, runWithRequestContext } from '../utils/request-context.js';
import { isSseAuthRequired, validateRemoteAuth } from '../utils/remote-auth.js';

// Setup file-based logging
const logFile = '/tmp/opik-mcp-sse.log';

function logToFile(message: string): void {
  try {
    const timestamp = new Date().toISOString();
    fs.appendFileSync(logFile, `[${timestamp}] ${message}\n`);
  } catch (error) {
    // Silently fail if we can't write to the log file
  }
}

/**
 * SSE (Server-Sent Events) transport for the MCP server
 * This allows the server to be accessed over a network connection
 * with a simple unidirectional streaming protocol
 */
export class SSEServerTransport implements Transport {
  private app = express();
  private server: http.Server | null = null;
  private port: number;
  private clients: Map<string, express.Response> = new Map();
  private started = false;

  onclose?: () => void;
  onerror?: (error: Error) => void;
  onmessage?: (message: JSONRPCMessage) => void;

  constructor(options: SSETransportOptions = {}) {
    this.port = options.port || 3001;

    // Setup Express server
    this.app.use(cors());
    this.app.use(express.json());

    // Add health check endpoint
    this.app.get('/health', (req: express.Request, res: express.Response) => {
      const response: HealthResponse = { status: 'ok' };
      res.json(response);
    });

    // SSE endpoint for receiving MCP messages
    this.app.get('/events', async (req: express.Request, res: express.Response) => {
      const requestContext = extractContextFromHeaders(
        req.headers as Record<string, string | string[] | undefined>
      );

      if (isSseAuthRequired()) {
        const authResult = await validateRemoteAuth(requestContext);
        if (!authResult.ok) {
          const errorResponse: MessageResponse = {
            status: 'error',
            message: authResult.message || 'Unauthorized',
          };
          res.status(authResult.status).json(errorResponse);
          return;
        }
      }

      const clientId = (req.query.clientId as string) || Date.now().toString();

      // Set headers for SSE
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      });

      // Send a welcome message
      res.write(`data: ${JSON.stringify({ type: 'connection', clientId })}\n\n`);

      // Add client to the list
      this.clients.set(clientId, res);
      logToFile(`SSE client connected: ${clientId}`);

      // Handle client disconnect
      req.on('close', () => {
        this.clients.delete(clientId);
        logToFile(`SSE client disconnected: ${clientId}`);
      });
    });

    // Endpoint for sending messages to the MCP server
    this.app.post('/send', async (req: express.Request, res: express.Response) => {
      const message = req.body;
      const requestContext = extractContextFromHeaders(
        req.headers as Record<string, string | string[] | undefined>
      );

      if (isSseAuthRequired()) {
        const authResult = await validateRemoteAuth(requestContext);
        if (!authResult.ok) {
          const errorResponse: MessageResponse = {
            status: 'error',
            message: authResult.message || 'Unauthorized',
          };
          res.status(authResult.status).json(errorResponse);
          return;
        }
      }

      if (this.onmessage) {
        try {
          // Forward the message to the MCP connection handler
          runWithRequestContext(requestContext, () => {
            this.onmessage?.(message);
          });
          const response: MessageResponse = { status: 'success' };
          res.status(200).json(response);
        } catch (error) {
          logToFile(`Error handling message: ${error}`);
          if (this.onerror) {
            this.onerror(error instanceof Error ? error : new Error(String(error)));
          }
          const errorResponse: MessageResponse = {
            status: 'error',
            message: String(error),
          };
          res.status(500).json(errorResponse);
        }
      } else {
        const errorResponse: MessageResponse = {
          status: 'error',
          message: 'Server not ready',
        };
        res.status(503).json(errorResponse);
      }
    });

    // Create HTTP server
    this.server = http.createServer(this.app);
  }

  /**
   * Start listening for connections
   */
  async start(): Promise<void> {
    if (this.started) {
      return;
    }

    this.started = true;

    return new Promise(resolve => {
      if (!this.server) {
        this.server = http.createServer(this.app);
      }

      this.server.listen(this.port, () => {
        logToFile(`SSE transport listening on port ${this.port}`);
        resolve();
      });
    });
  }

  /**
   * Send a message to all connected clients
   */
  async send(message: JSONRPCMessage): Promise<void> {
    const messageStr = JSON.stringify(message);

    // Broadcast the message to all connected clients
    for (const [clientId, client] of this.clients.entries()) {
      try {
        client.write(`data: ${messageStr}\n\n`);
      } catch (error) {
        logToFile(`Error sending message to client ${clientId}: ${error}`);
        // Remove client if we can't send messages to it
        this.clients.delete(clientId);
      }
    }
  }

  /**
   * Close the transport
   */
  async close(): Promise<void> {
    if (!this.started) {
      return;
    }

    this.started = false;

    return new Promise((resolve, reject) => {
      // Close all SSE connections
      for (const [clientId, client] of this.clients.entries()) {
        try {
          client.end();
        } catch (error) {
          logToFile(`Error closing connection to client ${clientId}: ${error}`);
        }
      }

      // Clear the clients map
      this.clients.clear();

      // Close the HTTP server
      if (this.server) {
        this.server.close(err => {
          if (err) {
            logToFile(`Error closing SSE server: ${err}`);
            reject(err);
          } else {
            logToFile('SSE transport stopped');
            if (this.onclose) this.onclose();
            resolve();
          }
        });
        this.server = null;
      } else {
        resolve();
      }
    });
  }
}
