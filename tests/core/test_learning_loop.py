"""Tests for the local, evidence-gated Hermes learning loop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evolution.learning.loop import (
    LearningError,
    cmd_accept,
    cmd_context,
    cmd_gate,
    cmd_propose,
    cmd_record,
    ensure_store,
)


def ns(**kwargs):
    class Args:
        pass
    args = Args()
    for key, value in kwargs.items():
        setattr(args, key, value)
    return args


def record_event(store: Path, outcome: str = "corrected") -> str:
    cmd_record(ns(
        store=store,
        task_class="project-work",
        namespace="project:learning-loop-tests",
        outcome=outcome,
        summary="Agent claimed tests passed but no test command was executed.",
        lesson="Require actual test command, output, and exit code.",
        correction="Do not report a test as passed without a real exit code.",
        tag=["verification", "tests"],
        evidence=["local-report.md"],
        profile="programmer",
        model_id="local-model",
    ))
    events = [json.loads(line) for line in (store / "events.jsonl").read_text().splitlines()]
    return events[-1]["event_id"]


def proposal(store: Path, event_id: str) -> str:
    cmd_propose(ns(
        store=store,
        title="Require test evidence",
        trigger="When reporting tests",
        instruction="Report PASS only when the actual test command and exit code are recorded.",
        why="Past report lacked evidence; the user corrected the claim.",
        source_event=[event_id],
        test_case=["test-report-evidence", "test-missing-exit-code"],
        tag=["verification", "tests"],
    ))
    files = list((store / "candidates").glob("*.json"))
    return files[0].stem


def make_run(tmp_path: Path, candidate_id: str, *, all_pass: bool, fail_safety: bool = False, steps: int = 10):
    candidate_tag = "baseline" if candidate_id == "baseline" else "candidate"
    artifact_path = tmp_path / f"{candidate_tag}-artifact.md"
    artifact_path.write_text(f"# {candidate_tag} tested instruction\\n", encoding="utf-8")
    artifact_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    evidence_dir = tmp_path / f"{candidate_tag}-evidence"
    evidence_dir.mkdir(exist_ok=True)
    cases = []
    for case_idx in range(1, 6):
        for repeat in range(1, 4):
            passed = all_pass or case_idx < 5
            safety_passed = not (fail_safety and case_idx == 2 and repeat == 2)
            evidence_path = evidence_dir / f"case-{case_idx}-repeat-{repeat}.json"
            evidence = {
                "case_id": f"case-{case_idx}",
                "repeat": repeat,
                "passed": passed,
                "safety_passed": safety_passed,
                "steps": steps,
                "tokens": steps * 100,
                "latency_ms": steps * 1000,
                "source": "test fixture; real deployments must point to actual run evidence",
            }
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            cases.append({
                "case_id": f"case-{case_idx}",
                "repeat": repeat,
                "passed": passed,
                "safety_passed": safety_passed,
                "steps": steps,
                "tokens": steps * 100,
                "latency_ms": steps * 1000,
                "evidence_path": str(evidence_path),
                "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            })
    return {
        "schema_version": 1,
        "suite_id": "hermes-smoke-v1",
        "condition": "warm" if candidate_id == "baseline" else "warm_plus_learning",
        "model_id": "local:stable-8b",
        "environment_id": "hermes-test-build-1",
        "artifact_path": str(artifact_path),
        "artifact_sha256": artifact_sha256,
        "candidate_id": candidate_id,
        "cases": cases,
    }

def write_run(path: Path, data: dict):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_record_redacts_common_tokens_and_keeps_local_store_private(tmp_path, capsys):
    store = tmp_path / "private-store"
    cmd_record(ns(
        store=store,
        task_class="general",
        outcome="failed",
        summary="Encountered credential sk-abcdefghijklmnopqrstu while testing",
        lesson="Keep it local",
        correction="",
        tag=[],
        evidence=[],
        profile="",
        model_id="",
    ))
    saved = (store / "events.jsonl").read_text()
    assert "sk-abcdefghijklmnopqrstu" not in saved
    assert "[REDACTED]" in saved
    assert (store.stat().st_mode & 0o777) == 0o700
    assert ((store / "events.jsonl").stat().st_mode & 0o777) == 0o600


def test_proposal_requires_real_source_event_and_test_cases(tmp_path):
    store = tmp_path / "store"
    ensure_store(store)
    with pytest.raises(LearningError):
        cmd_propose(ns(
            store=store, title="x", trigger="x", instruction="x", why="x",
            source_event=["not-real"], test_case=["test-1"], tag=[]
        ))


def test_gate_passes_only_with_improvement_and_human_approval_is_separate(tmp_path, capsys):
    store = tmp_path / "store"
    ensure_store(store)
    event_id = record_event(store)
    candidate_id = proposal(store, event_id)
    baseline_path = write_run(tmp_path / "baseline.json", make_run(tmp_path, "baseline", all_pass=False, steps=12))
    candidate_path = write_run(tmp_path / "candidate.json", make_run(tmp_path, candidate_id, all_pass=True, steps=12))
    result_code = cmd_gate(ns(
        store=store,
        candidate_id=candidate_id,
        baseline=baseline_path,
        candidate_run=candidate_path,
        min_cases=5,
        min_repeats=3,
        min_efficiency_gain=0.10,
    ))
    report = json.loads(capsys.readouterr().out)
    assert result_code == 0
    assert report["decision"] == "PASS"
    assert report["candidate_pass_rate"] > report["baseline_pass_rate"]
    assert report["human_approval_required"] is True

    # Gate PASS still does not auto-promote the candidate.
    gate_path = Path(report["report_path"])
    with pytest.raises(LearningError):
        cmd_accept(ns(
            store=store, candidate_id=candidate_id, gate_report=gate_path,
            approved_by=""
        ))
    cmd_accept(ns(
        store=store, candidate_id=candidate_id, gate_report=gate_path,
        approved_by="owner-review"
    ))
    accepted = store / "accepted" / f"{candidate_id}.json"
    assert accepted.exists()
    assert json.loads(accepted.read_text())["status"] == "accepted"


def test_gate_rejects_safety_regression(tmp_path, capsys):
    store = tmp_path / "store"
    ensure_store(store)
    event_id = record_event(store)
    candidate_id = proposal(store, event_id)
    baseline_path = write_run(tmp_path / "baseline.json", make_run(tmp_path, "baseline", all_pass=True, steps=12))
    candidate_path = write_run(
        tmp_path / "candidate.json",
        make_run(tmp_path, candidate_id, all_pass=True, fail_safety=True, steps=5),
    )
    code = cmd_gate(ns(
        store=store, candidate_id=candidate_id,
        baseline=baseline_path, candidate_run=candidate_path,
        min_cases=5, min_repeats=3, min_efficiency_gain=0.10,
    ))
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["decision"] == "REJECT"
    assert report["safety_failures"]


def test_equal_quality_requires_measured_efficiency_improvement(tmp_path, capsys):
    store = tmp_path / "store"
    ensure_store(store)
    event_id = record_event(store)
    candidate_id = proposal(store, event_id)
    baseline_path = write_run(tmp_path / "baseline.json", make_run(tmp_path, "baseline", all_pass=True, steps=10))
    candidate_path = write_run(tmp_path / "candidate.json", make_run(tmp_path, candidate_id, all_pass=True, steps=10))
    code = cmd_gate(ns(
        store=store, candidate_id=candidate_id,
        baseline=baseline_path, candidate_run=candidate_path,
        min_cases=5, min_repeats=3, min_efficiency_gain=0.10,
    ))
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["decision"] == "NO_DEMONSTRATED_IMPROVEMENT"


def test_context_retrieves_accepted_playbook_only_and_respects_char_cap(tmp_path, capsys):
    store = tmp_path / "store"
    ensure_store(store)
    event_id = record_event(store)
    candidate_id = proposal(store, event_id)
    accepted = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "status": "accepted",
        "namespace": "project:learning-loop-tests",
        "title": "Test evidence verification",
        "trigger": "When reporting tests and verification",
        "instruction": "Require a real test command and exit code.",
        "source_event_ids": [event_id],
        "tags": ["tests", "verification"],
        "approved_by": "owner-review",
        "gate_report_sha256": "c" * 64,
    }
    (store / "accepted" / f"{candidate_id}.json").write_text(json.dumps(accepted))
    (store / "accepted" / "unrelated-namespace.json").write_text(json.dumps({
        "schema_version": 1,
        "candidate_id": "unrelated-namespace",
        "status": "accepted",
        "namespace": "project:acer-rescue-usb",
        "title": "Test evidence verification for unrelated USB work",
        "trigger": "When reporting tests verification",
        "instruction": "This unrelated namespace must never appear in this context.",
        "source_event_ids": [event_id],
        "tags": ["tests", "verification"],
        "approved_by": "owner-review",
        "gate_report_sha256": "d" * 64,
    }))
    cmd_context(ns(store=store, namespace="project:learning-loop-tests", task="report tests verification", max_chars=1000))
    output = capsys.readouterr().out
    assert "Require a real test command" in output
    assert "accepted playbook(s)" in output
    assert "unrelated namespace must never appear" not in output

    # Proposed candidates are never loaded into context.
    (store / "accepted" / f"{candidate_id}.json").unlink()
    cmd_context(ns(store=store, namespace="project:learning-loop-tests", task="report tests verification", max_chars=1000))
    assert "No accepted playbooks matched" in capsys.readouterr().out
