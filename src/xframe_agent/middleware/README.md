# middleware

ASGI middleware used by the FastAPI app.

Public API:

- `RequestIdMiddleware`: propagates or creates `X-Request-ID`.
- `RateLimitMiddleware`: Redis sliding-window limiter with memory fallback for local/test use.

Extension point: add auth-independent cross-cutting middleware here. Endpoint-specific permission checks belong in `auth.dependencies`.
