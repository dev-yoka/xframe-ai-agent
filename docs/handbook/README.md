# xFRAME AI Agent — Engineering Handbook

**Audience:** any engineer who needs to understand, debug, test, extend, or operate the xFRAME AI Agent — including engineers new to AI agents, LLM systems, and tool-calling.

**Purpose:** unlike the `docs/ai-agent/` folder (which holds planning, phase handoffs, and technical reference cards), this handbook is a **learning-first engineering manual**. It teaches AI agents from first principles, then walks the actual implementation line-by-line, then teaches you how to test, debug, deploy, and improve it.

**Reading paths:**

| If you are… | Read in this order |
|---|---|
| New to AI agents | 02 → 01 → 03 → 05 → 14 |
| New to this codebase (already know LLMs) | 01 → 03 → 04 → 05 → 06 |
| Debugging a live issue | 10 → 11 → 05 → 14 |
| Writing tests | 09 → 04 → 14 |
| Deploying / operating | 13 → 11 → 12 |
| Adding a new tool | 04 (§4.6 tools) → 06 → 07 → 09 |
| Reviewing for security | 12 → 06 → 04 (§4.4 auth) |

---

## Table of contents

| §  | File | Topic |
|----|------|-------|
| 01 | [01-overview.md](./01-overview.md) | What the agent does, business purpose, end-to-end user journey, system map |
| 02 | [02-fundamentals.md](./02-fundamentals.md) | AI agents from scratch — LLMs, tool calling, reasoning loops, memory, context |
| 03 | [03-architecture.md](./03-architecture.md) | Complete system architecture — components, data flow, dependencies |
| 04 | [04-source-walkthrough.md](./04-source-walkthrough.md) | Folder-by-folder, file-by-file walk of `src/xframe_agent/` |
| 05 | [05-execution-flow.md](./05-execution-flow.md) | Step-by-step trace of what happens after a user message |
| 06 | [06-priceframe-integration.md](./06-priceframe-integration.md) | Auth, REST API mapping, audit callbacks, HMAC signing, retries |
| 07 | [07-prompt-engineering.md](./07-prompt-engineering.md) | System prompts, tool-output wrapping, redaction, prompt-injection defense |
| 08 | [08-memory-context-reasoning.md](./08-memory-context-reasoning.md) | Context assembly, durable event log, run/conversation state |
| 09 | [09-testing-strategy.md](./09-testing-strategy.md) | Unit, integration, behavioral, eval, security, load testing |
| 10 | [10-debugging-guide.md](./10-debugging-guide.md) | Symptoms → root causes; troubleshooting matrices |
| 11 | [11-observability.md](./11-observability.md) | Logs, metrics, traces, token + cost tracking |
| 12 | [12-security-safety.md](./12-security-safety.md) | Threat model, secret handling, prompt-injection, audit |
| 13 | [13-deployment.md](./13-deployment.md) | Local, Docker, production; rollback; scaling |
| 14 | [14-walkthroughs.md](./14-walkthroughs.md) | 8 realistic end-to-end scenarios with trace logs |
| 15 | [15-improvements.md](./15-improvements.md) | Prioritized backlog of architectural improvements |
| —  | [glossary.md](./glossary.md) | Every term used in this handbook |
| —  | [faq.md](./faq.md) | Common questions |

---

## How to use this handbook

**Code citations.** Every claim about the implementation cites `path/to/file.py:LINE`. Click those in your editor or run `grep -n` to jump.

**Diagrams.** All diagrams are written in [Mermaid](https://mermaid.js.org/). They render natively in GitHub, GitLab, and VS Code's Markdown preview. To export to PNG: copy the source block into [mermaid.live](https://mermaid.live).

**Cross-references.** When a section references another, it's linked. Follow them — the handbook is intentionally cross-cut rather than linear.

**Code annotations.** Where source code is quoted, comments prefixed with `# ←` are added by the handbook to explain context; they are not in the actual code.

---

## Companion documents

| Doc | Use it for |
|---|---|
| [`docs/ai-agent/09-xframe-ai-agent-complete-reference.md`](../ai-agent/09-xframe-ai-agent-complete-reference.md) | Quick reference card (API endpoints, tool list, env vars) |
| [`docs/ai-agent/11-testing-guide.md`](../ai-agent/11-testing-guide.md) | Step-by-step CI verification checklist for a specific sprint |
| [`docs/ai-agent/12-v1-completion-plan.md`](../ai-agent/12-v1-completion-plan.md) | v1 scope + deployment plan against the deployed PriceFRAME |
| [`docs/ai-agent/13-v1-tracker.md`](../ai-agent/13-v1-tracker.md) | v1 completion status |
| [`docs/deploy/v1-deployment.md`](../deploy/v1-deployment.md) | Production deployment runbook |
| [`docs/deploy/provider-setup.md`](../deploy/provider-setup.md) | GCP/Anthropic provider credential setup |
| `openapi.yaml` (repo root) | Machine-readable API contract |

---

## Maintenance

When code changes:

- New tool → update §04 source walkthrough + §06 PriceFRAME mapping + add test pattern in §09
- New endpoint → update §03 architecture + §04 source walkthrough + regenerate `openapi.yaml`
- New provider → update §04 source walkthrough + §05 execution flow
- New env var → update §13 deployment + §10 debugging (if it has failure modes)
- New event type → update §05 execution flow + §08 memory & reasoning + §11 observability

Run `uv run pytest && uv run ruff check . && uv run mypy && uv run python scripts/export_openapi.py` before committing handbook updates that reference code (so citations stay accurate).
