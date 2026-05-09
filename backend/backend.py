#!/usr/bin/env python3
"""
Autoblow Sync Backend – WebSocket only
Receives video events (play/pause/seek) and toolbar commands via WebSocket.
"""

import base64
import hashlib
import json
import logging
import os
import socket
import struct
import sys
import threading
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler

# ---------- Configuration ----------
HOST = "0.0.0.0"
WS_PORT = 7879
LOCK_PORT = 7322

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(SCRIPT_DIR)
LOG_FILE = os.path.join(SCRIPT_DIR, "backend.log")
OFFSET_FILE = os.path.join(SCRIPT_DIR, "offset.json")
DEBUG_FLAG = os.path.join(SCRIPT_DIR, ".debug")

STASH_URL = "http://localhost:9999/graphql"
STASH_API_KEY = ""

# ---------- Singleton lock ----------
def already_running(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

if already_running(LOCK_PORT):
    sys.stderr.write(f"Backend already running on port {LOCK_PORT}, exiting.\n")
    sys.exit(0)



# ---------- Logging ----------
if os.path.exists(DEBUG_FLAG) or os.environ.get("AB_LOG_LEVEL", "").upper() == "DEBUG":
    LOG_LEVEL = logging.DEBUG
else:
    LOG_LEVEL = logging.INFO

logger = logging.getLogger("autoblow")
logger.setLevel(LOG_LEVEL)
logger.propagate = False

fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
fh.setLevel(LOG_LEVEL)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(fh)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger.addHandler(ch)

logger.info("Logging started (level=%s)", logging.getLevelName(LOG_LEVEL))

# ---------- Global state ----------
state = {
    "enabled": True,
    "armed": True,
    "video_playing": False,
    "current_script": None,
    "scripts": [],
    "scene_id": None,
    "scene_path": None,
    "autoblow_url": "",
    "device_token": "",
    "offset_ms": 0,
    "uploading": False,
}

state_lock = threading.Lock()
clients = []
clients_lock = threading.Lock()

# ---------- Helpers ----------
def make_response(action, ok=True, **kwargs):
    resp = {
        "ok": ok,
        "action": action,
        "enabled": state["enabled"],
        "armed": state["armed"],
        "video_playing": state["video_playing"],
        "playing": state["enabled"] and state["armed"] and state["video_playing"],
        "uploading": state["uploading"],
        "scripts": [os.path.basename(s) for s in state["scripts"]],
        "current": os.path.basename(state["current_script"]) if state["current_script"] else None,
        "scene_id": state["scene_id"],
        "offset_ms": state["offset_ms"],
    }
    resp.update(kwargs)
    return resp

def broadcast(data):
    msg = json.dumps(data)
    with clients_lock:
        for conn in list(clients):
            try:
                ws_send_frame(conn, msg)
            except Exception:
                pass

def info(msg):
    """Log message and send info text to all connected toolbars."""
    logger.info(msg)
    broadcast({"action": "info", "message": msg})

def broadcast_info(text):
    info(text)

# ---------- Stash GraphQL ----------
def stash_query(query, variables=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if STASH_API_KEY:
        headers["ApiKey"] = STASH_API_KEY
    try:
        req = urllib.request.Request(STASH_URL, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error("Stash query failed: %s", e)
        return None

def load_offset():
    try:
        with open(OFFSET_FILE) as f:
            state["offset_ms"] = int(json.load(f).get("offset_ms", 0))
        logger.info("Offset loaded: %dms", state["offset_ms"])
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error("Failed to load offset: %s", e)

def save_offset():
    try:
        with open(OFFSET_FILE, "w") as f:
            json.dump({"offset_ms": state["offset_ms"]}, f)
    except Exception as e:
        logger.error("Failed to save offset: %s", e)

def load_plugin_config():
    query = "{ configuration { plugins } }"
    result = stash_query(query)
    if not result:
        return
    try:
        plugins = result["data"]["configuration"]["plugins"]
        cfg = plugins.get("autoblow_sync", {}) or plugins.get("AutoblowSync", {})
        state["device_token"] = cfg.get("device_token", "") or ""
        state["autoblow_url"] = (cfg.get("server_url", "") or "").rstrip("/")
        try:
            state["offset_ms"] = int(cfg.get("offset_ms", 0) or 0)
        except Exception:
            state["offset_ms"] = 0
        logger.info("Plugin config loaded: url=%s, offset=%dms, token=%s",
                    state["autoblow_url"], state["offset_ms"],
                    "set" if state["device_token"] else "missing")
    except Exception as e:
        logger.error("Failed to parse plugin config: %s", e)

def get_scene_path(scene_id):
    query = """
    query FindScene($id: ID!) {
      findScene(id: $id) {
        files { path }
      }
    }
    """
    result = stash_query(query, {"id": scene_id})
    if not result:
        return None
    try:
        files = result["data"]["findScene"]["files"]
        if files:
            return files[0]["path"]
    except Exception as e:
        logger.error("Failed to read scene path: %s", e)
    return None

# ---------- Funscripts ----------
def scan_funscripts(video_path):
    """Find all .funscript files next to the video and pick the best default match."""
    if not video_path or not os.path.exists(video_path):
        logger.warning("Video path not found: %s", video_path)
        return [], None

    video_dir = os.path.dirname(video_path)
    video_name = os.path.splitext(os.path.basename(video_path))[0]

    try:
        all_files = os.listdir(video_dir)
    except Exception as e:
        logger.error("Failed to read directory: %s", e)
        return [], None

    funscripts = sorted([
        os.path.join(video_dir, f)
        for f in all_files
        if f.lower().endswith(".funscript")
    ])

    # Prefer a funscript matching the video filename
    default_script = None
    for fs in funscripts:
        fs_name = os.path.splitext(os.path.basename(fs))[0]
        if fs_name.lower() == video_name.lower():
            default_script = fs
            break
    if not default_script and funscripts:
        default_script = funscripts[0]

    logger.info("Found %d funscripts (default: %s)",
                len(funscripts),
                os.path.basename(default_script) if default_script else "none")
    return funscripts, default_script

# ---------- Autoblow API ----------
def autoblow_request(method, path, body=None, files=None):
    if not state["autoblow_url"] or not state["device_token"]:
        logger.error("Autoblow not configured (url/token missing)")
        return None, None

    url = state["autoblow_url"] + path

    try:
        if files:
            # Build a multipart/form-data body manually (no external deps)
            boundary = "----AutoblowBoundary"
            body_parts = []
            for field_name, (filename, content, content_type) in files.items():
                body_parts.append((
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8"))
                if isinstance(content, str):
                    body_parts.append(content.encode("utf-8"))
                else:
                    body_parts.append(content)
                body_parts.append(b"\r\n")
            body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))

            raw_body = b"".join(body_parts)
            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "x-device-token": state["device_token"],
            }
        else:
            raw_body = json.dumps(body or {}).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "x-device-token": state["device_token"],
            }

        req = urllib.request.Request(url, data=raw_body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            try:
                data = json.loads(resp.read().decode("utf-8"))
            except Exception:
                data = {}
            return status, data

    except urllib.error.HTTPError as e:
        logger.error("HTTP error %s: %s", e.code, e.reason)
        return e.code, None
    except Exception as e:
        logger.error("Request failed: %s", e)
        return None, None

def autoblow_upload_script(script_path):
    if not script_path or not os.path.exists(script_path):
        logger.error("Script not found: %s", script_path)
        return None
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.error("Failed to read script: %s", e)
        return None

    filename = os.path.basename(script_path)
    status, data = autoblow_request(
        "PUT",
        "/autoblow/sync-script/upload-funscript",
        files={"file": (filename, content, "application/json")}
    )
    if status == 200 and data:
        token = data.get("syncScriptToken") or data.get("scriptToken")
        if token:
            logger.info("Script uploaded: %s -> token=%s", filename, token[:12] + "...")
            return token
    logger.error("Script upload failed: status=%s data=%s", status, data)
    return None

def autoblow_load_token(token):
    status, _ = autoblow_request(
        "PUT",
        "/autoblow/sync-script/load-token",
        body={"scriptToken": token}
    )
    if status == 200:
        logger.info("Token loaded onto device")
        return True
    logger.error("load-token failed: status=%s", status)
    return False

def autoblow_play(time_ms):
    time_ms = max(0, int(time_ms) + state["offset_ms"])
    status, _ = autoblow_request(
        "PUT",
        "/autoblow/sync-script/start",
        body={"startTimeMs": time_ms}
    )
    logger.debug("Play @ %dms (status=%s)", time_ms, status)
    return status == 200

def autoblow_pause():
    status, _ = autoblow_request("PUT", "/autoblow/sync-script/stop", body={})
    logger.debug("Pause (status=%s)", status)
    return status == 200

# ---------- Scene handling ----------
def ensure_script_uploaded(scene_id, script_path, resume=False):
    with state_lock:
        state["uploading"] = True
    broadcast_info(f"Uploading {os.path.basename(script_path)}…")
    try:
        token = autoblow_upload_script(script_path)
        if not token:
            broadcast_info("Upload failed!")
            return False
        if not autoblow_load_token(token):
            broadcast_info("Load-token failed!")
            return False
        broadcast({"action": "script_ready"})
        return True
    finally:
        with state_lock:
            state["uploading"] = False
        broadcast(make_response("status"))


def load_scene(scene_id):
    logger.info("=== Loading scene %s ===", scene_id)
    video_path = get_scene_path(scene_id)
    if not video_path:
        logger.warning("No video path for scene %s", scene_id)
        return

    state["scene_id"] = scene_id
    state["scene_path"] = video_path

    scripts, default_script = scan_funscripts(video_path)
    state["scripts"] = scripts
    state["current_script"] = default_script

    broadcast({
        "action": "scripts_updated",
        "scripts": [os.path.basename(s) for s in scripts],
        "current": os.path.basename(default_script) if default_script else None,
    })

    if not scripts:
        broadcast_info("No funscripts found")
        return

    if state["enabled"]:
        ensure_script_uploaded(scene_id, default_script)

# ---------- WebSocket protocol ----------
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def ws_handshake(conn, request_data):
    lines = request_data.decode("utf-8", errors="replace").split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    key = headers.get("sec-websocket-key", "")
    accept = base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    )
    conn.sendall(response.encode())

def ws_recv_frame(conn):
    try:
        header = conn.recv(2)
        if len(header) < 2:
            return None, None
        b1, b2 = header[0], header[1]
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", conn.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", conn.recv(8))[0]
        mask = conn.recv(4) if masked else None
        data = b""
        while len(data) < length:
            chunk = conn.recv(length - len(data))
            if not chunk:
                break
            data += chunk
        if masked and mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return opcode, data
    except Exception as e:
        logger.debug("ws_recv_frame error: %s", e)
        return None, None

