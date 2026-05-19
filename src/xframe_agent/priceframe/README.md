# priceframe

Typed HTTP client wrapper for PriceFRAME REST APIs.

Public API:

- `PriceFrameClient.get_profile(jwt_raw)`: calls `GET /api/auth/profile` with JWT pass-through.
- `PriceFrameError` subclasses: structured upstream error mapping for route dependencies and tools.

Extension point: future agent tools add narrowly typed methods here. The client must keep passing the end user's JWT through to PriceFRAME; it must not use DB access or elevated service credentials for user-scoped actions.
