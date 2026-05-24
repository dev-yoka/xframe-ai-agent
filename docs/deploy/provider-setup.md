# Provider Setup

This document covers provisioning AI provider credentials for xFRAME AI Agent.

---

## Current test provider: Gemini Developer API

For the Render test deployment, use the Gemini Developer API key path. This
matches the direct `generativelanguage.googleapis.com` curl flow.

```env
GEMINI_API_KEY=<google-ai-studio-or-gemini-api-key>
GEMINI_API_BASE_URL=https://generativelanguage.googleapis.com/v1beta
GEMINI_API_MAX_RETRIES=2
GEMINI_API_RETRY_BASE_DELAY_SECONDS=0.25
DEFAULT_MODEL=gemini-2.5-flash
```

The provider retries transient Google API responses such as 429 and 5xx,
including temporary "high demand" 503s, before failing over.

For this test setup, leave these unset unless they are fully configured and
funded:

```env
GEMINI_VERTEX_PROJECT=
ANTHROPIC_API_KEY=
```

If `GEMINI_VERTEX_PROJECT` is set without valid Google Application Default
Credentials, the router will try Vertex and fail with an ADC error. If
`ANTHROPIC_API_KEY` is set on an account with no credits, the router will try
Anthropic and fail with a billing error.

---

## Primary Provider: Google Vertex AI (Gemini)

### 1. Provision a GCP Service Account

1. Open the [GCP Console](https://console.cloud.google.com) and navigate to
   **IAM & Admin → Service Accounts**.
2. Click **Create Service Account**.  Use a name such as `xframe-agent-vertex`.
3. Grant the service account the **Vertex AI User** role
   (`roles/aiplatform.user`).
4. Click **Done**, then open the new account and go to **Keys → Add Key →
   Create new key → JSON**.  Download the key file.

### 2. Mount the Key as a Docker Secret

Add the key file as a Docker secret named `gcp.json` so the container reads it
at runtime without baking credentials into the image:

```yaml
# docker-compose.yml (or Docker Swarm / Kubernetes equivalent)
secrets:
  gcp_key:
    file: ./secrets/gcp.json

services:
  agent:
    image: xframe-agent:latest
    secrets:
      - source: gcp_key
        target: /var/run/secrets/gcp.json
```

For plain `docker run`:

```bash
docker run \
  --mount type=bind,source=/path/to/gcp.json,target=/var/run/secrets/gcp.json,readonly \
  -e GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/gcp.json \
  -e GEMINI_VERTEX_PROJECT=my-gcp-project-id \
  xframe-agent:latest
```

### 3. Set the `GOOGLE_APPLICATION_CREDENTIALS` Environment Variable

```env
GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/gcp.json
GEMINI_VERTEX_PROJECT=my-gcp-project-id
# Optional: override the default region (us-central1)
GEMINI_VERTEX_LOCATION=us-central1
```

Setting `GEMINI_VERTEX_PROJECT` together with valid Application Default
Credentials is **all that is required** — no code changes are needed.

---

## Optional Fallback Provider: Anthropic

If Vertex AI is unavailable the failover router will attempt Anthropic next,
provided the API key is set:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

No other configuration is needed.  The router quarantines unhealthy providers
automatically and retries on the next request.

---

## Smoke Test

After deploying, verify that the primary provider responds end-to-end.  The
test below follows the same fixture pattern used in `tests/test_runner.py` but
targets a real provider by running against a live database.

```bash
# Export credentials so the SDK picks them up
export GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/gcp.json
export GEMINI_VERTEX_PROJECT=my-gcp-project-id

# Run only the runner tests (fast, single round-trip)
uv run pytest tests/test_create_pricing_request_flow.py -v
```

A passing run confirms:
- The service account has Vertex AI User role.
- The key file is mounted and readable.
- `GEMINI_VERTEX_PROJECT` is set to the correct project.
- The failover router selects the Gemini Vertex provider successfully.

---

## Summary

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes (Gemini Developer API) | API-key provider for Render/test deployments |
| `GEMINI_API_BASE_URL` | No | Defaults to Google `v1beta` endpoint |
| `GEMINI_API_MAX_RETRIES` | No | Retries transient Gemini API 429/5xx responses |
| `GEMINI_API_RETRY_BASE_DELAY_SECONDS` | No | Base exponential retry delay |
| `GOOGLE_APPLICATION_CREDENTIALS` | Yes (Vertex) | Path to GCP service account key JSON |
| `GEMINI_VERTEX_PROJECT` | Yes (Vertex) | GCP project ID that has Vertex AI enabled |
| `GEMINI_VERTEX_LOCATION` | No | Vertex AI region (default: `us-central1`) |
| `ANTHROPIC_API_KEY` | No | Enables Anthropic as a fallback provider |
