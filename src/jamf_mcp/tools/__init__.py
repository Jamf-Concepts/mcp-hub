# Copyright 2026, Jamf Software LLC
"""Jamf Pro MCP Tools.

This package provides tools for interacting with Jamf Pro's API through
the Model Context Protocol (MCP).

Tools are automatically registered with the MCP server using the @jamf_tool
decorator. Import register_all_tools and call it with the mcp server instance
to register all tools.

Example:
    from .tools import register_all_tools, set_client
    from .client import JamfClient

    client = JamfClient(...)
    set_client(client)
    register_all_tools(mcp)
"""

# Common utilities
from ._common import (
    PRODUCT_CONFIG,
    format_error,
    format_not_configured_error,
    format_response,
    get_client,
    get_client_safe,
    get_protect_client,
    get_protect_client_safe,
    get_security_client,
    get_security_client_safe,
    is_pro_available,
    is_protect_available,
    is_security_available,
    set_client,
    set_protect_client,
    set_security_client,
)

# Registry for auto-registration
from ._registry import get_registered_tools, register_all

# Import all tool modules to register their @jamf_tool decorated functions.
# The imports trigger the decorator which adds them to the registry.
from . import api_roles
from . import app_installers
from . import apps
from . import categories
from . import computers
from . import extension_attributes
from . import groups
from . import locations
from . import mdm_commands
from . import mobile_devices
from . import policies
from . import prestages
from . import printers
from . import profiles
from . import risk
from . import scripts
from . import users

# Jamf Protect tools (optional - only work when Protect is configured)
from . import protect_alerts
from . import protect_computers
from . import protect_analytics

# Setup and onboarding tools (always available)
from . import setup


def register_all_tools(
    mcp,
    tool_filter: str | None = None,
    allowed_products: list[str] | None = None,
) -> None:
    """Register all Jamf MCP tools with the FastMCP server.

    This function registers all tools decorated with @jamf_tool.
    Call this after creating the mcp server instance and setting up the client.

    Args:
        mcp: The FastMCP server instance.
        tool_filter: Optional filter for tool types ('api', 'complex', or 'all').
        allowed_products: Optional list of product names to register tools for.
    """
    register_all(mcp, tool_filter, allowed_products)


__all__ = [
    # Registration
    "register_all_tools",
    "get_registered_tools",
    # Product configuration
    "PRODUCT_CONFIG",
    # Client management (Jamf Pro)
    "set_client",
    "get_client",
    "get_client_safe",
    "is_pro_available",
    # Client management (Jamf Protect)
    "set_protect_client",
    "get_protect_client",
    "get_protect_client_safe",
    "is_protect_available",
    # Client management (Jamf Security Cloud)
    "set_security_client",
    "get_security_client",
    "get_security_client_safe",
    "is_security_available",
    # Utilities
    "format_response",
    "format_error",
    "format_not_configured_error",
]
