#!/usr/bin/env python3
"""Chess prompt evolution -- optimize chess play via composed Packages.

Each move runs the full Package pipeline through factory's real
WorkflowExecutor:

  Sequential(
    board_analysis,
    Parallel(tactical_scan, positional_eval),
    move_selector
  )

The outer loop mutates knobs (model, selector style) across
generations, plays real games against Stockfish, and selects the best.

Run: uv run python examples/chess_evolve.py
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import random
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.engine
from anthropic import AsyncAnthropicVertex

from factory.workflow.package import (
    Conditional,
    Loop,
    OptKnob,
    Package,
    Parallel,
    Port,
    Sequential,
    StateContract,
)
from factory.cycle_analyzer import CycleRecord
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    GateNode,
    Workflow,
)

# ── config ────────────────────────────────────────────────────────

# Dynamic prompt registry — Opus can add new entries at runtime
PROMPT_REGISTRY: dict[str, dict[str, str]] = {}

STOCKFISH_PATH = "/opt/homebrew/bin/stockfish"
ELO_OPTIONS = [1320, 1420, 1520, 1620]
GAMES_PER_EVAL = 1
MAX_MOVES = 60
NUM_GENERATIONS = 6
CANDIDATES_PER_GEN = 5
WORKSPACE = Path(os.environ.get("CHESS_WORKSPACE", "/tmp/chess-factory"))

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
WHITE = "\033[97m"

print = functools.partial(print, flush=True)  # type: ignore[assignment]


def header(text: str) -> None:
    print(f"\n{'━' * 70}")
    print(f"  {BOLD}{CYAN}{text}{RESET}")
    print(f"{'━' * 70}")


def bar(value: float, width: int = 25) -> str:
    filled = int(value * width)
    return f"{GREEN}{'█' * filled}{DIM}{'░' * (width - filled)}{RESET}"


# ── package definitions ──────────────────────────────────────────
#
# Each package is a real workflow node that reads/writes artifact files.
# The WorkflowExecutor runs them as a DAG -- one full execution per move.
#
#   board_state.md → board_analyst → [tactician ∥ positionalist] → selector → move.md
#

ANALYSIS_PROMPTS = {
    "concise": (
        "Analyze the chess position. "
        "Give exactly 3 bullet points: material count, biggest threat, best opportunity."
    ),
    "detailed": (
        "Analyze the chess position thoroughly: material balance, "
        "king safety (castled? pawn shield?), piece activity (well/poorly placed), "
        "pawn structure weaknesses, and immediate threats."
    ),
    "threat_focused": (
        "Analyze the chess position focusing on threats. "
        "What is the opponent threatening? What can you threaten? "
        "List every undefended piece on both sides."
    ),
}

TACTICAL_PROMPTS = {
    "broad": (
        "Scan the position for tactical opportunities: "
        "forks, pins, skewers, hanging pieces, checkmate threats. "
        "For each tactic found, name the move (in UCI notation) that exploits it."
    ),
    "mate_focused": (
        "Look for forcing sequences: checks, captures, and threats that limit "
        "the opponent's options. Prioritize checkmate patterns -- back rank mates, "
        "smothered mates, discovered checks. Name the moves in UCI notation."
    ),
    "material": (
        "Focus on material: can you capture an undefended piece? "
        "Can you win a trade (e.g. knight for rook)? Are any of your pieces "
        "hanging? List every capture that gains material with the UCI move."
    ),
}

POSITIONAL_PROMPTS = {
    "classical": (
        "Evaluate the position: pawn structure, central control, piece coordination, "
        "open files, and king safety. Suggest the 2-3 best positional moves in UCI notation."
    ),
    "dynamic": (
        "Focus on piece activity over structure. Which pieces are passive? "
        "How can you activate them? Suggest moves (in UCI) that improve your worst piece."
    ),
    "prophylactic": (
        "Think about what the opponent wants to do. What is their best plan? "
        "Suggest moves (in UCI) that prevent their plan while improving your position."
    ),
}

OPENING_HINTS = {
    "principled": (
        "\n\nGAME PHASE: OPENING. Follow opening principles strictly: "
        "1) Control the center with pawns (e4, d4 as White; e5, d5, c5, Nf6 as Black). "
        "2) Develop knights before bishops (Nf3, Nc3, Nf6, Nc6). "
        "3) Castle early (within first 8-10 moves). "
        "4) Do NOT play a3, a4, h3, h4, or move edge pawns in the opening. "
        "5) Do NOT move the queen before developing minor pieces. "
        "6) Do NOT move the same piece twice unless capturing or avoiding capture. "
        "Strong White openings: 1.e4 or 1.d4. Strong Black replies: vs 1.e4 play e5 or c5; vs 1.d4 play d5 or Nf6."
    ),
    "aggressive": (
        "\n\nGAME PHASE: OPENING. Play for initiative: "
        "1) Occupy the center with e4+d4 as White; challenge it as Black. "
        "2) Develop pieces toward the center and the opponent's king. "
        "3) Look for early tactical shots — gambits, piece sacrifices for development lead. "
        "4) Castle quickly, then attack. Do NOT play passively. "
        "5) Avoid a3/h3 unless preventing a specific pin or threat."
    ),
    "solid": (
        "\n\nGAME PHASE: OPENING. Play solid and safe: "
        "1) Control the center, but do NOT overextend. "
        "2) Develop all minor pieces before creating threats. "
        "3) Castle as early as possible — kingside preferred. "
        "4) Do NOT push pawns beyond the 4th rank in the opening. "
        "5) Avoid early queen moves and pawn weaknesses. Build a fortress first."
    ),
    "theory": (
        "\n\nGAME PHASE: OPENING. Play known strong openings from theory: "
        "As White: play the Italian Game (1.e4 e5 2.Nf3 Nc6 3.Bc4), the Ruy Lopez "
        "(1.e4 e5 2.Nf3 Nc6 3.Bb5), or the Queen's Gambit (1.d4 d5 2.c4). "
        "As Black vs 1.e4: play the Sicilian Defense (1...c5) or the French Defense (1...e6). "
        "As Black vs 1.d4: play the Queen's Gambit Declined (1...d5 2.c4 e6) or the King's Indian "
        "(1...Nf6 2.c4 g6 3...Bg7). "
        "Follow the main lines you know. Castle kingside. Complete development before attacking."
    ),
}

MIDDLEGAME_HINTS = {
    "safety_first": (
        "\n\nGAME PHASE: MIDDLEGAME. Before choosing a move: "
        "1) What did the opponent's last move THREATEN? Respond to it. "
        "2) Does your candidate move leave any piece undefended? If yes, pick a different move. "
        "3) Check for tactics: forks, pins, skewers, discovered attacks — for BOTH sides. "
        "4) If no tactics exist, improve your worst-placed piece or create a threat. "
        "5) Keep your king safe — do NOT open lines toward your own king. "
        "6) Look for pawn breaks to open the position when your pieces are active. "
        "7) Put rooks on open files and behind passed pawns."
    ),
    "attacking": (
        "\n\nGAME PHASE: MIDDLEGAME. Play for the attack: "
        "1) Look for forcing moves first: checks, captures, threats — in that order. "
        "2) Target the opponent's king — pile pieces toward it. "
        "3) Open files and diagonals pointing at the enemy king. "
        "4) Sacrifice material if it exposes the king or creates a mating attack. "
        "5) Only defend if you are under immediate threat of losing material."
    ),
    "positional": (
        "\n\nGAME PHASE: MIDDLEGAME. Play positionally: "
        "1) Improve your worst piece every move. "
        "2) Control key squares — especially outposts (squares your opponent can't attack with pawns). "
        "3) Create and exploit pawn structure weaknesses (isolated, doubled, backward pawns). "
        "4) Trade bad pieces for good ones (e.g. your bad bishop for their good knight). "
        "5) Only attack when your position is ready — accumulate small advantages first."
    ),
    "theory": (
        "\n\nGAME PHASE: MIDDLEGAME. Apply classical middlegame concepts: "
        "1) Nimzowitsch's blockade — place a piece (ideally a knight) on the square in front "
        "of the opponent's passed or isolated pawn. "
        "2) Minority attack — advance your queenside pawns (a4-b5) to create weaknesses in "
        "the opponent's pawn structure. "
        "3) Greek gift sacrifice (Bxh7+) — consider it when: bishop on d3, queen can reach h5, "
        "knight can reach g5, and the opponent has castled kingside with a standard pawn shield. "
        "4) Alekhine's gun — stack queen behind rooks on an open file. "
        "5) Tarrasch rule — rooks belong on the 7th rank or behind passed pawns."
    ),
}

ENDGAME_HINTS = {
    "technical": (
        "\n\nGAME PHASE: ENDGAME. Critical rules: "
        "1) Activate your king — move it toward the center and toward passed pawns. "
        "2) Passed pawns must be pushed. A passed pawn on the 6th/7th rank wins games. "
        "3) If ahead in material, trade pieces (not pawns) to simplify. "
        "4) If behind in material, avoid trades and create counterplay. "
        "5) Rook endgames: rooks belong BEHIND passed pawns (yours or the opponent's). "
        "6) Do NOT make pointless moves — every tempo matters in the endgame."
    ),
    "aggressive": (
        "\n\nGAME PHASE: ENDGAME. Push for the win: "
        "1) Advance your king aggressively — it's a fighting piece now. "
        "2) Create passed pawns on both sides of the board to stretch the opponent. "
        "3) Push passed pawns relentlessly — promotion is the goal. "
        "4) Use zugzwang — force the opponent into positions where any move loses."
    ),
    "defensive": (
        "\n\nGAME PHASE: ENDGAME. Hold the position: "
        "1) Keep your king in front of the opponent's passed pawns — blockade them. "
        "2) Maintain the opposition when kings face each other. "
        "3) Create a fortress if possible — a structure the opponent cannot break through. "
        "4) Trade pawns (not pieces) to reduce the opponent's winning chances. "
        "5) Activate your rook — passive defense loses. Counterattack on the other side."
    ),
    "theory": (
        "\n\nGAME PHASE: ENDGAME. Apply known endgame theory: "
        "1) Lucena position — if you have rook + pawn vs rook and your pawn is on the 7th rank "
        "with your king on the promotion square, build a bridge: rook to the 4th rank, "
        "then interpose on the 8th to block checks. "
        "2) Philidor position — if defending rook vs rook+pawn, keep your rook on the 6th rank "
        "while the enemy pawn hasn't reached the 6th; once it does, go to the back rank and check. "
        "3) Opposite-color bishops — these tend to be drawn even a pawn down; if behind, "
        "trade into this endgame. "
        "4) Knight vs bishop — knights are better in closed positions, bishops in open ones. "
        "5) Rule of the square — a king can catch a passed pawn if it's inside the square "
        "formed by the pawn and the promotion square."
    ),
}

def _get_phase_hint(cfg: 'PipelineConfig', phase: str) -> str:
    hints = {"opening": OPENING_HINTS, "middlegame": MIDDLEGAME_HINTS, "endgame": ENDGAME_HINTS}
    key = {"opening": cfg.opening_hint, "middlegame": cfg.middlegame_hint, "endgame": cfg.endgame_hint}[phase]
    table = hints[phase]
    if "+" in key:
        return " ".join(table.get(k, k) for k in key.split("+"))
    return table.get(key, key)


PROMPT_TABLES: dict[str, dict[str, str]] = {}  # populated after dicts are defined


def _get_prompt(knob_name: str, value: str) -> str:
    """Look up a prompt by knob name and value. Falls back to dynamic registry."""
    table = PROMPT_TABLES.get(knob_name, {})
    if value in table:
        return table[value]
    if knob_name in PROMPT_REGISTRY and value in PROMPT_REGISTRY[knob_name]:
        return PROMPT_REGISTRY[knob_name][value]
    # Handle + combos (e.g. "theory+safety_first")
    if "+" in value:
        parts = [table.get(v, PROMPT_REGISTRY.get(knob_name, {}).get(v, "")) for v in value.split("+")]
        combined = " ".join(p for p in parts if p)
        if combined:
            return combined
    # Unknown value (invented prompt lost on restart) — use the value as the prompt
    if value and value not in table:
        return value
    return table.get(list(table.keys())[0], "") if table else ""


def _register_prompt(knob_name: str, value: str, prompt_text: str) -> None:
    """Register a new prompt variant created by Opus."""
    PROMPT_REGISTRY.setdefault(knob_name, {})[value] = prompt_text
    # Auto-expand KNOB_SPACE
    for i, (name, choices) in enumerate(KNOB_SPACE):
        if name == knob_name and value not in choices:
            choices.append(value)
            break
    # Persist to recording so prompts survive restarts
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LIVE_DIR / "recording.jsonl", "a") as f:
        f.write(json.dumps({
            "t": time.monotonic(), "type": "prompt_invented",
            "knob": knob_name, "value": value, "prompt": prompt_text,
        }) + "\n")


def detect_phase(fen: str) -> str:
    """Detect game phase from piece count in FEN."""
    piece_chars = sum(1 for c in fen.split()[0] if c.isalpha() and c.lower() != 'k')
    if piece_chars > 26:
        return "opening"
    elif piece_chars > 10:
        return "middlegame"
    return "endgame"


SINGLE_DEEP_PROMPT = (
    "You are a chess expert. Analyze this position deeply. Consider: "
    "1) Tactical patterns (forks, pins, skewers, hanging pieces, mates) "
    "2) Positional factors (pawn structure, piece activity, king safety, central control) "
    "3) For your top 3 candidate moves, think ahead 2-3 moves. "
    "Conclude with your chosen move."
)

ENUMERATE_PROMPT = (
    "You are a chess expert. List the 5 best candidate moves in this position. "
    "For each, give a one-line evaluation. Format each line as: "
    "MOVE: <uci> SCORE: <1-10> REASON: <brief>. "
    "Then state which move you recommend."
)

BLUNDER_CHECK_PROMPTS = {
    "strict": (
        "A chess engine selected the move shown below. Check for blunders: "
        "1) Does this move hang a piece (leave it undefended and attacked)? "
        "2) Does it walk into a fork, pin, or skewer? "
        "3) Does it allow the opponent a forced checkmate? "
        "4) Does it lose material in a trade sequence? "
        "5) Does it weaken king safety? "
        "Be suspicious — assume the move is a blunder unless you can prove it's safe. "
        "If the move is a blunder, suggest a better move from the legal moves list. "
        "If the move is fine, confirm it."
    ),
    "standard": (
        "A chess engine selected the move shown below. Check for blunders: "
        "1) Does this move hang a piece (leave it undefended and attacked)? "
        "2) Does it walk into a fork, pin, or skewer? "
        "3) Does it allow the opponent a forced checkmate? "
        "If the move is a blunder, suggest a better move from the legal moves list. "
        "If the move is fine, confirm it."
    ),
    "lenient": (
        "A chess engine selected the move shown below. Only flag it as a blunder "
        "if it immediately loses material (hangs a piece) or allows forced checkmate. "
        "Positional inaccuracies are fine. "
        "If the move is a blunder, suggest a better move from the legal moves list. "
        "If the move is fine, confirm it."
    ),
}

CRITIQUE_PROMPTS = {
    "devils_advocate": (
        "You are a chess critic. A move has been selected for this position. "
        "Your job is to argue AGAINST this move. Find the strongest objection: "
        "What does the opponent do after this move? Does it ignore a threat? "
        "Does it miss a better alternative? Does it create a long-term weakness? "
        "Be specific — name the opponent's best reply and why it's dangerous. "
        "If you genuinely cannot find a problem, say 'no objection'."
    ),
    "sanity_check": (
        "You are a chess advisor doing a final sanity check. A move has been selected. "
        "Quickly verify: 1) Is the opponent's best reply dangerous? "
        "2) Are we missing an obvious better move? "
        "If the move looks reasonable, confirm it. Only object if something is clearly wrong."
    ),
}

GAME_PLAN_PROMPT = (
    "You are a chess strategist. Given the position, the move just played, "
    "and the previous plan (if any), write a 1-sentence strategic plan for the next few moves. "
    "Examples: 'Attack the weak f7 pawn with bishop and queen.' "
    "'Trade pieces to simplify into a won endgame.' "
    "'Develop remaining pieces and castle kingside.' "
    "Output ONLY the plan sentence, nothing else."
)

OPPONENT_MODEL_PROMPT = (
    "You are predicting the opponent's intentions. Given the position, "
    "what is the opponent most likely threatening? What is their plan? "
    "List the top 2 threats in order of danger. Be specific about which "
    "pieces and squares are involved."
)

_SELECTOR_FORMAT = (
    "\n\nCRITICAL: Your entire response must be exactly one UCI move "
    "(4 or 5 characters like e2e4 or e7e8q). No other text. No explanation. "
    "No punctuation. Just the move."
)

SELECTOR_PROMPTS = {
    "balanced": (
        "You are given tactical and positional analysis of a chess position. "
        "Weigh both equally. Choose the move that best balances tactics and position."
        + _SELECTOR_FORMAT
    ),
    "aggressive": (
        "You are given tactical and positional analysis of a chess position. "
        "Strongly prefer moves that create threats, win material, or attack the king. "
        "Only play defensively if you are losing material otherwise."
        + _SELECTOR_FORMAT
    ),
    "defensive": (
        "You are given tactical and positional analysis of a chess position. "
        "Strongly prefer safe, solid moves. Never sacrifice material. "
        "Prioritize king safety and avoiding blunders over attacking."
        + _SELECTOR_FORMAT
    ),
    "calculating": (
        "You are given tactical and positional analysis of a chess position. "
        "For your top 3 candidate moves, think ahead: your move, opponent's best reply, "
        "your follow-up. Pick the move with the best position after 3 moves."
        + _SELECTOR_FORMAT
    ),
}


PROMPT_TABLES.update({
    "tactical_style": TACTICAL_PROMPTS,
    "positional_style": POSITIONAL_PROMPTS,
    "verify_style": BLUNDER_CHECK_PROMPTS,
    "critique_style": CRITIQUE_PROMPTS,
    "opening_hint": OPENING_HINTS,
    "middlegame_hint": MIDDLEGAME_HINTS,
    "endgame_hint": ENDGAME_HINTS,
})


@dataclass
class PipelineConfig:
    """Tunable knobs -- only non-obvious trade-offs."""
    # Fixed
    model: str = "haiku"
    think_tokens: int = 200
    selector_style: str = "balanced"
    analysis_style: str = "concise"
    include_analyst: bool = True
    opponent_elo: int = 1320
    endgame_threshold: int = 25

    # Fixed (Opus found these always hurt or don't matter)
    use_game_context: bool = False
    use_game_plan: bool = False
    selector_tokens: int = 10
    verify_loop_target: str = "selector_only"
    verify_iterations: int = 2                # 1 = no loop, 2 = one retry, 3 = two retries

    # Tunable — topology
    pipeline_mode: str = "parallel"           # parallel / single_deep / enumerate
    use_verification: bool = True             # verify (critique + blunder) loop
    game_phase_routing: bool = False          # Conditional routes to phase-specific analysis
    skip_forced: bool = False                 # skip full pipeline when ≤2 legal moves
    use_opponent_model: bool = False          # predict opponent threats before selecting

    # Tunable — prompts (defaults from Opus reflection)
    tactical_style: str = "mate_focused"      # broad / mate_focused / material
    positional_style: str = "dynamic"         # classical / dynamic / prophylactic
    verify_style: str = "strict"              # strict / standard / lenient
    critique_style: str = "sanity_check"      # devils_advocate / sanity_check

    # Tunable — representation
    board_representation: str = "both"        # fen / ascii / both

    # Tunable — phase strategy
    opening_hint: str = "theory"              # theory / principled
    middlegame_hint: str = "theory"           # safety_first / attacking / positional / theory + combos
    endgame_hint: str = "theory"             # technical / aggressive / defensive / theory + combos

    @property
    def label(self) -> str:
        parts = [f"mode={self.pipeline_mode}"]
        if self.tactical_style != "mate_focused":
            parts.append(f"tact={self.tactical_style}")
        if self.positional_style != "dynamic":
            parts.append(f"pos={self.positional_style}")
        if self.verify_style != "strict":
            parts.append(f"verify={self.verify_style}")
        if self.critique_style != "sanity_check":
            parts.append(f"crit={self.critique_style}")
        if self.verify_loop_target != "selector_only":
            parts.append("reanalyze")
        if not self.use_verification:
            parts.append("no-verify")
        elif self.verify_iterations != 2:
            parts.append(f"verify-x{self.verify_iterations}")
        if self.selector_tokens != 10:
            parts.append(f"tok={self.selector_tokens}")
        if self.use_game_context:
            parts.append("ctx")
        if self.use_game_plan:
            parts.append("plan")
        if self.use_opponent_model:
            parts.append("opp")
        if self.skip_forced:
            parts.append("skip")
        if self.board_representation != "both":
            parts.append(f"repr={self.board_representation}")
        if self.game_phase_routing:
            parts.append("phased")
        if self.opening_hint != "theory":
            parts.append(f"open={self.opening_hint}")
        if self.middlegame_hint != "theory":
            parts.append(f"mid={self.middlegame_hint}")
        if self.endgame_hint != "theory":
            parts.append(f"end={self.endgame_hint}")
        return " ".join(parts)

    @property
    def full_label(self) -> str:
        return " | ".join(
            f"{k}={getattr(self, k)}" for k, _ in KNOB_SPACE if hasattr(self, k)
        )


def build_pipeline(cfg: PipelineConfig | None = None) -> Package:
    """Build the chess move pipeline as a Package composition."""
    if cfg is None:
        cfg = PipelineConfig()

    tactician = AgentNode(
        id="tactician", role=AgentRole.RESEARCHER,
        prompt_template=_get_prompt("tactical_style", cfg.tactical_style),
        reads={".factory/chess/board_state.md"},
        writes={".factory/chess/tactics.md"},
    )
    tactical_pkg = Package(
        name="tactical-scan", version="1.0.0",
        inputs=[Port(name="board", artifact_path=".factory/chess/board_state.md")],
        outputs=[Port(name="tactics", artifact_path=".factory/chess/tactics.md")],
        contract=StateContract(produces=frozenset({"tactics_complete"}),
                               capabilities=["chess-tactics"]),
        graph=Workflow(name="tactical-scan", nodes={"tactician": tactician},
                       edges=[], start_node="tactician"),
        entry_node="tactician", exit_node="tactician",
    )

    positionalist = AgentNode(
        id="positionalist", role=AgentRole.RESEARCHER,
        prompt_template=_get_prompt("positional_style", cfg.positional_style),
        reads={".factory/chess/board_state.md"},
        writes={".factory/chess/positional.md"},
    )
    positional_pkg = Package(
        name="positional-eval", version="1.0.0",
        inputs=[Port(name="board", artifact_path=".factory/chess/board_state.md")],
        outputs=[Port(name="positional", artifact_path=".factory/chess/positional.md")],
        contract=StateContract(produces=frozenset({"positional_complete"}),
                               capabilities=["chess-positional"]),
        graph=Workflow(name="positional-eval", nodes={"positionalist": positionalist},
                       edges=[], start_node="positionalist"),
        entry_node="positionalist", exit_node="positionalist",
    )

    selector = AgentNode(
        id="selector", role=AgentRole.STRATEGIST,
        prompt_template=SELECTOR_PROMPTS[cfg.selector_style],
        reads={".factory/chess/tactics.md", ".factory/chess/positional.md",
               ".factory/chess/board_state.md"},
        writes={".factory/chess/move.md"},
    )
    selector_pkg = Package(
        name="move-selector", version="1.0.0",
        inputs=[Port(name="tactics", artifact_path=".factory/chess/tactics.md"),
                Port(name="positional", artifact_path=".factory/chess/positional.md")],
        outputs=[Port(name="move", artifact_path=".factory/chess/move.md")],
        contract=StateContract(
            requires=frozenset({"tactics_complete", "positional_complete"}),
            produces=frozenset({"move_selected"}),
            capabilities=["chess-move-selection"]),
        graph=Workflow(name="move-selector", nodes={"selector": selector},
                       edges=[], start_node="selector"),
        entry_node="selector", exit_node="selector",
    )

    # Combined verify node: critique + blunder check in one call
    verify_prompt_parts = [_get_prompt("critique_style", cfg.critique_style),
                           _get_prompt("verify_style", cfg.verify_style)]
    verify_prompt = " ".join(verify_prompt_parts)

    verifier = AgentNode(
        id="verifier", role=AgentRole.RESEARCHER,
        prompt_template=verify_prompt,
        reads={".factory/chess/move.md", ".factory/chess/board_state.md"},
        writes={".factory/chess/verification.md"},
    )
    verify_pkg = Package(
        name="move-verify", version="1.0.0",
        inputs=[Port(name="move", artifact_path=".factory/chess/move.md")],
        outputs=[Port(name="verify", artifact_path=".factory/chess/verification.md")],
        contract=StateContract(requires=frozenset({"move_selected"}),
                               produces=frozenset({"move_verified"})),
        graph=Workflow(name="move-verify", nodes={"verifier": verifier},
                       edges=[], start_node="verifier"),
        entry_node="verifier", exit_node="verifier",
    )

    # Gate: reloop if blunder or valid objection found
    verify_gate = GateNode(
        id="verify_gate",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "from pathlib import Path; "
            "text = Path('{project_path}/.factory/chess/verification.md').read_text().lower() "
            "if Path('{project_path}/.factory/chess/verification.md').exists() else ''; "
            "has_issue = 'blunder' in text or ('objection' in text and 'no objection' not in text); "
            'print("RELOOP" if has_issue else "PROCEED")'
            '"'
        ),
    )

    parallel_analysis = Parallel(tactical_pkg, positional_pkg, name="parallel-analysis")

    # Build the analyst pkg early so it can join the parallel fork
    analyst = AgentNode(
        id="board_analyst", role=AgentRole.RESEARCHER,
        prompt_template=ANALYSIS_PROMPTS[cfg.analysis_style],
        reads={".factory/chess/board_state.md"},
        writes={".factory/chess/analysis.md"},
    )
    analyst_pkg = Package(
        name="board-analysis", version="1.0.0",
        inputs=[Port(name="board", artifact_path=".factory/chess/board_state.md")],
        outputs=[Port(name="analysis", artifact_path=".factory/chess/analysis.md")],
        contract=StateContract(produces=frozenset({"analysis_complete"}),
                               capabilities=["chess-analysis"]),
        graph=Workflow(name="board-analysis", nodes={"board_analyst": analyst},
                       edges=[], start_node="board_analyst"),
        entry_node="board_analyst", exit_node="board_analyst",
    )
    if cfg.include_analyst:
        parallel_analysis = Parallel(analyst_pkg, tactical_pkg, positional_pkg, name="parallel-analysis")

    # Phase-routed analysis: Conditional routes to phase-specific pipelines
    if cfg.game_phase_routing:
        # Opening: skip deep analysis, just use positional guidance
        opening_analyst = AgentNode(
            id="opening_analyst", role=AgentRole.RESEARCHER,
            prompt_template=(
                "This is a chess OPENING position. "
                "Play principled opening moves: center pawns (e4/d4 or e5/d5/c5), "
                "develop knights (Nf3/Nc3 or Nf6/Nc6), then bishops, then castle. "
                "NEVER play a3, a4, h3, h4, or edge pawn moves. "
                "Suggest the single best developing move in UCI notation."
            ),
            reads={".factory/chess/board_state.md"},
            writes={".factory/chess/analysis.md"},
        )
        opening_pkg = Package(
            name="opening-analysis", version="1.0.0",
            inputs=[Port(name="board", artifact_path=".factory/chess/board_state.md")],
            outputs=[Port(name="analysis", artifact_path=".factory/chess/analysis.md")],
            contract=StateContract(produces=frozenset({"analysis_complete"})),
            graph=Workflow(name="opening-analysis", nodes={"opening_analyst": opening_analyst},
                           edges=[], start_node="opening_analyst"),
            entry_node="opening_analyst", exit_node="opening_analyst",
        )
        endgame_analyst = AgentNode(
            id="endgame_analyst", role=AgentRole.RESEARCHER,
            prompt_template=(
                "This is a chess ENDGAME position. Focus on: "
                "1) King activity -- can your king advance safely? "
                "2) Passed pawns -- create or push them. "
                "3) Piece trades -- trade if ahead, avoid if behind. "
                "Name the best endgame move in UCI."
            ),
            reads={".factory/chess/board_state.md"},
            writes={".factory/chess/analysis.md"},
        )
        endgame_pkg = Package(
            name="endgame-analysis", version="1.0.0",
            inputs=[Port(name="board", artifact_path=".factory/chess/board_state.md")],
            outputs=[Port(name="analysis", artifact_path=".factory/chess/analysis.md")],
            contract=StateContract(produces=frozenset({"analysis_complete"})),
            graph=Workflow(name="endgame-analysis", nodes={"endgame_analyst": endgame_analyst},
                           edges=[], start_node="endgame_analyst"),
            entry_node="endgame_analyst", exit_node="endgame_analyst",
        )
        phase_gate = GateNode(
            id="phase_gate",
            evaluator_type="fn",
            evaluator_command=(
                'python3 -c "'
                "fen = open('{project_path}/.factory/chess/board_state.md').readline().split(': ')[1].strip(); "
                "pieces = sum(1 for c in fen.split()[0] if c.isalpha() and c.lower() != 'k'); "
                'print("HALT" if pieces > 26 else "RELOOP" if pieces <= 10 else "PROCEED")'
                '"'
            ),
        )
        analysis_step = Conditional(
            phase_gate,
            {"HALT": opening_pkg, "PROCEED": parallel_analysis, "RELOOP": endgame_pkg},
            name="phase-router",
        )
    else:
        analysis_step = parallel_analysis

    # Verification loop: selector picks → verifier checks → reloop if issue found
    # The verifier combines critique + blunder check in a single call
    selector.reads.add(".factory/chess/verification.md")
    if cfg.use_verification:
        if cfg.verify_loop_target == "full_reanalysis":
            loop_body = Sequential(analysis_step, selector_pkg, verify_pkg,
                                   name="analyze-select-verify")
        else:
            loop_body = Sequential(selector_pkg, verify_pkg, name="select-verify")
        select_and_verify = Loop(
            loop_body, verify_gate, max_iterations=cfg.verify_iterations, name="verify-loop",
        )
    else:
        select_and_verify = selector_pkg

    # Check if analysis is already inside a loop body (avoid node ID collision)
    analysis_in_loop = (
        cfg.use_verification and cfg.verify_loop_target == "full_reanalysis"
    )

    if analysis_in_loop:
        result = select_and_verify
    else:
        result = Sequential(analysis_step, select_and_verify, name="chess-pipeline")

    # Attach OptKnobs so factory's KNOB_MUTATE can see them
    knobs = []
    for knob_name, choices in KNOB_SPACE:
        val = getattr(cfg, knob_name, None)
        if val is None:
            continue
        # Coerce bools to strings for OptKnob (str | float only)
        def coerce(v: object) -> str | float:
            return str(v) if isinstance(v, bool) else v  # type: ignore[return-value]
        kind = "prompt" if isinstance(val, str) and knob_name not in ("pipeline_mode", "verify_loop_target", "board_representation") else "threshold" if isinstance(val, (int, float)) else "topology"
        knobs.append(OptKnob(
            name=knob_name, kind=kind, node_id="root",
            default=coerce(val), bounds=[coerce(c) for c in choices],
            expandable=kind == "prompt",
            expansion_hint=f"chess {knob_name} variant",
        ))
    return result.model_copy(update={"knobs": knobs})


# ── game engine ──────────────────────────────────────────────────


def setup_workspace(workspace: Path) -> None:
    """Create the workspace with .factory/chess/ directory."""
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / ".factory" / "chess").mkdir(parents=True)
    (workspace / ".factory" / "strategy").mkdir(parents=True)
    (workspace / ".factory" / "reviews").mkdir(parents=True)


def write_board_state(workspace: Path, board: chess.Board) -> None:
    """Write the current board state as an artifact for the pipeline to read."""
    legal_moves = [move.uci() for move in board.legal_moves]
    content = (
        f"FEN: {board.fen()}\n\n"
        f"You are playing {'White' if board.turn else 'Black'}.\n\n"
        f"Legal moves: {', '.join(legal_moves)}\n\n"
        f"Board:\n{board}\n"
    )
    (workspace / ".factory" / "chess" / "board_state.md").write_text(content)


def read_move(workspace: Path, board: chess.Board) -> str | None:
    """Read the move chosen by the pipeline from the artifact file."""
    move_file = workspace / ".factory" / "chess" / "move.md"
    if not move_file.exists():
        return None

    content = move_file.read_text().strip()
    legal_moves = [m.uci() for m in board.legal_moves]

    # Try exact match
    for token in content.split():
        cleaned = token.strip(".,!()[]{}\"'`\n")
        if cleaned in legal_moves:
            return cleaned

    # Try regex
    for match in re.finditer(r'[a-h][1-8][a-h][1-8][qrbn]?', content):
        if match.group() in legal_moves:
            return match.group()

    return None


# ── SDK-backed agent runner ───────────────────────────────────────
#
# Replaces factory's subprocess-based invoke_agent with in-process
# Vertex SDK calls. The WorkflowExecutor runs for real -- DAG traversal,
# fork/join, events -- only the agent backend is swapped.

_client: AsyncAnthropicVertex | None = None
_api_semaphore = asyncio.Semaphore(10)
SDK_MODEL = "claude-haiku-4-5@20251001"
MODEL_LABEL = "Haiku 4.5"


def _get_client() -> AsyncAnthropicVertex:
    global _client
    if _client is None:
        _client = AsyncAnthropicVertex(region="us-east5")
    return _client


async def _api_call(
    system_prompt: str, user_msg: str, max_tokens: int = 200,
) -> str:
    """Call Vertex AI API directly. Fast, truly async."""
    try:
        client = _get_client()
        resp = await asyncio.wait_for(
            client.messages.create(
                model=SDK_MODEL,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            ),
            timeout=15.0,
        )
        return resp.content[0].text.strip() if resp.content else ""
    except Exception:
        return ""


async def _cli_call_opus(system_prompt: str, user_msg: str, max_tokens: int = 500) -> str:
    """Call Opus via claude CLI."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", user_msg,
            "--model", "opus",
            "--append-system-prompt", system_prompt,
            "--max-turns", "1",
            "--output-format", "text",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        return stdout.decode().strip()
    except Exception:
        return ""


