# 11 — Observability & Monitoring

> **Reading this section answers:** what data does the system emit? Where do logs/metrics/traces land? What should you watch?

## 11.1 The four observability surfaces

| Surface | Where | Cardinality | Use case |
|---|---|---|---|
| **Structured logs** | structlog → stdout (JSON) | High — one line per event | Trace one request; grep by `X-Request-ID` |
| **Metrics** | Prometheus `/metrics` | Low — counters/histograms | SLO dashboards; alerting |
| **LLM traces** | Langfuse (optional) | Per-run | Inspect exact prompts + completions |
| **Durable run events** | `agent_run_events` table | Per-run | Replay, audit, debug |

Architectural principle: **logs for narrative, metrics for math, traces for forensics, events for state**.

## 11.2 Logs

### 11.2.1 Setup

`logging.py` (referenced from `main.py:setup_logging`) configures structlog → JSON to stdout. Each log line includes:

```json
{
  "timestamp": "2026-05-20T11:42:13.412Z",
  "level": "info",
  "logger": "xframe_agent.api.v1.conversations",
  "event": "run_created",
  "request_id": "abc-123-def",
  "user_id": 7,
  "run_id": "01HX...",
  "conversation_id": "01HW..."
}
```

`request_id` is bound at the start of every request via `RequestIdMiddleware` and inherited by all log lines in that request's coroutine context.

### 11.2.2 Key log events to know

| Event name | Logger | Meaning |
|---|---|---|
| `request_started` / `request_completed` | `middleware.request_id` | Per HTTP request |
| `rate_limit_exceeded` | `middleware.rate_limit` | 429 returned |
| `jwt_verification_failed` | `auth.jwt` | Bad / expired / unsigned token |
| `priceframe_profile_cache_hit` / `_miss` | `auth.priceframe_session` | Profile cache behavior |
| `priceframe_request` | `priceframe.client` | Each outbound HTTP call (method, path, status, duration_ms) |
| `priceframe_retry` | `priceframe.client` | Retried due to 5xx or transport error |
| `tool_executed` | `agent.runner` | Per-tool, after success |
| `tool_failed` | `agent.runner` | Per-tool, with exception class |
| `run_finalized` | `agent.runner` | At terminal state, includes `cause` |

### 11.2.3 Querying logs

In production with a log shipper (Loki, Elastic, Datadog):

```
# All log lines for one request
{service="xframe-agent"} | json | request_id="abc-123"

# Errors per minute
{service="xframe-agent"} | json | level="error" | __error__="" |~ ".*" | rate(1m)

# Runs that finalized with budget exceeded
{service="xframe-agent"} | json | event="run_finalized" | cause="cost_budget_exceeded"
```

## 11.3 Prometheus metrics

`/metrics` endpoint exposed by `prometheus_fastapi_instrumentator` (`observability/metrics.py`).

### 11.3.1 Built-in (FastAPI instrumentator)

| Metric | Type | Labels |
|---|---|---|
| `http_requests_total` | counter | `method`, `status`, `handler` |
| `http_request_duration_seconds` | histogram | `method`, `handler` |
| `http_request_size_bytes` | histogram | `method`, `handler` |
| `http_response_size_bytes` | histogram | `method`, `handler` |

### 11.3.2 Custom (recommended additions — not all implemented today)

Targets to add over time:

```python
agent_runs_total           # counter, labels: status (completed/error/cancelled/awaiting_decision), cause
agent_tool_calls_total     # counter, labels: tool_name, status
agent_provider_latency_seconds  # histogram, labels: provider, model
agent_priceframe_latency_seconds  # histogram, labels: method, path_template, status
agent_budget_consumed      # gauge, labels: budget_type (steps/tokens/cost)
agent_sse_connections_active  # gauge
```

If you implement these, decorate the call sites in `runner.py`, `priceframe/client.py`, and `api/v1/runs.py`.

## 11.4 Token + cost tracking

`LoopBudget.snapshot()` is emitted in `v1.run.completed` and `v1.run.error` payloads:

```json
{
  "steps": 4,
  "tool_calls": 2,
  "input_tokens": 6_142,
  "output_tokens": 287,
  "cost_usd": 0.000713,
  "elapsed_s": 8.41
}
```

### Daily cost dashboard query (Postgres)

```sql
SELECT
  date_trunc('day', e.created_at) AS day,
  SUM((e.payload->'budget'->>'cost_usd')::numeric) AS total_cost,
  SUM((e.payload->'budget'->>'input_tokens')::int) AS in_tokens,
  SUM((e.payload->'budget'->>'output_tokens')::int) AS out_tokens,
  COUNT(*) AS runs
FROM agent_run_events e
WHERE e.event_type IN ('v1.run.completed', 'v1.run.error')
  AND e.created_at >= NOW() - INTERVAL '30 days'
GROUP BY day ORDER BY day DESC;
```

### Per-user cost

```sql
SELECT r.user_id,
       SUM((e.payload->'budget'->>'cost_usd')::numeric) AS cost
FROM agent_runs r
JOIN agent_run_events e ON e.run_id = r.id
WHERE e.event_type IN ('v1.run.completed', 'v1.run.error')
  AND e.created_at >= NOW() - INTERVAL '7 days'
GROUP BY r.user_id ORDER BY cost DESC LIMIT 20;
```

## 11.5 Langfuse traces (optional)

When `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` are set, the agent exports every LLM call as a trace.

Each trace contains:

- Full message list sent to provider
- Full response received (text + tool_use blocks)
- Latency
- Token counts
- Model identifier
- Trace metadata: `run_id`, `user_id`, `conversation_id`

This is **the** tool for prompt debugging. Without Langfuse, you'd need to add temporary print statements or capture network traffic.

