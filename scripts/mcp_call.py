#!/usr/bin/env python3
"""Call one tjai MCP tool over HTTPS (docs/mcp.md), for scripts and checks.

Usage: mcp_call.py <tool> ['<json arguments>']
Token: TJAI_MCP_TOKEN from ~/.env. Prints the tool's text result.
"""
import json
import os
import sys
import urllib.request

URL = os.environ.get('TJAI_MCP_URL', 'https://etaverse.com/tjai/mcp/')


def token():
    if os.environ.get('TJAI_MCP_TOKEN'):
        return os.environ['TJAI_MCP_TOKEN']
    with open(os.path.expanduser('~/.env')) as f:
        for line in f:
            line = line.strip()
            if line.startswith('export '):
                line = line[7:]
            if line.startswith('TJAI_MCP_TOKEN='):
                return line.split('=', 1)[1].strip().strip('"').strip("'")
    sys.exit('TJAI_MCP_TOKEN not found')


def call(tool, arguments, timeout=120):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method='POST', headers={
        'Authorization': f'Bearer {token()}', 'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    if 'error' in resp:
        sys.exit(f"MCP error: {resp['error']}")
    res = resp['result']
    text = ''.join(c.get('text', '') for c in res.get('content', []))
    if res.get('isError'):
        sys.exit(f'tool error: {text[:800]}')
    return text


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(call(sys.argv[1], args))
