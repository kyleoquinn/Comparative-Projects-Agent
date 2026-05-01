from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import asdict
from pathlib import Path

from comp_agent.models import ProjectBrief
from comp_agent.pipeline import CompPackagePipeline
from comp_agent.stages import CompAppStages
from comp_agent.ui import run_server
from comp_agent.workspace import write_json


EXAMPLE_BRIEF = ProjectBrief(
    project_name="Competition Study",
    address="230 Vesey St, New York, NY 10281",
    program_type="office repositioning",
    total_sf=2100000,
    amenity_priorities=["fitness_wellness", "food_beverage", "outdoor_space", "conference_event", "public_realm"],
    geography="New York, NY",
    comp_types=["adaptive reuse", "premium workplace", "public realm adjacency"],
    radius_miles=3.0,
    time_horizon_years=8,
    audience="concept presentation",
    filters={
        "asset_scale": "large urban commercial",
        "delivery_status": "built or announced",
        "quality_band": "upper market",
    },
    presentation_priorities=[
        "clear precedent relevance",
        "defensible source trail",
        "tables and graphics that can be dropped into a pitch deck",
    ],
)


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if value:
            try:
                parts = shlex.split(value, posix=False)
            except ValueError:
                parts = []
            if len(parts) == 1:
                value = parts[0].strip("\"'")
            else:
                value = value.strip("\"'")
        os.environ[key] = value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a concept-level comps research package.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Write an example project brief JSON file.")
    init_parser.add_argument("--output", default="project_brief.json", help="Where to write the example brief.")

    ui_parser = subparsers.add_parser("ui", help="Start the temporary local comp search UI.")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8765)
    ui_parser.add_argument("--output-root", default="projects_ui")

    run_parser = subparsers.add_parser("run", help="Run the comp package pipeline.")
    run_parser.add_argument("--brief", help="Path to project brief JSON.")
    run_parser.add_argument("--project", help="Project name when not using --brief.")
    run_parser.add_argument("--address", help="Project address when not using --brief.")
    run_parser.add_argument("--program-type", help="Program type when not using --brief.")
    run_parser.add_argument("--geography", default="unknown", help="Target market/geography.")
    run_parser.add_argument("--comp-type", action="append", default=[], help="Comp lane; repeat for multiple lanes.")
    run_parser.add_argument("--radius-miles", type=float, default=5.0)
    run_parser.add_argument("--time-horizon-years", type=int, default=10)
    run_parser.add_argument("--output-root", default="projects")

    for command, help_text in (
        ("discover", "Create source plan and candidate comps for user review."),
        ("approve", "Approve candidate comps for research."),
        ("research", "Create raw research records for approved comps."),
        ("format", "Create normalized tables from researched records."),
        ("outputs", "Generate deck-ready graphics and PowerPoint output."),
        ("audit", "Audit formatted comp information and queue revision tasks."),
        ("poc", "Run discover, pause for comp approval, then research -> format -> outputs -> audit."),
    ):
        stage_parser = subparsers.add_parser(command, help=help_text)
        stage_parser.add_argument("--brief", required=True, help="Path to project brief JSON.")
        stage_parser.add_argument("--output-root", default="projects")
        if command == "approve":
            stage_parser.add_argument("--comp-id", action="append", default=[], help="Candidate comp ID to approve; repeat for multiple.")
            stage_parser.add_argument("--limit", type=int, default=1, help="Approve this many top candidates when --comp-id is omitted.")
            stage_parser.add_argument("--notes", default="Approved from CLI.")
        if command == "poc":
            stage_parser.add_argument("--comp-id", action="append", default=[], help="Candidate comp ID to approve; repeat for multiple.")
            stage_parser.add_argument("--auto-approve", action="store_true", help="Skip the approval prompt and approve candidates by --comp-id or --limit.")
            stage_parser.add_argument("--limit", type=int, default=None, help="With --auto-approve, approve this many top candidates.")
            stage_parser.add_argument("--notes", default="Approved from CLI POC approval gate.")
    return parser


def _brief_from_args(args: argparse.Namespace) -> ProjectBrief:
    if args.brief:
        payload = json.loads(Path(args.brief).read_text(encoding="utf-8"))
        return ProjectBrief.from_dict(payload)
    missing = [name for name in ("project", "address", "program_type") if not getattr(args, name)]
    if missing:
        raise SystemExit(f"Provide --brief or the required fields: {', '.join('--' + item.replace('_', '-') for item in missing)}")
    return ProjectBrief(
        project_name=args.project,
        address=args.address,
        program_type=args.program_type,
        geography=args.geography,
        comp_types=args.comp_type,
        radius_miles=args.radius_miles,
        time_horizon_years=args.time_horizon_years,
    )


