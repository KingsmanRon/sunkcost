# Benchmark datasets: provenance and licences

All data is downloaded by `bench/run.py` from the pinned sources below into `bench/.cache/`
(gitignored). Nothing from these datasets is committed or redistributed: the repository holds only
aggregate results (`bench/results/`). Each trajectory is converted on the fly to the fields the SDK
would send: step kind, model name, tool name, an HMAC of the tool arguments, token counts, cost,
error flag and timestamp. No prompt, completion, argument or tool-output text is kept.

The architecture names two candidate sources: the SWE-bench `experiments` trajectories, and the
public datasets listed in arXiv 2608.23670 ("Automata from Agent Traces: Failure and Next-Step
Prediction", Cho et al., 24 Aug 2026). The paper and its mirrors (arxiv.org, huggingface.co,
paperswithcode.co, the authors' site) are blocked by this build environment's egress policy, so its
dataset list could not be read. Web search confirmed only that it uses twelve public datasets across
coding, web, desktop, mobile, customer service, safety and multi-agent settings. Every dataset below
was chosen because its data and licence could be verified from here, and each has per-run success
labels.

| Key | Source (pinned) | Licence | Labels | Cost |
|---|---|---|---|---|
| `tau2-retail`, `tau2-telecom`, `tau2-airline` | [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) @ `b7ea9074`, `data/tau2/results/final/` | MIT ([LICENSE](https://github.com/sierra-research/tau2-bench/blob/b7ea9074c1cba482b30687fecdb5c8425fd6f619/LICENSE)) | `reward_info.reward` 1.0 / 0.0 | recorded USD per assistant call |
| `tau-bench-retail` | [sierra-research/tau-bench](https://github.com/sierra-research/tau-bench) @ `59a200c6`, `historical_trajectories/` | MIT ([LICENSE](https://github.com/sierra-research/tau-bench/blob/59a200c6d575d595120f1cb70fea53cef0632f6b/LICENSE)) | `reward` 1.0 / 0.0 | none recorded: step count |
| `swe-bench-verified-mini` | Labels: [SWE-bench/experiments](https://github.com/SWE-bench/experiments) @ `40f164d5`, `evaluation/verified/<entry>/per_instance_details.json`. Trajectories: `s3://swe-bench-submissions/bash-only/<entry>/trajs/` (public, as named in each entry's `metadata.yaml`) | **No licence stated** (see below) | `resolved` | recorded USD per model call |

Retrieved and checked on 2026-09-23. Files from the two Sierra repositories are verified against
their git blob ids at the pinned commits (`BLOBS` in the loaders), so a truncated download is caught.

## τ²-bench (`tau2-*`)

Results files for four agent models on each domain, 4 trials per task, with a simulated user
(GPT-4.1): Claude 3.7 Sonnet, GPT-4.1, GPT-4.1-mini (the `base` mode files) and o4-mini.
Each domain is one workload pooling the four agent models (one batch each).

- **Steps.** Each assistant message with a model call becomes a `model` step with its `usage`
  tokens and `cost`. The canned opening greeting has no usage and no cost, so it is not a step.
  Each tool result requested by the assistant becomes a `tool` step: its name and arguments come
  from the matching tool call, and its `error` flag is copied. Simulated-user turns, and the tool
  calls the user makes in the telecom domain, are not agent steps.
- **Timestamps** are the recorded message timestamps (naive ISO-8601, treated as UTC).
- **Size.** Retail: 1,824 runs (1,324 successes). Telecom: 1,824 (773). Airline: 800 (431). Airline
  is below the calibration minimum (100 successes in each of the calibration and test splits) and
  is reported as insufficient data.

## τ-bench (`tau-bench-retail`)

`historical_trajectories/gpt-4o-retail.json` (460 runs) and `sonnet-35-new-retail.json` (920 runs:
Claude 3.5 Sonnet, October 2024): 1,380 runs, 915 successes.

- **Steps.** Each assistant message is a `model` step and each tool result a `tool` step. A result
  starting with `Error` sets the error flag.
- **No tokens, costs or timestamps** are recorded. Following ARCHITECTURE.md §4 C10, step count is
  the cost proxy: every step costs one unit, so savings are shares of steps. With no timing, every
  step is stamped at the run start, and runs are ordered trial-major, task-minor within each batch.

## SWE-bench Verified, mini-SWE-agent 2.0.0 (`swe-bench-verified-mini`)

The eleven "bash-only" entries dated 2026-02-17 that publish per-instance labels, run by the
SWE-bench maintainers with mini-SWE-agent 2.0.0: Claude 4.5 Haiku, 4.5 Opus, 4.5 Sonnet and 4.6 Opus,
DeepSeek 3.2, Gemini 3 Flash, GLM-5, GPT-5.2, GPT-5-mini, Kimi K2.5, MiniMax 2.5. That is 500
instances each.

- **Excluded.** `20260219_mini-v2.0.0_gpt-5-2-codex` has no `per_instance_details.json`.
  `20260226_mini-v2.0.0_gemini-3-pro-high` records 0 resolved instances, which looks like an
  incomplete entry. Two Gemini 3 Flash runs (`django__django-15731`, `django__django-15814`) ended
  with `ServiceUnavailableError` before the first model response. With no agent steps, they are not
  runs the SDK would record. That leaves 5,498 of 5,500 runs.
- **Steps.** Both trajectory formats are handled: chat-completions messages and Responses-API
  items. Each model call becomes a `model` step with its recorded `extra.cost`, prompt and
  completion tokens, and timestamp. Each bash result becomes a `tool` step named after the tool
  (`bash`), with an HMAC of the command arguments. The error flag is set by a non-zero `returncode`
  or an exception. The run starts at the first model call's `created` time.
- **Licence.** SWE-bench/experiments has no licence file. The maintainers publish the entries and
  artifacts "to enable greater reproducibility and transparency" (repository README). The
  benchmark code (SWE-bench) and the agent (mini-SWE-agent) are MIT-licensed, but that does not by
  itself licence the trajectories. This benchmark downloads them from their public location at run
  time, keeps only derived counts and hashes locally, and publishes only aggregate metrics.
  **Owner decision needed:** confirm this use, or drop the dataset (S1 is also evaluated without it).

## Conversion constants

- Argument HMAC salt: `sunkcost-bench-v1`. It is fixed so runs hash consistently, and it is not a
  secret.
- Money: recorded floats are converted with `Decimal(repr(x))` and rounded to 9 places
  (`numeric(20,9)`).
- Arrival order for the time split: see "Method and limitations" in `docs/BENCHMARKS.md`.
