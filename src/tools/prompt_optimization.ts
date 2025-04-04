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
      'create-system-prompt',
      'Create a new system prompt template based on the instructions provided by the user. Can also be used to edit an existing prompt.',
      {
        instructions: z.string().describe('Instructions for the system prompt'),
        system_prompt: z.string().describe('Existing system prompt').optional(),
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

        return {
          content: [{ type: 'text', text: text }],
        };
      }
    );
  }

  return server;
};
