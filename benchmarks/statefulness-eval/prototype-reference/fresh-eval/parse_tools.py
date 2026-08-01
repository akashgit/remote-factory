import json, sys
from collections import Counter

def parse_stream(filepath):
    tool_calls = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: obj = json.loads(line)
            except json.JSONDecodeError: continue
            if obj.get("type") != "assistant": continue
            msg = obj.get("message", {})
            for block in msg.get("content", []):
                if block.get("type") == "tool_use":
                    tool_name = block.get("name", "unknown")
                    tool_input = block.get("input", {})
                    target = ""
                    if tool_name == "Read":
                        target = tool_input.get("file_path", "")
                    elif tool_name == "Bash":
                        target = tool_input.get("command", "")[:200]
                    elif tool_name in ("Write", "Edit"):
                        target = tool_input.get("file_path", "")
                    elif tool_name in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
                        target = json.dumps(tool_input)[:100]
                    else:
                        target = json.dumps(tool_input)[:100] if tool_input else ""
                    is_factory_read = ".factory/" in target and tool_name in ("Read", "Bash")
                    tool_calls.append({"tool": tool_name, "target": target, "is_factory_read": is_factory_read})
    return tool_calls

if __name__ == "__main__":
    calls = parse_stream(sys.argv[1])
    tool_counts = Counter(c["tool"] for c in calls)
    factory_reads = [c for c in calls if c["is_factory_read"]]
    print(f"TOTAL_TOOL_CALLS={len(calls)}")
    print(f"FACTORY_READS={len(factory_reads)}")
    print(f"TOOL_BREAKDOWN={dict(tool_counts)}")
    print(f"FACTORY_TARGETS={[c['target'][:80] for c in factory_reads]}")
    print("\n--- All tool calls (ordered) ---")
    for i, c in enumerate(calls, 1):
        marker = " [.factory/]" if c["is_factory_read"] else ""
        print(f"  {i:3d}. {c['tool']:15s} {c['target'][:120]}{marker}")
