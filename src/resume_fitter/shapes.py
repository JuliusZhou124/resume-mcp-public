"""Shared JSON-shaping helpers for the CLI and MCP server.

Both entry points report the same underlying dataclasses
(``Bullet``, ``BulletMetrics``, ``BulletEvaluation``, ``BoxWarning``); this
module is the single place those shapes are defined so the two stay
consistent.
"""

from __future__ import annotations

from .bullets import Bullet, RoleBlock
from .compare import PlanComparison
from .evaluate import BulletEvaluation
from .measure import BulletMetrics
from .skills import SkillCategory, SkillEvidence
from .structure import StructureEdit


def box_warnings_json(warnings) -> list[dict]:
    return [
        {
            "type": w.kind,
            "amount_pt": w.amount_pt,
            "badness": w.badness,
            "src_lines": w.src_lines,
        }
        for w in warnings
    ]


def metrics_json(metrics: BulletMetrics, overfull: bool) -> dict:
    return {
        "lines": metrics.lines,
        "last_line_fullness": metrics.last_line_fullness,
        "has_orphan": metrics.has_orphan,
        "meets_fullness_requirement": metrics.meets_fullness_requirement,
        "overfull": overfull,
        "page_count": metrics.page_count,
    }


def evaluation_json(evaluation: BulletEvaluation) -> dict:
    return {
        "word_count": evaluation.word_count,
        "has_action_verb": evaluation.has_action_verb,
        "has_metric": evaluation.has_metric,
        "has_result_clause": evaluation.has_result_clause,
        "xyz_score": evaluation.xyz_score,
        "specificity_score": evaluation.specificity_score,
        "verbosity_score": evaluation.verbosity_score,
    }


def source_json(record: Bullet) -> dict:
    return {
        "id": record.id,
        "index": record.index,
        "start_line": record.start_line,
        "end_line": record.end_line,
        "section": record.section,
        "role": record.role,
        "context": " > ".join(part for part in (record.section, record.role) if part),
    }


def skill_source_json(record: SkillCategory) -> dict:
    return {
        "id": record.id,
        "index": record.index,
        "category": record.category,
        "items": record.items,
        "tokens": record.tokens,
        "start_line": record.start_line,
        "end_line": record.end_line,
    }


def skill_evidence_json(evidence: SkillEvidence) -> dict:
    return {
        "added": evidence.added,
        "removed": evidence.removed,
        "evidenced": evidence.evidenced,
        "unevidenced": evidence.unevidenced,
        "has_unevidenced": evidence.has_unevidenced,
    }


def role_block_json(block: RoleBlock) -> dict:
    return {
        "role": block.role,
        "section": block.section,
        "heading_macro": block.heading_macro,
        "heading_start_line": block.heading_start_line,
        "block_end_line": block.block_end_line,
        "item_list_start_line": block.item_list_start_line,
        "item_list_end_line": block.item_list_end_line,
        "has_item_list": block.has_item_list,
    }


def structure_diff_json(edit: StructureEdit) -> dict:
    return {"diff": edit.diff}


def plan_comparison_json(cmp: PlanComparison) -> dict:
    return {
        "before": {"page_count": cmp.before_page_count, "overfull": cmp.before_overfull},
        "after": {"page_count": cmp.after_page_count, "overfull": cmp.after_overfull},
        "page_count_changed": cmp.page_count_changed,
        "fits_one_page": cmp.fits_one_page,
        "applied_ops": cmp.applied_ops,
        "box_warnings": box_warnings_json(cmp.after_box_warnings),
    }
