import asyncio

from factory.runners.types import AgentStep, ExecutionTrace, ToolCallTrace, ToolKind
from factory.store import ExperimentStore


class TestSaveTrace:
    def test_save_and_read(self, tmp_path):
        store = ExperimentStore(tmp_path)
        store.factory_dir.mkdir(parents=True, exist_ok=True)
        exp_dir = store._exp_dir(1)
        exp_dir.mkdir(parents=True, exist_ok=True)

        trace = ExecutionTrace(
            files_read=["a.py"],
            files_written=["b.py"],
            commands_executed=["pytest"],
            steps=[
                AgentStep(
                    step_index=0,
                    tool_calls=[
                        ToolCallTrace(tool_name="Read", kind=ToolKind.READ),
                    ],
                )
            ],
        )
        asyncio.run(store.save_trace(1, trace))

        data = store.read_trace(1)
        assert data is not None
        assert data["files_read"] == ["a.py"]
        assert len(data["steps"]) == 1

    def test_read_missing(self, tmp_path):
        store = ExperimentStore(tmp_path)
        assert store.read_trace(999) is None
