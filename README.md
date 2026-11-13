# Jamf MCP Server

> [!IMPORTANT]
> **mcp-hub is no longer actively maintained and is under consideration for archival.**
>
> We recommend [jamf-cli](https://github.com/Jamf-Concepts/jamf-cli), the preferred local tool for the functionality mcp-hub provides.
>
> If you rely on mcp-hub and archiving would affect you, please [open an issue](https://github.com/Jamf-Concepts/mcp-hub/issues) and let us know how you use it.

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
| [Tools](docs/TOOLS.md)               | Complete reference for all 50 MCP tools by product          |
| [Contributing](CONTRIBUTING.md)      | Development setup, testing, adding new tools                |

## Supported Products

| Product                 | Tools | Description                                                            |
| ----------------------- | ----- | ---------------------------------------------------------------------- |
| **Setup**               | 2     | Onboarding tools (always available, no credentials needed)             |
| **Jamf Pro**            | 40    | Device management, groups, policies, profiles, apps, scripts, printers |
| **Jamf Protect**        | 6     | Security alerts, enrolled computers, analytics (detection rules)       |
| **Jamf Security Cloud** | 2     | Device risk status and overrides via RISK API                          |

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
