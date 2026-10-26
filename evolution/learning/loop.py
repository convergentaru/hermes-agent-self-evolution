"""Local-first, evidence-gated learning loop for Hermes Agent.

No hosted model calls or third-party dependencies are used. Learning artifacts
remain proposals until paired evaluation data passes deterministic gates and a
human explicitly accepts the candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
OUTCOMES = {"success", "partial", "failed", "corrected"}
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    ),
)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]{1,}", re.IGNORECASE)


class LearningError(Exception):
    """Expected, actionable learning-loop error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_store() -> Path:
    configured = os.environ.get("HERMES_LEARNING_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "hermes-learning-loop"


def safe_text(value: str, limit: int = 4000) -> str:
    """Normalize a concise user-authored summary and redact common token forms.

    This is defense in depth, not a guarantee that arbitrary text contains no
    private data. Callers must not submit raw conversation transcripts.
    """
    value = str(value).replace("\x00", "").strip()
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            value = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", value)
        else:
            value = pattern.sub("[REDACTED]", value)
    if len(value) > limit:
        value = value[:limit] + " …[TRUNCATED]"
    return value


def ensure_store(store: Path) -> None:
    for name in ("candidates", "accepted", "reports"):
        path = store / name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        store.chmod(0o700)
    except OSError:
        pass


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        temp.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if temp.exists():
            temp.unlink()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    # One append write avoids a read/modify/write cycle and preserves prior events.
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LearningError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(item, dict):
                raise LearningError(f"Expected a JSON object at {path}:{line_no}")
            result.append(item)
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LearningError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LearningError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LearningError(f"Expected a JSON object in {path}")
    return value


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug[:48].strip("-") or "candidate") + "-" + uuid.uuid4().hex[:8]


def cmd_init(args: argparse.Namespace) -> int:
    store = args.store
    ensure_store(store)
    (store / "events.jsonl").touch(exist_ok=True, mode=0o600)
    print(json.dumps({"status": "initialized", "store": str(store)}, ensure_ascii=False))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    store = args.store
    ensure_store(store)
    if args.outcome not in OUTCOMES:
        raise LearningError(f"Unsupported outcome: {args.outcome}")
    record = {
        "schema_version": SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "created_at": utc_now(),
        "task_class": safe_text(args.task_class, 120),
        "outcome": args.outcome,
        "summary": safe_text(args.summary),
        "lesson": safe_text(args.lesson or "", 2000),
        "correction": safe_text(args.correction or "", 2000),
        "tags": sorted({safe_text(tag.strip().lower(), 80) for tag in args.tag if tag.strip()}),
        "evidence": [safe_text(path, 500) for path in args.evidence],
        "profile": safe_text(args.profile or "", 120),
        "model_id": safe_text(args.model_id or "", 160),
    }
    if not record["summary"]:
        raise LearningError("A concise summary is required; do not store a raw transcript.")
    append_jsonl(store / "events.jsonl", record)
    print(json.dumps({
        "status": "recorded",
        "event_id": record["event_id"],
        "store": str(store / "events.jsonl"),
        "privacy_note": "Only the supplied summary was stored; inspect it before sharing or committing.",
    }, ensure_ascii=False))
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    store = args.store
    ensure_store(store)
    events = load_jsonl(store / "events.jsonl")
    known = {event.get("event_id") for event in events}
    source_ids = sorted(set(args.source_event))
    missing = [event_id for event_id in source_ids if event_id not in known]
    if not source_ids or missing:
        raise LearningError(
            "A proposal needs existing source event IDs. "
            + (f"Unknown IDs: {', '.join(missing)}" if missing else "")
        )
    if not args.test_case:
        raise LearningError("Add at least one regression test-case ID before proposing a candidate.")
    candidate_id = slugify(args.title)
    candidate = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "created_at": utc_now(),
        "status": "proposed",
        "title": safe_text(args.title, 200),
        "trigger": safe_text(args.trigger, 500),
        "instruction": safe_text(args.instruction, 3000),
        "why": safe_text(args.why, 2000),
        "source_event_ids": source_ids,
        "test_case_ids": sorted(set(args.test_case)),
        "tags": sorted({safe_text(tag.strip().lower(), 80) for tag in args.tag if tag.strip()}),
        "activation": "not_active_until_evaluated_and_human_approved",
    }
    if not all(candidate[key] for key in ("title", "trigger", "instruction", "why")):
        raise LearningError("title, trigger, instruction, and why must all be non-empty.")
    path = store / "candidates" / f"{candidate_id}.json"
    atomic_write(path, json.dumps(candidate, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "status": "proposed_not_active",
        "candidate_id": candidate_id,
        "path": str(path),
        "message": "Proposal is stored for review only; active instructions were not modified.",
    }, ensure_ascii=False))
    return 0


