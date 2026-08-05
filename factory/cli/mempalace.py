"""CLI subcommand: factory mempalace {read,write,browse} <project_path>."""

from __future__ import annotations

import argparse
from pathlib import Path


def cmd_mempalace(args: argparse.Namespace) -> int:
    action = args.mempalace_action
    project_path = Path(args.project_path).resolve()

    if action == "read":
        return _do_read(project_path, task_hint=getattr(args, "task_hint", None))
    elif action == "write":
        return _do_write(project_path)
    elif action == "browse":
        return _do_browse(project_path, args)
    return 1


def _do_read(project_path: Path, task_hint: str | None = None) -> int:
    from factory.mempalace.reader import mp_read

    result = mp_read(project_path, task_hint=task_hint)
    if result:
        print(result)
    return 0


def _do_write(project_path: Path) -> int:
    from factory.mempalace.writer import mp_write

    result = mp_write(project_path)
    if result:
        print(result)
    return 0


def _do_browse(project_path: Path, args: argparse.Namespace) -> int:
    wing = getattr(args, "wing", None)
    room = getattr(args, "room", None)
    drawer_id = getattr(args, "drawer", None)

    try:
        from mempalace.palace import get_collection
    except ImportError:
        print("mempalace is not installed")
        return 1

    from factory.mempalace.helpers import get_palace_path, get_project_name

    palace = get_palace_path()
    try:
        collection = get_collection(palace, create=False)
    except Exception:
        print("No palace found at", palace)
        return 1

    if drawer_id:
        results = collection.get(ids=[drawer_id], include=["metadatas", "documents"])
        if not results["ids"]:
            print(f"Drawer not found: {drawer_id}")
            return 1
        meta = results["metadatas"][0]
        doc = results["documents"][0]
        print(f"Drawer: {drawer_id}")
        print(f"  Wing: {meta.get('wing', '?')}")
        print(f"  Room: {meta.get('room', '?')}")
        print(f"  Hall: {meta.get('hall', '?')}")
        print(f"  Filed: {meta.get('filed_at', '?')}")
        print(f"  Source: {meta.get('source_file', '?')}")
        print(f"  Agent: {meta.get('added_by', '?')}")
        print()
        print(doc)
        return 0

    all_results = collection.get(include=["metadatas", "documents"])
    if not all_results["ids"]:
        print("Palace is empty")
        return 0

    metas = all_results["metadatas"]
    docs = all_results["documents"]
    ids = all_results["ids"]

    if not wing:
        pn = get_project_name(project_path)
        default_wing = "project:" + pn

        wings: dict[str, dict[str, int]] = {}
        for m in metas:
            w = m.get("wing", "?")
            r = m.get("room", "?")
            if w not in wings:
                wings[w] = {}
            wings[w][r] = wings[w].get(r, 0) + 1

        for w in sorted(wings):
            marker = " ← this project" if w == default_wing else ""
            rooms_summary = ", ".join(f"{r} ({c})" for r, c in sorted(wings[w].items()))
            print(f"Wing: {w}{marker}")
            print(f"  Rooms: {rooms_summary}")
            print()
        return 0

    if not room:
        rooms: dict[str, list[tuple[str, dict, str]]] = {}
        for i, m in enumerate(metas):
            if m.get("wing") == wing:
                r = m.get("room", "?")
                if r not in rooms:
                    rooms[r] = []
                rooms[r].append((ids[i], m, docs[i]))

        if not rooms:
            print(f"No drawers found in wing: {wing}")
            return 0

        print(f"Wing: {wing}")
        for r in sorted(rooms):
            print(f"\n  Room: {r} ({len(rooms[r])} drawers)")
            for did, m, doc in rooms[r]:
                filed = m.get("filed_at", "?")[:10]
                hall = m.get("hall", "?")
                preview = doc[:80].replace("\n", " ").strip()
                print(f"    [{filed}] [{hall}] {did[:40]}...  \"{preview}...\"")
        return 0

    drawers: list[tuple[str, dict, str]] = []
    for i, m in enumerate(metas):
        if m.get("wing") == wing and m.get("room") == room:
            drawers.append((ids[i], m, docs[i]))

    if not drawers:
        print(f"No drawers in wing={wing} room={room}")
        return 0

    print(f"Wing: {wing}")
    print(f"Room: {room} ({len(drawers)} drawers)")
    for did, m, doc in drawers:
        filed = m.get("filed_at", "?")[:19]
        hall = m.get("hall", "?")
        source = m.get("source_file", "?")
        preview = doc[:120].replace("\n", " ").strip()
        print(f"\n  Drawer: {did}")
        print(f"    Filed: {filed}  Hall: {hall}")
        print(f"    Source: {source}")
        print(f"    Preview: \"{preview}...\"")
    return 0
