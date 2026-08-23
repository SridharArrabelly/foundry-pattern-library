# Pattern 10 — Memory (short-term + long-term)

**Group:** Agent factory  ·  **Runs 7th of 13** in the run order

**Slide title:** *Memory is a platform primitive — not a database you build.*

## In brief
> "The usual approach bolts a state store and a vector DB onto an agent to make it
> *remember*. Foundry gives you two kinds of memory as a primitive. **Short-term** is
> a Conversation — inside one session the agent recalls what the client just said.
> [run it] Watch the second answer honour 'max 20% equities, no crypto' with no
> re-stating. **Long-term** is a per-user **Memory Store** — Foundry extracts the
> durable facts [show the 4 extracted memories] and a **brand-new conversation**,
> days later, recalls them. You didn't build a state store or a vector DB — you set
> a scope and a TTL. And because it's scoped per user, *forget* is one call —
> right-to-be-forgotten for a bank."

## What Foundry gives you here
- **Managed memory** — extraction, consolidation, retrieval, TTL are Foundry's job.
- **Per-user scope** — isolation + GDPR *forget* (`delete_scope`) out of the box.
- **New API, no glue** — `beta.memory_stores` + `MemorySearchPreviewTool` + the
  Responses/Conversations API. No thread-juggling, no bespoke embeddings pipeline.

## The one-liner
> "Short-term is the session. Long-term is the store. You configured a scope and a
> TTL — you didn't build a memory system."

## Running it
1. `uv run python 10-memory/memory_agent.py`.
2. **Short-term:** the follow-up in the same conversation already respects the
   stated preference — no context re-passed.
3. **Long-term:** the script force-extracts memories (prints them), then a **fresh
   conversation** answers a rebalance question honouring '20% cap / no crypto'.
4. Open Foundry → project → **Agents → rm-assistant-memory** and the per-user
   **Memory** store to show the extracted facts in the portal.
5. (Optional) uncomment `delete_scope` to prove *forget*.

## Why this one stays off the gateway
If someone asks why Pattern 10 isn't on the BYOM route like Patterns 2 and 6: `memory_search`
is **not supported with a BYO model** — Foundry rejects it outright with *"The following tools
are not supported with BYO model: memory_search"*. File Search and function tools do work over
BYOM; this one doesn't. The memory store's own extraction and embedding calls also resolve
against deployments directly. See [`docs/coexistence.md`](../docs/coexistence.md).

## Grounding
Uses the official API from *Create and use memory in Foundry Agent Service*:
`project.beta.memory_stores.create(MemoryStoreDefaultDefinition(...))`,
`MemorySearchPreviewTool(memory_store_name, scope)`, and runs via
`openai_client.responses.create(..., conversation=...)`. Long-term memory needs a
chat model (`gpt-5.4-mini`) **and** an embedding deployment
(`text-embedding-3-small`). Keyless via `DefaultAzureCredential`. Preview surface —
verify method names against the current quickstart before demoing.

## Golden expected output
```
memory store created: rm-client-memory
agent: rm-assistant-memory v1

== SHORT-TERM (same session) ==
assistant: No — a 60% equity fund is not a fit for your stated profile.
It's well above your max 20% equity limit ... (caps equities at 20%)

== LONG-TERM (new session, days later) ==
extracted 4 memories:
  - created: ... conservative, maximum 20% allocation to equities, and dislikes crypto ...
  - updated: ... conservative risk appetite ...
  - updated: ... equity exposure capped at 20% or less ...
  - updated: ... dislikes crypto / no crypto exposure ...

assistant (new session): Given your conservative profile, I'd keep equities capped
at 20% and no crypto ... (rebalance honours the remembered preference)
```
> The point: the **new session** answer respects "20% cap / no crypto" though it was
> never re-told — recall came from the long-term store. (Store name says "reused" on
> re-runs — that's fine.)

