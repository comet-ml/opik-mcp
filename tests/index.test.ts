import { expect, jest, test, describe, beforeEach, afterEach } from '@jest/globals';

// Mock fetch function
const mockFetch = jest.fn();
global.fetch = mockFetch;

// Mock console.error
const originalConsoleError = console.error;
console.error = jest.fn();

// Mock process.exit
const originalProcessExit = process.exit;

// Type definitions
type ApiResponse<T> = {
  data: T | null;
  error: string | null;
};

// Helper function to make test API requests
async function makeApiRequestTest<T>(path: string, options: RequestInit = {}): Promise<ApiResponse<T>> {
  const API_HEADERS = {
    Accept: 'application/json',
    'Comet-Workspace': 'test-workspace',
    'Content-Type': 'application/json',
    authorization: 'test-api-key',
  };

  try {
    const response = await mockFetch(`https://api.example.com${path}`, {
      ...options,
      headers: {
        ...API_HEADERS,
        ...options.headers,
      },
    });

    if (!response.ok) {
      return {
        data: null,
        error: `HTTP error! status: ${response.status} ${JSON.stringify(
          response.body
        )}`,
      };
    }

    const data = await response.json() as T;
    return {
      data,
      error: null,
    };
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : 'Unknown error occurred';
    console.error('Error making API request:', error);
    return {
      data: null,
      error: errorMessage,
    };
  }
}

