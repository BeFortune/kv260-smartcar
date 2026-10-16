"""Board SSH helper: password auth via paramiko, key injection, SFTP transfer.

Note: the board password is NOT stored in this file (removed before
publishing). Set ``FFN_BOARD_PASSWORD`` in the environment before use; the
board address/user were supplied by the project owner in-session and are
kept here because they are needed to reproduce the on-board runs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
import paramiko

HOST = "192.168.110.200"
PORT = 22
USER = "ubuntu"
PASSWORD = os.environ.get("FFN_BOARD_PASSWORD")


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD,
                   timeout=15, banner_timeout=15, auth_timeout=15, look_for_keys=False, allow_agent=False)
    return client


def run(client, command, timeout=120, sudo=False):
    if sudo:
        command = "echo '%s' | sudo -S bash -c %s" % (PASSWORD.replace("'", r"'\''"), "''" if command is None else "'"+command.replace("'", "'\\''")+"'")
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "echo ok"
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    client = connect()
    try:
        code, out, err = run(client, cmd, timeout=timeout)
        if out:
            print(out, end="" if out.endswith("\n") else "\n")
        if err:
            print("[stderr]", err, file=sys.stderr, end="" if err.endswith("\n") else "\n")
        sys.exit(code)
    finally:
        client.close()


if __name__ == "__main__":
    main()
