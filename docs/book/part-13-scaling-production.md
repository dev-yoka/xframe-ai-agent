# Part 13 — Scaling and Production

> Six chapters on running xFRAME under real load. Performance targets, horizontal scaling, cost math, concurrency, database capacity, and SLOs/on-call.

---

## Chapter 85 — Performance Targets and Profiling

### 85.1 The SLO ladder

A realistic SLO ladder for xFRAME, ordered most-to-least-strict:

| Metric | Target | Tier |
|---|---|---|
| `/health` p99 | < 100ms | Tier 1 (must always meet) |
| HTTP API p95 (non-streaming) | < 500ms | Tier 1 |
| First SSE event after `/runs` POST | < 1s | Tier 1 |
| Time-to-first-token from LLM | < 2s | Tier 2 |
| Full simple read run (`get_quotation`) | < 5s | Tier 2 |
| Full multi-step Create Pricing Request | depends on user approvals | N/A |
| 99.9% availability per month | ~43 min downtime budget | Tier 1 |

Pick targets that match your business. Track them. Alert when violated.

### 85.2 Where time goes in a typical run

```
[User → API] 50ms      ← network + middleware
[API → Runner] <10ms   ← in-process
[Runner → LLM stream] 1200ms first token, ~80 tps after
[Runner → PriceFRAME] 200ms typical, 600ms p95
[PriceFRAME → Runner] ...
[Runner → DB writes] 5ms each (event append, message persist)
[Total per round] ~2-3s typical
```

The LLM and PriceFRAME dominate. Code time is negligible.

### 85.3 Profiling tools

| Tool | Purpose |
|---|---|
| `py-spy` | Sampling profiler; works on running processes |
| `pyinstrument` | Statistical profiler; nice flame graphs |
| Langfuse | LLM-side latency |
| Postgres `pg_stat_statements` | Slow queries |
| `htop` / `docker stats` | CPU, memory |
| `tcpdump` / Wireshark | Network latency |

### 85.4 Profiling agents with py-spy

```bash
docker exec -it xframe-agent uv run py-spy top --pid 1
```

Shows top functions consuming CPU **in real time**, without restarting the process. Look for:

- `_execute` (tool call work) — should be small; PriceFRAME wait dominates.
- Pydantic model validation — minor; usually fine.
- Event log appends — minor; should not be hot.

If you see something unexpected in the top 10 — surprising, investigate.

### 85.5 Common performance pitfalls

| Symptom | Likely cause | Fix |
|---|---|---|
| High DB CPU | Sequential scans on `agent_run_events` | Add index, check query plans |
| High agent CPU | JSON serialization in hot path | Reduce projection size; check for accidental large payloads |
| High memory | Long-running connections accumulating | Set connection limits; recycle workers |
| Slow startup | Alembic migrations running on every container | Use dedicated migration job |
| Slow first-token | Cold provider; wrong region | Pre-warm; switch to closer region |

### 85.6 Benchmark before optimizing

Don't optimize blindly. Measure first:

```bash
# Latency under realistic load
locust -f locustfile.py --host=... -u 50 -r 5 --run-time 5m --csv=baseline

# Run optimization

# Re-measure
locust -f locustfile.py --host=... -u 50 -r 5 --run-time 5m --csv=after
```

Compare p50, p95, p99. Did the change help? By how much? Worth the complexity?

### 85.7 What's slow that you can't fix

Be honest about the unfixable:

- **LLM TTFT** is mostly the vendor's problem. You can pick faster models (Gemini Flash vs Pro) or shorter prompts.
- **PriceFRAME slow endpoints** are a PriceFRAME-team conversation.
- **Network between regions** is physics.

Focus optimization on code you control. Plan around the rest.

### 🔑 Chapter 85 takeaways

- SLOs define what "good" means. Track them.
- LLM and PriceFRAME dominate latency; code time is negligible.
- Measure before optimizing.
- Some bottlenecks aren't yours to fix; design around them.

---

## Chapter 86 — Horizontal Scale: API + Worker

### 86.1 Stateless services scale horizontally

The agent's HTTP API is **stateless** between requests (state lives in Postgres). So you can run N replicas behind a load balancer and traffic distributes.

