# SOSFiler Codex Intelligence Flywheel

**Status:** Active
**Last verified:** 2026-05-15
**Scope:** SOSFiler repo-local memory, research, and implementation workflow

## Purpose

This document makes SOSFiler a first-class participant in the Swan intelligence flywheel. Repo-local docs remain the implementation source of truth, while the Swan vault carries durable cross-project memory.

## Memory Stack

| Layer | Use |
|---|---|
| Repo-local docs | Implementation truth for SOSFiler behavior, tests, APIs, deployment, and state automation code. |
| Swan vault | Durable cross-project memory, project context, lessons learned, and session summaries. |
| Context7 | Current docs for libraries, SDKs, APIs, CLIs, cloud tools, and framework behavior. |
| NotebookLM | Deep state filing/compliance research across official sources, PDFs, YouTube, Reddit, and portal walkthroughs. |
| Codex skills/scripts | Repeatable expert workflows such as competitive analysis, distribution, pricing, security review, Supabase, and webapp testing. |
| Swan Command Center | Hot memory, vault search, task history, NotebookLM bridge, and capability registry. |

## Start-of-Work Checklist

For non-trivial SOSFiler work:

- Read the relevant code and tests.
- Check `docs/research_status.md`, `docs/research_worker.md`, and any state-specific data under `data/regulatory/`.
- Search recent vault sessions under `/Users/stevenswan/project-folders/swan-vault/03-Sessions/Code/`.
- Use Context7 before relying on memory for external APIs/libraries.
- Use NotebookLM when the task depends on state portal behavior or fragmented real-world guidance.
- Choose the relevant specialist skill before producing strategy or high-risk implementation.

## Research Standard For State Filing Automation

Each state/portal research packet should capture:

- Official filing portal URL and authority.
- Entity types supported.
- Required fields, forms, fees, payment behavior, login/MFA/CAPTCHA behavior.
- Automation path: API, form POST, browser automation, or operator-only.
- Failure modes and operator handoff triggers.
- Evidence artifacts: screenshots, source URLs, PDFs, notes, or test runs.
- Confidence, evidence level, and last verified date.

Use this metadata when committing durable research:

```yaml
type: state-filing-research
project: sosfiler
state: CA
portal:
confidence: high
evidence_level: primary-source-tested
last_verified: 2026-05-15
status: active
```

## Durable Write Targets

- Project context: `/Users/stevenswan/project-folders/swan-vault/01-Projects/SOSFiler/CONTEXT.md`
- Session summaries: `/Users/stevenswan/project-folders/swan-vault/03-Sessions/Code/`
- System rule: `/Users/stevenswan/project-folders/swan-vault/00-System/rules/codex-intelligence-flywheel.md`
- Repo implementation docs: `docs/`
- State filing data: `data/regulatory/`

Do not write secrets to the vault or repo docs. Do not duplicate facts that are better expressed in executable code or tests.

## Promotion Rule

If a state, filing type, support workflow, or research task repeats, promote it into one of:

- A test.
- A script.
- A state automation module.
- A NotebookLM notebook.
- A vault playbook.
- A Codex skill.
- A Swan Command Center tool.

## Completion Standard

Before ending substantial work:

- State what changed.
- State how it was verified.
- Capture reusable lessons.
- Identify the intended durable-memory write path if a direct vault write was not possible.
