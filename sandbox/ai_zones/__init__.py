"""Two-zone AI sandbox: fail-closed policy loading and signed release installs.

Zones are fixed: ``internal`` and ``confidential``.  Nothing in this package
searches the environment or the current directory for configuration, executes
payload code, or modifies policy or secrets.
"""

from ai_zones.policy import (
    Policy,
    PolicyError,
    assert_protected,
    load_policy,
    validate_policy,
)

__all__ = [
    "Policy",
    "PolicyError",
    "assert_protected",
    "load_policy",
    "validate_policy",
]
