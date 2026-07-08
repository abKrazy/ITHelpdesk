"""Shared, dependency-light helpers used by more than one component.

Exposes config loading (env -> typed settings) and an Azure credential helper.
Keep this module boring and stable — many components import it.
"""

from .config import Settings, get_settings
from .credential import get_credential

__all__ = ["Settings", "get_settings", "get_credential"]
