# Security

## Key rules

- Do not commit API keys, cookies, session files, or private prompts.
- Use environment variables or an external credential helper.
- The panel does not collect or display keys. Provider helper scripts read a
  key only when the selected route needs it.
- If an agent offers to scan for keys, require a current user approval first.
- The server binds to loopback by default.
- The daemon does not print authorization headers or request bodies.
- The public repository contains no machine-specific paths or account data.

## Local use

Run the panel on a trusted computer. Do not expose its port to a network.
Review the provider URL before enabling a custom provider. Use HTTPS for remote
providers. Keep provider keys in the provider's supported credential store or
environment variable.

## Reporting

Report a security issue privately through the repository security contact. Do
not include live credentials in the report.
