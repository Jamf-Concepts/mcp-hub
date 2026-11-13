#!/usr/bin/env python3
# Copyright 2026, Jamf Software LLC
"""
Test Coverage Verification Script for Jamf MCP Server

This script verifies that all MCP tools registered via @jamf_tool have
corresponding tests in test_agent.py. It helps ensure complete test coverage
and should be run whenever new tools are added or removed.

Usage:
    python3 verify_test_coverage.py

Exit codes:
    0 - All tools have test coverage
    1 - Missing test coverage detected
"""

import re
import sys
from pathlib import Path
from typing import NamedTuple


class Colors:
    """ANSI color codes for terminal output"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ENDC = '\033[0m'


class MCPTool(NamedTuple):
    """Represents an MCP tool definition"""
    name: str
    module: str


class TestMethod(NamedTuple):
    """Represents a test method"""
    name: str
    line_number: int
    category: str


# Mapping of MCP tools to their corresponding test(s)
# This defines what tests cover each tool
TOOL_TEST_MAPPING = {
    "jamf_get_computer": ["test_get_computers_list", "test_get_computer_detail"],
    "jamf_update_computer": ["test_computer_update_endpoint"],
    "jamf_get_mobile_device": ["test_get_mobile_devices_list", "test_get_mobile_device_detail"],
    "jamf_update_mobile_device": ["test_mobile_device_update_endpoint"],
    "jamf_get_user": ["test_get_users_list", "test_get_user_detail"],
    "jamf_update_user": ["test_user_update_endpoint"],
    "jamf_get_smart_groups": ["test_get_computer_groups", "test_get_mobile_device_groups"],
    "jamf_create_smart_group": ["test_create_smart_group"],
    "jamf_get_static_groups": ["test_get_computer_groups", "test_get_mobile_device_groups"],
    "jamf_create_static_group": ["test_create_static_group"],
    "jamf_get_policies": ["test_get_policies"],
    "jamf_get_app_installer_titles": ["test_get_app_installer_titles"],
    "jamf_get_app_installer_deployments": [
        "test_get_app_installer_deployments",
        "test_get_app_installer_deployment_detail",
    ],
    "jamf_create_app_installer_deployment": ["test_create_app_installer_deployment"],
    # Legacy function, covered by titles test
    "jamf_get_app_installers": ["test_get_app_installer_titles"],
    "jamf_get_computer_configuration_profiles": [
        "test_get_computer_configuration_profiles",
    ],
    "jamf_get_mobile_device_configuration_profiles": [
        "test_get_mobile_device_configuration_profiles",
    ],
    "jamf_get_scripts": ["test_get_scripts"],
    "jamf_get_extension_attributes": [
        "test_get_computer_eas",
        "test_get_mobile_eas",
        "test_get_user_eas",
    ],
    "jamf_create_extension_attribute": ["test_create_extension_attribute"],
    "jamf_get_categories": ["test_get_categories"],
    "jamf_create_category": ["test_create_category"],
    "jamf_get_buildings": ["test_get_buildings"],
    "jamf_get_departments": ["test_get_departments"],
    # Printers
    "jamf_get_printers": ["test_get_printers", "test_get_printer_detail"],
    "jamf_create_printer": ["test_create_printer"],
    "jamf_update_printer": ["test_update_printer"],
    "jamf_get_prestages": ["test_get_computer_prestages", "test_get_mobile_device_prestages"],
    "jamf_get_mac_apps": ["test_get_mac_apps"],
    "jamf_get_mobile_device_apps": ["test_get_mobile_device_apps"],
    "jamf_get_restricted_software": ["test_get_restricted_software"],
    "jamf_get_ebooks": ["test_get_ebooks"],
    "jamf_get_patch_policies": ["test_get_patch_policies"],
    # API Roles and Integrations
    "jamf_get_api_role_privileges": ["test_get_api_role_privileges"],
    "jamf_get_api_roles": ["test_get_api_roles"],
    "jamf_create_api_role": ["test_create_api_role_and_integration"],
    "jamf_get_api_integrations": ["test_get_api_integrations"],
    "jamf_create_api_integration": ["test_create_api_role_and_integration"],
    # Covered by same test
    "jamf_create_api_client_credentials": ["test_create_api_role_and_integration"],
    # Convenience function
    "jamf_create_computer_update_api_client": ["test_create_api_role_and_integration"],
    # Jamf Protect tools
    "jamf_protect_get_alert": ["test_protect_get_alert"],
    "jamf_protect_list_alerts": ["test_protect_list_alerts"],
    "jamf_protect_get_computer": ["test_protect_get_computer"],
    "jamf_protect_list_computers": ["test_protect_list_computers"],
    "jamf_protect_get_analytic": ["test_protect_get_analytic"],
    "jamf_protect_list_analytics": ["test_protect_list_analytics"],
    # Risk API (Jamf Security Cloud)
    "jamf_get_risk_devices": ["test_get_risk_devices"],
    "jamf_override_device_risk": ["test_override_device_risk"],
    # Setup tools (always available, no credentials needed)
    "jamf_get_setup_status": ["test_setup_status"],
    "jamf_configure_help": ["test_configure_help"],
    # MDM Management Commands
    "jamf_list_mdm_commands": ["test_list_mdm_commands"],
    "jamf_get_device_management_id": [
        "test_resolve_management_id_computer",
        "test_resolve_management_id_mobile",
    ],
    "jamf_send_mdm_command": [
        "test_send_mdm_command_requires_confirmation",
        "test_send_device_information_command",
    ],
    "jamf_lock_device": ["test_send_mdm_command_requires_confirmation"],
    "jamf_erase_device": ["test_send_mdm_command_requires_confirmation"],
    "jamf_restart_device": ["test_send_mdm_command_requires_confirmation"],
    "jamf_shut_down_device": ["test_send_mdm_command_requires_confirmation"],
    "jamf_clear_passcode": ["test_send_mdm_command_requires_confirmation"],
    "jamf_set_recovery_lock": ["test_send_mdm_command_requires_confirmation"],
    "jamf_delete_user": ["test_send_mdm_command_requires_confirmation"],
    "jamf_enable_lost_mode": ["test_send_device_information_command"],
    "jamf_disable_lost_mode": ["test_send_device_information_command"],
    "jamf_enable_remote_desktop": ["test_send_device_information_command"],
    "jamf_disable_remote_desktop": ["test_send_device_information_command"],
    "jamf_log_out_user": ["test_send_mdm_command_requires_confirmation"],
}


def get_registered_tools() -> list[MCPTool]:
    """Get all registered MCP tools from the tools package."""
    try:
        # Add src to path for imports
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from jamf_mcp.tools import get_registered_tools as get_tools
        tools = get_tools()
        return [MCPTool(name=func.__name__, module=func.__module__) for func, _ in tools]
    except ImportError as e:
        print(f"{Colors.RED}Error importing tools: {e}{Colors.ENDC}")
        print(f"{Colors.DIM}Make sure dependencies are installed: uv sync{Colors.ENDC}")
        sys.exit(2)


def extract_test_methods(test_path: Path) -> list[TestMethod]:
    """Extract all test methods from test_agent.py"""
    tests = []
    content = test_path.read_text()

    # Also track which category section each test is in
    current_category = "Unknown"
    lines = content.split('\n')

    for i, line in enumerate(lines):
        # Check for category comments
        if '# =====' in line and i + 1 < len(lines):
            next_line = lines[i + 1]
            if '#' in next_line:
                category_match = re.search(r'#\s*(.+)\s*(?:TESTS|$)', next_line.upper())
                if category_match:
                    current_category = category_match.group(1).strip()

        # Check for test method
        match = re.match(r'\s*async def (test_\w+)\(self\)', line)
        if match:
            tests.append(TestMethod(
                name=match.group(1),
                line_number=i + 1,
                category=current_category
            ))

    return tests


def verify_coverage(
    tools: list[MCPTool], tests: list[TestMethod]
) -> tuple[list[str], list[str], list[str]]:
    """
    Verify test coverage for all MCP tools.

    Returns:
        Tuple of (covered_tools, uncovered_tools, unmapped_tests)
    """
    test_names = {t.name for t in tests}
    covered_tools = []
    uncovered_tools = []

    for tool in tools:
        tool_name = tool.name
        required_tests = TOOL_TEST_MAPPING.get(tool_name, [])

        if not required_tests:
            # Tool not in mapping - needs to be added
            uncovered_tools.append(f"{tool_name} (NOT IN MAPPING - add to TOOL_TEST_MAPPING)")
        else:
            # Check if all required tests exist
            missing_tests = [t for t in required_tests if t not in test_names]
            if missing_tests:
                uncovered_tools.append(f"{tool_name} (missing: {', '.join(missing_tests)})")
            else:
                covered_tools.append(tool_name)

    # Find tests that aren't mapped to any tool
    all_mapped_tests = set()
    for tests_list in TOOL_TEST_MAPPING.values():
        all_mapped_tests.update(tests_list)

    # Also add authentication test which isn't a tool
    all_mapped_tests.add("test_authentication")

    unmapped_tests = [t.name for t in tests if t.name not in all_mapped_tests]

    return covered_tools, uncovered_tools, unmapped_tests


def print_report(tools: list[MCPTool], tests: list[TestMethod],
                 covered: list[str], uncovered: list[str], unmapped: list[str]):
    """Print a detailed coverage report"""
    print(f"\n{Colors.BOLD}{'=' * 70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{'JAMF MCP TEST COVERAGE REPORT':^70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{'=' * 70}{Colors.ENDC}\n")

    # Summary
    total_tools = len(tools)
    covered_count = len(covered)
    coverage_pct = (covered_count / total_tools * 100) if total_tools > 0 else 0

    print(f"  {Colors.CYAN}MCP Tools:{Colors.ENDC}     {total_tools}")
    print(f"  {Colors.CYAN}Test Methods:{Colors.ENDC}  {len(tests)}")
    print(f"  {Colors.CYAN}Coverage:{Colors.ENDC}      {coverage_pct:.1f}%")

    # Covered tools
    covered_header = f"  COVERED TOOLS ({covered_count}/{total_tools}):"
    print(f"\n{Colors.GREEN}{Colors.BOLD}{covered_header}{Colors.ENDC}")
    for tool_name in sorted(covered):
        tests_for_tool = TOOL_TEST_MAPPING.get(tool_name, [])
        print(f"    {Colors.GREEN}\u2713{Colors.ENDC} {tool_name}")
        for test in tests_for_tool:
            print(f"      {Colors.DIM}\u2514\u2500 {test}{Colors.ENDC}")

    # Uncovered tools
    if uncovered:
        print(f"\n{Colors.RED}{Colors.BOLD}  UNCOVERED TOOLS ({len(uncovered)}):{Colors.ENDC}")
        for item in uncovered:
            print(f"    {Colors.RED}\u2717{Colors.ENDC} {item}")

    # Unmapped tests (tests that exist but aren't linked to a tool)
    if unmapped:
        print(f"\n{Colors.YELLOW}{Colors.BOLD}  UNMAPPED TESTS ({len(unmapped)}):{Colors.ENDC}")
        print(f"  {Colors.DIM}(Tests that exist but aren't in TOOL_TEST_MAPPING){Colors.ENDC}")
        for test in unmapped:
            print(f"    {Colors.YELLOW}?{Colors.ENDC} {test}")

    # Final status
    print(f"\n{'=' * 70}")
    if not uncovered:
        print(f"{Colors.GREEN}{Colors.BOLD}  STATUS: ALL TOOLS HAVE TEST COVERAGE{Colors.ENDC}")
    else:
        print(f"{Colors.RED}{Colors.BOLD}  STATUS: MISSING TEST COVERAGE{Colors.ENDC}")
        print(f"{Colors.RED}  {len(uncovered)} tool(s) need tests{Colors.ENDC}")
    print(f"{'=' * 70}\n")


def main():
    """Main entry point"""
    # Determine paths
    script_dir = Path(__file__).parent
    test_path = script_dir / "test_agent.py"

    # Verify test file exists
    if not test_path.exists():
        print(f"{Colors.RED}Error: test_agent.py not found at {test_path}{Colors.ENDC}")
        sys.exit(2)

    # Get registered tools from the tools package
    tools = get_registered_tools()
    tests = extract_test_methods(test_path)

    # Verify coverage
    covered, uncovered, unmapped = verify_coverage(tools, tests)

    # Print report
    print_report(tools, tests, covered, uncovered, unmapped)

    # Exit with appropriate code
    sys.exit(0 if not uncovered else 1)


if __name__ == "__main__":
    main()
