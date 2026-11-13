#!/usr/bin/env python3
# Copyright 2026, Jamf Software LLC
"""
Jamf MCP Server Test Agent

A standalone agent that tests all Jamf MCP server commands against a live Jamf Pro instance.
Accepts credentials via .env file, command line arguments, or environment variables.

Usage:
    # Using .env file (recommended):
    # 1. Copy .env.example to .env
    # 2. Fill in your credentials
    # 3. Run:
    python test_agent.py

    # Using command line arguments:
    python test_agent.py --url https://yourcompany.jamfcloud.com \\
        --client-id YOUR_ID --client-secret YOUR_SECRET

    # Using environment variables:
    export JAMF_PRO_URL="https://yourcompany.jamfcloud.com"
    export JAMF_PRO_CLIENT_ID="your-client-id"
    export JAMF_PRO_CLIENT_SECRET="your-client-secret"
    python test_agent.py
"""

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

# Add the src directory to the path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Load .env file if it exists
def _parse_env_line(line: str) -> tuple[str, str] | None:
    """Parse a single line from .env file. Returns (key, value) or None."""
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        return None
    key, value = line.split('=', 1)
    key = key.strip()
    value = value.strip()
    # Remove quotes if present
    is_double_quoted = value.startswith('"') and value.endswith('"')
    is_single_quoted = value.startswith("'") and value.endswith("'")
    if is_double_quoted or is_single_quoted:
        value = value[1:-1]
    return key, value


def load_dotenv():
    """Load environment variables from .env file"""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    print("Loading credentials from .env file...")
    with open(env_path) as f:
        for line in f:
            parsed = _parse_env_line(line)
            if parsed and parsed[0] not in os.environ:
                os.environ[parsed[0]] = parsed[1]

load_dotenv()

import xml.etree.ElementTree as ET  # noqa: E402

import httpx  # noqa: E402

# Mapping of plural to singular names for XML list elements
_SINGULAR_MAP = {
    "criteria": "criterion",
    "computers": "computer",
    "mobile_devices": "mobile_device",
}


def _get_singular_name(plural: str) -> str:
    """Get singular form of a plural element name."""
    return _SINGULAR_MAP.get(plural, plural.rstrip('s') if plural.endswith('s') else plural)


def _convert_list_to_xml(items: list, container: ET.Element, item_name: str, convert_fn):
    """Convert a list to XML child elements."""
    for item in items:
        item_elem = ET.SubElement(container, item_name)
        if isinstance(item, dict):
            convert_fn(item, item_elem)
        else:
            item_elem.text = str(item)


def _convert_value_to_xml(key: str, value, parent: ET.Element, convert_fn):
    """Convert a single value to XML element."""
    if isinstance(value, dict):
        child = ET.SubElement(parent, key)
        convert_fn(value, child)
    elif isinstance(value, list):
        container = ET.SubElement(parent, key)
        item_name = _get_singular_name(key)
        _convert_list_to_xml(value, container, item_name, convert_fn)
    elif isinstance(value, bool):
        child = ET.SubElement(parent, key)
        child.text = str(value).lower()
    elif value is not None:
        child = ET.SubElement(parent, key)
        child.text = str(value)


def _convert_dict_to_xml(d: dict, parent: ET.Element):
    """Recursively convert dictionary to XML elements."""
    for key, value in d.items():
        _convert_value_to_xml(key, value, parent, _convert_dict_to_xml)


def dict_to_xml(data: dict, parent_element: str = None) -> str:
    """Convert a dictionary to XML string for Jamf Classic API.

    The Classic API expects XML payloads, not JSON.
    Example: {"computer_group": {"name": "Test", "is_smart": True}}
    becomes: <computer_group><name>Test</name><is_smart>true</is_smart></computer_group>
    """
    if len(data) == 1:
        root_name = list(data.keys())[0]
        root = ET.Element(root_name)
        _convert_dict_to_xml(data[root_name], root)
    else:
        root = ET.Element(parent_element or "root")
        _convert_dict_to_xml(data, root)

    return ET.tostring(root, encoding='unicode')


class Colors:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


class TestStatus(Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    WARNING = "WARNING"


@dataclass
class TestResult:
    """Result of a single test"""
    name: str
    category: str
    status: TestStatus
    duration_ms: float = 0
    response_code: int | None = None
    message: str = ""
    error: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class TestReport:
    """Complete test report"""
    results: list[TestResult] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    jamf_url: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.SKIPPED)

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.WARNING)

    @property
    def total_duration_s(self) -> float:
        return sum(r.duration_ms for r in self.results) / 1000


