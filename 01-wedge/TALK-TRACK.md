# Pattern 1 — The Wedge → AI Hub Gateway / Citadel

**Slide title:** *Keep your gateway. Foundry plugs in behind it — and sits above it.*

## The 60-second track
> "You built a LiteLLM gateway. Good — that's the right pattern for model access:
> one OpenAI-compatible endpoint, virtual keys, budgets, routing across providers.
> Here's the same idea on Azure API Management — my **Azure AI Gateway**. Watch:
> [run `call_gateway.py`] one call, no app change, and it's hitting a Foundry-served
> model right next to your Bedrock models.
>
> But a gateway only solves **model access**. It doesn't give you hosted agents,
> grounding, evaluation, tracing, identity or safety. That's the **factory** — and
> that's the rest of this hour. Microsoft ships this exact composition as the
> **AI Hub Gateway / Citadel landing zone**: *Any LLM, any agent, any tool — powered
> by Foundry + APIM*, with a universal AI registry, observability and governance built in."

## What it beats in a homegrown factory
- Their LiteLLM gateway stays; nothing is ripped out. Zero-risk entry point.
- Reframes the gateway as **commodity** and the factory (identity, eval, tracing,
  governance) as the **differentiation** — which their platform lacks.

## Money line
> "The gateway gives you model access. Foundry gives you the agent factory."

## Keyless / Entra ID beat (land this hard for a bank)
> "Notice there's **no API key** anywhere — not in the code, not in `.env`. The app
> authenticates with **Microsoft Entra ID** (`az login` / managed identity). The gateway
> validates the Entra token and only then forwards; APIM reaches the model with its own
> managed identity. Keyless end to end."

Optional live proof: call the endpoint with **no token** →

```
HTTP 401 — "A valid Microsoft Entra ID token is required (no subscription keys accepted)."
```

Then run `call_gateway.py` (which uses `DefaultAzureCredential`) → HTTP 200. No secret ever
touches the client. This is the governance story a regulated shop can't get from static keys.

## Assets on screen
- `call_gateway.py` — the drop-in provider call against the Azure AI Gateway (APIM),
  authenticated **keyless** with **Entra ID** (`DefaultAzureCredential`).
- Gateway endpoint `.../your-project` enforces `validate-azure-ad-token`; the AOAI
  backend is reached via APIM's managed identity.
- Reference: **aka.ms/ai-hub-gateway** and **github.com/Azure/AI-Landing-Zones** (Citadel).

## Coexistence note (for the AWS room)
Their LiteLLM gateway already fronts Bedrock. Adding Foundry is one more provider entry —
same pattern as this call. New agentic workloads land on Foundry for the factory depth;
existing Bedrock traffic is untouched.
