#!/usr/bin/env python3
"""Run one configured model without starting Codex."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--wire-api", choices=("responses", "chat"), default="chat")
    parser.add_argument("prompt", nargs="*")
    args = parser.parse_args()
    if not args.base_url.startswith(("http://", "https://")):
        parser.error("base URL must use http or https")
    key = os.environ.get(args.api_key_env) if args.api_key_env else None
    prompt = " ".join(args.prompt) if args.prompt else sys.stdin.read()
    if not prompt.strip():
        parser.error("provide a prompt or pipe one on standard input")
    if args.wire_api == "responses":
        endpoint = args.base_url.rstrip("/") + "/responses"
        payload = {"model": args.model_id, "input": prompt}
    else:
        endpoint = args.base_url.rstrip("/") + "/chat/completions"
        payload = {"model": args.model_id, "messages": [{"role": "user", "content": prompt}]}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        print(f"provider request failed: HTTP {error.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"provider connection failed: {error.reason}", file=sys.stderr)
        return 1
    if args.wire_api == "responses":
        print(result.get("output_text") or "")
        return 0
    choices = result.get("choices") or []
    message = choices[0].get("message") if choices else {}
    print((message or {}).get("content") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
