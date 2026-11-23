"""Helpers for live scripts. Deliberately NOT importing tests.conftest (which forces optimisation flags off)."""
from app.core.auth import Principal, Role


def make_principal(role: str, **kw) -> Principal:
    base = dict(user_id=f"u-{role}", role=Role(role), org_id="org1", school_id="sch1", batch_ids=["b1"])
    return Principal(**{**base, **kw})


# The synthetic corpus the legacy benchmarks/live checks were written for (their questions are about the old dummy docs).
from pathlib import Path as _P
SYNTH_KNOWLEDGE = _P(__file__).resolve().parent.parent / "tests" / "fixtures" / "knowledge_synthetic"
