"""ChessEvolveTask — demonstrates the Task contract with a real chess engine evolution loop.

Uses the python-chess library for legal move generation, board state, and game management.
The inner loop evolves a minimax engine with alpha-beta pruning via self-play evaluation.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Iterator

import structlog

from factory.task import (
    JSONScoring,
    Task,
    TaskConstraints,
    TaskDefinition,
    TaskInstance,
    VerifyResult,
)

log = structlog.get_logger()

_OPENING_POSITIONS = [
    ("startpos", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
    ("italian", "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"),
    ("sicilian", "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2"),
    ("queens-gambit", "rnbqkbnr/ppp1pppp/8/3p4/2PP4/8/PP2PPPP/RNBQKBNR b KQkq c3 0 2"),
]

BASE_ENGINE_SOURCE = textwrap.dedent("""\
    \"\"\"Minimax chess engine with alpha-beta pruning.\"\"\"

    import chess

    PIECE_VALUES = {
        chess.PAWN: 100,
        chess.KNIGHT: 320,
        chess.BISHOP: 330,
        chess.ROOK: 500,
        chess.QUEEN: 900,
        chess.KING: 20000,
    }

    def evaluate_board(board: chess.Board) -> float:
        if board.is_checkmate():
            return -99999 if board.turn else 99999
        if board.is_stalemate() or board.is_insufficient_material():
            return 0

        score = 0.0
        for square in chess.SQUARES:
            piece = board.piece_at(square)
            if piece is None:
                continue
            value = PIECE_VALUES.get(piece.piece_type, 0)
            if piece.color == chess.WHITE:
                score += value
            else:
                score -= value
        return score

    def minimax(board: chess.Board, depth: int, alpha: float, beta: float,
                maximizing: bool) -> float:
        if depth == 0 or board.is_game_over():
            return evaluate_board(board)

        if maximizing:
            max_eval = -float("inf")
            for move in board.legal_moves:
                board.push(move)
                val = minimax(board, depth - 1, alpha, beta, False)
                board.pop()
                max_eval = max(max_eval, val)
                alpha = max(alpha, val)
                if beta <= alpha:
                    break
            return max_eval
        else:
            min_eval = float("inf")
            for move in board.legal_moves:
                board.push(move)
                val = minimax(board, depth - 1, alpha, beta, True)
                board.pop()
                min_eval = min(min_eval, val)
                beta = min(beta, val)
                if beta <= alpha:
                    break
            return min_eval

    def best_move(board: chess.Board, depth: int = 2) -> chess.Move:
        best = None
        best_val = -float("inf") if board.turn else float("inf")

        for move in board.legal_moves:
            board.push(move)
            val = minimax(board, depth - 1, -float("inf"), float("inf"),
                          not board.turn)
            board.pop()
            if board.turn:
                if val > best_val:
                    best_val = val
                    best = move
            else:
                if val < best_val:
                    best_val = val
                    best = move
        return best