_node_reads_by_prompt: dict[str, set[str]] = {}


async def _sdk_invoke_agent(
    role: str,
    task: str,
    project_path: Path,
    model: str | None = None,
    timeout: float = 25.0,
    **kwargs: object,
) -> tuple[str, int]:
    """Drop-in for factory's invoke_agent using Vertex API."""
    system = f"You are a {role}."
    if role == "strategist":
        system += (
            "\n\nCRITICAL: When asked to choose a chess move, your ENTIRE response "
            "must be exactly one UCI move (4-5 characters like e2e4 or e7e8q). "
            "No explanation. No punctuation. Just the move."
        )
    # Inject declared reads as context (the real agent would read from disk)
    reads: set[str] = set()
    for key, node_reads in _node_reads_by_prompt.items():
        if task.startswith(key):
            reads = node_reads
            break
    if reads:
        context_parts = []
        for rpath in sorted(reads):
            fpath = project_path / rpath
            if fpath.exists():
                content = fpath.read_text().strip()
                if content:
                    context_parts.append(f"--- {Path(rpath).name} ---\n{content}")
        if context_parts:
            task = task + "\n\n" + "\n\n".join(context_parts)
    async with _api_semaphore:
        text = await _api_call(system, task)
        if text:
            reviews_dir = project_path / ".factory" / "reviews"
            reviews_dir.mkdir(parents=True, exist_ok=True)
            (reviews_dir / f"{role}-latest.md").write_text(text)
            return text, 0
        return "cli call failed", 1


