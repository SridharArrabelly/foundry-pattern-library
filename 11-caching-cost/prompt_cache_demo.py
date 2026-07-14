"""
Pattern 11 — Caching & Cost. Keyless, through your gateway.

Two cost levers Foundry gives you for free that a raw model call doesn't:

  1. PROMPT CACHING — send the same long, STABLE prefix twice; the second call
     reuses the cached input tokens (billed at a discount; up to 100% off input
     on Provisioned). We print prompt_tokens_details.cached_tokens to prove the
     hit. Rules (from the docs): >= 1,024 tokens, first 1,024 identical, then a
     hit every 128 identical tokens after. prompt_cache_key sharpens routing.

  2. MODEL ROUTER — one deployment that picks the cheapest model that can still
     answer. A trivial prompt should land on a small model.

Both run through the customer's Azure AI Gateway (APIM), keyless via Entra ID —
same endpoint every app already uses. Semantic caching (equivalent, not just
identical, prompts) lives at the gateway itself (APIM azure-openai-semantic-cache
policy) and complements model-side prompt caching.

Run:  uv run python 11-caching-cost/prompt_cache_demo.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common.foundry import GATEWAY_MODEL, MODEL_ROUTER_DEPLOYMENT, gateway_client

client = gateway_client()

# A large, STABLE prefix (>1,024 tokens). Reused verbatim so the prefix hash
# matches and the cache hits. Put big static content FIRST; keep the variable
# user turn LAST.
BIG_SYSTEM = (
    "You are a private-banking RM assistant. Follow this suitability policy verbatim.\n"
    + ("Policy clause: advice must match the client's stated risk appetite. " * 120)
)


def call(user, model=GATEWAY_MODEL, cache_key="rm-suitability-policy"):
    t0 = time.perf_counter()
    r = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": BIG_SYSTEM},
            {"role": "user", "content": user},
        ],
        extra_body={"prompt_cache_key": cache_key},
    )
    ms = (time.perf_counter() - t0) * 1000
    u = r.usage
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    print(
        f"  model={r.model}  prompt={u.prompt_tokens}  cached={cached}  latency={ms:.0f}ms"
    )
    return cached


def main():
    print("Prompt caching (same stable prefix twice):")
    print("Call 1 (cold — populates the cache):")
    call("Summarise the policy in one line.")
    time.sleep(2)
    print("Call 2 (same prefix — expect cached_tokens > 0, lower latency):")
    cached = call("Now summarise it in two lines.")
    print(
        "\nPrompt cache HIT — cached input is discounted (up to 100% off on Provisioned)."
        if cached
        else "\nNo cache hit yet — the first 1,024 tokens must be identical (and >=1,024 total)."
    )

    if MODEL_ROUTER_DEPLOYMENT:
        print(f"\nModel Router ({MODEL_ROUTER_DEPLOYMENT}) — a trivial prompt should")
        print("route to the cheapest capable model:")
        call("What is 2+2?", model=MODEL_ROUTER_DEPLOYMENT)

    print(
        "\nSemantic cache (equivalent, not identical, prompts) lives at the gateway —"
        "\nAPIM azure-openai-semantic-cache-lookup/store (see 01-wedge). Two layers, one bill."
    )


if __name__ == "__main__":
    main()
