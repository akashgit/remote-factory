"""Knowledge graph module — triplet-based representation of learned facts."""

from factory.knowledge.extractor import (
    extract_from_diff as extract_from_diff,
    extract_from_tool_calls as extract_from_tool_calls,
    extract_triplets_from_log as extract_triplets_from_log,
    parse_extraction_response as parse_extraction_response,
)
from factory.knowledge.insight import (
    Insight as Insight,
    InsightType as InsightType,
    format_insights as format_insights,
)
from factory.knowledge.gates import (
    evaluate_insights_gate as evaluate_insights_gate,
    generate_report as generate_report,
)
from factory.knowledge.models import (
    Entity as Entity,
    EntityType as EntityType,
    KnowledgeGraph as KnowledgeGraph,
    KnowledgeTaskConfig as KnowledgeTaskConfig,
    PredicateType as PredicateType,
    TauBenchTaskConfig as TauBenchTaskConfig,
    TauRunState as TauRunState,
    Triplet as Triplet,
)
from factory.knowledge.store import KnowledgeStore as KnowledgeStore
from factory.knowledge.tau_adapter import (
    compute_aggregate_score as compute_aggregate_score,
    evaluate_compare_gate as evaluate_compare_gate,
    evaluate_score_gate as evaluate_score_gate,
    extract_scores as extract_scores,
    parse_simulation as parse_simulation,
    run_tau_eval as run_tau_eval,
    write_failing_tasks as write_failing_tasks,
)

__all__ = [
    "Entity",
    "EntityType",
    "ExtractionResult",
    "Insight",
    "InsightType",
    "KnowledgeGraph",
    "KnowledgeStore",
    "KnowledgeTaskConfig",
    "PredicateType",
    "TauBenchTaskConfig",
    "Triplet",
    "compute_aggregate_score",
    "extract_from_diff",
    "extract_from_tool_calls",
    "extract_scores",
    "extract_triplets_from_log",
    "format_insights",
    "parse_extraction_response",
    "parse_simulation",
]
