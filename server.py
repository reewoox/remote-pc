"""
Remote Control - contrôle ton PC Windows depuis ton iPhone (Safari).

Lance :  python server.py            (ou double-clic sur start.bat)
Puis ouvre l'URL affichée (ou scanne le QR code) depuis l'iPhone,
sur le même réseau Wi-Fi.
"""
import argparse
import asyncio
import ctypes
import hmac
import io
import json
import os
import secrets
import socket
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

# --- DPI awareness AVANT tout le reste, sinon les coordonnées sont fausses ---
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import mss
from aiohttp import web, WSMsgType
from PIL import Image, ImageDraw
from pynput.keyboard import Controller as KController, Key, KeyCode
from pynput.mouse import Button, Controller as MController

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
CONFIG = os.path.join(HERE, "config.json")

mouse = MController()
keyboard = KController()

# --------------------------------------------------------------------------- #
# Configuration / authentification
# --------------------------------------------------------------------------- #
def load_config():
    cfg = {}
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(12)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    return cfg


FAILED = {}  # ip -> (count, first_ts)
MAX_FAILS, LOCK_SECONDS = 5, 120


def is_locked(ip):
    n, ts = FAILED.get(ip, (0, 0))
    if n >= MAX_FAILS and time.time() - ts < LOCK_SECONDS:
        return True
    if time.time() - ts >= LOCK_SECONDS:
        FAILED.pop(ip, None)
    return False


def register_fail(ip):
    n, ts = FAILED.get(ip, (0, time.time()))
    FAILED[ip] = (n + 1, ts)


# --------------------------------------------------------------------------- #
# Capture d'écran
# --------------------------------------------------------------------------- #
_tls = {}


def _sct():
    # mss doit être utilisé dans le thread qui l'a créé
    import threading
    tid = threading.get_ident()
    if tid not in _tls:
        _tls[tid] = mss.mss()
    return _tls[tid]


def list_monitors():
    mons = _sct().monitors
    # monitors[0] = tous les écrans combinés
    return [{"i": i, "w": m["width"], "h": m["height"], "left": m["left"], "top": m["top"]}
            for i, m in enumerate(mons)]


def draw_cursor(draw, x, y, s):
    pts = [(0, 0), (0, 17), (4.5, 13), (7.5, 20), (10, 19), (7, 12), (12.5, 12)]
    pts = [(x + px * s, y + py * s) for px, py in pts]
    draw.polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))
    draw.line(pts + [pts[0]], fill=(0, 0, 0), width=max(1, int(s)))


def grab(settings, last_sig):
    """Retourne (jpeg_bytes|None, sig, meta). None si l'image n'a pas changé."""
    sct = _sct()
    mons = sct.monitors
    idx = min(max(int(settings["monitor"]), 1 if len(mons) > 1 else 0), len(mons) - 1)
    mon = mons[idx]
    shot = sct.grab(mon)
    cx, cy = mouse.position
    rel_x, rel_y = cx - mon["left"], cy - mon["top"]
    sig = (zlib.crc32(shot.raw), int(rel_x), int(rel_y), settings["quality"], settings["maxw"], idx)
    meta = {
        "t": "f",
        "w": mon["width"], "h": mon["height"],
        "cx": rel_x / mon["width"], "cy": rel_y / mon["height"],
    }
    if sig == last_sig:
        return None, sig, meta

    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    maxw = int(settings["maxw"])
    if maxw and img.width > maxw:
        h = round(img.height * maxw / img.width)
        img = img.resize((maxw, h), Image.BILINEAR)
    if 0 <= rel_x < mon["width"] and 0 <= rel_y < mon["height"]:
        sx = img.width / mon["width"]
        draw_cursor(ImageDraw.Draw(img), rel_x * sx, rel_y * sx, max(1.0, img.width / 1400))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=int(settings["quality"]), optimize=False)
    return buf.getvalue(), sig, meta


# --------------------------------------------------------------------------- #
# Entrées clavier / souris
# --------------------------------------------------------------------------- #
BUTTONS = {"left": Button.left, "right": Button.right, "middle": Button.middle}
KEYS = {
    "ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift, "win": Key.cmd,
    "enter": Key.enter, "esc": Key.esc, "tab": Key.tab, "backspace": Key.backspace,
    "delete": Key.delete, "space": Key.space, "up": Key.up, "down": Key.down,
    "left": Key.left, "right": Key.right, "home": Key.home, "end": Key.end,
    "pageup": Key.page_up, "pagedown": Key.page_down, "insert": Key.insert,
    "printscreen": Key.print_screen, "capslock": Key.caps_lock,
    "volumeup": Key.media_volume_up, "volumedown": Key.media_volume_down,
    "mute": Key.media_volume_mute, "playpause": Key.media_play_pause,
    "next": Key.media_next, "prev": Key.media_previous,
    **{f"f{i}": getattr(Key, f"f{i}") for i in range(1, 13)},
}


def to_key(name):
    name = str(name)
    if name.lower() in KEYS:
        return KEYS[name.lower()]
    if len(name) == 1:
        return KeyCode.from_char(name.lower())
    raise ValueError(f"touche inconnue: {name}")


def monitor_rect(settings):
    mons = _sct().monitors
    idx = min(max(int(settings["monitor"]), 1 if len(mons) > 1 else 0), len(mons) - 1)
    return mons[idx]


