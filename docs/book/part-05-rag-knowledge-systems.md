# Part 5 — RAG and Knowledge Systems (Concept + Future Direction)

> Five chapters teaching retrieval-augmented generation as a discipline, then sketching how xFRAME *would* add a RAG layer. The codebase doesn't ship RAG today; this part is forward-looking and conceptual. Read it to understand the trade-offs you'd face if/when you implement it.

---

## Chapter 35 — The Chunking Decision Tree

### 35.1 Why chunk at all?

You can't dump a 10,000-page knowledge base into a model's context window. Even with 1M-token windows, you'd burn $20 per query. You need to **find the relevant 1% and inject only that**.

That requires *finding* relevant pieces. Embeddings can rank text by similarity, but only at the granularity you embed at. Embed a whole book and you get one vector for the whole book — useless for finding a specific paragraph.

So you **chunk**: split documents into smaller pieces, embed each chunk, store with metadata, search at chunk granularity.

### 35.2 The chunking spectrum

| Strategy | Chunk size | When to use |
|---|---|---|
| **Fixed character/token windows** | 200–500 tokens | Quick to implement; works for prose |
| **Sentence-based** | Variable | Preserves syntactic boundaries |
| **Paragraph-based** | Variable | Preserves semantic units in well-formatted text |
| **Recursive / hierarchical** | Tree of chunks | Long structured docs (books, manuals) |
| **Semantic chunking** | Variable | Embed sliding windows; split on similarity dips |
| **Structure-aware** | By section / header | Markdown, HTML, code |

For xFRAME's hypothetical use case ("search past quotations"), each quotation is already structured. You'd treat one quotation as one chunk, or split by section (header / corridor list / approvals).

### 35.3 Overlap

A common technique: chunks share 10–20% of their boundaries.

```
Chunk 1: "...the customer is Acme Corp. The quote covers India and Vietnam corridors..."
Chunk 2: "...covers India and Vietnam corridors. The applied FX spread is 0.02 for India..."
```

Overlap reduces "edge effects" where the relevant sentence sits across a boundary. Costs more storage but improves recall.

### 35.4 Chunking trade-offs

| Bigger chunks | Smaller chunks |
|---|---|
| Fewer chunks to index | Better recall (granular matches) |
| Higher context efficiency per query | Higher latency (more vectors to search) |
| Coarser similarity | Worse for snippet-level questions |
| Cheaper embedding cost | More storage |

The sweet spot for prose: **400–800 tokens with 50–100 token overlap**.

For xFRAME quotations: **one chunk per quote section** (header, corridors, fees, approval history). Use rich metadata for filtering, narrow to top-K via embedding rank.

### 35.5 Metadata is the unsung hero

Every chunk should carry metadata:

```json
{
  "text": "Quote 5042 for Acme Corp covers...",
  "embedding": [0.12, ..., 0.87],
  "quote_id": 5042,
  "customer_id": 42,
  "currency": "USD",
  "spread": 0.02,
  "owner_user_id": 7,
  "created_at": "2026-05-20"
}
```

The embedding rank gives you the top-K. Metadata filters narrow further: "only my quotes," "only India corridor," "only in the last quarter."

This is where vector search beats pure keyword: combine semantic *and* structured filters in one query.

---

## Chapter 36 — Embedding Models in 2026

### 36.1 The landscape

| Model | Provider | Dim | Cost | Notes |
|---|---|---|---|---|
| `text-embedding-3-large` | OpenAI | 3072 (configurable down) | $0.13/M tokens | Strong default |
| `text-embedding-005` | Google Vertex | 768 | $0.025/M tokens | Best price/perf for production |
| `voyage-3-large` | Voyage AI | 1024 | $0.18/M tokens | Highly ranked on MTEB |
| `BAAI/bge-large-en` | Open (HuggingFace) | 1024 | Free + GPU time | Best open option for English |
| `intfloat/e5-large-v2` | Open | 1024 | Free + GPU time | Strong on retrieval |
| `nomic-embed-text-v1.5` | Open | 768 | Free + GPU time | Good multilingual |