describe('Opik MCP API Functions', () => {
  beforeEach(() => {
    // Reset mocks before each test
    jest.clearAllMocks();

    // Mock process.exit
    process.exit = jest.fn() as any;

    // Setup default mock response
    mockFetch.mockResolvedValue({
      ok: true,
      json: jest.fn().mockResolvedValue({ data: 'test' }),
    });
  });

  afterEach(() => {
    // Restore original functions
    process.exit = originalProcessExit;
  });

  afterAll(() => {
    console.error = originalConsoleError;
  });

  describe('makeApiRequest', () => {
    test('should handle successful API requests', async () => {
      const mockResponse = {
        page: 1,
        size: 10,
        total: 1,
        content: [{ id: '123', name: 'Test Item' }]
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue(mockResponse),
      });

      const result = await makeApiRequestTest<typeof mockResponse>('/test-path');

      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.example.com/test-path',
        expect.objectContaining({
          headers: expect.objectContaining({
            Accept: 'application/json',
            'Comet-Workspace': 'test-workspace',
            'Content-Type': 'application/json',
            authorization: 'test-api-key',
          }),
        })
      );

      expect(result).toEqual({
        data: mockResponse,
        error: null,
      });
    });

    test('should handle API request errors', async () => {
      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      const result = await makeApiRequestTest('/test-path');

      expect(console.error).toHaveBeenCalledWith(
        expect.stringContaining('Error making API request:'),
        expect.any(Error)
      );

      expect(result).toEqual({
        data: null,
        error: 'Network error',
      });
    });

    test('should handle non-OK API responses', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 404,
        body: JSON.stringify({ error: 'Not found' }),
      });

      const result = await makeApiRequestTest('/test-path');

      expect(result.data).toBeNull();
      expect(result.error).toContain('HTTP error!');
      expect(result.error).toContain('404');
    });
  });

  // Tests for Projects/Workspaces API
  describe('Projects/Workspaces API', () => {
    test('getProjects should return list of projects', async () => {
      const mockResponse = {
        page: 1,
        size: 10,
        total: 2,
        content: [
          { id: '123', name: 'Project 1', description: 'Test project 1' },
          { id: '456', name: 'Project 2', description: 'Test project 2' }
        ]
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue(mockResponse),
      });

      const result = await makeApiRequestTest<typeof mockResponse>('/v1/private/projects?page=1&size=10');

      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.example.com/v1/private/projects?page=1&size=10',
        expect.anything()
      );

      expect(result.data).toEqual(mockResponse);
    });

    test('getProjectById should return project details', async () => {
      const mockResponse = {
        id: '123',
        name: 'Project 1',
        description: 'Test project 1'
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue(mockResponse),
      });

      const result = await makeApiRequestTest<typeof mockResponse>('/v1/private/projects/123');

      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.example.com/v1/private/projects/123',
        expect.anything()
      );

      expect(result.data).toEqual(mockResponse);
    });

    test('createProject should create a new project', async () => {
      const projectData = {
        name: 'New Project',
        description: 'A new test project'
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue({ success: true }),
      });

      const result = await makeApiRequestTest<{ success: boolean }>('/v1/private/projects', {
        method: 'POST',
        body: JSON.stringify(projectData),
      });

      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.example.com/v1/private/projects',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify(projectData),
        })
      );

      expect(result.error).toBeNull();
    });
  });

  // Tests for Traces API
  describe('Traces API', () => {
    test('getTraces should return list of traces', async () => {
      interface TraceContent {
        id: string;
        name: string;
        input: Record<string, any>;
        output: Record<string, any>;
        metadata: Record<string, any>;
        tags: string[];
        created_at: string;
        created_by: string;
        project_id: string;
      }

      interface TracesResponse {
        page: number;
        size: number;
        total: number;
        content: TraceContent[];
      }

      const mockResponse: TracesResponse = {
        page: 1,
        size: 10,
        total: 1,
        content: [
          {
            id: '123',
            name: 'Trace 1',
            input: { query: 'test' },
            output: { result: 'success' },
            metadata: { timestamp: new Date().toISOString() },
            tags: ['test'],
            created_at: new Date().toISOString(),
            created_by: 'user1',
            project_id: 'proj1'
          }
        ]
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue(mockResponse),
      });

      const result = await makeApiRequestTest<TracesResponse>('/v1/private/traces?page=1&size=10');

      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.example.com/v1/private/traces?page=1&size=10',
        expect.anything()
      );

      expect(result.data).toEqual(mockResponse);
    });

    test('getTraceStats should return trace statistics', async () => {
      interface TraceStats {
        total_traces: number;
        total_spans: number;
        average_trace_duration_ms: number;
        total_tokens: number;
        prompt_tokens: number;
        completion_tokens: number;
        stats_by_day: Array<{
          date: string;
          trace_count: number;
          span_count: number;
          total_tokens: number;
        }>;
      }

      const mockResponse: TraceStats = {
        total_traces: 100,
        total_spans: 500,
        average_trace_duration_ms: 250,
        total_tokens: 10000,
        prompt_tokens: 5000,
        completion_tokens: 5000,
        stats_by_day: [
          {
            date: '2023-04-01',
            trace_count: 50,
            span_count: 250,
            total_tokens: 5000
          },
          {
            date: '2023-04-02',
            trace_count: 50,
            span_count: 250,
            total_tokens: 5000
          }
        ]
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue(mockResponse),
      });

      const result = await makeApiRequestTest<TraceStats>('/v1/private/traces/stats?project_id=proj1');

      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.example.com/v1/private/traces/stats?project_id=proj1',
        expect.anything()
      );

      expect(result.data).toEqual(mockResponse);
    });
  });

  // Tests for Metrics API
  describe('Metrics API', () => {
    test('getMetrics should return metrics data', async () => {
      interface Metric {
        name: string;
        description: string;
        value: number;
        unit: string;
        timestamp: string;
      }

      interface MetricsResponse {
        metrics: Metric[];
      }

      const mockResponse: MetricsResponse = {
        metrics: [
          {
            name: 'trace_count',
            description: 'Number of traces',
            value: 42,
            unit: 'count',
            timestamp: new Date().toISOString()
          },
          {
            name: 'token_count',
            description: 'Number of tokens',
            value: 1000,
            unit: 'count',
            timestamp: new Date().toISOString()
          }
        ]
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: jest.fn().mockResolvedValue(mockResponse),
      });

      const result = await makeApiRequestTest<MetricsResponse>('/v1/private/metrics?metric_name=trace_count&project_id=proj1');

      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.example.com/v1/private/metrics?metric_name=trace_count&project_id=proj1',
        expect.anything()
      );

      expect(result.data).toEqual(mockResponse);
    });
  });
});