def ws_send_frame(conn, text):
    payload = text.encode("utf-8")
    length = len(payload)
    if length <= 125:
        header = bytes([0x81, length])
    elif length <= 65535:
        header = bytes([0x81, 126]) + struct.pack(">H", length)
    else:
        header = bytes([0x81, 127]) + struct.pack(">Q", length)
    conn.sendall(header + payload)

def ws_send_pong(conn):
    try:
        conn.sendall(bytes([0x8A, 0]))
    except Exception:
        pass

# ---------- Message handler ----------
def handle_message(raw):
    try:
        msg = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": False, "error": "JSON parse error"}

    action = msg.get("action", "")
    logger.debug("Action: %s | Msg: %s", action, msg)

    def should_play():
        return state["enabled"] and state["armed"] and state["video_playing"]

    if action == "status":
        return make_response("status")

    elif action == "scene":
        scene_id = msg.get("scene_id")
        if not scene_id:
            return make_response("scene", ok=False, error="scene_id missing")
        if str(scene_id) == str(state["scene_id"]):
            return make_response("scene", message="already loaded")
        if not state["enabled"]:
            state["scene_id"] = str(scene_id)
            return make_response("scene", message="plugin disabled")
        threading.Thread(target=load_scene, args=(str(scene_id),), daemon=True).start()
        return make_response("scene")

    elif action == "video_play":
        time_ms = int(msg.get("time_ms", 0) or 0)
        state["video_playing"] = True
        state["video_time_ms"] = time_ms
        if should_play() and state["current_script"] and not state["uploading"]:
            threading.Thread(target=autoblow_play, args=(time_ms,), daemon=True).start()
        return make_response("video_play")

    elif action == "video_pause":
        state["video_playing"] = False
        if state["enabled"]:
            threading.Thread(target=autoblow_pause, daemon=True).start()
        return make_response("video_pause")

    elif action == "shutdown":
        info("Backend shutting down via WS")
        def _exit_soon():
            time.sleep(0.3)
            os._exit(0)
        threading.Thread(target=_exit_soon, daemon=True).start()
        return make_response("shutdown")

    elif action == "video_seek":
        time_ms = int(msg.get("time_ms", 0) or 0)
        state["video_time_ms"] = time_ms
        if should_play() and state["current_script"]:
            threading.Thread(target=autoblow_play, args=(time_ms,), daemon=True).start()
        return make_response("video_seek")

    elif action == "arm":
        state["armed"] = True
        time_ms = int(msg.get("time_ms", 0) or 0)
        if should_play() and state["current_script"] and not state["uploading"]:
            threading.Thread(target=autoblow_play, args=(time_ms,), daemon=True).start()
        info("Script armed")
        broadcast(make_response("status"))
        return make_response("arm")

    elif action == "disarm":
        state["armed"] = False
        if state["enabled"]:
            threading.Thread(target=autoblow_pause, daemon=True).start()
        info("Script paused")
        broadcast(make_response("status"))
        return make_response("disarm")

    elif action == "enable":
        state["enabled"] = True
        is_playing = bool(msg.get("is_playing", False))
        scene_id = state["scene_id"]
        script = state["current_script"]

        def do_enable():
            broadcast_info("Plugin enabled")
            if scene_id and not script:
                load_scene(scene_id)
            elif scene_id and script:
                ensure_script_uploaded(scene_id, script, resume=is_playing)

        threading.Thread(target=do_enable, daemon=True).start()
        return make_response("enable")

    elif action == "disable":
        state["enabled"] = False
        threading.Thread(target=autoblow_pause, daemon=True).start()
        info("Plugin disabled")
        return make_response("disable")

    elif action == "select":
        if not state["enabled"]:
            return make_response("select", ok=False, error="plugin disabled")
        script_name = msg.get("script")
        matched = next((s for s in state["scripts"] if os.path.basename(s) == script_name), None)
        if not matched:
            return make_response("select", ok=False, error="script not found")

        state["current_script"] = matched
        with state_lock:
            state["uploading"] = True
        scene_id = state["scene_id"]
        is_playing = bool(msg.get("is_playing", False))

        def do_select():
            ensure_script_uploaded(scene_id, matched, resume=is_playing)

        threading.Thread(target=do_select, daemon=True).start()
        return make_response("select")
        return make_response("select")

    elif action == "reupload":
        if not state["enabled"]:
            return make_response("reupload", ok=False, error="plugin disabled")
        scene_id = state["scene_id"]
        script = state["current_script"]
        if not (scene_id and script):
            return make_response("reupload", ok=False, error="no scene/script")
        is_playing = bool(msg.get("is_playing", False))

        def do_reupload():
            ensure_script_uploaded(scene_id, script, resume=is_playing)

        threading.Thread(target=do_reupload, daemon=True).start()
        return make_response("reupload")

    elif action == "set_offset":
        try:
            state["offset_ms"] = int(msg.get("offset_ms", 0))
            save_offset()
            broadcast_info(f"Offset: {state['offset_ms']}ms")
            broadcast(make_response("status"))
            return make_response("set_offset")
        except Exception as e:
            return make_response("set_offset", ok=False, error=str(e))

    else:
        return {"ok": False, "error": f"unknown action: {action}"}

