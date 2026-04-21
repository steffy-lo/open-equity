import json
import logging
import os
import shutil
import subprocess
from typing import Any


logger = logging.getLogger(__name__)


OPENCLAW_BIN = os.getenv("OPENCLAW_BIN") or shutil.which("openclaw") or "/home/ubuntu/.npm-global/bin/openclaw"


def _telegram_target_parts(target: str) -> tuple[str, str | None]:
    raw = (target or "").strip()
    if raw.startswith("telegram:"):
        raw = raw[len("telegram:") :]

    parts = raw.split(":")
    if len(parts) >= 2 and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return raw, None


def send_openclaw_message(*, channel: str, target: str, message: str, account_id: str | None = None, wait: bool = True) -> dict[str, Any]:
    if not target:
        return {"ok": False, "skipped": True, "reason": "missing_target"}

    command = [
        OPENCLAW_BIN,
        "message",
        "send",
        "--json",
        "--channel",
        channel,
        "--message",
        message,
    ]

    if account_id:
        command.extend(["--account", account_id])

    if channel == "telegram":
        chat_target, thread_id = _telegram_target_parts(target)
        command.extend(["--target", chat_target])
        if thread_id:
            command.extend(["--thread-id", thread_id])
    else:
        command.extend(["--target", target])

    try:
        if not wait:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"ok": True, "queued": True}

        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        stdout = (completed.stdout or "").strip()
        parsed = json.loads(stdout) if stdout else {}
        return {"ok": True, "result": parsed}
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or exc.stdout or str(exc)).strip()
        logger.warning(f"[openclaw_notify] send failed: {stderr}")
        return {"ok": False, "error": stderr}
    except Exception as exc:
        logger.warning(f"[openclaw_notify] send failed: {exc}")
        return {"ok": False, "error": str(exc)}
