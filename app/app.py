import json
import threading
import subprocess
import sys
from flask import Flask, request, render_template, Response
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

# ======================
# 🔒 ЛИМИТЫ
# ======================
active_downloads = 0
lock = threading.Lock()
MAX_DOWNLOADS = 1
active_ips = set()
ip_lock = threading.Lock()

# ======================
# 🌐 FRONTEND
# ======================
@app.route("/")
def index():
    return render_template("index.html")


# ======================
# 📡 WEBSOCKET PROGRESS
# ======================
clients = []

@sock.route("/ws")
def ws(ws):
    clients.append(ws)
    try:
        while True:
            ws.receive()
    except:
        if ws in clients:
            clients.remove(ws)


def send_progress(value):
    msg = json.dumps({"type": "progress", "value": value})
    for c in clients[:]:
        try:
            c.send(msg)
        except:
            if c in clients:
                clients.remove(c)


# ======================
# 🚀 СТРИМ С ПЕРЕМОТКОЙ
# ======================
def stream_ytdlp(url, mode, quality, user_ip):
    global active_downloads

    with lock:
        if active_downloads >= MAX_DOWNLOADS:
            yield b"SERVER BUSY"
            return
        active_downloads += 1
    
    with ip_lock:
        active_ips.add(user_ip)

    process = None
    
    try:
        if mode == "audio":
            fmt = "bestaudio[ext=m4a]/bestaudio"
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "-f", fmt,
                "-o", "-",
                "--no-playlist",
                "--no-part",
                "--buffer-size", "64k",
                url
            ]
        else:
            # Используем bestvideo+bestaudio для правильной перемотки
            if quality == 1080:
                fmt = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
            elif quality == 720:
                fmt = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]"
            else:
                fmt = f"best[height<={quality}]"
            
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "-f", fmt,
                "-o", "-",
                "--no-playlist",
                "--no-part",
                "--buffer-size", "64k",
                "--fixup", "force",  # Важно для перемотки
                url
            ]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0
        )
        
        total_sent = 0
        last_progress = 0
        
        while True:
            chunk = process.stdout.read(65536)  # 64KB как ты просил
            if not chunk:
                break
            
            total_sent += len(chunk)
            
            # Прогресс
            progress = min(int(total_sent / 500000), 95)
            if progress > last_progress:
                send_progress(progress)
                last_progress = progress
            
            yield chunk
        
        send_progress(100)
        
        if process.poll() is None:
            process.kill()
            
    except Exception as e:
        print(f"Error: {e}")
        yield b""
    finally:
        if process and process.poll() is None:
            process.kill()
        with lock:
            active_downloads -= 1
        with ip_lock:
            active_ips.discard(user_ip)


# ======================
# ⬇ DOWNLOAD ENDPOINT
# ======================
@app.route("/download", methods=["POST"])
def download():
    data = request.json
    url = data.get("url")
    quality = int(data.get("quality", 720))
    mode = data.get("mode", "video")
    user_ip = request.remote_addr

    if not url:
        return {"error": "NO URL"}, 400
    
    with ip_lock:
        if user_ip in active_ips:
            return {"error": "У вас уже есть загрузка"}, 429
    
    with lock:
        if active_downloads >= MAX_DOWNLOADS:
            return {"error": "Сервер занят"}, 429

    if mode == "audio":
        mimetype = "audio/mp4"
        filename = "audio.m4a"
    else:
        mimetype = "video/mp4"
        filename = "video.mp4"

    return Response(
        stream_ytdlp(url, mode, quality, user_ip),
        mimetype=mimetype,
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache",
            "Accept-Ranges": "bytes"  # Для перемотки
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=11643, threaded=False)
