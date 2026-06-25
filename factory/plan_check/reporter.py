"""Generate verification reports in JSON and markdown formats."""

from __future__ import annotations

from factory.plan_check.models import VerificationReport


def generate_report(report: VerificationReport) -> VerificationReport:
    raise NotImplementedError
