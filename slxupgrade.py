#!/usr/bin/env python3
"""
slxupgrade.py

Usage:
  python3 slxupgrade.py <device_ip> <slxos_version> [--username admin] [--password password] [--port 22] [-y]

Highlights:
  - Pre-check: reads current Firmware name; aborts if same as requested
  - Confirmation prompt (or -y)
  - Kicks off firmware download (FTP anonymous/gtac @ 10.53.26.23)
  - Auto-answers 'y'
  - Reboot handling:
      * First reconnect after upgrade trigger: sleep 2 minutes, then retry
      * Any later disconnects: short backoff reconnect (no 2-minute wait)
  - Progress sourced ONLY from last SULB-1000 onward:
      term len 0
      show logging raslog reverse count 200 | inc SULB|DCM
    (newest first; we slice from the most-recent SULB-1000 onward)
  - Prints latest status line every poll + friendly % mapping
  - Final: history, status, show ver; validates target Firmware name

Press Ctrl-C anytime to abort cleanly.
"""

import argparse
import re
import sys
import time
import signal
from datetime import datetime
from typing import Tuple, List, Optional

import paramiko
from paramiko.ssh_exception import ChannelException

# ===== Defaults / Constants =====
FTP_USER = "anonymous"
FTP_PASS = "gtac"
FTP_HOST = "10.53.26.23"

USERNAME = "admin"
PASSWORD = "password"
PORT = 22

PROMPT_REGEX = re.compile(r"[A-Za-z0-9_\-\.\/\(\)\s]*[#>]\s?$")
READ_SLEEP = 0.10
RECV_SIZE = 65535
PROMPT_WAIT_S = 20.0

# Timers
REBOOT_GRACE_S = 30
INITIAL_RECONNECT_SLEEP_S = 120      # 2-minute pause only for first reconnect after upgrade
RECONNECT_RETRIES = 240              # ~20 min of retries
RECONNECT_DELAY_S = 5                # short backoff for normal reconnects
RASLOG_POLL_INTERVAL_S = 5
RASLOG_POLL_RETRIES = 240            # ~20 min
HEARTBEAT_EVERY = 30

# RASLOG markers and friendly messages + nominal progress %
PROGRESS_STEPS = [
    (["SULB-1000"],   5, "Firmware request accepted; download/prepare starting"),
    (["SULB-1100"],  15, "Firmware upgrade process has begun (pre-commit)"),
    (["DCM-4001"],   40, "Database schema converted to new SLXOS"),
    (["DCM-1002"],   55, "Post-boot config replay started"),
    (["DCM-1005"],   70, "Config replay completed successfully"),
    (["DCM-1116"],   85, "System initialized on new SLXOS; configs loaded"),
    (["SULB-1106"], 100, "Firmware upgrade session completed"),
]
ALL_MARKERS = [code for codes, _, _ in PROGRESS_STEPS for code in codes]

# ===== Graceful abort =====
ABORT = False
def _sigint_handler(signum, frame):
    global ABORT
    ABORT = True
    print("\n[ABORT] Ctrl-C received. Finishing current step and exiting cleanly...")
signal.signal(signal.SIGINT, _sigint_handler)

# ===== Utilities =====
def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def info(msg: str):
    print(f"[{ts()}] {msg}")

def version_to_dir(ver: str) -> str:
    base = re.match(r"^(\d+\.\d+\.\d+)", ver)
    if not base:
        raise ValueError(f"Unrecognized version format: {ver}")
    base_str = base.group(1)
    return f"sre/Released/slxos/slxos{base_str}/slxos{ver}/slxos{ver}"

def build_fw_command(version: str) -> str:
    directory = version_to_dir(version)
    return (
        "firmware download ftp "
        f"user {FTP_USER} password {FTP_PASS} host {FTP_HOST} "
        f"directory {directory}"
    )

def open_shell(host: str, username: str, password: str, port: int = PORT):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host, port=port, username=username, password=password,
        look_for_keys=False, allow_agent=False, timeout=20, banner_timeout=25, auth_timeout=25
    )
    try:
        client.get_transport().set_keepalive(10)
    except Exception:
        pass

    chan = client.invoke_shell(width=200, height=200)
    chan.settimeout(PROMPT_WAIT_S)
    time.sleep(0.2)
    try:
        while chan.recv_ready():
            _ = chan.recv(RECV_SIZE)
    except Exception:
        pass

    # Disable paging early
    for cmd in ("term len 0", "terminal length 0", "skip-page-display"):
        try:
            _ = send_and_wait(chan, cmd)
        except Exception:
            pass

    return client, chan