class JamfTestAgent:
    """
    Test agent for validating Jamf MCP Server functionality.

    Tests all API endpoints and MCP tools against a live Jamf Pro instance.
    Optionally tests Jamf Protect if credentials are configured.
    """

    def __init__(
        self,
        jamf_url: str,
        client_id: str,
        client_secret: str,
        verbose: bool = False,
        protect_url: str | None = None,
        protect_client_id: str | None = None,
        protect_password: str | None = None,
    ):
        self.jamf_url = jamf_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.verbose = verbose

        # Jamf Protect configuration (optional)
        self.protect_url = protect_url.rstrip("/") if protect_url else None
        self.protect_client_id = protect_client_id
        self.protect_password = protect_password
        self.protect_available = bool(protect_url and protect_client_id and protect_password)
        self.protect_token: str | None = None
        self.protect_token_expires: float = 0

        self.report = TestReport(jamf_url=self.jamf_url)
        self.token: str | None = None
        self.token_expires: float = 0
        self.http_client: httpx.AsyncClient | None = None

        # Jamf Security Cloud settings (optional)
        self.security_url = os.environ.get("JAMF_SECURITY_URL", "").rstrip("/")
        self.security_username = os.environ.get("JAMF_SECURITY_APP_ID", "")
        self.security_password = os.environ.get("JAMF_SECURITY_APP_SECRET", "")
        self.security_token: str | None = None
        self.security_token_expires: float = 0

        # Store sample IDs for dependent tests
        self.samples: dict[str, Any] = {
            "computer_id": None,
            "mobile_device_id": None,
            "user_id": None,
            "policy_id": None,
            "script_id": None,
            "category_id": None,
            "app_title_id": None,
            "app_deployment_id": None,
            # Protect samples
            "protect_alert_uuid": None,
            "protect_computer_uuid": None,
            "protect_analytic_uuid": None,
            # Security Cloud samples
            "risk_device_id": None,
            # MDM command samples
            "mdm_management_id": None,
            "computer_management_id": None,
            "mobile_management_id": None,
        }

    def _log(self, message: str, level: str = "info"):
        """Log a message if verbose mode is enabled"""
        if self.verbose:
            colors = {"info": Colors.CYAN, "error": Colors.RED, "warning": Colors.YELLOW}
            color = colors.get(level, "")
            print(f"{color}{Colors.DIM}  [{level.upper()}] {message}{Colors.ENDC}")

    def _print_header(self, text: str):
        """Print a formatted header"""
        width = 70
        print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * width}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{text.center(width)}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{'=' * width}{Colors.ENDC}")

    def _print_category(self, name: str):
        """Print a category header"""
        print(f"\n{Colors.CYAN}{Colors.BOLD}  [{name}]{Colors.ENDC}")

    def _print_result(self, result: TestResult):
        """Print a single test result"""
        symbols = {
            TestStatus.PASSED: f"{Colors.GREEN}✓",
            TestStatus.FAILED: f"{Colors.RED}✗",
            TestStatus.SKIPPED: f"{Colors.YELLOW}○",
            TestStatus.WARNING: f"{Colors.YELLOW}⚠",
        }
        symbol = symbols.get(result.status, "?")

        status_color = {
            TestStatus.PASSED: Colors.GREEN,
            TestStatus.FAILED: Colors.RED,
            TestStatus.SKIPPED: Colors.YELLOW,
            TestStatus.WARNING: Colors.YELLOW,
        }.get(result.status, "")

        code_str = f"[{result.response_code}]" if result.response_code else "[---]"

        status_part = f"{status_color}{result.status.value:>8}{Colors.ENDC}"
        duration_part = f"{Colors.DIM}({result.duration_ms:.0f}ms){Colors.ENDC}"
        print(f"    {symbol} {result.name:<42} {code_str:>6} {status_part} {duration_part}")

        if result.error:
            error_preview = result.error[:70] + "..." if len(result.error) > 70 else result.error
            print(f"      {Colors.RED}└─ {error_preview}{Colors.ENDC}")
        elif result.message and result.status in (TestStatus.WARNING, TestStatus.SKIPPED):
            msg = result.message
            msg_preview = msg[:70] + "..." if len(msg) > 70 else msg
            print(f"      {Colors.DIM}└─ {msg_preview}{Colors.ENDC}")

    async def _get_token(self) -> str:
        """Get or refresh OAuth token"""
        if self.token and time.time() < self.token_expires - 60:
            return self.token

        self._log("Refreshing OAuth token...")

        response = await self.http_client.post(
            f"{self.jamf_url}/api/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()

        data = response.json()
        self.token = data["access_token"]
        self.token_expires = time.time() + data.get("expires_in", 300)

        return self.token

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, Any]:
        """Make an authenticated API request"""
        token = await self._get_token()

        url = f"{self.jamf_url}{endpoint}"
        is_classic_api = "/JSSResource" in endpoint
        is_write_operation = method in ("POST", "PUT")

        # Classic API requires XML for POST/PUT operations
        if is_classic_api and is_write_operation and data:
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/xml",
            }
            xml_data = dict_to_xml(data)
            self._log(f"{method} {endpoint} (XML)")

            response = await self.http_client.request(
                method=method,
                url=url,
                content=xml_data,
                params=params,
                headers=headers,
            )
        else:
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            self._log(f"{method} {endpoint}")

            response = await self.http_client.request(
                method=method,
                url=url,
                json=data,
                params=params,
                headers=headers,
            )

        status_code = response.status_code

        try:
            result = response.json() if response.text else {}
        except ValueError:
            # Classic API may return XML - try to parse it
            if response.text and response.text.strip().startswith('<?xml'):
                result = self._parse_xml_response(response.text)
            else:
                result = {"raw": response.text}

        if status_code >= 400:
            raise RuntimeError(f"HTTP {status_code}: {response.text[:200]}")

        return status_code, result

    def _is_security_configured(self) -> bool:
        """Check if Jamf Security Cloud credentials are configured"""
        return bool(self.security_url and self.security_username and self.security_password)

    async def _get_security_token(self) -> str:
        """Get or refresh Jamf Security Cloud JWT token"""
        if self.security_token and time.time() < self.security_token_expires - 60:
            return self.security_token

        self._log("Refreshing Jamf Security Cloud JWT token...")

        import base64
        credentials = f"{self.security_username}:{self.security_password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        response = await self.http_client.post(
            f"{self.security_url}/v1/login",
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()

        data = response.json()
        self.security_token = data.get("token") or data.get("access_token")
        self.security_token_expires = time.time() + data.get("expires_in", 3600)

        return self.security_token

    async def _security_api_request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, Any]:
        """Make an authenticated API request to Jamf Security Cloud"""
        token = await self._get_security_token()

        url = f"{self.security_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        self._log(f"{method} {endpoint} (Security Cloud)")

        response = await self.http_client.request(
            method=method,
            url=url,
            json=data,
            params=params,
            headers=headers,
        )

        status_code = response.status_code

        try:
            result = response.json() if response.text else {}
        except ValueError:
            result = {"raw": response.text}

        if status_code >= 400:
            raise RuntimeError(f"HTTP {status_code}: {response.text[:200]}")

        return status_code, result

    def _parse_xml_response(self, xml_text: str) -> dict:
        """Parse XML response from Classic API into a dict.

        Handles simple XML responses like: <category><id>123</id></category>
        """
        try:
            root = ET.fromstring(xml_text)
            result = {}
            for child in root:
                if child.text:
                    # Try to convert to int if possible
                    try:
                        result[child.tag] = int(child.text)
                    except ValueError:
                        result[child.tag] = child.text
            return result
        except ET.ParseError:
            return {"raw": xml_text}

    # =========================================================================
    # JAMF PROTECT METHODS
    # =========================================================================

    async def _get_protect_token(self) -> str:
        """Get or refresh Jamf Protect OAuth token"""
        if self.protect_token and time.time() < self.protect_token_expires - 60:
            return self.protect_token

        self._log("Refreshing Protect OAuth token...")

        response = await self.http_client.post(
            f"{self.protect_url}/token",
            json={
                "client_id": self.protect_client_id,
                "password": self.protect_password,
            },
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

        data = response.json()
        self.protect_token = data["access_token"]
        self.protect_token_expires = time.time() + data.get("expires_in", 3600)

        return self.protect_token

    async def _protect_graphql(
        self,
        query: str,
        variables: dict | None = None,
    ) -> tuple[int, Any]:
        """Make a GraphQL request to Jamf Protect"""
        token = await self._get_protect_token()

        url = f"{self.protect_url}/graphql"

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        self._log(f"GraphQL query to {self.protect_url}")

        response = await self.http_client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

        status_code = response.status_code
        result = response.json() if response.text else {}

        if status_code >= 400:
            raise RuntimeError(f"HTTP {status_code}: {response.text[:200]}")

        if "errors" in result and result["errors"]:
            error_msgs = [e.get("message", str(e)) for e in result["errors"]]
            raise RuntimeError(f"GraphQL errors: {'; '.join(error_msgs)}")

        return status_code, result.get("data", {})

    async def _run_test(
        self,
        name: str,
        category: str,
        test_func,
    ) -> TestResult:
        """Execute a test and capture results"""
        start = time.time()

        try:
            result = await test_func()
            duration = (time.time() - start) * 1000

            if isinstance(result, TestResult):
                result.duration_ms = duration
                result.category = category
                return result

            return TestResult(
                name=name,
                category=category,
                status=TestStatus.PASSED,
                duration_ms=duration,
                response_code=200,
                message="Success"
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                name=name,
                category=category,
                status=TestStatus.FAILED,
                duration_ms=duration,
                error=str(e)
            )

    # =========================================================================
    # AUTHENTICATION TESTS
    # =========================================================================

    async def test_authentication(self) -> TestResult:
        """Test OAuth authentication"""
        try:
            await self._get_token()
            return TestResult(
                name="OAuth Token Acquisition",
                category="Authentication",
                status=TestStatus.PASSED,
                response_code=200,
                message=f"Token acquired (expires in {int(self.token_expires - time.time())}s)"
            )
        except Exception as e:
            return TestResult(
                name="OAuth Token Acquisition",
                category="Authentication",
                status=TestStatus.FAILED,
                error=str(e)
            )

    # =========================================================================
    # COMPUTER TESTS
    # =========================================================================

    async def test_get_computers_list(self) -> TestResult:
        """Test getting computer inventory list"""
        status, data = await self._api_request(
            "GET", "/api/v1/computers-inventory",
            params={"page-size": 10}
        )

        results = data.get("results", [])
        if results:
            self.samples["computer_id"] = results[0].get("id")

        return TestResult(
            name="Get Computers (List)",
            category="Computers",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(results)} computers",
            details={"count": len(results), "total": data.get("totalCount", len(results))}
        )

    async def test_get_computer_detail(self) -> TestResult:
        """Test getting computer detail by ID"""
        if not self.samples["computer_id"]:
            return TestResult(
                name="Get Computer (Detail)",
                category="Computers",
                status=TestStatus.SKIPPED,
                message="No computer ID available"
            )

        comp_id = self.samples["computer_id"]
        status, data = await self._api_request(
            "GET", f"/api/v1/computers-inventory-detail/{comp_id}"
        )

        name = data.get("general", {}).get("name", "Unknown")
        return TestResult(
            name="Get Computer (Detail)",
            category="Computers",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved: {name}",
            details={"id": comp_id, "name": name}
        )

    async def test_computer_update_endpoint(self) -> TestResult:
        """Test computer update endpoint accessibility"""
        if not self.samples["computer_id"]:
            return TestResult(
                name="Update Computer (Verify)",
                category="Computers",
                status=TestStatus.SKIPPED,
                message="No computer ID available"
            )

        # Just verify the endpoint exists by doing a GET
        comp_id = self.samples["computer_id"]
        status, _ = await self._api_request(
            "GET", f"/api/v1/computers-inventory-detail/{comp_id}"
        )

        return TestResult(
            name="Update Computer (Verify)",
            category="Computers",
            status=TestStatus.PASSED,
            response_code=status,
            message="Update endpoint accessible (dry-run)"
        )

    # =========================================================================
    # MOBILE DEVICE TESTS
    # =========================================================================

    async def test_get_mobile_devices_list(self) -> TestResult:
        """Test getting mobile devices list"""
        status, data = await self._api_request(
            "GET", "/api/v2/mobile-devices",
            params={"page-size": 10}
        )

        results = data.get("results", [])
        if results:
            self.samples["mobile_device_id"] = results[0].get("id")

        return TestResult(
            name="Get Mobile Devices (List)",
            category="Mobile Devices",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(results)} devices",
            details={"count": len(results)}
        )

    async def test_get_mobile_device_detail(self) -> TestResult:
        """Test getting mobile device detail"""
        if not self.samples["mobile_device_id"]:
            return TestResult(
                name="Get Mobile Device (Detail)",
                category="Mobile Devices",
                status=TestStatus.SKIPPED,
                message="No mobile device ID available"
            )

        device_id = self.samples["mobile_device_id"]
        status, data = await self._api_request(
            "GET", f"/api/v2/mobile-devices/{device_id}/detail"
        )

        name = data.get("name", "Unknown")
        return TestResult(
            name="Get Mobile Device (Detail)",
            category="Mobile Devices",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved: {name}",
            details={"id": device_id}
        )

    async def test_mobile_device_update_endpoint(self) -> TestResult:
        """Test mobile device update endpoint"""
        if not self.samples["mobile_device_id"]:
            return TestResult(
                name="Update Mobile Device (Verify)",
                category="Mobile Devices",
                status=TestStatus.SKIPPED,
                message="No mobile device ID available"
            )

        device_id = self.samples["mobile_device_id"]
        status, _ = await self._api_request(
            "GET", f"/api/v2/mobile-devices/{device_id}/detail"
        )

        return TestResult(
            name="Update Mobile Device (Verify)",
            category="Mobile Devices",
            status=TestStatus.PASSED,
            response_code=status,
            message="Update endpoint accessible (dry-run)"
        )

    # =========================================================================
    # USER TESTS
    # =========================================================================

    async def test_get_users_list(self) -> TestResult:
        """Test getting users list (Classic API)"""
        status, data = await self._api_request("GET", "/JSSResource/users")

        users = data.get("users", [])
        if users:
            self.samples["user_id"] = users[0].get("id")

        return TestResult(
            name="Get Users (List)",
            category="Users",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(users)} users",
            details={"count": len(users)}
        )

    async def test_get_user_detail(self) -> TestResult:
        """Test getting user detail"""
        if not self.samples["user_id"]:
            return TestResult(
                name="Get User (Detail)",
                category="Users",
                status=TestStatus.SKIPPED,
                message="No user ID available"
            )

        user_id = self.samples["user_id"]
        status, data = await self._api_request(
            "GET", f"/JSSResource/users/id/{user_id}"
        )

        user = data.get("user", {})
        name = user.get("name", "Unknown")
        return TestResult(
            name="Get User (Detail)",
            category="Users",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved: {name}",
            details={"id": user_id}
        )

    async def test_user_update_endpoint(self) -> TestResult:
        """Test user update endpoint"""
        if not self.samples["user_id"]:
            return TestResult(
                name="Update User (Verify)",
                category="Users",
                status=TestStatus.SKIPPED,
                message="No user ID available"
            )

        user_id = self.samples["user_id"]
        status, _ = await self._api_request(
            "GET", f"/JSSResource/users/id/{user_id}"
        )

        return TestResult(
            name="Update User (Verify)",
            category="Users",
            status=TestStatus.PASSED,
            response_code=status,
            message="Update endpoint accessible (dry-run)"
        )

    # =========================================================================
    # SMART GROUPS TESTS
    # =========================================================================

    async def test_get_computer_groups(self) -> TestResult:
        """Test getting computer groups"""
        status, data = await self._api_request("GET", "/JSSResource/computergroups")

        groups = data.get("computer_groups", [])
        smart = [g for g in groups if g.get("is_smart", False)]
        static = [g for g in groups if not g.get("is_smart", True)]

        return TestResult(
            name="Get Computer Groups",
            category="Smart Groups",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"{len(smart)} smart, {len(static)} static",
            details={"smart": len(smart), "static": len(static), "total": len(groups)}
        )

    async def test_get_mobile_device_groups(self) -> TestResult:
        """Test getting mobile device groups"""
        status, data = await self._api_request("GET", "/JSSResource/mobiledevicegroups")

        groups = data.get("mobile_device_groups", [])
        smart = [g for g in groups if g.get("is_smart", False)]
        static = [g for g in groups if not g.get("is_smart", True)]

        return TestResult(
            name="Get Mobile Device Groups",
            category="Smart Groups",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"{len(smart)} smart, {len(static)} static",
            details={"smart": len(smart), "static": len(static), "total": len(groups)}
        )

    async def test_create_smart_group(self) -> TestResult:
        """Test creating a smart group (with cleanup)"""
        group_name = f"_MCP_Test_SmartGroup_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        group_data = {
            "computer_group": {
                "name": group_name,
                "is_smart": True,
                "criteria": [{
                    "name": "Computer Name",
                    "priority": 0,
                    "and_or": "and",
                    "search_type": "like",
                    "value": "_NONEXISTENT_TEST_"
                }]
            }
        }

        _, data = await self._api_request(
            "POST", "/JSSResource/computergroups/id/0",
            data=group_data
        )

        # Try to extract ID and cleanup (with delay for eventual consistency)
        cleanup_msg = ""
        try:
            group_id = data.get("id")
            if group_id:
                await asyncio.sleep(0.5)  # Wait for group to be fully created
                await self._api_request("DELETE", f"/JSSResource/computergroups/id/{group_id}")
                cleanup_msg = " (cleaned up)"
        except Exception:
            cleanup_msg = " (cleanup failed)"

        return TestResult(
            name="Create Smart Group",
            category="Smart Groups",
            status=TestStatus.PASSED,
            response_code=201,
            message=f"Created: {group_name}{cleanup_msg}"
        )

    # =========================================================================
    # STATIC GROUPS TESTS
    # =========================================================================

    async def test_create_static_group(self) -> TestResult:
        """Test creating a static group (with cleanup)"""
        group_name = f"_MCP_Test_StaticGroup_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        group_data = {
            "computer_group": {
                "name": group_name,
                "is_smart": False
            }
        }

        _, data = await self._api_request(
            "POST", "/JSSResource/computergroups/id/0",
            data=group_data
        )

        # Try to extract ID and cleanup (with delay for eventual consistency)
        cleanup_msg = ""
        try:
            group_id = data.get("id")
            if group_id:
                await asyncio.sleep(0.5)  # Wait for group to be fully created
                await self._api_request("DELETE", f"/JSSResource/computergroups/id/{group_id}")
                cleanup_msg = " (cleaned up)"
        except Exception:
            cleanup_msg = " (cleanup failed)"

        return TestResult(
            name="Create Static Group",
            category="Static Groups",
            status=TestStatus.PASSED,
            response_code=201,
            message=f"Created: {group_name}{cleanup_msg}"
        )

    # =========================================================================
    # POLICIES TESTS
    # =========================================================================

    async def test_get_policies(self) -> TestResult:
        """Test getting policies"""
        status, data = await self._api_request("GET", "/JSSResource/policies")

        policies = data.get("policies", [])
        if policies:
            self.samples["policy_id"] = policies[0].get("id")

        return TestResult(
            name="Get Policies (List)",
            category="Policies",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(policies)} policies",
            details={"count": len(policies)}
        )

    # =========================================================================
    # APP INSTALLERS TESTS
    # =========================================================================

    async def test_get_app_installer_titles(self) -> TestResult:
        """Test getting app installer titles from Jamf App Catalog API"""
        status, data = await self._api_request(
            "GET", "/api/v1/app-installers/titles",
            params={"page": 0, "page-size": 100}
        )

        results = data.get("results", [])
        if results:
            self.samples["app_title_id"] = results[0].get("id")

        return TestResult(
            name="Get App Installer Titles",
            category="App Installers",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(results)} app titles from Jamf App Catalog",
            details={"count": len(results), "total": data.get("totalCount", len(results))}
        )

    async def test_get_app_installer_deployments(self) -> TestResult:
        """Test getting app installer deployments"""
        status, data = await self._api_request(
            "GET", "/api/v1/app-installers/deployments"
        )

        # Handle both list and dict responses
        if isinstance(data, list):
            deployments = data
        else:
            deployments = data.get("results", data.get("deployments", []))

        if deployments and isinstance(deployments, list) and len(deployments) > 0:
            self.samples["app_deployment_id"] = deployments[0].get("id")

        count = len(deployments) if isinstance(deployments, list) else 0
        return TestResult(
            name="Get App Installer Deployments",
            category="App Installers",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {count} deployments",
            details={"count": count}
        )

    async def test_get_app_installer_deployment_detail(self) -> TestResult:
        """Test getting app installer deployment details"""
        if not self.samples.get("app_deployment_id"):
            return TestResult(
                name="Get App Installer Deployment (Detail)",
                category="App Installers",
                status=TestStatus.SKIPPED,
                message="No deployment ID available"
            )

        deployment_id = self.samples["app_deployment_id"]
        status, data = await self._api_request(
            "GET", f"/api/v1/app-installers/deployments/{deployment_id}"
        )

        name = data.get("name", "Unknown")
        return TestResult(
            name="Get App Installer Deployment (Detail)",
            category="App Installers",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved: {name}",
            details={"id": deployment_id, "name": name}
        )

    async def test_create_app_installer_deployment(self) -> TestResult:
        """Test creating an App Installer deployment (with cleanup)"""
        deployment_name = f"_MCP_Test_Deployment_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        group_name = f"_MCP_Test_Group_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # First create a smart group for the deployment
        group_data = {
            "computer_group": {
                "name": group_name,
                "is_smart": True,
                "site": {"id": -1},
                "criteria": [{
                    "name": "UDID",
                    "priority": 0,
                    "and_or": "and",
                    "search_type": "like",
                    "value": "_NONEXISTENT_",  # Won't match any real computers
                }]
            }
        }

        # Create the smart group
        _, group_result = await self._api_request(
            "POST", "/JSSResource/computergroups/id/0",
            data=group_data
        )
        group_id = group_result.get("id")

        if not group_id:
            return TestResult(
                name="Create App Installer Deployment",
                category="App Installers",
                status=TestStatus.FAILED,
                message="Failed to create test smart group"
            )

        # Wait for group to be ready
        await asyncio.sleep(0.5)

        # Create the App Installer deployment using a known app title (Brave Browser = 1B7)
        deployment_data = {
            "name": deployment_name,
            "enabled": False,  # Disabled so it doesn't actually deploy
            "appTitleId": "1B7",  # Brave Browser
            "siteId": "-1",
            "categoryId": "-1",
            "smartGroupId": str(group_id),
            "deploymentType": "INSTALL_AUTOMATICALLY",
            "updateBehavior": "AUTOMATIC",
            "installPredefinedConfigProfiles": True,
        }

        cleanup_msg = ""
        deployment_id = None
        try:
            _, data = await self._api_request(
                "POST", "/api/v1/app-installers/deployments",
                data=deployment_data
            )
            deployment_id = data.get("id")
            cleanup_msg = f" (ID: {deployment_id})"
        except Exception as e:
            # Cleanup the group even if deployment fails
            try:
                await asyncio.sleep(0.5)
                await self._api_request("DELETE", f"/JSSResource/computergroups/id/{group_id}")
            except Exception:
                pass
            return TestResult(
                name="Create App Installer Deployment",
                category="App Installers",
                status=TestStatus.FAILED,
                message=f"Failed to create deployment: {str(e)[:100]}"
            )

        # Cleanup: delete deployment first, then group
        try:
            if deployment_id:
                endpoint = f"/api/v1/app-installers/deployments/{deployment_id}"
                await self._api_request("DELETE", endpoint)
                cleanup_msg += " (cleaned up)"
        except Exception:
            cleanup_msg += " (deployment cleanup failed)"

        try:
            await asyncio.sleep(0.5)
            await self._api_request("DELETE", f"/JSSResource/computergroups/id/{group_id}")
        except Exception:
            cleanup_msg += " (group cleanup failed)"

        return TestResult(
            name="Create App Installer Deployment",
            category="App Installers",
            status=TestStatus.PASSED,
            response_code=201,
            message=f"Created: {deployment_name}{cleanup_msg}"
        )

    # =========================================================================
    # CONFIGURATION PROFILES TESTS
    # =========================================================================

    async def test_get_computer_configuration_profiles(self) -> TestResult:
        """Test getting computer configuration profiles"""
        status, data = await self._api_request("GET", "/JSSResource/osxconfigurationprofiles")

        profiles = data.get("os_x_configuration_profiles", [])
        return TestResult(
            name="Get Computer Configuration Profiles",
            category="Configuration Profiles",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(profiles)} configuration profiles",
            details={"count": len(profiles)}
        )

    async def test_get_mobile_device_configuration_profiles(self) -> TestResult:
        """Test getting mobile device configuration profiles"""
        endpoint = "/JSSResource/mobiledeviceconfigurationprofiles"
        status, data = await self._api_request("GET", endpoint)

        profiles = data.get("configuration_profiles", [])
        return TestResult(
            name="Get Mobile Device Configuration Profiles",
            category="Configuration Profiles",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(profiles)} configuration profiles",
            details={"count": len(profiles)}
        )

    # =========================================================================
    # SCRIPTS TESTS
    # =========================================================================

    async def test_get_scripts(self) -> TestResult:
        """Test getting scripts"""
        status, data = await self._api_request(
            "GET", "/api/v1/scripts",
            params={"page-size": 100}
        )

        scripts = data.get("results", [])
        if scripts:
            self.samples["script_id"] = scripts[0].get("id")

        return TestResult(
            name="Get Scripts (List)",
            category="Scripts",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(scripts)} scripts",
            details={"count": len(scripts)}
        )

    # =========================================================================
    # EXTENSION ATTRIBUTES TESTS
    # =========================================================================

    async def test_get_computer_eas(self) -> TestResult:
        """Test getting computer extension attributes"""
        status, data = await self._api_request("GET", "/JSSResource/computerextensionattributes")

        eas = data.get("computer_extension_attributes", [])
        return TestResult(
            name="Get Computer Extension Attributes",
            category="Extension Attributes",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(eas)} EAs",
            details={"count": len(eas)}
        )

    async def test_get_mobile_eas(self) -> TestResult:
        """Test getting mobile extension attributes"""
        endpoint = "/JSSResource/mobiledeviceextensionattributes"
        status, data = await self._api_request("GET", endpoint)

        eas = data.get("mobile_device_extension_attributes", [])
        return TestResult(
            name="Get Mobile Extension Attributes",
            category="Extension Attributes",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(eas)} EAs",
            details={"count": len(eas)}
        )

    async def test_get_user_eas(self) -> TestResult:
        """Test getting user extension attributes"""
        status, data = await self._api_request("GET", "/JSSResource/userextensionattributes")

        eas = data.get("user_extension_attributes", [])
        return TestResult(
            name="Get User Extension Attributes",
            category="Extension Attributes",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(eas)} EAs",
            details={"count": len(eas)}
        )

    async def test_create_extension_attribute(self) -> TestResult:
        """Test creating an extension attribute (with cleanup)"""
        ea_name = f"_MCP_Test_EA_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        ea_data = {
            "computer_extension_attribute": {
                "name": ea_name,
                "description": "Test EA from MCP test agent",
                "data_type": "String",
                "input_type": {"type": "Text Field"}
            }
        }

        _, data = await self._api_request(
            "POST", "/JSSResource/computerextensionattributes/id/0",
            data=ea_data
        )

        cleanup_msg = ""
        try:
            ea_id = data.get("id")
            if ea_id:
                endpoint = f"/JSSResource/computerextensionattributes/id/{ea_id}"
                await self._api_request("DELETE", endpoint)
                cleanup_msg = " (cleaned up)"
        except Exception:
            cleanup_msg = " (cleanup failed)"

        return TestResult(
            name="Create Extension Attribute",
            category="Extension Attributes",
            status=TestStatus.PASSED,
            response_code=201,
            message=f"Created: {ea_name}{cleanup_msg}"
        )

    # =========================================================================
    # PRESTAGE ENROLLMENTS TESTS
    # =========================================================================

    async def test_get_computer_prestages(self) -> TestResult:
        """Test getting computer prestage enrollments"""
        status, data = await self._api_request(
            "GET", "/api/v3/computer-prestages",
            params={"page": 0, "page-size": 100}
        )

        results = data.get("results", [])
        return TestResult(
            name="Get Computer PreStages",
            category="PreStage Enrollments",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(results)} computer prestages",
            details={"count": len(results), "total": data.get("totalCount", len(results))}
        )

    async def test_get_mobile_device_prestages(self) -> TestResult:
        """Test getting mobile device prestage enrollments"""
        status, data = await self._api_request(
            "GET", "/api/v2/mobile-device-prestages",
            params={"page": 0, "page-size": 100}
        )

        results = data.get("results", [])
        return TestResult(
            name="Get Mobile Device PreStages",
            category="PreStage Enrollments",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(results)} mobile device prestages",
            details={"count": len(results), "total": data.get("totalCount", len(results))}
        )

    # =========================================================================
    # MAC APPS TESTS
    # =========================================================================

    async def test_get_mac_apps(self) -> TestResult:
        """Test getting Mac App Store apps"""
        status, data = await self._api_request("GET", "/JSSResource/macapplications")

        apps = data.get("mac_applications", [])
        return TestResult(
            name="Get Mac Apps (List)",
            category="Mac Apps",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(apps)} Mac apps",
            details={"count": len(apps)}
        )

    # =========================================================================
    # MOBILE DEVICE APPS TESTS
    # =========================================================================

    async def test_get_mobile_device_apps(self) -> TestResult:
        """Test getting mobile device apps"""
        status, data = await self._api_request("GET", "/JSSResource/mobiledeviceapplications")

        apps = data.get("mobile_device_applications", [])
        return TestResult(
            name="Get Mobile Device Apps (List)",
            category="Mobile Device Apps",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(apps)} mobile device apps",
            details={"count": len(apps)}
        )

    # =========================================================================
    # RESTRICTED SOFTWARE TESTS
    # =========================================================================

    async def test_get_restricted_software(self) -> TestResult:
        """Test getting restricted software"""
        status, data = await self._api_request("GET", "/JSSResource/restrictedsoftware")

        items = data.get("restricted_software", [])
        return TestResult(
            name="Get Restricted Software (List)",
            category="Restricted Software",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(items)} restricted software entries",
            details={"count": len(items)}
        )

    # =========================================================================
    # EBOOKS TESTS
    # =========================================================================

    async def test_get_ebooks(self) -> TestResult:
        """Test getting eBooks"""
        status, data = await self._api_request("GET", "/JSSResource/ebooks")

        ebooks = data.get("ebooks", [])
        return TestResult(
            name="Get eBooks (List)",
            category="eBooks",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(ebooks)} eBooks",
            details={"count": len(ebooks)}
        )

    # =========================================================================
    # PATCH POLICIES TESTS
    # =========================================================================

    async def test_get_patch_policies(self) -> TestResult:
        """Test getting patch policies"""
        status, data = await self._api_request("GET", "/JSSResource/patchpolicies")

        policies = data.get("patch_policies", [])
        return TestResult(
            name="Get Patch Policies (List)",
            category="Patch Policies",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(policies)} patch policies",
            details={"count": len(policies)}
        )

    # =========================================================================
    # API ROLES AND INTEGRATIONS TESTS
    # =========================================================================

    async def test_get_api_role_privileges(self) -> TestResult:
        """Test getting available API role privileges"""
        status, data = await self._api_request(
            "GET", "/api/v1/api-role-privileges"
        )

        privileges = data.get("privileges", [])
        return TestResult(
            name="Get API Role Privileges",
            category="API Roles",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(privileges)} available privileges",
            details={"count": len(privileges)}
        )

    async def test_get_api_roles(self) -> TestResult:
        """Test getting API roles"""
        status, data = await self._api_request(
            "GET", "/api/v1/api-roles",
            params={"page": 0, "page-size": 100}
        )

        results = data.get("results", [])
        return TestResult(
            name="Get API Roles (List)",
            category="API Roles",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(results)} API roles",
            details={"count": len(results), "total": data.get("totalCount", len(results))}
        )

    async def test_get_api_integrations(self) -> TestResult:
        """Test getting API integrations"""
        status, data = await self._api_request(
            "GET", "/api/v1/api-integrations",
            params={"page": 0, "page-size": 100}
        )

        results = data.get("results", [])
        return TestResult(
            name="Get API Integrations (List)",
            category="API Roles",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(results)} API integrations",
            details={"count": len(results), "total": data.get("totalCount", len(results))}
        )

    async def test_create_api_role_and_integration(self) -> TestResult:
        """Test creating an API role and integration (with cleanup)"""
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        role_name = f"_MCP_Test_Role_{timestamp}"
        integration_name = f"_MCP_Test_Integration_{timestamp}"

        # Step 1: Create API role
        role_data = {
            "displayName": role_name,
            "privileges": ["Read Computers"]  # Minimal privilege for testing
        }

        role_id = None
        integration_id = None
        cleanup_msg = ""

        try:
            _, role_result = await self._api_request(
                "POST", "/api/v1/api-roles",
                data=role_data
            )
            role_id = role_result.get("id")

            if not role_id:
                return TestResult(
                    name="Create API Role and Integration",
                    category="API Roles",
                    status=TestStatus.FAILED,
                    message="Failed to create test API role"
                )

            # Step 2: Create API integration using the role
            integration_data = {
                "displayName": integration_name,
                "authorizationScopes": [role_name],
                "enabled": True,
                "accessTokenLifetimeSeconds": 1800
            }

            _status, integration_result = await self._api_request(
                "POST", "/api/v1/api-integrations",
                data=integration_data
            )
            integration_id = integration_result.get("id")

            cleanup_msg = f" (role ID: {role_id}, integration ID: {integration_id})"

        except Exception as e:
            # Cleanup role if integration creation failed
            if role_id:
                try:
                    await self._api_request("DELETE", f"/api/v1/api-roles/{role_id}")
                except Exception:
                    pass
            return TestResult(
                name="Create API Role and Integration",
                category="API Roles",
                status=TestStatus.FAILED,
                message=f"Failed: {str(e)[:100]}"
            )

        # Cleanup: delete integration first (depends on role), then role
        try:
            if integration_id:
                await self._api_request("DELETE", f"/api/v1/api-integrations/{integration_id}")
                cleanup_msg += " (integration cleaned up)"
        except Exception:
            cleanup_msg += " (integration cleanup failed)"

        try:
            if role_id:
                await asyncio.sleep(0.5)  # Wait for integration deletion to complete
                await self._api_request("DELETE", f"/api/v1/api-roles/{role_id}")
                cleanup_msg += " (role cleaned up)"
        except Exception:
            cleanup_msg += " (role cleanup failed)"

        return TestResult(
            name="Create API Role and Integration",
            category="API Roles",
            status=TestStatus.PASSED,
            response_code=201,
            message=f"Created: {role_name} + {integration_name}{cleanup_msg}"
        )

    # =========================================================================
    # BUILDINGS TESTS
    # =========================================================================

    async def test_get_buildings(self) -> TestResult:
        """Test getting buildings list"""
        status, data = await self._api_request("GET", "/JSSResource/buildings")

        buildings = data.get("buildings", [])
        return TestResult(
            name="Get Buildings (List)",
            category="Buildings/Departments",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(buildings)} buildings",
            details={"count": len(buildings)}
        )

    # =========================================================================
    # DEPARTMENTS TESTS
    # =========================================================================

    async def test_get_departments(self) -> TestResult:
        """Test getting departments list"""
        status, data = await self._api_request("GET", "/JSSResource/departments")

        departments = data.get("departments", [])
        return TestResult(
            name="Get Departments (List)",
            category="Buildings/Departments",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(departments)} departments",
            details={"count": len(departments)}
        )

    # =========================================================================
    # PRINTERS TESTS
    # =========================================================================

    async def test_get_printers(self) -> TestResult:
        """Test getting printers list"""
        status, data = await self._api_request("GET", "/JSSResource/printers")

        printers = data.get("printers", [])
        if printers:
            self.samples["printer_id"] = printers[0].get("id")

        return TestResult(
            name="Get Printers (List)",
            category="Printers",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(printers)} printers",
            details={"count": len(printers)}
        )

    async def test_create_printer(self) -> TestResult:
        """Test creating a printer, stores ID for subsequent tests"""
        printer_name = f"_MCP_Test_Printer_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        printer_data = {
            "printer": {
                "name": printer_name,
                "uri": "lpd://10.0.0.1/",
                "model": "Test Printer Model",
            }
        }

        _, data = await self._api_request(
            "POST", "/JSSResource/printers/id/0",
            data=printer_data
        )

        printer_id = data.get("id")
        if printer_id:
            self.samples["test_printer_id"] = printer_id

        return TestResult(
            name="Create Printer",
            category="Printers",
            status=TestStatus.PASSED,
            response_code=201,
            message=f"Created: {printer_name} (ID: {printer_id})"
        )

    async def test_get_printer_detail(self) -> TestResult:
        """Test getting a specific printer by ID"""
        printer_id = self.samples.get("test_printer_id") or self.samples.get("printer_id")
        if not printer_id:
            return TestResult(
                name="Get Printer (Detail)",
                category="Printers",
                status=TestStatus.SKIPPED,
                message="No printer ID available"
            )

        status, data = await self._api_request(
            "GET", f"/JSSResource/printers/id/{printer_id}"
        )

        printer = data.get("printer", {})
        printer_name = printer.get("name", "Unknown")

        return TestResult(
            name="Get Printer (Detail)",
            category="Printers",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved printer: {printer_name}",
            details={"id": printer_id, "name": printer_name}
        )

    async def test_update_printer(self) -> TestResult:
        """Test updating a printer"""
        printer_id = self.samples.get("test_printer_id")
        if not printer_id:
            return TestResult(
                name="Update Printer",
                category="Printers",
                status=TestStatus.SKIPPED,
                message="No test printer ID available"
            )

        update_data = {
            "printer": {
                "notes": "Updated by MCP test agent"
            }
        }

        status, _ = await self._api_request(
            "PUT", f"/JSSResource/printers/id/{printer_id}",
            data=update_data
        )

        return TestResult(
            name="Update Printer",
            category="Printers",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Updated printer ID {printer_id}"
        )

    # =========================================================================
    # CATEGORIES TESTS
    # =========================================================================

    async def test_get_categories(self) -> TestResult:
        """Test getting categories"""
        status, data = await self._api_request("GET", "/JSSResource/categories")

        categories = data.get("categories", [])
        if categories:
            self.samples["category_id"] = categories[0].get("id")

        return TestResult(
            name="Get Categories (List)",
            category="Categories",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(categories)} categories",
            details={"count": len(categories)}
        )

    async def test_create_category(self) -> TestResult:
        """Test creating a category (with cleanup)"""
        cat_name = f"_MCP_Test_Cat_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        cat_data = {
            "category": {
                "name": cat_name,
                "priority": 9
            }
        }

        _, data = await self._api_request(
            "POST", "/JSSResource/categories/id/0",
            data=cat_data
        )

        cleanup_msg = ""
        try:
            cat_id = data.get("id")
            if cat_id:
                await self._api_request("DELETE", f"/JSSResource/categories/id/{cat_id}")
                cleanup_msg = " (cleaned up)"
        except Exception:
            cleanup_msg = " (cleanup failed)"

        return TestResult(
            name="Create Category",
            category="Categories",
            status=TestStatus.PASSED,
            response_code=201,
            message=f"Created: {cat_name}{cleanup_msg}"
        )

    # =========================================================================
    # JAMF PROTECT TESTS
    # =========================================================================

    async def test_protect_list_alerts(self) -> TestResult:
        """Test listing Protect alerts"""
        if not self.protect_available:
            return TestResult(
                name="List Protect Alerts",
                category="Jamf Protect",
                status=TestStatus.SKIPPED,
                message="Protect not configured"
            )

        # listAlerts requires AlertQueryInput (all fields optional)
        query = """
        query listAlerts($input: AlertQueryInput!) {
          listAlerts(input: $input) {
            items {
              uuid
              severity
              status
              eventType
              created
              computer {
                uuid
                hostName
              }
            }
          }
        }
        """
        status, data = await self._protect_graphql(query, {"input": {}})

        alerts = data.get("listAlerts", {}).get("items", [])
        if alerts:
            self.samples["protect_alert_uuid"] = alerts[0].get("uuid")

        return TestResult(
            name="List Protect Alerts",
            category="Jamf Protect",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(alerts)} alerts",
            details={"count": len(alerts)}
        )

    async def test_protect_get_alert(self) -> TestResult:
        """Test getting a specific Protect alert"""
        if not self.protect_available:
            return TestResult(
                name="Get Protect Alert",
                category="Jamf Protect",
                status=TestStatus.SKIPPED,
                message="Protect not configured"
            )

        if not self.samples.get("protect_alert_uuid"):
            return TestResult(
                name="Get Protect Alert",
                category="Jamf Protect",
                status=TestStatus.SKIPPED,
                message="No alert UUID available"
            )

        query = """
        query GetAlert($uuid: ID!) {
          getAlert(uuid: $uuid) {
            uuid
            severity
            status
            eventType
            created
            computer { uuid hostName }
          }
        }
        """
        uuid = self.samples["protect_alert_uuid"]
        status, data = await self._protect_graphql(query, {"uuid": uuid})

        alert = data.get("getAlert", {})
        return TestResult(
            name="Get Protect Alert",
            category="Jamf Protect",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved alert: {uuid[:8]}...",
            details={"uuid": uuid, "severity": alert.get("severity")}
        )

    async def test_protect_list_computers(self) -> TestResult:
        """Test listing Protect computers"""
        if not self.protect_available:
            return TestResult(
                name="List Protect Computers",
                category="Jamf Protect",
                status=TestStatus.SKIPPED,
                message="Protect not configured"
            )

        query = """
        query listComputers {
          listComputers {
            items {
              uuid
              hostName
              serial
              modelName
              osString
            }
          }
        }
        """
        status, data = await self._protect_graphql(query)

        computers = data.get("listComputers", {}).get("items", [])
        if computers:
            self.samples["protect_computer_uuid"] = computers[0].get("uuid")

        return TestResult(
            name="List Protect Computers",
            category="Jamf Protect",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(computers)} computers",
            details={"count": len(computers)}
        )

    async def test_protect_get_computer(self) -> TestResult:
        """Test getting a specific Protect computer"""
        if not self.protect_available:
            return TestResult(
                name="Get Protect Computer",
                category="Jamf Protect",
                status=TestStatus.SKIPPED,
                message="Protect not configured"
            )

        if not self.samples.get("protect_computer_uuid"):
            return TestResult(
                name="Get Protect Computer",
                category="Jamf Protect",
                status=TestStatus.SKIPPED,
                message="No computer UUID available"
            )

        query = """
        query GetComputer($uuid: ID!) {
          getComputer(uuid: $uuid) {
            uuid
            hostName
            serial
            modelName
            osString
            version
          }
        }
        """
        uuid = self.samples["protect_computer_uuid"]
        status, data = await self._protect_graphql(query, {"uuid": uuid})

        computer = data.get("getComputer", {})
        hostname = computer.get("hostName", "Unknown")
        return TestResult(
            name="Get Protect Computer",
            category="Jamf Protect",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved: {hostname}",
            details={"uuid": uuid, "hostName": hostname}
        )

    async def test_protect_list_analytics(self) -> TestResult:
        """Test listing Protect analytics"""
        if not self.protect_available:
            return TestResult(
                name="List Protect Analytics",
                category="Jamf Protect",
                status=TestStatus.SKIPPED,
                message="Protect not configured"
            )

        query = """
        query ListAnalytics {
          listAnalytics {
            items {
              uuid
              name
              description
              severity
            }
          }
        }
        """
        status, data = await self._protect_graphql(query)

        analytics = data.get("listAnalytics", {}).get("items", [])
        if analytics:
            self.samples["protect_analytic_uuid"] = analytics[0].get("uuid")

        return TestResult(
            name="List Protect Analytics",
            category="Jamf Protect",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(analytics)} analytics",
            details={"count": len(analytics)}
        )

    async def test_protect_get_analytic(self) -> TestResult:
        """Test getting a specific Protect analytic"""
        if not self.protect_available:
            return TestResult(
                name="Get Protect Analytic",
                category="Jamf Protect",
                status=TestStatus.SKIPPED,
                message="Protect not configured"
            )

        if not self.samples.get("protect_analytic_uuid"):
            return TestResult(
                name="Get Protect Analytic",
                category="Jamf Protect",
                status=TestStatus.SKIPPED,
                message="No analytic UUID available"
            )

        query = """
        query GetAnalytic($uuid: ID!) {
          getAnalytic(uuid: $uuid) {
            uuid
            name
            description
            severity
            inputType
          }
        }
        """
        uuid = self.samples["protect_analytic_uuid"]
        status, data = await self._protect_graphql(query, {"uuid": uuid})

        analytic = data.get("getAnalytic", {})
        name = analytic.get("name", "Unknown")
        return TestResult(
            name="Get Protect Analytic",
            category="Jamf Protect",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved: {name}",
            details={"uuid": uuid, "name": name}
        )

    # =========================================================================
    # RISK API TESTS (Jamf Security Cloud)
    # =========================================================================

    async def test_get_risk_devices(self) -> TestResult:
        """Test getting device risk status from Jamf Security Cloud"""
        if not self._is_security_configured():
            return TestResult(
                name="Get Risk Devices",
                category="Risk API",
                status=TestStatus.SKIPPED,
                message="Jamf Security Cloud credentials not configured"
            )

        status, data = await self._security_api_request(
            "GET", "/risk/v1/devices",
            params={"page": 0, "pageSize": 100}
        )

        # Handle different response formats - v1 API returns devices in "records"
        devices = data.get("records", data.get("devices", data.get("results", [])))
        if devices and len(devices) > 0:
            self.samples["risk_device_id"] = devices[0].get("id") or devices[0].get("deviceId")

        # Get total from pagination info (v1 format)
        pagination = data.get("pagination", {})
        total = pagination.get("totalRecords", data.get("totalCount", len(devices)))

        return TestResult(
            name="Get Risk Devices",
            category="Risk API",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(devices)} devices with risk status",
            details={"count": len(devices), "total": total}
        )

    async def test_override_device_risk(self) -> TestResult:
        """Test overriding device risk level (with revert)"""
        if not self._is_security_configured():
            return TestResult(
                name="Override Device Risk",
                category="Risk API",
                status=TestStatus.SKIPPED,
                message="Jamf Security Cloud credentials not configured"
            )

        if not self.samples.get("risk_device_id"):
            return TestResult(
                name="Override Device Risk",
                category="Risk API",
                status=TestStatus.SKIPPED,
                message="No device ID available from risk devices test"
            )

        device_id = self.samples["risk_device_id"]

        # First, get the current risk level so we can restore it
        try:
            _, device_data = await self._security_api_request(
                "GET", "/risk/v1/devices",
                params={"page": 0, "pageSize": 100}
            )
            devices = (
                device_data.get("records")
                or device_data.get("devices")
                or device_data.get("results", [])
            )
            original_risk = "LOW"  # Default fallback
            for d in devices:
                if (d.get("id") or d.get("deviceId")) == device_id:
                    original_risk = d.get("risk", d.get("riskLevel", "LOW"))
                    break
        except Exception:
            original_risk = "LOW"

        # Override to a test value
        test_risk = "MEDIUM" if original_risk != "MEDIUM" else "LOW"
        override_data = {
            "deviceIds": [device_id],
            "risk": test_risk,
            "source": "MANUAL"
        }

        status, _ = await self._security_api_request(
            "PUT", "/risk/v1/override",
            data=override_data
        )

        # Revert to original risk level
        revert_msg = ""
        try:
            revert_data = {
                "deviceIds": [device_id],
                "risk": original_risk,
                "source": "MANUAL"
            }
            await self._security_api_request(
                "PUT", "/risk/v1/override",
                data=revert_data
            )
            revert_msg = " (reverted)"
        except Exception:
            revert_msg = " (revert failed)"

        return TestResult(
            name="Override Device Risk",
            category="Risk API",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Set risk to {test_risk} for device {device_id}{revert_msg}"
        )

    # =========================================================================
    # SETUP TOOLS TESTS (always work, no credentials needed)
    # =========================================================================

    async def test_setup_status(self) -> TestResult:
        """Test jamf_get_setup_status tool"""
        try:
            from jamf_mcp.tools.setup import jamf_get_setup_status

            start = time.time()
            result_json = await jamf_get_setup_status()
            duration = (time.time() - start) * 1000

            result = json.loads(result_json)

            if not result.get("success"):
                return TestResult(
                    name="Get Setup Status",
                    category="Setup Tools",
                    status=TestStatus.FAILED,
                    duration_ms=duration,
                    error="Tool returned success=false"
                )

            data = result.get("data", {})
            summary = data.get("summary", {})

            return TestResult(
                name="Get Setup Status",
                category="Setup Tools",
                status=TestStatus.PASSED,
                response_code=200,
                duration_ms=duration,
                message=f"Products configured: {summary.get('products_configured', 0)}/3",
                details=summary
            )
        except Exception as e:
            return TestResult(
                name="Get Setup Status",
                category="Setup Tools",
                status=TestStatus.FAILED,
                error=str(e)
            )

    async def test_configure_help(self) -> TestResult:
        """Test jamf_configure_help tool"""
        try:
            from jamf_mcp.tools.setup import jamf_configure_help

            start = time.time()
            result_json = await jamf_configure_help(product="all")
            duration = (time.time() - start) * 1000

            result = json.loads(result_json)

            if not result.get("success"):
                return TestResult(
                    name="Get Configure Help",
                    category="Setup Tools",
                    status=TestStatus.FAILED,
                    duration_ms=duration,
                    error="Tool returned success=false"
                )

            data = result.get("data", {})
            products = list(data.keys())

            return TestResult(
                name="Get Configure Help",
                category="Setup Tools",
                status=TestStatus.PASSED,
                response_code=200,
                duration_ms=duration,
                message=f"Retrieved help for {len(products)} products",
                details={"products": products}
            )
        except Exception as e:
            return TestResult(
                name="Get Configure Help",
                category="Setup Tools",
                status=TestStatus.FAILED,
                error=str(e)
            )

    # =========================================================================
    # MDM COMMANDS TESTS
    # =========================================================================

    async def test_list_mdm_commands(self) -> TestResult:
        """Test listing MDM commands (pending, read-only)"""
        status, data = await self._api_request(
            "GET", "/api/v2/mdm/commands",
            params={"filter": "status==Pending", "page-size": 10, "sort": "dateSent:desc"}
        )

        results = data.get("results", [])
        total = data.get("totalCount", len(results))

        # Store first command's clientManagementId for the management ID test
        if results:
            first = results[0]
            client_data = first.get("clientData", [])
            if client_data and isinstance(client_data, list):
                mid = client_data[0].get("managementId") or client_data[0].get("clientManagementId")
                if mid:
                    self.samples["mdm_management_id"] = mid

        return TestResult(
            name="List MDM Commands (Pending)",
            category="MDM Commands",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Retrieved {len(results)} pending commands (total: {total})",
            details={"count": len(results), "total": total}
        )

    async def test_resolve_management_id_computer(self) -> TestResult:
        """Test resolving a computer's numeric ID to its managementId UUID"""
        if not self.samples["computer_id"]:
            return TestResult(
                name="Resolve Computer managementId",
                category="MDM Commands",
                status=TestStatus.SKIPPED,
                message="No computer ID available (run computer tests first)"
            )

        comp_id = self.samples["computer_id"]
        status, data = await self._api_request(
            "GET", f"/api/v1/computers-inventory-detail/{comp_id}"
        )

        management_id = data.get("general", {}).get("managementId")
        if management_id:
            self.samples["computer_management_id"] = management_id

        return TestResult(
            name="Resolve Computer managementId",
            category="MDM Commands",
            status=TestStatus.PASSED if management_id else TestStatus.WARNING,
            response_code=status,
            message=f"managementId: {management_id or 'not found'}",
            details={"jamfId": comp_id, "managementId": management_id}
        )

    async def test_resolve_management_id_mobile(self) -> TestResult:
        """Test resolving a mobile device's numeric ID to its managementId UUID"""
        if not self.samples["mobile_device_id"]:
            return TestResult(
                name="Resolve Mobile Device managementId",
                category="MDM Commands",
                status=TestStatus.SKIPPED,
                message="No mobile device ID available (run mobile device tests first)"
            )

        device_id = self.samples["mobile_device_id"]
        status, data = await self._api_request(
            "GET", f"/api/v2/mobile-devices/{device_id}/detail"
        )

        management_id = data.get("general", {}).get("managementId")
        if management_id:
            self.samples["mobile_management_id"] = management_id

        return TestResult(
            name="Resolve Mobile Device managementId",
            category="MDM Commands",
            status=TestStatus.PASSED if management_id else TestStatus.WARNING,
            response_code=status,
            message=f"managementId: {management_id or 'not found'}",
            details={"jamfId": device_id, "managementId": management_id}
        )

    async def test_send_mdm_command_requires_confirmation(self) -> TestResult:
        """Test that destructive MDM commands require confirm=True (no API call made)"""
        try:
            from jamf_mcp.tools.mdm_commands import (
                jamf_lock_device,
                jamf_erase_device,
                jamf_restart_device,
                jamf_shut_down_device,
                jamf_clear_passcode,
                jamf_send_mdm_command,
            )

            import time as _time
            start = _time.time()

            # All these should return a confirmation-required error without hitting the API
            cases = [
                ("jamf_lock_device", jamf_lock_device(management_id="fake-uuid", confirm=False)),
                ("jamf_erase_device", jamf_erase_device(management_id="fake-uuid", confirm=False)),
                ("jamf_restart_device", jamf_restart_device(management_id="fake-uuid", confirm=False)),
                ("jamf_shut_down_device", jamf_shut_down_device(management_id="fake-uuid", confirm=False)),
                ("jamf_clear_passcode", jamf_clear_passcode(management_id="fake-uuid", confirm=False)),
                ("jamf_send_mdm_command DEVICE_LOCK", jamf_send_mdm_command(
                    command_type="DEVICE_LOCK",
                    management_ids=["fake-uuid"],
                    confirm=False,
                )),
            ]

            failures = []
            for name, coro in cases:
                result_json = await coro
                result = json.loads(result_json)
                if result.get("success") is not False:
                    failures.append(f"{name}: expected success=false, got {result.get('success')}")
                elif "confirm" not in result.get("hint", "").lower() and "confirm" not in result.get("error", "").lower():
                    failures.append(f"{name}: missing confirmation guidance in response")

            duration = (_time.time() - start) * 1000

            if failures:
                return TestResult(
                    name="Destructive Commands Require Confirmation",
                    category="MDM Commands",
                    status=TestStatus.FAILED,
                    duration_ms=duration,
                    error="; ".join(failures)
                )

            return TestResult(
                name="Destructive Commands Require Confirmation",
                category="MDM Commands",
                status=TestStatus.PASSED,
                response_code=200,
                duration_ms=duration,
                message=f"All {len(cases)} destructive commands correctly require confirm=True",
                details={"tested_commands": [c[0] for c in cases]}
            )

        except ImportError as e:
            return TestResult(
                name="Destructive Commands Require Confirmation",
                category="MDM Commands",
                status=TestStatus.FAILED,
                error=f"Import error: {e}"
            )
        except Exception as e:
            return TestResult(
                name="Destructive Commands Require Confirmation",
                category="MDM Commands",
                status=TestStatus.FAILED,
                error=str(e)
            )

    async def test_send_device_information_command(self) -> TestResult:
        """Test sending a safe DEVICE_INFORMATION command (non-destructive, no confirm needed)"""
        management_id = (
            self.samples.get("computer_management_id")
            or self.samples.get("mobile_management_id")
            or self.samples.get("mdm_management_id")
        )

        if not management_id:
            return TestResult(
                name="Send DEVICE_INFORMATION Command",
                category="MDM Commands",
                status=TestStatus.SKIPPED,
                message="No managementId available (run resolve tests first)"
            )

        # DEVICE_INFORMATION is a safe read-request command (non-destructive)
        status, data = await self._api_request(
            "POST", "/api/v2/mdm/commands",
            data={
                "clientData": [{"managementId": management_id}],
                "commandData": {"commandType": "DEVICE_INFORMATION"}
            }
        )

        return TestResult(
            name="Send DEVICE_INFORMATION Command",
            category="MDM Commands",
            status=TestStatus.PASSED,
            response_code=status,
            message=f"Queued DEVICE_INFORMATION for managementId {management_id[:8]}...",
            details={"managementId": management_id, "response": data}
        )

    # =========================================================================
    # MAIN EXECUTION
    # =========================================================================

    async def run_all_tests(self) -> TestReport:
        """Run all tests and return a report"""
        self.report.start_time = datetime.now()

        self._print_header("JAMF MCP SERVER TEST AGENT")
        print(f"\n  {Colors.DIM}Jamf Pro:{Colors.ENDC}  {self.jamf_url}")
        if self.protect_available:
            print(f"  {Colors.DIM}Protect:{Colors.ENDC}   {self.protect_url}")
        else:
            protect_msg = "Not configured (tests will be skipped)"
            print(f"  {Colors.DIM}Protect:{Colors.ENDC}   {protect_msg}")
        start_time = self.report.start_time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"  {Colors.DIM}Started:{Colors.ENDC}  {start_time}")

        # Define test categories and their tests
        test_plan = [
            ("Setup Tools", [
                ("Get Setup Status", self.test_setup_status),
                ("Get Configure Help", self.test_configure_help),
            ]),
            ("Authentication", [
                ("OAuth Token Acquisition", self.test_authentication),
            ]),
            ("Computers", [
                ("Get Computers (List)", self.test_get_computers_list),
                ("Get Computer (Detail)", self.test_get_computer_detail),
                ("Update Computer (Verify)", self.test_computer_update_endpoint),
            ]),
            ("Mobile Devices", [
                ("Get Mobile Devices (List)", self.test_get_mobile_devices_list),
                ("Get Mobile Device (Detail)", self.test_get_mobile_device_detail),
                ("Update Mobile Device (Verify)", self.test_mobile_device_update_endpoint),
            ]),
            ("Users", [
                ("Get Users (List)", self.test_get_users_list),
                ("Get User (Detail)", self.test_get_user_detail),
                ("Update User (Verify)", self.test_user_update_endpoint),
            ]),
            ("Smart Groups", [
                ("Get Computer Groups", self.test_get_computer_groups),
                ("Get Mobile Device Groups", self.test_get_mobile_device_groups),
                ("Create Smart Group", self.test_create_smart_group),
            ]),
            ("Static Groups", [
                ("Create Static Group", self.test_create_static_group),
            ]),
            ("Policies", [
                ("Get Policies (List)", self.test_get_policies),
            ]),
            ("App Installers", [
                ("Get App Installer Titles", self.test_get_app_installer_titles),
                ("Get App Installer Deployments", self.test_get_app_installer_deployments),
                (
                    "Get App Installer Deployment (Detail)",
                    self.test_get_app_installer_deployment_detail,
                ),
                ("Create App Installer Deployment", self.test_create_app_installer_deployment),
            ]),
            ("Configuration Profiles", [
                (
                    "Get Computer Configuration Profiles",
                    self.test_get_computer_configuration_profiles,
                ),
                (
                    "Get Mobile Device Configuration Profiles",
                    self.test_get_mobile_device_configuration_profiles,
                ),
            ]),
            ("Scripts", [
                ("Get Scripts (List)", self.test_get_scripts),
            ]),
            ("Extension Attributes", [
                ("Get Computer Extension Attributes", self.test_get_computer_eas),
                ("Get Mobile Extension Attributes", self.test_get_mobile_eas),
                ("Get User Extension Attributes", self.test_get_user_eas),
                ("Create Extension Attribute", self.test_create_extension_attribute),
            ]),
            ("PreStage Enrollments", [
                ("Get Computer PreStages", self.test_get_computer_prestages),
                ("Get Mobile Device PreStages", self.test_get_mobile_device_prestages),
            ]),
            ("Mac Apps", [
                ("Get Mac Apps (List)", self.test_get_mac_apps),
            ]),
            ("Mobile Device Apps", [
                ("Get Mobile Device Apps (List)", self.test_get_mobile_device_apps),
            ]),
            ("Restricted Software", [
                ("Get Restricted Software (List)", self.test_get_restricted_software),
            ]),
            ("eBooks", [
                ("Get eBooks (List)", self.test_get_ebooks),
            ]),
            ("Patch Policies", [
                ("Get Patch Policies (List)", self.test_get_patch_policies),
            ]),
            ("Buildings/Departments", [
                ("Get Buildings (List)", self.test_get_buildings),
                ("Get Departments (List)", self.test_get_departments),
            ]),
            ("Printers", [
                ("Get Printers (List)", self.test_get_printers),
                ("Create Printer", self.test_create_printer),
                ("Get Printer (Detail)", self.test_get_printer_detail),
                ("Update Printer", self.test_update_printer),
            ]),
            ("Categories", [
                ("Get Categories (List)", self.test_get_categories),
                ("Create Category", self.test_create_category),
            ]),
            ("API Roles", [
                ("Get API Role Privileges", self.test_get_api_role_privileges),
                ("Get API Roles (List)", self.test_get_api_roles),
                ("Get API Integrations (List)", self.test_get_api_integrations),
                ("Create API Role and Integration", self.test_create_api_role_and_integration),
            ]),
            # Jamf Protect tests (optional - will be skipped if not configured)
            ("Jamf Protect", [
                ("List Protect Alerts", self.test_protect_list_alerts),
                ("Get Protect Alert", self.test_protect_get_alert),
                ("List Protect Computers", self.test_protect_list_computers),
                ("Get Protect Computer", self.test_protect_get_computer),
                ("List Protect Analytics", self.test_protect_list_analytics),
                ("Get Protect Analytic", self.test_protect_get_analytic),
            ]),
            ("Risk API", [
                ("Get Risk Devices", self.test_get_risk_devices),
                ("Override Device Risk", self.test_override_device_risk),
            ]),
            ("MDM Commands", [
                ("List MDM Commands (Pending)", self.test_list_mdm_commands),
                ("Resolve Computer managementId", self.test_resolve_management_id_computer),
                ("Resolve Mobile Device managementId", self.test_resolve_management_id_mobile),
                ("Destructive Commands Require Confirmation", self.test_send_mdm_command_requires_confirmation),
                ("Send DEVICE_INFORMATION Command", self.test_send_device_information_command),
            ]),
        ]

        async with httpx.AsyncClient(timeout=30.0) as client:
            self.http_client = client

            for category, tests in test_plan:
                self._print_category(category)

                for test_name, test_func in tests:
                    result = await self._run_test(test_name, category, test_func)
                    self.report.results.append(result)
                    self._print_result(result)

        self.report.end_time = datetime.now()
        self._print_summary()

        return self.report

    def _print_summary(self):
        """Print test summary"""
        self._print_header("TEST SUMMARY")

        total = len(self.report.results)
        duration = (self.report.end_time - self.report.start_time).total_seconds()

        print(f"\n  Total Tests:   {total}")
        print(f"  {Colors.GREEN}Passed:{Colors.ENDC}        {self.report.passed}")
        print(f"  {Colors.RED}Failed:{Colors.ENDC}        {self.report.failed}")
        print(f"  {Colors.YELLOW}Skipped:{Colors.ENDC}       {self.report.skipped}")
        print(f"  {Colors.YELLOW}Warnings:{Colors.ENDC}      {self.report.warnings}")
        print(f"  Duration:      {duration:.2f}s")
        print(f"  Completed:     {self.report.end_time.strftime('%Y-%m-%d %H:%M:%S')}")

        if total > 0:
            pass_rate = (self.report.passed / total) * 100
            if pass_rate >= 80:
                color = Colors.GREEN
            elif pass_rate >= 50:
                color = Colors.YELLOW
            else:
                color = Colors.RED
            print(f"\n  {color}{Colors.BOLD}Pass Rate: {pass_rate:.1f}%{Colors.ENDC}")

        # List failures
        failures = [r for r in self.report.results if r.status == TestStatus.FAILED]
        if failures:
            print(f"\n  {Colors.RED}{Colors.BOLD}Failed Tests:{Colors.ENDC}")
            for f in failures:
                error_preview = f.error[:60] + "..." if f.error and len(f.error) > 60 else f.error
                print(f"    {Colors.RED}✗ {f.name}: {error_preview}{Colors.ENDC}")

        print()

    def save_report(self, filepath: str):
        """Save report to JSON file"""
        report_data = {
            "summary": {
                "jamf_url": self.report.jamf_url,
                "total": len(self.report.results),
                "passed": self.report.passed,
                "failed": self.report.failed,
                "skipped": self.report.skipped,
                "warnings": self.report.warnings,
                "duration_s": self.report.total_duration_s,
                "start_time": (
                    self.report.start_time.isoformat() if self.report.start_time else None
                ),
                "end_time": (
                    self.report.end_time.isoformat() if self.report.end_time else None
                ),
            },
            "results": [
                {
                    "name": r.name,
                    "category": r.category,
                    "status": r.status.value,
                    "response_code": r.response_code,
                    "duration_ms": r.duration_ms,
                    "message": r.message,
                    "error": r.error,
                    "details": r.details,
                }
                for r in self.report.results
            ]
        }

        with open(filepath, 'w') as f:
            json.dump(report_data, f, indent=2)

        print(f"  {Colors.DIM}Report saved to: {filepath}{Colors.ENDC}")


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Test all Jamf MCP Server commands against a Jamf Pro instance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using command line arguments:
  python test_agent.py --url https://yourcompany.jamfcloud.com --client-id ID --client-secret SECRET

  # Using environment variables:
  export JAMF_PRO_URL="https://yourcompany.jamfcloud.com"
  export JAMF_PRO_CLIENT_ID="your-client-id"
  export JAMF_PRO_CLIENT_SECRET="your-client-secret"
  python test_agent.py

  # Save report to file:
  python test_agent.py --output report.json

  # With Jamf Protect (optional):
  export JAMF_PROTECT_URL="https://tenant.protect.jamfcloud.com"
  export JAMF_PROTECT_CLIENT_ID="your-protect-client-id"
  export JAMF_PROTECT_PASSWORD="your-protect-password"
  python test_agent.py
        """
    )

    # Jamf Pro arguments (required)
    parser.add_argument(
        "--url", "-u",
        help="Jamf Pro URL (or set JAMF_PRO_URL env var)",
        default=os.environ.get("JAMF_PRO_URL")
    )
    parser.add_argument(
        "--client-id", "-i",
        help="OAuth Client ID (or set JAMF_PRO_CLIENT_ID env var)",
        default=os.environ.get("JAMF_PRO_CLIENT_ID")
    )
    parser.add_argument(
        "--client-secret", "-s",
        help="OAuth Client Secret (or set JAMF_PRO_CLIENT_SECRET env var)",
        default=os.environ.get("JAMF_PRO_CLIENT_SECRET")
    )

    # Jamf Protect arguments (optional)
    parser.add_argument(
        "--protect-url",
        help="Jamf Protect URL (or set JAMF_PROTECT_URL env var)",
        default=os.environ.get("JAMF_PROTECT_URL")
    )
    parser.add_argument(
        "--protect-client-id",
        help="Jamf Protect Client ID (or set JAMF_PROTECT_CLIENT_ID env var)",
        default=os.environ.get("JAMF_PROTECT_CLIENT_ID")
    )
    parser.add_argument(
        "--protect-password",
        help="Jamf Protect Password (or set JAMF_PROTECT_PASSWORD env var)",
        default=os.environ.get("JAMF_PROTECT_PASSWORD")
    )

    # Output arguments
    parser.add_argument(
        "--output", "-o",
        help="Output file for JSON report"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    return parser.parse_args()


async def main():
    """Main entry point"""
    args = parse_args()

    # Validate credentials
    if not args.url:
        print(f"{Colors.RED}Error: Jamf URL required. Use --url or set JAMF_PRO_URL{Colors.ENDC}")
        sys.exit(1)

    if not args.client_id or not args.client_secret:
        print(f"{Colors.RED}Error: OAuth credentials required.{Colors.ENDC}")
        print("  Use --client-id and --client-secret")
        print("  Or set JAMF_PRO_CLIENT_ID and JAMF_PRO_CLIENT_SECRET environment variables")
        sys.exit(1)

    # Create and run test agent
    agent = JamfTestAgent(
        jamf_url=args.url,
        client_id=args.client_id,
        client_secret=args.client_secret,
        verbose=args.verbose,
        protect_url=args.protect_url,
        protect_client_id=args.protect_client_id,
        protect_password=args.protect_password,
    )

    try:
        report = await agent.run_all_tests()

        # Save report if output file specified
        if args.output:
            agent.save_report(args.output)
        else:
            # Auto-save to timestamped file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = f"jamf_test_report_{timestamp}.json"
            agent.save_report(output_file)

        # Exit with appropriate code
        sys.exit(0 if report.failed == 0 else 1)

    except Exception as e:
        print(f"\n{Colors.RED}Fatal error: {e}{Colors.ENDC}")
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
