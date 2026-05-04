# Copyright 2026, Jamf Software LLC
"""MDM management command tools for Jamf Pro.

This module provides tools for sending MDM commands to managed Apple devices
(macOS computers, iOS/iPadOS, and tvOS devices) via the Jamf Pro API v2.

All commands use POST /api/v2/mdm/commands which requires the device's
managementId (UUID). Numeric Jamf device IDs are resolved automatically.

Destructive commands (lock, erase, restart, etc.) require confirm=True.
Always verify device identity with the user before sending destructive commands.
"""

import logging
from typing import Any, Optional

from ..client import JamfAPIError
from ._common import format_error, format_response, get_client_safe
from ._registry import jamf_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_confirmation(confirm: bool, action: str) -> Optional[str]:
    """Return a confirmation-required error response if confirm is False.

    Args:
        confirm: Whether the caller has confirmed the action
        action: Human-readable description of the destructive action

    Returns:
        JSON error string if confirmation is missing, None if confirmed
    """
    if not confirm:
        import json
        return json.dumps({
            "success": False,
            "error": f"Confirmation required for destructive action: {action}",
            "hint": (
                "Re-call this tool with confirm=True ONLY after you have verified "
                "the exact device name and serial number with the user and they have "
                "explicitly authorized this action."
            ),
        }, indent=2)
    return None


async def _resolve_management_ids(
    client,
    computer_ids: Optional[list[int]] = None,
    mobile_device_ids: Optional[list[int]] = None,
    management_ids: Optional[list[str]] = None,
) -> tuple[list[str], list[str]]:
    """Resolve a mix of numeric IDs and UUID managementIds to UUIDs.

    The v2 MDM commands API identifies devices by their managementId (UUID),
    not by numeric Jamf Pro IDs. This helper resolves numeric IDs by fetching
    device inventory and extracting the managementId field.

    Args:
        client: JamfClient instance
        computer_ids: List of numeric Jamf Pro computer IDs
        mobile_device_ids: List of numeric Jamf Pro mobile device IDs
        management_ids: Pre-resolved management UUIDs (passed through unchanged)

    Returns:
        Tuple of (resolved_management_ids, errors). errors is a list of
        human-readable strings describing any resolution failures.
    """
    resolved: list[str] = list(management_ids or [])
    errors: list[str] = []

    for cid in (computer_ids or []):
        try:
            detail = await client.v1_get(f"computers-inventory-detail/{cid}")
            mid = detail.get("general", {}).get("managementId")
            if mid:
                resolved.append(mid)
            else:
                errors.append(f"Computer {cid}: managementId not found in inventory")
        except JamfAPIError as e:
            errors.append(f"Computer {cid}: API error {e.status_code} - {e}")
        except Exception as e:
            errors.append(f"Computer {cid}: unexpected error - {e}")

    for did in (mobile_device_ids or []):
        try:
            detail = await client.v2_get(f"mobile-devices/{did}/detail")
            mid = detail.get("general", {}).get("managementId")
            if mid:
                resolved.append(mid)
            else:
                errors.append(f"Mobile device {did}: managementId not found in inventory")
        except JamfAPIError as e:
            errors.append(f"Mobile device {did}: API error {e.status_code} - {e}")
        except Exception as e:
            errors.append(f"Mobile device {did}: unexpected error - {e}")

    return resolved, errors


def _build_command_payload(
    command_type: str,
    management_ids: list[str],
    **command_data: Any,
) -> dict:
    """Build the request body for POST /api/v2/mdm/commands.

    Args:
        command_type: The MDM command type string (e.g. "DEVICE_LOCK")
        management_ids: List of device managementId UUIDs
        **command_data: Additional fields merged into commandData

    Returns:
        Request body dict ready for v2_post
    """
    client_data = [{"managementId": mid} for mid in management_ids]
    command_data_dict: dict[str, Any] = {"commandType": command_type}
    command_data_dict.update({k: v for k, v in command_data.items() if v is not None})
    return {"clientData": client_data, "commandData": command_data_dict}


