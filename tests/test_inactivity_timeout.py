"""Test that sub-agent inactivity timeout is configurable via env var."""

import os
from pathlib import Path


def test_resolve_inactivity_timeout_default():
    """Test that _resolve_inactivity_timeout returns 7200 by default."""
    from factory.cli._helpers import _resolve_inactivity_timeout
    
    # Clear env var if set
    old_val = os.environ.pop('FACTORY_AGENT_INACTIVITY_TIMEOUT', None)
    try:
        assert _resolve_inactivity_timeout() == 7200.0
    finally:
        if old_val is not None:
            os.environ['FACTORY_AGENT_INACTIVITY_TIMEOUT'] = old_val


def test_resolve_inactivity_timeout_from_env():
    """Test that _resolve_inactivity_timeout reads from env var."""
    from factory.cli._helpers import _resolve_inactivity_timeout
    
    old_val = os.environ.get('FACTORY_AGENT_INACTIVITY_TIMEOUT')
    try:
        os.environ['FACTORY_AGENT_INACTIVITY_TIMEOUT'] = '3600'
        assert _resolve_inactivity_timeout() == 3600.0
        
        os.environ['FACTORY_AGENT_INACTIVITY_TIMEOUT'] = '1800'
        assert _resolve_inactivity_timeout() == 1800.0
    finally:
        if old_val is not None:
            os.environ['FACTORY_AGENT_INACTIVITY_TIMEOUT'] = old_val
        else:
            os.environ.pop('FACTORY_AGENT_INACTIVITY_TIMEOUT', None)


def test_import_os_in_helpers():
    """Test that _helpers.py imports os (required for env var access)."""
    import factory.cli._helpers as helpers
    assert hasattr(helpers, 'os'), "_helpers.py must import os for env var access"


def test_no_hardcoded_7200_timeout():
    """Test that no hardcoded timeout=7200 remains in the 4 target files."""
    repo_root = Path(__file__).parent.parent
    target_files = [
        repo_root / "factory/cli/_ceo_helpers.py",
        repo_root / "factory/cli/_mode_handlers.py",
        repo_root / "factory/cli/run.py",
        repo_root / "factory/cli/outer_loop.py",
    ]
    
    for file_path in target_files:
        content = file_path.read_text()
        # Check for timeout=7200 or timeout=7200.0 (not inside a comment)
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            # Skip comment lines
            if line.strip().startswith('#'):
                continue
            # Check for hardcoded timeout value
            if 'timeout=7200' in line and '_resolve_inactivity_timeout()' not in line:
                raise AssertionError(
                    f"{file_path}:{i} contains hardcoded 'timeout=7200': {line.strip()}"
                )
