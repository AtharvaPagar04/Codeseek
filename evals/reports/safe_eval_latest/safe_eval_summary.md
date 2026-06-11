# CodeSeek Safe Evaluation Run Summary

**Overall Status**: `ERROR`
**Started At**: 2026-06-11T16:00:02.472321Z
**Finished At**: 2026-06-11T16:00:10.930956Z
**Duration**: 8.46 seconds
**Session ID**: `430247b88bcd464f8e181d9b9ecb9ceb`
**Expected Repo Root**: `/tmp/codeseek_repo_workspace/local/atharvapagar04_codeseek`
**Expected Collection**: `repository_chunks__local__atharvapagar04_codeseek`

## Execution Steps

| Step Name | Status | Return Code | Duration (seconds) |
|---|---|---|---|
| retrieval_eval | `ERROR` | 1 | 4.22 |
| conversation_eval | `ERROR` | 1 | 4.24 |
| eval_policy_summary | `ERROR` | -1 | 0.0 |

## Gating Policy Summary

**Hard Gate Status**: `ERROR`

### Hard Gate Failures

- Step failed: retrieval_eval
- Step failed: conversation_eval

### Warnings

- None

### Diagnostics

- None

### Recommendation

Upstream eval failed. Inspect step logs.