Same for the worker — each instance pulls jobs from Redis, processes them, repeats. Add more workers → more parallel runs.

```
Load balancer → 3 API replicas
                              ↓
                         Postgres
                         Redis ← 2 worker replicas
                                       ↓
                              Postgres
```

### 86.2 The bottleneck moves

As you scale, the bottleneck shifts:

| Scale | Bottleneck | Fix |
|---|---|---|
| 1 user | n/a | n/a |
| 100 users | LLM provider quota | Request quota increase |
| 1000 users | PriceFRAME backend | PriceFRAME team scales |
| 10K users | Postgres write throughput | Read replicas, partitioning |
| 100K users | Redis throughput | Cluster; or replace with something bigger |
| 1M users | Everything | Rearchitect |

xFRAME v1 is comfortable in the 1-1000 active-user range. Beyond that, expect to revisit.

### 86.3 Read replicas for Postgres

`agent_run_events` is read-heavy (SSE clients keep polling). At scale, point reads to a replica:

```python
class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://master/db"
    database_url_read: str | None = None   # optional read replica

# In db/session.py
def make_read_engine(settings):
    return create_async_engine(settings.database_url_read or settings.database_url)
```

Use the read engine for SSE queries. Writes still go to master.

⚠️ **Read replica lag** — a write may not be on the replica immediately. For SSE, this could cause "I just saw event seq 7 but the replica doesn't have it yet." Mitigate by polling the master briefly after a write, or by including a `lsn` (replication position) in responses.

xFRAME doesn't do this today. Not needed at current scale.

### 86.4 Connection pooling

Each API container has its own SQLAlchemy pool (default 10 connections). 3 replicas × 10 = 30 connections to Postgres. Add workers + their pools.

Postgres default `max_connections=100`. You'll hit it at ~10 containers.

Two options:

1. **Raise Postgres `max_connections`** — easy but burns more memory per connection.
2. **Add PgBouncer** — connection pooler in front of Postgres. Containers connect to PgBouncer; PgBouncer reuses a smaller pool to Postgres.

For scale, PgBouncer is the right tool. For v1, raise `max_connections` to 200 and move on.

### 86.5 Stateless sessions

xFRAME uses JWTs (not server-side sessions). Profile cache is per-process. So:

- A user's request can land on any replica — no sticky sessions needed.
- The profile cache is colder on each replica → more `/auth/profile` calls.
- Trade-off: simplicity (no sticky sessions) vs PriceFRAME load.

For now, the 60-second cache TTL means even at 3 replicas, each user adds ~1 extra `/profile` call per minute. Acceptable.

If PriceFRAME becomes the bottleneck, move profile cache to Redis (one source of truth across replicas).

### 86.6 Worker autoscaling

arq workers don't auto-scale natively. Manual or HPA-driven:

```yaml
# Kubernetes HPA based on queue depth
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: { name: xframe-worker }
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: xframe-worker }
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: External
      external:
        metric:
          name: redis_queue_length
          selector: { matchLabels: { queue: "agent-runs" } }
        target: { type: AverageValue, averageValue: "10" }
```

Requires a metric exporter to publish Redis queue depth. If you don't have one, scale manually based on observed backlog.

### 86.7 LLM provider quota scaling

When you outgrow default quotas:

- **Vertex**: file a quota increase request via GCP console. Approval typically same-day.
- **Anthropic**: contact sales to move up tier.
- **Multiple keys**: not generally recommended — vendors have rules against this. Talk to the vendor.

Plan ahead. A surprise quota throttle = users see errors.

### 🔑 Chapter 86 takeaways

- Stateless services scale by adding replicas.
- Bottleneck moves: LLM quota → PriceFRAME → Postgres → Redis.
- Connection pooling becomes important around 10 replicas.
- Worker autoscaling needs a queue-depth metric exporter.

---

## Chapter 87 — Cost Optimization: Token Math

### 87.1 The cost breakdown

A typical Create Pricing Request flow:

