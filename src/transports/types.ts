/**
 * Types for the transport layer of the MCP server
 */
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
 * Options for the Streamable HTTP transport host
 */
export interface StreamableHttpTransportOptions {
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
 * Response format for transport health check
 */
export interface HealthResponse {
  status: string;
}

/**
 * Response format for transport message responses
 */
export interface MessageResponse {
  status: string;
  message?: string;
}