async def _call_llm(
    system_prompt: str, user_msg: str, max_tokens: int = 200,
    model_override: str = "",
) -> str:
    """Call LLM via Vertex API."""
    async with _api_semaphore:
        return await _api_call(system_prompt, user_msg, max_tokens)


def _ascii_board(board: chess.Board) -> str:
    """Render board as ASCII with coordinates."""
    symbols = {"R": "R", "N": "N", "B": "B", "Q": "Q", "K": "K", "P": "P",
               "r": "r", "n": "n", "b": "b", "q": "q", "k": "k", "p": "p"}
    lines = ["  a b c d e f g h"]
    for rank in range(7, -1, -1):
        row = f"{rank+1} "
        for file in range(8):
            piece = board.piece_at(chess.square(file, rank))
            row += (symbols[piece.symbol()] if piece else ".") + " "
        lines.append(row + f"{rank+1}")
    lines.append("  a b c d e f g h")
    return "\n".join(lines)


def _board_user_msg(
    board: chess.Board, game_moves: list[str] | None = None,
    use_context: bool = False, cfg: PipelineConfig | None = None,
) -> str:
    legal_moves = [m.uci() for m in board.legal_moves]
    phase = detect_phase(board.fen())
    board_repr = cfg.board_representation if cfg else "fen"
    parts = []
    if board_repr in ("fen", "both"):
        parts.append(f"Position (FEN): {board.fen()}")
    if board_repr in ("ascii", "both"):
        parts.append(f"Board:\n{_ascii_board(board)}")
    parts.extend([
        f"You are {'White' if board.turn else 'Black'}.",
        f"Legal moves: {', '.join(legal_moves)}",
    ])
    if cfg:
        parts.append(_get_phase_hint(cfg, phase))
    else:
        parts.append(OPENING_HINTS["principled"] if phase == "opening"
                     else MIDDLEGAME_HINTS["safety_first"] if phase == "middlegame"
                     else ENDGAME_HINTS["technical"])
    if use_context and game_moves:
        move_pairs = []
        for i in range(0, len(game_moves), 2):
            num = i // 2 + 1
            w = game_moves[i]
            b = game_moves[i + 1] if i + 1 < len(game_moves) else ""
            move_pairs.append(f"{num}.{w} {b}".strip())
        parts.insert(1, f"Game so far: {' '.join(move_pairs)}")
        parts.insert(2, f"Move number: {len(game_moves) // 2 + 1}")
    return "\n".join(parts)


