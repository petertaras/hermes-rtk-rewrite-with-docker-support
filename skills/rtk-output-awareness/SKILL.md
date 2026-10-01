---
name: rtk-output-awareness
description: "Use when RTK may filter terminal output."
version: 0.1.0
author: Peter Taras (petertaras), Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [rtk, terminal, output, code-review, diagnostics]
    related_skills: []
---

# RTK output awareness

RTK can automatically rewrite terminal commands into output-filtering commands.
Use summaries for orientation, not as proof that every relevant detail was shown.
This skill provides a per-command raw-output path; it does not disable plugins.

## When to Use

- Running terminal commands with the rtk-rewrite plugin enabled.
- Reviewing complete diffs, security findings, detailed errors or exact output.
- Investigating suspiciously short output, missing fields or omitted context.
- Do not use this as permission to bypass tool approvals or execution isolation.

## Prerequisites

Use the `terminal` tool within the authorised execution target. RTK must be
available there for `rtk proxy`; host-side `RTK_BIN` does not select the target's
executable. Docker/SSH targets need their own RTK installation.

## Procedure

1. Decide whether a summary is sufficient **before** execution. For complete
   code review, security evidence or exact-output comparisons, request raw output
   from the start. A convincing summary does not prove nothing was omitted.
2. Prefix a single command with `rtk proxy` to execute without RTK filtering.
   For example, use `terminal(command="rtk proxy git diff --no-ext-diff")` or
   `terminal(command="rtk proxy git status --porcelain=v1")`. Keep the original
   working directory, arguments and execution target. RTK still tracks usage.
3. Apply the prefix separately to each command needing raw output. A prefix
   before the first command does not cover later shell commands after `&&`,
   `;` or a pipe. Prefer separate terminal calls over compound shell workarounds.
4. If a filtered result looks incomplete, repeat it through `rtk proxy` only
   after confirming the command is safe to repeat. Do not repeat writes,
   deployments, migrations, sends or other state-changing operations just to
   recover output. Inspect existing logs/artifacts or ask for approval instead.
5. Check the result for Hermes/tool output truncation. `rtk proxy` removes only
   RTK filtering, not tool size limits, pagination, application filters or model
   context compression. When a full-output file is provided, use `read_file`
   and follow its continuation offsets until the relevant content is covered.
6. Base conclusions on the complete evidence required by the task. If that
   evidence is still unavailable, state the gap rather than claiming completeness.

## Pitfalls

- The adapter has no semantic completeness detector or automatic raw retry.
  Do not assume the bot can identify details it never received.
- `rtk proxy` is not a safety bypass: normal approvals, credentials, permissions
  and sandbox boundaries still apply. Do not switch execution backends or use
  another execution tool to evade them.
- Do not globally disable the plugin, alter PATH or change profile configuration
  merely to inspect one result. Use the per-command escape hatch.
- If target RTK is missing, report that prerequisite. Do not install software or
  change remote/container configuration without the relevant authority.
- Prefer `read_file` and `search_files` for file inspection. This adapter targets
  `terminal`; those tools have their own output limits, which still need checking.

## Verification

- On a new RTK version, inspect `terminal(command="rtk proxy --help")` and verify
  `terminal(command="rtk rewrite 'rtk proxy git status --porcelain=v1'")`
  preserves the proxy command. The example quoting is for POSIX shells; adapt
  argument quoting to the active shell, not the execution semantics.
- Confirm the raw command ran on the intended target and inspect its exit code.
- Confirm output is neither summarised by RTK nor incompletely read after tool
  truncation. A successful exit code alone does not establish complete evidence.
- Report what was checked and any remaining coverage limits, without calling a
  filtered summary a full review.
