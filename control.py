#!/usr/bin/env python3
import sys, json, subprocess, os, signal

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(PLUGIN_DIR, "backend.pid")
BACKEND = os.path.join(PLUGIN_DIR, "backend", "backend.py")
LOG_FILE = os.path.join(PLUGIN_DIR, "control.log")

def log(msg):
    pass

raw = sys.stdin.read()
log(f"--- called ---")
log(f"argv: {sys.argv}")
log(f"stdin: {raw!r}")
log(f"cwd: {os.getcwd()}")

try:
    payload = json.loads(raw) if raw.strip() else {}
except Exception as e:
    log(f"json error: {e}")
    payload = {}

mode = payload.get("args", {}).get("mode", "start")
log(f"mode: {mode}")


if mode == "start":
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            print(json.dumps({"output": f"Backend already running (PID {old_pid})"}))
            sys.exit(0)
        except (OSError, ValueError):
            os.remove(PID_FILE)

    proc = subprocess.Popen(
        [sys.executable, BACKEND],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    print(json.dumps({"output": f"Backend started (PID {proc.pid})"}))

elif mode == "stop":
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        os.remove(PID_FILE)
        print(json.dumps({"output": f"Backend stopped (PID {pid})"}))
    except FileNotFoundError:
        print(json.dumps({"output": "Backend not running (no PID file)"}))
    except ProcessLookupError:
        os.remove(PID_FILE)
        print(json.dumps({"output": "Backend not running (stale PID removed)"}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

else:
    print(json.dumps({"error": f"unknown mode: {mode}"}))
