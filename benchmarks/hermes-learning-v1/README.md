# Hermes Learning Benchmark v1

This is a small, task-specific regression suite for the failure patterns already observed while using the local Hermes CLI. It deliberately measures **behavioral execution and honest evidence**, not whether Hermes recalls a fact.

## Why this suite exists

The referenced Reddit discussion distinguishes memory benchmarks such as LoCoMo/LongMemEval from a benchmark of whether repeated interaction changes future behavior. Reflexio's GDPVal experiment uses cold → warm → warm-plus-learning runs, comparing the incremental value of a persisted recipe against the agent's own warm baseline. Our first stage borrows that experimental structure but focuses on the user's actual pain points: context/model troubleshooting, claim-vs-evidence, user handoffs, and safe hardware operations.

This is a pilot, not a broad claim of general intelligence or recursive self-improvement. A suite of 10 cases cannot establish generalization. It should grow from real, redacted regressions.

## Files

- `cases.jsonl`: ten hand-curated cases with success conditions and safety level.
- Each case must be run against the same Hermes CLI version, profile, model alias, tool availability, workspace and environment in baseline and candidate conditions.
- Run at least 3 paired repeats per case. Save actual prompts, model outputs, tool traces, commands, stdout/stderr, exit codes and artifact hashes to a private local evidence directory. Do not put user transcripts or local machine configuration in Git.
- The evaluator should mark pass/fail from deterministic checks where possible. For subjective output quality, a human may review a sample; an LLM judge alone cannot promote a candidate.
- A task that depends on actual physical USB hardware must not be simulated as a successful hardware result. Its acceptance check can validate safe preflight behavior, but physical boot/SSH success must be separately evidenced.

## Core metrics

1. Task pass rate across fixed cases.
2. Safety violations (must remain zero).
3. Regressions where a baseline-passing case fails in the candidate.
4. Actual tools/commands executed vs. claimed, including exit codes.
5. Number of user handoffs when the agent could safely execute a read-only step itself.
6. Median steps/tokens/latency among comparable successful runs.
7. Number of lessons that pass a repeat test and are later retrieved for a relevant task.
8. Irrelevant lesson retrieval and extra context overhead.

For changes that leave task success unchanged, this MVP demands a measurable ≥10% reduction in steps/tokens/latency before it calls the candidate an improvement. For any safety regression, reject immediately. See [Local Verified Learning Loop](../../docs/LOCAL-VERIFIED-LEARNING-LOOP-RU.md) for the gate implementation and limitations.

## Experimental protocol

- **Cold:** clean task workspace, baseline instructions, no newly learned playbook.
- **Warm baseline:** repeat the identical task with only the current known-good Hermes learning state.
- **Candidate:** repeat it with a proposed playbook, isolated from the active profile.
- Compare candidate with the warm baseline, not just cold. Keep model/runtime/environment fixed. Do not change sampling or task wording between baseline and candidate.
- Keep the held-out cases separate from those used to draft the candidate instruction.
- Report all cases, including null results and failures. Do not drop inconvenient results after seeing them; document exclusions before running.

## Current integration status

The dataset is defined. The repository's gate validates paired run receipts and local evidence-file hashes. The current MVP does **not** automatically execute Hermes or verify the semantic correctness of evidence files; a local Hermes adapter and deterministic case evaluators are the next integration stage. Never claim this suite has been run against Oleg's live Hermes until actual run artifacts exist.
