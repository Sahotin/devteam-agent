---
name: safe-code-change
description: Safely implement a feature change or bug fix in an existing repository using DevTeam task context, code search, memory, bounded edits, tests, diff review, and evidence-based repair. Use only when a DevTeam task_id and a locally accessible repository are available.
---

# Safe Code Change

Use the DevTeam MCP server as a read-only context source. It does not authorize
file writes, shell commands, Git operations, workflow control, or memory writes.
Use only the host's separately sandboxed native tools for those operations, and
preserve the user's existing authorization boundaries.

## Required inputs

Obtain a `task_id` and a concrete change request. Reproduction steps, expected
behavior, acceptance criteria, constraints, and approved test commands are useful
when available. Do not ask the user for `project_id`, `workspace_root`, permissions,
agent identity, or an internal tool name; the MCP server derives those values.

## Workflow

1. Call `get_project_context(task_id)` before reading or changing files.
   - Confirm its project root is the repository opened by the host.
   - If `active_execution` is present, do not modify or index the repository while
     that DevTeam execution is queued or running.
   - Treat current disk content as fact; artifacts and memory are context.
2. Search before planning.
   - Call `search_code` with the symptom, relevant identifiers, and likely entry
     points. Inspect definitions, callers, configuration, entry points, and tests.
   - Call `query_memory` for prior failures, architecture decisions, project
     conventions, and repairs already shown to be ineffective.
   - Do not modify code from one search hit or an unverified hypothesis.
3. Present a compact plan before mutation. For each planned file, state the
   evidence, intended change, verification, and risk. Keep the change minimal;
   avoid unrelated refactors, formatting, renames, or dependency upgrades.
4. Re-read each target immediately before editing with the host's native file
   tools. Modify only files in the confirmed project root. Never access `.env`,
   private keys, dependency directories, generated output, or paths outside it.
5. Require explicit confirmation before installing dependencies, changing lock
   files, deleting files, applying migrations, committing, pushing, or taking any
   other material external action.
6. Validate with the repository's existing commands. Run focused checks first,
   then the relevant build, type check, and full tests. Never delete tests, weaken
   assertions, disable type checking, or use pass-with-no-tests behavior to hide a
   failure.
7. Review the final diff for requirement coverage, callers missed, error paths,
   security, test adequacy, and unexpected files.
8. Repair only failures supported by current test or review evidence. Re-search
   and re-read before each repair. Do not repeat an already failed repair. Stop
   after two repair rounds by default and never exceed three; report the blocker
   instead of claiming completion.

## Completion conditions

Report completion only when the change maps to evidence, the diff matches the
plan, relevant checks pass, no unexpected files changed, and no unresolved
blocking issue remains. List every check actually run. Mark unavailable checks as
unverified; do not infer success from missing tooling or an unexecuted command.
