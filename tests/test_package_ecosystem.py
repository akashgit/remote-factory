"""Tests for the package ecosystem prototype — composable workflow packages."""

from __future__ import annotations

from factory.workflow.package import (
    Conditional,
    Loop,
    MemoryDeclaration,
    OptKnob,
    Package,
    Parallel,
    Port,
    Sequential,
    StateContract,
)
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    VerdictType,
    Workflow,
)


# ── helpers ────────────────────────────────────────────────────────


def _make_simple_package(name: str, *, input_path: str = "", output_path: str = "") -> Package:
    """Create a minimal single-node package for testing composition."""
    node = FnNode(id=f"{name}_node", command=f"echo {name}", writes={output_path} if output_path else set())
    graph = Workflow(
        name=name,
        nodes={node.id: node},
        edges=[],
        start_node=node.id,
    )
    return Package(
        name=name,
        inputs=[Port(name="in", artifact_path=input_path)] if input_path else [],
        outputs=[Port(name="out", artifact_path=output_path)] if output_path else [],
        graph=graph,
        entry_node=node.id,
        exit_node=node.id,
    )


def _study_package() -> Package:
    """Wrap the study subgraph pattern as a Package."""
    nodes = {
        "graph_update": FnNode(
            id="graph_update",
            command="factory graph update {project_path}",
            writes={"graph.json"},
        ),
        "study": Study(
            id="study",
            command="factory study {project_path}",
            writes={".factory/strategy/observations.md"},
        ),
        "graph_explorer": AgentNode(
            id="graph_explorer",
            role=AgentRole.RESEARCHER,
            prompt_template="Explore the code graph.",
            reads={".factory/strategy/observations.md"},
            writes={".factory/strategy/graph-context.md"},
        ),
        "concat_study": FnNode(
            id="concat_study",
            command="cat observations.md graph-context.md > study-combined.md",
            reads={".factory/strategy/observations.md", ".factory/strategy/graph-context.md"},
            writes={".factory/strategy/study-combined.md"},
        ),
    }
    edges = [
        Edge(source="graph_update", target="study"),
        Edge(source="study", target="graph_explorer"),
        Edge(source="graph_explorer", target="concat_study"),
    ]
    graph = Workflow(name="study", nodes=nodes, edges=edges, start_node="graph_update")

    return Package(
        name="study",
        version="1.0.0",
        description="Graph-powered codebase study",
        outputs=[
            Port(name="study_combined", artifact_path=".factory/strategy/study-combined.md"),
        ],
        contract=StateContract(
            produces=frozenset({"observations_exist", "study_complete"}),
            capabilities=["codebase-analysis", "observation"],
        ),
        graph=graph,
        entry_node="graph_update",
        exit_node="concat_study",
    )


def _deep_qa_package() -> Package:
    """Wrap the deep-QA subgraph pattern as a Package."""
    nodes = {
        "health_checker": AgentNode(
            id="health_checker",
            role=AgentRole.HEALTH_CHECKER,
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/health-check.md"},
        ),
        "code_reviewer": AgentNode(
            id="code_reviewer",
            role=AgentRole.CODE_REVIEWER,
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/code-review.md"},
        ),
        "adversarial_tester": AgentNode(
            id="adversarial_tester",
            role=AgentRole.ADVERSARIAL_TESTER,
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/adversarial-qa.md"},
        ),
        "fork_qa": ForkNode(
            id="fork_qa",
            targets=["health_checker", "code_reviewer", "adversarial_tester"],
        ),
        "join_qa": JoinNode(
            id="join_qa",
            sources=["health_checker", "code_reviewer", "adversarial_tester"],
            reads={
                ".factory/reviews/health-check.md",
                ".factory/reviews/code-review.md",
                ".factory/reviews/adversarial-qa.md",
            },
        ),
    }
    edges = [Edge(source="fork_qa", target="join_qa")]
    graph = Workflow(name="deep-qa", nodes=nodes, edges=edges, start_node="fork_qa")

    return Package(
        name="deep-qa",
        version="1.0.0",
        description="Parallel health check, code review, and adversarial QA",
        inputs=[
            Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md"),
        ],
        outputs=[
            Port(name="health_check", artifact_path=".factory/reviews/health-check.md"),
            Port(name="code_review", artifact_path=".factory/reviews/code-review.md"),
            Port(name="adversarial_qa", artifact_path=".factory/reviews/adversarial-qa.md"),
        ],
        contract=StateContract(
            requires=frozenset({"build_complete"}),
            produces=frozenset({"qa_complete"}),
            capabilities=["health-check", "code-review", "adversarial-qa"],
        ),
        graph=graph,
        entry_node="fork_qa",
        exit_node="join_qa",
        knobs=[
            OptKnob(
                name="adversarial_timeout",
                kind="threshold",
                node_id="adversarial_tester",
                default=1800,
                bounds=[600, 3600],
            ),
        ],
    )


