# Pattern 2 — Agent Service

**Group:** Agent factory  ·  **Runs 3rd of 13** in the run order

**Slide title:** *A managed agent runtime — threads, tools, memory and identity, server-side.*

## The 60-second track
> "Your homegrown factory has to build the boring-but-hard parts: conversation state,
> tool orchestration, retries, a vector store for RAG, and an identity for the agent.
> Foundry's **Agent Service** ships all of that as a managed primitive — Build
> 2026 calls it *'the primitive for agents the way containers were for cloud-native
> apps'*: per-session sandboxes, persistent memory, elastic scale.
>
> And there are **two ways to run an agent on Foundry** — I'll show both."

## Two hosting models (say this out loud)
| | **A. Prompt-based agent** (`create_prompt_agent.py`) | **B. Hosted agent** (`hosted/`) |
|---|---|---|
| You provide | model + instructions + tools (config) | your **code / container** (any framework) |
| Runtime | Foundry-managed assistant | your process on Foundry-managed compute |
| State | managed threads + managed vector store | you own it (Agent Framework here) |
| Tools | File Search (RAG) + function tools | your Python `@tool` functions |
| Identity | Entra Agent ID | **dedicated** Entra Agent ID + endpoint |
| Reach for it when | fast, declarative, File Search RAG | custom orchestration / framework / protocol |

> "Same business scenario — a Private Banking RM assistant citing our suitability policy
> and flagging client **C-1290** — shipped two ways. Both are first-class, governed
> Foundry agents with an Entra Agent ID. The point: **you didn't build the runtime, the
> hosting plane, or the identity system. Foundry did.**"

## Demo A — prompt-based agent (declarative, ~60s)
1. `uv run python 02-agent-service/create_prompt_agent.py`
2. Show the compound answer (File Search cites the policy + the function tool flags C-1290).
3. Portal: **Agents → rm-assistant-prompt** — a *versioned* agent (v1, v2… on re-run) with
   its **Identity** (Entra Agent ID). Built on the new unified SDK
   (`AIProjectClient.agents.create_version(PromptAgentDefinition(...))`), invoked via the
   **Responses** API — not the legacy `asst_...` assistants surface.

## Demo B — real hosted agent (your container, ~90s)
Your Agent Framework code, served on the **Responses** protocol, run *by* Foundry.
**Already deployed** as `rm-assistant-hosted` (v1, active) — show it two ways:
1. **Local** (the code): `cd 02-agent-service/hosted; azd ai agent run` → host on `:8088`
   (this **blocks — it's a server**), then in a **second terminal**
   `azd ai agent invoke --local "Is client C-1290 compliant per the suitability policy?"`.
   - No azd ext? `uv run --with agent-framework-foundry --with agent-framework-foundry-hosting --with python-dotenv python src/rm-assistant/main.py`, then POST to `http://localhost:8088/responses` from a 2nd terminal.
2. **On Foundry** (the point of "hosted"): open the **Agents → rm-assistant-hosted**
   playground in the portal and ask the same question live — or from the terminal
   `azd ai agent invoke rm-assistant-hosted "Is client C-1290 compliant per the suitability policy?"`.
3. Show **Identity** (its **own** dedicated Entra Agent ID), **Logs** (live container logs),
   and the **Endpoint**. Re-running `azd deploy` ships a new **version**.

> Verified end-to-end locally: keyless via `DefaultAzureCredential`, the model calls
> **both** tools (`get_suitability_policy` + `get_client_holdings`) and answers
> *"No — C-1290 is not compliant; a Conservative client must not hold >70% equities."*

## Keep it on your gateway (BYOM)
> "One thing a platform team always asks here: *if Foundry runs the agent, does my gateway
> still see the traffic?* Yes — with **BYOM**. Foundry calls the model server-side, so the
> URL my client used is irrelevant to that call. Instead APIM becomes the model's **declared
> backend**: I create a gateway connection on the project and the agent references its model
> as `<connection>/<model>`. Same agent, same tools — and every inference lands on your
> gateway, with your policies and your metrics."

Set `AGENT_MODEL_CONNECTION` in `.env` to the connection name; `agent_model()` in
`common/foundry.py` qualifies the model id. Leave it blank to keep agents on the direct
Foundry route. Full setup in [`docs/coexistence.md`](../docs/coexistence.md).

## What it beats in a homegrown factory
- **Managed threads / memory** — no bespoke state store.
- **Server-side tool orchestration** + auto function calling.
- **Managed vector store** for RAG (File Search) — no separate vector DB to run.
- **Bring-your-own-code hosting** — any framework, containerized, with managed compute
  that deprovisions when idle (no always-on cost).
- **Entra Agent ID** — governable identity per agent, not a shared cloud IAM role.

## Money line
> "You didn't build a runtime, a vector store, a hosting plane, and an identity system.
> You called an API — or handed us a container."

## Where it pulls ahead
Most managed agent runtimes cover the declarative assistant model. Foundry adds
**bring-your-own-code hosted agents, native MCP tools, a managed vector store + enterprise
search, and a dedicated Entra Agent ID** (next patterns).