def _extract_move(text: str, board: chess.Board) -> str | None:
    legal_moves = [m.uci() for m in board.legal_moves]
    # Try UCI first
    for token in text.split():
        cleaned = token.strip(".,!()[]{}\"'`\n")
        if cleaned in legal_moves:
            return cleaned
    for match in re.finditer(r'[a-h][1-8][a-h][1-8][qrbn]?', text):
        if match.group() in legal_moves:
            return match.group()
    # Try SAN (e.g. "e5", "Nf3", "Bxc6")
    for token in text.split():
        cleaned = token.strip(".,!()[]{}\"'`+#\n")
        try:
            move = board.parse_san(cleaned)
            return move.uci()
        except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError):
            pass
    return None


async def get_pipeline_move(
    pipeline: Package,
    board: chess.Board,
    cfg: PipelineConfig,
    workspace: Path,
    game_tag: str = "",
    llm_white: bool = True,
    stockfish_elo: int = 1320,
    game_moves: list[str] | None = None,
    move_count: int = 0,
    gen: int = 0,
    eval_curve: list[int] | None = None,
    config_label: str = "",
    full_config_label: str = "",
    extra_context: str = "",
) -> tuple[str | None, int, dict[str, str]]:
    """Run the Package pipeline through factory's real WorkflowExecutor.

    Uses SDK-backed invoke_agent for speed (no CLI subprocesses).
    Returns (move_uci, nodes_executed, node_outputs).
    """
    from unittest.mock import patch
    from factory.workflow.executor import WorkflowExecutor

    # Write board state for pipeline nodes to read
    user_msg = _board_user_msg(board, game_moves, use_context=cfg.use_game_context, cfg=cfg)
    if extra_context:
        user_msg += f"\n{extra_context}"
    chess_dir = workspace / ".factory" / "chess"
    chess_dir.mkdir(parents=True, exist_ok=True)
    (chess_dir / "board_state.md").write_text(user_msg)

    # Clear previous analysis
    for name in ["analysis.md", "tactics.md", "positional.md", "move.md", "critique.md", "verification.md"]:
        f = chess_dir / name
        if f.exists():
            f.unlink()

    # Compile Package to Workflow and run through real executor
    wf = pipeline.compile()
    node_outputs: dict[str, str] = {}

    # Build reads lookup so _sdk_invoke_agent can filter context per node
    _node_reads_by_prompt.clear()
    for node in wf.nodes.values():
        if hasattr(node, 'prompt_template') and hasattr(node, 'reads'):
            key = node.prompt_template[:60]
            _node_reads_by_prompt[key] = node.reads

    # Hook into executor events for live UI
    def make_hooked_emit(original_emit):
        NODE_NAME_MAP = {
            "board_analyst": "analyst",
            "tactician": "tactician",
            "positionalist": "positionalist",
            "selector": "selector",
            "opening_analyst": "opening",
            "endgame_analyst": "endgame",
            "phase_gate": "phase_gate",
            "verifier": "verifier",
            "verify_gate": "verifier",
        }
        def _hooked_emit(event_type, event):
            original_emit(event_type, event)
            if not game_tag:
                return
            node_id = getattr(event, "node_id", "")
            if event_type == "node.started":
                active = NODE_NAME_MAP.get(node_id, node_id)
                broadcast_game_state(
                    game_tag, board, game_moves or [], llm_white,
                    stockfish_elo, move_count=move_count,
                    gen=gen, config=config_label,
                    whose_turn="llm", active_node=active,
                    node_outputs=node_outputs,
                    eval_curve=eval_curve, full_config=full_config_label,
                )
            elif event_type == "node.completed":
                if executor_ref and event.node_id in executor_ref[0].result.node_outputs:
                    output = executor_ref[0].result.node_outputs[event.node_id]
                    node_outputs[event.node_id] = output[:500]
                    # Write to declared files immediately so downstream nodes can read them
                    node_def = wf.nodes.get(event.node_id)
                    if node_def and node_def.writes and output:
                        for wpath in node_def.writes:
                            fpath = workspace / wpath
                            fpath.parent.mkdir(parents=True, exist_ok=True)
                            fpath.write_text(output)
        return _hooked_emit

    executor_ref: list[WorkflowExecutor] = []
    with patch("factory.agents.runner.invoke_agent", side_effect=_sdk_invoke_agent):
        executor = WorkflowExecutor(workflow=wf, project_path=workspace, auto_approve=True)
        executor_ref.append(executor)
        executor.completed_files.add(".factory/chess/board_state.md")
        executor.completed_files.add(".factory/chess/verification.md")
        executor._emit = make_hooked_emit(executor._emit)  # type: ignore[assignment]
        result = await executor.execute()

    # Write node outputs to their declared files (executor stores outputs
    # in memory but doesn't write to disk for agent nodes)
    for nid, output in result.node_outputs.items():
        node = wf.nodes.get(nid)
        if node and node.writes and output:
            for wpath in node.writes:
                fpath = workspace / wpath
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(output)

    move_file = chess_dir / "move.md"
    move = None
    if move_file.exists():
        move = _extract_move(move_file.read_text(), board)

    # Read blunder check output if it exists
    blunder_file = chess_dir / "blunder_check.md"
    if blunder_file.exists():
        blunder_text = blunder_file.read_text().strip()
        node_outputs["blunder_check"] = blunder_text[:500]
        alt_move = _extract_move(blunder_text, board)
        if alt_move and alt_move != move and "blunder" in blunder_text.lower():
            node_outputs["blunder_check"] += f" [OVERRIDE: {move} -> {alt_move}]"
            move = alt_move

    return move, result.nodes_executed, node_outputs


