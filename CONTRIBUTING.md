# Contributing

Keep changes small and easy to verify.

- Do not add private paths, personal data, keys, or local account details.
- Add checks for route decisions and input validation.
- Run `python3 -m compileall -q skill/scripts skill/web` and `bash -n` on the
  shell scripts before submitting changes.
- Keep the local server loopback-only unless a separate security design is
  approved.
