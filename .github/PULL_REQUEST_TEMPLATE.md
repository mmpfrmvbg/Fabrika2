## Summary

- Describe the change.

## Risk tier

- [ ] **trivial** — typo / rename, minimal risk
- [ ] **normal** — feature or fix with tests
- [ ] **high** — DB schema, auth, CI, secrets (requires ChatGPT + DeepSeek review before Codex per [.comet/PROCESS.md](.comet/PROCESS.md))

## Process

- [ ] Read [.comet/PROCESS.md](.comet/PROCESS.md) and [.comet/AGENT_CONTEXT.md](.comet/AGENT_CONTEXT.md); if Context-Date is stale vs `origin/main`, resync per PROCESS before merge-related work.
- [ ] Pre-merge: from repo root run [`.comet/check_pr.ps1`](.comet/check_pr.ps1) (optionally `-PullRequestNumber <N>` if `gh` is installed). Require **LOCAL GATES: PASS** and **CI STATUS: green** before merge unless emergency path is recorded in DECISIONS + AGENT_CONTEXT.

## Checklist

- [ ] Tests pass locally (`check_pr.ps1` / pytest) and required CI jobs are green
- [ ] Coverage is maintained (minimum threshold still met)
- [ ] README updated if needed
