# Reading price sheets

How to get model prices out of a sheet and into the rows file. The script checks numbers and units; this page is about finding the right numbers.

## By format

- **Excel (.xlsx, .xls).** Read every sheet, hidden ones included, with Python and the `openpyxl` package, or `pandas` (an `.xls` file needs `xlrd`). Use computed values, not formulas (`load_workbook(path, data_only=True)`). The header row is often not row 1, and merged cells repeat a model or provider over several rows. If none of these packages is installed and the user doesn't want one added, ask them to export each sheet as CSV.
- **CSV.** Check the delimiter (`,` `;` or tab) and the decimal mark. `0,25` in a `;`-separated file is 0.25; in a `,`-separated file it is two columns.
- **PDF.** Read every page. Tables split across pages repeat or drop their header. Footnotes often hold the unit (`* per 1K tokens`) or the currency.
- **Email or pasted text.** Prices can be in a table, a list or a sentence ("gpt-4o: $2 in / $8 out per million"). Each model with a price is one row.
- **Screenshot.** Read digits carefully and ask about any you can't read for sure. A wrong digit is a wrong bill.

## Which column is which rate

| Rate | Names vendors use |
|---|---|
| `input` | Input, Prompt, Input tokens, Prompt tokens, Uncached input, $/MTok in |
| `output` | Output, Completion, Generated, Output tokens, Response, $/MTok out |
| `cache_read` | Cached input, Cache hits, Cache read, Prompt caching read, Cached tokens |
| `cache_write` | Cache write, Cache creation, Prompt caching write, 5m cache writes |

- **Anthropic** lists two cache writes: 5 minutes (1.25× input) and 1 hour (2× input). Opik has one cache write rate. Use the 5-minute one unless the user says they cache for an hour, and say which one you used.
- **Reasoning or thinking tokens** bill as output. They need no row of their own.
- A sheet with one price per model (embeddings, "blended") gives `input` only.

## Model and provider

- The model name must match what spans send. Take it from an LLM span in Opik's traces, and confirm it with the script's `check`. Sheets add dates, regions or marketing names ("GPT-4o (Aug '24)", "Claude Sonnet 4.5 — us-east-1").
- The provider is the one spans send, such as `openai`, `azure`, `anthropic`, `bedrock`, `gemini`, `mistral`, `groq` or `together_ai`. Opik saves a few other spellings under its own name (`google_ai` as `gemini`), and the preview says so (`name_normalized`). A price sheet from Azure OpenAI prices `azure`, not `openai`: the same model on two providers is two rows.
- For a model only this organization uses, use the provider its spans send, or leave it empty to match spans from any provider.

## Units

| The sheet says | `unit` |
|---|---|
| per 1M tokens, per million, /MTok, /1M | `per_1m` |
| per 1K tokens, per thousand, /1K | `per_1k` |
| per token | `per_token` |

When the sheet doesn't say, a price for a well-known model gives it away: gpt-4o input near 2.5 is per 1M; near 0.0025 is per 1K. The preview warns when a price is over 10× or under a tenth of the built-in one. If the unit still isn't clear, ask.

## Currency

Opik stores US dollars. `$` alone usually means dollars, but check the document: some vendors use `$` for other dollars. Another currency is a blocker until the user gives an exchange rate; then write the converted price and put the rate and date in `note`.

## What Opik can't price (mark the row `skip` with the reason)

- **Batch, flex or async prices.** Opik prices a span at one rate per token type. Upload the standard price and skip the batch one.
- **Long-context tiers** ("over 200K tokens"). Upload the base tier, skip the higher tier, and say that long requests will be under-estimated.
- **Per-image, per-second (audio, video), per-character (speech), per-request or per-search prices.**
- **Provisioned or reserved capacity** (monthly or hourly commitments, PTUs) and **fine-tuning training** prices.
- **A discount with no prices** ("20% off list"). Ask for the prices per model.