```
Step 1: Model call (~3K input + 50 output) → $0.0003 + $0.00002 = $0.00032
Step 2: Tool call (no LLM cost)              → $0
Step 3: Model call (~3.5K input + 30 output) → $0.00035
Step 4: Tool call                            → $0
Step 5: Model call (~4K input + 100 output)  → $0.00044
...
Total per flow: ~$0.002 - $0.010 depending on complexity
```

At Gemini 2.5 Flash rates ($0.10/M input, $0.40/M output, 2026). The structural prompt (system + tool catalog) is ~3K tokens repeated each step.

### 87.2 Where to optimize

Order by leverage:

1. **Context caching** (vendor side) — ~80% savings on repeated prefix. Roadmap §15.3.
2. **Shorter system prompt** — every token saved × every call forever.
3. **Tool catalog pruning** — `available_for` already filters; can't shrink much more.
4. **`project_for_model`** — reduce tool output size.
5. **Smaller model** — Gemini Flash already cheap; consider Haiku.
6. **Conversation summarization** — collapse history. Roadmap §15.2.
7. **Per-user budgets** — hard caps on heavy users. Roadmap §15.20.

### 87.3 Context caching math

Without caching, a 10-turn conversation re-sends the system prompt + tool catalog (3K tokens) on every turn:

```
10 turns × 3K = 30,000 input tokens just for structure
At $0.10/M = $0.003 per conversation
```

With caching (90% off on the cached portion):

```
First turn: 3K full price = $0.0003
Turns 2-10: 9 × 3K cached × 10% = $0.00027
Total: ~$0.00057 per conversation
```

**~5x cost reduction** on the structural overhead. Worth implementing when traffic justifies the engineering.

### 87.4 Per-user budgets (roadmap §15.20)

Today, only per-run hard ceiling ($0.60). A single user could submit 100 runs/day and burn $60. Per-user budgets would cap that:

```python
class AgentUserBudget(Base):
    user_id: int (PK)
    daily_limit_usd: float
    monthly_limit_usd: float
    current_day_spend: float
    current_month_spend: float
    reset_day_at: datetime
    reset_month_at: datetime
```

On each run start, check current spend vs limit. Reject if over.

Implementation: ~half-day. Worth doing once you have heavy users.

### 87.5 Cost monitoring

Daily query:

```sql
SELECT
  date_trunc('day', e.created_at) AS day,
  COUNT(*) AS runs,
  SUM((e.payload->'budget'->>'cost_usd')::numeric) AS total_cost,
  SUM((e.payload->'budget'->>'input_tokens')::int) AS in_tokens,
  SUM((e.payload->'budget'->>'output_tokens')::int) AS out_tokens
FROM agent_run_events e
WHERE e.event_type IN ('v1.run.completed', 'v1.run.error')
  AND e.created_at >= NOW() - INTERVAL '30 days'
GROUP BY day
ORDER BY day DESC;
```

Plot daily cost. Watch for sudden jumps (a runaway user, a prompt regression that calls more tools, a new feature that bumped cost).

### 87.6 Model selection trade-offs

| Model | Quality | Speed | Cost |
|---|---|---|---|
| Gemini 2.5 Flash | Good | Fast | Cheap |
| Gemini 2.5 Pro | Better | Slower | Pricier |
| Claude Haiku 4.5 | Good | Fast | Modest |
| Claude Sonnet 4.6 | Best | Slowest | Expensive |
| Claude Opus 4.7 | Best | Slowest | Most expensive |

xFRAME defaults to Gemini Flash. Sound choice for tool-calling agents — Flash handles function calling well at low cost.

Use Pro/Sonnet/Opus when you need:

- Complex reasoning chains.
- Multi-document understanding.
- Strict format adherence under adversarial input.

Most pricing-flow tasks fit Flash. Reserve premium models for premium use cases.

### 87.7 The "cheap default, smart fallback" pattern

A pattern worth considering (not implemented in xFRAME):

```
Default: Gemini Flash (fast + cheap)
If response indicates uncertainty (e.g., "I'm not sure..."), retry with Claude Sonnet
```

Roadmap §15.16. Saves 5-10x cost on simple queries, retains quality on complex.

### 🔑 Chapter 87 takeaways

- Today: ~$0.002-$0.010 per Create Pricing Request flow.
- Context caching (vendor side) = ~5x reduction. Roadmap.
- Per-user budgets prevent runaway spend.
- Pick model by task; Flash is the sound default.

