# Copyright 2026, Jamf Software LLC
"""Computer management tools for Jamf Pro.

This module provides tools for retrieving and updating macOS computer
inventory information.
"""

import logging
from typing import Any, Optional, Union

from ..client import JamfAPIError
from ._common import format_error, format_response, get_client_safe
from ._registry import jamf_tool

logger = logging.getLogger(__name__)


def _build_fields(field_mapping: dict[str, Any]) -> dict[str, Any]:
    """Build a dict from field mapping, excluding None values.

    Args:
        field_mapping: Dict of API field names to values

    Returns:
        Dict containing only non-None values
    """
    return {key: value for key, value in field_mapping.items() if value is not None}


def _build_extension_attributes(extension_attributes: list[dict]) -> list[dict]:
    """Format extension attributes for the Jamf API.

    Args:
        extension_attributes: List of dicts with 'id' and 'value' keys

    Returns:
        List formatted for Jamf API with 'definitionId' and 'values'
    """
    return [
        {"definitionId": str(ea["id"]), "values": [str(ea["value"])]}
        for ea in extension_attributes
    ]


def _build_concise_computer_result(
    computer_id: int, update_data: dict, api_result: Any
) -> dict:
    """Build a concise result dict from update response.

    Args:
        computer_id: The computer ID that was updated
        update_data: The data that was sent in the update
        api_result: The raw API response

    Returns:
        Concise result dict with key fields only
    """
    result = {
        "id": computer_id,
        "updated_fields": list(update_data.keys()),
    }
    if isinstance(api_result, dict) and "general" in api_result:
        result["name"] = api_result["general"].get("name")
        result["serialNumber"] = api_result["general"].get("serialNumber")
    return result


@jamf_tool
async def jamf_get_computer(
    computer_id: Optional[int] = None,
    serial_number: Optional[str] = None,
    name: Optional[str] = None,
    page: int = 0,
    page_size: int = 100,
) -> str:
    """Get computer information from Jamf Pro.

    Retrieves detailed inventory information for macOS computers managed by Jamf Pro.
    You can search by ID, serial number, or name. If no identifier is provided,
    returns a paginated list of all computers.

    Args:
        computer_id: Jamf Pro computer ID to retrieve specific device
        serial_number: Computer serial number to search for exact match
        name: Computer name to search for (supports partial matches with wildcards)
        page: Page number for pagination (0-indexed, default: 0)
        page_size: Number of results per page (default: 100, max: 2000)

    Returns:
        JSON containing computer details or list of computers with inventory data
        including hardware, software, and management information.
    """
    client, error = get_client_safe()
    if error:
        return error

    # Jamf Pro returns operatingSystem/hardware only when sections are
    # requested (or via computers-inventory-detail). Without this, MCP
    # callers always see operatingSystem: null.
    default_sections = [
        "GENERAL",
        "OPERATING_SYSTEM",
        "HARDWARE",
        "USER_AND_LOCATION",
    ]

    try:
        if computer_id:
            result = await client.v1_get(
                f"computers-inventory/{computer_id}",
                params={"section": default_sections},
            )
            return format_response(result, f"Retrieved computer ID {computer_id}")

        filters = []
        if serial_number:
            filters.append(f'hardware.serialNumber=="{serial_number}"')
        if name:
            filters.append(f'general.name=="{name}*"')

        params = {
            "page": page,
            "page-size": page_size,
            "section": default_sections,
        }
        if filters:
            params["filter"] = " and ".join(filters)

        result = await client.v1_get("computers-inventory", params=params)
        count = result.get("totalCount", len(result.get("results", [])))
        return format_response(result, f"Retrieved {count} computers")

    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error getting computer info")
        return format_error(e)


@jamf_tool
async def jamf_update_computer(
    computer_id: int,
    name: Optional[str] = None,
    asset_tag: Optional[str] = None,
    username: Optional[str] = None,
    realname: Optional[str] = None,
    email: Optional[str] = None,
    department_id: Optional[Union[str, int]] = None,
    building_id: Optional[Union[str, int]] = None,
    room: Optional[str] = None,
    position: Optional[str] = None,
    phone: Optional[str] = None,
    extension_attributes: Optional[list[dict]] = None,
) -> str:
    """Update computer information in Jamf Pro.

    Updates inventory fields for a specific computer. Only provided fields will be
    updated; omitted fields remain unchanged. Useful for updating asset information,
    user assignments, and custom extension attribute values.

    Args:
        computer_id: Jamf Pro computer ID to update (required)
        name: New computer name
        asset_tag: Asset tag value for inventory tracking
        username: Assigned user's username
        realname: Assigned user's full/display name
        email: Assigned user's email address
        department_id: Department ID (use jamf_get_departments to find valid IDs).
            Accepts integer or string, e.g., 1 or "1"
        building_id: Building ID (use jamf_get_buildings to find valid IDs).
            Accepts integer or string, e.g., 28 or "28"
        room: Room number or name (free-form text, no validation)
        position: User's job position or title (free-form text)
        phone: Contact phone number (free-form text)
        extension_attributes: List of extension attribute updates, each with:
            - id: Extension attribute definition ID (integer)
            - value: New value to set (string)

    Returns:
        JSON with update confirmation and the updated computer record.
    """
    client, error = get_client_safe()
    if error:
        return error

    try:
        update_data = {}

        general = _build_fields({"name": name, "assetTag": asset_tag})
        if general:
            update_data["general"] = general

        user_and_location = _build_fields({
            "username": username,
            "realname": realname,
            "email": email,
            "departmentId": str(department_id) if department_id is not None else None,
            "buildingId": str(building_id) if building_id is not None else None,
            "room": room,
            "position": position,
            "phone": phone,
        })
        if user_and_location:
            update_data["userAndLocation"] = user_and_location

        if extension_attributes:
            update_data["extensionAttributes"] = _build_extension_attributes(
                extension_attributes
            )

        if not update_data:
            return format_error(ValueError("No update fields provided"))

        result = await client.v1_patch(
            f"computers-inventory-detail/{computer_id}", update_data
        )
        concise_result = _build_concise_computer_result(computer_id, update_data, result)
        return format_response(concise_result, f"Updated computer ID {computer_id}")

    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error updating computer info")
        return format_error(e)
