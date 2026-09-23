# prices.v1 sources

Every entry in `prices.v1.json` carries its own `source_url` and `retrieved_at`. This file records how
the table was assembled and what it does not cover yet.

## Anthropic (18 models), retrieved 2026-09-23

- Prices: <https://platform.claude.com/docs/en/about-claude/pricing>, "Model pricing" table, base input
  and output columns. The page states that Claude 4.6 and later models bill the full 1M context
  window at standard rates, so no long-context tiers are recorded. Earlier models that are still
  listed carry no long-context pricing on that page either.
- Model IDs: the API model names on <https://platform.claude.com/docs/en/about-claude/model-deprecations>
  ("Model status" table), and the aliases on <https://platform.claude.com/docs/en/models/overview>
  and <https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions> (dated
  pre-4.6 snapshots have a dateless alias; 4.6 and later IDs are dateless snapshots).
- Not modelled: prompt-caching reads and writes, the Batch API discount, the 1.1x `inference_geo: "us"`
  multiplier, fast mode, and partner-platform (Bedrock, Google Cloud) prices. Bedrock-style
  `anthropic.*` IDs are deliberately not aliased because Bedrock prices independently.

## Not yet covered

| Provider | Official source | Status |
|---|---|---|
| OpenAI | `openai.com/api/pricing`, `platform.openai.com/docs/pricing` | Not added: both hosts are blocked by the build environment's egress policy (2026-09-23) |
| Google (Gemini) | `ai.google.dev/gemini-api/docs/pricing`; Google Cloud Agent Platform pricing | Not added: prices are readable on `cloud.google.com`, but the pages that give the API model ID strings (`ai.google.dev`, `docs.cloud.google.com`) are blocked, so IDs could not be confirmed |

Until then, steps on these models get `cost_usd: null` unless the caller passes `cost_usd`.
