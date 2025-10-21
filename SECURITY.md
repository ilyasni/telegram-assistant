# Security Policy

## Secrets and configuration
- Store secrets only in local  (not committed) or in CI/CD secret manager.
- Do not commit , database dumps, or session files to VCS.
- Regenerate JWT keys when migrating environments. Keep a single  across Supabase services.
- Provide a sanitized  for onboarding (no real secrets).

## Network and routing
- Expose Supabase Studio on a dedicated subdomain (e.g. ). Avoid path-based routing for Studio.
- Route all Supabase APIs through Kong. Do not expose internal services directly.
- Keep Kong CORS minimal; enable only required origins.
- Prefer short DNS TTL for Kong upstreams inside Docker to avoid stale IPs.

## Least privilege
- Use , ,  roles. Avoid granting superuser to application paths.
- Enable RLS and explicit policies before production.

## Transport security
- Terminate TLS at Caddy with automatic certificates. Enforce HSTS and security headers.

## Logging and observability
- Do not log secrets or tokens. Redact sensitive fields.
- Centralize access logs at the reverse proxy; enable per-service minimal logs.

## Dependency management
- Pin image versions (Kong, PostgREST, Studio, Postgres Meta) and update periodically.

## Incident response
- Revoke and rotate leaked keys immediately.
- Restore from backups following tested runbooks.