---

## Chapter 88 — Concurrency and the Event Loop

### 88.1 Python asyncio basics

Python's `asyncio` is a **cooperative concurrency** model:

- One thread runs an event loop.
- Coroutines `await` to yield back to the loop.
- The loop schedules ready coroutines.

This is *not* parallelism (one CPU core at a time). It's *concurrency* — overlapping I/O waits.

For xFRAME, this is ideal: most time is spent waiting on LLM and PriceFRAME. Awaiting frees the loop to handle other requests.

### 88.2 What blocks the loop

| Operation | Blocks? | Mitigation |
|---|---|---|
| `await httpx.get(...)` | No | Good async I/O |
| `await session.execute(...)` (async SQLAlchemy) | No | Good async I/O |
| Synchronous DB call | **Yes** | Don't do it |
| CPU-heavy computation | **Yes** | Offload to a thread |
| `time.sleep()` | **Yes** | Use `await asyncio.sleep()` |
| `requests.get()` (sync HTTP) | **Yes** | Use httpx |

A single blocking call freezes the entire event loop. All concurrent requests stall.

xFRAME is rigorous about async-only — `requests` is not imported, only `httpx`. SQLAlchemy uses the async engine. Verify in any new code.

### 88.3 Connection pool sizing

```python
create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
)
```

Total = 30 connections per process. With 3 replicas = 90 max.

If your DB only allows 100 connections, you're close to the ceiling. Tune `pool_size` down or add PgBouncer.

httpx pool is separate; per `PriceFrameClient` instance. xFRAME creates one client per request handler — short-lived but creates a new pool each time. For higher throughput, a long-lived module-level client would be better.

### 88.4 The semaphore in tool dispatch

```python
sem = asyncio.Semaphore(self._settings.max_parallel_tool_calls)  # 3 by default

async def _exec_read(p, t, r, a):
    async with sem:
        return await self._execute_one(...)
```

Caps concurrent reader executions to `max_parallel_tool_calls`. Why?

- **PriceFRAME respects rate limits** — bursting 20 calls at once may trigger throttling.
- **Connection pool** — 20 concurrent httpx connections × multiple runs = exhaustion.
- **Memory** — each in-flight call carries state.

3 is conservative. Adjust upward if PriceFRAME can handle it.

### 88.5 Worker concurrency vs process concurrency

The arq worker has `max_jobs=4` per process. So one worker process runs 4 concurrent runs.

To get more concurrency:

- **Add worker processes** (more containers).
- **Raise `max_jobs`** — but each job has its own DB connections, memory. Don't push too high.

Test with realistic load before pinning numbers.

### 88.6 Uvicorn workers

Uvicorn defaults to 1 worker. For more:

```bash
uvicorn xframe_agent.main:app --workers 4 --host 0.0.0.0 --port 8000
```

Each worker is a separate process with its own event loop and connection pools. 4 workers = ~120 max DB connections per replica.

For containers: usually 1 worker per container. Scale by adding containers, not workers per container. Simpler operationally.

### 88.7 Long-running task patterns

If you need to do something heavy (e.g., embedding 1M documents), don't do it inline:

```python
# BAD — blocks the request
@router.post("/embed-all")
async def embed_all(...):
    for doc in fetch_all_docs():
        await embed(doc)  # could take hours
    return {}

# GOOD — enqueue
@router.post("/embed-all")
async def embed_all(settings: SettingsDep):
    await enqueue_job(settings, "embed_all_docs")
    return {"status": "queued"}
```

The worker (arq) handles the long job. The API stays responsive.

### 🔑 Chapter 88 takeaways

- asyncio = cooperative concurrency, not parallelism.
- Never block the loop — verify any new dep uses async I/O.
- Cap concurrent tool reads via semaphore (3 default).
- Long jobs → worker queue, not inline.

---

## Chapter 89 — Database Capacity Planning

### 89.1 Storage growth model

For each conversation:

