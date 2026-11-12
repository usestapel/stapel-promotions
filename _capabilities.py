"""stapel-promotions capabilities.json emitter — thin shim over stapel_tools.capabilities."""
from pathlib import Path

from stapel_tools.capabilities import axis_group_rules, run_capabilities_cli


def main(argv=None):
    from stapel_promotions._codegen import _configure

    _configure()
    from stapel_promotions.conf import DEFAULTS
    from stapel_promotions.urls import GATE_REGISTRY

    # One CTO-facing axis, behavioural: does a cancelled order give its code
    # use back. Everything else is a bound or a format.
    axes = {"RETURN_USAGE_ON_RELEASE"}
    return run_capabilities_cli(
        argv,
        repo=Path(__file__).resolve().parent,
        canonical_prefix="/promotions/api/v1",
        defaults=DEFAULTS,
        registry=GATE_REGISTRY,
        is_axis=lambda k: k in axes,
        axis_group=axis_group_rules(exact={"RETURN_USAGE_ON_RELEASE": "promotions.redemption"}),
        prog="stapel-promotions-capabilities",
    )


if __name__ == "__main__":
    raise SystemExit(main())
