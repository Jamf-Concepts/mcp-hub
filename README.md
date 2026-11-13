![icon.png](assets/icon.png)

# Jamf MCP Server

An MCP server that enables LLMs to interact with Jamf Pro, Protect, and Security Cloud for Apple device management.

## Quick Start

1. **Configure your MCP client** (see [Installation](docs/INSTALLATION.md))
2. **Restart your client** — it automatically starts the server
3. **Ask Claude** for help:
   - "What's the setup status?" → shows which products are configured
   - "How do I configure Jamf Pro?" → step-by-step setup instructions

No credentials required to start — the server runs in onboarding mode until configured.

### Try It Out

Once configured, ask things like:

- "Find all computers running macOS 15"
- "Create a smart group for M3 MacBooks"
- "Show me policies in the Security category"

See [Installation](docs/INSTALLATION.md) for full configuration details.

## Documentation

| Doc                                  | Description                                                 |
| ------------------------------------ | ----------------------------------------------------------- |
| [Installation](docs/INSTALLATION.md) | Setup, env vars, client configuration (Claude Desktop, CLI) |
| [Tools](docs/TOOLS.md)               | Complete reference for all 64 MCP tools by product          |
| [Contributing](CONTRIBUTING.md)      | Development setup, testing, adding new tools                |

## Supported Products

| Product                 | Tools | Description                                                            |
| ----------------------- | ----- | ---------------------------------------------------------------------- |
| **Setup**               | 2     | Onboarding tools (always available, no credentials needed)             |
| **Jamf Pro**            | 54    | Device management, groups, policies, profiles, apps, scripts, printers, MDM commands |
| **Jamf Protect**        | 6     | Security alerts, enrolled computers, analytics (detection rules)       |
| **Jamf Security Cloud** | 2     | Device risk status and overrides via RISK API                          |

### Management Commands (new)

Send MDM commands to managed Apple devices (macOS, iOS/iPadOS, tvOS):

| Tool | Description |
| ---- | ----------- |
| `jamf_list_mdm_commands` | List queued/completed MDM commands with RSQL filtering |
| `jamf_get_device_management_id` | Resolve a numeric Jamf ID to its MDM managementId UUID |
| `jamf_send_mdm_command` | Generic: send any command type with custom payload |
| `jamf_lock_device` | ⚠️ Remote lock (DEVICE_LOCK) |
| `jamf_erase_device` | ⚠️ Remote wipe (ERASE_DEVICE) — irreversible |
| `jamf_restart_device` | ⚠️ Remote restart (RESTART_DEVICE) |
| `jamf_shut_down_device` | ⚠️ Remote shutdown (SHUT_DOWN_DEVICE) |
| `jamf_clear_passcode` | ⚠️ Clear iOS/iPadOS passcode (CLEAR_PASSCODE) |
| `jamf_set_recovery_lock` | ⚠️ Set/remove macOS Recovery Lock password |
| `jamf_delete_user` | ⚠️ Delete user account (DELETE_USER) |
| `jamf_log_out_user` | ⚠️ Log out current user (LOG_OUT_USER) |
| `jamf_enable_lost_mode` | Enable Lost Mode on supervised iOS/iPadOS |
| `jamf_disable_lost_mode` | Disable Lost Mode |
| `jamf_enable_remote_desktop` | Enable Screen Sharing on macOS |
| `jamf_disable_remote_desktop` | Disable Screen Sharing on macOS |

> ⚠️ Destructive commands require `confirm=True`. The model will always verify device identity with you before setting this flag.

## Requirements

- Python 3.10+
- Jamf instance (optional - server starts without credentials for onboarding)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and guidelines.

## License

Copyright 2026 Jamf Software LLC.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## Links

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Jamf Developer Portal](https://developer.jamf.com/)
