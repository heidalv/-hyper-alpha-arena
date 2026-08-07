"""QAA 3.1 接入全面集成测试（隔离临时知识库，不污染生产数据）。"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
QAA_PKG = os.path.join(PROJECT_ROOT, "qaa_architecture_package")
BACKEND_ROOT = os.path.join(PROJECT_ROOT, "backend")
for p in (PROJECT_ROOT, BACKEND_ROOT, QAA_PKG):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture()
def isolated_qaa_knowledge(monkeypatch):
    """每个测试使用独立临时 QAA 知识库，并重置 bridge 单例。"""
    import backend.services.qaa_trade_memory_bridge as bridge

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("QAA_KNOWLEDGE_DIR", tmpdir)
        monkeypatch.setenv("QAA_SEMANTIC_MEMORY_ENABLED", "true")
        bridge._RAG = None
        yield tmpdir
        bridge._RAG = None


def test_qaa_package_version():
    import qaa

    assert getattr(qaa, "__version__", "") == "3.1.0-alphaarena"


def test_qaa_context_has_learning_subsystems():
    from qaa.core.context import QAAContext
    from qaa.platform.tenant import TenantContext

    ctx = QAAContext.for_tenant(TenantContext(tenant_id="test-tenant"))
    assert ctx.knowledge_store is not None
    assert ctx.rag_pipeline is not None
    assert ctx.memory_coordinator is not None
    assert ctx.consolidation_engine is not None
    assert ctx.learning_loop is not None


def test_three_layer_memory_ingest_and_retrieve(isolated_qaa_knowledge):
    from backend.services.qaa_trade_memory_bridge import (
        build_qaa_rag_lessons_section,
        get_qaa_trade_memory_stats,
        ingest_learning_artifact,
        ingest_trade_lesson,
        ingest_trade_outcome,
    )
    from backend.services.unified_learning_service import TradeOutcome

    lesson_ids = ingest_trade_lesson(
        lesson="BTC 追高止损过宽，下次缩小仓位并等回踩确认。",
        symbol="BTC",
        side="buy",
        pnl=-12.5,
        pnl_pct=-0.025,
        exit_reason="stop_loss",
        strategy_id="strat_a",
        tier="mid",
        trade_nature="swing",
        regime="trending",
    )
    assert lesson_ids

    outcome = TradeOutcome(
        source="paper",
        strategy_id="strat_a",
        symbol="ETH",
        side="sell",
        pnl=8.0,
        pnl_pct=0.016,
        tier="short",
        trade_nature="scalp",
        regime_at_entry="ranging",
        exit_channel="take_profit",
        confidence=0.72,
        duration_seconds=3600,
    )
    outcome_ids = ingest_trade_outcome(outcome)
    assert outcome_ids

    artifact_ids = ingest_learning_artifact(
        artifact_type="pattern_mining_report",
        text="BTC swing 在 trending regime 胜率 62%",
        payload={
            "strategy_id": "strat_a",
            "best_symbol": "BTC",
            "best_regime": "trending",
            "best_nature": "swing",
            "win_rate": 0.62,
            "total_records": 40,
        },
        strategy_id="strat_a",
        symbol="BTC",
        regime="trending",
        trade_nature="swing",
    )
    assert artifact_ids

    stats = get_qaa_trade_memory_stats()
    assert stats.get("total_chunks", stats.get("chunks", 0)) >= 3

    section = build_qaa_rag_lessons_section(
        symbols=["BTC", "ETH"],
        regime="trending",
        trade_nature="swing",
        limit=5,
    )
    assert "QAA 3.1 语义记忆" in section
    assert "BTC" in section or "ETH" in section


def test_qaa_semantic_memory_backend_registration():
    from backend.services.learning.backend_loader import BackendLoader
    from backend.services.learning.backend_registry import registry

    registry.clear()
    count = BackendLoader().load_all()
    names = set(registry.names())
    assert count >= 1
    assert "qaa_semantic_memory" in names


def test_qaa_semantic_memory_backend_skips_partial_close(isolated_qaa_knowledge):
    from backend.services.learning.backends.qaa_semantic_memory_backend import (
        QaaSemanticMemoryBackend,
    )
    from backend.services.unified_learning_service import TradeOutcome

    backend = QaaSemanticMemoryBackend()
    db = MagicMock()

    full_close = TradeOutcome(
        source="paper",
        strategy_id="s1",
        symbol="BTC",
        pnl=-5.0,
        metadata={"partial_close": False},
    )
    partial = TradeOutcome(
        source="paper",
        strategy_id="s1",
        symbol="BTC",
        pnl=-2.0,
        metadata={"partial_close": True},
    )
    assert backend.should_trigger(db, full_close) is True
    assert backend.should_trigger(db, partial) is False

    backend.handle_outcome(db, full_close)
    from backend.services.qaa_trade_memory_bridge import get_qaa_trade_memory_stats

    assert get_qaa_trade_memory_stats().get("total_chunks", 0) >= 1


def test_qaa_semantic_memory_backend_disabled(monkeypatch):
    from backend.services.learning.backends.qaa_semantic_memory_backend import (
        QaaSemanticMemoryBackend,
    )
    from backend.services.unified_learning_service import TradeOutcome

    monkeypatch.setenv("QAA_SEMANTIC_MEMORY_ENABLED", "false")
    backend = QaaSemanticMemoryBackend()
    outcome = TradeOutcome(source="paper", strategy_id="s1", symbol="BTC")
    assert backend.should_trigger(MagicMock(), outcome) is False


def test_hash_embedding_rag_core():
    from qaa.knowledge.base import RetrievalQuery
    from qaa.knowledge.embeddings import HashEmbeddingProvider, cosine_similarity
    from qaa.knowledge.rag import RAGPipeline
    from qaa.knowledge.stores.memory import InMemoryKnowledgeStore

    emb = HashEmbeddingProvider(dimensions=64)
    vec = emb.embed("test phrase")
    assert len(vec) == 64
    assert cosine_similarity(vec, vec) == pytest.approx(1.0, abs=1e-5)

    rag = RAGPipeline(store=InMemoryKnowledgeStore(), embedder=emb)
    ids = rag.ingest_text("止损距离要匹配波动率", domain="trading", source="t1")
    assert ids
    ctx = rag.retrieve(RetrievalQuery(query_text="止损", domain="trading", top_k=3))
    assert len(ctx.hits) >= 1


def test_jsonl_knowledge_store_persistence():
    from qaa.knowledge.base import RetrievalQuery
    from qaa.knowledge.rag import RAGPipeline
    from qaa.knowledge.stores.jsonl import JsonlKnowledgeStore

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "knowledge.jsonl")
        rag = RAGPipeline(store=JsonlKnowledgeStore(path=path, scope="test"))
        rag.ingest_text("持久化语义记忆测试", domain="trading", source="persist-1")

        reloaded = RAGPipeline(store=JsonlKnowledgeStore(path=path, scope="test"))
        ctx = reloaded.retrieve(
            RetrievalQuery(query_text="持久化", domain="trading", top_k=3)
        )
        assert len(ctx.hits) >= 1
