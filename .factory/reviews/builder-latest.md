## Builder Report — disk_reads re-scan after setup()

### Changes

**factory/workflow/executor.py** (`_execute_data` → `run_item`):
- Added a per-item re-scan of subgraph reads after `resolved_task.setup()` completes
- New local variable `setup_reads: set[str]` scans `sub_workflow.nodes` reads against `item_project_path`
- Changed `item_executor.completed_files = self.completed_files | disk_reads` to `self.completed_files | disk_reads | setup_reads`
- The pre-scan at line 791 (`disk_reads`) is preserved — it handles pre-existing files
- `setup_reads` is local to `run_item()` — no mutation of shared `disk_reads` set

**tests/test_data_node.py**:
- Added `_SetupWritingTask` — a minimal Task whose `setup()` writes a file to the workspace
- Added `TestDiskReadsRescanAfterSetup::test_setup_created_file_in_completed_files` — verifies that when `setup()` creates a file declared in a subgraph node's `reads`, it appears in the sub-executor's `completed_files`

### Verification
- All 64 DataNode tests pass (including new test)
- All 46 executor tests pass
- `ruff check` clean
- `mypy` clean
