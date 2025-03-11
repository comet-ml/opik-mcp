import { jest, describe, test, expect } from '@jest/globals';
import { getExampleForTask, getAllExampleTasks, ExampleData } from '../src/utils/examples';

describe('Examples Module Tests', () => {
  // Test getting an example for a specific task
  test('getExampleForTask should return an example for a valid task', () => {
    // Test with valid tasks
    const validTasks = [
      'create prompt',
      'version prompt',
      'create project',
      'log trace',
      'analyze traces',
      'evaluate response'
    ];

    for (const task of validTasks) {
      const result = getExampleForTask(task);

      // Verify the result structure
      expect(result).toBeDefined();
      if (result) {
        expect(result).toHaveProperty('title');
        expect(result).toHaveProperty('description');
        expect(result).toHaveProperty('steps');
        expect(result).toHaveProperty('codeExample');

        // Verify the content is relevant to the task
        expect(result.title.toLowerCase()).toContain(task.toLowerCase());
        expect(Array.isArray(result.steps)).toBe(true);
        expect(result.steps.length).toBeGreaterThan(0);
        expect(typeof result.codeExample).toBe('string');
        expect(result.codeExample.length).toBeGreaterThan(0);
      }
    }
  });

  // Test getting an example with an invalid task
  test('getExampleForTask should return null for an invalid task', () => {
    const result = getExampleForTask('invalid-task');
    expect(result).toBeNull();
  });

  // Test case insensitivity and fuzzy matching
  test('getExampleForTask should be case insensitive and support fuzzy matching', () => {
    // Test case insensitivity
    const lowerCase = getExampleForTask('create prompt');
    const upperCase = getExampleForTask('CREATE PROMPT');
    const mixedCase = getExampleForTask('CrEaTe PrOmPt');

    expect(lowerCase).not.toBeNull();
    expect(upperCase).not.toBeNull();
    expect(mixedCase).not.toBeNull();

    if (lowerCase && upperCase && mixedCase) {
      expect(lowerCase).toEqual(upperCase);
      expect(lowerCase).toEqual(mixedCase);
    }

    // Test fuzzy matching
    const exactMatch = getExampleForTask('create prompt');
    const fuzzyMatch1 = getExampleForTask('creating a prompt');
    const fuzzyMatch2 = getExampleForTask('how to create prompt');
    const fuzzyMatch3 = getExampleForTask('prompt creation');

    expect(fuzzyMatch1).toBeDefined();
    expect(fuzzyMatch2).toBeDefined();
    expect(fuzzyMatch3).toBeDefined();

    // They might not be exactly equal due to fuzzy matching, but they should be for the same task
    if (fuzzyMatch1 && fuzzyMatch2 && fuzzyMatch3) {
      expect(fuzzyMatch1.title).toContain('Create Prompt');
      expect(fuzzyMatch2.title).toContain('Create Prompt');
      expect(fuzzyMatch3.title).toContain('Create Prompt');
    }
  });

  // Test getting all example tasks
  test('getAllExampleTasks should return a list of all available tasks', () => {
    const result = getAllExampleTasks();

    // Verify the result is an array
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);

    // Verify all expected tasks are included
    const expectedTasks = [
      'create prompt',
      'version prompt',
      'create project',
      'log trace',
      'analyze traces',
      'evaluate response'
    ];

    for (const task of expectedTasks) {
      expect(result.some(t => t.toLowerCase().includes(task.toLowerCase()))).toBe(true);
    }
  });
});
