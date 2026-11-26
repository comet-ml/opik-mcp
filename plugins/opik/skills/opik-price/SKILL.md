---
name: opik-price
description: Upload an organization's own model prices to Opik's Cost center from a price sheet in any format (Excel, CSV, PDF, an email or a pasted rate card), so Opik estimates span costs at the prices the organization really pays (negotiated discounts, fine-tuned or self-hosted models) instead of public list prices. Maps the sheet's columns, converts units to dollars per 1M tokens, previews what will change (updates, new models, errors, skipped rows), saves only after the user confirms, then reads the prices back. Uses its bundled script against the Opik REST API, because the Opik MCP has no price tools yet. Use for "upload our model prices", "import this rate card into Opik", "set our negotiated price for gpt-4o", "add a price for our fine-tuned model", "Opik's costs are wrong, here are our real prices". Not for adding tracing (use instrument) or for explaining one trace's cost (use explain).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host that can run Python 3.9+ in a shell. Requires an Opik with the Cost center (custom model prices turned on) and, to preview or save, an API key of an admin of the workspace's organization. Reads the Opik URL, API key and workspace the way the Opik SDK does (`opik configure`, `~/.opik.config`, or `OPIK_URL_OVERRIDE`, `OPIK_API_KEY`, `OPIK_WORKSPACE`).
metadata:
  last_updated: "2026-11-25"
  source_commit: "4dc2a6da347dc8f312f917c5a6296721b7b0c3df"
  argument-hint: "[path to the price sheet, or paste the prices]"
---

# Price — Your Own Model Prices in Opik

**Definition of done:** the prices from the user's sheet are saved in the organization's Opik price list **after the user said yes to a preview**, every saved row read back as sent, and the user knows what changed (updated, added, unchanged, skipped, errors) and where to see it. If something blocks the save, stop at the **first** blocker and return **exactly one** next step.

Operate: **read the whole sheet, let the script convert units, preview before saving, never save without a yes.** This skill writes to Opik only and changes no application code.

## What to know first

- Opik estimates each LLM span's cost once, when the span arrives, from built-in public prices. An organization's own price replaces the built-in one for **new** spans only. Spans logged before keep their cost, and so does a span whose app sent its own cost.
- There is one price list per organization, shared by its workspaces. Every save makes a new version and says which version it started from, so a save never overwrites a colleague's edit made in between.
- A model has up to four rates: input, output, cache read, cache write, in **dollars per 1M tokens**. A rate left out keeps the built-in rate for a model Opik knows. For a model it doesn't know, a missing rate bills nothing, and cached tokens bill at the input rate.
- A price applies to spans whose model name and provider match it. Use the names spans send, or the price applies to nothing. A row also prices the same name with a date added: a `gpt-4o` row prices spans named `gpt-4o-2024-08-06`, unless a row names the dated model.
- A model Opik prices per second or per character (speech, audio, video) keeps its built-in price. Token rates don't apply to it.

## Inputs

`/opik-price <file>` (Excel, CSV, PDF, an exported email), prices pasted into the chat, or a sentence ("our gpt-4o price is $2 in, $8 out"). Optional: which workspace (default: configured), which rows to include.

## Activation — the only in-scope work

The script is `scripts/model_prices.py` in this skill's folder; run it with `python3`. It needs only the Python standard library. It prints the Opik URL and workspace it uses on every run, and never the API key. Exit codes: 0 done, 1 failed (the message says whether to try again), 2 invalid rows, 3 someone saved in between, 4 not allowed, 5 prices turned off or Opik too old.

### 1. Check the connection
`python3 <skill>/scripts/model_prices.py show` — the Opik and workspace, the price list version and the rows saved now. Confirm it is the Opik and workspace the user means. A non-zero exit is a blocker.

### 2. Read the sheet
Read every table: every tab of a workbook, every page of a PDF. `references/reading-price-sheets.md` says how to read each format, which column names mean which rate, how to tell the unit, and what Opik can't price. Before writing any row, list what won't upload and why (batch prices, long-context tiers, per-image or per-second prices, another currency).

### 3. Use the names spans send
Sheets name models for people ("GPT-4o (2024-08-06)"); spans name them as the app's SDK sends them (`gpt-4o-2024-08-06` on `openai`). In Opik, open a trace and select an LLM span: its model and provider are the names to use. With the Opik MCP connected, listing a project's spans with `filters='type = "llm"'` shows them too. Ask the user when you can't tell.

`python3 <skill>/scripts/model_prices.py check <model> [<model> …] --provider <provider>` says how spans with each name are priced now: `your row …` (which saved row, and whether it matched by the name without its date), `built-in price`, or `NO PRICE` (those spans cost $0 now). Leave out `--provider` for spans that send none.
- Check each name you mapped. `NO PRICE` for a well-known model usually means the name or the provider is wrong; fix it before writing the row.
- When the sheet lists a dated model and the spans send the plain name (or the reverse), use the name the spans send.
- For a model only this organization uses (a fine-tune, a self-hosted model, a gateway name), `NO PRICE` is expected. The provider is what its spans send, or empty to match any provider.

