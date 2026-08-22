"""
Pattern 10 — Memory: short-term (session) + long-term (persistent store).

Two kinds of memory, one agent, both keyless (Entra ID):

  * SHORT-TERM  = a Conversation. Context lives inside ONE session; the agent
    recalls what you said a moment ago in the SAME conversation — no re-stating.
  * LONG-TERM   = a Memory Store (per-user scope + TTL). Foundry extracts durable
    facts (preferences, profile) and recalls them in a BRAND-NEW conversation,
    across sessions/days. You didn't build a state store or a vector DB.

Grounded in the official API ("Create and use memory in Foundry Agent Service"):
  project.beta.memory_stores.create(...) + MemorySearchPreviewTool + the
  Responses/Conversations API. Long-term memory needs a chat model AND an
  embedding deployment (text-embedding-3-small).

Run:  uv run python 10-memory/memory_agent.py
Then: Foundry portal -> project -> Agents -> "rm-assistant-memory", and the
      per-user Memory store, to see the extracted facts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common.foundry import (
    EMBEDDING_DEPLOYMENT_NAME,
    MODEL_DEPLOYMENT_NAME,
    project_client,
)

from azure.ai.projects.models import (
    MemorySearchPreviewTool,
    MemoryStoreDefaultDefinition,
    MemoryStoreDefaultOptions,
    PromptAgentDefinition,
)

STORE = "rm-client-memory"
USER = "client-0007"  # scope key — isolates memory per end user
AGENT = "rm-assistant-memory"
PREFERENCE = "I'm conservative — max 20% equities, and I dislike crypto."


def reply(oai, agent, conversation_id, text):
    """One agent turn on a conversation, via the Responses API."""
    r = oai.responses.create(
        input=text,
        conversation=conversation_id,
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
    )
    return r.output_text


def main():
    pc = project_client()
    with pc:
        oai = pc.get_openai_client()

        # 1) LONG-TERM store: per-user scope, 30-day TTL. Foundry manages
        #    extraction / consolidation / retrieval — no vector DB of your own.
        # NOTE: these two stay on DIRECT deployment names on purpose. The memory
        # store's extraction/embedding calls are Foundry's own machinery, not your
        # agent's inference, and the API resolves them against deployments only —
        # a gateway-qualified name fails with "Chat model deployment ... not found".
        definition = MemoryStoreDefaultDefinition(
            chat_model=MODEL_DEPLOYMENT_NAME,
            embedding_model=EMBEDDING_DEPLOYMENT_NAME,
            options=MemoryStoreDefaultOptions(
                chat_summary_enabled=True,
                user_profile_enabled=True,
                default_ttl_seconds=30 * 24 * 60 * 60,
                user_profile_details=(
                    "Private-banking client preferences: risk appetite, asset "
                    "likes/dislikes. Avoid sensitive PII."
                ),
            ),
        )
        try:
            store = pc.beta.memory_stores.create(
                name=STORE,
                definition=definition,
                description="RM client memory (per-user, 30-day TTL)",
            )
            print("memory store created:", store.name)
        except Exception:
            store = pc.beta.memory_stores.get(STORE)
            print("memory store reused:", store.name)

        # 2) Agent wired to the store via the memory-search tool (scoped per user).
        tool = MemorySearchPreviewTool(
            memory_store_name=STORE, scope=USER, update_delay=1
        )
        # NOTE: this agent stays on the DIRECT deployment, unlike Patterns 2 and 6.
        # memory_search is not supported with a BYO (gateway) model — Foundry rejects
        # it outright: "The following tools are not supported with BYO model:
        # memory_search". File Search and function tools DO work over BYOM; this one
        # doesn't. See docs/coexistence.md.
        agent = pc.agents.create_version(
            agent_name=AGENT,
            definition=PromptAgentDefinition(
                model=MODEL_DEPLOYMENT_NAME,
                instructions=(
                    "You are a private-banking RM assistant. Personalise advice using "
                    "remembered client preferences. Be concise."
                ),
                tools=[tool],
            ),
        )
        print(f"agent: {agent.name} v{agent.version}\n")

        # ---- SHORT-TERM (same session): one conversation remembers itself ----
        print("== SHORT-TERM (same session) ==")
        conv = oai.conversations.create()
        reply(oai, agent, conv.id, f"For the record: {PREFERENCE}")
        answer = reply(
            oai, agent, conv.id, "Given what I just told you, is a 60%-equity fund a fit?"
        )
        print("assistant:", answer, "\n")

        # ---- LONG-TERM (new session): force-extract, then a fresh conversation recalls ----
        print("== LONG-TERM (new session, days later) ==")
        # Trigger extraction immediately instead of waiting for conversation inactivity.
        poller = pc.beta.memory_stores.begin_update_memories(
            name=STORE,
            scope=USER,
            update_delay=0,
            items=[{"role": "user", "type": "message", "content": PREFERENCE}],
        )
        result = poller.result()
        print(f"extracted {len(result.memory_operations)} memories:")
        for op in result.memory_operations:
            print(f"  - {op.kind}: {op.memory_item.content}")

        conv2 = oai.conversations.create()  # brand-new session, nothing re-told
        answer2 = reply(
            oai, agent, conv2.id, "Suggest a rebalance for my portfolio this quarter."
        )
        print("\nassistant (new session):", answer2)
        print(
            "\nExpect it to honour 'max 20% equities / no crypto' WITHOUT being re-told."
        )

        # 3) Control — right-to-be-forgotten (GDPR). Uncomment to prove it live:
        # pc.beta.memory_stores.delete_scope(name=STORE, scope=USER)
        # print("scope forgotten.")


if __name__ == "__main__":
    main()
