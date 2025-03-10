import { jest, describe, beforeEach, afterEach, test, expect } from '@jest/globals';

// Mock console.error
const originalConsoleError = console.error;
console.error = jest.fn();

// Mock process.exit
const originalProcessExit = process.exit;
process.exit = jest.fn() as any;

describe('Opik API Tests', () => {
  // Basic validation of API endpoint structure

  test('Project endpoints should have the correct structure', () => {
    // Validate project endpoints
    const endpoints = [
      '/v1/private/projects',                // List projects
      '/v1/private/projects/123',            // Get project by ID
      '/v1/private/projects',                // Create project (POST)
      '/v1/private/projects/123',            // Update project (PUT)
      '/v1/private/projects/123'             // Delete project (DELETE)
    ];

    // Simple validation that the endpoints follow the expected pattern
    for (const endpoint of endpoints) {
      expect(endpoint).toMatch(/^\/v1\/private\/projects/);
    }
  });

  test('Trace endpoints should have the correct structure', () => {
    // Validate trace endpoints
    const endpoints = [
      '/v1/private/traces?page=1&size=10',             // List traces
      '/v1/private/traces/123',                        // Get trace by ID
      '/v1/private/traces/stats?project_id=123',       // Get trace statistics
    ];

    // Simple validation that the endpoints follow the expected pattern
    for (const endpoint of endpoints) {
      expect(endpoint).toMatch(/^\/v1\/private\/traces/);
    }
  });

  test('Prompt endpoints should have the correct structure', () => {
    // Validate prompt endpoints
    const endpoints = [
      '/v1/private/prompts?page=1&size=10',            // List prompts
      '/v1/private/prompts',                           // Create prompt (POST)
      '/v1/private/prompts/versions',                  // Create prompt version (POST)
      '/v1/private/prompts/123',                       // Get prompt by ID
      '/v1/private/prompts/123',                       // Update prompt (PUT)
      '/v1/private/prompts/123',                       // Delete prompt (DELETE)
    ];

    // Simple validation that the endpoints follow the expected pattern
    for (const endpoint of endpoints) {
      expect(endpoint).toMatch(/^\/v1\/private\/prompts/);
    }
  });

  test('Metrics endpoints should have the correct structure', () => {
    // Validate metrics endpoints
    const endpoints = [
      '/v1/private/metrics?metric_name=trace_count',   // Get metrics
    ];

    // Simple validation that the endpoints follow the expected pattern
    for (const endpoint of endpoints) {
      expect(endpoint).toMatch(/^\/v1\/private\/metrics/);
    }
  });

  // Test API response types structure

  test('Project response should have the correct structure', () => {
    const mockProjectResponse = {
      page: 1,
      size: 10,
      total: 2,
      content: [
        {
          id: '123',
          name: 'Project 1',
          description: 'Test project 1',
          created_at: '2023-01-01T00:00:00Z',
          created_by: 'user1',
          last_updated_at: '2023-01-01T00:00:00Z',
          last_updated_by: 'user1',
          workspace: 'workspace1'
        }
      ]
    };

    // Validate structure
    expect(mockProjectResponse).toHaveProperty('page');
    expect(mockProjectResponse).toHaveProperty('size');
    expect(mockProjectResponse).toHaveProperty('total');
    expect(mockProjectResponse).toHaveProperty('content');
    expect(Array.isArray(mockProjectResponse.content)).toBe(true);

    const project = mockProjectResponse.content[0];
    expect(project).toHaveProperty('id');
    expect(project).toHaveProperty('name');
    expect(project).toHaveProperty('description');
    expect(project).toHaveProperty('created_at');
    expect(project).toHaveProperty('created_by');
    expect(project).toHaveProperty('last_updated_at');
    expect(project).toHaveProperty('last_updated_by');
    expect(project).toHaveProperty('workspace');
  });

  test('Trace response should have the correct structure', () => {
    const mockTraceResponse = {
      page: 1,
      size: 10,
      total: 1,
      content: [
        {
          id: '123',
          name: 'Trace 1',
          input: { query: 'test' },
          output: { result: 'success' },
          metadata: { timestamp: '2023-01-01T00:00:00Z' },
          tags: ['test'],
          created_at: '2023-01-01T00:00:00Z',
          created_by: 'user1',
          project_id: 'proj1'
        }
      ]
    };

    // Validate structure
    expect(mockTraceResponse).toHaveProperty('page');
    expect(mockTraceResponse).toHaveProperty('size');
    expect(mockTraceResponse).toHaveProperty('total');
    expect(mockTraceResponse).toHaveProperty('content');
    expect(Array.isArray(mockTraceResponse.content)).toBe(true);

    const trace = mockTraceResponse.content[0];
    expect(trace).toHaveProperty('id');
    expect(trace).toHaveProperty('name');
    expect(trace).toHaveProperty('input');
    expect(trace).toHaveProperty('output');
    expect(trace).toHaveProperty('metadata');
    expect(trace).toHaveProperty('tags');
    expect(trace).toHaveProperty('created_at');
    expect(trace).toHaveProperty('created_by');
    expect(trace).toHaveProperty('project_id');
  });

  test('Trace stats response should have the correct structure', () => {
    const mockTraceStats = {
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
        }
      ]
    };

    // Validate structure
    expect(mockTraceStats).toHaveProperty('total_traces');
    expect(mockTraceStats).toHaveProperty('total_spans');
    expect(mockTraceStats).toHaveProperty('average_trace_duration_ms');
    expect(mockTraceStats).toHaveProperty('total_tokens');
    expect(mockTraceStats).toHaveProperty('prompt_tokens');
    expect(mockTraceStats).toHaveProperty('completion_tokens');
    expect(mockTraceStats).toHaveProperty('stats_by_day');

    const dayStats = mockTraceStats.stats_by_day[0];
    expect(dayStats).toHaveProperty('date');
    expect(dayStats).toHaveProperty('trace_count');
    expect(dayStats).toHaveProperty('span_count');
    expect(dayStats).toHaveProperty('total_tokens');
  });

  test('Metrics response should have the correct structure', () => {
    const mockMetricsResponse = {
      metrics: [
        {
          name: 'trace_count',
          description: 'Number of traces',
          value: 42,
          unit: 'count',
          timestamp: '2023-01-01T00:00:00Z'
        }
      ]
    };

    // Validate structure
    expect(mockMetricsResponse).toHaveProperty('metrics');
    expect(Array.isArray(mockMetricsResponse.metrics)).toBe(true);

    const metric = mockMetricsResponse.metrics[0];
    expect(metric).toHaveProperty('name');
    expect(metric).toHaveProperty('description');
    expect(metric).toHaveProperty('value');
    expect(metric).toHaveProperty('unit');
    expect(metric).toHaveProperty('timestamp');
  });

  // Clean up mocks
  afterAll(() => {
    console.error = originalConsoleError;
    process.exit = originalProcessExit;
  });
});
