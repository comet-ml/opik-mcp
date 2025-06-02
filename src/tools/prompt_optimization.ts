import { z } from 'zod';
import { loadConfig } from './../config.js';
import { generateText } from 'ai';
import { openai } from '@ai-sdk/openai';
import { system_prompt_creation } from '../prompts/system_prompt_creation.js';
import { system_prompt_editing } from '../prompts/system_prompt_editing.js';

const config = loadConfig();

export const loadPromptOptimizationTools = (server: any) => {
  if (config.mcpEnablePromptOptimizationTools) {
    server.tool(
      'create-system-prompt-for-evaluation',
      'Create a specialized system prompt template for LLM-as-a-judge evaluation metrics, model evaluation, or other prompt engineering tasks. Particularly useful for creating evaluation prompts, benchmarking criteria, and structured assessment frameworks. Example usage: "create a system prompt for evaluating code quality" or "generate an LLM-as-a-judge metric for assessing factual accuracy".',
      {
        instructions: z
          .string()
          .describe(
            'Instructions for creating the system prompt. For LLM-as-a-judge metrics, include details about evaluation criteria, scoring methodology, and expected output format.'
          ),
        system_prompt: z
          .string()
          .describe(
            'Existing system prompt to edit or refine. Provide this parameter when improving an existing evaluation prompt.'
          )
          .optional(),
      },
      async (args: any) => {
        const { instructions, system_prompt } = args;

        let template = '';
        let instructions_template = '';
        if (!system_prompt) {
          template = system_prompt_creation;
          instructions_template = instructions;
        } else {
          template = system_prompt_editing;
          instructions_template = `Instructions: ${instructions}\n\nExisting prompt: ${system_prompt}`;
        }

        const { text } = await generateText({
          model: openai('gpt-4o'),
          messages: [
            { role: 'system', content: template },
            { role: 'user', content: instructions_template },
          ],
        });

        // Add helpful context about how to use the generated prompt for LLM-as-a-judge metrics
        const usageGuidance = `
## How to Use This System Prompt for LLM-as-a-Judge Evaluation

This system prompt is designed for evaluation tasks. To use it effectively:

1. **Implementation**: Use this prompt as the system message when calling your evaluation model
2. **User Message**: Provide the content to evaluate in the user message
3. **Output Processing**: Parse the model's response according to the specified output format
4. **Consistency**: For benchmark comparisons, use the same prompt across all evaluations

For more information on LLM-as-a-judge metrics, see the [Opik documentation](https://www.comet.com/site/products/opik/).
`;

        return {
          content: [
            {
              type: 'text',
              text: text + (text.includes('# Output Format') ? '' : usageGuidance),
            },
          ],
        };
      }
    );
  }

  return server;
};
