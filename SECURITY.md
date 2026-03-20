# Security Policy

## Reporting

Do not open public issues for security vulnerabilities.

Report vulnerabilities through:

- GitHub Security Advisories for this repository, if enabled
- Private contact with the maintainer

Include:

- A clear description of the issue
- Steps to reproduce
- Expected impact
- Any suggested mitigation

## HermitCrab Security Model

HermitCrab is designed so Python remains the enforcement layer.
Models can propose actions, but they must not be trusted to enforce security boundaries.

Current security posture:

- Tool execution is validated in Python before it runs
- File access should stay constrained to explicit allowed paths
- Memory writes should remain typed, deterministic, and auditable
- Channel access control should be configured explicitly for production use
- Local workspace data under `~/.hermitcrab/` should be treated as sensitive

## Operational Guidance

### Protect secrets

- Never commit API keys, tokens, or private keys
- Keep `~/.hermitcrab/config.json` permissioned to the local user only
- Prefer separate credentials for development and production

Example:

```bash
chmod 700 ~/.hermitcrab
chmod 600 ~/.hermitcrab/config.json
```

### Lock down channels

- Configure Telegram `allowFrom` lists before exposing a bot publicly
- Configure Nostr `allowedPubkeys` before exposing a key to untrusted senders
- Treat an empty allowlist as open access unless code explicitly documents otherwise

### Run with least privilege

- Do not run HermitCrab as `root`
- Prefer a dedicated user account for long-running deployments
- Restrict filesystem permissions around any directories the agent can access

### Review tool exposure

- Be conservative with shell, web, and file tools
- Keep Python-side validation authoritative
- Prefer explicit fallback behavior over silent failures when a tool call is malformed

### Audit dependencies

- Keep Python dependencies current
- Review provider and channel integrations carefully because they are the most exposed boundaries

Example:

```bash
pip install pip-audit
pip-audit
```

## Deployment Notes

For production or unattended use:

- Isolate the runtime in a container, VM, or dedicated account
- Monitor logs for access denials, tool failures, and unusual channel activity
- Back up `~/.hermitcrab/` appropriately if it contains important memory or journal data
- Review changes touching `agent/`, `channels/`, `providers/`, and tool execution paths more strictly

## Scope And Limitations

HermitCrab is a local-first alpha project. That means:

- Security controls are improving, not complete
- Misconfigured channels can expose the agent to unwanted input
- Third-party model providers can still see prompts sent to them
- Local memory, logs, and journals may contain sensitive user data

If you change tool execution, web access, provider parsing, or channel handling, review the security implications as part of the change.
