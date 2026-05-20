# xFRAME AI Agent — Documentation

This folder is the **canonical planning and reference** set for the Sales Representative **AI agent** that works with **PriceFRAME** (system of record) and the separate **`xframe-ai-agent`** service (Python / FastAPI).

| Doc | Purpose |
|-----|---------|
| [01-workspace-and-domain.md](./01-workspace-and-domain.md) | Workspace context, domain vocabulary, stakeholders |
| [02-architecture-proposal.md](./02-architecture-proposal.md) | v2 architecture: repos, nginx, JWT pass-through, providers |
| [03-priceframe-delta-prs.md](./03-priceframe-delta-prs.md) | PriceFRAME API deltas (audit callback, pricing preview, etc.) |
| [04-phase1-verification.md](./04-phase1-verification.md) | Phase 1 verification checklist |
| [05-phase-B-bootstrap.md](./05-phase-B-bootstrap.md) | Bootstrap / early integration notes |
| [06-phase-C-delta-prs.md](./06-phase-C-delta-prs.md) | Phase C delta PR tracking |
| [07-phase-D-mvp.md](./07-phase-D-mvp.md) | Phase D MVP handoff (**historical**; see note in file) |
| [08-phase-E-beta.md](./08-phase-E-beta.md) | Phase E beta handoff (web chat, writes, audit) |
| [**09-xframe-ai-agent-complete-reference.md**](./09-xframe-ai-agent-complete-reference.md) | **Full technical reference** — APIs, tools, lifecycle, env, gaps |

## Where to start

- **Product / architecture**: read **02** then **03**.
- **Current implementation truth** (what ships in code today): read **09** and the companion repo **`xframe-ai-agent`** root **`README.md`** (and `src/xframe_agent/tools/registry.py`).
- **Handoff status at a point in time**: **07** (Phase D) and **08** (Phase E).

## Repositories

| Repository | Role |
|------------|------|
| **PriceFRAME** (this repo) | AdonisJS API, web client, pricing domain, RBAC, audit logs, delta endpoints consumed by the agent |
| **xframe-ai-agent** | FastAPI service: conversations, runs, SSE, tool registry, HITL decisions, attachments, memory, voice stub |

## Maintenance

When behavior changes in **`xframe-ai-agent`**, update **09** and the phase handoff (**08** or a future **10-…**) so this directory stays aligned with code. OpenAPI for the agent is generated in the agent repo (`scripts/export_openapi.py` → `openapi.yaml`).