For xFRAME extending into RAG: **Vertex `text-embedding-005`** is the natural fit — already in the GCP ecosystem.

### 36.2 What dimensionality means

Bigger vectors = more "room" to encode meaning. But:

- 3072-D vector takes 4× storage of 768-D.
- Similarity search latency scales with dimensionality.
- Beyond 1024-D, gains plateau for most use cases.

OpenAI's `text-embedding-3-large` lets you truncate to e.g. 1024-D with minimal quality loss — useful if storage is constrained.

### 36.3 Embedding the right thing

A common error: embed user **queries** with the same model as documents but at the same dimensionality. Mismatch ranks badly.

Best practice:

1. Pick one embedding model.
2. Use it for both documents (offline) and queries (online).
3. Use the *same version* — model updates change the embedding space.

If you have to migrate to a new model: **re-embed everything**. There's no shortcut.

### 36.4 Caching query embeddings

If your users often ask the same questions ("show my recent quotes"), you can cache query embeddings keyed by the normalized query string. Saves 10-50ms per query. Cheap optimization.

For xFRAME, query patterns are likely **idiosyncratic** ("show me Acme's deal from last month") — cache hit rate would be low. Skip the cache; spend the engineering effort elsewhere.

### 36.5 Multimodal embeddings (sidebar)

Some embedding models accept images, audio, or video. E.g., CLIP variants. Not relevant to xFRAME's text-only flow, but if you ever needed "find quotes whose PDF attachments match this scanned page," that's a multimodal embedding job.

---

## Chapter 37 — Vector Database Choices for Python Shops

### 37.1 The contenders

| DB | Architecture | When to pick |
|---|---|---|
| **pgvector** | Postgres extension | Already have Postgres, want simplicity |
| **Qdrant** | Self-host, Rust | Best perf for self-host, rich filtering |
| **Weaviate** | Self-host, Go | Want REST/GraphQL out of the box |
| **Pinecone** | Managed SaaS | Don't want to operate vector infra |
| **Milvus** | Self-host, complex | 100M+ vectors, high QPS |
| **Chroma** | Local-first, Python | Prototyping, small projects |
| **LanceDB** | Embedded, Rust | "SQLite for vectors" model |
| **OpenSearch / Elasticsearch** | Hybrid search | Already on ES; want lexical + vector |

For xFRAME: **pgvector** is the obvious choice. Postgres is already in the stack. No new operational concerns.

### 37.2 pgvector basics

Add the extension:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Define a table:

```sql
CREATE TABLE quote_embeddings (
    id          UUID PRIMARY KEY,
    quote_id    BIGINT NOT NULL,
    user_id     INTEGER NOT NULL,
    chunk_text  TEXT NOT NULL,
    embedding   VECTOR(768) NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX ON quote_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON quote_embeddings (user_id, created_at);
```

Search:

```sql
SELECT chunk_text, quote_id, 1 - (embedding <=> $1) AS similarity
FROM quote_embeddings
WHERE user_id = $2
  AND created_at > NOW() - INTERVAL '90 days'
ORDER BY embedding <=> $1
LIMIT 10;
```

`<=>` is cosine distance (smaller = closer). The HNSW index makes this O(log n) instead of O(n).

### 37.3 Index choices

pgvector supports two index types:

- **HNSW** (Hierarchical Navigable Small World): higher build cost, faster queries, slightly approximate.
- **IVFFlat**: lower build cost, slower queries, also approximate.

HNSW is the modern default. Use `IVFFlat` only if you have very large datasets and limited memory for the HNSW graph.

### 37.4 Why not Pinecone?

Managed = easier ops, lower customization. For xFRAME:

- Pinecone adds another vendor + bill + auth surface.
- pgvector is plenty for the scale (<100M vectors).
- Keeping vector and relational data colocated lets us join SQL filters with embedding rank in one query.

For a startup with no Postgres expertise, Pinecone makes sense. For xFRAME, pgvector wins.

### 37.5 Hybrid search

You'll often want **lexical + semantic** ranking. PostgreSQL's full-text search (`tsvector`, `to_tsquery`) combined with `pgvector` rank can be merged with Reciprocal Rank Fusion (RRF):

