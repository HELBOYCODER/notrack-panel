# Security policy

## Status

0.1.2 is a prerelease, not an audited or production-hardened multi-tenant service. Security fixes target the current prerelease; there is no guaranteed response time or long-term support promise. Update before exposing it remotely.

## Report a vulnerability

Do not put tokens, cookies, plaintext conversations, or working exploit details in a public issue. Use the repository's **Security → Advisories → Report a vulnerability** facility if enabled:

https://github.com/HELBOYCODER/notrack-panel/security/advisories/new

Include the affected version, deployment type, impact, and minimal redacted reproduction. If private reporting is unavailable, open an issue requesting a private contact **without disclosing vulnerability details**. No private email address or bug-bounty program is implied.

## Trust boundaries

- One operator, one shared token, shared history. This is **not multi-tenant**; all holders of the token can access/manage sessions. Session IDs are not authorization boundaries.
- A non-empty `PANEL_TOKEN` is mandatory. Every `/api/*` and `/v1/*` route, including health/model listings, must reject unauthenticated access. Missing configuration fails closed.
- Browser authentication is HTTP Basic: username `panel`, password `PANEL_TOKEN`. Programmatic API authentication is Bearer with the same token. Basic is base64, not encryption.
- Bind to loopback (`127.0.0.1:8080`) by default. Use HTTPS for remote access, preferably with an additional access-control layer. A random tunnel URL is not authentication. A tunnel operator terminates TLS and becomes a trusted intermediary.
- `.env` is mode 600 on Unix. Identity/session files in `data/` are persistent **plaintext**, ignored by Git but not encrypted. Protect host accounts, file permissions, disk and backups. Review staged content before publishing. Windows requires appropriate ACLs.
- The upstream web service receives prompts and may retain conversations. Deleting local state does not guarantee upstream deletion. Do not assume anonymous web access is confidential or unlogged.
- One persistent server-issued identity and one globally serialized server process; no UID rotation, quota evasion, multiple workers, or replicas. Rate-limit responses are surfaced, not bypassed.
- Model output is untrusted. Do not execute it as code or render unsanitized HTML. Speaker names are unverified routing labels, not provenance guarantees.

## Operational checklist

1. Keep the generated token private; never put it in a URL, issue, screenshot or shell command argument. Never use a documented/example password.
2. Keep `.env`, `data/`, logs and backups out of public repositories and web roots. Ignore rules do not remove files that were already tracked or historical secrets.
3. Limit host access. Run under a non-root account. Optional systemd user units set `NoNewPrivileges`, `PrivateTmp`, and private umask; their availability depends on OS support.
4. Containers run as UID/GID 10001. New named data volumes inherit matching ownership; prepare ownership explicitly for existing volumes/bind mounts. Docker administrators can inspect environment secrets.
5. Before a tunnel, verify unauthenticated `/`, `/api/health` and `/v1/models` challenge for authentication, then verify authenticated access. `tunnel.sh quick` checks these but is not a security audit.
6. If a token leaks, stop public exposure, replace it locally with a strong random token, update clients/environment overrides, and restart all server/container instances. Investigate local and upstream history exposure.
7. Upgrade dependencies and rerun the offline tests. Passing tests does not guarantee upstream behavior or make public deployment risk-free.

The official paid NoTrack API has separate credentials and terms: https://notrack.ai/api/docs. Do not use a panel token as an official API key.
