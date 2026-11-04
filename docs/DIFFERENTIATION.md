# Step 0: adjacent plugins and differentiation

Checked 2026-11-04, before implementation. Sources: the installed Hermes catalog and the live announcement at https://x.com/NousResearch/status/2118094256438936062.

## What was actually inspected

- The live announcement says submissions close **Nov 11 at 5 PM EST**, a catalog PR + an X demo tagging @NousResearch + the linked form, demo under 90 seconds. The repository remains private and none of those publication actions has been performed.
- The visible Relevant reply slice mentioned phone control, an avatar plugin, an existing print3d plugin and an eBay/Pokémon business tool. No room bridge was visible in that slice. This is **not** an exhaustive scan of all replies or future entrants; indexed web search returned no useful announcement-thread result, so the actual browser thread was read instead. No paid X API/search call was made.
- `plugin-catalog/parlor.yaml` pins dariorapisardi/parlor.sh at 31db52cfd456750338e4dcc98b47b8ec8efc2ef5: portable skill + hosted unauthenticated MCP rooms, not a native gateway adapter.
- `plugin-catalog/huddo.yaml` pins puffo-ai/huddo-mcp at 3b724cb4d066096edfdf86cc091b3cc03f21f8b8: people/agent rooms with a portable stdio MCP integration and repeated bounded `wait` calls; its catalog disclosure describes that token-consuming keep-waiting loop and much broader write capabilities.
- `plugin-catalog/beam.yaml` pins Beam-Network/beam-hermes at d41448cb3f08bacbc368a4948ecbe310e5f6964c: cloud transfer + E2EE agent room tools, requiring BEAM_API_KEY. BotSesh is **not E2EE** and should not claim otherwise.
- Generic `bridge-adapter`, `chatto-platform`, `jp-chatwork`, `bunny_space-platform` and bundled A2A adapters were inspected for the adjacent messaging/native adapter shape. Native bridges themselves are not a novel invention.

## The pitch that the code must earn

The useful difference is **BotSesh as a first-class Hermes channel**: native gateway events, own enrollment identity, no model calls while idle, real later room/peer messages, safe room-scoped read/post tools, and exact processed-context/durable-send semantics. The plugin does more than a skill because the transport lifecycle, routing, authorization, persistence, retries and reply delivery are executable host integration—not prompt instructions to leave an LLM in a loop.

BotSesh remains a collaboration room for humans and separately running agents, not a model host or an autonomous-approval system. Preserve that distinction in the demo. A scripted peer is an acceptable transport handoff fixture, not proof that ChatGPT/Claude/Grok independently stayed connected or returned.

## Source mismatch found

The card's supplied `/home/sigma/Projects/botsesh-k-int` was at 2514b478680567abb39b25a2c1cfafa88783cdeb and predates native creation, manager invites, draft_ready and routine_report. The live SDK/reference includes them. The plugin therefore pins the real live SDK bytes rather than inventing endpoints or silently downgrading the request to a browser-creator session. That checkout was not edited.
