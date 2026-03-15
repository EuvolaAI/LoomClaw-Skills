# LoomClaw Skills

## Overview
This directory is the split-ready skills subtree for LoomClaw runtime skills and evals. It is intended to become the standalone `loomclaw-skills` repository during split publish.

## Local Development
- Run `make install` to install the editable package and dev dependencies.
- Keep local experiments inside `workspace/`, which is intentionally ignored.

## Test Commands
- `make eval`
- `python -m pytest evals -q`

## Release / Split Publish Notes
This subtree should remain self-contained after split publish. Skill packaging, eval commands, and CI are written relative to the split repo root, and `workspace/` must never be published.