LIVE_DIR = WORKSPACE / "live"


def broadcast_game_state(
    tag: str, board: chess.Board, game_moves: list[str],
    llm_white: bool, elo: int, result: str | None = None,
    move_count: int = 0, gen: int = 0, config: str = "",
    whose_turn: str = "", active_node: str = "",
    node_outputs: dict[str, str] | None = None,
    eval_curve: list[int] | None = None,
    full_config: str = "",
) -> None:
    """Write game state to a JSON file for the live web UI."""
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = tag.replace(":", "_").replace("(", "").replace(")", "").replace(" ", "_")[:80]
    is_llm_turn = (board.turn == chess.WHITE) == llm_white
    data = {
        "tag": tag,
        "fen": board.fen(),
        "moves": game_moves,
        "llm_white": llm_white,
        "elo": elo,
        "result": result,
        "move_count": move_count,
        "gen": gen,
        "config": config,
        "full_config": full_config,
        "whose_turn": whose_turn or ("llm" if is_llm_turn else "stockfish"),
        "active_node": active_node,
        "node_outputs": node_outputs or {},
        "eval_curve": eval_curve or [],
        "game_phase": detect_phase(board.fen()),
    }
    (LIVE_DIR / f"{safe_name}.json").write_text(json.dumps(data))
    # Append to recording for replay
    with open(LIVE_DIR / "recording.jsonl", "a") as f:
        f.write(json.dumps({"t": time.monotonic(), "type": "game", "file": safe_name, **data}) + "\n")




def broadcast_eval_result(
    label: str, cfg: PipelineConfig, result: EvalResult,
    gen: int, is_best: bool = False,
) -> None:
    """Append an eval result to the experiment log for the web UI."""
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "label": label,
        "gen": gen,
        "score": result.composite_score,
        "win_rate": result.win_rate,
        "wins": result.wins,
        "draws": result.draws,
        "losses": result.losses,
        "errors": result.total_errors,
        "pipelines": result.total_pipeline_runs,
        "elo": cfg.opponent_elo,
        "avg_eval": result.avg_eval,
        "blunders": result.blunder_count,
        "moves": result.total_moves,
        "composite": result.composite_score,
        "config": cfg.label,
        "is_best": is_best,
    }
    log_path = LIVE_DIR / "experiment_log.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    with open(LIVE_DIR / "recording.jsonl", "a") as f:
        f.write(json.dumps({"t": time.monotonic(), "type": "eval", **entry}) + "\n")


