"""Offline regression tests for MDM command safety guards.

These tests exercise the guard logic in jamf_mcp.tools.mdm_commands with a
recording fake Jamf client — no live Jamf Pro instance is required and no
real MDM command can ever be sent.

They cover the safety review findings on PR #7:
  1. command_data must not be able to override commandType (confirm bypass)
  2. non-read-only command types must require confirmation (allowlist)
  3. destructive commands must refuse multiple targets
  4. single-device tools must reject calls that name more than one device
  5. CLEAR_PASSCODE must send the spec-required unlockToken
"""

import inspect
import json

import pytest

from jamf_mcp.tools import _common
from jamf_mcp.tools.mdm_commands import (
    _build_command_payload,
    jamf_clear_passcode,
    jamf_delete_user,
    jamf_disable_lost_mode,
    jamf_disable_remote_desktop,
    jamf_enable_lost_mode,
    jamf_enable_remote_desktop,
    jamf_erase_device,
    jamf_get_device_management_id,
    jamf_lock_device,
    jamf_log_out_user,
    jamf_restart_device,
    jamf_send_mdm_command,
    jamf_set_recovery_lock,
    jamf_shut_down_device,
)


class RecordingJamfClient:
    """Fake JamfClient that records every POST and never touches the network."""

    def __init__(self):
        self.posts: list[tuple[str, dict]] = []

    async def v1_get(self, endpoint, params=None):
        return {"general": {"managementId": f"mgmt-computer-{endpoint.split('/')[-1]}"}}

    async def v2_get(self, endpoint, params=None):
        if endpoint.startswith("mobile-devices/"):
            return {"general": {"managementId": f"mgmt-mobile-{endpoint.split('/')[1]}"}}
        return {"results": [], "totalCount": 0}

    async def v2_post(self, endpoint, data):
        self.posts.append((endpoint, data))
        return {"uuid": "fake-command-uuid"}


@pytest.fixture(autouse=True)
def fake_client():
    client = RecordingJamfClient()
    _common.set_client(client)
    yield client
    _common._client = None


def parse(result_json: str) -> dict:
    return json.loads(result_json)


# ---------------------------------------------------------------------------
# Finding 1: command_data must not override commandType
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confirm", [False, True])
async def test_command_data_cannot_override_command_type(fake_client, confirm):
    """command_data={'commandType': 'ERASE_DEVICE'} must never smuggle a
    different (possibly destructive) command past the confirmation guard."""
    result = parse(await jamf_send_mdm_command(
        command_type="DEVICE_INFORMATION",
        management_ids=["uuid-1"],
        command_data={"commandType": "ERASE_DEVICE"},
        confirm=confirm,
    ))
    assert result["success"] is False
    assert "commandType" in result["error"]
    assert fake_client.posts == [], "no command may be sent when commandType is smuggled"


def test_build_command_payload_rejects_command_type_key():
    with pytest.raises(ValueError, match="commandType"):
        _build_command_payload("DEVICE_INFORMATION", ["uuid-1"], commandType="ERASE_DEVICE")


# ---------------------------------------------------------------------------
# Finding 2: allowlist — anything not read-only requires confirmation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command_type", [
    "ENABLE_LOST_MODE",
    "ENABLE_REMOTE_DESKTOP",
    "DISABLE_LOST_MODE",
    "DISABLE_REMOTE_DESKTOP",
    "PLAY_LOST_MODE_SOUND",
    "erase_device",           # lowercase variant must not slip past the guard
    "SOME_FUTURE_COMMAND",    # unknown types fail closed
    "ERASE_DEVICE",
])
async def test_non_read_only_commands_require_confirmation(fake_client, command_type):
    result = parse(await jamf_send_mdm_command(
        command_type=command_type,
        management_ids=["uuid-1"],
        confirm=False,
    ))
    assert result["success"] is False
    assert "confirm" in (result.get("hint", "") + result.get("error", "")).lower()
    assert fake_client.posts == []


# The read-only query commands from the Jamf Pro API spec (MdmCommandType
# enum, POST /api/v2/mdm/commands). Every other command type changes device
# state and must require confirmation.
READ_ONLY = (
    "CERTIFICATE_LIST",
    "DEVICE_INFORMATION",
    "DEVICE_LOCATION",
    "INSTALLED_APPLICATION_LIST",
    "MANAGED_APPLICATION_LIST",
    "MANAGED_MEDIA_LIST",
    "PROFILE_LIST",
    "PROVISIONING_PROFILE_LIST",
    "SECURITY_INFO",
    "VERIFY_RECOVERY_LOCK",
)


@pytest.mark.parametrize("command_type", READ_ONLY)
async def test_read_only_commands_do_not_require_confirmation(fake_client, command_type):
    result = parse(await jamf_send_mdm_command(
        command_type=command_type,
        management_ids=["uuid-1"],
    ))
    assert result["success"] is True
    assert len(fake_client.posts) == 1
    _, payload = fake_client.posts[0]
    assert payload["commandData"]["commandType"] == command_type


# ---------------------------------------------------------------------------
# Finding 3: destructive commands are limited to a single target
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("targets", [
    {"management_ids": ["uuid-1", "uuid-2"]},
    {"management_ids": ["uuid-1"], "computer_ids": [101]},
    {"computer_ids": [101, 102]},
    {"computer_ids": [101], "mobile_device_ids": [202]},
])
async def test_destructive_command_refuses_multiple_targets(fake_client, targets):
    result = parse(await jamf_send_mdm_command(
        command_type="ERASE_DEVICE",
        confirm=True,
        **targets,
    ))
    assert result["success"] is False
    assert "one" in result["error"].lower()
    assert fake_client.posts == []