```sql
WITH lex AS (
    SELECT id, ts_rank_cd(tsv, q) AS r
    FROM docs, to_tsquery('english', $1) q
    WHERE tsv @@ q
    ORDER BY r DESC LIMIT 50
), sem AS (
    SELECT id, 1 - (embedding <=> $2) AS r
    FROM docs
    ORDER BY embedding <=> $2 LIMIT 50
), combined AS (
    SELECT id, 1.0 / (60 + ROW_NUMBER() OVER (ORDER BY r DESC)) AS rrf
    FROM lex
    UNION ALL
    SELECT id, 1.0 / (60 + ROW_NUMBER() OVER (ORDER BY r DESC))
    FROM sem
)
SELECT id, SUM(rrf) AS score
FROM combined
GROUP BY id
ORDER BY score DESC LIMIT 10;
```

Catches both "literal customer ID" (sparse) and "semantic match for description" (dense). The constant 60 is a tunable smoothing parameter.

---

## Chapter 38 — Designing the xFRAME RAG Layer (Hypothetical)

### 38.1 Use case to motivate

> "Show me deals similar to the one I'm currently quoting."

A sales rep is building a quotation for an East Africa corridor. They'd benefit from seeing prior quotations they did for similar corridors — to remember the spreads, fees, customer feedback.

A traditional search ("filter by corridor") works but is brittle (different rep names, slight description variation). Embeddings let "similar" be defined semantically.

### 38.2 The data model

Add two tables:

```python
class QuoteEmbedding(Base):
    __tablename__ = "agent_quote_embeddings"
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    quote_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(768), nullable=False)
    metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (Index("ix_quote_embeddings_hnsw", "embedding", postgresql_using="hnsw"),)
```

And a job that populates it:

```python
async def embed_recent_quotes(session, user_id):
    quotes = await fetch_recent_quotes_from_priceframe(user_id, days=180)
    for quote in quotes:
        summary = build_quote_summary(quote)  # text representation
        vec = await embed_text(summary)        # call Vertex embeddings API
        existing = await session.get(QuoteEmbedding, quote.id)
        if existing:
            existing.summary = summary
            existing.embedding = vec
        else:
            session.add(QuoteEmbedding(
                id=ulid(), quote_id=quote.id, user_id=user_id,
                summary=summary, embedding=vec,
                metadata={"currency": quote.currency, "spread": quote.spread, ...},
            ))
    await session.commit()
```

Run nightly or after each quote is finalized.

### 38.3 The new tool

```python
class SearchMyHistoryInput(BaseModel):
    query: str = Field(min_length=3)
    limit: int = Field(default=5, ge=1, le=20)

class SearchMyHistoryTool(ToolDefinition[SearchMyHistoryInput, JsonOutput]):
    name = "search_my_history"
    description = "Search the user's past quotations by semantic similarity to a query."
    input_model = SearchMyHistoryInput
    output_model = JsonOutput
    permission = "agent.quotes.read"
    risk: ClassVar[Risk] = "READ"
    cost_class: ClassVar[CostClass] = "cheap"

    async def _execute(self, args, ctx, _priceframe):
        query_vec = await embed_text(args.query)
        async with session_factory() as session:
            results = await session.execute(
                select(QuoteEmbedding.quote_id, QuoteEmbedding.summary,
                       QuoteEmbedding.metadata)
                .where(QuoteEmbedding.user_id == ctx.user_id)
                .order_by(QuoteEmbedding.embedding.cosine_distance(query_vec))
                .limit(args.limit)
            )
            return JsonOutput(data=[
                {"quote_id": r[0], "summary": r[1], "metadata": r[2]}
                for r in results.all()
            ])
```

Register it in `REGISTERED_TOOLS`. Done — the LLM can now call `search_my_history` when the user asks about past deals.

### 38.4 What changes in the system prompt

Add to the prompt:

> When the user asks about "past deals," "similar quotes," or "what I did before," call `search_my_history` first. Use the results to ground your recommendations.

That's it. The model picks up the tool from the `tools` parameter; the prompt nudges when to use it.

### 38.5 What changes operationally