async def _send_command(
    client,
    command_type: str,
    management_ids: list[str],
    **command_data: Any,
) -> dict:
    """POST a command to /api/v2/mdm/commands and return the raw response."""
    payload = _build_command_payload(command_type, management_ids, **command_data)
    return await client.v2_post("mdm/commands", payload)


# ---------------------------------------------------------------------------
# Query / inspection tools (non-destructive, no confirmation required)
# ---------------------------------------------------------------------------


@jamf_tool
async def jamf_list_mdm_commands(
    status: Optional[str] = "Pending",
    command_type: Optional[str] = None,
    client_management_id: Optional[str] = None,
    client_type: Optional[str] = None,
    page: int = 0,
    page_size: int = 100,
) -> str:
    """List MDM commands queued or completed in Jamf Pro.

    Retrieves MDM command history. The Jamf API requires at least one filter
    field — status defaults to 'Pending' when no other filter is specified.

    Args:
        status: Command status filter. One of: Pending, Acknowledged, Error,
            CommandFormatError, Idle, NotNow, Completed. Defaults to 'Pending'.
        command_type: Filter by command type, e.g. DEVICE_LOCK, ERASE_DEVICE,
            RESTART_DEVICE, CLEAR_PASSCODE, ENABLE_LOST_MODE, DEVICE_INFORMATION.
        client_management_id: Filter by specific device managementId UUID.
        client_type: Filter by device type: COMPUTER, MOBILE_DEVICE, APPLE_TV.
        page: Page number for pagination (0-indexed, default: 0).
        page_size: Results per page (default: 100, max: 2000).

    Returns:
        JSON list of MDM command records with uuid, command, status, dateSent,
        dateCompleted, and clientType.
    """
    client, error = get_client_safe()
    if error:
        return error

    try:
        # Build RSQL filter — at least one field is required by the API
        filters: list[str] = []
        if client_management_id:
            filters.append(f'clientManagementId=={client_management_id}')
        if command_type:
            filters.append(f'command=={command_type}')
        if client_type:
            filters.append(f'clientType=={client_type}')
        # Apply status last so it can be omitted when other filters are present
        if status:
            filters.append(f'status=={status}')
        elif not filters:
            # Fallback: API requires at least one filter
            filters.append('status==Pending')

        params: dict[str, Any] = {
            "page": page,
            "page-size": page_size,
            "filter": ";".join(filters),
            "sort": "dateSent:desc",
        }

        result = await client.v2_get("mdm/commands", params=params)
        count = result.get("totalCount", len(result.get("results", [])))
        return format_response(result, f"Retrieved {count} MDM commands")

    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error listing MDM commands")
        return format_error(e)


@jamf_tool
async def jamf_get_device_management_id(
    computer_id: Optional[int] = None,
    mobile_device_id: Optional[int] = None,
) -> str:
    """Resolve a Jamf Pro numeric device ID to its MDM managementId (UUID).

    The v2 MDM commands API requires a managementId UUID rather than the
    numeric Jamf Pro device ID. Use this tool to look up a device's UUID
    before sending management commands.

    Provide exactly one of computer_id or mobile_device_id.

    Args:
        computer_id: Numeric Jamf Pro computer ID.
        mobile_device_id: Numeric Jamf Pro mobile device ID.

    Returns:
        JSON with the device's managementId UUID and key identifying details
        (name, serialNumber, model).
    """
    client, error = get_client_safe()
    if error:
        return error

    if not computer_id and not mobile_device_id:
        import json
        return json.dumps({"success": False, "error": "Provide computer_id or mobile_device_id"}, indent=2)

    try:
        if computer_id:
            detail = await client.v1_get(f"computers-inventory-detail/{computer_id}")
            general = detail.get("general", {})
            result = {
                "deviceType": "computer",
                "jamfId": computer_id,
                "managementId": general.get("managementId"),
                "name": general.get("name"),
                "serialNumber": general.get("serialNumber"),
                "model": detail.get("hardware", {}).get("model"),
            }
        else:
            detail = await client.v2_get(f"mobile-devices/{mobile_device_id}/detail")
            general = detail.get("general", {})
            result = {
                "deviceType": "mobile_device",
                "jamfId": mobile_device_id,
                "managementId": general.get("managementId"),
                "name": general.get("name"),
                "serialNumber": general.get("serialNumber"),
                "model": general.get("model"),
            }

        return format_response(result, "Resolved device managementId")

    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error resolving device managementId")
        return format_error(e)