@dataclass
class EvalResult:
    wins: int = 0
    draws: int = 0
    losses: int = 0
    total_moves: int = 0
    total_errors: int = 0
    total_pipeline_runs: int = 0
    games: list[dict] = field(default_factory=list)

    @property
    def score(self) -> float:
        total = self.wins + self.draws + self.losses
        return (self.wins + 0.5 * self.draws) / max(total, 1)

    @property
    def avg_eval(self) -> float:
        """Average centipawn eval across all games."""
        all_evals = []
        for g in self.games:
            all_evals.extend(g.get("eval_curve", []))
        return sum(all_evals) / max(len(all_evals), 1)

    @property
    def blunder_count(self) -> int:
        """Count moves where eval dropped >200cp."""
        total = 0
        for g in self.games:
            curve = g.get("eval_curve", [])
            total += sum(1 for i in range(1, len(curve)) if curve[i] - curve[i-1] < -200)
        return total

    @property
    def composite_score(self) -> float:
        """Win-oriented scoring: wins dominate, losses reward survival."""
        base = self.avg_eval - 20 * self.blunder_count + 8 * self.total_moves
        return base + 500 * self.wins + 200 * self.draws

    def to_cycle_record(self, gen: int = 0) -> CycleRecord:
        """Build a factory CycleRecord from chess results."""
        from factory.cycle_analyzer import AgentStep, CycleRecord, ExperimentRecord
        steps = []
        experiments = []
        order = 0
        for i, g in enumerate(self.games):
            curve = g.get("eval_curve", [])
            blunders = sum(1 for j in range(1, len(curve)) if curve[j] - curve[j-1] < -200)
            won = g.get("result") == "win"
            drew = g.get("result") == "draw"
            # One AgentStep per pipeline node role per game
            for role in ["tactician", "positionalist", "selector", "verifier"]:
                steps.append(AgentStep(
                    order=order, role=role, started_at="",
                    duration_s=0, cost_usd=None, output_tokens=None,
                    succeeded=g.get("llm_errors", 0) == 0,
                    node_id=f"game{i}_{role}",
                ))
                order += 1
            # Blunder steps — mark as failed
            for b in range(blunders):
                steps.append(AgentStep(
                    order=order, role="blunder", started_at="",
                    duration_s=0, cost_usd=None, output_tokens=None,
                    succeeded=False, error=f"blunder #{b+1} (>200cp drop)",
                    node_id=f"game{i}_blunder{b}",
                ))
                order += 1
            avg = sum(curve) / len(curve) if curve else 0
            experiments.append(ExperimentRecord(
                exp_id=i, hypothesis=g.get("tag", ""),
                verdict="keep" if won or drew else "revert",
                score_before=0, score_after=avg,
                score_delta=avg, cost_usd=0, duration_s=0,
            ))
        curves = []
        for g in self.games:
            curves.extend(g.get("eval_curve", []))
        return CycleRecord(
            cycle_number=gen, mode="chess", started_at=None, ended_at=None,
            duration_s=0,
            score_start=0, score_end=self.composite_score,
            score_delta=self.composite_score,
            score_trajectory=[float(c) for c in curves],
            experiments=experiments,
            kept=self.wins + self.draws, reverted=self.losses,
            errored=self.total_errors,
            keep_rate=(self.wins + self.draws) / max(self.wins + self.draws + self.losses, 1),
            steps=steps,
        )

    @property
    def win_rate(self) -> str:
        return f"+{self.wins}={self.draws}-{self.losses}"


PIECE_MAP = {
    "R": "♖", "N": "♘", "B": "♗", "Q": "♕", "K": "♔", "P": "♙",
    "r": "♜", "n": "♞", "b": "♝", "q": "♛", "k": "♚", "p": "♟",
}


def format_moves(game_moves: list[str]) -> str:
    """Format a move list as standard chess notation: 1.e2e4 e7e5 2.g1f3 ..."""
    paired = []
    for m in range(0, len(game_moves), 2):
        num = m // 2 + 1
        white = game_moves[m]
        black = game_moves[m + 1] if m + 1 < len(game_moves) else ""
        paired.append(f"{num}.{white} {black}".strip())
    return " ".join(paired)


def render_board(board: chess.Board, llm_white: bool, elo: int, tag: str = "") -> str:
    """Render the board as a unicode diagram with player labels."""
    top_label = f"Stockfish {elo}" if llm_white else "Haiku (LLM)"
    bot_label = "Haiku (LLM)" if llm_white else f"Stockfish {elo}"
    prefix = f"[{tag}] " if tag else ""
    lines = []
    lines.append(f"      {prefix}{top_label}")
    lines.append("      ┌───┬───┬───┬───┬───┬───┬───┬───┐")
    for rank in range(7, -1, -1):
        cells = []
        for file in range(8):
            sq = chess.square(file, rank)
            piece = board.piece_at(sq)
            sym = PIECE_MAP.get(piece.symbol(), piece.symbol()) if piece else " "
            cells.append(f" {sym} ")
        lines.append(f"    {rank+1} │" + "│".join(cells) + "│")
        if rank > 0:
            lines.append("      ├───┼───┼───┼───┼───┼───┼───┼───┤")
    lines.append("      └───┴───┴───┴───┴───┴───┴───┴───┘")
    lines.append("        a   b   c   d   e   f   g   h")
    lines.append(f"      {prefix}{bot_label}")
    return "\n".join(lines)


async def play_game(
    pipeline: Package,
    cfg: PipelineConfig,
    llm_plays_white: bool = True,
    game_tag: str = "",
    gen: int = 0,
) -> dict:
    """Play one game using the pipeline for each LLM move."""
    # Each game gets its own workspace for the executor
    safe_tag = game_tag.replace(":", "_").replace("(", "").replace(")", "").replace(" ", "_")[:80]
    workspace = WORKSPACE / safe_tag
    setup_workspace(workspace)

    board = chess.Board()
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    engine.configure({"UCI_LimitStrength": True, "UCI_Elo": cfg.opponent_elo})
    # Full-strength evaluator for position scoring
    evaluator = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)

    move_count = 0
    llm_errors = 0
    pipeline_runs = 0
    game_moves: list[str] = []
    eval_curve: list[int] = []
    current_plan: str = ""

    try:
        while not board.is_game_over() and move_count < MAX_MOVES:
            is_llm_turn = (board.turn == chess.WHITE) == llm_plays_white

            if is_llm_turn:
                legal = list(board.legal_moves)

                # Skip forced moves (≤2 options, no analysis needed)
                if cfg.skip_forced and len(legal) <= 2:
                    move_uci = legal[0].uci()
                    board.push(legal[0])
                    game_moves.append(move_uci)
                    move_count += 1
                    try:
                        info = evaluator.analyse(board, chess.engine.Limit(time=0.05))
                        cp = info["score"].white().score(mate_score=10000)
                        eval_curve.append(cp if llm_plays_white else -cp)
                    except Exception:
                        eval_curve.append(0)
                    broadcast_game_state(
                        game_tag, board, game_moves, llm_plays_white,
                        cfg.opponent_elo, result=None, move_count=move_count,
                        gen=gen, config=cfg.label, eval_curve=eval_curve, full_config=cfg.full_label,
                    )
                    print(f"      {DIM}[{game_tag}] {format_moves(game_moves)} (forced){RESET}")
                    continue

                # Opponent modeling: predict threats before analysis
                opponent_context = ""
                if cfg.use_opponent_model:
                    user_msg = _board_user_msg(board, game_moves, use_context=True)
                    opponent_context = await _call_llm(OPPONENT_MODEL_PROMPT, user_msg, max_tokens=150)

                # Dynamic endgame style switching
                effective_cfg = cfg

                # Inject game plan + opponent model into the pipeline context
                extra_context = ""
                if current_plan:
                    extra_context += f"\nCurrent plan: {current_plan}"
                if opponent_context:
                    extra_context += f"\nOpponent threats: {opponent_context}"

                try:
                    move_uci, nodes_executed, node_outputs = await asyncio.wait_for(
                        get_pipeline_move(
                            pipeline, board, effective_cfg, workspace,
                            game_tag=game_tag, llm_white=llm_plays_white,
                            stockfish_elo=cfg.opponent_elo, game_moves=game_moves,
                            move_count=move_count, gen=gen, config_label=cfg.label,
                            full_config_label=cfg.full_label,
                            eval_curve=eval_curve, extra_context=extra_context,
                        ),
                        timeout=45.0,
                    )
                except asyncio.TimeoutError:
                    move_uci = None
                pipeline_runs += 1

                if move_uci is None:
                    llm_errors += 1
                    move = random.choice(legal)
                    move_uci = move.uci()

                board.push_uci(move_uci)

                # Update game plan after move
                if cfg.use_game_plan:
                    plan_input = _board_user_msg(board, game_moves, use_context=True)
                    if current_plan:
                        plan_input += f"\nPrevious plan: {current_plan}"
                    current_plan = await _call_llm(GAME_PLAN_PROMPT, plan_input, max_tokens=60)
            else:
                broadcast_game_state(
                    game_tag, board, game_moves, llm_plays_white,
                    cfg.opponent_elo, move_count=move_count,
                    gen=gen, config=cfg.label,
                    whose_turn="stockfish", active_node="",
                    eval_curve=eval_curve, full_config=cfg.full_label,
                )
                result = engine.play(board, chess.engine.Limit(time=0.1))
                move_uci = result.move.uci()
                board.push(result.move)

            game_moves.append(move_uci)
            move_count += 1

            # Get full-strength Stockfish eval of current position
            try:
                info = evaluator.analyse(board, chess.engine.Limit(time=0.05))
                score = info["score"].white()
                cp = score.score(mate_score=10000)
                # Flip sign if LLM is black so positive = good for LLM
                eval_cp = cp if llm_plays_white else -cp
            except Exception:
                eval_cp = 0
            eval_curve.append(eval_cp)

            broadcast_game_state(
                game_tag, board, game_moves, llm_plays_white,
                cfg.opponent_elo, result=None, move_count=move_count,
                gen=gen, config=cfg.label,
                eval_curve=eval_curve, full_config=cfg.full_label,
            )
            print(f"      {DIM}[{game_tag}] {format_moves(game_moves)} eval={eval_cp:+d}cp{RESET}")

            # Early resign only in hopeless positions (checkmate-level)
            if len(eval_curve) >= 2 and all(e < -500 for e in eval_curve[-2:]):
                print(f"      {DIM}[{game_tag}] Resigning (eval < -500cp for 2 moves){RESET}")
                break

        outcome = board.outcome()
        if outcome is not None and outcome.winner is not None:
            if (outcome.winner == chess.WHITE) == llm_plays_white:
                result_str = "win"
                llm_score = 1.0
            else:
                result_str = "loss"
                llm_score = 0.0
        elif outcome is not None and outcome.winner is None:
            # Real stalemate/draw
            result_str = "draw"
            llm_score = 0.5
        else:
            # Timeout or early resign -- score based on final eval
            final_eval = eval_curve[-1] if eval_curve else 0
            if final_eval > 100:
                result_str = "draw"  # was winning, ran out of time
                llm_score = 0.6
            elif final_eval < -100:
                result_str = "loss"  # was losing
                llm_score = 0.1
            else:
                result_str = "draw"  # roughly even
                llm_score = 0.4

        broadcast_game_state(
            game_tag, board, game_moves, llm_plays_white,
            cfg.opponent_elo, result=result_str, move_count=move_count,
            gen=gen, config=cfg.label,
            eval_curve=eval_curve, full_config=cfg.full_label,
        )
    finally:
        engine.quit()
        evaluator.quit()

    return {
        "result": result_str, "score": llm_score, "moves": move_count,
        "llm_errors": llm_errors, "pipeline_runs": pipeline_runs,
        "termination": outcome.termination.name if outcome else "max_moves",
        "move_list": game_moves,
        "eval_curve": eval_curve,
    }