def validate_run(run: dict[str, Any], label: str, min_repeats: int, min_cases: int) -> dict[tuple[str, int], dict[str, Any]]:
    required = ("schema_version", "suite_id", "model_id", "environment_id", "artifact_path", "artifact_sha256", "candidate_id", "cases")
    missing = [field for field in required if field not in run]
    if missing:
        raise LearningError(f"{label} run is missing required fields: {', '.join(missing)}")
    if run["schema_version"] != SCHEMA_VERSION:
        raise LearningError(f"{label} run schema_version must be {SCHEMA_VERSION}")
    artifact_path = Path(str(run["artifact_path"])).expanduser()
    if not artifact_path.is_file():
        raise LearningError(f"{label} artifact file does not exist: {artifact_path}")
    actual_artifact_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    if actual_artifact_hash != str(run["artifact_sha256"]).lower():
        raise LearningError(f"{label} artifact SHA-256 does not match the actual file")
    if not re.fullmatch(r"[0-9a-f]{64}", str(run["artifact_sha256"]).lower()):
        raise LearningError(f"{label} artifact_sha256 must be a 64-character SHA-256 hex digest")
    if not isinstance(run["cases"], list) or not run["cases"]:
        raise LearningError(f"{label} run must contain a non-empty 'cases' array")
    mapped: dict[tuple[str, int], dict[str, Any]] = {}
    case_ids: set[str] = set()
    repeat_ids: set[int] = set()
    for index, item in enumerate(run["cases"]):
        if not isinstance(item, dict):
            raise LearningError(f"{label} cases[{index}] must be an object")
        for field in ("case_id", "repeat", "passed", "safety_passed", "evidence_path", "evidence_sha256"):
            if field not in item:
                raise LearningError(f"{label} cases[{index}] missing {field}")
        case_id = str(item["case_id"])
        try:
            repeat = int(item["repeat"])
        except (TypeError, ValueError) as exc:
            raise LearningError(f"{label} cases[{index}].repeat must be an integer") from exc
        if not case_id or repeat < 1:
            raise LearningError(f"{label} cases[{index}] has invalid case_id/repeat")
        if not isinstance(item["passed"], bool) or not isinstance(item["safety_passed"], bool):
            raise LearningError(f"{label} cases[{index}] passed/safety_passed must be booleans")
        evidence_path = Path(str(item["evidence_path"])).expanduser()
        if not evidence_path.is_file():
            raise LearningError(f"{label} evidence file does not exist for {case_id} repeat {repeat}: {evidence_path}")
        actual_evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        if actual_evidence_hash != str(item["evidence_sha256"]).lower():
            raise LearningError(f"{label} evidence SHA-256 mismatch for {case_id} repeat {repeat}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(item["evidence_sha256"]).lower()):
            raise LearningError(f"{label} evidence_sha256 must be a 64-character SHA-256 hex digest")
        key = (case_id, repeat)
        if key in mapped:
            raise LearningError(f"{label} has duplicate case_id/repeat row: {key}")
        row = dict(item)
        for metric in ("steps", "tokens", "latency_ms"):
            if metric in row and row[metric] is not None:
                if isinstance(row[metric], bool) or not isinstance(row[metric], (int, float)) or row[metric] < 0:
                    raise LearningError(f"{label} {key} has invalid {metric}")
        mapped[key] = row
        case_ids.add(case_id)
        repeat_ids.add(repeat)
    if len(case_ids) < min_cases:
        raise LearningError(f"{label} has {len(case_ids)} unique cases; minimum is {min_cases}")
    counts = {case_id: sum(1 for row_id, _ in mapped if row_id == case_id) for case_id in case_ids}
    short = {case_id: count for case_id, count in counts.items() if count < min_repeats}
    if short:
        raise LearningError(
            f"{label} needs at least {min_repeats} paired repeats per case; insufficient: {short}"
        )
    return mapped


def _mean_metric(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return statistics.mean(values) if values else None


def _reduction(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return (baseline - candidate) / baseline


def cmd_gate(args: argparse.Namespace) -> int:
    store = args.store
    ensure_store(store)
    baseline = load_json(args.baseline)
    candidate_run = load_json(args.candidate_run)
    proposal_path = store / "candidates" / f"{args.candidate_id}.json"
    proposal = load_json(proposal_path)
    base_rows = validate_run(baseline, "baseline", args.min_repeats, args.min_cases)
    cand_rows = validate_run(candidate_run, "candidate", args.min_repeats, args.min_cases)

    failures: list[str] = []
    if baseline["suite_id"] != candidate_run["suite_id"]:
        failures.append("suite_id differs")
    if baseline["model_id"] != candidate_run["model_id"]:
        failures.append("model_id differs; this is not a paired learning comparison")
    if baseline["environment_id"] != candidate_run["environment_id"]:
        failures.append("environment_id differs; this is not a controlled comparison")
    if baseline["candidate_id"] != "baseline":
        failures.append("baseline run must use candidate_id='baseline'")
    if candidate_run["candidate_id"] != args.candidate_id:
        failures.append("candidate run candidate_id does not match the proposal")
    if str(baseline.get("artifact_sha256", "")).lower() == str(candidate_run.get("artifact_sha256", "")).lower():
        failures.append("candidate artifact hash equals baseline; no distinct artifact was evaluated")
    if set(base_rows) != set(cand_rows):
        failures.append("baseline and candidate case/repeat rows do not match")
    source_ids = set(proposal.get("source_event_ids", []))
    known_events = {event.get("event_id") for event in load_jsonl(store / "events.jsonl")}
    if not source_ids or not source_ids.issubset(known_events):
        failures.append("proposal source events are missing from the local event journal")

    common_keys = sorted(set(base_rows) & set(cand_rows))
    regressions = [
        f"{case_id} repeat {repeat}"
        for (case_id, repeat) in common_keys
        if base_rows[(case_id, repeat)]["passed"] and not cand_rows[(case_id, repeat)]["passed"]
    ]
    safety_failures = [
        f"{case_id} repeat {repeat}"
        for (case_id, repeat) in common_keys
        if not cand_rows[(case_id, repeat)]["safety_passed"]
    ]

    base_pass_rate = (
        sum(1 for key in common_keys if base_rows[key]["passed"]) / len(common_keys)
        if common_keys else 0.0
    )
    cand_pass_rate = (
        sum(1 for key in common_keys if cand_rows[key]["passed"]) / len(common_keys)
        if common_keys else 0.0
    )
    shared_success = [
        key for key in common_keys
        if base_rows[key]["passed"] and cand_rows[key]["passed"]
    ]
    base_success_rows = [base_rows[key] for key in shared_success]
    cand_success_rows = [cand_rows[key] for key in shared_success]
    metric_report: dict[str, Any] = {}
    efficiency_wins: list[str] = []
    for metric in ("steps", "tokens", "latency_ms"):
        base_value = _mean_metric(base_success_rows, metric)
        cand_value = _mean_metric(cand_success_rows, metric)
        reduction = _reduction(base_value, cand_value)
        metric_report[metric] = {
            "baseline_mean_on_shared_successes": base_value,
            "candidate_mean_on_shared_successes": cand_value,
            "relative_reduction": reduction,
        }
        if reduction is not None and reduction >= args.min_efficiency_gain:
            efficiency_wins.append(metric)

    if failures:
        decision = "INSUFFICIENT_OR_INVALID_DATA"
    elif safety_failures:
        decision = "REJECT"
        failures.append("candidate has safety failures")
    elif regressions:
        decision = "REJECT"
        failures.append("candidate regresses cases that passed in the baseline")
    elif cand_pass_rate < base_pass_rate:
        decision = "REJECT"
        failures.append("candidate pass rate is lower than baseline")
    elif cand_pass_rate > base_pass_rate:
        decision = "PASS"
        failures.append("pass rate improved without safety failures or regressions")
    elif efficiency_wins:
        decision = "PASS"
        failures.append(f"pass rate held; efficiency improvement met threshold for {', '.join(efficiency_wins)}")
    else:
        decision = "NO_DEMONSTRATED_IMPROVEMENT"
        failures.append("no pass-rate improvement and no efficiency reduction meeting threshold")

    report = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "candidate_id": args.candidate_id,
        "decision": decision,
        "promotion_allowed": decision == "PASS",
        "human_approval_required": True,
        "suite_id": baseline.get("suite_id"),
        "model_id": baseline.get("model_id"),
        "environment_id": baseline.get("environment_id"),
        "baseline_artifact_sha256": baseline.get("artifact_sha256"),
        "candidate_artifact_sha256": candidate_run.get("artifact_sha256"),
        "artifacts": {
            "baseline": baseline.get("artifact_path"),
            "candidate": candidate_run.get("artifact_path"),
        },
        "evidence_files": [
            {
                "case_id": case_id,
                "repeat": repeat,
                "baseline_path": base_rows[(case_id, repeat)].get("evidence_path"),
                "baseline_sha256": base_rows[(case_id, repeat)].get("evidence_sha256"),
                "candidate_path": cand_rows[(case_id, repeat)].get("evidence_path"),
                "candidate_sha256": cand_rows[(case_id, repeat)].get("evidence_sha256"),
            }
            for case_id, repeat in common_keys
        ],
        "observations": len(common_keys),
        "unique_cases": len({key[0] for key in common_keys}),
        "repeats": sorted({key[1] for key in common_keys}),
        "baseline_pass_rate": base_pass_rate,
        "candidate_pass_rate": cand_pass_rate,
        "safety_failures": safety_failures,
        "regressions": regressions,
        "efficiency": metric_report,
        "reasons": failures,
        "note": "This is a deterministic gate over supplied run receipts, not proof that the receipts themselves are authentic. Inspect artifacts/logs independently.",
    }
    report_path = store / "reports" / f"gate-{args.candidate_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    report["report_path"] = str(report_path)
    atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if decision == "PASS" else 2


def cmd_accept(args: argparse.Namespace) -> int:
    store = args.store
    ensure_store(store)
    proposal_path = store / "candidates" / f"{args.candidate_id}.json"
    proposal = load_json(proposal_path)
    report = load_json(args.gate_report)
    if report.get("candidate_id") != args.candidate_id:
        raise LearningError("Gate report belongs to a different candidate.")
    if report.get("decision") != "PASS" or report.get("promotion_allowed") is not True:
        raise LearningError("Candidate cannot be accepted: gate report is not PASS.")
    if not args.approved_by.strip():
        raise LearningError("An explicit human --approved-by value is required.")
    if proposal.get("status") != "proposed":
        raise LearningError(f"Candidate status is not proposed: {proposal.get('status')}")
    accepted = dict(proposal)
    accepted.update({
        "status": "accepted",
        "accepted_at": utc_now(),
        "approved_by": safe_text(args.approved_by, 120),
        "gate_report_sha256": hashlib.sha256(args.gate_report.read_bytes()).hexdigest(),
        "gate_decision": report["decision"],
    })
    target = store / "accepted" / f"{args.candidate_id}.json"
    atomic_write(target, json.dumps(accepted, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "status": "accepted_for_retrieval",
        "candidate_id": args.candidate_id,
        "path": str(target),
        "active_hermes_files_modified": False,
        "note": "This makes the playbook retrievable by this tool only; it does not edit SOUL.md or SKILL.md.",
    }, ensure_ascii=False))
    return 0


def words(value: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_RE.finditer(value)}


def cmd_context(args: argparse.Namespace) -> int:
    ensure_store(args.store)
    query_words = words(args.task)
    if not query_words:
        raise LearningError("Task query must contain words.")
    entries: list[tuple[int, Path, dict[str, Any]]] = []
    for path in sorted((args.store / "accepted").glob("*.json")):
        playbook = load_json(path)
        if playbook.get("status") != "accepted" or not playbook.get("approved_by") or not playbook.get("gate_report_sha256"):
            continue
        searchable = " ".join([
            str(playbook.get("title", "")),
            str(playbook.get("trigger", "")),
            " ".join(playbook.get("tags", [])),
        ])
        overlap = len(query_words & words(searchable))
        if overlap > 0:
            entries.append((overlap, path, playbook))
    entries.sort(key=lambda item: (-item[0], item[1].name))

    chunks: list[str] = []
    remaining = args.max_chars
    for score, path, playbook in entries:
        chunk = (
            f"## {playbook.get('title', path.stem)}\n"
            f"Trigger: {playbook.get('trigger', '')}\n"
            f"Instruction: {playbook.get('instruction', '')}\n"
            f"Source events: {', '.join(playbook.get('source_event_ids', []))}\n"
            f"Verified candidate: {path.stem}; gate report hash: {playbook.get('gate_report_sha256')}\n"
        )
        if len(chunk) > remaining:
            continue
        chunks.append(chunk)
        remaining -= len(chunk)
    if not chunks:
        print("No accepted playbooks matched this task. Do not invent a lesson or load unreviewed candidates.")
    else:
        print("\n".join(chunks))
    print(f"\n[learning-loop: returned {len(chunks)} approved playbook(s), max_chars={args.max_chars}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local-first evidence-gated learning store for Hermes Agent."
    )
    parser.add_argument("--store", type=Path, default=default_store(), help="Private local store path.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create the local store.")
    init.set_defaults(func=cmd_init)

    record = sub.add_parser("record", help="Record one concise observed outcome.")
    record.add_argument("--task-class", default="general")
    record.add_argument("--outcome", required=True, choices=sorted(OUTCOMES))
    record.add_argument("--summary", required=True, help="Short factual summary, not a raw transcript.")
    record.add_argument("--lesson", default="")
    record.add_argument("--correction", default="")
    record.add_argument("--tag", action="append", default=[])
    record.add_argument("--evidence", action="append", default=[])
    record.add_argument("--profile", default="")
    record.add_argument("--model-id", default="")
    record.set_defaults(func=cmd_record)

    propose = sub.add_parser("propose", help="Create a non-active candidate playbook.")
    propose.add_argument("--title", required=True)
    propose.add_argument("--trigger", required=True)
    propose.add_argument("--instruction", required=True)
    propose.add_argument("--why", required=True)
    propose.add_argument("--source-event", action="append", default=[])
    propose.add_argument("--test-case", action="append", default=[])
    propose.add_argument("--tag", action="append", default=[])
    propose.set_defaults(func=cmd_propose)

    gate = sub.add_parser("gate", help="Compare baseline/candidate run receipts.")
    gate.add_argument("--candidate-id", required=True)
    gate.add_argument("--baseline", required=True, type=Path)
    gate.add_argument("--candidate-run", required=True, type=Path)
    gate.add_argument("--min-cases", type=int, default=5)
    gate.add_argument("--min-repeats", type=int, default=3)
    gate.add_argument("--min-efficiency-gain", type=float, default=0.10)
    gate.set_defaults(func=cmd_gate)

    accept = sub.add_parser("accept", help="Accept a passing candidate after human approval.")
    accept.add_argument("--candidate-id", required=True)
    accept.add_argument("--gate-report", required=True, type=Path)
    accept.add_argument("--approved-by", required=True, help="Human approval record, e.g. owner-review.")
    accept.set_defaults(func=cmd_accept)

    context = sub.add_parser("context", help="Retrieve a bounded set of accepted playbooks for a task.")
    context.add_argument("--task", required=True)
    context.add_argument("--max-chars", type=int, default=3000)
    context.set_defaults(func=cmd_context)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "min_cases", 1) < 1 or getattr(args, "min_repeats", 1) < 1:
        parser.error("--min-cases and --min-repeats must be positive")
    if getattr(args, "min_efficiency_gain", 0) < 0:
        parser.error("--min-efficiency-gain cannot be negative")
    if getattr(args, "max_chars", 1) < 200:
        parser.error("--max-chars must be at least 200")
    try:
        return int(args.func(args))
    except LearningError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
