"""Keep the existing private Android serial protocol, with desktop Linux tools."""
import base64
import os
import platform
import re
import subprocess
import sys
import tempfile
import termios

VERSION = f"debian12-desktop-{platform.machine()}-v1"


def encode(value):
    return base64.b64encode(value).decode() if value else "-"


def main():
    tty = termios.tcgetattr(0)
    tty[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(0, termios.TCSANOW, tty)
    subprocess.run(["ip", "link", "set", "eth0", "up"], check=False)
    try:
        network = "ready" if subprocess.run(["dhclient", "-1", "eth0"], timeout=45, check=False).returncode == 0 else "unavailable"
    except subprocess.TimeoutExpired:
        network = "unavailable"
    print(f"CYRENE_VM_READY {VERSION} {network} ready", flush=True)
    for line in sys.stdin:
        fields = line.strip().split(" ", 2)
        if len(fields) != 3 or not re.fullmatch(r"[a-zA-Z0-9_]+", fields[1]):
            continue
        operation, request_id, payload = fields
        if operation == "CYRENE_PING":
            print(f"CYRENE_PONG {request_id} {VERSION} {network} ready", flush=True)
        elif operation == "CYRENE_SHUTDOWN":
            subprocess.run(["/opt/cyrene-mobile/desktop-control", "stop"], check=False)
            os.sync()
            subprocess.run(["systemctl", "poweroff"], check=False)
            return
        elif operation == "CYRENE_EXEC":
            try:
                command = base64.b64decode(payload, validate=True)
                if len(command) > 256 * 1024:
                    raise ValueError("command too large")
                with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                    result = subprocess.run(["/bin/sh"], input=command, stdout=stdout, stderr=stderr, cwd="/workspace")
                    os.sync()
                    stdout.seek(0)
                    stderr.seek(0)
                    output, errors = stdout.read(393216), stderr.read(393216)
                code = result.returncode
            except (ValueError, OSError) as error:
                code, output, errors = 126, b"", str(error).encode()
            print(f"CYRENE_RESULT {request_id} {code} {encode(output)} {encode(errors)}", flush=True)


if __name__ == "__main__":
    main()