### 4. Write the rows file
Write it in a temporary folder, never in the user's repository:
```json
{
  "unit": "per_1m",
  "rows": [
    {"ref": "Prices!A4", "model": "gpt-4o", "provider": "openai", "input": 2, "output": 8, "cache_read": 1, "note": "Contract 2026, page 3"},
    {"ref": "Prices!A7", "model": "acme-chat-large", "provider": "acme", "input": "0.005", "output": "0.015", "unit": "per_1k"},
    {"ref": "Prices!A9", "model": "gateway-4o", "provider": "bifrost", "same_as": {"model": "gpt-4o", "provider": "openai"}},
    {"ref": "Prices!A12", "skip": "batch price; Opik prices regular calls only"}
  ]
}
```
- Copy each price exactly as the sheet writes it and set `unit` to `per_1m`, `per_1k` or `per_token`, for the file or per row. The script converts with exact decimals; don't convert in your head.
- Bring every rate the sheet has (input, output, cache read, cache write), not only input and output. When the user asks to include custom price fields, this is what they mean, with the note.
- Leave a rate out when the sheet has none. Never write 0 for a missing cache rate: Opik refuses a cache rate of 0, and leaving it out keeps the built-in one.
- `same_as` prices a model exactly like another one (a gateway's name for a known model), with no prices of its own.
- `ref` says where the row is in the sheet (tab and cell, page and line). `note`, up to 500 characters, says where the price comes from.
- Every sheet row that won't upload gets a `skip` row with the reason, so nothing disappears silently.
- Rows the file doesn't mention keep their saved prices, and a rate a row leaves out keeps its saved value.

### 5. Preview, then ask
`python3 <skill>/scripts/model_prices.py preview <rows file>` saves nothing: Opik checks the rows with the same checks as the Cost center page, as a dry run. Per sheet row it prints `UPDATE` (before → after), `NEW` (a custom model, or one that replaces the built-in price, with built-in → new), `SAME`, `ERROR` or `SKIPPED`, then warnings, totals and the exact save command with `--expected-version`.
- **Errors:** fix the row, or mark it `skip` with the reason, and preview again. Nothing saves while one remains.
- **`price_out_of_range`** (over 10× or under a tenth of the built-in price) almost always means a wrong unit. Check the unit before going on.
- **`priced_per_unit`:** Opik prices this model per second or per character, so the row would change nothing. Mark it `skip` with that reason.
- **`name_normalized`:** a note, not a problem. For a model, it says which span names the row will match: with a prefix such as `azure/`, only the part after the first `/` counts. Check that this is the name spans send, and leave the file as it is. For a provider, Opik saves its own spelling (`google_ai` as `gemini`).
- **`shadows_builtin`:** a `same_as` row replaces the built-in price of a model Opik knows. Say so in the preview.
- **`provider_unpriced` and `no_base`** are expected for a custom model. Say once that only these rates will price it.

Show the user the preview: each change with its sheet reference, totals, skipped rows with reasons, warnings that matter. Ask for an explicit yes to save. Anything short of a yes is a no.

### 6. Save
Only after the yes, run the save command the preview printed, with the rows file unchanged since that preview. It saves only if the list is still at the previewed version, then reads every row back and prints what new spans of each model will be charged per rate, and whether each rate is the organization's or the built-in one.
- Exit 3: someone saved in between and nothing was saved. Preview again, show what changed, and ask again.

### 7. Report
Version saved; counts (updated, added, unchanged, skipped, errors); the rows that changed, before → after; skipped rows with reasons; the Cost center link the script printed. Say that the prices apply to new spans only, and that the Cost center page shows them and their history.

## Blockers

Stop at the **earliest** and return **exactly one** next step:
- No connection, or exit 1 on `show` → "Run `opik configure` (or set `OPIK_URL_OVERRIDE`, `OPIK_API_KEY` and `OPIK_WORKSPACE`), then rerun `/opik-price`."
- Exit 1 that says to try again, twice in a row → "Opik couldn't answer just now. Rerun `/opik-price` in a few minutes."
- Exit 5 → "Custom model prices are off on this Opik, or it is too old. Ask your Opik admin to turn them on, then rerun."
- Exit 4 → "Only an admin of the workspace's organization can preview and save prices. Ask one to run this, or to make you an admin."
- The prices aren't in US dollars → "Opik stores prices in dollars. Give me the exchange rate to use, or a price sheet in dollars."
- A discount with no prices ("20% off list") → "Opik needs a price per model. List the models and their prices, or the prices after the discount."
- No prices found → "I found no model prices in <file>. Which table or page has them?"

## Output

**User-facing:** a short message — the saved version and counts, a compact table of what changed, skipped rows with reasons, the Cost center link, and that only new spans use the prices. Not JSON.

**Underneath**, one shape:
- `status`: `saved` | `nothing_to_save` | `cancelled` | `blocked`
- `version`: the saved list version
- `counts`: `updated`, `added`, `unchanged`, `skipped`, `errors`
- `rows_file`: where the rows file is
- `source`: `script` (the Opik REST API; the MCP has no price tools yet)
- `next_step`: exactly one, when `blocked`

Invariants: `saved` means the user said yes to the preview of that exact version and every row read back as sent; every sheet row is counted once; the API key appears nowhere.

## Anti-patterns
Saving without a preview and a yes; editing the rows file after the preview and saving anyway; converting units by hand; writing 0 for a rate the sheet doesn't have; guessing a model name or provider the spans don't send; dropping a sheet row without a `skip` reason; saving again after exit 3 without a new preview; printing the API key; **editing application code**.

## References

`references/reading-price-sheets.md` — reading Excel, CSV, PDF and email tables; the column names vendors use for each rate; units; Anthropic cache writes; what Opik can't price and how to skip it.
