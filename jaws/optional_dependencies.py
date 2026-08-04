"""Small, dependency-free loader for capability-specific integrations."""

from importlib import import_module
from types import ModuleType


def require_module(module_name: str, extra: str, purpose: str) -> ModuleType:
    """Import an optional module or raise an actionable installation error.

    Keeping this helper inside the lightweight package lets analytical modules expose
    pure numeric functions without importing database drivers, providers, model stacks,
    capture libraries, plotting libraries, or interface SDKs until they are invoked.
    """

    try:
        return import_module(module_name)
    except ModuleNotFoundError as exc:
        package = module_name.split(".", 1)[0]
        # If an installed integration is itself broken, retain the original missing
        # transitive-dependency error instead of misreporting the integration package
        # as absent.  The capability hint applies only when the requested import (or
        # its top-level package) is what could not be found.
        missing = exc.name or ""
        if missing != package and missing != module_name and not module_name.startswith(
            f"{missing}."
        ):
            raise
        raise ModuleNotFoundError(
            f"{purpose} requires the optional '{package}' package. "
            f"Install the JAWS capability with: python -m pip install \"JAWS[{extra}]\""
        ) from exc