def handle_input(msg, settings):
    t = msg.get("t")
    if t == "move":
        mouse.move(int(round(msg["dx"])), int(round(msg["dy"])))
    elif t == "moveto":
        m = monitor_rect(settings)
        x = m["left"] + min(max(float(msg["x"]), 0), 0.9999) * m["width"]
        y = m["top"] + min(max(float(msg["y"]), 0), 0.9999) * m["height"]
        mouse.position = (int(x), int(y))
    elif t == "click":
        mouse.click(BUTTONS.get(msg.get("b", "left"), Button.left), int(msg.get("n", 1)))
    elif t == "down":
        mouse.press(BUTTONS.get(msg.get("b", "left"), Button.left))
    elif t == "up":
        mouse.release(BUTTONS.get(msg.get("b", "left"), Button.left))
    elif t == "scroll":
        mouse.scroll(float(msg.get("dx", 0)), float(msg.get("dy", 0)))
    elif t == "text":
        keyboard.type(str(msg["s"])[:2000])
    elif t == "key":
        mods = [to_key(m) for m in msg.get("mods", [])]
        key = to_key(msg["k"])
        for m in mods:
            keyboard.press(m)
        try:
            keyboard.press(key)
            keyboard.release(key)
        finally:
            for m in reversed(mods):
                keyboard.release(m)


# --------------------------------------------------------------------------- #
# Serveur web
# --------------------------------------------------------------------------- #
async def ws_handler(request):
    ip = request.remote
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 20)
    await ws.prepare(request)

    # --- authentification ---
    try:
        first = await ws.receive(timeout=10)
        data = json.loads(first.data) if first.type == WSMsgType.TEXT else {}
    except Exception:
        data = {}
    token = request.app["token"]
    if is_locked(ip):
        await ws.send_json({"t": "error", "msg": "Trop d'essais. Réessaie dans 2 minutes."})
        await ws.close()
        return ws
    if data.get("t") != "auth" or not hmac.compare_digest(str(data.get("token", "")), token):
        register_fail(ip)
        await ws.send_json({"t": "error", "msg": "Code incorrect."})
        await ws.close()
        return ws
    FAILED.pop(ip, None)
    print(f"[+] Connecté : {ip}")

    executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    settings = {"quality": 60, "maxw": 1600, "fps": 20, "monitor": 1}
    ack = asyncio.Event()
    ack.set()
    state = {"force": True}

    monitors = await loop.run_in_executor(executor, list_monitors)
    await ws.send_json({"t": "hello", "monitors": monitors, "host": socket.gethostname()})

    async def streamer():
        last_sig = None
        while not ws.closed:
            await ack.wait()
            ack.clear()
            t0 = time.perf_counter()
            if state["force"]:
                last_sig, state["force"] = None, False
            try:
                jpg, sig, meta = await loop.run_in_executor(executor, grab, dict(settings), last_sig)
            except Exception as e:  # écran verrouillé, UAC, etc.
                print("[!] capture:", e)
                await asyncio.sleep(0.5)
                ack.set()
                continue
            if jpg is None:
                ack.set()
            else:
                last_sig = sig
                await ws.send_json(meta)
                await ws.send_bytes(jpg)
            wait = 1 / max(1, settings["fps"]) - (time.perf_counter() - t0)
            await asyncio.sleep(max(wait, 0.005))

    task = asyncio.create_task(streamer())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                m = json.loads(msg.data)
            except ValueError:
                continue
            t = m.get("t")
            if t == "ack":
                ack.set()
            elif t == "settings":
                for k in ("quality", "maxw", "fps", "monitor"):
                    if k in m:
                        settings[k] = int(m[k])
                settings["quality"] = min(max(settings["quality"], 10), 95)
                settings["fps"] = min(max(settings["fps"], 1), 60)
                state["force"] = True
                ack.set()
            else:
                try:
                    await loop.run_in_executor(executor, handle_input, m, settings)
                except Exception as e:
                    print("[!] entrée:", e)
    finally:
        task.cancel()
        executor.shutdown(wait=False)
        print(f"[-] Déconnecté : {ip}")
    return ws


async def index(request):
    return web.FileResponse(os.path.join(STATIC, "index.html"),
                            headers={"Cache-Control": "no-cache"})


_icon_cache = {}


async def icon(request):
    if "png" not in _icon_cache:
        img = Image.new("RGB", (180, 180), (20, 22, 30))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((30, 40, 150, 120), 10, outline=(90, 160, 255), width=8)
        d.rectangle((75, 120, 105, 138), fill=(90, 160, 255))
        d.rounded_rectangle((55, 136, 125, 146), 4, fill=(90, 160, 255))
        draw_cursor(d, 82, 62, 2.2)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        _icon_cache["png"] = buf.getvalue()
    return web.Response(body=_icon_cache["png"], content_type="image/png")


async def manifest(request):
    return web.json_response({
        "name": "Remote PC", "short_name": "Remote PC", "start_url": "/",
        "display": "standalone", "background_color": "#000000", "theme_color": "#000000",
        "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png"}],
    })


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def print_banner(url, token):
    print("\n" + "=" * 60)
    print("  Remote PC est lancé !")
    print(f"  Sur l'iPhone (même Wi-Fi), ouvre : {url}")
    print(f"  Code d'accès : {token}")
    print("=" * 60)
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(f"{url}#{token}")
        qr.make(fit=True)
        print("\n  Ou scanne ce QR code avec l'appareil photo de l'iPhone :\n")
        qr.print_ascii(invert=True)
    except Exception:
        pass
    print("  Astuce : dans Safari, Partager > « Sur l'écran d'accueil »")
    print("  pour l'utiliser comme une vraie app.  Ctrl+C pour arrêter.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--new-code", action="store_true", help="génère un nouveau code d'accès")
    args = ap.parse_args()

    if args.new_code and os.path.exists(CONFIG):
        os.remove(CONFIG)
    cfg = load_config()

    app = web.Application()
    app["token"] = cfg["token"]
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/icon.png", icon)
    app.router.add_get("/manifest.json", manifest)

    print_banner(f"http://{lan_ip()}:{args.port}", cfg["token"])
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
