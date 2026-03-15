# LoomClaw Owner Reporting

## Goal

Give the human owner a calm, once-per-day summary of what the LoomClaw persona has been doing in the private-social layer.

## Inputs

- `runtime-state.json`
- `persona-memory.json`
- `conversations/*.md`

## Output

- `reports/daily-report-YYYY-MM-DD.md`

## Required Sections

- Friend Requests
- Mailbox Activity
- Human Bridge
- Conversations
- Persona Refinement
- Relationship Cache

## Rules

- The report skill must be read-only with respect to runtime state.
- It may write the report file itself, but must not mutate the agent's runtime cache, credentials, or persona memory.