@jamf_tool
async def jamf_send_mdm_command(
    command_type: str,
    management_ids: Optional[list[str]] = None,
    computer_ids: Optional[list[int]] = None,
    mobile_device_ids: Optional[list[int]] = None,
    command_data: Optional[dict] = None,
    confirm: bool = False,
) -> str:
    """Send any MDM command to one or more managed Apple devices.

    Generic low-level tool for sending MDM commands via the Jamf Pro v2 API.
    For common operations prefer the dedicated tools (jamf_lock_device,
    jamf_erase_device, etc.) which have typed parameters and clearer docs.

    Devices can be identified by managementId UUID (preferred), or by numeric
    Jamf computer/mobile device IDs which are resolved automatically.

    ⚠️ Many command types are DESTRUCTIVE (DEVICE_LOCK, ERASE_DEVICE,
    RESTART_DEVICE, SHUT_DOWN_DEVICE, CLEAR_PASSCODE, SET_RECOVERY_LOCK,
    DELETE_USER). Confirm device identity with the user before setting
    confirm=True.

    Args:
        command_type: MDM command type. Supported values include:
            DEVICE_LOCK, ERASE_DEVICE, RESTART_DEVICE, SHUT_DOWN_DEVICE,
            CLEAR_PASSCODE, ENABLE_LOST_MODE, DISABLE_LOST_MODE,
            SET_RECOVERY_LOCK, ENABLE_REMOTE_DESKTOP, DISABLE_REMOTE_DESKTOP,
            LOG_OUT_USER, DELETE_USER, UNLOCK_USER_ACCOUNT,
            DEVICE_INFORMATION, DEVICE_LOCATION, SECURITY_INFO,
            INSTALLED_APPLICATION_LIST, PROFILE_LIST, and others.
        management_ids: List of device managementId UUIDs. Use
            jamf_get_device_management_id to look these up.
        computer_ids: Numeric Jamf Pro computer IDs (resolved automatically).
        mobile_device_ids: Numeric Jamf Pro mobile device IDs (resolved).
        command_data: Additional command-specific fields as a dict. For example,
            DEVICE_LOCK accepts {"message": "...", "phoneNumber": "...", "pin": "123456"}.
        confirm: Must be True for destructive commands. Re-call with confirm=True
            ONLY after the user has explicitly authorized the action.

    Returns:
        JSON with command queuing result or confirmation-required error.
    """
    DESTRUCTIVE_COMMANDS = {
        "DEVICE_LOCK", "ERASE_DEVICE", "RESTART_DEVICE", "SHUT_DOWN_DEVICE",
        "CLEAR_PASSCODE", "CLEAR_RESTRICTIONS_PASSWORD", "SET_RECOVERY_LOCK",
        "DELETE_USER", "LOG_OUT_USER", "UNLOCK_USER_ACCOUNT",
        "SET_AUTO_ADMIN_PASSWORD",
    }

    if command_type in DESTRUCTIVE_COMMANDS:
        err = _require_confirmation(confirm, f"{command_type} on {len(management_ids or []) + len(computer_ids or []) + len(mobile_device_ids or [])} device(s)")
        if err:
            return err

    client, error = get_client_safe()
    if error:
        return error

    if not management_ids and not computer_ids and not mobile_device_ids:
        import json
        return json.dumps({
            "success": False,
            "error": "Provide at least one of: management_ids, computer_ids, mobile_device_ids",
        }, indent=2)

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client, computer_ids, mobile_device_ids, management_ids
        )

        if not resolved:
            import json
            return json.dumps({
                "success": False,
                "error": "No devices could be resolved",
                "resolution_errors": resolution_errors,
            }, indent=2)

        result = await _send_command(client, command_type, resolved, **(command_data or {}))

        response_data: dict[str, Any] = {
            "commandType": command_type,
            "devicesTargeted": len(resolved),
            "managementIds": resolved,
        }
        if resolution_errors:
            response_data["resolution_errors"] = resolution_errors
        if result:
            response_data["apiResponse"] = result

        return format_response(response_data, f"Queued {command_type} for {len(resolved)} device(s)")

    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending MDM command")
        return format_error(e)


