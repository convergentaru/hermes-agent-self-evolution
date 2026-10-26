"""Evolve a Hermes Agent skill using DSPy + GEPA.

Usage:
    python -m evolution.skills.evolve_skill --skill github-code-review --iterations 10
    python -m evolution.skills.evolve_skill --skill arxiv --eval-source golden --dataset datasets/skills/arxiv/
"""

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

import click
import dspy
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from evolution.core.config import EvolutionConfig, resolve_hermes_agent_path
from evolution.core.dataset_builder import SyntheticDatasetBuilder, EvalDataset, GoldenDatasetLoader
from evolution.core.external_importers import build_dataset_from_external
from evolution.core.fitness import skill_fitness_metric, LLMJudge, FitnessScore
from evolution.core.constraints import ConstraintValidator
from evolution.skills.skill_module import (
    SkillModule,
    load_skill,
    find_skill,
    reassemble_skill,
)

console = Console()


def evolve(
    skill_name: str,
    max_metric_calls: int = 40,
    eval_source: str = "synthetic",
    dataset_path: Optional[str] = None,
    optimizer_model: str = "openai/gpt-4.1",
    eval_model: str = "openai/gpt-4.1-mini",
    hermes_repo: Optional[str] = None,
    run_tests: bool = False,
    dry_run: bool = False,
    api_base: Optional[str] = None,
    api_key_env: Optional[str] = "OPENAI_API_KEY",
    num_threads: int = 1,
):
    """Main evolution function — orchestrates the full optimization loop."""

    api_key = os.environ.get(api_key_env) if api_key_env else None
    if api_base and not api_key:
        # Local OpenAI-compatible endpoints such as llama.cpp normally do not
        # authenticate, but OpenAI-compatible clients may require a non-empty key.
        api_key = "local-no-auth"

    config = EvolutionConfig(
        hermes_agent_path=resolve_hermes_agent_path(hermes_repo),
        optimizer_model=optimizer_model,
        eval_model=eval_model,
        judge_model=eval_model,  # Use same model for dataset generation
        run_pytest=run_tests,
        api_base=api_base,
        api_key=api_key,
    )

    # ── 1. Find and load the skill ──────────────────────────────────────
    console.print(f"\n[bold cyan]🧬 Hermes Agent Self-Evolution[/bold cyan] — Evolving skill: [bold]{skill_name}[/bold]\n")

    skill_path = find_skill(skill_name, config.hermes_agent_path)
    if not skill_path:
        console.print(f"[red]✗ Skill '{skill_name}' not found in {config.hermes_agent_path / 'skills'}[/red]")
        sys.exit(1)

    skill = load_skill(skill_path)
    console.print(f"  Loaded: {skill_path.relative_to(config.hermes_agent_path)}")
    console.print(f"  Name: {skill['name']}")
    console.print(f"  Size: {len(skill['raw']):,} chars")
    console.print(f"  Description: {skill['description'][:80]}...")

    if dry_run:
        console.print(f"\n[bold green]DRY RUN — setup validated successfully.[/bold green]")
        console.print(f"  Would generate eval dataset (source: {eval_source})")
        console.print(f"  Would run GEPA optimization (budget: {max_metric_calls} metric calls)")
        console.print("  GEPA is mandatory; no fallback optimizer will be substituted.")
        console.print(f"  Would validate constraints and create PR")
        return

    # ── 2. Build or load evaluation dataset ─────────────────────────────
    console.print(f"\n[bold]Building evaluation dataset[/bold] (source: {eval_source})")

    if eval_source == "golden" and dataset_path:
        dataset = GoldenDatasetLoader.load(Path(dataset_path))
        console.print(f"  Loaded golden dataset: {len(dataset.all_examples)} examples")
    elif eval_source == "sessiondb":
        save_path = Path(dataset_path) if dataset_path else Path("datasets") / "skills" / skill_name
        dataset = build_dataset_from_external(
            skill_name=skill_name,
            skill_text=skill["raw"],
            sources=["claude-code", "copilot", "hermes"],
            output_path=save_path,
            model=eval_model,
            api_base=api_base,
            api_key=api_key,
        )
        if not dataset.all_examples:
            console.print("[red]✗ No relevant examples found from session history[/red]")
            sys.exit(1)
        console.print(f"  Mined {len(dataset.all_examples)} examples from session history")
    elif eval_source == "synthetic":
        builder = SyntheticDatasetBuilder(config)
        dataset = builder.generate(
            artifact_text=skill["raw"],
            artifact_type="skill",
        )
        # Save for reuse
        save_path = Path("datasets") / "skills" / skill_name
        dataset.save(save_path)
        console.print(f"  Generated {len(dataset.all_examples)} synthetic examples")
        console.print(f"  Saved to {save_path}/")
    elif dataset_path:
        dataset = EvalDataset.load(Path(dataset_path))
        console.print(f"  Loaded dataset: {len(dataset.all_examples)} examples")
    else:
        console.print("[red]✗ Specify --dataset-path or use --eval-source synthetic[/red]")
        sys.exit(1)

    console.print(f"  Split: {len(dataset.train)} train / {len(dataset.val)} val / {len(dataset.holdout)} holdout")

    # ── 3. Validate constraints on baseline ─────────────────────────────
    console.print(f"\n[bold]Validating baseline constraints[/bold]")
    validator = ConstraintValidator(config)
    baseline_constraints = validator.validate_all(skill["raw"], "skill")
    all_pass = True
    for c in baseline_constraints:
        icon = "✓" if c.passed else "✗"
        color = "green" if c.passed else "red"
        console.print(f"  [{color}]{icon} {c.constraint_name}[/{color}]: {c.message}")
        if not c.passed:
            all_pass = False

    if not all_pass:
        console.print("[yellow]⚠ Baseline skill has constraint violations — proceeding anyway[/yellow]")

    # ── 4. Set up DSPy + GEPA optimizer ─────────────────────────────────
    console.print(f"\n[bold]Configuring optimizer[/bold]")
    console.print(f"  Optimizer: GEPA (mandatory; max {max_metric_calls} metric calls)")
    console.print(f"  Reflection model: {optimizer_model}")
    console.print(f"  Evaluation model: {eval_model}")
    console.print(f"  API base: {api_base or '(provider default)'}")
    console.print(f"  Evaluation threads: {num_threads}")

    # GEPA's reflection LM and the candidate/evaluation LM are separate roles.
    # num_threads defaults to 1: this avoids flooding a single-slot local server.
    eval_lm = config.make_lm(eval_model)
    reflection_lm = config.make_lm(optimizer_model)
    dspy.configure(lm=eval_lm)

    # Create the baseline skill module
    baseline_module = SkillModule(skill["body"])

    # Prepare DSPy examples
    trainset = dataset.to_dspy_examples("train")
    valset = dataset.to_dspy_examples("val")

    # ── 5. Run GEPA optimization ────────────────────────────────────────
    console.print(
        f"\n[bold cyan]Running mandatory GEPA optimization "
        f"(budget: {max_metric_calls} metric calls)...[/bold cyan]\n"
    )

    start_time = time.time()
    gepa_log_dir = Path("output") / skill_name / (
        "gepa-log-" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    try:
        optimizer = dspy.GEPA(
            metric=skill_fitness_metric,
            reflection_lm=reflection_lm,
            max_metric_calls=max_metric_calls,
            num_threads=max(1, num_threads),
            log_dir=str(gepa_log_dir),
            track_stats=True,
            seed=0,
        )
        optimized_module = optimizer.compile(
            baseline_module,
            trainset=trainset,
            valset=valset,
        )
    except Exception as exc:
        console.print(
            "[bold red]GEPA failed. GEPA is mandatory for this project; "
            "no fallback optimizer was run.[/bold red]"
        )
        console.print(f"Reason: {exc}")
        raise

    elapsed = time.time() - start_time
    console.print(f"\n  GEPA completed in {elapsed:.1f}s")

    # GEPA evolves predictor instructions. Read the actual optimized instructions,
    # not the wrapper's original skill_text attribute.
    evolved_body = optimized_module.predictor.signature.instructions.strip()

    evolved_full = reassemble_skill(skill["frontmatter"], evolved_body)

    # ── 7. Validate evolved skill ───────────────────────────────────────
    console.print(f"\n[bold]Validating evolved skill[/bold]")
    evolved_constraints = validator.validate_all(evolved_full, "skill", baseline_text=skill["raw"])
    all_pass = True
    for c in evolved_constraints:
        icon = "✓" if c.passed else "✗"
        color = "green" if c.passed else "red"
        console.print(f"  [{color}]{icon} {c.constraint_name}[/{color}]: {c.message}")
        if not c.passed:
            all_pass = False

    if not all_pass:
        console.print("[red]✗ Evolved skill FAILED constraints — not deploying[/red]")
        # Still save for inspection
        output_path = Path("output") / skill_name / "evolved_FAILED.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(evolved_full)
        console.print(f"  Saved failed variant to {output_path}")
        return

    if run_tests:
        console.print("\n[bold]Running requested Hermes test-suite gate[/bold]")
        test_result = validator.run_test_suite(config.hermes_agent_path)
        icon = "✓" if test_result.passed else "✗"
        console.print(f"  {icon} {test_result.constraint_name}: {test_result.message}")
        if test_result.details:
            console.print(test_result.details)
        if not test_result.passed:
            output_path = Path("output") / skill_name / "evolved_FAILED.md"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(evolved_full)
            console.print(
                f"[red]Test gate failed; candidate kept for inspection at {output_path}[/red]"
            )
            return

    # ── 8. Evaluate on holdout set ──────────────────────────────────────
    console.print(f"\n[bold]Evaluating on holdout set ({len(dataset.holdout)} examples)[/bold]")

    holdout_examples = dataset.to_dspy_examples("holdout")

    baseline_scores = []
    evolved_scores = []
    for ex in holdout_examples:
        # Score baseline
        with dspy.context(lm=lm):
            baseline_pred = baseline_module(task_input=ex.task_input)
            baseline_score = skill_fitness_metric(ex, baseline_pred)
            baseline_scores.append(float(baseline_score.score))

            evolved_pred = optimized_module(task_input=ex.task_input)
            evolved_score = skill_fitness_metric(ex, evolved_pred)
            evolved_scores.append(float(evolved_score.score))

    avg_baseline = sum(baseline_scores) / max(1, len(baseline_scores))
    avg_evolved = sum(evolved_scores) / max(1, len(evolved_scores))
    improvement = avg_evolved - avg_baseline

    # ── 9. Report results ───────────────────────────────────────────────
    table = Table(title="Evolution Results")
    table.add_column("Metric", style="bold")
    table.add_column("Baseline", justify="right")
    table.add_column("Evolved", justify="right")
    table.add_column("Change", justify="right")

    change_color = "green" if improvement > 0 else "red"
    table.add_row(
        "Holdout Score",
        f"{avg_baseline:.3f}",
        f"{avg_evolved:.3f}",
        f"[{change_color}]{improvement:+.3f}[/{change_color}]",
    )
    table.add_row(
        "Skill Size",
        f"{len(skill['body']):,} chars",
        f"{len(evolved_body):,} chars",
        f"{len(evolved_body) - len(skill['body']):+,} chars",
    )
    table.add_row("Time", "", f"{elapsed:.1f}s", "")
    table.add_row("Metric-call budget", "", str(max_metric_calls), "")

    console.print()
    console.print(table)

    # ── 10. Save output ─────────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("output") / skill_name / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save evolved skill
    (output_dir / "evolved_skill.md").write_text(evolved_full)

    # Save baseline for comparison
    (output_dir / "baseline_skill.md").write_text(skill["raw"])

    # Save metrics
    metrics = {
        "skill_name": skill_name,
        "timestamp": timestamp,
        "max_metric_calls": max_metric_calls,
        "optimizer_model": optimizer_model,
        "eval_model": eval_model,
        "baseline_score": avg_baseline,
        "evolved_score": avg_evolved,
        "improvement": improvement,
        "baseline_size": len(skill["body"]),
        "evolved_size": len(evolved_body),
        "train_examples": len(dataset.train),
        "val_examples": len(dataset.val),
        "holdout_examples": len(dataset.holdout),
        "elapsed_seconds": elapsed,
        "constraints_passed": all_pass,
    }
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    console.print(f"\n  Output saved to {output_dir}/")

    if improvement > 0:
        console.print(f"\n[bold green]✓ Evolution improved skill by {improvement:+.3f} ({improvement/max(0.001, avg_baseline)*100:+.1f}%)[/bold green]")
        console.print(f"  Review the diff: diff {output_dir}/baseline_skill.md {output_dir}/evolved_skill.md")
    else:
        console.print(f"\n[yellow]⚠ Evolution did not improve skill (change: {improvement:+.3f})[/yellow]")
        console.print("  Try: more iterations, better eval dataset, or different optimizer model")


@click.command()
@click.option("--skill", required=True, help="Name of the skill to evolve")
@click.option(
    "--max-metric-calls",
    "--iterations",
    "max_metric_calls",
    default=40,
    show_default=True,
    help="GEPA evaluation budget. --iterations is a legacy alias, not a step count.",
)
@click.option("--eval-source", default="synthetic", type=click.Choice(["synthetic", "golden", "sessiondb"]),
              help="Source for evaluation dataset")
@click.option("--dataset-path", default=None, help="Path to existing eval dataset (JSONL)")
@click.option("--optimizer-model", default="openai/gpt-4.1", help="Model used for GEPA reflection")
@click.option("--eval-model", default="openai/gpt-4.1-mini", help="Model used to execute/evaluate candidates")
@click.option("--api-base", default=None, help="Optional OpenAI-compatible base URL, e.g. local llama.cpp /v1")
@click.option("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing the API key; never put keys on the command line")
@click.option("--num-threads", default=1, type=click.IntRange(min=1), show_default=True,
              help="Concurrent evaluation threads; leave at 1 for a single-slot local server")
@click.option("--hermes-repo", default=None, help="Path containing the Hermes skills/ directory")
@click.option("--run-tests", is_flag=True, help="Run the full Hermes test suite (verify this gate for your install)")
@click.option("--dry-run", is_flag=True, help="Validate target and arguments without running optimization")
def main(skill, max_metric_calls, eval_source, dataset_path, optimizer_model, eval_model, api_base, api_key_env, num_threads, hermes_repo, run_tests, dry_run):
    """Evolve a Hermes Agent skill using mandatory DSPy + GEPA optimization."""
    evolve(
        skill_name=skill,
        max_metric_calls=max_metric_calls,
        eval_source=eval_source,
        dataset_path=dataset_path,
        optimizer_model=optimizer_model,
        eval_model=eval_model,
        hermes_repo=hermes_repo,
        run_tests=run_tests,
        dry_run=dry_run,
        api_base=api_base,
        api_key_env=api_key_env,
        num_threads=num_threads,
    )


if __name__ == "__main__":
    main()
