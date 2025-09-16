"""CDP WebSocket connectivity test for E2B sandbox.

English: This module provides a pytest-based test that:
  1) creates an E2B sandbox and launches Supervisor;
  2) discovers Chrome's DevTools WebSocket endpoint; and
  3) attempts an optional handshake via websockets (if the library is available).

中文：本模块提供基于 pytest 的测试，用于：
  1) 创建 E2B 沙箱并启动 Supervisor；
  2) 发现 Chrome DevTools 的 WebSocket 端点；
  3) 可选地通过 websockets 执行握手（若库已安装）。

Env vars required: E2B_API_KEY, E2B_TEMPLATE_ID / 需要的环境变量：E2B_API_KEY, E2B_TEMPLATE_ID
"""
from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from e2b_code_interpreter import Sandbox


def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def wait_for_supervisor(base_url: str, timeout: int = 90) -> None:
    sess = requests.Session()
    sess.verify = False
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = sess.get(f"{base_url}/api/v1/supervisor/status", timeout=5)
            if r.ok and r.json().get("success"):
                print(r.json())
                print("Supervisor reports RUNNING; proceeding.")
                return
        except Exception as exc:
            print(f"Waiting for supervisor: {exc!r}")
        time.sleep(3)
    raise RuntimeError("Supervisor readiness timeout")


def discover_cdp_ws(host_9222: str) -> str:
    """Find Chrome DevTools WebSocket URL, returning a wss:// URL.

    Strategy:
      - Try http://host/json/version then https://host/json/version
      - If successful, rewrite returned ws://127.0.0.1:PORT/... to wss://{host}/...
    """
    sess = requests.Session()
    sess.verify = False
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

    for scheme in ("http", "https"):
        url = f"{scheme}://{host_9222}/json/version"
        try:
            r = sess.get(url, timeout=10)
            if not r.ok:
                print(f"{url} -> status {r.status_code}")
                continue
            data = r.json()
            ws = data.get("webSocketDebuggerUrl")
            if not ws:
                print(f"{url} -> missing webSocketDebuggerUrl")
                continue
            # Normalize to wss://{host}/{path}
            # Example ws: ws://127.0.0.1:8222/devtools/browser/<id>
            path_start = ws.find("/devtools/")
            if path_start == -1:
                print(f"{url} -> unexpected ws url format: {ws}")
                continue
            path = ws[path_start:]
            final = f"wss://{host_9222}{path}"
            print(f"Discovered CDP WS endpoint: {final}")
            return final
        except Exception as exc:
            print(f"Fetch {url} failed: {exc!r}")
            continue

    raise RuntimeError("Unable to discover CDP WebSocket endpoint via /json/version")


def optional_handshake(ws_url: str) -> None:
    try:
        import websockets  # type: ignore
    except Exception:
        print("websockets not installed; skipping handshake test")
        return

    async def _run():
        async with websockets.connect(ws_url) as ws:
            payload = {"id": 1, "method": "Browser.getVersion"}
            await ws.send(json.dumps(payload))
            reply = await ws.recv()
            print("Handshake reply:", reply)

    import asyncio

    asyncio.run(_run())


def main() -> None:
    # Load .env from project root (two levels up from this file: /ai-manus/.env)
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent
    dotenv_path = project_root / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path, override=False)

    require_env("E2B_API_KEY")
    template = require_env("E2B_TEMPLATE_ID")

    print(f"Creating sandbox from template {template} ...")
    sb = Sandbox.create(template=template)
    sup = None
    try:
        # Start supervisor and wait for our services, including Chrome+socat
        sup = sb.commands.run("supervisord -c /app/supervisord.e2b.conf", background=True)

        # cat /app/supervisord.e2b.conf file in sandbox

        base_url = f"https://{sb.get_host(8080)}"
        wait_for_supervisor(base_url)

        host_9222 = sb.get_host(9222)
        print("9222 host:", host_9222)
        ws_url = discover_cdp_ws(host_9222)
        optional_handshake(ws_url)
        print("CDP WS connectivity check completed.")
    finally:
        time.sleep(10)
        if sup is not None:
            with suppress(Exception):
                sup.kill()
        with suppress(Exception):
            sb.kill()


@pytest.mark.e2b
def test_cdp_ws_connectivity() -> None:
    """English: Pytest entrypoint to validate CDP WS discovery and optional handshake.

    中文：Pytest 用例入口，验证 CDP WS 发现与可选握手。
    """
    # Delegate to the existing flow kept in `main()` for parity.
    main()


if __name__ == "__main__":
    main()