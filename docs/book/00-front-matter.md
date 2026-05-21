# Building Production AI Agents

## A Practical Engineering Book Based on the xFRAME AI Agent

---

**Project:** `xframe-ai-agent`
**Domain:** Conversational pricing assistant for the **PriceFRAME** remittance pricing platform
**Stack:** Python 3.12 · FastAPI · SQLAlchemy async · Gemini Vertex / Anthropic · PostgreSQL · Redis · Docker
**Version:** v1.0 (post-merge, 2026-05-20)
**Authors:** xFRAME engineering, augmented by Claude (Anthropic) for technical writing

---

## Preface

### What this book is

This is **not** a generic "build an AI agent" tutorial. It is the engineering manual for **one real, shipped AI agent** — the xFRAME AI Agent — written so that anyone reading it can:

1. Understand AI agents from first principles.
2. Reverse-engineer the project end to end.
3. Build, deploy, and operate similar agents in production.

Every concept is taught twice: once in plain language for the beginner, then in implementation depth using the actual code. Every code reference cites a real file and line number.

### Why a real codebase?

Generic AI tutorials skip the hard parts: persistent state, human-in-the-loop approval, audit signing, provider failover, multi-tenant authorization, cost ceilings, prompt-injection defense, observability. Real production agents live and die on those concerns. The xFRAME AI Agent implements all of them — so we use it as the worked example.

### Who this book is for

You are the reader if you are:

- A developer **new to AI agents** but comfortable with HTTP, JSON, and at least one programming language.
- An experienced backend engineer who wants to **see how the LLM pieces fit into a normal web stack**.
- A technical PM, architect, or security reviewer needing **deep comprehension without writing code**.
- A team taking over the xFRAME AI Agent codebase.

You do **not** need prior experience with:

- Large Language Models (Chapter 2 teaches them from scratch)
- Tool calling / function calling (Chapter 6)
- Retrieval-augmented generation (Chapter 7, conceptual)
- The Model Context Protocol (Chapter 8, conceptual)
- Multi-agent orchestration (Chapter 8, conceptual)
- AsyncIO / FastAPI (we explain the relevant parts inline)

### Prerequisites

Helpful but not required:

- Python ≥ 3.10 syntax (the codebase is 3.12).
- Comfort with the command line, git, and Docker.
- Understanding of REST APIs, JSON, JWT bearer tokens.

If you can read a `for` loop and understand `await`, you have enough to follow.

### What you will be able to do after reading

After this book you will be able to:

- Explain in interview-level depth how a modern tool-calling agent works.
- Read the xFRAME codebase fluently — every file, every concept.
- Add a new tool, prompt, or provider to the agent.
- Debug a stuck or hallucinating run from logs + the durable event journal.
- Deploy a production AI agent (Docker, secrets, nginx, observability, alerts).
- Make defensible architecture decisions around HITL, audit, prompt injection, and cost.
- Build your own similar agent for a different domain.

### How to read this book

The book is **17 parts** plus glossary and appendices. Three reading paths:

| If you are… | Read in this order |
|---|---|
| New to AI agents | Parts 1 → 2 → 4 → 3 → 6 → 7 → 9 → 11 → 12 → 14 |
| Backend engineer joining the project | Parts 2 → 3 → 4 → 7 → 11 → 12 |
| Operating / SRE / security | Parts 2 → 10 → 11 → 12 → 13 |
| Architecting your own agent | Parts 1 → 4 → 6 → 7 → 14 → 15 |

Each chapter ends with:

- 🔑 **Key takeaways** (5–7 bullets)
- ✍️ **Exercises** (hands-on, with answers in the appendix)
- 📚 **Further reading** (papers, blog posts, vendor docs)

### Relationship to other documentation

This book complements:

- `docs/handbook/` — terser, file-by-file engineering reference (good for day-to-day lookups)
- `docs/ai-agent/` — historical planning + phase handoffs
- `docs/deploy/` — operational runbooks
- `openapi.yaml` — machine-readable API contract
- The source code itself

The book is the **teaching** document. The handbook is the **reference** document. Use both.

### Conventions

