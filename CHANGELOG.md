# Changelog

## 0.1.2 — prerelease

### Fixed

- Replace the hardcoded 64 KiB HTTP request gate with bounded streaming-aware buffering, default/hard ceiling 1 MiB; configurable downward with `PANEL_MAX_REQUEST_BYTES`.
- Check declared and actual bytes without trusting Content-Length; reject malformed/conflicting headers, compressed bodies, and oversize input before JSON parsing/upstream dispatch.
- Sanitize validation errors so rejected messages/tool schemas are not echoed; unsupported Agent tools remain explicit errors rather than being silently dropped.
- Added 19 body-limit regression cases; all 94 Python and four Node tests pass. Verified a real >64 KiB valid request returning `BODY_70K_OK`, the HTTPS oversize safety guard, and normal Minis text inference.
- Document direct Venice API setup for Minis; no Venice key, account access, or successful inference is implied by public model discovery.

## 0.1.1 — prerelease

### Fixed

- Accept the `max_completion_tokens` and `stream_options.include_usage` defaults sent by Minis model tests and standard text-model calls; these previously failed validation with HTTP 422.
- Keep validation strict for option types and unknown fields. Unsupported tools, sampling controls, and legacy `max_tokens` remain rejected.
- Explicitly disclose that completion-token budgets are advisory and real token usage is unavailable using response headers and `notrack.warnings`; do not invent usage or pretend to enforce a native token cap.
- Added nine offline compatibility regressions; all 75 Python and four Node tests pass.
- Verified real standard `minis-model-use` requests, the 128-token model-test request, a Persian question, and a full SSE stream ending with `finish_reason: stop` and `[DONE]` on the deployed service. This verifies text connectivity, not full Agent/tool support.

## 0.1.0 — prerelease

Initial public prerelease; interfaces and the unofficial upstream may change.

### Added

- Single-operator browser panel with shared persistent history and a text-only subset of OpenAI chat-completions, streaming, and `session_id` continuation.
- Required `PANEL_TOKEN`: browser HTTP Basic (`panel` / token), programmatic Bearer authentication, and fail-closed protection for all `/api/*` and `/v1/*` routes.
- Linux/macOS virtualenv installer with private randomly generated `.env`, loopback defaults, and optional non-root systemd user service only with `--service`.
- Non-root Docker/Compose alternative with loopback publication and an owned named data volume; optional explicit HTTPS quick-tunnel helper without automatic cloudflared installation.
- English documentation, Persian quickstart, security policy, MIT license, and Python 3.11/3.12 CI with unittest, offline Node frontend tests, and JavaScript/shell syntax checks.
- Clean temporary Python 3.12 virtualenv install, repeat-install preservation, local launch/authentication, and simulated tunnel-preflight checks verified during release preparation.

### Corrected release scope

- One persistent server-issued web identity and global request serialization; no identity rotation, quota farming, or rate-limit bypass. Rate limits are returned to callers.
- Conservative observed character budgets (input 4000; system/instructions about 1980), not exact upstream guarantees. Over-limit histories error rather than silent truncation.
- Unsupported parameters (including tools, max_tokens, temperature) are rejected; no fabricated token `usage`.
- A/B remain legacy experimental routing labels, not verified model identities; B previously failed and availability is not promised.
- Official paid API credentials/limits are separate from this web-backend wrapper. No model architecture/version or unfiltered-output guarantee.
- CLI-Anything is credited as researched inspiration only; no generator execution, installation, or integration is claimed.

### Caveats

- Prerelease, not multi-tenant or independently security-audited; local identity/history persists in plaintext.
- Upstream may be undocumented or change without notice. No live model availability guarantee.
- Docker build/runtime has not been verified in the release-preparation environment.
