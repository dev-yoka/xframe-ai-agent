# observability

Tracing and metrics adapters for the agent service.

Public API:

- `get_langfuse_client(settings)`: optional Langfuse client factory.
- `setup_metrics(app, settings)`: Prometheus FastAPI instrumentation.

Extension point: future provider and tool code should emit Langfuse traces and Prometheus metrics through helpers added here.