def _load_candidates(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _candidate_lines(candidates: list[dict]) -> list[str]:
    lines = []
    for index, candidate in enumerate(candidates, start=1):
        attrs = candidate.get("known_attributes") or {}
        takeaway = attrs.get("presentation_takeaway") or "; ".join(candidate.get("source_notes") or [])
        if len(str(takeaway)) > 180:
            takeaway = f"{str(takeaway)[:177]}..."
        lines.append(
            "\n".join(
                [
                    f"{index}. {candidate.get('comp_name')} [{candidate.get('comp_id')}]",
                    f"   Type: {candidate.get('comp_type')} | Fit: {candidate.get('relevance_score')}/100 | Status: {candidate.get('status')}",
                    f"   Location: {candidate.get('location')}",
                    f"   Takeaway: {takeaway or 'Review source notes before approval.'}",
                ]
            )
        )
    return lines


def _parse_approval_selection(selection: str, candidates: list[dict]) -> list[str]:
    value = selection.strip().lower()
    if value in {"", "none", "n", "no"}:
        return []
    if value in {"all", "a", "yes", "y"}:
        return [str(candidate["comp_id"]) for candidate in candidates]
    selected: list[str] = []
    by_id = {str(candidate["comp_id"]): str(candidate["comp_id"]) for candidate in candidates}
    for token in value.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            index = int(token)
            if 1 <= index <= len(candidates):
                selected.append(str(candidates[index - 1]["comp_id"]))
            continue
        if token in by_id:
            selected.append(by_id[token])
    return list(dict.fromkeys(selected))


def _approval_required_result(discovery: dict[str, Path], candidates: list[dict], output_root: str, brief_path: str) -> dict[str, object]:
    approve_example = (
        f"python -m comp_agent.cli approve --brief {brief_path} --output-root {output_root} "
        f"--comp-id {candidates[0]['comp_id']}"
        if candidates
        else ""
    )
    continue_example = (
        f"python -m comp_agent.cli research --brief {brief_path} --output-root {output_root}; "
        f"python -m comp_agent.cli format --brief {brief_path} --output-root {output_root}; "
        f"python -m comp_agent.cli outputs --brief {brief_path} --output-root {output_root}; "
        f"python -m comp_agent.cli audit --brief {brief_path} --output-root {output_root}"
    )
    return {
        **{key: str(value) for key, value in discovery.items()},
        "approval_required": True,
        "candidate_count": len(candidates),
        "candidates": [
            {
                "index": index,
                "comp_id": candidate.get("comp_id"),
                "comp_name": candidate.get("comp_name"),
                "comp_type": candidate.get("comp_type"),
                "relevance_score": candidate.get("relevance_score"),
            }
            for index, candidate in enumerate(candidates, start=1)
        ],
        "next_approve_example": approve_example,
        "next_continue_example": continue_example,
    }


def _run_poc_with_approval_gate(args: argparse.Namespace, brief: ProjectBrief, stages: CompAppStages) -> dict[str, object]:
    discovery = stages.discover(brief)
    candidates = _load_candidates(discovery["candidate_comps"])

    approved_ids = list(args.comp_id or [])
    if args.auto_approve and not approved_ids:
        approved_ids = [str(candidate["comp_id"]) for candidate in candidates[: args.limit or len(candidates)]]

    if not approved_ids and sys.stdin.isatty():
        print("\nCandidate comps found. Approve the comps to continue:\n", file=sys.stderr)
        print("\n\n".join(_candidate_lines(candidates)), file=sys.stderr)
        print("\nEnter numbers or comp IDs separated by commas, 'all' to approve all, or 'none' to stop:", file=sys.stderr)
        try:
            approved_ids = _parse_approval_selection(input("> "), candidates)
        except EOFError:
            approved_ids = []

    if not approved_ids:
        return _approval_required_result(discovery, candidates, args.output_root, args.brief)

    paths: dict[str, Path] = dict(discovery)
    for stage_paths in (
        stages.approve(brief, approved_ids=approved_ids, limit=None, notes=args.notes),
        stages.research(brief),
        stages.format_outputs(brief),
        stages.generate_outputs(brief),
        stages.audit(brief),
    ):
        paths.update(stage_paths)
    return {key: str(value) for key, value in paths.items()}


def main() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "init":
        path = write_json(args.output, EXAMPLE_BRIEF)
        print(json.dumps({"project_brief": str(path)}, indent=2))
        return
    if args.command == "ui":
        run_server(host=args.host, port=args.port, output_root=args.output_root)
        return

    if args.command in {"discover", "approve", "research", "format", "outputs", "audit", "poc"}:
        brief = _brief_from_args(args)
        stages = CompAppStages(output_root=args.output_root)
        if args.command == "discover":
            result = stages.discover(brief)
        elif args.command == "approve":
            result = stages.approve(brief, approved_ids=args.comp_id or None, limit=args.limit, notes=args.notes)
        elif args.command == "research":
            result = stages.research(brief)
        elif args.command == "format":
            result = stages.format_outputs(brief)
        elif args.command == "outputs":
            result = stages.generate_outputs(brief)
        elif args.command == "audit":
            result = stages.audit(brief)
        else:
            result = _run_poc_with_approval_gate(args, brief, stages)
        print(json.dumps({key: str(value) for key, value in result.items()} if args.command != "poc" else result, indent=2))
        return

    brief = _brief_from_args(args)
    manifest = CompPackagePipeline(output_root=args.output_root).run(brief)
    print(json.dumps(asdict(manifest), indent=2))


if __name__ == "__main__":
    main()