# ---------------------------------------------------------------------------
# Lifecycle / security management (destructive — require confirm=True)
# ---------------------------------------------------------------------------


@jamf_tool
async def jamf_lock_device(
    message: Optional[str] = None,
    phone_number: Optional[str] = None,
    pin: Optional[str] = None,
    computer_id: Optional[int] = None,
    mobile_device_id: Optional[int] = None,
    management_id: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Remotely lock a managed Apple device.

    Sends DEVICE_LOCK to the target device. The device is immediately locked
    and requires the PIN/passcode to unlock.

    For macOS computers a 6-digit numeric PIN is required to display on the
    lock screen — the user must enter this PIN to unlock after MDM removal.
    For iOS/iPadOS devices the current device passcode is used; the PIN
    parameter is ignored.

    ⚠️ DESTRUCTIVE. Before calling:
    1. Confirm the exact device name and serial number with the user.
    2. Set confirm=True only after the user has explicitly authorized locking.

    Provide exactly one of: computer_id, mobile_device_id, or management_id.

    Args:
        message: Message displayed on the lock screen (optional).
        phone_number: Phone number displayed on the lock screen (optional).
        pin: 6-digit PIN required for macOS computers to unlock post-lock.
            Ignored for iOS/iPadOS/tvOS.
        computer_id: Numeric Jamf Pro computer ID.
        mobile_device_id: Numeric Jamf Pro mobile device ID.
        management_id: Device managementId UUID (use jamf_get_device_management_id).
        confirm: Must be True to execute. Confirm device identity first.

    Returns:
        JSON with command queue confirmation or error.
    """
    err = _require_confirmation(confirm, "DEVICE_LOCK")
    if err:
        return err

    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            [computer_id] if computer_id else None,
            [mobile_device_id] if mobile_device_id else None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(
            client, "DEVICE_LOCK", resolved,
            message=message, phoneNumber=phone_number, pin=pin,
        )
        return format_response(
            {"commandType": "DEVICE_LOCK", "managementId": resolved[0], "apiResponse": result},
            f"Queued DEVICE_LOCK for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending DEVICE_LOCK")
        return format_error(e)


@jamf_tool
async def jamf_erase_device(
    pin: Optional[str] = None,
    obliteration_behavior: Optional[str] = None,
    return_to_service_enabled: Optional[bool] = None,
    computer_id: Optional[int] = None,
    mobile_device_id: Optional[int] = None,
    management_id: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Remotely erase (wipe) a managed Apple device.

    Sends ERASE_DEVICE to the target device. ALL data on the device will be
    permanently destroyed. This action is irreversible.

    ⚠️ EXTREMELY DESTRUCTIVE — IRREVERSIBLE DATA LOSS. Before calling:
    1. Confirm the exact device name and serial number with the user.
    2. Confirm the user understands ALL data will be wiped permanently.
    3. Consider whether remote lock + lost mode would be sufficient first.
    4. Set confirm=True only after the user has explicitly authorized the erase.

    Provide exactly one of: computer_id, mobile_device_id, or management_id.

    Args:
        pin: 6-digit PIN for macOS computers (displayed on screen after erase).
        obliteration_behavior: macOS-specific erase behavior. One of:
            'Default', 'DoNotObliterate', 'ObliterateWithWarning', 'Always'.
        return_to_service_enabled: If True, device auto-re-enrolls after erase
            (requires supervised device with Return to Service configured).
        computer_id: Numeric Jamf Pro computer ID.
        mobile_device_id: Numeric Jamf Pro mobile device ID.
        management_id: Device managementId UUID.
        confirm: Must be True to execute. This wipes ALL data permanently.

    Returns:
        JSON with command queue confirmation or error.
    """
    err = _require_confirmation(confirm, "ERASE_DEVICE (ALL data will be permanently destroyed)")
    if err:
        return err

    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            [computer_id] if computer_id else None,
            [mobile_device_id] if mobile_device_id else None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(
            client, "ERASE_DEVICE", resolved,
            pin=pin,
            obliterationBehavior=obliteration_behavior,
            returnToService={"enabled": return_to_service_enabled} if return_to_service_enabled is not None else None,
        )
        return format_response(
            {"commandType": "ERASE_DEVICE", "managementId": resolved[0], "apiResponse": result},
            f"Queued ERASE_DEVICE for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending ERASE_DEVICE")
        return format_error(e)


@jamf_tool
async def jamf_restart_device(
    notify_user: Optional[bool] = None,
    rebuild_kernel_cache: Optional[bool] = None,
    computer_id: Optional[int] = None,
    mobile_device_id: Optional[int] = None,
    management_id: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Remotely restart a managed Apple device.

    Sends RESTART_DEVICE to the target device. Unsaved user work may be lost.

    ⚠️ DESTRUCTIVE. Unsaved user work will be lost. Before calling:
    1. Confirm the exact device name and serial number with the user.
    2. Set confirm=True only after the user has authorized the restart.

    Provide exactly one of: computer_id, mobile_device_id, or management_id.

    Args:
        notify_user: If True, notify the logged-in user before restarting
            (macOS only). The user can defer for up to 15 minutes.
        rebuild_kernel_cache: If True, rebuild the kernel cache before restart
            (macOS only, useful after kernel extension changes).
        computer_id: Numeric Jamf Pro computer ID.
        mobile_device_id: Numeric Jamf Pro mobile device ID.
        management_id: Device managementId UUID.
        confirm: Must be True to execute.

    Returns:
        JSON with command queue confirmation or error.
    """
    err = _require_confirmation(confirm, "RESTART_DEVICE")
    if err:
        return err

    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            [computer_id] if computer_id else None,
            [mobile_device_id] if mobile_device_id else None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(
            client, "RESTART_DEVICE", resolved,
            notifyUser=notify_user,
            rebuildKernelCache=rebuild_kernel_cache,
        )
        return format_response(
            {"commandType": "RESTART_DEVICE", "managementId": resolved[0], "apiResponse": result},
            f"Queued RESTART_DEVICE for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending RESTART_DEVICE")
        return format_error(e)


@jamf_tool
async def jamf_shut_down_device(
    computer_id: Optional[int] = None,
    mobile_device_id: Optional[int] = None,
    management_id: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Remotely shut down a managed Apple device.

    Sends SHUT_DOWN_DEVICE to the target device. Unsaved user work may be lost.
    The device must be powered on manually to recover.

    ⚠️ DESTRUCTIVE. Before calling:
    1. Confirm the exact device name and serial number with the user.
    2. Set confirm=True only after the user has authorized the shutdown.

    Provide exactly one of: computer_id, mobile_device_id, or management_id.

    Args:
        computer_id: Numeric Jamf Pro computer ID.
        mobile_device_id: Numeric Jamf Pro mobile device ID.
        management_id: Device managementId UUID.
        confirm: Must be True to execute.

    Returns:
        JSON with command queue confirmation or error.
    """
    err = _require_confirmation(confirm, "SHUT_DOWN_DEVICE")
    if err:
        return err

    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            [computer_id] if computer_id else None,
            [mobile_device_id] if mobile_device_id else None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(client, "SHUT_DOWN_DEVICE", resolved)
        return format_response(
            {"commandType": "SHUT_DOWN_DEVICE", "managementId": resolved[0], "apiResponse": result},
            f"Queued SHUT_DOWN_DEVICE for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending SHUT_DOWN_DEVICE")
        return format_error(e)


@jamf_tool
async def jamf_clear_passcode(
    mobile_device_id: Optional[int] = None,
    management_id: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Clear the passcode on a managed iOS/iPadOS device.

    Sends CLEAR_PASSCODE to the target mobile device, removing the screen
    lock passcode. After the command executes the device is unlocked and
    the user must set a new passcode.

    This command applies to iOS/iPadOS/tvOS only. macOS uses
    FileVault recovery keys for disk-level access.

    ⚠️ DESTRUCTIVE. This removes the device's screen lock protection.
    Before calling:
    1. Confirm the exact device name and serial number with the user.
    2. Set confirm=True only after the user has authorized this action.

    Provide exactly one of: mobile_device_id or management_id.

    Args:
        mobile_device_id: Numeric Jamf Pro mobile device ID.
        management_id: Device managementId UUID.
        confirm: Must be True to execute.

    Returns:
        JSON with command queue confirmation or error.
    """
    err = _require_confirmation(confirm, "CLEAR_PASSCODE")
    if err:
        return err

    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            None,
            [mobile_device_id] if mobile_device_id else None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(client, "CLEAR_PASSCODE", resolved)
        return format_response(
            {"commandType": "CLEAR_PASSCODE", "managementId": resolved[0], "apiResponse": result},
            f"Queued CLEAR_PASSCODE for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending CLEAR_PASSCODE")
        return format_error(e)


@jamf_tool
async def jamf_set_recovery_lock(
    new_password: str,
    computer_id: Optional[int] = None,
    management_id: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Set or clear the Recovery Lock password on a macOS Apple silicon Mac.

    Sends SET_RECOVERY_LOCK to the target Mac. The Recovery Lock password
    protects Recovery Mode — required to boot into Recovery or enter DFU mode.

    Supported on: Apple silicon Macs and Macs with Apple T2 chip running
    macOS 11.5 or later.

    To REMOVE the recovery lock, pass an empty string as new_password.

    ⚠️ DESTRUCTIVE. Changing or removing the Recovery Lock may affect
    physical security of the device. Before calling:
    1. Confirm the exact device name and serial number with the user.
    2. Set confirm=True only after the user has authorized the change.

    Provide exactly one of: computer_id or management_id.

    Args:
        new_password: New Recovery Lock password. Pass empty string "" to remove.
        computer_id: Numeric Jamf Pro computer ID.
        management_id: Device managementId UUID.
        confirm: Must be True to execute.

    Returns:
        JSON with command queue confirmation or error.
    """
    err = _require_confirmation(confirm, "SET_RECOVERY_LOCK")
    if err:
        return err

    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            [computer_id] if computer_id else None,
            None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(
            client, "SET_RECOVERY_LOCK", resolved,
            newPassword=new_password,
        )
        return format_response(
            {"commandType": "SET_RECOVERY_LOCK", "managementId": resolved[0], "apiResponse": result},
            f"Queued SET_RECOVERY_LOCK for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending SET_RECOVERY_LOCK")
        return format_error(e)


@jamf_tool
async def jamf_delete_user(
    user_name: str,
    force_deletion: Optional[bool] = None,
    delete_all_users: Optional[bool] = None,
    computer_id: Optional[int] = None,
    management_id: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Delete a user account from a managed Shared iPad or Mac.

    Sends DELETE_USER to the target device. On Shared iPad this removes
    the specified user's cached data. On macOS DEP-enrolled devices it
    removes the local user account.

    Requires: DEP-enrolled device. User data will be permanently deleted.

    ⚠️ DESTRUCTIVE. User data on this device will be permanently deleted.
    Before calling:
    1. Confirm the exact device name and serial number with the user.
    2. Confirm the username to be deleted.
    3. Set confirm=True only after the user has authorized this action.

    Provide exactly one of: computer_id or management_id.

    Args:
        user_name: Username of the account to delete.
        force_deletion: If True, force deletion even if user is logged in
            (Shared iPad only).
        delete_all_users: If True, delete all users on the device (Shared iPad).
            Overrides user_name.
        computer_id: Numeric Jamf Pro computer ID.
        management_id: Device managementId UUID.
        confirm: Must be True to execute.

    Returns:
        JSON with command queue confirmation or error.
    """
    err = _require_confirmation(confirm, f"DELETE_USER '{user_name}'")
    if err:
        return err

    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            [computer_id] if computer_id else None,
            None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(
            client, "DELETE_USER", resolved,
            userName=user_name,
            forceDeletion=force_deletion,
            deleteAllUsers=delete_all_users,
        )
        return format_response(
            {"commandType": "DELETE_USER", "userName": user_name, "managementId": resolved[0], "apiResponse": result},
            f"Queued DELETE_USER '{user_name}' for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending DELETE_USER")
        return format_error(e)


# ---------------------------------------------------------------------------
# Lost mode (iOS/iPadOS supervised devices)
# ---------------------------------------------------------------------------


@jamf_tool
async def jamf_enable_lost_mode(
    message: Optional[str] = None,
    phone_number: Optional[str] = None,
    footnote: Optional[str] = None,
    always_enforce: Optional[bool] = None,
    play_sound: Optional[bool] = None,
    mobile_device_id: Optional[int] = None,
    management_id: Optional[str] = None,
) -> str:
    """Enable Lost Mode on a supervised iOS/iPadOS device.

    Sends ENABLE_LOST_MODE to the target device. Lost Mode locks the device
    and displays the specified message and phone number on the lock screen.
    GPS location tracking is enabled while Lost Mode is active.

    Requires: Supervised iOS/iPadOS device. At least one of message or
    phone_number must be provided.

    Provide exactly one of: mobile_device_id or management_id.

    Args:
        message: Message displayed on the lock screen. Required if phone_number
            is not set.
        phone_number: Phone number displayed on the lock screen. Required if
            message is not set.
        footnote: Optional footnote text displayed on the lock screen.
        always_enforce: If True, Lost Mode cannot be disabled by the user
            (default True). Set to False to allow user to disable.
        play_sound: If True, plays a sound when Lost Mode is enabled
            (default False).
        mobile_device_id: Numeric Jamf Pro mobile device ID.
        management_id: Device managementId UUID.

    Returns:
        JSON with command queue confirmation or error.
    """
    if not message and not phone_number:
        import json
        return json.dumps({
            "success": False,
            "error": "At least one of 'message' or 'phone_number' is required for ENABLE_LOST_MODE",
        }, indent=2)

    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            None,
            [mobile_device_id] if mobile_device_id else None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(
            client, "ENABLE_LOST_MODE", resolved,
            lostModeMessage=message,
            lostModePhone=phone_number,
            lostModeFootnote=footnote,
            alwaysEnforceLostMode=always_enforce,
            lostModeWithSound=play_sound,
        )
        return format_response(
            {"commandType": "ENABLE_LOST_MODE", "managementId": resolved[0], "apiResponse": result},
            f"Queued ENABLE_LOST_MODE for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending ENABLE_LOST_MODE")
        return format_error(e)


@jamf_tool
async def jamf_disable_lost_mode(
    mobile_device_id: Optional[int] = None,
    management_id: Optional[str] = None,
) -> str:
    """Disable Lost Mode on a supervised iOS/iPadOS device.

    Sends DISABLE_LOST_MODE to the target device, restoring normal operation.
    Lost Mode must be active on the device for this command to have effect.

    Requires: Supervised iOS/iPadOS device currently in Lost Mode.

    Provide exactly one of: mobile_device_id or management_id.

    Args:
        mobile_device_id: Numeric Jamf Pro mobile device ID.
        management_id: Device managementId UUID.

    Returns:
        JSON with command queue confirmation or error.
    """
    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            None,
            [mobile_device_id] if mobile_device_id else None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(client, "DISABLE_LOST_MODE", resolved)
        return format_response(
            {"commandType": "DISABLE_LOST_MODE", "managementId": resolved[0], "apiResponse": result},
            f"Queued DISABLE_LOST_MODE for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending DISABLE_LOST_MODE")
        return format_error(e)


# ---------------------------------------------------------------------------
# Remote desktop (macOS 10.14.4+)
# ---------------------------------------------------------------------------


@jamf_tool
async def jamf_enable_remote_desktop(
    computer_id: Optional[int] = None,
    management_id: Optional[str] = None,
) -> str:
    """Enable Remote Desktop (Screen Sharing) on a managed Mac.

    Sends ENABLE_REMOTE_DESKTOP to the target Mac, enabling the macOS
    Screen Sharing / Remote Management service so administrators can
    connect remotely.

    Requires: macOS 10.14.4 or later.

    Provide exactly one of: computer_id or management_id.

    Args:
        computer_id: Numeric Jamf Pro computer ID.
        management_id: Device managementId UUID.

    Returns:
        JSON with command queue confirmation or error.
    """
    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            [computer_id] if computer_id else None,
            None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(client, "ENABLE_REMOTE_DESKTOP", resolved)
        return format_response(
            {"commandType": "ENABLE_REMOTE_DESKTOP", "managementId": resolved[0], "apiResponse": result},
            f"Queued ENABLE_REMOTE_DESKTOP for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending ENABLE_REMOTE_DESKTOP")
        return format_error(e)


@jamf_tool
async def jamf_disable_remote_desktop(
    computer_id: Optional[int] = None,
    management_id: Optional[str] = None,
) -> str:
    """Disable Remote Desktop (Screen Sharing) on a managed Mac.

    Sends DISABLE_REMOTE_DESKTOP to the target Mac, disabling the macOS
    Screen Sharing / Remote Management service.

    Requires: macOS 10.14.4 or later.

    Provide exactly one of: computer_id or management_id.

    Args:
        computer_id: Numeric Jamf Pro computer ID.
        management_id: Device managementId UUID.

    Returns:
        JSON with command queue confirmation or error.
    """
    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            [computer_id] if computer_id else None,
            None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(client, "DISABLE_REMOTE_DESKTOP", resolved)
        return format_response(
            {"commandType": "DISABLE_REMOTE_DESKTOP", "managementId": resolved[0], "apiResponse": result},
            f"Queued DISABLE_REMOTE_DESKTOP for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending DISABLE_REMOTE_DESKTOP")
        return format_error(e)


@jamf_tool
async def jamf_log_out_user(
    computer_id: Optional[int] = None,
    mobile_device_id: Optional[int] = None,
    management_id: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Log out the currently logged-in user on a managed Apple device.

    Sends LOG_OUT_USER to the target device. The current user session is
    terminated and the device returns to the login screen. Unsaved work
    may be lost.

    ⚠️ DESTRUCTIVE. Unsaved user work will be lost. Before calling:
    1. Confirm the exact device name and serial number with the user.
    2. Set confirm=True only after the user has authorized the log out.

    Provide exactly one of: computer_id, mobile_device_id, or management_id.

    Args:
        computer_id: Numeric Jamf Pro computer ID.
        mobile_device_id: Numeric Jamf Pro mobile device ID.
        management_id: Device managementId UUID.
        confirm: Must be True to execute.

    Returns:
        JSON with command queue confirmation or error.
    """
    err = _require_confirmation(confirm, "LOG_OUT_USER")
    if err:
        return err

    client, error = get_client_safe()
    if error:
        return error

    try:
        resolved, resolution_errors = await _resolve_management_ids(
            client,
            [computer_id] if computer_id else None,
            [mobile_device_id] if mobile_device_id else None,
            [management_id] if management_id else None,
        )
        if not resolved:
            import json
            return json.dumps({"success": False, "error": "Device not resolved", "details": resolution_errors}, indent=2)

        result = await _send_command(client, "LOG_OUT_USER", resolved)
        return format_response(
            {"commandType": "LOG_OUT_USER", "managementId": resolved[0], "apiResponse": result},
            f"Queued LOG_OUT_USER for device {resolved[0]}",
        )
    except JamfAPIError as e:
        return format_error(e)
    except Exception as e:
        logger.exception("Error sending LOG_OUT_USER")
        return format_error(e)
