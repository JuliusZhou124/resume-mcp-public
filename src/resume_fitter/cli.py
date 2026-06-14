"""CLI: compile a resume and report rendered layout metrics for one bullet.

Usage:
    python -m resume_fitter.cli resume.tex --bullet "Improved API performance"
    python -m resume_fitter.cli resume.tex --bullet-index 1
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .bullets import BulletLookupError, find_bullet_record
from .compare import compare_candidate
from .compile import CompileError, compile_tex
from .evaluate import compare_truth_risk, evaluate_bullet
from .measure import BulletNotFoundError, measure_layout
from .patch import diff_bullet, replace_bullet
from .shapes import box_warnings_json, evaluation_json, metrics_json, source_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile a LaTeX resume and measure a bullet's rendered layout."
    )
    parser.add_argument("tex_path", type=Path, help="Path to the resume .tex file")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--bullet", help="Case-insensitive substring of the bullet's rendered text"
    )
    group.add_argument(
        "--bullet-index",
        type=int,
        help="0-based index of the \\resumeItem in source order",
    )

    parser.add_argument(
        "--candidate",
        help=(
            "Replacement bullet text (LaTeX-ready, not escaped by this tool). "
            "If given, compiles the resume before and after substituting this "
            "text for the selected bullet and reports whether the page count "
            "changed."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write the --candidate substitution back to tex_path (requires "
            "--candidate). Without this flag, --candidate only produces a "
            "diff and comparison; tex_path is never modified."
        ),
    )
    parser.add_argument(
        "--tectonic", help="Path to the tectonic binary (default: search PATH)"
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print the JSON result"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.apply and args.candidate is None:
        parser.error("--apply requires --candidate")

    try:
        if args.bullet_index is not None:
            record = find_bullet_record(args.tex_path, index=args.bullet_index)
        else:
            record = find_bullet_record(args.tex_path, text=args.bullet)

        source = source_json(record)

        evaluation = evaluate_bullet(record.text)

        if args.candidate is not None:
            comparison = compare_candidate(
                args.tex_path, record, args.candidate, tectonic_path=args.tectonic
            )
            truth_risk = compare_truth_risk(record.text, args.candidate)

            if args.apply:
                patch = replace_bullet(args.tex_path, record, args.candidate)
            else:
                patch = diff_bullet(args.tex_path, record, args.candidate)

            result = {
                "bullet": record.text,
                "candidate": args.candidate,
                "source": source,
                "before": metrics_json(comparison.before, comparison.before_overfull),
                "after": metrics_json(comparison.after, comparison.after_overfull),
                "evaluation": evaluation_json(evaluation),
                "candidate_evaluation": evaluation_json(evaluate_bullet(args.candidate)),
                "diff": patch.diff,
                "notes": {
                    "page_count_changed": comparison.page_count_changed,
                    "box_warnings": box_warnings_json(comparison.after_box_warnings),
                    "truth_risk": truth_risk.truth_risk,
                    "changed_entities": truth_risk.changed_entities,
                    "applied": args.apply,
                },
            }
        else:
            with tempfile.TemporaryDirectory() as tmp:
                compile_result = compile_tex(
                    args.tex_path, Path(tmp), tectonic_path=args.tectonic
                )
                metrics = measure_layout(compile_result.pdf_path, record.text)

            result = {
                "bullet": record.text,
                "source": source,
                "layout": metrics_json(metrics, compile_result.overfull),
                "evaluation": evaluation_json(evaluation),
                "notes": {
                    "page_count_changed": None,
                    "box_warnings": box_warnings_json(compile_result.box_warnings),
                },
            }
    except (FileNotFoundError, CompileError, BulletLookupError, BulletNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
