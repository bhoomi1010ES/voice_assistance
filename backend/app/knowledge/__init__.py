"""Mode-aware selection and bounded composition of personal knowledge."""

from .context import KnowledgeContext, build_knowledge_context
from .engines import OkfKnowledgeEngine, RagKnowledgeEngine, configured_engines
from .selector import KnowledgeSelector
from .types import KnowledgeEngineResult, KnowledgeFact, KnowledgeSelection

__all__ = [
    "KnowledgeContext",
    "KnowledgeEngineResult",
    "KnowledgeFact",
    "KnowledgeSelection",
    "KnowledgeSelector",
    "OkfKnowledgeEngine",
    "RagKnowledgeEngine",
    "build_knowledge_context",
    "configured_engines",
]
