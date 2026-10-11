#!/usr/bin/env python3
"""Unseen Mythology - daily Hindi YouTube Short bot (free stack).

Flow: Gemini (script) -> edge-tts / gTTS (Hindi voice) -> Pollinations (images)
      -> ffmpeg (zoom clips, captions, music, thumbnail) -> YouTube upload
      -> optional Facebook -> topics.csv history -> Telegram message.

Run `python main.py --selftest` to test only the ffmpeg assembly (no network, no upload).
"""
import asyncio
import csv
import datetime
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "out"
TOPICS_CSV = ROOT / "topics.csv"
BGM = ROOT / "assets" / "bgm.mp3"
FPS = 30
PAD = 0.35  # seconds of silence after each scene's narration
CAPTION_FONT = os.environ.get("CAPTION_FONT", "Noto Sans Devanagari")
VOICE = os.environ.get("TTS_VOICE", "hi-IN-MadhurNeural")


# ----------------------------------------------------------------- helpers
def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd[:4])} ...\n{r.stderr[-1500:]}")
    return r.stdout


def probe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def telegram(text):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[telegram skipped]", text)
        return
    import requests
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": text[:3800]}, timeout=30)
    except Exception as e:  # never let a notification crash the run
        print("telegram error:", e)


# ----------------------------------------------------------------- history
def load_used_topics():
    if not TOPICS_CSV.exists():
        return []
    with open(TOPICS_CSV, newline="", encoding="utf-8") as f:
        return [row["topic"] for row in csv.DictReader(f) if row.get("topic")]


def save_row(topic, source, title, link, status):
    new = not TOPICS_CSV.exists()
    with open(TOPICS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "topic", "source", "title", "link", "status"])
        w.writerow([datetime.date.today().isoformat(), topic, source, title, link, status])


# ----------------------------------------------------------------- script
PROMPT = """You are a researcher and Hindi storyteller for a YouTube Shorts channel "Unseen Mythology".

Pick ONE lesser-known story from Indian mythology, the Puranas, Mahabharata, Ramayana or other ancient texts.
Do NOT pick any of these already used topics: {used}

Rules:
- Name the real source text (e.g. "Skanda Purana", "Vana Parva"). Only cite a source you are confident about; otherwise choose another story.
- Write a ~60 second Hindi (Devanagari) storytelling script in exactly 8 scenes. Each scene narration is 16-20 Hindi words.
- Scene 1 must open with a mystery hook (a question or surprising line) in the first 3 seconds.
- Tone: calm, dramatic, respectful. Nothing that mocks any deity or community.
- For every scene give an English image_prompt: cinematic, painterly Indian art style, vertical composition, respectful, no text in image, no gore.

Return ONLY JSON:
{{"topic": "...", "source_text": "...", "title": "Hindi title max 70 chars", "description": "short Hindi description",
 "tags": ["..."], "scenes": [{{"narration_hindi": "...", "image_prompt": "..."}}]}}"""


def gen_script(used):
    import requests
    key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last_err = None
    for attempt in range(4):
        body = {
            "contents": [{"parts": [{"text": PROMPT.format(used=" | ".join(used) or "none yet")}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0},
        }
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=120)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            assert data["topic"] and data["source_text"] and data["title"]
            assert len(data["scenes"]) >= 5
            if data["topic"].strip().lower() in [u.strip().lower() for u in used]:
                raise ValueError("topic already used")
            return data
        except Exception as e:
            last_err = e
            print(f"script attempt {attempt + 1} failed: {e}")
            time.sleep(5)
    raise RuntimeError(f"could not generate script: {last_err}")


# ----------------------------------------------------------------- voice
async def _edge_tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="-5%").save(str(path))


def make_voice(text, path):
    try:
        asyncio.run(_edge_tts(text, path))
        if path.stat().st_size > 1000:
            return
    except Exception as e:
        print("edge-tts failed, falling back to gTTS:", e)
    from gtts import gTTS
    gTTS(text=text, lang="hi").save(str(path))


