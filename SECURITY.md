# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | ✅ |

## Reporting a vulnerability

Please **do not** open a public issue. Use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Security" tab → "Report a vulnerability").

Include affected versions, reproduction steps and impact. We aim to acknowledge within 3 business
days and to agree on a disclosure timeline with you.

## Scope

In scope: the CLI and library — e.g. code execution via rule/exam/YAML input, secret values leaking
into reports, signature or verification bypasses, unsafe handling of agent/judge credentials.

Out of scope: the correctness of regulatory mappings (please open a normal issue), and findings
that require an already-compromised signing key or host.
