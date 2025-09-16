"""Manual test runner for Manus sandbox on e2b.

The script creates an e2b sandbox based on the configured template, starts the
Manus sandbox services via Supervisor, and then exercises a few representative
API endpoints (shell exec/wait/view and file write/read). Use this to verify the
cloud sandbox quickly before wiring it into the server code path.
"""
from __future__ import annotations

import os
import time
from contextlib import suppress
from pathlib import Path

import requests
from dotenv import load_dotenv
from e2b_code_interpreter import Sandbox

SUPERVISOR_ENDPOINT = "/api/v1/supervisor/status"
SHELL_EXEC_ENDPOINT = "/api/v1/shell/exec"
SHELL_WAIT_ENDPOINT = "/api/v1/shell/wait"
SHELL_VIEW_ENDPOINT = "/api/v1/shell/view"
FILE_WRITE_ENDPOINT = "/api/v1/file/write"
FILE_READ_ENDPOINT = "/api/v1/file/read"


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot be made ready."""


def require_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise SandboxError(f"Missing required environment variable: {var_name}")
    return value


def wait_for_api(base_url: str, timeout: int = 90) -> None:
    """Poll the supervisor status endpoint until services report ready."""
    session = requests.Session()
    session.verify = False  # e2b issues self-signed certs for the ephemeral host
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = session.get(f"{base_url}{SUPERVISOR_ENDPOINT}", timeout=5)
            if resp.status_code == 200:
                payload = resp.json()
                if payload.get("success"):
                    print("Sandbox API is ready.")
                    return
                print(f"Supervisor returned not-ready state: {payload!r}")
            else:
                print(f"Supervisor status {resp.status_code}, retrying...")
        except Exception as exc:  # pragma: no cover - best effort logging
            print(f"Waiting for supervisor, encountered: {exc!r}")
        time.sleep(3)
    raise SandboxError("Timed out waiting for sandbox API to become ready")


def exercise_endpoints(base_url: str) -> None:
    """Run a minimal smoke test against the Manus sandbox API."""
    session = requests.Session()
    session.verify = False
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

    print("\n--- Shell: exec / wait / view ---")
    exec_resp = session.post(
        f"{base_url}{SHELL_EXEC_ENDPOINT}",
        json={
            "exec_dir": "/home/ubuntu",
            "command": "echo 'hello from e2b'",
        },
        timeout=10,
    )
    exec_resp.raise_for_status()
    exec_payload = exec_resp.json()
    print(f"Exec response: {exec_payload}")
    session_id = exec_payload.get("data", {}).get("session_id")
    if not session_id:
        raise SandboxError("Shell exec did not return a session id")

    wait_resp = session.post(
        f"{base_url}{SHELL_WAIT_ENDPOINT}",
        json={"id": session_id, "seconds": 10},
        timeout=10,
    )
    wait_resp.raise_for_status()
    print(f"Wait response: {wait_resp.json()}")

    view_resp = session.post(
        f"{base_url}{SHELL_VIEW_ENDPOINT}",
        json={"id": session_id, "console": True},
        timeout=10,
    )
    view_resp.raise_for_status()
    print(f"View response: {view_resp.json()}")

    print("\n--- File: write / read ---")
    test_path = "/home/ubuntu/test_e2b_sandbox.txt"
    test_content = "This file lives in the e2b sandbox."

    write_resp = session.post(
        f"{base_url}{FILE_WRITE_ENDPOINT}",
        json={
            "file": test_path,
            "content": test_content,
            "append": False,
        },
        timeout=10,
    )
    write_resp.raise_for_status()
    print(f"Write response: {write_resp.json()}")

    read_resp = session.post(
        f"{base_url}{FILE_READ_ENDPOINT}",
        json={"file": test_path},
        timeout=10,
    )
    read_resp.raise_for_status()
    read_payload = read_resp.json()
    print(f"Read response: {read_payload}")

    sandbox_content = read_payload.get("data", {}).get("content")
    if sandbox_content != test_content:
        raise SandboxError("File content mismatch inside sandbox")


def main() -> None:
    # Load .env from project root (two levels up from this file: /ai-manus/.env)
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent
    dotenv_path = project_root / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path, override=False)

    template_id = require_env("E2B_TEMPLATE_ID")
    require_env("E2B_API_KEY")  # validated for clarity

    print(f"Spawning e2b sandbox using template {template_id}...")
    sandbox = Sandbox.create(template=template_id)
    supervisor_proc = None
    try:
        supervisor_proc = sandbox.commands.run(
            "supervisord -c /app/supervisord.conf",
            background=True,
        )

        host = sandbox.get_host(8080)
        base_url = f"https://{host}"
        print(f"Sandbox HTTP endpoint available at: {base_url}")

        wait_for_api(base_url)
        exercise_endpoints(base_url)
        print("\nAll smoke tests passed.")
    finally:
        if supervisor_proc is not None:
            with suppress(Exception):
                supervisor_proc.kill()
        with suppress(Exception):
            sandbox.close()


if __name__ == "__main__":
    main()
