/**
 * Types for the transport layer of the MCP server
 */

import { Response } from 'express';
import { JSONRPCMessage } from '@modelcontextprotocol/sdk/types.js';

/**
 * Common interface for all transport mechanisms
 */
export interface Transport {
  /**
   * Handler for when a message is received from the client
   */
  onmessage?: (message: JSONRPCMessage) => void;

  /**
   * Handler for when an error occurs in the transport
   */
  onerror?: (error: Error) => void;

  /**
   * Handler for when the transport connection is closed
   */
  onclose?: () => void;

  /**
   * Method to send a message to the client
   * @param message The message to send
   */
  send(message: JSONRPCMessage): Promise<void>;

  /**
   * Method to start the transport
   */
  start(): Promise<void>;

  /**
   * Method to close the transport
   */
  close(): Promise<void>;
}

/**
 * Options for the SSE server transport
 */
export interface SSETransportOptions {
  /**
   * Port to listen on
   * @default 3001
   */
  port?: number;
  /**
   * Host interface to bind
   * @default 127.0.0.1
   */
  host?: string;
}

/**
 * Map of connected SSE clients
 */
export type SSEClientMap = Map<string, Response>;

/**
 * HTTP response for SSE connections
 */
export interface SSEResponse extends Response {
  /**
   * Method to send a message to the client
   * @param data The data to send
   */
  sse?: (data: any) => void;
}

/**
 * Response format for SSE health check
 */
export interface HealthResponse {
  status: string;
}

/**
 * Response format for SSE message responses
 */
export interface MessageResponse {
  status: string;
  message?: string;
}
