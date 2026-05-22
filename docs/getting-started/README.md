# Getting Started

Three resources, depending on what you want to do.

| If you want to… | Open… |
|---|---|
| **Get xFRAME running locally and test every feature** | [QUICKSTART.md](./QUICKSTART.md) |
| **Deploy to a free cloud platform for testing** | [QUICKSTART.md](./QUICKSTART.md#part-8--free-cloud-deployment) (Part 8) |
| **Troubleshoot a specific issue** | [QUICKSTART.md](./QUICKSTART.md#part-9--troubleshooting) (Part 9) |
| **Understand the architecture first** | [`../handbook/03-architecture.md`](../handbook/03-architecture.md) |
| **Learn AI agents from scratch** | [`../book/part-01-foundations.md`](../book/part-01-foundations.md) |

## Time estimates

| Task | Time |
|---|---|
| Local setup + verification | ~30 minutes |
| Full feature test pass (Parts 6-7) | ~60 minutes |
| Free cloud deployment (Railway) | ~20 minutes |
| Free cloud deployment (Fly.io + Neon + Upstash) | ~45 minutes |

## Prerequisites at a glance

- Python 3.12+
- Docker 24+
- `uv` (Python package manager)
- (For Phase 7) `PRICEFRAME_JWT_SECRET` + `PRICEFRAME_SERVICE_SECRET` from the PriceFRAME team
- (Optional) LLM provider: Gemini Vertex (GCP) OR Anthropic API key

## What's in QUICKSTART.md

10 parts:

1. **Prerequisites** — tools, secrets, credentials
2. **Local Setup** — clone, configure, start dependencies, migrate
3. **Verify the Build** — static checks + 40-test suite
4. **Start the API** — uvicorn + health check
5. **Configure LLM Provider (Optional)** — Vertex or Anthropic
6. **Test Every Feature** — 10 functional tests, no PriceFRAME required
7. **PriceFRAME Integration Tests** — 22 end-to-end scenarios against real PriceFRAME
8. **Free Cloud Deployment** — Railway (easiest), Fly.io + Neon + Upstash (truly free), Render
9. **Troubleshooting** — symptom → cause → fix tables
10. **Cleanup** — remove local + cloud resources

Plus an Appendix with a one-page command cheat sheet.