# ── Package primitive tests ────────────────────────────────────────


class TestPackagePrimitive:
    def test_create_package(self):
        pkg = _study_package()
        assert pkg.name == "study"
        assert pkg.version == "1.0.0"
        assert pkg.entry_node == "graph_update"
        assert pkg.exit_node == "concat_study"
        assert len(pkg.graph.nodes) == 4

    def test_port_paths(self):
        pkg = _study_package()
        assert pkg.output_paths == {".factory/strategy/study-combined.md"}
        assert pkg.input_paths == set()

    def test_configure_knobs(self):
        pkg = _deep_qa_package()
        configured = pkg.configure(adversarial_timeout=600)
        original_knob = pkg.knobs[0]
        new_knob = configured.knobs[0]
        assert original_knob.default == 1800
        assert new_knob.default == 600

    def test_compile_returns_workflow(self):
        pkg = _study_package()
        wf = pkg.compile()
        assert isinstance(wf, Workflow)
        assert wf.name == "study"
        assert len(wf.nodes) == 4

    def test_compile_is_deep_copy(self):
        pkg = _study_package()
        wf = pkg.compile()
        wf.name = "mutated"
        assert pkg.graph.name == "study"

    def test_memory_declaration(self):
        pkg = Package(
            name="test",
            graph=Workflow(
                name="test",
                nodes={"n": FnNode(id="n", command="echo")},
                edges=[],
                start_node="n",
            ),
            entry_node="n",
            exit_node="n",
            memory=[
                MemoryDeclaration(
                    namespace="test",
                    kind="vector",
                    schema_def={"finding": "str", "relevance": "float"},
                    retention="persistent",
                ),
            ],
        )
        assert len(pkg.memory) == 1
        assert pkg.memory[0].kind == "vector"


# ── Sequential composition ─────────────────────────────────────────


class TestSequential:
    def test_two_packages(self):
        a = _make_simple_package("a", output_path="a.md")
        b = _make_simple_package("b", input_path="a.md", output_path="b.md")
        composed = Sequential(a, b)

        assert composed.entry_node == "a_node"
        assert composed.exit_node == "b_node"
        assert len(composed.graph.nodes) == 2

        bridge = [e for e in composed.graph.edges if e.source == "a_node" and e.target == "b_node"]
        assert len(bridge) == 1

    def test_three_packages(self):
        a = _make_simple_package("a", output_path="a.md")
        b = _make_simple_package("b", input_path="a.md", output_path="b.md")
        c = _make_simple_package("c", input_path="b.md", output_path="c.md")
        composed = Sequential(a, b, c)

        assert composed.entry_node == "a_node"
        assert composed.exit_node == "c_node"
        assert len(composed.graph.nodes) == 3

    def test_single_package_passthrough(self):
        a = _make_simple_package("a")
        composed = Sequential(a)
        assert composed is a

    def test_inherits_first_inputs_last_outputs(self):
        a = _make_simple_package("a", input_path="input.md", output_path="mid.md")
        b = _make_simple_package("b", input_path="mid.md", output_path="output.md")
        composed = Sequential(a, b)

        assert composed.input_paths == {"input.md"}
        assert composed.output_paths == {"output.md"}

    def test_merges_knobs(self):
        qa = _deep_qa_package()
        study = _study_package()
        composed = Sequential(study, qa)
        assert len(composed.knobs) == 1
        assert composed.knobs[0].name == "adversarial_timeout"

    def test_merges_contracts(self):
        study = _study_package()
        qa = _deep_qa_package()
        composed = Sequential(study, qa)

        assert "study_complete" in composed.contract.produces
        assert "qa_complete" in composed.contract.produces
        # build_complete is required by QA but not produced by study, so it remains
        assert "build_complete" in composed.contract.requires
        # observations_exist is produced by study, so it's removed from requires
        assert "observations_exist" not in composed.contract.requires

    def test_study_then_qa_compiles(self):
        study = _study_package()
        qa = _deep_qa_package()
        composed = Sequential(study, qa)
        wf = composed.compile()

        assert isinstance(wf, Workflow)
        assert len(wf.nodes) == 9
        bridge = [e for e in wf.edges if e.source == "concat_study" and e.target == "fork_qa"]
        assert len(bridge) == 1


# ── Parallel composition ───────────────────────────────────────────


