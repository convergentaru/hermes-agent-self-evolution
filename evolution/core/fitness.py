"""Fitness functions for evaluating evolved artifacts.

Uses LLM-as-judge with rubrics to score agent outputs.
Supports length penalties and multi-dimensional scoring.
"""

import dspy
from dataclasses import dataclass
from typing import Optional

from evolution.core.config import EvolutionConfig


@dataclass
class FitnessScore:
    """Multi-dimensional fitness score."""
    correctness: float = 0.0  # Did the agent produce correct output? (0-1)
    procedure_following: float = 0.0  # Did it follow the skill's procedure? (0-1)
    conciseness: float = 0.0  # Was it appropriately concise? (0-1)
    length_penalty: float = 0.0  # Penalty for being too verbose (0-1, 0 = no penalty)
    feedback: str = ""  # Textual feedback for GEPA's reflective analysis

    @property
    def composite(self) -> float:
        """Weighted composite score."""
        raw = (
            0.5 * self.correctness
            + 0.3 * self.procedure_following
            + 0.2 * self.conciseness
        )
        return max(0.0, raw - self.length_penalty)


class LLMJudge:
    """LLM-as-judge scorer with rubric-based evaluation.

    Scores agent outputs on multiple dimensions and provides
    textual feedback that GEPA can use for reflective mutation.
    """

    class JudgeSignature(dspy.Signature):
        """Evaluate an agent's response against an expected behavior rubric.

        Score the response on three dimensions (0.0 to 1.0 each):
        1. correctness: Did the response correctly address the task?
        2. procedure_following: Did it follow the expected approach/procedure?
        3. conciseness: Was it appropriately concise without omitting important info?

        Also provide specific, actionable feedback on what could be improved.
        """
        task_input: str = dspy.InputField(desc="The task the agent was given")
        expected_behavior: str = dspy.InputField(desc="Rubric describing what a good response looks like")
        agent_output: str = dspy.InputField(desc="The agent's actual response")
        skill_text: str = dspy.InputField(desc="The skill/instructions the agent was following")
        correctness: float = dspy.OutputField(desc="Score 0.0-1.0: Did the response correctly address the task?")
        procedure_following: float = dspy.OutputField(desc="Score 0.0-1.0: Did it follow the expected procedure?")
        conciseness: float = dspy.OutputField(desc="Score 0.0-1.0: Appropriately concise?")
        feedback: str = dspy.OutputField(desc="Specific, actionable feedback on what could be improved")

    def __init__(self, config: EvolutionConfig):
        self.config = config
        self.judge = dspy.ChainOfThought(self.JudgeSignature)

    def score(
        self,
        task_input: str,
        expected_behavior: str,
        agent_output: str,
        skill_text: str,
        artifact_size: Optional[int] = None,
        max_size: Optional[int] = None,
    ) -> FitnessScore:
        """Score an agent output using LLM-as-judge."""

        lm = self.config.make_lm(self.config.eval_model)

        with dspy.context(lm=lm):
            result = self.judge(
                task_input=task_input,
                expected_behavior=expected_behavior,
                agent_output=agent_output,
                skill_text=skill_text,
            )

        # Parse scores (clamp to 0-1)
        correctness = _parse_score(result.correctness)
        procedure_following = _parse_score(result.procedure_following)
        conciseness = _parse_score(result.conciseness)

        # Length penalty
        length_penalty = 0.0
        if artifact_size is not None and max_size is not None:
            ratio = artifact_size / max_size
            if ratio > 0.9:
                # Penalty ramps from 0 at 90% to 0.3 at 100%+
                length_penalty = min(0.3, (ratio - 0.9) * 3.0)

        return FitnessScore(
            correctness=correctness,
            procedure_following=procedure_following,
            conciseness=conciseness,
            length_penalty=length_penalty,
            feedback=str(result.feedback),
        )


def skill_fitness_metric(
    example: dspy.Example,
    prediction: dspy.Prediction,
    trace=None,
    pred_name=None,
    pred_trace=None,
) -> dspy.Prediction:
    """Return a GEPA score plus actionable text feedback.

    GEPA expects a metric that can accept (gold, pred, trace, pred_name,
    pred_trace). The feedback is deliberately bounded and contains no hidden
    conversation history; evaluation examples should already be sanitized.

    This first version uses a deterministic keyword-coverage proxy. It is useful
    for a low-cost smoke test, but it is NOT proof of semantic correctness.
    Use a curated holdout set and human review before adopting a candidate.
    """
    import re

    agent_output = str(getattr(prediction, "output", "") or "")
    expected = str(getattr(example, "expected_behavior", "") or "")
    task = str(getattr(example, "task_input", "") or "")

    if not agent_output.strip():
        return dspy.Prediction(
            score=0.0,
            feedback=(
                "The candidate returned an empty response. It must produce a "
                "non-empty result and address the expected behavior."
            ),
        )

    stop_words = {
        "the", "and", "for", "with", "that", "this", "from", "then", "must",
        "should", "have", "has", "had", "was", "were", "are", "into", "when",
        "what", "where", "which", "will", "your", "you", "use", "using",
        "как", "что", "это", "для", "при", "если", "или", "его", "её",
        "они", "она", "оно", "быть", "бы", "надо", "нужно", "должен",
    }

    def tokens(text: str) -> set[str]:
        return {
            token for token in re.findall(r"[a-zа-яё0-9_]+", text.lower())
            if len(token) > 2 and token not in stop_words
        }

    expected_tokens = tokens(expected)
    output_tokens = tokens(agent_output)
    matched = expected_tokens & output_tokens
    missing = sorted(expected_tokens - output_tokens)
    coverage = len(matched) / max(1, len(expected_tokens))
    score = min(1.0, max(0.0, 0.25 + 0.75 * coverage))

    missing_preview = ", ".join(missing[:12]) if missing else "none detected"
    feedback = (
        "Evaluate the candidate against the rubric, not style alone.\n"
        f"Task: {task[:500]}\n"
        f"Expected behavior: {expected[:700]}\n"
        f"Actual response: {agent_output[:1200]}\n"
        f"Deterministic rubric-term coverage: {coverage:.0%}.\n"
        f"Expected terms not seen (diagnostic only): {missing_preview}.\n"
        "Improve the procedure so it meets the stated behavior without adding "
        "unsupported claims or violating the task's safety constraints. "
        "Keyword coverage is only a proxy; the holdout review remains mandatory."
    )

    return dspy.Prediction(score=score, feedback=feedback)


def _parse_score(value) -> float:
    """Parse a score value, handling various LLM output formats."""
    if isinstance(value, (int, float)):
        return min(1.0, max(0.0, float(value)))
    try:
        return min(1.0, max(0.0, float(str(value).strip())))
    except (ValueError, TypeError):
        return 0.5  # Default to neutral on parse failure
