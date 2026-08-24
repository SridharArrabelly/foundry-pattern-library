# Pattern 12 — Centralized Toolboxes (one governed MCP endpoint)

**Group:** Agent construction & knowledge  ·  **Runs 6th of 15** in the run order

**Slide title:** *Curate tools once. Every agent gets them — governed, versioned, discoverable.*

## In brief
> "Every pattern so far wires tools to *one* agent. That works right up until you have
> forty. Then five teams build the same connector, every agent carries its own
> credentials, and nobody can answer the auditor's question: *what tools exist, and who
> can call them?*
>
> A **Toolbox** is Foundry's answer. You curate tools once — MCP servers, Code
> Interpreter, Web Search, File Search, AI Search, OpenAPI, A2A — and Foundry publishes
> them behind **one MCP endpoint**. Credentials stay server-side. Guardrails apply at the
> toolbox level. [run it]
>
> Now the two moves that matter. First, **versioning**: I create a new version, promote it
> to default, and every agent pointed at the consumer URL picks it up — **nothing
> redeployed, no code changed**. That's central revocation and change control for your
> tool plane.
>
> Second, **tool search**. Every tool definition is input tokens on *every* request,
> whether the model uses it or not. Watch the count: this toolbox exposes four
> definitions directly — and two meta-tools once tool search is on. Scale that to a
> hundred tools and you've stopped paying for context you don't use, and stopped asking
> the model to choose between a hundred near-identical options."

## Where it sits next to the patterns you've seen
Say this explicitly or people will hear an echo of Pattern 3:

- **Pattern 3** governs *one tool call at your gateway*. APIM authenticates the caller,
  holds the backend key and meters the call. It works for any client, Foundry or not.
- **Pattern 12** governs *the whole catalogue inside Foundry* — one endpoint, versioned,
  discoverable, curated centrally.
- **They compose.** Create a Foundry project connection to Pattern 3's APIM MCP route, set
  `TOOLBOX_WEBIQ_CONNECTION_NAME`, then enable `TOOLBOX_INCLUDE_WEBIQ=true`. The toolbox
  passes the project connection ID, not a raw key. The gateway still meters it; the toolbox
  hands it to every agent.
- **Pattern 4** is the loop that *uses* skills. Foundry **Skills** (preview) is where
  skills live server-side — versioned, immutable, attached to a toolbox.

## What Foundry gives you here
- **One endpoint, not N integrations** — add or swap a tool without touching agent code.
- **Central credential custody** — the toolbox injects credentials and refreshes tokens;
  agents hold none.
- **Versioned tool plane** — promote or roll back centrally; every consumer follows.
- **Tool search** — a hundred tools without a hundred tool definitions in every request.

## The one-liner
> "You don't wire tools to agents. You run a tool plane — and the agents subscribe."

## Running it
1. `uv run python 12-toolbox/toolbox_demo.py`
2. Two versions are created: one plain, one with tool search.
3. Point at **`v_n` exposes 4 / `v_n+1` exposes 2** — that's the context-cost story.
4. The **promotion**: default flips to the new version and the *same* consumer URL starts
   serving it. Nothing was redeployed.
5. `tool_search` resolves a real tool by intent, proving the meta-tools work.
6. Portal: **Foundry → Tools → Toolboxes** — the toolbox, its versions and its endpoint.

## Notes
- Keyless, but the audience is **`https://ai.azure.com`**. The
  `cognitiveservices` scope the other patterns use returns a clean **401** here — a
  decent 10-second aside on why audience matters.
- A toolbox allows **one tool without a `name`**. Give every tool a `name` (or a
  `server_label`) or creation fails with `invalid_payload`.
- Tools are namespaced by source: `microsoft_learn___microsoft_docs_search`.
- Optional Web IQ credentials stay in the Foundry project connection. Pattern 12 never reads
  `WEBIQ_APIM_KEY` or embeds headers in a toolbox tool definition.
- Each run creates two more versions, so the numbers climb between runs. That's the
  feature, not a leak.
- Requires the **Foundry User** role on the project and a supported region.
- Tool search and Skills are **preview**.

## Golden expected output
```
Curating 2 tools into toolbox 'rm-toolbox':
  - microsoft_learn (mcp)
  - code (code_interpreter)

v7 created - tools listed directly.
v8 created - same tools, plus tool search.

Default version is still v1. Agents are untouched.

  v7 exposes 4 tool definitions: ['code', 'microsoft_learn___microsoft_code_sample_search',
    'microsoft_learn___microsoft_docs_fetch', 'microsoft_learn___microsoft_docs_search']
  v8 exposes 2: ['tool_search', 'call_tool']
  Every definition in the first list is input tokens on EVERY request.

Promoted default: v1 -> v8
Consumer endpoint (same URL as before) now serves: ['tool_search', 'call_tool']

tool_search resolved:
{"tools":[{"name":"microsoft_learn___microsoft_docs_search", ... }]}
```
> The two numbers that sell it: **4 definitions → 2 meta-tools**, and a default version
> promoted from **v1 → v8** with no consumer touched.