class TestParallel:
    def test_fork_join_structure(self):
        a = _make_simple_package("a", output_path="a.md")
        b = _make_simple_package("b", output_path="b.md")
        composed = Parallel(a, b)

        assert composed.entry_node.startswith("fork_")
        assert composed.exit_node.startswith("join_")
        assert isinstance(composed.graph.nodes[composed.entry_node], ForkNode)
        assert isinstance(composed.graph.nodes[composed.exit_node], JoinNode)

    def test_parallel_three_packages(self):
        a = _make_simple_package("a", output_path="a.md")
        b = _make_simple_package("b", output_path="b.md")
        c = _make_simple_package("c", output_path="c.md")
        composed = Parallel(a, b, c)

        fork = composed.graph.nodes[composed.entry_node]
        assert isinstance(fork, ForkNode)
        assert len(fork.targets) == 3

    def test_merges_all_outputs(self):
        a = _make_simple_package("a", output_path="a.md")
        b = _make_simple_package("b", output_path="b.md")
        composed = Parallel(a, b)

        assert composed.output_paths == {"a.md", "b.md"}


# ── Conditional composition ────────────────────────────────────────


class TestConditional:
    def test_gate_routes_to_branches(self):
        gate = GateNode(
            id="gate_lang",
            evaluator_type="fn",
            evaluator_command="detect_language",
        )
        py = _make_simple_package("python_qa", output_path="py.md")
        rs = _make_simple_package("rust_qa", output_path="rs.md")

        composed = Conditional(gate, {"PROCEED": py, "HALT": rs})

        assert composed.entry_node == "gate_lang"
        proceed_edges = [
            e for e in composed.graph.edges
            if e.source == "gate_lang" and e.condition == VerdictType.PROCEED
        ]
        halt_edges = [
            e for e in composed.graph.edges
            if e.source == "gate_lang" and e.condition == VerdictType.HALT
        ]
        assert len(proceed_edges) == 1
        assert proceed_edges[0].target == "python_qa_node"
        assert len(halt_edges) == 1
        assert halt_edges[0].target == "rust_qa_node"


# ── Loop composition ──────────────────────────────────────────────


class TestLoop:
    def test_loop_structure(self):
        body = _make_simple_package("build", output_path="build.md")
        gate = GateNode(
            id="gate_precheck",
            evaluator_type="fn",
            evaluator_command="factory precheck",
        )
        composed = Loop(body, gate)

        assert composed.entry_node == "build_node"
        assert composed.exit_node == "gate_precheck"

        reloop_edges = [
            e for e in composed.graph.edges
            if e.source == "gate_precheck" and e.condition == VerdictType.RELOOP
        ]
        assert len(reloop_edges) == 1
        assert reloop_edges[0].target == "build_node"

        exit_edges = [
            e for e in composed.graph.edges
            if e.source == "build_node" and e.target == "gate_precheck"
        ]
        assert len(exit_edges) == 1


# ── Nested composition ─────────────────────────────────────────────


class TestNestedComposition:
    def test_sequential_of_parallel(self):
        """Sequential(study, Parallel(health, review, adversarial))"""
        study = _study_package()
        qa = _deep_qa_package()
        composed = Sequential(study, qa)

        wf = composed.compile()
        assert len(wf.nodes) == 9
        assert wf.start_node == "graph_update"

    def test_design_mode_as_composition(self):
        """Demonstrate design mode as a package composition."""
        study = _study_package()
        qa = _deep_qa_package()

        build_node = AgentNode(
            id="builder",
            role=AgentRole.BUILDER,
            reads={".factory/strategy/current.md"},
            writes={".factory/reviews/builder-latest.md"},
        )
        build_pkg = Package(
            name="build",
            inputs=[Port(name="strategy", artifact_path=".factory/strategy/current.md")],
            outputs=[Port(name="build_output", artifact_path=".factory/reviews/builder-latest.md")],
            contract=StateContract(
                requires=frozenset({"strategy_complete"}),
                produces=frozenset({"build_complete"}),
                capabilities=["code-generation"],
            ),
            graph=Workflow(
                name="build",
                nodes={"builder": build_node},
                edges=[],
                start_node="builder",
            ),
            entry_node="builder",
            exit_node="builder",
        )

        pipeline = Sequential(study, build_pkg, qa, name="design")
        wf = pipeline.compile()

        assert wf.name == "design"
        assert wf.start_node == "graph_update"
        assert len(wf.nodes) == 10

        bridge_1 = [e for e in wf.edges if e.source == "concat_study" and e.target == "builder"]
        bridge_2 = [e for e in wf.edges if e.source == "builder" and e.target == "fork_qa"]
        assert len(bridge_1) == 1
        assert len(bridge_2) == 1

        assert "codebase-analysis" in pipeline.contract.capabilities
        assert "code-generation" in pipeline.contract.capabilities
        assert "adversarial-qa" in pipeline.contract.capabilities


# ── Compile round-trip ─────────────────────────────────────────────


class TestCompileRoundTrip:
    def test_compile_to_dict_and_back(self):
        study = _study_package()
        qa = _deep_qa_package()
        composed = Sequential(study, qa)

        wf = composed.compile()
        data = wf.to_dict()
        restored = Workflow.from_dict(data)

        assert restored.name == wf.name
        assert set(restored.nodes.keys()) == set(wf.nodes.keys())
        assert len(restored.edges) == len(wf.edges)