async def evaluate_pipeline(
    pipeline: Package,
    cfg: PipelineConfig,
    n_games: int = GAMES_PER_EVAL,
    eval_tag: str = "",
    gen: int = 0,
) -> EvalResult:
    # Launch all games in parallel
    game_tasks = []
    for i in range(n_games):
        color = "W" if i % 2 == 0 else "B"
        game_tag = f"{eval_tag}:g{i+1}({color})"
        game_tasks.append(play_game(
            pipeline, cfg, llm_plays_white=(i % 2 == 0),
            game_tag=game_tag, gen=gen,
        ))

    games = await asyncio.gather(*game_tasks)

    result = EvalResult()
    for game in games:
        result.games.append(game)
        if game["result"] == "win":
            result.wins += 1
        elif game["result"] == "draw":
            result.draws += 1
        else:
            result.losses += 1
        result.total_moves += game["moves"]
        result.total_errors += game["llm_errors"]
        result.total_pipeline_runs += game["pipeline_runs"]

        tag = eval_tag or "?"
        print(f"    {WHITE}{tag}: {game['result'].upper()}{RESET} in {game['moves']} moves "
              f"vs SF {cfg.opponent_elo}  "
              f"({game['pipeline_runs']} pipelines, {game['llm_errors']} errors)")
        print(f"    {DIM}{format_moves(game['move_list'])}{RESET}")
    return result


# ── reflection-guided mutations ───────────────────────────────────


def chess_features(cfg: PipelineConfig) -> tuple[int, ...]:
    """Feature vector for MAP-Elites grid. Hash-based to handle dynamic knob values."""
    return (
        hash(cfg.pipeline_mode) % 3,
        hash(cfg.tactical_style) % 5,
        hash(cfg.verify_loop_target) % 2,
        hash(cfg.endgame_hint) % 6,
        int(cfg.use_verification),
    )


KNOB_SPACE: list[tuple[str, list]] = [
    # Topology
    ("pipeline_mode", ["parallel", "single_deep", "enumerate"]),
    ("use_verification", [True, False]),
    ("verify_iterations", [1, 2, 3, 4, 5]),
    ("game_phase_routing", [True, False]),
    ("skip_forced", [True, False]),
    ("use_opponent_model", [True, False]),
    # Prompts
    ("tactical_style", ["broad", "mate_focused", "material"]),
    ("positional_style", ["classical", "dynamic", "prophylactic"]),
    ("verify_style", ["strict", "standard", "lenient"]),
    ("critique_style", ["devils_advocate", "sanity_check"]),
    ("selector_tokens", [10, 100]),
    # Representation
    ("board_representation", ["fen", "ascii", "both"]),
    # Phase strategy
    ("opening_hint", ["theory", "principled"]),
    ("middlegame_hint", ["theory", "safety_first", "attacking", "positional", "theory+safety_first", "theory+positional"]),
    ("endgame_hint", ["theory", "technical", "aggressive", "defensive", "theory+technical", "theory+defensive"]),
]



def mutate_knobs(
    cfg: PipelineConfig, rng: random.Random,
) -> tuple[PipelineConfig, str]:
    """Mutate exactly 1 knob — matches factory's single-mutation-per-individual policy."""
    import dataclasses
    new = dataclasses.replace(cfg)
    knob_name, choices = rng.choice(KNOB_SPACE)
    alternatives = [v for v in choices if v != getattr(new, knob_name)]
    if not alternatives:
        return new, "no-op"
    new_val = rng.choice(alternatives)
    object.__setattr__(new, knob_name, new_val)
    return new, f"{knob_name}={new_val}"


# ── Opus-driven proposal parsing ─────────────────────────────────


def parse_opus_proposals(
    raw: str, best_cfg: PipelineConfig, gen: int,
) -> list[tuple[str, PipelineConfig, Package, int]]:
    """Parse Opus JSON proposals into (label, cfg, pipeline, n_games) tuples."""
    import dataclasses
    candidates: list[tuple[str, PipelineConfig, Package, int]] = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            prop = json.loads(line)
            n_games = prop.get("games", GAMES_PER_EVAL)
            if "knobs" in prop and isinstance(prop["knobs"], dict):
                overrides = {k: v for k, v in prop["knobs"].items() if hasattr(best_cfg, k)}
                for k, v in overrides.items():
                    if "prompt" in prop:
                        _register_prompt(k, v, prop["prompt"])
                    for _, (kn, choices) in enumerate(KNOB_SPACE):
                        if kn == k and v not in choices:
                            choices.append(v)
                child_cfg = dataclasses.replace(best_cfg, **overrides)
                desc = " + ".join(f"{k}={v}" for k, v in overrides.items())
            elif prop.get("rerun"):
                child_cfg = dataclasses.replace(best_cfg)
                desc = "rerun (variance check)"
            else:
                knob, value = prop["knob"], prop["value"]
                if not hasattr(best_cfg, knob):
                    continue
                if "prompt" in prop and isinstance(prop["prompt"], str):
                    _register_prompt(knob, value, prop["prompt"])
                    print(f"  {CYAN}NEW PROMPT: {knob}={value}{RESET}")
                for _, (kn, choices) in enumerate(KNOB_SPACE):
                    if kn == knob and value not in choices:
                        choices.append(value)
                child_cfg = dataclasses.replace(best_cfg, **{knob: value})
                desc = f"{knob}={value}"
            candidates.append((
                f"Gen {gen}.{len(candidates)+1}: {desc}",
                child_cfg, build_pipeline(child_cfg), n_games,
            ))
        except Exception:
            continue
    return candidates


# ── main ──────────────────────────────────────────────────────────


def _load_invented_prompts() -> int:
    """Reload invented prompts from the recording on startup."""
    recording = LIVE_DIR / "recording.jsonl"
    if not recording.exists():
        return 0
    count = 0
    for line in recording.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            if e.get("type") == "prompt_invented":
                PROMPT_REGISTRY.setdefault(e["knob"], {})[e["value"]] = e["prompt"]
                for _, (kn, choices) in enumerate(KNOB_SPACE):
                    if kn == e["knob"] and e["value"] not in choices:
                        choices.append(e["value"])
                count += 1
        except Exception:
            continue
    return count


