# Prompt 06 — Initialize AI Control Center and synchronize Atlas reports

**Purpose:** create or initialize `convergentaru/ai-control-center`, preserve the original Atlas reports, publish a safe repeatable synchronization script, and verify the result. This prompt is an execution specification; do not assume it has already been run.

## Tasks

1. Inspect these source reports if present:
   - `/home/oleg/hermes-project-atlas-progress.md`
   - `/home/oleg/hermes-project-atlas-report.md`
2. Check Git, GitHub CLI/authentication, and access to `convergentaru/ai-control-center`. Search before creating anything. If the repo exists, reuse it. If it does not, create a private repository only if permitted. Never bypass permissions.
3. Use a dedicated local working directory, preferably `/home/oleg/projects/ai-control-center`. Do not work inside or alter unrelated repositories.
4. Create a small, documented repository structure, reusing existing files where possible:
   - `README.md`
   - `CURRENT_STATE.md`
   - `PROJECT_INDEX.md`
   - `TASKS.md`
   - `DECISIONS.md`
   - `memory/PROJECT_CONTEXT.md`
   - `memory/KNOWN_CONSTRAINTS.md`
   - `memory/CHATGPT_MEMORY_PROPOSALS.md`
   - `reports/atlas/`
   - `inbox/README.md`
   - `outbox/README.md`
   - `scripts/sync-atlas-reports.sh`
5. Copy the two Atlas reports into `reports/atlas/` without changing or deleting the originals. Record source paths and hashes. Fill state files only with facts verified from evidence; clearly label assumptions and unknowns.
6. Create a repeatable `scripts/sync-atlas-reports.sh` that safely copies/updates the source reports, creates needed directories, checks inputs, reports missing files, and avoids overwriting unrelated content. Make it idempotent. Do not embed secrets or tokens.
7. Validate shell syntax and test the script twice. Inspect `git status` and diffs before committing. Do not use `git reset --hard`, `git clean`, force-push, destructive deletion, or history rewriting. Do not stage unrelated files.
8. Commit only the intended files with message `feat: initialize AI control center and sync Atlas reports`, push normally, then re-read the published files from GitHub and verify contents/hashes against local sources. Report the commit SHA and verification results.
9. If any step is blocked, complete the safe local preparation and report the exact blocker and required human action. Do not claim a step succeeded unless it was verified.

## Safety boundaries

- Preserve all original reports and known-good files.
- Never publish secrets, credentials, tokens, private keys, or unrelated personal data.
- Do not modify other repositories or perform a broad cleanup.
- Do not automatically promote proposed memory items into approved personal memory.
- No unrelated installations, service changes, or configuration changes.

## Final report

List the local script path, repository URL, files created/updated, commit SHA, source and published-file verification, results of two sync runs, blockers, and what remains manual. Keep the scope to this initial MVP.