# ----------------------------------------------------------------- images
def make_gradient(path, idx):
    colors = ["0x1b1030", "0x2a1a0a", "0x0a1f2a", "0x2a0a14", "0x10261c", "0x241a33", "0x30200a", "0x0a1a30"]
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={colors[idx % len(colors)]}:s=1080x1920",
         "-frames:v", "1", str(path)])


def make_image(prompt, path, idx):
    import requests
    for attempt in range(3):
        try:
            seed = random.randint(1, 10**6)
            url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
                   + f"?width=1080&height=1920&nologo=true&seed={seed}")
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and len(r.content) > 20000 and r.headers.get("content-type", "").startswith("image"):
                path.write_bytes(r.content)
                return
        except Exception as e:
            print(f"image attempt {attempt + 1} failed: {e}")
        time.sleep(8)
    print(f"scene {idx + 1}: using gradient fallback image")
    make_gradient(path, idx)


# ----------------------------------------------------------------- assembly
def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def chunk_words(text, n=4):
    words = text.split()
    return [" ".join(words[i:i + n]) for i in range(0, len(words), n)] or [text]


def build_video(scenes, voice_files, image_files):
    """scenes: list of dicts with narration_hindi. Returns path of final.mp4 and thumb.jpg."""
    clips, wavs, srt_lines, t0, counter = [], [], [], 0.0, 1
    for i, sc in enumerate(scenes):
        wav = OUT / f"voice_{i}.wav"
        run(["ffmpeg", "-y", "-i", str(voice_files[i]), "-ar", "44100", "-ac", "2",
             "-af", f"apad=pad_dur={PAD}", str(wav)])
        dur = probe_duration(wav)
        speech = max(dur - PAD, 0.5)
        wavs.append(wav)

        frames = int(dur * FPS) + 1
        zoom_in = i % 2 == 0
        z = "min(zoom+0.0007,1.18)" if zoom_in else "if(eq(on,0),1.18,max(zoom-0.0007,1.0))"
        vf = (f"scale=1296:2304:force_original_aspect_ratio=increase,crop=1296:2304,"
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps={FPS},"
              f"format=yuv420p")
        clip = OUT / f"clip_{i}.mp4"
        run(["ffmpeg", "-y", "-i", str(image_files[i]), "-vf", vf, "-t", f"{dur:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS), str(clip)])
        clips.append(clip)

        chunks = chunk_words(sc["narration_hindi"])
        total_w = sum(len(c.split()) for c in chunks)
        t = t0
        for c in chunks:
            d = speech * len(c.split()) / total_w
            srt_lines += [str(counter), f"{srt_time(t)} --> {srt_time(t + d)}", c, ""]
            counter += 1
            t += d
        t0 += dur

    (OUT / "captions.srt").write_text("\n".join(srt_lines), encoding="utf-8")
    (OUT / "clips.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    (OUT / "voices.txt").write_text("".join(f"file '{w.name}'\n" for w in wavs))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "clips.txt", "-c", "copy", "video.mp4"], cwd=OUT)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "voices.txt", "-c", "copy", "voice.wav"], cwd=OUT)

    bgm = BGM
    if not bgm.exists():
        bgm = OUT / "bgm_synth.mp3"
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=110:duration=90",
             "-f", "lavfi", "-i", "sine=frequency=165:duration=90",
             "-f", "lavfi", "-i", "sine=frequency=220:duration=90",
             "-filter_complex", "[0][1][2]amix=inputs=3:normalize=0,lowpass=f=500,tremolo=f=0.2:d=0.5,volume=1.5",
             str(bgm)])

    style = (f"FontName={CAPTION_FONT},FontSize=14,Bold=1,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Alignment=2,MarginV=200")
    fc = (f"[0:v]subtitles=captions.srt:force_style='{style}'[v];"
          "[2:a]volume=0.12[bg];[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[a]")
    run(["ffmpeg", "-y", "-i", "video.mp4", "-i", "voice.wav", "-stream_loop", "-1", "-i", str(bgm),
         "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "final.mp4"], cwd=OUT)

    run(["ffmpeg", "-y", "-i", str(image_files[0]), "-vf",
         "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", "-frames:v", "1",
         "-q:v", "2", str(OUT / "thumb.jpg")])
    return OUT / "final.mp4", OUT / "thumb.jpg"


# ----------------------------------------------------------------- upload
def upload_youtube(video, thumb, data):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"])
    yt = build("youtube", "v3", credentials=creds)
    desc = (f"{data['description']}\n\nSource: {data['source_text']}\n\n"
            "#Shorts #Mythology #Hindi #UnseenMythology")
    body = {
        "snippet": {"title": f"{data['title'][:88]} #Shorts", "description": desc,
                    "tags": data.get("tags", [])[:15], "categoryId": "24",
                    "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
        "status": {"privacyStatus": os.environ.get("YT_PRIVACY", "public"),
                   "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg")).execute()
    except Exception as e:
        print("thumbnail not set (channel may need verification):", e)
    return f"https://www.youtube.com/shorts/{vid}"


def upload_facebook(video, data):
    page, token = os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_TOKEN")
    if not page or not token:
        return
    import requests
    try:
        with open(video, "rb") as f:
            r = requests.post(f"https://graph-video.facebook.com/v21.0/{page}/videos",
                              data={"title": data["title"], "description": data["description"],
                                    "access_token": token},
                              files={"source": f}, timeout=600)
        print("facebook:", r.status_code, r.text[:200])
    except Exception as e:
        print("facebook upload failed (ignored):", e)


# ----------------------------------------------------------------- main
def selftest():
    OUT.mkdir(exist_ok=True)
    scenes = [{"narration_hindi": "यह एक परीक्षण कहानी है जो बहुत पुरानी है और किसी ने नहीं सुनी"},
              {"narration_hindi": "दूसरे दृश्य में रहस्य गहरा होता है और कथा आगे बढ़ती है"},
              {"narration_hindi": "अंत में सच सामने आता है और सब कुछ बदल जाता है"}]
    voices, images = [], []
    for i, sc in enumerate(scenes):
        v = OUT / f"v{i}.mp3"
        secs = len(sc["narration_hindi"].split()) * 0.4
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={200 + 60 * i}:duration={secs}", str(v)])
        voices.append(v)
        img = OUT / f"img_{i}.png"
        make_gradient(img, i)
        images.append(img)
    final, thumb = build_video(scenes, voices, images)
    dur = probe_duration(final)
    info = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=width,height", "-of", "csv=p=0", str(final)]).strip()
    print(f"SELFTEST OK: {final} {info} {dur:.1f}s thumb={thumb.exists()}")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    OUT.mkdir(exist_ok=True)
    data = None
    try:
        used = load_used_topics()
        data = gen_script(used)
        scenes = data["scenes"][:8]
        voices, images = [], []
        for i, sc in enumerate(scenes):
            v = OUT / f"v{i}.mp3"
            make_voice(sc["narration_hindi"], v)
            voices.append(v)
            img = OUT / f"img_{i}.png"
            make_image(sc["image_prompt"], img, i)
            images.append(img)
            time.sleep(3)
        final, thumb = build_video(scenes, voices, images)
        link = upload_youtube(final, thumb, data)
        upload_facebook(final, data)
        save_row(data["topic"], data["source_text"], data["title"], link, "posted")
        telegram(f"✅ Short post ho gaya!\n\n{data['title']}\nSource: {data['source_text']}\n{link}")
    except Exception as e:
        if data:
            save_row(data["topic"], data["source_text"], data["title"], "", "failed")
        telegram(f"❌ Short fail hua: {e}")
        raise


if __name__ == "__main__":
    main()
