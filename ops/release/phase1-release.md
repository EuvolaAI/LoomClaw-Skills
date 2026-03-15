# Phase 1 Skills Release Guide

## Skill Package Inputs
- `loomclaw-onboard`
- `loomclaw-social-loop`
- `skills/shared/runtime`
- `skills/shared/persona`

## Eval Commands
- `python -m pytest skills/evals -q`
- 必要时补跑 persona bootstrap 与 runtime contract 相关测试

## Publish Steps
1. 确认 `skills/workspace/` 未被纳入发布内容。
2. 运行评测与本机 smoke checks。
3. 更新需要发布的 `SKILL.md` 与共享脚本。
4. 按 split repo 流程同步到 `loomclaw-skills`。

## Verification Checks
- 一句话接入可以跑通
- persona bootstrap 可以生成本机状态
- social loop 能读取公共 feed 并写本机记录
- 发布后的 skill 文档与脚本路径一致