def send_and_wait(chan, cmd: str, wait_prompt: bool = True) -> str:
    """
    Send a command and wait either for next prompt (default) or until a y/N prompt appears.
    Raises exceptions to allow the caller to reconnect if needed.
    """
    chan.send(cmd + "\n")

    buff = ""
    start = time.time()
    while True:
        if ABORT:
            raise KeyboardInterrupt()
        time.sleep(READ_SLEEP)
        if chan.recv_ready():
            chunk = chan.recv(RECV_SIZE).decode("utf-8", errors="ignore")
            buff += chunk
            if not wait_prompt:
                if "Do you want to continue" in buff or "[y/n]" in buff.lower():
                    break
            else:
                lines = [ln for ln in buff.splitlines() if ln.strip()]
                if lines and PROMPT_REGEX.search(lines[-1]):
                    break

        if time.time() - start > PROMPT_WAIT_S:
            break

    # Clean echo + trailing prompt
    lines = buff.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith(cmd):
            lines = lines[i + 1 :]
            break
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and PROMPT_REGEX.search(lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()

def try_reconnect(host: str, username: str, password: str, port: int = PORT, initial: bool = False):
    """
    initial=True -> sleep INITIAL_RECONNECT_SLEEP_S first (for first reboot window)
    initial=False -> short backoff retries only
    """
    if initial:
        info(f"Sleeping {INITIAL_RECONNECT_SLEEP_S}s before starting SSH retries ...")
        for _ in range(INITIAL_RECONNECT_SLEEP_S):
            if ABORT:
                raise KeyboardInterrupt()
            time.sleep(1)

    attempts = 0
    while attempts < RECONNECT_RETRIES:
        if ABORT:
            raise KeyboardInterrupt()
        try:
            return open_shell(host, username, password, port)
        except Exception:
            attempts += 1
            for _ in range(RECONNECT_DELAY_S):
                if ABORT:
                    raise KeyboardInterrupt()
                time.sleep(1)
    raise RuntimeError("Failed to reconnect after reboot")

def safe_cmd(chan, client, host, username, password, port, cmd: str, wait_prompt: bool = True, initial_reconnect: bool = False) -> Tuple[str, paramiko.SSHClient, any]:
    """
    Run a command, recovering automatically from 'Socket is closed'/10054/etc.
    Returns (output, client, chan) because on recovery we return new handles.
    """
    try:
        out = send_and_wait(chan, cmd, wait_prompt=wait_prompt)
        return out, client, chan
    except Exception as e:
        err = str(e)
        if "Socket is closed" in err or "10054" in err or isinstance(e, ChannelException):
            info("Socket exception detected while sending command. Reconnecting ...")
            client, chan = try_reconnect(host, username, password, port, initial=initial_reconnect)
            # re-ensure paging off
            try:
                _ = send_and_wait(chan, "term len 0")
            except Exception:
                pass
            out = send_and_wait(chan, cmd, wait_prompt=wait_prompt)
            return out, client, chan
        raise

def run_filtered_log_cmd(chan, client, host, username, password, port, initial_reconnect=False) -> Tuple[List[str], paramiko.SSHClient, any]:
    """
    After reconnection, scope status ONLY to the most recent upgrade:
      1) ensure 'term len 0'
      2) run: 'show logging raslog reverse count 200 | inc SULB|DCM'
         (newest first)
      3) find the most-recent 'SULB-1000' in this reversed list
      4) slice lines from that 'SULB-1000' onward (still newest→older)
    Returns (scoped_lines, client, chan).
    """
    try:
        _ = send_and_wait(chan, "term len 0")
    except Exception:
        pass

    # Read newest-first, filtered to just SULB/DCM
    try:
        out = send_and_wait(chan, "show logging raslog reverse count 200 | inc SULB|DCM")
    except Exception:
        info("Socket exception during filtered log read. Reconnecting ...")
        client, chan = try_reconnect(host, username, password, port, initial=initial_reconnect)
        try:
            _ = send_and_wait(chan, "term len 0")
        except Exception:
            pass
        out = send_and_wait(chan, "show logging raslog reverse count 200 | inc SULB|DCM")

    # Split & scope from the last (most recent) SULB-1000
    lines = [ln for ln in out.splitlines() if ln.strip()]
    idx = None
    for i, ln in enumerate(lines):  # reversed list: newest first
        if re.search(r"\bSULB-1000\b", ln):
            idx = i
            break
    if idx is not None:
        scoped = lines[idx:]  # from that SULB-1000 onward (toward older)
        info(f"Scoping progress to last SULB-1000 onward (lines {idx}..{len(lines)-1}, total {len(scoped)}).")
        lines = scoped
    else:
        info("No SULB-1000 found in recent 200 lines; using full filtered window.")

    return lines, client, chan

def check_raslog_markers_from_lines(lines: List[str]) -> set:
    found = set()
    text = "\n".join(lines)
    for code in ALL_MARKERS:
        if re.search(rf"\b{re.escape(code)}\b", text):
            found.add(code)
    return found

def most_recent_status_line(lines: List[str]) -> str:
    return lines[0] if lines else ""

def parse_firmware_name_from_show_ver(text: str) -> str:
    m = re.search(r"(?i)^\s*Firmware\s+name:\s*([A-Za-z0-9.\-]+)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1)
    m2 = re.search(r"(?i)SLX-OS\s+Operating\s+System\s+Version:\s*([A-Za-z0-9.\-]+)", text)
    return m2.group(1) if m2 else ""

def get_current_firmware(host: str, username: str, password: str, port: int) -> Tuple[Optional[str], Optional[paramiko.SSHClient], Optional[any]]:
    try:
        client, chan = open_shell(host, username, password, port)
        ver_out = send_and_wait(chan, "show ver")
        fw_name = parse_firmware_name_from_show_ver(ver_out)
        return fw_name, client, chan
    except Exception:
        return None, None, None

# ===== Main workflow =====
def do_upgrade(host: str, version: str, username: str, password: str, port: int = PORT, assume_yes: bool = False):
    fw_cmd = build_fw_command(version)
    directory = version_to_dir(version)

    # Pre-check: connect and read current firmware
    info(f"Pre-check: connecting to {host} to read current firmware ...")
    pre_client = pre_chan = None
    current_fw, pre_client, pre_chan = get_current_firmware(host, username, password, port)

    # Confirmation
    print("\n================ Confirmation ================")
    print(f"Target Device : {host}")
    print(f"Target SLX-OS : {version}")
    if current_fw:
        print(f"Current FW    : {current_fw}")
    print("Planned Command:")
    print(f"  {fw_cmd}")
    print("================================================")

    if current_fw and current_fw.lower() == version.lower():
        print("\nUpgrade to the same firmware version is not permitted.")
        print(" Hint: Use a different firmware version.")
        try:
            if pre_chan: pre_chan.close()
        except Exception:
            pass
        try:
            if pre_client: pre_client.close()
        except Exception:
            pass
        return

    if not assume_yes:
        resp = input(f"\nThis action will upgrade SLX {host} to SLXOS {version}. "
                     f"Do you wish to proceed? (y/yes to continue): ").strip()
        if resp.lower() not in ("y", "yes"):
            print("Aborted by user.")
            try:
                if pre_chan: pre_chan.close()
            except Exception:
                pass
            try:
                if pre_client: pre_client.close()
            except Exception:
                pass
            return
    else:
        print("[AUTO-CONFIRM] Proceeding without interactive prompt (-y).")

    # Start (reuse pre-check session when possible)
    info(f"Connecting to {host} ...")
    client = pre_client
    chan = pre_chan
    start_time = time.time()
    try:
        if not client or not chan:
            client, chan = open_shell(host, username, password, port)

        info("Connected. Issuing firmware download ...")
        info(f"Directory: {directory}")

        # Kick off firmware download (wait only for y/N)
        _, client, chan = safe_cmd(chan, client, host, username, password, port,
                                   fw_cmd, wait_prompt=False, initial_reconnect=False)
        # Auto-accept the prompt
        try:
            chan.send("y\n")
        except Exception:
            info("Socket exception while confirming 'y'. Reconnecting ...")
            client, chan = try_reconnect(host, username, password, port, initial=False)
            chan.send("y\n")

        info("Confirmed (y). Device will reboot for upgrade...")
        for _ in range(REBOOT_GRACE_S):
            if ABORT:
                raise KeyboardInterrupt()
            time.sleep(1)

        # Channel will likely die—ignore errors
        try:
            _ = send_and_wait(chan, "echo upgrade-in-progress")
        except Exception:
            pass
        try:
            chan.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass

        # First reconnect after reboot (use 2-minute sleep)
        info("Waiting for device to reboot and SSH to return ...")
        client, chan = try_reconnect(host, username, password, port, initial=True)
        info("Reconnected. Getting most recent status and polling progress ...")

        # Initial filtered status
        try:
            lines, client, chan = run_filtered_log_cmd(chan, client, host, username, password, port, initial_reconnect=False)
            recent = most_recent_status_line(lines)
            if recent:
                info(f"Most recent status: {recent}")
        except Exception:
            lines = []

        # Progress loop
        seen = set()
        last_heartbeat = time.time()
        max_pct = 0

        for _ in range(RASLOG_POLL_RETRIES):
            if ABORT:
                raise KeyboardInterrupt()

            try:
                lines, client, chan = run_filtered_log_cmd(chan, client, host, username, password, port, initial_reconnect=False)
            except Exception:
                info("Socket exception during RASLOG poll. Reconnecting ...")
                client, chan = try_reconnect(host, username, password, port, initial=False)
                lines, client, chan = run_filtered_log_cmd(chan, client, host, username, password, port, initial_reconnect=False)

            # Print latest line each iteration (if present)
            if lines:
                latest = most_recent_status_line(lines)
                if latest:
                    info(f"Latest status: {latest}")

            now_found = check_raslog_markers_from_lines(lines)
            newly = now_found - seen
            if newly:
                for codes, pct, message in PROGRESS_STEPS:
                    if any(code in newly for code in codes):
                        max_pct = max(max_pct, pct)
                        info(f"[{max_pct:>3}%] {message} ({', '.join(code for code in codes if code in newly)})")
                seen |= newly
                last_heartbeat = time.time()

            # Heartbeat if quiet
            if time.time() - last_heartbeat >= HEARTBEAT_EVERY:
                elapsed = int(time.time() - start_time)
                info(f"[{max_pct:>3}%] Waiting... elapsed {elapsed}s")
                last_heartbeat = time.time()

            # All markers seen?
            all_seen = all(any(code in seen for code in codes) for codes, _, _ in PROGRESS_STEPS)
            if all_seen:
                info("[100%] All required markers detected. Upgrade sequence completed.")
                break

            time.sleep(RASLOG_POLL_INTERVAL_S)
        else:
            info("WARNING: Not all markers observed within polling window. Proceeding to post-checks.")

        # Post-checks
        print("\n=== show firmwaredownloadhistory ===")
        out, client, chan = safe_cmd(chan, client, host, username, password, port, "show firmwaredownloadhistory")
        print(out)

        print("\n=== show firmwaredownloadstatus ===")
        out, client, chan = safe_cmd(chan, client, host, username, password, port, "show firmwaredownloadstatus")
        print(out)

        print("\n=== show version ===")
        ver_out, client, chan = safe_cmd(chan, client, host, username, password, port, "show ver")
        print(ver_out)

        # Verify firmware name
        fw_name = parse_firmware_name_from_show_ver(ver_out)
        if fw_name and fw_name.lower() == version.lower():
            info(f"SUCCESS: Device reports Firmware name '{fw_name}', matching requested '{version}'.")
        else:
            info(f"ATTENTION: Device reports Firmware name '{fw_name}', expected '{version}'. "
                 f"Please verify if another cycle is pending or if commit will occur on next reboot.")

        elapsed_total = int(time.time() - start_time)
        info(f"Total elapsed time: {elapsed_total}s")

    except KeyboardInterrupt:
        info("User aborted. Cleaning up and exiting.")
    finally:
        try:
            if pre_chan and pre_chan is not chan:
                pre_chan.close()
        except Exception:
            pass
        try:
            if pre_client and pre_client is not client:
                pre_client.close()
        except Exception:
            pass
        try:
            if chan:
                chan.close()
        except Exception:
            pass
        try:
            if client:
                client.close()
        except Exception:
            pass

# ===== CLI =====
def main():
    ap = argparse.ArgumentParser(description="Automate SLX-OS firmware upgrade over SSH with scoped progress (last SULB-1000 onward)")
    ap.add_argument("host", help="Device management IP or hostname")
    ap.add_argument("version", help="Target SLX-OS firmware (e.g., 20.7.2 or 20.7.1b)")
    ap.add_argument("--username", "-u", default=USERNAME, help="Username (default: admin)")
    ap.add_argument("--password", "-p", default=PASSWORD, help="Password (default: password)")
    ap.add_argument("--port", "-P", type=int, default=PORT, help="SSH port (default: 22)")
    ap.add_argument("-y", "--assume-yes", action="store_true", help="Skip interactive confirmation (auto-yes)")
    args = ap.parse_args()

    do_upgrade(args.host, args.version, args.username, args.password, args.port, assume_yes=args.assume_yes)

if __name__ == "__main__":
    main()
