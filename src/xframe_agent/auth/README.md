# auth

JWT verification and PriceFRAME profile introspection.

Public API:

- `verify_priceframe_jwt(token, settings)`: validates PriceFRAME HS256 JWTs.
- `get_auth_context(...)`: FastAPI dependency that returns an `AuthContext`.
- `require_permission("agent.quotes.read")`: dependency factory for endpoint-level permission gates.
- `get_auth_context_from_profile(...)`: cached `/api/auth/profile` introspection.

Extension point: future endpoints depend on `AuthContext`; future tools must still re-check permissions inside execution.
