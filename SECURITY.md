# Security Policy

## Reporting a vulnerability

**Do NOT open a public issue.** Use GitHub "Report a vulnerability"
(Security → Advisories) for `SolignusCorp/edma-core`. You will get a response
within 7 days. Please include: affected version/commit, minimal reproduction,
impact assessment.

## Scope

In scope: the deterministic kernel (dop/actions/verification/runtime),
port implementations shipped in this repo, secret handling in traces,
cross-org isolation.

Out of scope: your own plugin handlers (you register them — you own them),
the hosted platform, provider-side issues.

## Design assumptions you must keep

1. The MODEL is untrusted input: never feed model control fields into
   authorization decisions (the core already scrubs them — do not bypass).
2. Handlers must stay bounded: no eval/exec/shell; explicit, injectable code.
3. Unattended/public contexts should auto-DENY human-approval actions.
4. Never put credentials into payloads — traces scrub common prefixes, but
   prevention beats redaction.
