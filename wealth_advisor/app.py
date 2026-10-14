"""Composition root: the only place that knows concrete tool and agent classes."""

from __future__ import annotations

from dataclasses import dataclass

from .agents.advisor import AdvisorAgent
from .agents.analyzer import AnalyzerAgent
from .agents.data_fetcher import DataFetcherAgent
from .agents.orchestrator import Orchestrator
from .config import Settings
from .hitl import Reviewer
from .memory.long_term import LongTermMemory, build_long_term_memory
from .tools.analysis import AnomalyDetectionTool, MetricsTool
from .tools.base import ToolExecutor, ToolRegistry
from .tools.client_data import ClientDataLoaderTool, ClientDataNormalizerTool
from .tools.crm import CRMTool, MockCRMService
from .tools.market import MarketDataTool, MockMarketDataService
from .tools.memory_tools import RecallClientHistoryTool, StoreRunTool, StoreSessionTool
from .tools.narrator import NarrativeTool


@dataclass
class System:
    orchestrator: Orchestrator
    registry: ToolRegistry
    memory: LongTermMemory

    def close(self) -> None:
        self.memory.close()


def build_registry(settings: Settings, memory: LongTermMemory) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ClientDataLoaderTool(settings.data_dir / "clients"))
    reg.register(ClientDataNormalizerTool())
    reg.register(CRMTool(MockCRMService(settings.data_dir / "crm" / "crm.json", settings.crm_failure_mode)))
    reg.register(MarketDataTool(MockMarketDataService(settings.data_dir / "market" / "quotes.json",
                                                      settings.market_failure_mode)))
    reg.register(MetricsTool())
    reg.register(AnomalyDetectionTool(settings.thresholds, settings.policy))
    reg.register(NarrativeTool(settings.use_llm, settings.llm_model))
    reg.register(RecallClientHistoryTool(memory))
    reg.register(StoreRunTool(memory))
    reg.register(StoreSessionTool(memory))
    return reg


def build_system(settings: Settings, reviewer: Reviewer, memory_backend: str = "sqlite",
                 memory: LongTermMemory | None = None) -> System:
    memory = memory or build_long_term_memory(memory_backend, settings.memory_db)
    registry = build_registry(settings, memory)
    executor = ToolExecutor(
        registry,
        max_retries=settings.tool_max_retries,
        backoff_seconds=settings.tool_backoff_seconds,
        failure_threshold=settings.circuit_failure_threshold,
    )
    orchestrator = Orchestrator(
        agents=[DataFetcherAgent(), AnalyzerAgent(), AdvisorAgent()],
        tools=executor,
        reviewer=reviewer,
        settings=settings,
    )
    return System(orchestrator, registry, memory)
