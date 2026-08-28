"""Live chess visualization server.

Watches /tmp/chess-factory/live/*.json for game state updates
and streams them to a browser via SSE.

Run: uv run python examples/chess_web.py
Then open: http://localhost:8421
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

app = FastAPI()
LIVE_DIR = Path("/tmp/chess-factory/live")
HTML_PATH = Path(__file__).parent / "chess_ui.html"


# Serve HTML from file -- edits take effect on next browser refresh, no server restart needed
HTML_PLACEHOLDER = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Chess Pipeline Evolution — Live</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0d1117; color: #c9d1d9; font-family: 'SF Mono', monospace;
    padding: 20px;
  }
  h1 { color: #58a6ff; font-size: 18px; margin-bottom: 6px; }
  .subtitle { color: #8b949e; font-size: 13px; margin-bottom: 20px; line-height: 1.6; }
  .subtitle code {
    background: #21262d; color: #58a6ff; padding: 1px 5px;
    border-radius: 3px; font-size: 12px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 16px;
  }
  .game-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 12px; min-height: 420px;
    display: flex; flex-direction: column;
  }
  .game-card.win { border-color: #3fb950; }
  .game-card.loss { border-color: #f85149; }
  .game-card.draw { border-color: #d29922; }
  .game-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 8px; font-size: 12px;
  }
  .game-tag { color: #58a6ff; font-weight: bold; cursor: help; position: relative; }
  .game-tag .tooltip {
    display: none; position: absolute; left: 0; top: 100%;
    background: #1c2128; border: 1px solid #30363d; border-radius: 4px;
    padding: 6px 8px; font-size: 10px; color: #c9d1d9; white-space: nowrap;
    z-index: 10; font-weight: normal;
  }
  .game-tag:hover .tooltip { display: block; }
  .game-result { font-weight: bold; font-size: 14px; }
  .game-result.win { color: #3fb950; }
  .game-result.loss { color: #f85149; }
  .game-result.draw { color: #d29922; }
  .game-result.playing { color: #8b949e; }
  .player-label {
    text-align: center; font-size: 11px; color: #8b949e; padding: 4px 0;
  }
  .player-label.llm { color: #58a6ff; }
  .board {
    display: grid; grid-template-columns: repeat(8, 1fr);
    width: 100%; aspect-ratio: 1; margin: 4px auto;
    border: 2px solid #5a5a5a; border-radius: 2px;
  }
  .sq {
    display: flex; align-items: center; justify-content: center;
    font-size: min(4vw, 32px); line-height: 1;
    aspect-ratio: 1;
  }
  .sq.light { background: #f0d9b5; }
  .sq.dark { background: #b58863; }
  .sq.last-move.light { background: #cdd26a; }
  .sq.last-move.dark { background: #aaa23a; }
  .sq .white-piece { color: #fff; text-shadow: 0 0 2px #000, 0 0 2px #000; }
  .sq .black-piece { color: #000; text-shadow: 0 0 1px rgba(255,255,255,0.3); }
  .moves {
    font-size: 10px; color: #8b949e; margin-top: 4px;
    word-break: break-all; overflow-y: auto;
    line-height: 1.5; flex: 1; min-height: 0;
  }
  .gen-header {
    grid-column: 1 / -1; color: #f0883e; font-size: 14px;
    font-weight: bold; padding: 10px 0 4px;
    border-top: 1px solid #30363d; margin-top: 8px;
    cursor: pointer; user-select: none;
  }
  .gen-header:first-child { border-top: none; margin-top: 0; }
  .gen-header .caret {
    display: inline-block; transition: transform 0.2s; margin-right: 6px;
  }
  .gen-header .caret.collapsed { transform: rotate(-90deg); }
  .gen-group {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 16px;
  }
  .gen-group.collapsed { display: none; }
  .reflection {
    grid-column: 1 / -1; background: #161b22; border: 1px solid #30363d;
    border-radius: 8px; padding: 12px; font-size: 11px; color: #8b949e;
  }
  .reflection h3 { color: #d29922; font-size: 12px; margin-bottom: 6px; }
  .knob-row { display: flex; gap: 12px; flex-wrap: wrap; }
  .knob-val { color: #c9d1d9; }
  .knob-val.best { color: #3fb950; font-weight: bold; }
  .turn-indicator {
    text-align: center; font-size: 11px; padding: 4px 0;
    margin: 4px 0;
  }
  .turn-indicator.llm { color: #58a6ff; }
  .turn-indicator.stockfish { color: #f0883e; }
  .turn-indicator.done { color: #8b949e; }
  .pipeline {
    display: flex; align-items: center; justify-content: center;
    gap: 4px; margin: 6px 0; font-size: 10px;
  }
  .pipe-node {
    padding: 2px 6px; border-radius: 3px;
    background: #21262d; color: #8b949e; border: 1px solid #30363d;
  }
  .pipe-node.active {
    background: #1f3a5f; color: #58a6ff; border-color: #58a6ff;
    animation: pulse 1s ease-in-out infinite alternate;
  }
  .pipe-arrow { color: #484f58; }
  .pipe-parallel { display: flex; flex-direction: column; gap: 2px; }
  @keyframes pulse { from { opacity: 0.7; } to { opacity: 1; } }
  #log-panel {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 12px; margin-bottom: 16px;
  }
  #log-panel h2 { color: #f0883e; font-size: 14px; margin-bottom: 8px; }
  .log-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .log-table th {
    text-align: left; color: #8b949e; padding: 4px 8px;
    border-bottom: 1px solid #30363d;
  }
  .log-table td { padding: 4px 8px; border-bottom: 1px solid #21262d; }
  .log-table tr.best td { color: #3fb950; }
  .log-table tr:hover td { background: #1c2128; }
  .log-score { font-weight: bold; }
  .log-bar {
    display: inline-block; height: 10px; background: #3fb950;
    border-radius: 2px; vertical-align: middle;
  }
  .log-config { color: #8b949e; font-size: 10px; }
  .node-outputs {
    margin-top: 6px; font-size: 10px;
    max-height: 150px; overflow-y: auto; flex-shrink: 0;
  }
  .node-output {
    background: #0d1117; border: 1px solid #21262d; border-radius: 4px;
    padding: 4px 6px; margin: 2px 0; max-height: 60px; overflow-y: auto;
  }
  .node-output-label {
    color: #58a6ff; font-weight: bold; font-size: 9px;
    text-transform: uppercase; margin-bottom: 2px;
  }
  .node-output-text {
    color: #8b949e; white-space: pre-wrap; word-break: break-word;
    font-size: 10px; line-height: 1.4;
  }
</style>
</head>
<body>
<h1>Chess Pipeline Evolution</h1>
<div class="subtitle">
  Can factory's evolutionary outer loop improve an LLM's chess play?
  Each move runs a <code>Package</code> pipeline through the real <code>WorkflowExecutor</code> — the same DAG engine that runs factory's
  code improvement workflows. The optimizer searches over graph topology, token budgets, and prompt strategies,
  playing real games against Stockfish to score each variant.
  Phase 1 finds the best structure. Phase 2 marks it <code>frozen</code> and refines prompts via <code>OptKnob</code> mutations.
</div>

<div id="explainer" style="display:flex; gap:16px; margin-bottom:16px; flex-wrap:wrap;">
  <div style="flex:1; min-width:300px; background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px;">
    <h3 style="color:#58a6ff; font-size:13px; margin-bottom:8px;">Package Composition</h3>
    <pre style="color:#c9d1d9; font-size:11px; line-height:1.6; margin:0;">
<span style="color:#8b949e"># Each chess move runs this pipeline.</span>
<span style="color:#8b949e"># Sequential chains the phases in order.</span>
pipeline = <span style="color:#d29922">Sequential</span>(

    <span style="color:#8b949e"># Parallel forks into two analysts that</span>
    <span style="color:#8b949e"># evaluate the same position independently</span>
    <span style="color:#8b949e"># (tactics vs positional), then joins their</span>
    <span style="color:#8b949e"># findings for the selector.</span>
    <span style="color:#d29922">Parallel</span>(
        tactical_pkg,
        positional_pkg,
    ),

    <span style="color:#8b949e"># Loop picks a move, then checks it for</span>
    <span style="color:#8b949e"># blunders. If the check finds a mistake,</span>
    <span style="color:#8b949e"># the gate RELOOPs — selector picks again</span>
    <span style="color:#8b949e"># with the blunder flagged. Exits when</span>
    <span style="color:#8b949e"># the move passes or max_iterations hit.</span>
    <span style="color:#d29922">Loop</span>(
        body=<span style="color:#d29922">Sequential</span>(
            selector_pkg,
            blunder_check_pkg,
        ),
        gate=blunder_gate,
        max_iterations=3,
    ),
)

wf = pipeline.<span style="color:#3fb950">compile</span>()  <span style="color:#8b949e"># → flat DAG</span>
await <span style="color:#3fb950">WorkflowExecutor</span>(wf, proj).execute()

<span style="color:#8b949e"># Phase 1: optimize topology + structure</span>
best = evolve(pipeline, knobs, gens=6)

<span style="color:#8b949e"># Phase 2: freeze structure, refine prompts</span>
<span style="color:#8b949e"># The frozen list prevents the optimizer from</span>
<span style="color:#8b949e"># changing what Phase 1 already optimized.</span>
best = evolve(best, knobs,
    gens=3,
    frozen=[<span style="color:#a5d6ff">"pipeline_mode"</span>,    <span style="color:#8b949e"># locked</span>
            <span style="color:#a5d6ff">"selector_tokens"</span>,  <span style="color:#8b949e"># locked</span>
            <span style="color:#a5d6ff">"opponent_elo"</span>],    <span style="color:#8b949e"># locked</span>
    <span style="color:#8b949e"># only selector_style, tactical_style,</span>
    <span style="color:#8b949e"># positional_style can mutate now</span>
)</pre>
  </div>

  <div style="flex:1; min-width:300px; background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px;">
    <h3 style="color:#58a6ff; font-size:13px; margin-bottom:8px;">Pipeline Graph (per move)</h3>
    <pre style="color:#c9d1d9; font-size:11px; line-height:1.6; margin:0;">
 board_state.md
       │
       ▼
 ┌───────────┐
 │ <span style="color:#58a6ff">analyst</span>   │ AgentNode
 └─────┬─────┘
       │
 ┌─────┴─────┐
 │ <span style="color:#f0883e">ForkNode</span>  │
 └──┬─────┬──┘
    │     │
    ▼     ▼
 <span style="color:#58a6ff">tact</span>   <span style="color:#58a6ff">pos</span>    parallel
    │     │
 ┌──┴─────┴──┐
 │ <span style="color:#f0883e">JoinNode</span>  │
 └─────┬─────┘
       │
       ▼
 ┌───────────┐
 │ <span style="color:#3fb950">selector</span>  │◄─╮ picks move
 └─────┬─────┘  │
       │        │
       ▼        │
 ┌───────────┐  │
 │ <span style="color:#d29922">blunder</span>   │──╯ RELOOP
 │ <span style="color:#d29922">check</span>     │    if blunder
 └─────┬─────┘
       │ PROCEED
       ▼
   move.md</pre>
  </div>

  <div style="flex:1; min-width:300px; background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px;">
    <h3 style="color:#58a6ff; font-size:13px; margin-bottom:8px;">Optimization Surface</h3>
    <pre style="color:#c9d1d9; font-size:11px; line-height:1.5; margin:0;">
<span style="color:#f0883e"># ── Phase 1: structure (6 gens) ──</span>

<span style="color:#8b949e"># How the analysts are organized:</span>
<span style="color:#8b949e"># 3 parallel, 1 deep, or list+pick</span>
<span style="color:#d29922">OptKnob</span>(<span style="color:#a5d6ff">"pipeline_mode"</span>, <span style="color:#a5d6ff">"topology"</span>,
  bounds=[<span style="color:#a5d6ff">"parallel"</span>, <span style="color:#a5d6ff">"single_deep"</span>,
          <span style="color:#a5d6ff">"enumerate"</span>])

<span style="color:#8b949e"># How much the selector can reason</span>
<span style="color:#8b949e"># before choosing a move</span>
<span style="color:#d29922">OptKnob</span>(<span style="color:#a5d6ff">"selector_tokens"</span>, <span style="color:#a5d6ff">"threshold"</span>,
  bounds=[10, 100, 500])

<span style="color:#8b949e"># Should analysts see the full game</span>
<span style="color:#8b949e"># history or just the current board?</span>
<span style="color:#d29922">OptKnob</span>(<span style="color:#a5d6ff">"use_game_context"</span>, <span style="color:#a5d6ff">"topology"</span>,
  bounds=[True, False])

<span style="color:#8b949e"># Stockfish difficulty — reflector</span>
<span style="color:#8b949e"># learns when to push harder</span>
<span style="color:#d29922">OptKnob</span>(<span style="color:#a5d6ff">"opponent_elo"</span>, <span style="color:#a5d6ff">"threshold"</span>,
  bounds=[1320, 1420, 1520, 1620])

<span style="color:#f0883e"># ── Phase 2: prompts (3 gens) ───</span>
<span style="color:#8b949e"># Phase 1 knobs frozen</span>

<span style="color:#8b949e"># How the selector weighs tactics</span>
<span style="color:#8b949e"># vs safety vs calculation depth</span>
<span style="color:#d29922">OptKnob</span>(<span style="color:#a5d6ff">"selector_style"</span>, <span style="color:#a5d6ff">"prompt"</span>,
  bounds=[<span style="color:#a5d6ff">"balanced"</span>, <span style="color:#a5d6ff">"aggressive"</span>,
          <span style="color:#a5d6ff">"defensive"</span>, <span style="color:#a5d6ff">"calculating"</span>])

<span style="color:#8b949e"># What the tactician looks for:</span>
<span style="color:#8b949e"># broad scan, checkmates, or trades</span>
<span style="color:#d29922">OptKnob</span>(<span style="color:#a5d6ff">"tactical_style"</span>, <span style="color:#a5d6ff">"prompt"</span>,
  bounds=[<span style="color:#a5d6ff">"broad"</span>, <span style="color:#a5d6ff">"mate_focused"</span>,
          <span style="color:#a5d6ff">"material"</span>])

<span style="color:#8b949e"># How the positionalist evaluates:</span>
<span style="color:#8b949e"># structure, activity, or prevention</span>
<span style="color:#d29922">OptKnob</span>(<span style="color:#a5d6ff">"positional_style"</span>, <span style="color:#a5d6ff">"prompt"</span>,
  bounds=[<span style="color:#a5d6ff">"classical"</span>, <span style="color:#a5d6ff">"dynamic"</span>,
          <span style="color:#a5d6ff">"prophylactic"</span>])</pre>
  </div>
</div>

<div id="log-panel"></div>
<div class="grid" id="grid"></div>

<script>
const PIECE_MAP = {
  'R': '♖', 'N': '♘', 'B': '♗', 'Q': '♕', 'K': '♔', 'P': '♙',
  'r': '♜', 'n': '♞', 'b': '♝', 'q': '♛', 'k': '♚', 'p': '♟',
};

function parseFEN(fen) {
  const rows = fen.split(' ')[0].split('/');
  const board = [];
  for (const row of rows) {
    const rank = [];
    for (const ch of row) {
      if (ch >= '1' && ch <= '8') {
        for (let i = 0; i < parseInt(ch); i++) rank.push('');
      } else {
        rank.push(ch);
      }
    }
    board.push(rank);
  }
  return board;
}

function renderBoard(fen, lastMove, llmWhite) {
  const board = parseFEN(fen);
  const flipped = !llmWhite;
  let lastFrom = -1, lastTo = -1;
  if (lastMove && lastMove.length >= 4) {
    const fc = lastMove.charCodeAt(0) - 97, fr = 8 - parseInt(lastMove[1]);
    const tc = lastMove.charCodeAt(2) - 97, tr = 8 - parseInt(lastMove[3]);
    lastFrom = fr * 8 + fc;
    lastTo = tr * 8 + tc;
  }
  let html = '';
  for (let ri = 0; ri < 8; ri++) {
    for (let ci = 0; ci < 8; ci++) {
      const r = flipped ? 7 - ri : ri;
      const c = flipped ? 7 - ci : ci;
      const idx = r * 8 + c;
      const light = (r + c) % 2 === 0;
      const highlight = (idx === lastFrom || idx === lastTo) ? ' last-move' : '';
      const piece = board[r][c];
      const sym = PIECE_MAP[piece] || '';
      const pieceClass = piece ? (piece === piece.toUpperCase() ? 'white-piece' : 'black-piece') : '';
      const content = sym ? `<span class="${pieceClass}">${sym}</span>` : '';
      html += `<div class="sq ${light ? 'light' : 'dark'}${highlight}">${content}</div>`;
    }
  }
  return html;
}

function formatMoves(moves) {
  const pairs = [];
  for (let i = 0; i < moves.length; i += 2) {
    const num = Math.floor(i / 2) + 1;
    const w = moves[i];
    const b = moves[i + 1] || '';
    pairs.push(`${num}.${w} ${b}`.trim());
  }
  return pairs.join(' ');
}

function renderNodeOutputs(outputs) {
  const labels = {
    'board_analyst': 'Analyst',
    'tactician': 'Tactician',
    'positionalist': 'Positionalist',
    'selector': 'Selector',
  };
  const keys = Object.keys(outputs);
  if (keys.length === 0) return '';
  let html = '<div class="node-outputs">';
  for (const [nodeId, text] of Object.entries(outputs)) {
    const label = labels[nodeId] || nodeId;
    const escaped = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    html += `<div class="node-output">
      <div class="node-output-label">${label}</div>
      <div class="node-output-text">${escaped}</div>
    </div>`;
  }
  html += '</div>';
  return html;
}

const games = {};
const grid = document.getElementById('grid');

// Track which gens user has manually toggled
const collapsedGens = {};

function updateUI() {
  const sorted = Object.values(games).sort((a, b) => {
    if (a.gen !== b.gen) return a.gen - b.gen;
    return a.tag.localeCompare(b.tag);
  });

  // Find the highest gen with any active (non-finished) games
  const maxGen = Math.max(...sorted.map(g => g.gen), 0);
  const activeGens = new Set();
  for (const g of sorted) {
    if (!g.result) activeGens.add(g.gen);
  }

  let html = '';
  let lastGen = -1;
  for (const g of sorted) {
    if (g.gen !== lastGen) {
      // Close previous gen group
      if (lastGen >= 0) html += '</div>';
      lastGen = g.gen;

      // Auto-collapse finished gens, unless user toggled
      const isActive = activeGens.has(g.gen);
      const isCollapsed = (g.gen in collapsedGens) ? collapsedGens[g.gen] : !isActive;
      const caretClass = isCollapsed ? 'collapsed' : '';
      const groupClass = isCollapsed ? 'collapsed' : '';

      let genLabel;
      if (g.gen === 0) genLabel = 'Phase 1 — Gen 0 (Seed)';
      else if (g.gen < 100) genLabel = `Phase 1 — Gen ${g.gen}`;
      else if (g.gen === 100) genLabel = 'Phase 2 — Gen 0 (Seed, structure frozen)';
      else genLabel = `Phase 2 — Gen ${g.gen - 100} (structure frozen)`;
      html += `<div class="gen-header" onclick="toggleGen(${g.gen})">
        <span class="caret ${caretClass}">▼</span>${genLabel}
      </div>`;
      html += `<div class="gen-group ${groupClass}" data-gen="${g.gen}">`;
    }

    const resultClass = g.result || 'playing';
    const resultText = g.result ? g.result.toUpperCase() : `move ${g.move_count}...`;
    const lastMove = g.moves.length > 0 ? g.moves[g.moves.length - 1] : '';
    const llmColor = g.llm_white ? 'White' : 'Black';
    const sfColor = g.llm_white ? 'Black' : 'White';
    const topLabel = `Stockfish ${g.elo} (${sfColor})`;
    const botLabel = `Haiku LLM (${llmColor})`;
    const topClass = '';
    const botClass = 'llm';

    // Turn indicator + pipeline
    let turnHtml = '';
    if (g.result) {
      turnHtml = `<div class="turn-indicator done">Game over</div>`;
    } else if (g.whose_turn === 'llm') {
      turnHtml = `<div class="turn-indicator llm">LLM thinking...</div>`;
    } else if (g.whose_turn === 'stockfish') {
      turnHtml = `<div class="turn-indicator stockfish">Stockfish moving...</div>`;
    }

    // Pipeline visualization
    const nodes = ['board_analyst', 'tactician + positionalist', 'selector', 'blunder check'];
    const nodeLabels = {
      'board_analyst': 'analyst',
      'tactician + positionalist': 'tact ∥ pos',
      'selector': 'selector',
      'blunder check': 'blunder ↺',
    };
    const showPipeline = g.whose_turn === 'llm' && !g.result;
    const pipeHtml = showPipeline ? `
      <div class="pipeline">
        ${nodes.map(n => {
          const active = g.active_node === n ? 'active' : '';
          const label = nodeLabels[n] || n;
          return `<span class="pipe-node ${active}">${label}</span>`;
        }).join('<span class="pipe-arrow">→</span>')}
      </div>` : '';

    html += `
      <div class="game-card ${resultClass}">
        <div class="game-header">
          <span class="game-tag">${g.tag}<span class="tooltip">${g.config || g.tag}</span></span>
          <span class="game-result ${resultClass}">${resultText}</span>
        </div>
        <div class="player-label ${topClass}">${topLabel}</div>
        <div class="board">${renderBoard(g.fen, lastMove, g.llm_white)}</div>
        <div class="player-label ${botClass}">${botLabel}</div>
        ${turnHtml}
        ${pipeHtml}
        <div class="moves">${formatMoves(g.moves)}</div>
      </div>`;
  }
  // Close last gen group
  if (lastGen >= 0) html += '</div>';
  grid.innerHTML = html;
}

function toggleGen(gen) {
  collapsedGens[gen] = !(collapsedGens[gen] ?? false);
  updateUI();
}

// Load full state on page load, then stream updates via SSE
fetch('/state').then(r => r.json()).then(state => {
  for (const g of state.games) { games[g.tag] = g; }
  updateUI();
  for (const e of state.log) {
    const key = e.label + ':' + e.gen;
    if (!seenLogLabels.has(key)) {
      seenLogLabels.add(key);
      logEntries.push(e);
    }
  }
  updateLog();
});

const evtSource = new EventSource('/events');
evtSource.onmessage = (e) => {
  const data = JSON.parse(e.data);
  if (data._reload) { location.reload(); return; }
  games[data.tag] = data;
  updateUI();
};

// Experiment log
const logEntries = [];
const logPanel = document.getElementById('log-panel');

function updateLog() {
  if (logEntries.length === 0) {
    logPanel.innerHTML = '';
    return;
  }
  const maxScore = Math.max(...logEntries.map(e => e.score), 0.001);
  let html = '<h2>Experiment Log</h2><table class="log-table"><tr>';
  html += '<th>Gen</th><th>Candidate</th><th>Win%</th><th></th>';
  html += '<th>W/D/L</th><th>vs ELO</th><th>Est. ELO</th><th>Config</th></tr>';
  function rawScore(entry) {
    const t = entry.wins + entry.draws + entry.losses;
    return t > 0 ? (entry.wins + 0.5 * entry.draws) / t : 0;
  }
  function calcElo(entry) {
    const s = rawScore(entry);
    if (s > 0 && s < 1) return Math.round(entry.elo + 400 * Math.log10(s / (1 - s)));
    return s >= 1 ? entry.elo + 400 : entry.elo - 400;
  }
  const maxElo = Math.max(...logEntries.map(calcElo));
  for (const e of logEntries) {
    const rs = rawScore(e);
    const estElo = calcElo(e);
    const best = estElo === maxElo ? 'best' : '';
    const minElo = Math.min(...logEntries.map(calcElo));
    const eloRange = Math.max(maxElo - minElo, 1);
    const barW = Math.round(((estElo - minElo) / eloRange) * 120);
    html += `<tr class="${best}">`;
    const genDisplay = e.gen >= 100 ? `P2.${e.gen - 100}` : `P1.${e.gen}`;
    html += `<td>${genDisplay}</td>`;
    html += `<td>${e.label}</td>`;
    html += `<td class="log-score">${(rs * 100).toFixed(0)}%</td>`;
    html += `<td><span class="log-bar" style="width:${barW}px"></span></td>`;
    html += `<td>+${e.wins}=${e.draws}-${e.losses}</td>`;
    html += `<td>${e.elo}</td>`;
    html += `<td class="log-score">${estElo}</td>`;
    // Highlight mutated knobs in config
    const knobToConfig = {
      'pipeline_mode': 'mode',
      'selector_tokens': 'sel_tok',
      'opponent_elo': 'elo',
      'use_game_context': 'ctx',
      'include_analyst': 'no-analyst',
    };
    let configHtml = e.config;
    const mutMatch = e.label.match(/: (.+)$/);
    if (mutMatch && e.gen > 0) {
      const mutations = mutMatch[1].split(' + ').map(m => m.split('=')[0].trim());
      for (const knob of mutations) {
        const configKey = knobToConfig[knob] || knob;
        // Highlight the key=value pair in the config string
        const re = new RegExp(`(${configKey}[=][^ ]+|${configKey})`, 'g');
        configHtml = configHtml.replace(re, `<span style="color:#d29922;font-weight:bold">$1</span>`);
      }
    }
    html += `<td class="log-config">${configHtml}</td>`;
    html += '</tr>';
  }
  html += '</table>';
  logPanel.innerHTML = html;
}

const seenLogLabels = new Set();
const logSource = new EventSource('/log-events');
logSource.onmessage = (e) => {
  const entry = JSON.parse(e.data);
  const key = entry.label + ':' + entry.gen;
  if (!seenLogLabels.has(key)) {
    seenLogLabels.add(key);
    logEntries.push(entry);
    updateLog();
  }
};
logSource.onerror = () => {
  // On reconnect, clear dedup so replayed entries come through
  seenLogLabels.clear();
  logEntries.length = 0;
};
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PATH.read_text()


@app.get("/state")
async def full_state():
    """Return recent game state for initial load. Caps data to prevent browser freeze."""
    import json as _json
    game_states = []
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    # Only load game files from the most recent generation
    json_files = sorted(
        (f for f in LIVE_DIR.glob("*.json") if not f.name.startswith('_')),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    for f in json_files[:20]:
        try:
            game_states.append(_json.loads(f.read_text()))
        except Exception:
            pass
    log_entries = []
    log_path = LIVE_DIR / "experiment_log.jsonl"
    if log_path.exists():
        lines = log_path.read_text().strip().split("\n")
        # Load last 200 log entries (enough for ~40 gens)
        for line in lines:
            if line.strip():
                try:
                    log_entries.append(_json.loads(line))
                except Exception:
                    pass
    best_games = None
    best_path = LIVE_DIR / "_best_games.json"
    if best_path.exists():
        try:
            best_games = _json.loads(best_path.read_text())
        except Exception:
            pass
    return {"games": game_states, "log": log_entries, "best_games": best_games}


@app.get("/events")
async def events():
    async def stream():
        seen: dict[str, str] = {}
        last_html_mtime = HTML_PATH.stat().st_mtime if HTML_PATH.exists() else 0
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        while True:
            # Check if HTML file changed -- trigger browser reload
            if HTML_PATH.exists():
                mtime = HTML_PATH.stat().st_mtime
                if mtime > last_html_mtime:
                    last_html_mtime = mtime
                    yield {"data": '{"_reload": true}'}
            import time as _time
            now = _time.time()
            for f in sorted(LIVE_DIR.glob("*.json")):
                if f.name.startswith('_'):
                    continue
                try:
                    # Skip old files on first pass (only load recent)
                    if not seen and (now - f.stat().st_mtime) > 60:
                        continue
                    content = f.read_text()
                    if seen.get(f.name) != content:
                        seen[f.name] = content
                        yield {"data": content}
                except Exception:
                    pass
            await asyncio.sleep(0.3)

    return EventSourceResponse(stream())


@app.post("/summarize-reflection")
async def summarize_reflection(request: dict):
    """Summarize a verbose reflection into plain language via LLM."""
    text = request.get("text", "")
    if len(text) < 50 or "Failures:" not in text:
        return {"summary": text}
    try:
        import os
        from anthropic import AnthropicVertex
        import httpx
        client = AnthropicVertex(
            project_id=os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", ""),
            region="us-east5",
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        resp = client.messages.create(
            model="claude-haiku-4-5@20251001",
            max_tokens=100,
            system="Summarize this chess optimization reflection in 2 plain sentences. No jargon.",
            messages=[{"role": "user", "content": text}],
        )
        return {"summary": resp.content[0].text.strip()}
    except Exception:
        return {"summary": text[:200]}


@app.post("/compute-fens")
async def compute_fens(request: dict):
    """Compute per-move FENs from a UCI move list."""
    import chess
    moves = request.get("moves", [])
    board = chess.Board()
    fens = [board.fen()]
    for move_uci in moves:
        try:
            board.push_uci(move_uci)
        except Exception:
            try:
                board.push_san(move_uci)
            except Exception:
                break
        fens.append(board.fen())
    return {"fens": fens}


@app.get("/reload")
async def trigger_reload():
    """Write a reload marker so the SSE stream tells all clients to refresh."""
    import json as _json
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    (LIVE_DIR / "_reload.json").write_text(_json.dumps({"_reload": True}))
    return {"status": "reload triggered"}


@app.get("/log-events")
async def log_events():
    async def stream():
        log_path = LIVE_DIR / "experiment_log.jsonl"
        seen_lines = 0
        initialized = False
        while True:
            if log_path.exists():
                try:
                    lines = log_path.read_text().strip().split("\n")
                    if not initialized:
                        # Skip to near the end on first connect (last 200 lines)
                        seen_lines = len(lines)
                        initialized = True
                    for line in lines[seen_lines:]:
                        if line.strip():
                            yield {"data": line}
                    seen_lines = len(lines)
                except Exception:
                    pass
            await asyncio.sleep(0.5)

    return EventSourceResponse(stream())


@app.post("/replay")
async def replay(speed: float = 10.0, recording_path: str = ""):
    """Replay a recording by writing events to the live directory.

    The existing UI SSE streams pick up the data automatically.
    POST /replay?speed=20&recording_path=/path/to/recording.jsonl
    """
    import json as _json
    import shutil

    src = Path(recording_path) if recording_path else LIVE_DIR / "recording.jsonl"
    if not src.exists():
        return {"error": f"recording not found: {src}"}

    # Clear live dir and start fresh
    if LIVE_DIR.exists():
        shutil.rmtree(LIVE_DIR)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    lines = src.read_text().strip().split("\n")
    events = [_json.loads(line) for line in lines if line.strip()]
    if not events:
        return {"error": "empty recording"}

    # Pre-compute best games from recording so UI shows them immediately
    def _score(e):
        ec = e.get("eval_curve", [])
        avg = sum(ec) / len(ec) if ec else 0
        blun = sum(1 for i in range(1, len(ec)) if ec[i] - ec[i-1] < -200)
        return avg - 20 * blun + 8 * e.get("move_count", 0)

    finished = [e for e in events if e.get("type") == "game" and e.get("result")]
    if finished:
        best_score_game = max(finished, key=_score)
        longest_game = max(finished, key=lambda e: (len(e.get("moves", [])), _score(e)))
        # Attach reflections for those generations
        reflections = {e.get("gen"): e.get("text", "") for e in events
                       if e.get("type") == "reflection"}
        # Pre-compute per-move FENs for board replay
        try:
            import chess
            for game in [best_score_game, longest_game]:
                board = chess.Board()
                fens = [board.fen()]
                for move_uci in game.get("moves", []):
                    try:
                        board.push_uci(move_uci)
                    except Exception:
                        try:
                            board.push_san(move_uci)
                        except Exception:
                            break
                    fens.append(board.fen())
                game["fens"] = fens
        except ImportError:
            pass
        best_score_game["reflection"] = reflections.get(best_score_game.get("gen"), "")
        longest_game["reflection"] = reflections.get(longest_game.get("gen"), "")
        # Compute seed baseline
        seed_games = [e for e in finished if e.get("gen") == 0]
        baseline = _score(seed_games[0]) if seed_games else 0
        best_games = {
            "best_score": best_score_game,
            "longest": longest_game,
            "baseline": baseline,
        }
        (LIVE_DIR / "_best_games.json").write_text(_json.dumps(best_games))

    # Pre-write the full experiment log so the chart renders immediately
    log_events = [e for e in events if e.get("type") in ("eval", "reflection")]
    with open(LIVE_DIR / "experiment_log.jsonl", "w") as f:
        for e in log_events:
            entry = {k: v for k, v in e.items() if k != "t"}
            f.write(_json.dumps(entry) + "\n")

    async def _replay():
        t0 = events[0].get("t", 0)
        last_gen = -1
        for event in events:
            delay = (event.get("t", 0) - t0) / speed
            t0 = event.get("t", 0)
            # Minimum delay so parallel events render sequentially
            delay = max(delay, 0.15 / speed)
            # Pause between generations for readability
            gen = event.get("gen", last_gen)
            if gen != last_gen and last_gen >= 0:
                await asyncio.sleep(1.0)
                last_gen = gen
            elif last_gen < 0:
                last_gen = gen
            await asyncio.sleep(min(delay, 2.0))

            etype = event.get("type")
            if etype == "game":
                fname = event.get("file", "")
                if fname:
                    data = {k: v for k, v in event.items() if k not in ("t", "type", "file")}
                    (LIVE_DIR / f"{fname}.json").write_text(_json.dumps(data))
            elif etype in ("eval", "reflection"):
                entry = {k: v for k, v in event.items() if k != "t"}
                with open(LIVE_DIR / "experiment_log.jsonl", "a") as f:
                    f.write(_json.dumps(entry) + "\n")

    asyncio.create_task(_replay())
    return {"status": "replay started", "events": len(events), "speed": speed}


if __name__ == "__main__":
    import uvicorn
    print("Chess Evolution Live UI: http://localhost:8421")
    uvicorn.run(app, host="0.0.0.0", port=8421, log_level="warning")