""")


def _ensure_chess_available() -> bool:
    """Check if the chess library is importable."""
    return importlib.util.find_spec("chess") is not None


def _play_game(
    engine_module: Any,
    base_module: Any,
    depth_evolved: int,
    depth_base: int,
    start_fen: str,
    max_moves: int = 80,
) -> dict[str, Any]:
    """Play one game: evolved (white) vs base (black). Returns result dict."""
    import chess  # type: ignore[import-not-found]

    board = chess.Board(start_fen)
    move_count = 0

    while not board.is_game_over() and move_count < max_moves:
        if board.turn == chess.WHITE:
            move = engine_module.best_move(board, depth=depth_evolved)
        else:
            move = base_module.best_move(board, depth=depth_base)

        if move is None:
            break
        board.push(move)
        move_count += 1

    result = board.result()
    outcome = board.outcome()

    if outcome is None:
        winner = None
        termination = "max_moves"
    else:
        winner = "white" if outcome.winner is True else ("black" if outcome.winner is False else None)
        termination = outcome.termination.name

    return {
        "result": result,
        "winner": winner,
        "termination": termination,
        "moves": move_count,
        "fen": board.fen(),
    }


def _load_engine_module(engine_path: Path, module_name: str = "engine") -> Any:
    """Dynamically load a chess engine Python file as a module."""
    spec = importlib.util.spec_from_file_location(module_name, engine_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load engine from {engine_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ChessEvolveTask(Task):
    """Evolves a chess engine via self-play evaluation.

    instances() yields game configurations with varying depths and openings.
    setup() writes a base minimax engine into the workspace.
    prompt() generates improvement hypotheses.
    verify() evaluates via self-play against the base engine.
    run() executes the full in-process evolution pipeline.
    """

    def __init__(self) -> None:
        defn = TaskDefinition(
            name="chess-evolve",
            description="Evolve a chess engine that beats the baseline via self-play",
            scoring=JSONScoring(metric_path="win_rate"),
            constraints=TaskConstraints(timeout=120, max_retries=1),
        )
        super().__init__(definition=defn)

    def instances(self) -> Iterator[TaskInstance]:
        depths = [1, 2, 3]
        for depth in depths:
            for opening_name, fen in _OPENING_POSITIONS:
                yield TaskInstance(
                    id=f"depth{depth}-{opening_name}",
                    metadata={
                        "depth": depth,
                        "num_games": 2,
                        "opening_fen": fen,
                        "opening_name": opening_name,
                    },
                )

    def setup(self, instance: TaskInstance, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        src_dir = workspace / "src"
        src_dir.mkdir(exist_ok=True)

        engine_path = src_dir / "engine.py"
        if not engine_path.exists():
            engine_path.write_text(BASE_ENGINE_SOURCE)

        base_dir = workspace / "base"
        base_dir.mkdir(exist_ok=True)
        base_path = base_dir / "engine.py"
        if not base_path.exists():
            base_path.write_text(BASE_ENGINE_SOURCE)

        if not _ensure_chess_available():
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "chess"],
                capture_output=True, timeout=60,
            )

    def prompt(self, instance: TaskInstance) -> str:
        depth = instance.metadata.get("depth", 2)
        opening = instance.metadata.get("opening_name", "standard")
        return (
            f"Improve the chess engine in src/engine.py. "
            f"The engine currently uses minimax with alpha-beta pruning at depth {depth}. "
            f"Focus on improving the evaluation function for {opening} positions. "
            f"Consider adding: piece-square tables, mobility scoring, pawn structure, "
            f"king safety, or better move ordering. "
            f"The engine must expose a best_move(board, depth) function."
        )

    def verify(self, instance: TaskInstance, workspace: Path) -> VerifyResult:
        if not _ensure_chess_available():
            return VerifyResult(
                passed=False, score=0.0,
                details={"error": "chess library not available"},
            )

        evolved_path = workspace / "src" / "engine.py"
        base_path = workspace / "base" / "engine.py"

        if not evolved_path.exists() or not base_path.exists():
            return VerifyResult(
                passed=False, score=0.0,
                details={"error": "engine files missing"},
            )

        try:
            evolved = _load_engine_module(evolved_path, "evolved_engine")
            base = _load_engine_module(base_path, "base_engine")
        except Exception as exc:
            return VerifyResult(
                passed=False, score=0.0,
                details={"error": f"engine load failed: {exc}"},
            )

        depth = instance.metadata.get("depth", 2)
        num_games = instance.metadata.get("num_games", 2)
        fen = instance.metadata.get("opening_fen", _OPENING_POSITIONS[0][1])

        wins = 0
        draws = 0
        losses = 0
        game_results = []

        for game_idx in range(num_games):
            try:
                if game_idx % 2 == 0:
                    result = _play_game(evolved, base, depth, depth, fen)
                    if result["winner"] == "white":
                        wins += 1
                    elif result["winner"] == "black":
                        losses += 1
                    else:
                        draws += 1
                else:
                    result = _play_game(base, evolved, depth, depth, fen)
                    if result["winner"] == "black":
                        wins += 1
                    elif result["winner"] == "white":
                        losses += 1
                    else:
                        draws += 1
                game_results.append(result)
            except Exception as exc:
                game_results.append({"error": str(exc)})
                losses += 1

        total = wins + draws + losses
        win_rate = (wins + 0.5 * draws) / total if total > 0 else 0.0

        return VerifyResult(
            passed=win_rate >= 0.5,
            score=win_rate,
            details={
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "win_rate": win_rate,
                "games": game_results,
                "depth": depth,
            },
        )

    def run(
        self,
        instance: TaskInstance,
        workspace: Path,
        workflow: Any = None,
    ) -> VerifyResult:
        """In-process evolution pipeline — does NOT shell out to factory ceo.

        Plays the engine against itself at different configurations.
        If a workflow is provided, its name influences the mutation strategy.
        """
        self.setup(instance, workspace)

        strategy = "default"
        if workflow is not None:
            wf_name = getattr(workflow, "name", "")
            if wf_name:
                strategy = wf_name

        log.info(
            "chess_evolve.run",
            instance=instance.id,
            strategy=strategy,
        )

        if strategy != "default" and strategy != "improve":
            self._apply_mutation(workspace, strategy)

        return self.verify(instance, workspace)

    def _apply_mutation(self, workspace: Path, strategy: str) -> None:
        """Apply a strategy-driven mutation to the evolved engine.

        Different workflow names map to different improvement strategies.
        This is a lightweight in-process mutation — no agent invocation.
        """
        engine_path = workspace / "src" / "engine.py"
        if not engine_path.exists():
            return

        source = engine_path.read_text()

        if "piece_square" not in source and strategy in ("research", "design"):
            pst_addition = textwrap.dedent("""\

    # Piece-square table for pawns (white perspective)
    PAWN_TABLE = [
         0,  0,  0,  0,  0,  0,  0,  0,
        50, 50, 50, 50, 50, 50, 50, 50,
        10, 10, 20, 30, 30, 20, 10, 10,
         5,  5, 10, 25, 25, 10,  5,  5,
         0,  0,  0, 20, 20,  0,  0,  0,
         5, -5,-10,  0,  0,-10, -5,  5,
         5, 10, 10,-20,-20, 10, 10,  5,
         0,  0,  0,  0,  0,  0,  0,  0,
    ]

    def piece_square_bonus(board):
        bonus = 0.0
        for sq in chess.SQUARES:
            piece = board.piece_at(sq)
            if piece and piece.piece_type == chess.PAWN:
                idx = sq if piece.color == chess.WHITE else chess.square_mirror(sq)
                val = PAWN_TABLE[idx]
                bonus += val if piece.color == chess.WHITE else -val
        return bonus
""")
            source = source.replace(
                "def evaluate_board(board: chess.Board) -> float:",
                pst_addition + "\ndef evaluate_board(board: chess.Board) -> float:",
            )
            engine_path.write_text(source)


meta = {
    "name": "chess-evolve",
    "description": "Evolve a chess engine via self-play evaluation",
}


def task() -> ChessEvolveTask:
    return ChessEvolveTask()