To set up Langfuse locally:

```bash
docker compose up -d langfuse langfuse-db
open http://localhost:3001    # default port from docker-compose.yml
```

Generate API keys in the UI; set them in `.env`.

## 11.6 The durable event log as observability

`agent_run_events` is the system's most useful **historical record**. For any past run you can fully reconstruct what happened:

```sql
-- Timeline view
SELECT seq, event_type,
       payload->>'tool_name' AS tool,
       payload->>'cause' AS cause,
       jsonb_pretty(payload) AS details,
       created_at
FROM agent_run_events
WHERE run_id = $1
ORDER BY seq;
```

Powerful queries:

```sql
-- Find runs that took >30s
SELECT r.id,
       MAX(e.created_at) - MIN(e.created_at) AS duration
FROM agent_runs r
JOIN agent_run_events e ON e.run_id = r.id
GROUP BY r.id
HAVING MAX(e.created_at) - MIN(e.created_at) > INTERVAL '30 seconds'
ORDER BY duration DESC LIMIT 20;

-- Most-called tools this week
SELECT payload->>'tool_name' AS tool, COUNT(*)
FROM agent_run_events
WHERE event_type = 'v1.tool.completed'
  AND created_at >= NOW() - INTERVAL '7 days'
GROUP BY tool ORDER BY COUNT(*) DESC;

-- Failure causes this week
SELECT payload->>'cause' AS cause, COUNT(*)
FROM agent_run_events
WHERE event_type = 'v1.run.error'
  AND created_at >= NOW() - INTERVAL '7 days'
GROUP BY cause ORDER BY COUNT(*) DESC;
```

## 11.7 Recommended alerts

| Alert | Threshold | Action |
|---|---|---|
| Run error rate spike | `rate(v1.run.error) > 5% over 5min` | Page on-call |
| Provider failover triggered | `provider_unhealthy_total > 0` | Investigate Vertex/Anthropic status |
| PriceFRAME 5xx rate | `rate(priceframe 5xx) > 1/min` | Page PriceFRAME team |
| Auth profile cache miss spike | `rate(profile_cache_miss) > expected` | PriceFRAME `/auth/profile` slow? |
| Stuck runs | `count(agent_runs WHERE status IN (queued,running) AND created_at < NOW()-INTERVAL '5min') > 0` | Look at worker; possibly clean up |
| Cost overage | `daily_cost_sum > daily_budget` | Notify finance |
| SSE FD usage | `process_open_fds > 80% of ulimit` | Scale or rotate connections |
| DB connection saturation | `pg_stat_activity count > 80% of max_connections` | Increase pool or DB capacity |

## 11.8 Dashboards (Grafana templates)

A minimal Grafana dashboard for the agent should have:

**Row 1 — Liveness**
- `up{job="xframe-agent"}` — is the service responding to scrape?
- `rate(http_requests_total[1m])` per `handler`
- P50/P95/P99 of `http_request_duration_seconds`

**Row 2 — Run lifecycle**
- Runs created per minute
- Runs by terminal cause (stacked)
- Active SSE subscriptions

**Row 3 — LLM**
- Tokens in/out per minute
- Cost USD/minute
- Provider failover events

**Row 4 — PriceFRAME**
- Outbound RPS to PriceFRAME by endpoint
- 4xx/5xx rate to PriceFRAME
- P95 latency to PriceFRAME

**Row 5 — Dependencies**
- Redis ops/sec
- Postgres active connections
- DB query latency P95

## 11.9 Health endpoint

`GET /api/v1/agent/health` (`api/v1/health.py:33-44`) returns:

```json
{
  "status": "ok",          // or "degraded"
  "version": "1.0.0",
  "components": {
    "database": "ok",
    "redis": "ok",
    "priceframe": "ok",   // skipped if HEALTH_CHECK_EXTERNALS=false
    "providers": {
      "configured": true,
      "primary": "gemini_vertex"
    }
  }
}
```

The container's Docker health check curl's this endpoint every 30s (see `docker-compose.prod.yml`). If any component is `degraded`, status code is 503.

## 11.10 Audit trail observability

Two complementary tables:

- **`agent_audit_log`** (local, in agent DB) — every agent-initiated write attempt.
- **`audit_logs`** (in PriceFRAME) — what PriceFRAME's compliance system recorded.

These are linked by `agent_tool_calls.priceframe_audit_log_id`. For compliance reviews:

```sql
SELECT
  atc.tool_name,
  atc.args,
  atc.status,
  atc.priceframe_audit_log_id,
  atc.created_at,
  ar.user_id
FROM agent_tool_calls atc
JOIN agent_runs ar ON ar.id = atc.run_id
WHERE atc.tool_name IN ('create_quotation', 'submit_for_approval', ...)
  AND atc.created_at >= '2026-05-01'
ORDER BY atc.created_at DESC;
```

Cross-check with PriceFRAME's `audit_logs` table by id to ensure no audit gaps.

## 11.11 Sampling and retention

| Data | Retention recommendation | Why |
|---|---|---|
| Application logs | 14 days | Volume; debugging window |
| Prometheus metrics | 90 days | Trend analysis |
| Langfuse traces | 30 days | Prompt iteration |
| `agent_run_events` | 1 year+ | Audit, replay |
| `agent_audit_log` | 7 years+ | Compliance (varies by jurisdiction) |
| `agent_messages` | per data policy | User-visible; consider GDPR |

For `agent_run_events`, consider partitioning by month if volume grows:

```sql
ALTER TABLE agent_run_events PARTITION BY RANGE (created_at);
```

---

**Next:** [§12 Security & safety](./12-security-safety.md) — the threat model and hardening guide.
