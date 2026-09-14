"""Report availability of optional research frameworks without importing project data."""

from __future__ import annotations

import importlib
import importlib.metadata
import json

FRAMEWORKS = {
    "vectorbt": ("vectorbt", "vectorbt", "parameter screening"),
    "pybroker": ("pybroker", "lib-pybroker", "walk-forward backtesting"),
    "skfolio": ("skfolio", "skfolio", "portfolio optimization"),
    "quantstats": ("quantstats", "quantstats", "performance reports"),
    "edgartools": ("edgar", "edgartools", "SEC filing parsing"),
    "quantlib": ("QuantLib", "QuantLib", "derivatives validation"),
    "optopsy": ("optopsy", "optopsy", "options strategy research"),
    "ccxt": ("ccxt", "ccxt", "crypto market data"),
}


def inspect_frameworks() -> dict[str, dict[str, str | bool]]:
    result: dict[str, dict[str, str | bool]] = {}
    for name, (module_name, distribution_name, role) in FRAMEWORKS.items():
        try:
            importlib.import_module(module_name)
            version = importlib.metadata.version(distribution_name)
        except Exception as exc:  # The report must include broken binary imports too.
            result[name] = {"available": False, "role": role, "error": str(exc)}
        else:
            result[name] = {"available": True, "role": role, "version": version}
    return result


def main() -> int:
    result = inspect_frameworks()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(item["available"] for item in result.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