async def main():
    from factory.outer_loop.population import MAPElitesArchive, Population

    seed_cfg = PipelineConfig()
    seed = build_pipeline(seed_cfg)

    # Reload invented prompts from previous runs
    n_loaded = _load_invented_prompts()

    header("CHESS PIPELINE EVOLUTION — adaptive loop")
    print(f"\n  {WHITE}Model:{RESET}       haiku")
    print(f"  {WHITE}Knobs:{RESET}       {len(KNOB_SPACE)}")
    if n_loaded:
        print(f"  {CYAN}Loaded:{RESET}      {n_loaded} invented prompts from recording")
    print(f"  {WHITE}Seed:{RESET}  {seed_cfg.label}")
    print(f"  {DIM}Compiled: {len(seed.compile().nodes)} nodes{RESET}")

    if LIVE_DIR.exists():
        (LIVE_DIR / "_reload.json").write_text(json.dumps({"_reload": True}))
        time.sleep(1)
        shutil.rmtree(LIVE_DIR)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    # ── Gen 0: seed ───────────────────────────────────────────────

    header(f"GEN 0 -- seed ({seed_cfg.label})")
    seed_result = await evaluate_pipeline(seed, seed_cfg, eval_tag="seed", gen=0)
    print(f"  {WHITE}Gen 0:{RESET} {seed_result.win_rate} score={seed_result.composite_score:+.0f}")

    # Factory components
    archive = MAPElitesArchive()
    seed_ind = Population.make_individual(seed.compile(), generation=0, score=seed_result.composite_score)
    seed_ind.features = chess_features(seed_cfg)
    archive.add(seed_ind)
    ind_configs: dict[str, PipelineConfig] = {seed_ind.id: seed_cfg}
    broadcast_eval_result(f"Gen 0: {seed_cfg.label}", seed_cfg, seed_result, gen=0, is_best=True)

    # Factory components
    from factory.outer_loop.reflector import OuterLoopReflector
    reflector = OuterLoopReflector(k=3)
    score_trajectory: list[float] = [seed_result.composite_score]
    cycle_records: dict[str, CycleRecord] = {seed_ind.id: seed_result.to_cycle_record(0)}
    PLATEAU_WINDOW, PLATEAU_THRESHOLD = 10, 5.0
    all_results: list[tuple[str, PipelineConfig, EvalResult, float]] = []

    # ── Evolutionary loop ─────────────────────────────────────────

    for gen in range(1, 1001):
        best_ind = archive.best()
        best_score = best_ind.score if best_ind else 0
        header(f"GEN {gen} -- archive: {archive.size} cells, best={best_score:+.0f}")

        # Factory's plateau detection
        stalled = (len(score_trajectory) > PLATEAU_WINDOW and
                   all(abs(s - score_trajectory[-(PLATEAU_WINDOW+1)]) < PLATEAU_THRESHOLD
                       for s in score_trajectory[-PLATEAU_WINDOW:]))

        # Step 1: Factory's OuterLoopReflector (contrastive analysis)
        all_sorted = sorted(all_results, key=lambda x: x[3], reverse=True)
        history = "\n".join(
            f"  {cfg.label}: {r.win_rate} avg={r.avg_eval:+.0f}cp blun={r.blunder_count} mv={r.total_moves} score={s:+.0f}"
            for _, cfg, r, s in all_sorted[:20]
        ) if all_results else "(seed run)"

        reflection_report = None
        reflection = ""
        if cycle_records:
            records = [(iid, cycle_records[iid].score_end or 0, cycle_records[iid])
                       for iid in list(cycle_records.keys())[-20:]]
            try:
                reflection_report = reflector.reflect(records, gen)
                parts = []
                if reflection_report.failure_patterns:
                    parts.append(f"Failures: {'; '.join(reflection_report.failure_patterns[:3])}")
                if reflection_report.success_patterns:
                    parts.append(f"Successes: {'; '.join(reflection_report.success_patterns[:3])}")
                if reflection_report.mutation_suggestions:
                    parts.append(f"Suggestions: {'; '.join(reflection_report.mutation_suggestions[:3])}")
                reflection = " | ".join(parts)
                if reflection:
                    print(f"\n  {MAGENTA}Reflection:{RESET} {reflection}")
            except Exception as exc:
                print(f"  {DIM}(reflection error: {exc}){RESET}")

        # Fall back to Opus reflection when factory reflector has nothing
        if not reflection and all_results:
            try:
                reflection = (await _cli_call_opus(
                    "You are a concise chess optimization analyst. 2-3 sentences.",
                    f"All results (top 20):\n{history}\n\nArchive: {archive.size} cells, best={best_score:+.0f}\n"
                    f"What patterns separate winners from losers? Where is the bottleneck?",
                )).strip()
                if reflection:
                    print(f"\n  {MAGENTA}Reflection:{RESET} {reflection}")
            except Exception:
                pass

        # Step 2: Factory's apply_random_mutation with Opus as knob expander
        from factory.outer_loop.mutations import apply_random_mutation, WeightedRandomStrategy
        strategy = WeightedRandomStrategy()
        if stalled and hasattr(strategy, "on_plateau"):
            strategy.on_plateau()

        candidates: list[tuple[str, PipelineConfig, Package, int]] = []
        for c in range(CANDIDATES_PER_GEN):
            parent = archive.sample_parent(tournament_size=3)
            if parent is None:
                continue
            parent_cfg = ind_configs.get(parent.id, seed_cfg)
            parent_wf = build_pipeline(parent_cfg).compile()

            result = apply_random_mutation(
                parent_wf, strategy, gen,
                reflection_report=reflection_report,
            )
            if result is None:
                continue
            child_wf, rec = result
            # Reconstruct PipelineConfig from mutated knob_values
            import dataclasses
            def _coerce_back(k, v):
                """Coerce OptKnob string values back to PipelineConfig types."""
                orig = getattr(parent_cfg, k, None)
                if isinstance(orig, bool):
                    return v == "True" if isinstance(v, str) else bool(v)
                if isinstance(orig, int):
                    return int(v) if not isinstance(v, int) else v
                return v
            overrides = {k: _coerce_back(k, v) for k, v in child_wf.knob_values.items()
                         if hasattr(parent_cfg, k) and getattr(parent_cfg, k) != _coerce_back(k, v)}
            if overrides:
                child_cfg = dataclasses.replace(parent_cfg, **overrides)
                desc = " + ".join(f"{k}={v}" for k, v in overrides.items())
            else:
                child_cfg = parent_cfg
                desc = rec.rationale or "no-op"
            child_pipeline = build_pipeline(child_cfg)
            label = f"Gen {gen}.{c+1}: {desc}"
            candidates.append((label, child_cfg, child_pipeline, GAMES_PER_EVAL))
            print(f"  {YELLOW}▶ {label}{RESET}")

        # Step 3: Evaluate in parallel
        print(f"\n  {DIM}Evaluating {len(candidates)} in parallel...{RESET}\n")

        async def eval_one(label, cfg, pipeline, gen_num, n_games=1):
            t0 = time.monotonic()
            result = await evaluate_pipeline(pipeline, cfg, n_games=n_games, eval_tag=label, gen=gen_num)
            return label, cfg, pipeline, result, time.monotonic() - t0

        gen_start = time.monotonic()
        eval_results = await asyncio.gather(
            *(eval_one(lbl, c, p, gen, ng) for lbl, c, p, ng in candidates)
        )

        # Step 4: Update archive (factory's MAPElitesArchive.add)
        gen_candidates = []
        prev_best = best_score
        for label, cfg, pipeline, result, elapsed in eval_results:
            score = result.composite_score
            ind = Population.make_individual(pipeline.compile(), generation=gen, score=score)
            ind.features = chess_features(cfg)
            inserted = archive.add(ind)
            ind_configs[ind.id] = cfg
            cycle_records[ind.id] = result.to_cycle_record(gen)
            gen_candidates.append((label, cfg, result, score))
            marker = f" {GREEN}→ archive{RESET}" if inserted else ""
            print(f"  {WHITE}{label}:{RESET} {result.win_rate} score={score:+.0f} "
                  f"(avg={result.avg_eval:+.0f}cp blun={result.blunder_count} mv={result.total_moves}){marker}")

        new_score = archive.best().score if archive.best() else 0
        score_trajectory.append(new_score)
        if new_score > prev_best:
            print(f"\n  {GREEN}NEW BEST: score={new_score:+.0f} (archive: {archive.size} cells){RESET}")

        # Broadcast to UI
        for label, cfg, result, score in gen_candidates:
            broadcast_eval_result(label, cfg, result, gen=gen, is_best=(score == new_score and new_score > prev_best))
        if reflection:
            entry = {"type": "reflection", "gen": gen, "text": reflection}
            with open(LIVE_DIR / "experiment_log.jsonl", "a") as f:
                f.write(json.dumps(entry) + "\n")
            with open(LIVE_DIR / "recording.jsonl", "a") as f:
                f.write(json.dumps({"t": time.monotonic(), **entry}) + "\n")

        all_results.extend(gen_candidates)
        print(f"  {DIM}Gen {gen} in {time.monotonic() - gen_start:.0f}s{RESET}")

    header("RESULTS")
    best = archive.best()
    print(f"\n  {GREEN}{BOLD}Winner:{RESET} score={best.score:+.0f}")
    print(f"  {GREEN}Archive:{RESET} {archive.size} cells")
    print(f"  {GREEN}Config:{RESET} {ind_configs[best.id].full_label}")


if __name__ == "__main__":
    asyncio.run(main())
