"""
Pattern 1 — The Wedge (AI Hub Gateway / Citadel).

Your team built a LiteLLM gateway. Whether it's LiteLLM or Azure API Management's
AI Gateway, the gateway is the *model-access* layer. Foundry doesn't replace it —
Foundry becomes just another provider *behind* it, and (more importantly) the
*factory* above it. This script proves the "drop-in provider" half: one
OpenAI-shaped call to your Azure AI Gateway, hitting the Foundry-served model.

Run:  uv run python 01-wedge/call_gateway.py
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common.foundry import gateway_client, GATEWAY_MODEL, GATEWAY_ENDPOINT


def main():
    client = gateway_client()
    print(f"Calling the Azure AI Gateway at {GATEWAY_ENDPOINT}  (model: {GATEWAY_MODEL})\n")

    resp = client.chat.completions.create(
        model=GATEWAY_MODEL,
        messages=[
            {"role": "system", "content": "You are a private-banking assistant. Be concise."},
            {"role": "user", "content": "In one sentence: what is a suitability assessment?"},
        ],
    )
    print("assistant:", resp.choices[0].message.content)
    print()
    print("TALK TRACK: your apps never changed — same OpenAI-compatible endpoint, same")
    print("virtual keys and budgets on the gateway. Foundry is now just another provider")
    print("behind it, next to Bedrock. The gateway is the commodity; the factory is next.")


if __name__ == "__main__":
    main()
