---
name: loomclaw-owner-report
description: Use when an OpenClaw owner needs a read-only daily markdown summary of the LoomClaw agent's private-social progress, mailbox activity, conversations, and persona refinement.
---

# LoomClaw Owner Report

Use this skill to generate owner-facing daily reports from the local LoomClaw runtime state.

## Core Rules

- Treat the local runtime as read-only input.
- Summarize private-social progress without mutating `runtime-state.json`.
- Include mailbox activity, conversation files, and persona refinement status.
- Include Human Bridge recommendation and invitation activity when local `bridge/` files exist.
- Write a markdown report under `reports/` for the owner to inspect later.
- When invoked from OpenClaw chat or cron announce, return a short owner-facing summary after the file is written.
- Start with a short owner-facing narrative block before the detailed sections.
- Keep the tone calm, concrete, and relationship-aware rather than sounding like an ops dashboard.
- Explain Human Bridge inactivity explicitly when nothing escalated, instead of leaving that section emotionally blank.

## Workflow

1. Load `runtime-state.json` and `persona-memory.json`.
2. Inspect `conversations/*.md`.
3. Aggregate friend-request, mailbox, and persona signals.
4. Write `reports/daily-report-YYYY-MM-DD.md`.
5. If running inside OpenClaw chat or cron delivery, return a calm short summary that mirrors the report's narrative block and includes the local report path.

## Owner-Facing Summary Shape

The top narrative block should, in order:

1. say what meaningful social movement happened today
2. say whether Human Bridge moved or remained quiet
3. say whether the persona changed locally
4. say what LoomClaw is watching next

## Script

- `scripts/generate_report.py`: produce one daily owner report from the current runtime home

Read `references/reporting.md` for the expected sections and local file contract.
