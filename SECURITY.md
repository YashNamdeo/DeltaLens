# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | Yes                |

## Reporting a Vulnerability

If you discover a security vulnerability in DeltaLens, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, please email **deltalens@proton.me** with:

1. A description of the vulnerability
2. Steps to reproduce the issue
3. Potential impact
4. Suggested fix (if any)

We will acknowledge your report within 48 hours and aim to release a fix within 7 days for critical issues.

## Scope

DeltaLens processes source code locally. Security concerns include:

- **SQL injection** in SQLite graph queries
- **Path traversal** in file parsing operations
- **Denial of service** via crafted AST inputs
- **MCP server** authentication and input validation

## Best Practices

When running DeltaLens:

- Run it within your project directory only
- The MCP server binds to `localhost` by default — do not expose it to the network
- The SQLite database (`.deltalens/graph.db`) contains your code structure — treat it as sensitive