- **New service**: an offline embedder. Could be an arq cron job or a separate worker.
- **New monitoring**: vector DB size, embedding API cost, search latency.
- **New compliance concern**: embeddings of past quotes carry derived PII. Apply the same retention as `agent_messages`.
- **New cost**: embedding API + vector storage. Estimate: ~$0.001 per quote, ~$0.0001 per search query.

### 38.6 What doesn't change

- The runner. The tool fits the existing contract.
- The HITL flow. Searches are reads — no approval.
- The audit log. Reads aren't audited (though some auditors may want this).

This is the beauty of the tool abstraction: new capabilities don't perturb the core loop.

---

## Chapter 39 — Hallucination Reduction Patterns

### 39.1 The four levers

When the model can hallucinate, you have four tools:

1. **Grounding** — give the model real data (RAG, tool calls).
2. **Citation requirements** — tell the model to cite its sources from the grounded data.
3. **Verification** — after the model speaks, verify against ground truth.
4. **Human review** — HITL.

xFRAME uses **all four** to varying degrees:

| Lever | xFRAME implementation |
|---|---|
| Grounding | 12 tools fetch live data; no "guess the FX rate" |
| Citation | System prompt rules; tool result IDs flow into model text |
| Verification | Pydantic validation; PriceFRAME's own server-side validation |
| HITL | Required before all writes |

### 39.2 RAG-specific patterns

For RAG specifically:

**Pattern 1: "If you don't see the answer in the context, say so."**

System prompt: "Answer only from the provided context. If the context doesn't contain the answer, say 'I don't have information on that.'"

**Pattern 2: Require quote-the-source.**

System prompt: "After each claim, cite the source chunk: `[chunk_id: ...]`."

**Pattern 3: Verify cited chunks exist.**

After the model responds, regex-extract `[chunk_id: ...]`. Verify each exists in the retrieval results. If not — hallucination.

**Pattern 4: Lower the temperature.**

RAG flows benefit from low temperature (0.0–0.2). You want the model to follow context, not be creative.

### 39.3 When grounding fails (no relevant chunks found)

The model still has to respond. Options:

- **Refuse**: "No relevant information available."
- **Use parametric knowledge**: model answers from training data (risky — hallucination potential).
- **Ask clarifying questions**: "Could you rephrase or provide more detail?"

xFRAME's parallel: when a tool returns "no results," the model should ask the user to refine. The §15.4 error-feedback path enables this.

### 39.4 Eval as hallucination defense

Even with all the above, hallucinations leak. The fix: **catch them in evals**.

Golden traces (Chapter 71) should include:

- Cases where the right answer is "I don't know."
- Cases where the model should pause for human.
- Cases where prior agents hallucinated, with annotations.

Run evals on every model upgrade.

### 39.5 The mental model

Hallucination is a **feature** of next-token prediction, not a **bug**. The model is doing what it's trained to do — sample plausible text. "Truth" isn't in its objective function.

So: you cannot eliminate hallucination by prompting. You can only **constrain** the system around it. Grounding + verification + HITL + evals is the layered defense.

### 🔑 Part 5 takeaways

- Chunking is the first design decision in RAG. Pick by content structure, not dogma.
- pgvector is the right vector DB for xFRAME if/when RAG is added.
- The new tool drops into the existing contract — RAG doesn't perturb the runner.
- Hallucination reduction is layered: grounding + citations + verification + HITL + evals.

### ✍️ Part 5 exercises

1. Sketch the chunking strategy for "PriceFRAME quotations as text." How do you treat the corridor list, the approvals history, the comments? One chunk or many?
2. Compare pgvector and Qdrant for xFRAME's hypothetical RAG. Make the case for each.
3. Design 3 eval cases that would catch hallucination in the `search_my_history` tool.

### 📚 Part 5 further reading

- "Lost in the Middle: How Language Models Use Long Contexts" (Liu et al., 2023).
- pgvector documentation and benchmarks.
- "Retrieval-Augmented Generation for Large Language Models: A Survey" (Gao et al.).

---

**End of Part 5.**

**Next:** [Part 6 — Prompt Engineering Deep Dive](./part-06-prompt-engineering.md).
