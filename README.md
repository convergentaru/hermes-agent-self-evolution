# 🧬 Hermes Agent Self-Evolution

**Evolutionary self-improvement for [Hermes Agent](https://github.com/NousResearch/hermes-agent).**

Hermes Agent Self-Evolution uses DSPy + GEPA (Genetic-Pareto Prompt Evolution) to automatically evolve and optimize Hermes Agent's skills, tool descriptions, system prompts, and code — producing measurably better versions through reflective evolutionary search.

**No GPU training required.** Everything operates via API calls — mutating text, evaluating results, and selecting the best variants. ~$2-10 per optimization run.

## How It Works

```
Read current skill/prompt/tool ──► Generate eval dataset
                                        │
                                        ▼
                                   GEPA Optimizer ◄── Execution traces
                                        │                    ▲
                                        ▼                    │
                                   Candidate variants ──► Evaluate
                                        │
                                   Constraint gates (tests, size limits, benchmarks)
                                        │
                                        ▼
                                   Best variant ──► PR against hermes-agent
```

GEPA reads execution traces to understand *why* things fail (not just that they failed), then proposes targeted improvements. ICLR 2026 Oral, MIT licensed.

## Quick Start

```bash
# Install
git clone https://github.com/NousResearch/hermes-agent-self-evolution.git
cd hermes-agent-self-evolution
pip install -e ".[dev]"

# Point at your hermes-agent repo
export HERMES_AGENT_REPO=~/.hermes/hermes-agent

# Evolve a skill (synthetic eval data)
python -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --eval-source synthetic

# Or use real session history from Claude Code, Copilot, and Hermes
python -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --eval-source sessiondb
```

## What It Optimizes

| Phase | Target | Engine | Status |
|-------|--------|--------|--------|
| **Phase 1** | Skill files (SKILL.md) | DSPy + GEPA | ✅ Implemented |
| **Phase 2** | Tool descriptions | DSPy + GEPA | 🔲 Planned |
| **Phase 3** | System prompt sections | DSPy + GEPA | 🔲 Planned |
| **Phase 4** | Tool implementation code | Darwinian Evolver | 🔲 Planned |
| **Phase 5** | Continuous improvement loop | Automated pipeline | 🔲 Planned |

## Engines

| Engine | What It Does | License |
|--------|-------------|---------|
| **[DSPy](https://github.com/stanfordnlp/dspy) + [GEPA](https://github.com/gepa-ai/gepa)** | Reflective prompt evolution — reads execution traces, proposes targeted mutations | MIT |
| **[Darwinian Evolver](https://github.com/imbue-ai/darwinian_evolver)** | Code evolution with Git-based organisms | AGPL v3 (external CLI only) |

## Guardrails

Every evolved variant must pass:
1. **Full test suite** — `pytest tests/ -q` must pass 100%
2. **Size limits** — Skills ≤15KB, tool descriptions ≤500 chars
3. **Caching compatibility** — No mid-conversation changes
4. **Semantic preservation** — Must not drift from original purpose
5. **PR review** — All changes go through human review, never direct commit

## Practical Masterclass Roadmap (RU)

A Russian-language implementation plan based on Akshay Pachaar's public post and video:

- [Implementation roadmap](docs/HERMES-AGENT-MASTERCLASS-ROADMAP-RU.md) — identity, memory, progressive skill loading, curator, evaluation, and gated evolution.
- [Read-only skill audit tool](tools/audit_skills.py) — inventories SKILL.md files, hashes, oversized files, and exact duplicates without changing files.

Sources: [X post](https://x.com/akshay_pachaar/status/2054564519280804028) · [YouTube crash course](https://www.youtube.com/watch?v=bNp6YcKBLgY)

## Full Plan

See [PLAN.md](PLAN.md) for the complete architecture, evaluation data strategy, constraints, benchmarks integration, and phased timeline.

## Local Verified Learning Loop

A local-first MVP to turn observed corrections and task outcomes into **proposed** playbooks, compare baseline vs. candidate results using paired receipts, reject safety/regression failures, and retrieve only explicitly approved learnings. It uses Python's standard library and stores data locally by default; it does not train model weights or automatically rewrite active Hermes skills.

- [Russian implementation guide](docs/LOCAL-VERIFIED-LEARNING-LOOP-RU.md)
- [Prompt for local Hermes integration](docs/HERMES-LEARNING-LOOP-INTEGRATION-PROMPT-RU.md)
- CLI: `python -m evolution.learning.loop --help`
- Tests: `tests/core/test_learning_loop.py`

Current limitation: this is the evidence/store/gate layer, not yet an automatic importer of Hermes session history or an automatic runner of the active Hermes model. The integration prompt requires an audit of the installed Hermes version before wiring either path.

### Practical Acer Rescue runbook

- [Acer Rescue: USB, physical boot, Tailscale + SSH](docs/runbooks/ACER-RESCUE-USB-AND-SSH-RU.md) — current step-by-step operational runbook; if the USB has already been written, do not rewrite it by default.
- [Prompt to sync into JetBrains, Obsidian, and GBrain](docs/HERMES-RUNBOOK-SYNC-PROMPT-RU.md) — local Hermes instructions to place the runbook in the project tree and Obsidian vault, update the existing indexes, verify checksums, and synchronize only the relevant files.
- [Behavioral benchmark v1](benchmarks/hermes-learning-v1/README.md) — concrete pass/fail cases for honest execution evidence, USB safety, context troubleshooting, and measured learning.

## License

MIT — © 2026 Nous Research
