"""Daemon lifecycle management - systemd (Linux) / launchd (macOS)."""

import logging
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from tj.config import get_config
from tj.database import APP_DIR
# Note: tj_agent.sync import is lazy in run_forever() for macOS venv compatibility

logger = logging.getLogger(__name__)

SERVICE_NAME = "tj_agent"
SYSTEMD_SERVICE_FILE = Path.home() / ".config/systemd/user/tj_agent.service"
LAUNCHD_PLIST_FILE = Path.home() / "Library/LaunchAgents/com.tj_agent.plist"


def get_agent_path() -> str:
    """Get path to agent entry point."""
    # The agent module location
    return str(Path(__file__).parent)


def is_linux() -> bool:
    return platform.system() == "Linux"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def is_daemon_installed() -> bool:
    """Check if daemon service is installed."""
    if is_linux():
        return SYSTEMD_SERVICE_FILE.exists()
    elif is_macos():
        return LAUNCHD_PLIST_FILE.exists()
    return False


def is_running() -> bool:
    """Check if agent daemon is running."""
    if is_linux():
        result = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True, text=True
        )
        return result.stdout.strip() == "active"
    elif is_macos():
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True
        )
        return "com.tjai.agent" in result.stdout
    return False


def install_daemon() -> bool:
    """Install daemon service file."""
    if is_linux():
        return _install_systemd()
    elif is_macos():
        return _install_launchd()
    logger.error(f"Unsupported platform: {platform.system()}")
    return False


def _install_systemd() -> bool:
    """Install systemd user service."""
    SYSTEMD_SERVICE_FILE.parent.mkdir(parents=True, exist_ok=True)

    python_path = sys.executable
    agent_module = Path(__file__).parent

    service_content = f"""[Unit]
Description=tjai sync agent
After=network-online.target

[Service]
Type=simple
ExecStart={python_path} -m tj_agent run
WorkingDirectory={agent_module.parent}
Restart=on-failure
RestartSec=5
Environment=PYTHONPATH={agent_module.parent}

[Install]
WantedBy=default.target
"""
    SYSTEMD_SERVICE_FILE.write_text(service_content)

    # Reload systemd
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    logger.info(f"Installed systemd service: {SYSTEMD_SERVICE_FILE}")
    return True


def _install_launchd() -> bool:
    """Install launchd LaunchAgent."""
    LAUNCHD_PLIST_FILE.parent.mkdir(parents=True, exist_ok=True)

    # macOS: use venv Python due to Homebrew PEP 668 restrictions
    venv_python = APP_DIR / "venv" / "bin" / "python3"
    if venv_python.exists():
        python_path = str(venv_python)
    else:
        python_path = sys.executable
        logger.warning(f"venv not found at {venv_python}, using {python_path}")

    agent_module = Path(__file__).parent
    log_path = APP_DIR / "agent.log"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tjai.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>tj_agent</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{agent_module.parent}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>{agent_module.parent}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""
    LAUNCHD_PLIST_FILE.write_text(plist_content)
    logger.info(f"Installed launchd plist: {LAUNCHD_PLIST_FILE}")
    return True


def start_daemon() -> bool:
    """Start the daemon."""
    if is_linux():
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", SERVICE_NAME],
            check=True
        )
        return True
    elif is_macos():
        subprocess.run(
            ["launchctl", "load", str(LAUNCHD_PLIST_FILE)],
            check=True
        )
        return True
    return False


def stop_daemon() -> bool:
    """Stop the daemon."""
    if is_linux():
        subprocess.run(
            ["systemctl", "--user", "stop", SERVICE_NAME],
            check=True
        )
        return True
    elif is_macos():
        subprocess.run(
            ["launchctl", "unload", str(LAUNCHD_PLIST_FILE)],
            check=True
        )
        return True
    return False


def restart_daemon() -> bool:
    """Restart the daemon."""
    if is_linux():
        subprocess.run(
            ["systemctl", "--user", "restart", SERVICE_NAME],
            check=True
        )
        return True
    elif is_macos():
        stop_daemon()
        time.sleep(1)
        start_daemon()
        return True
    return False


def ensure_running() -> bool:
    """
    Ensure daemon is installed and running.
    Called by tj CLI on every command.
    Returns True if agent is running.
    """
    if is_running():
        return True

    if not is_daemon_installed():
        logger.info("Installing daemon service...")
        if not install_daemon():
            return False

    logger.info("Starting daemon...")
    return start_daemon()


def run_forever() -> None:
    """
    Run the sync loop forever.
    This is the daemon's main entry point.
    """
    # Lazy import: requires 'requests' which is only in venv on macOS
    from tj_agent.sync import sync_cycle, write_status

    config = get_config()
    interval = config.get("sync_interval_seconds", 5)

    logger.info(f"tj_agent starting, sync interval: {interval}s")
    write_status(last_error=None)

    while True:
        try:
            sync_cycle()
        except Exception as e:
            logger.exception(f"Sync cycle failed: {e}")
            # Continue running, will retry next cycle

        time.sleep(interval)