| Table | Rows per conversation | Size per row |
|---|---|---|
| `agent_conversations` | 1 | ~200 bytes |
| `agent_messages` | 5-30 | ~500 bytes |
| `agent_runs` | 1-N (one per user message + approvals) | ~300 bytes |
| `agent_run_events` | ~10-30 per run | ~500 bytes (JSON payload) |
| `agent_tool_calls` | 1-N per run | ~1 KB (args + result) |
| `agent_audit_log` | 1 per write | ~500 bytes |

Per active conversation: ~20-50 KB.

At 1000 conversations/day: ~20-50 MB/day, ~7-18 GB/year.

A modest Postgres (50 GB) supports years of operation without partitioning.

### 89.2 Partitioning for `agent_run_events`

The event log is the fastest-growing table. At very high scale, partition by month:

```sql
CREATE TABLE agent_run_events (
    ...
) PARTITION BY RANGE (created_at);

CREATE TABLE agent_run_events_2026_05 PARTITION OF agent_run_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
```

Benefits:

- Drop old months via `DROP TABLE` (instant, no VACUUM).
- Queries on `created_at` range skip irrelevant partitions.
- Index maintenance is per-partition (smaller, faster).

Not needed below ~100M events.

### 89.3 Index maintenance

Postgres auto-vacuums. But heavy write tables benefit from occasional manual `VACUUM` + `ANALYZE`:

```sql
VACUUM (ANALYZE) agent_run_events;
```

Schedule weekly during low traffic.

For `pgvector` (if added), HNSW indexes need rebuilding occasionally as the table grows. Plan for this in your maintenance window.

### 89.4 Backup strategy

| Strategy | Frequency | Retention | Tool |
|---|---|---|---|
| Continuous WAL archiving | Continuous | Configurable | pgBackRest, WAL-G |
| Daily full backups | Daily 3am UTC | 30 days | `pg_dump` to S3 |
| Weekly cold copy | Sunday | 90 days | `pg_basebackup` |

Test restore quarterly. An untested backup is no backup.

For xFRAME v1: daily `pg_dump | gzip > backup-$(date +%F).sql.gz` to S3 is sufficient. Upgrade to continuous archiving as scale demands.

### 89.5 Disaster recovery

Recovery time objective (RTO) and recovery point objective (RPO):

- **RTO** — how long until we're back up?
- **RPO** — how much data can we lose?

For xFRAME:

- RTO target: 1 hour (acceptable for a B2B sales tool).
- RPO target: 5 minutes (continuous WAL archiving) or 24 hours (daily backups).

Document the procedure:

```
DR Procedure (xFRAME)

1. Confirm outage. Notify on-call.
2. Restore Postgres from latest backup.
3. Apply WAL up to last consistent point (if using WAL archiving).
4. Rotate any potentially compromised secrets.
5. Bring services up in order: postgres → redis → agent → worker.
6. Smoke test (curl /health).
7. Notify users.
8. Post-mortem within 5 business days.
```

### 89.6 Read replicas (advanced)

For SSE-heavy load, send reads to a replica:

```python
# Hypothetical pattern
db_writer = make_engine(settings.database_url)
db_reader = make_engine(settings.database_url_read or settings.database_url)

# In SSE handler
async def list_run_events(read_session, ...):
    # Read from replica
    ...
```

Caveat: replication lag means events written milliseconds ago may not be on the replica. Handle with retries or by reading from master for the most recent N seconds.

xFRAME doesn't have read replicas in v1. Add when load justifies.

### 🔑 Chapter 89 takeaways

- Storage grows ~20-50 KB per conversation.
- Partition `agent_run_events` at high scale.
- Backups: daily `pg_dump` for v1; continuous WAL when stakes rise.
- DR: practice restores quarterly.

---

## Chapter 90 — SLOs, Alerts, and On-Call

### 90.1 Service Level Objectives

SLOs define "what good means":

```
xFRAME AI Agent SLOs (target)

1. Availability:        99.9% over rolling 30 days   (~43 min downtime/month)
2. HTTP p95 latency:    < 500ms (non-streaming endpoints)
3. SSE TTFE:            < 1s for 95% of new runs
4. LLM TTFT:            < 2s for 95% of model calls
5. Error rate:          < 0.1% of all requests
6. Provider failover:   < 5% of runs trigger failover (Vertex healthy)
7. Cost per run:        median < $0.005
```

