#!/usr/bin/env python3
"""Verify Lumen training environment is correctly set up."""

import sys
from pathlib import Path


def check_package(name: str, import_path: str | None = None, min_version: str | None = None) -> bool:
    """Check if a package is installed and optionally verify version."""
    import_path = import_path or name
    try:
        module = __import__(import_path)
        version = getattr(module, "__version__", "unknown")
        print(f"✓ {name:20s} {version}")

        if min_version and version != "unknown":
            # Simple version comparison (not robust, but good enough)
            if version < min_version:
                print(f"  ⚠️  Version {version} is older than recommended {min_version}")
                return False
        return True
    except ImportError:
        print(f"✗ {name:20s} NOT FOUND")
        return False


def check_cuda() -> bool:
    """Check CUDA availability."""
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        if cuda_available:
            device_count = torch.cuda.device_count()
            device_name = torch.cuda.get_device_name(0) if device_count > 0 else "N/A"
            print(f"✓ CUDA Available      {device_count} GPU(s) - {device_name}")
            return True
        else:
            print("✗ CUDA NOT AVAILABLE")
            return False
    except Exception as e:
        print(f"✗ CUDA check failed: {e}")
        return False


def check_verl_integration() -> bool:
    """Check VERL-specific integration points."""
    checks = []

    # Check if verl.trainer.main_ppo exists
    try:
        import verl.trainer.main_ppo
        print("✓ verl.trainer.main_ppo module found")
        checks.append(True)
    except ImportError as e:
        print(f"✗ verl.trainer.main_ppo NOT FOUND: {e}")
        checks.append(False)

    # Check if factory.lumen modules exist
    try:
        import factory.lumen.train
        print("✓ factory.lumen.train module found")
        checks.append(True)
    except ImportError as e:
        print(f"✗ factory.lumen.train NOT FOUND: {e}")
        checks.append(False)

    try:
        import factory.lumen.run_verl
        print("✓ factory.lumen.run_verl module found")
        checks.append(True)
    except ImportError as e:
        print(f"✗ factory.lumen.run_verl NOT FOUND: {e}")
        checks.append(False)

    return all(checks)


def main() -> int:
    """Run all verification checks."""
    print("=" * 60)
    print("Lumen Training Environment Verification")
    print("=" * 60)
    print()

    print("Core Packages:")
    print("-" * 60)
    core_checks = [
        check_package("torch"),
        check_package("vllm"),
        check_package("verl"),
        check_package("numpy", min_version="2.0.0"),
        check_package("pandas", min_version="2.3"),
        check_package("pyarrow", min_version="19.0.0"),
        check_package("transformers"),
        check_package("accelerate"),
        check_package("ray"),
        check_package("hydra", "hydra"),
    ]
    print()

    print("CUDA & GPU:")
    print("-" * 60)
    cuda_ok = check_cuda()
    print()

    print("VERL Integration:")
    print("-" * 60)
    verl_ok = check_verl_integration()
    print()

    print("=" * 60)
    if all(core_checks) and cuda_ok and verl_ok:
        print("✓ All checks passed! Environment is ready.")
        print("=" * 60)
        return 0
    else:
        print("✗ Some checks failed. See details above.")
        print("=" * 60)

        # Provide actionable guidance
        if not all(core_checks):
            print("\nMissing packages detected. Install with:")
            print("  pip install -r requirements-core.txt")

        if not cuda_ok:
            print("\nCUDA not available. Ensure:")
            print("  1. NVIDIA drivers are installed (check: nvidia-smi)")
            print("  2. PyTorch with CUDA support is installed")
            print("     pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu129")

        if not verl_ok:
            print("\nVERL integration issue. Ensure:")
            print("  1. VERL is installed from source:")
            print("     git clone https://github.com/volcengine/verl.git && cd verl && pip install -e .")
            print("  2. You're running from the remote-factory project root:")
            print("     cd /path/to/remote-factory && python factory/lumen/env_specs/verify_env.py")

        return 1


if __name__ == "__main__":
    sys.exit(main())
