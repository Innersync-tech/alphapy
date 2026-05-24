# Plan: Alphapy + Hermit Integration via Core API

## Goal
Connect Alphapy (fast executor) with Hermit (strategic layer) through the Core API for better long-term alignment, telemetry, and extensibility.

## Background
- Hermit runs on VPS and acts as the deep-thinking, long-term strategic agent.
- Alphapy runs on Railway and is the fast executor.
- Integration should go through Core (not direct Discord) for clean architecture.

## Proposed Changes

### 1. Core API Endpoints (to be defined/implemented)
- `GET /api/context/hermit` or `/memory/strategic`
- Returns latest strategic summaries from Hermit
- Should support basic authentication / Innersync Identity layer

### 2. Alphapy-side Changes
- Add a lightweight context fetcher that calls the Core API endpoint
- Cache the result for a short period (e.g. 15-60 minutes)
- Inject the Hermit context into the system prompt when available

### 3. Prompt Engineering
Add a section in Alphapy’s system prompt:

```
You are Alphapy, the fast executor in the Innersync ecosystem.
You have access to strategic context and long-term reflections from Hermit via Core.
When relevant, incorporate Hermit’s input for better alignment with Bryan’s long-term goals.
Latest Hermit context (if any):
{hermit_context}
```

### 4. Nice-to-haves
- Command to manually refresh Hermit context (`/refresh-hermit` or similar)
- Logging of context fetches for telemetry
- Fallback to empty context if Core is unreachable

## Implementation Order (Recommended)
1. Define and expose the Core API endpoint(s)
2. Implement context fetching + caching in Alphapy
3. Add prompt injection logic
4. Test with first real strategic summary from Hermit
5. Add telemetry/logging

## Status
- Hermit side: Ready (skills already created)
- Alphapy side: This plan
- Core side: Needs endpoint definition

## Owner
Bryan / Innersync team

---
Created by Hermit – 2026-05-24