Numbers should match your business value. SaaS-tier targets are stricter; internal tools more lenient.

### 90.2 Service Level Indicators

For each SLO, an SLI — the actual measurement:

| SLO | SLI source |
|---|---|
| Availability | `up{job="xframe-agent"}` from Prometheus |
| HTTP p95 | `histogram_quantile(0.95, ...)` on `http_request_duration_seconds` |
| Error rate | `rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])` |
| LLM TTFT | Custom metric exported by `_call_provider` |
| Cost per run | `agent_run_events.payload->'budget'->>'cost_usd'` |

Some come from Prometheus, some from the durable event log.

### 90.3 Error budgets

If SLO is 99.9% availability, the error budget is **0.1% downtime = ~43 min/month**.

If you've used 30 min this month (e.g., a botched deploy), you have 13 min of budget left. Be cautious with risky changes.

If you've burned the budget, **stop shipping non-essential changes**. Focus on reliability.

This is the heart of SRE practice. Adopt it gradually.

### 90.4 Alerting

| Alert | Condition | Action |
|---|---|---|
| Service down | `up == 0 for 1 min` | Page on-call |
| Error rate high | `rate(5xx) > 1% for 5 min` | Page on-call |
| Latency spike | `p95 > 1s for 5 min` | Notify (no page) |
| Provider failover | `mark_unhealthy events > 0` | Notify |
| Disk full | `node_filesystem_avail_bytes < 10 GiB` | Page |
| Postgres connections high | `pg_stat_activity > 80% of max` | Notify |
| Run error rate | `rate(v1.run.error[5m]) > 5%` | Page |

Tune thresholds for your tolerance. Too sensitive = alert fatigue.

### 90.5 On-call rotation

Even at 1-person scale, define **who answers the page**. For a team:

- **Primary**: takes the first page.
- **Secondary**: backup if primary unreachable.
- **Rotate weekly**.
- **Document runbooks** for each common alert.

Tools: PagerDuty, OpsGenie, VictorOps. Cheaper: a Slack channel + push notifications.

### 90.6 Runbooks

Per-alert procedure documents. Example for "Service down":

```
Alert: xFRAME service down

Severity: P1

Steps:
1. Confirm: curl https://agent.example.com/api/v1/agent/health
2. Check container: docker ps | grep xframe
3. Logs: docker logs xframe-agent --tail 100
4. Common causes:
   - DB connection: check postgres health
   - OOM kill: check `dmesg` or container exit reason
   - Bad deploy: roll back to previous tag (see DEPLOY runbook)
5. If unresolved in 15 min, escalate.
6. Post-mortem required within 5 business days.
```

Store runbooks in the same repo as code — they need to evolve together.

### 90.7 Post-mortems

After every significant incident:

1. **Timeline** — what happened and when.
2. **Impact** — users affected, duration, revenue impact if any.
3. **Root cause** — the actual cause, not just symptoms.
4. **Resolution** — what fixed it.
5. **Action items** — concrete, owned, dated.
6. **Lessons learned** — what we'd do differently.

Blameless culture. Focus on systems, not people.

### 🔑 Chapter 90 takeaways

- Define SLOs that match your business; track SLIs from Prometheus + event log.
- Error budgets gate risky deploys.
- Per-alert runbooks; rotate on-call; do post-mortems.
- Reliability is a discipline, not a feature.

---

### Part 13 wrap-up

You can now operate xFRAME at scale: profile bottlenecks, scale horizontally, optimize costs, handle concurrency, plan database capacity, and run an on-call rotation.

### ✍️ Part 13 exercises

1. Define your SLOs. Pick 4. Justify each number.
2. Estimate the cost per active user per month at 100K conversations/year. Assume 5 turns per conversation, Gemini Flash pricing.
3. Write a runbook for "PriceFRAME 5xx rate above 5%."

### 📚 Part 13 further reading

- Google SRE Book (free online).
- "Implementing Service Level Objectives" (Alex Hidalgo).
- Prometheus best practices for SLOs.

---

**End of Part 13.**

**Next:** [Part 14 — Advanced AI Engineering](./part-14-advanced-ai-engineering.md).