async def test_read_only_command_allows_multiple_targets(fake_client):
    result = parse(await jamf_send_mdm_command(
        command_type="DEVICE_INFORMATION",
        management_ids=["uuid-1", "uuid-2"],
    ))
    assert result["success"] is True
    _, payload = fake_client.posts[0]
    assert len(payload["clientData"]) == 2


# ---------------------------------------------------------------------------
# Finding 4: single-device tools must reject multiple target identifiers
# ---------------------------------------------------------------------------


def multi_target_cases():
    return [
        (jamf_lock_device, {"computer_id": 101, "mobile_device_id": 202,
                            "management_id": "uuid-1", "confirm": True}),
        (jamf_erase_device, {"computer_id": 101, "mobile_device_id": 202,
                             "management_id": "uuid-1", "confirm": True}),
        (jamf_restart_device, {"computer_id": 101, "management_id": "uuid-1",
                               "confirm": True}),
        (jamf_shut_down_device, {"computer_id": 101, "management_id": "uuid-1",
                                 "confirm": True}),
        (jamf_log_out_user, {"computer_id": 101, "management_id": "uuid-1",
                             "confirm": True}),
        (jamf_clear_passcode, {"mobile_device_id": 202, "management_id": "uuid-1",
                               "unlock_token": "dG9rZW4=", "confirm": True}),
        (jamf_set_recovery_lock, {"computer_id": 101, "management_id": "uuid-1",
                                  "new_password": "pw", "confirm": True}),
        (jamf_delete_user, {"computer_id": 101, "management_id": "uuid-1",
                            "user_name": "someuser", "confirm": True}),
        (jamf_enable_lost_mode, {"mobile_device_id": 202, "management_id": "uuid-1",
                                 "message": "lost", "confirm": True}),
        (jamf_disable_lost_mode, {"mobile_device_id": 202, "management_id": "uuid-1"}),
        (jamf_enable_remote_desktop, {"computer_id": 101, "management_id": "uuid-1",
                                      "confirm": True}),
        (jamf_disable_remote_desktop, {"computer_id": 101, "management_id": "uuid-1"}),
        (jamf_get_device_management_id, {"computer_id": 101, "mobile_device_id": 202}),
    ]


@pytest.mark.parametrize("tool,kwargs", multi_target_cases())
async def test_single_device_tools_reject_multiple_targets(fake_client, tool, kwargs):
    # Keep this test callable on the unfixed revision: newly introduced
    # parameters are supplied only where the running implementation supports
    # them. The old code then demonstrates the bug (and fails the assertions)
    # instead of dying early on an unexpected keyword argument.
    kwargs = dict(kwargs)
    parameters = inspect.signature(tool).parameters
    for optional_guard in ("unlock_token", "confirm"):
        if optional_guard in kwargs and optional_guard not in parameters:
            kwargs.pop(optional_guard)

    result = parse(await tool(**kwargs))
    assert result["success"] is False
    assert "exactly one" in result["error"].lower()
    assert fake_client.posts == [], "no command may be sent for an ambiguous target"


async def test_single_device_tool_sends_to_exactly_one_device(fake_client):
    result = parse(await jamf_lock_device(
        management_id="uuid-1", pin="123456", confirm=True,
    ))
    assert result["success"] is True
    assert len(fake_client.posts) == 1
    endpoint, payload = fake_client.posts[0]
    assert endpoint == "mdm/commands"
    assert payload["clientData"] == [{"managementId": "uuid-1"}]
    assert payload["commandData"]["commandType"] == "DEVICE_LOCK"
    assert payload["commandData"]["pin"] == "123456"


# ---------------------------------------------------------------------------
# Finding 5: CLEAR_PASSCODE must include the spec-required unlockToken
# ---------------------------------------------------------------------------


async def test_clear_passcode_sends_unlock_token(fake_client):
    assert "unlock_token" in inspect.signature(jamf_clear_passcode).parameters, (
        "jamf_clear_passcode must require the spec-mandated unlock_token"
    )
    result = parse(await jamf_clear_passcode(
        management_id="uuid-1", unlock_token="dG9rZW4=", confirm=True,
    ))
    assert result["success"] is True
    _, payload = fake_client.posts[0]
    assert payload["commandData"]["commandType"] == "CLEAR_PASSCODE"
    assert payload["commandData"]["unlockToken"] == "dG9rZW4="


# ---------------------------------------------------------------------------
# Finding 2 (tools): lost-mode / remote-desktop enable tools confirm first
# ---------------------------------------------------------------------------


async def test_enable_lost_mode_requires_confirmation(fake_client):
    assert "confirm" in inspect.signature(jamf_enable_lost_mode).parameters, (
        "jamf_enable_lost_mode must expose a confirmation guard"
    )
    result = parse(await jamf_enable_lost_mode(
        management_id="uuid-1", message="lost", confirm=False,
    ))
    assert result["success"] is False
    assert fake_client.posts == []


async def test_enable_remote_desktop_requires_confirmation(fake_client):
    assert "confirm" in inspect.signature(jamf_enable_remote_desktop).parameters, (
        "jamf_enable_remote_desktop must expose a confirmation guard"
    )
    result = parse(await jamf_enable_remote_desktop(
        management_id="uuid-1", confirm=False,
    ))
    assert result["success"] is False
    assert fake_client.posts == []
