# DT Beach Supply Control — Current State

Last updated: 2026-07-19

## Repository

Local repository:
`/Users/harrison/Downloads/DT_Beach_Supply_Control_Fable5:/Application`

Remote repository:
`https://github.com/Harrison0407/odysseus.git`

Active branch:
`integration/dt-beach-supply-control-1.0.0`

Upstream:
`origin/integration/dt-beach-supply-control-1.0.0`

Current repository HEAD:
`8b7e102`

Current HEAD purpose:
Documentation-only commit adding this current-state document.

Working tree at the time of this update:
Contains untracked evidence-package documents only. No tracked application-code modifications are present.

## Verified Functional Baseline

Latest verified application-code commit:
`58889c8`

Commit title:
`Document the Controlled Transparency / Confidentiality foundation (part 7/7)`

Tests reported at that verified functional baseline:
`455/455 passing`

Migrations at that verified functional baseline:
`clean`

The later commit `8b7e102` does not change application behavior; it only adds this current-state document.

## Latest Completed Milestone

Controlled Transparency, Commercial Confidentiality & Authorization Foundation.

## Architectural Rules

- MarketMatch is the platform name.
- PostgreSQL is the source of truth.
- Party is separate from role.
- Role is separate from capability.
- Authorization must occur before retrieval, translation, summarization, AI processing, or derived-artifact generation.
- Original content must never be overwritten by translations or derivatives.
- Commercial confidentiality must be enforced server-side.
- Confidential fields must not reach the browser when unauthorized.
- Missing source information must never be invented.
- Existing completed modules must not be rebuilt unnecessarily.
- Workflows must remain configurable rather than hard-coded.

## Localization Foundation

Canonical interface locales:

- `es` — Español
- `en` — English
- `zh-Hans` — 简体中文

Language and timezone are separate settings.

DT Beach operational timezone:
`America/Santo_Domingo`

## Git Workflow

Normal push command:

```bash
git push
```
