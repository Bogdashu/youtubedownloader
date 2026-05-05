import os
import re
import json
import threading
import subprocess
import tempfile
import shutil
from flask import Flask, render_template, request, Response, stream_with_context
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

clients = set()

# ---------------- WebSocket ----------------
@sock.route('/ws')
def ws(ws):
    clients.add(ws)
    try:
        while True:
            if ws.receive() is None:
                break
    finally:
        clients.discard(ws)

def broadcast(data):
    dead = []
    for ws in clients:
        try:
            ws.send(json.dumps(data))
        except:
            dead.append(ws)
    for d in dead:
        clients.discard(d)

# ---------------- Main page ----------------
@app.route('/')
def index():
    return render_template('index.html')

# ---------------- Progress ----------------
progress_regex = re.compile(r'(\d{1,3}\.\d)%')

def parse_progress(line):
    match = progress_regex.search(line)
    if match:
        return float(match.group(1))
    return None

# ---------------- Download ----------------
@app.route('/download', methods=['POST'])
def download():
    data = request.json or {}
    url = data.get('url')
    quality = data.get('quality', '360')
    mode = data.get('mode', 'video')

    if not url:
        return {"error": "Нет URL"}, 400

    if mode == 'audio':
        format_code = "bestaudio/best"
    else:
        if quality == '1080':
            format_code = "bv*[height<=1080]+ba/b[height<=1080]"
        else:
            format_code = "best[height<=360]/best"

    def generate():
        tmpdir = tempfile.mkdtemp(prefix="yt_")

        try:
            outtmpl = os.path.join(tmpdir, "video.%(ext)s")

            cmd = [
                "python", "-m", "yt_dlp",
                "-f", format_code,
                "--merge-output-format", "mp4",
                "--no-playlist",
                "--newline",
                "--fixup", "force",
                "-o", outtmpl,
                url
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            def read_stderr():
                for line in process.stderr:
                    percent = parse_progress(line)
                    if percent is not None:
                        broadcast({
                            "type": "progress",
                            "value": round(percent, 1)
                        })

            threading.Thread(target=read_stderr, daemon=True).start()

            process.wait()

            if process.returncode != 0:
                raise RuntimeError("Ошибка скачивания")

            files = [
                os.path.join(tmpdir, f)
                for f in os.listdir(tmpdir)
                if f.startswith("video.")
            ]

            if not files:
                raise RuntimeError("Файл не найден")

            downloaded_file = max(files, key=os.path.getmtime)

            final_file = os.path.join(tmpdir, "final.mp4")

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-i", downloaded_file,
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                final_file
            ]

            result = subprocess.run(ffmpeg_cmd, capture_output=True)

            if result.returncode != 0:
                raise RuntimeError("Ошибка ffmpeg")

            with open(final_file, "rb") as f:
                while True:
                    chunk = f.read(1024 * 64)
                    if not chunk:
                        break
                    yield chunk

        except GeneratorExit:
            pass
        except Exception as e:
            yield json.dumps({"error": str(e)}).encode()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    headers = {
        "Content-Disposition": 'attachment; filename="video.mp4"',
        "Content-Type": "application/octet-stream"
    }

    return Response(stream_with_context(generate()), headers=headers)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 11643))
    app.run(host="0.0.0.0", port=port)
