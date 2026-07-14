# Pattern 11 — Caching & Cost

**Slide title:** *Two cache layers and a router — cheaper without touching your app.*

## The 60-second track
> "Cost isn't a slide, it's a primitive. **Prompt caching** is on by default: send
> the same long, stable prefix — a policy, a system prompt, tool defs — and the
> second call reuses the cached input. [run it] Cold call: `cached=0`. Same prefix:
> `cached=1,280` and ~4× faster — billed at a discount, up to **100% off input on
> Provisioned**. Then **Model Router**: one deployment that picks the cheapest model
> that can still answer — a trivial prompt downshifts automatically. And this all
> runs **through your gateway**, keyless — the semantic cache at APIM catches
> *equivalent* (not just identical) prompts. Two cache layers and a router, one
> bill — and your app code didn't change."

## What it beats in a homegrown factory
- **Prompt caching** — automatic; `prompt_tokens_details.cached_tokens` proves it.
- **Model Router** — cost-aware model choice as a deployment, not hand-rolled logic.
- **Semantic cache at the gateway** — dedupes paraphrases before they hit a model.

## Money line
> "The second identical prefix is nearly free, and the router won't pay for a big
> model when a small one passes. FinOps you get by default, not by building it."

## Demo steps
1. `uv run python 11-caching-cost/prompt_cache_demo.py`.
2. Call 1 `cached=0` (cold) → Call 2 `cached=1,280` and much lower latency = **HIT**.
3. Model Router routes the trivial prompt to a cheaper model (often cross-provider).
4. Point at `01-wedge` APIM policy for the **semantic** cache layer (paraphrases).

## Grounding
From the official *Prompt caching* docs: needs ≥ 1,024 tokens with the first 1,024
identical, then a hit every 128 identical tokens; hits surface as
`usage.prompt_tokens_details.cached_tokens`; `prompt_cache_key` sharpens routing.
Runs keyless through the gateway (`gateway_client()`), `model-router` deployment for
the router leg. Prompt caching is stable (not preview).

## Golden expected output
```
Prompt caching (same stable prefix twice):
Call 1 (cold — populates the cache):
  model=gpt-5.4-mini-2026-03-17  prompt=1476  cached=0     latency=6913ms
Call 2 (same prefix — expect cached_tokens > 0, lower latency):
  model=gpt-5.4-mini-2026-03-17  prompt=1475  cached=1280  latency=1495ms

Prompt cache HIT — cached input is discounted (up to 100% off on Provisioned).

Model Router (model-router) — a trivial prompt should route to the cheapest capable model:
  model=grok-4-1-fast-reasoning  prompt=1463  cached=1618  latency=1756ms
```
> The two numbers that sell it: **cached 0 → 1,280** and latency **~6.9s → ~1.5s** on
> the identical prefix. The router leg often lands on a small cross-provider model.

