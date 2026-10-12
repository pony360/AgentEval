# Contributing to AgentEval

Thanks for helping! Rules, exam papers, collectors, translations and bug fixes are all welcome.

## Ground rules

- Be kind; see the [Code of Conduct](CODE_OF_CONDUCT.md).
- Open an issue before large changes so we can agree on the approach.
- Never commit secrets, real customer data, or real cluster snapshots. Use fictional names and `example.com` hosts.

## Development

```bash
git clone https://github.com/Kecheng8/agenteval.git && cd agenteval
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
agenteval rules validate
```

New Python files start with:

```python
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
```

## Contributing rules

1. Read [docs/rule-schema.md](docs/rule-schema.md).
2. Put the rule in the right pack (or propose a new pack with `pack.yaml`), and keep IDs unique.
3. Add a test or fixture showing it **pass** and **fail**.
4. Cite precise sources and leave `verified: false`. A maintainer with relevant expertise
   flips it to `true` after review; please say in the PR what you checked.
5. Translations in `i18n/zh.yaml` are appreciated but optional.

## Contributing exam papers

See [docs/exams.md](docs/exams.md). Use fictional businesses, keep cases self-contained (all facts
in `context` or `tool_mocks`), mark must-never-happen behaviour as `critical`, and include both a
passing and a failing demo transcript in tests when possible.

## Developer Certificate of Origin (DCO)

All commits must be signed off, certifying the [DCO 1.1](https://developercertificate.org/):

```bash
git commit -s -m "Add FED-SEC-008: require readiness probes"
```

This adds `Signed-off-by: Your Name <you@example.com>`. By contributing you agree your work is
licensed under the Apache License 2.0.

## Pull requests

- Keep PRs focused; update docs and `CHANGELOG.md` (Unreleased) when behaviour changes.
- CI must pass (`pytest` on Python 3.11–3.13, `agenteval rules validate`).
