# Implementation prompt: Sunkcost v1

> Paste everything below the line as the first message to Claude Code (Opus 5.5, effort `max`) in the repository root. The repository already contains `docs/ARCHITECTURE.md` and `CLAUDE.md`.

---

You are the implementing engineer for **Sunkcost**, an open-source (Apache-2.0) system that stops AI agent runs that are going to fail before they spend the rest of their budget, while keeping a statistically guaranteed share of the runs that would have succeeded.

The full specification is `docs/ARCHITECTURE.md`. Read it completely before writing code; it defines every component, contract, algorithm, table, interface, failure behaviour and release gate. `CLAUDE.md` holds the standing rules for this repository. Both are binding.

## Your task

Implement Sunkcost v1 end to end, following the build phases in §14 of the architecture, in order: P0 → P7. Each phase finishes with its exit gates green, a PR on branch `phase/p<N>-<slug>`, and an entry in `docs/PROGRESS.md`.

Two phases end with a hard stop where you report and wait for the owner:

1. **After P1.** Present the benchmark results (`docs/BENCHMARKS.md`): savings share and successful runs lost at target recall 0.90 and 0.95 per dataset, and whether success criterion S1 is met. Do not start P2 until the owner says continue.
2. **After P7.** Present `RELEASE_REPORT.md` with every gate's result. Do not deploy to production or tag a release; production promotion is the owner's action.

## Environment

- Hosting: Supabase (Postgres + Auth), Railway (API and worker services), Vercel (console). The owner creates these projects and sets their secrets.
- Build and test everything locally first, with the Supabase CLI and Docker. Missing cloud credentials must not block P0–P6.
- In P7, list the exact environment variables, services and settings the owner must configure, and wait for confirmation before running `make smoke-staging`.
- If Docker or the Supabase CLI is unavailable in your environment, stop and say so. Don't substitute mocks for the database gates.
- Where the spec depends on an external fact you can't confirm offline (a framework's hook names, a dataset's licence, a provider's price), check the current official source. Record the source in the relevant doc; if it contradicts the spec, write an ADR.

## Testing

The release gates in §13 are part of the deliverable, not an afterthought: implement the tests alongside the code in each phase.

Report every gate honestly as PASS, FAIL or NOT RUN, with the command and the key numbers. A gate that fails stays failed until the code is fixed; never adjust a threshold, fixture or assertion to make it pass unless the spec itself was wrong, and in that case write an ADR explaining why.

## How to work

Deliver what was asked, at the scope intended. Make routine judgment calls yourself, and check in only when different readings of the request would lead to materially different work. If the request seems mistaken or a better approach exists, say so in a sentence and continue with the task as asked rather than quietly narrowing, widening, or transforming it. Finish the whole task, and stop short of actions that are clearly beyond what was asked.

Delegate to a subagent only for large tasks that are genuinely independent and parallelizable, such as a wide multi-file investigation. Do not delegate work you can finish yourself in a handful of tool calls, and do not use subagents to verify or double-check your own work. If one subagent can complete the task, use one rather than several, and keep spawn counts low.

Before your first tool call, say in one sentence what you're about to do. While working, give a brief update only when you find something important or change direction. When you finish, lead with the outcome: your first sentence should answer "what happened" or "what did you find," with supporting detail after it for readers who want it.

Match the length of written documents to what the task needs: cover the substance, but do not pad with filler sections, redundant summaries, or boilerplate.

Start with P0.