# ---------- Client thread ----------
def client_thread(conn, addr):
    logger.info("Client connected: %s", addr)
    try:
        request_data = b""
        conn.settimeout(5)
        while b"\r\n\r\n" not in request_data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            request_data += chunk
            if len(request_data) > 65536:
                break
        conn.settimeout(None)

        if b"\r\n\r\n" not in request_data:
            logger.warning("Incomplete HTTP request from %s", addr)
            conn.close()
            return
        if b"upgrade: websocket" not in request_data.lower():
            logger.warning("Non-WebSocket request from %s", addr)
            conn.close()
            return

        ws_handshake(conn, request_data)

        with clients_lock:
            clients.append(conn)

        while True:
            opcode, data = ws_recv_frame(conn)
            if opcode is None:
                break
            if opcode == 0x8:  # close
                break
            if opcode == 0x9:  # ping
                ws_send_pong(conn)
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode == 0x1 and data is not None:  # text frame
                response = handle_message(data)
                if response is not None:
                    try:
                        ws_send_frame(conn, json.dumps(response))
                    except Exception:
                        break
    except Exception as e:
        logger.error("client_thread error: %s", e)
    finally:
        with clients_lock:
            if conn in clients:
                clients.remove(conn)
            no_clients_left = len(clients) == 0

        if no_clients_left and state["enabled"] and state["video_playing"]:
            logger.info("Last client disconnected while playing → pausing device")
            state["video_playing"] = False
            state["armed"] = False
            threading.Thread(target=autoblow_pause, daemon=True).start()

        try:
            conn.close()
        except Exception:
            pass
        logger.info("Client disconnected: %s", addr)

# ---------- Server ----------
def main():
    load_plugin_config()
    load_offset()
    logger.info("Autoblow Sync Backend starting on ws://%s:%d", HOST, WS_PORT)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_sock.bind((HOST, WS_PORT))
    except OSError as e:
        logger.error("Port %d already in use – is the backend running? (%s)", WS_PORT, e)
        sys.exit(1)
    server_sock.listen(10)
    logger.info("Waiting for connections ...")

    try:
        while True:
            conn, addr = server_sock.accept()
            threading.Thread(target=client_thread, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        logger.info("Shutting down ...")
    finally:
        server_sock.close()

if __name__ == "__main__":
    main()