- **Bold** marks key terms on first introduction.
- `monospace` marks code, file paths, and shell commands.
- Code blocks include the file path and line number when quoting source: `agent/runner.py:99-118`.
- 🎯 indicates a section that applies directly to xFRAME.
- 🧠 indicates a concept-only section (xFRAME doesn't use it yet, but you should know it).
- ⚠️ indicates a common pitfall.
- 💡 indicates a non-obvious insight or design decision.

### Acknowledgments

The xFRAME AI Agent was designed and built across Phases A–E and v1 (2026 Q1–Q2). This book is built on the working code and the planning record in `docs/ai-agent/`. Where production gaps remain, they are documented honestly in Part 15.

---

## Detailed Table of Contents

### Front Matter

- Title Page
- Preface
- Table of Contents

### Part 1 — Foundations (Concepts From Scratch)

**Chapter 1 — What Is an AI Agent?**
1.1 The vending machine, the workflow, and the agent
1.2 A precise definition
1.3 What separates an agent from a chatbot
1.4 What separates an agent from a workflow
1.5 The five components of every agent
1.6 Why agents now? A short history
1.7 What agents are NOT good at
1.8 Key takeaways, exercises, further reading

**Chapter 2 — Large Language Models from First Principles**
2.1 The world before LLMs
2.2 What a neural network actually does
2.3 The transformer breakthrough
2.4 What an LLM literally does at inference time
2.5 Pre-training, fine-tuning, RLHF in plain English
2.6 Closed vs open weights
2.7 Temperature, top-p, sampling
2.8 Why LLMs hallucinate (and what to do about it)
2.9 Key takeaways, exercises, further reading

**Chapter 3 — Tokens, Context Windows, and the Cost Equation**
3.1 Tokens are not words
3.2 Why tokenization affects everything
3.3 The context window
3.4 Reading the bill: input, output, cached
3.5 The hidden cost of long conversations
3.6 Context caching (Anthropic, Vertex)
3.7 How xFRAME's `LoopBudget` enforces sanity
3.8 Key takeaways, exercises, further reading

**Chapter 4 — Embeddings and the Geometry of Meaning**
4.1 Words as numbers
4.2 What an embedding model is
4.3 Cosine similarity in pictures
4.4 What you can and can't do with embeddings
4.5 Embeddings vs LLMs (different beasts)
4.6 Why xFRAME doesn't use embeddings yet (and what changes when it does)
4.7 Key takeaways, exercises, further reading

**Chapter 5 — Prompt Engineering: The Discipline**
5.1 The prompt is the program
5.2 System / user / assistant / tool roles
5.3 Zero-shot, few-shot, chain-of-thought
5.4 Structured outputs
5.5 Prompt injection: the OWASP-level threat
5.6 Defensive patterns
5.7 Where xFRAME's prompts live and why
5.8 Key takeaways, exercises, further reading

**Chapter 6 — Tool Calling (a.k.a. Function Calling)**
6.1 The single most important agent primitive
6.2 The JSON Schema contract
6.3 The four-phase tool round-trip
6.4 Why the model never executes
6.5 Parallel vs serial tool calls
6.6 Idempotency and retries
6.7 How xFRAME's `ToolDefinition` works
6.8 Key takeaways, exercises, further reading

**Chapter 7 — Retrieval-Augmented Generation (Concept-Only for xFRAME)**
7.1 The hallucination problem revisited
7.2 The RAG pipeline: chunk, embed, store, retrieve, generate
7.3 Vector databases (pgvector, Pinecone, Weaviate, Qdrant)
7.4 Hybrid search and re-ranking
7.5 When NOT to use RAG (xFRAME's choice)
7.6 If you added RAG to xFRAME tomorrow…
7.7 Key takeaways, exercises, further reading

**Chapter 8 — Multi-Agent, Orchestration, and MCP (Concept-Only)**
8.1 Why one agent isn't always enough
8.2 Orchestrator + worker patterns
8.3 Agent-as-a-tool
8.4 The Model Context Protocol explained
8.5 Why xFRAME is intentionally single-agent
8.6 What this would look like if extended
8.7 Key takeaways, exercises, further reading

**Chapter 9 — Memory, Planning, and Reasoning**
9.1 The five tiers of agent memory
9.2 Working vs episodic vs semantic vs procedural memory
9.3 ReAct, planning-then-execution, and reflection loops
9.4 How xFRAME's runner is a (minimalist) ReAct loop
9.5 Long-horizon tasks: the unsolved problem
9.6 Key takeaways, exercises, further reading

**Chapter 10 — AI Safety: The Engineering View**
10.1 What "safety" means at the agent layer
10.2 Authorization vs authentication revisited
10.3 The three independent checks before any write
10.4 Human-in-the-loop as a design pattern
10.5 Prompt injection, data exfiltration, model manipulation
10.6 Auditability as a safety property
10.7 Key takeaways, exercises, further reading

### Part 2 — Project Overview

**Chapter 11 — The Business Context**
11.1 What PriceFRAME is and why the agent exists
11.2 The user journey, end to end
11.3 The cardinal rule: agent ≠ system of record
11.4 What v1 ships and what's deferred

**Chapter 12 — High-Level Architecture**
12.1 The 8-layer view
12.2 Component dependencies
12.3 Data flow for one tool call (sequence diagram)
12.4 Data flow with HITL approval (sequence diagram)
12.5 The two-runner design (`AgentLoop` vs `ModelRunner`)
12.6 What lives where (cheat sheet)

**Chapter 13 — The Runtime Environment**
13.1 Process model: API + worker + DB + Redis
13.2 Local vs production topology
13.3 nginx, SSE, and reverse-proxy contracts
13.4 GCP service-account and provider credentials
13.5 Key environment variables you'll touch

### Part 3 — Codebase Deep Dive

**Chapter 14 — Repository Map**
**Chapter 15 — `main.py` and the FastAPI App Factory**
**Chapter 16 — `settings.py` and Configuration**
**Chapter 17 — `auth/` — JWT Verification and `AuthContext`**
**Chapter 18 — `api/v1/` — Every Endpoint Explained**
**Chapter 19 — `agent/runner.py` — The Heart of the System**
**Chapter 20 — `agent/loop.py` — The Deterministic Path**
**Chapter 21 — `agent/dispatch.py`, `history.py`, `events.py`, `budget.py`**
**Chapter 22 — `agent/redaction.py` and `wrapping.py`**
**Chapter 23 — `tools/` — Definition, Registry, Read, Write**
**Chapter 24 — `provider/` — Failover Router and Three Adapters**
**Chapter 25 — `priceframe/client.py` — HTTP, Retries, HMAC**
**Chapter 26 — `models/` and `migrations/`**
**Chapter 27 — `middleware/`, `observability/`, `attachments/`, `worker.py`**

### Part 4 — AI Agent Architecture

**Chapter 28 — The Run Loop, Frame by Frame**
**Chapter 29 — Tool Dispatch: Reads in Parallel, Writes in Serial**
**Chapter 30 — Human-in-the-Loop: The Pause-Resume Contract**
**Chapter 31 — Provider Failover Internals**
**Chapter 32 — Budget Enforcement and Loop Detection**
**Chapter 33 — Durable Event Sourcing for Replay**
**Chapter 34 — State Machine: Every Run Status Transition**

### Part 5 — RAG and Knowledge Systems (Concept-Only + Future Direction)

**Chapter 35 — The Chunking Decision Tree**
**Chapter 36 — Embedding Models in 2026**
**Chapter 37 — Vector Database Choices for Python Shops**
**Chapter 38 — Designing the xFRAME RAG Layer (Hypothetical)**
**Chapter 39 — Hallucination Reduction Patterns**

### Part 6 — Prompt Engineering Deep Dive

**Chapter 40 — Anatomy of the `create_pricing_request` Prompt**
**Chapter 41 — Tool Catalog as Prompt (Hidden Cost)**
**Chapter 42 — `wrap_tool_output` as Defense in Depth**
**Chapter 43 — PII Redaction Patterns and Trade-offs**
**Chapter 44 — Adding a New Conversation Kind**
**Chapter 45 — Few-Shot, Chain-of-Thought, and When to Use Each**

### Part 7 — Tools and Integrations

**Chapter 46 — The `ToolDefinition` Contract in Depth**
**Chapter 47 — Read Tools: All Six, Annotated**
**Chapter 48 — Write Tools: All Six, Annotated**
**Chapter 49 — The PriceFRAME REST Contract**
**Chapter 50 — HMAC-Signed Audit Callbacks**
**Chapter 51 — Adding a Brand-New Tool, End to End**

### Part 8 — Frontend and UX (Concept + xFRAME Specifics)

**Chapter 52 — Where the Frontend Lives (Flutter + PriceFRAME Web)**
**Chapter 53 — Designing for Streaming Token UX**
**Chapter 54 — Surfacing Tool Proposals and Approval Cards**
**Chapter 55 — SSE on Mobile: `EventSource` Quirks**

### Part 9 — Databases and Storage

**Chapter 56 — PostgreSQL Schema, Table by Table**
**Chapter 57 — Async SQLAlchemy Patterns Used**
**Chapter 58 — Alembic: How Migrations Work Here**
**Chapter 59 — Redis: Rate Limit, arq Queue, SSE Buffer**
**Chapter 60 — S3 / MinIO for Attachments**
**Chapter 61 — When to Add a Vector Database**

### Part 10 — Security

**Chapter 62 — The Threat Model in Detail**
**Chapter 63 — Secret Management**
**Chapter 64 — JWT, HMAC, and the Three-Layer Auth**
**Chapter 65 — Prompt Injection: Attacks Catalogued**
**Chapter 66 — Data Leakage Prevention**
**Chapter 67 — Rate Limiting and Abuse**
**Chapter 68 — Compliance Posture (GDPR, SOC2 readiness)**

### Part 11 — Testing and Debugging

**Chapter 69 — The 40-Test Suite, Categorized**
**Chapter 70 — `FakeProvider` and `FakePriceFrame` Patterns**
**Chapter 71 — Golden Trace Evals**
**Chapter 72 — Debugging a Stuck Run**
**Chapter 73 — Tracing with Langfuse**
**Chapter 74 — Property-Based Testing (Future)**
**Chapter 75 — Load and Chaos Testing**

### Part 12 — Deployment

**Chapter 76 — Local Development Setup**
**Chapter 77 — Docker Image Anatomy**
**Chapter 78 — docker-compose for Production**
**Chapter 79 — nginx, TLS, and SSE Buffering**
**Chapter 80 — GCP Service Account and Provider Credentials**
**Chapter 81 — Alembic Migrations on Container Start**
**Chapter 82 — Rolling Updates and Rollback**
**Chapter 83 — Kubernetes Sketch (Future)**
**Chapter 84 — CI/CD with GitHub Actions**

### Part 13 — Scaling and Production

**Chapter 85 — Performance Targets and Profiling**
**Chapter 86 — Horizontal Scale: API + Worker**
**Chapter 87 — Cost Optimization: Token Math**
**Chapter 88 — Concurrency and the Event Loop**
**Chapter 89 — Database Capacity Planning**
**Chapter 90 — SLOs, Alerts, and On-Call**

### Part 14 — Advanced AI Engineering

**Chapter 91 — Reflection and Self-Critique Loops**
**Chapter 92 — Multi-Agent Orchestration Patterns**
**Chapter 93 — Tool Learning and Discovery**
**Chapter 94 — Long-Term Memory and Personalization**
**Chapter 95 — Evaluation Beyond Unit Tests**
**Chapter 96 — Fine-Tuning vs Prompting vs RAG**
**Chapter 97 — Agent Marketplaces and MCP Servers**

### Part 15 — Improvements and Roadmap

**Chapter 98 — Shipped Improvements (§15.1, §15.4, §15.5)**
**Chapter 99 — Near-Term Roadmap**
**Chapter 100 — Long-Term Vision**

### Part 16 — Glossary

### Part 17 — Appendices

- **Appendix A** — Setup commands cheat sheet
- **Appendix B** — Useful scripts and shell aliases
- **Appendix C** — CLI references (`uv`, `alembic`, `arq`, `docker`)
- **Appendix D** — API reference (curl examples)
- **Appendix E** — Dependency rationale
- **Appendix F** — Exercise solutions
- **Appendix G** — Index

---

**Total chapters: 100 + 17 appendix sections.**

The book is generated in installments. This file is the front matter. Each subsequent file (`part-01-foundations.md`, `part-02-overview.md`, …) is one part of the book.

---

**Next:** [Part 1 — Foundations](./part-01-foundations.md).
