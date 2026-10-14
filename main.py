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
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", "Charon")
_ALL_MODELS = []                      # every model name this Gemini key can use
_STATS = {"voice_gemini": 0, "voice_other": 0, "img_ai": 0, "img_reused": 0, "img_plain": 0}
_STATE = {"img_gemini_off": False, "last_image": None}


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
CATEGORIES = [
    "a little-known story of Lord Krishna, Lord Shiva (Mahadev), Lord Vishnu's avatars or Devi from the Puranas, Mahabharata or Ramayana",
    "asuras, rakshasas and demon kings of Hindu and Buddhist texts: their boons, their pride and their downfall",
    "a legendary sea monster or primordial beast from world mythology (Leviathan, Kraken, Jormungandr, Tiamat, Makara and similar)",
    "ancient Egypt: gods, mummies, pharaoh curses, the Book of the Dead and the journey through the afterlife",
    "a rare mythological creature seen in almost no videos (Nagas, Yokai, Wendigo, Basilisk, Simurgh, Chimera, Rakshasa-like spirits)",
    "Greek, Roman or Norse mythology: a forgotten monster, god or doomed hero",
    "ancient Mesopotamian, Persian and Abrahamic-tradition legends: Gilgamesh, Behemoth, Jinn, Nephilim, Anunnaki",
    "Aztec, Maya, Inca, African, Slavic or Celtic legends: underworld rulers, spirits and strange creatures",
]

STYLE = ("epic cinematic digital painting, dramatic volumetric lighting, rich deep colors, highly detailed, "
         "sharp focus, 9:16 vertical composition, no text, no letters, no watermark")

PROMPT = """You are a world-class researcher and a gripping Hindi storyteller for the YouTube Shorts channel "Unseen Mythology".
The channel covers mythology and ancient legends from ALL cultures: Indian gods and the Puranas, demons and creatures,
sea monsters like Leviathan, ancient Egypt and mummies, rare mythical beasts, and forgotten legends of the world.

TODAY'S THEME: {category}
Pick ONE specific story or being inside this theme that most people have never heard of. It must be surprising.
Do NOT pick any of these already used topics: {used}

Truth rules:
- Name the real source (text, tablet, papyrus, scripture or folklore collection). Cite only what you are confident about; otherwise pick another story.
- Separate what the source says from later folklore. Never invent facts. Treat every deity and religion with respect, never mock any faith.

Script rules (all narration in Hindi, Devanagari script):
- Exactly 10 scenes. Each scene narration = 12 to 16 Hindi words. Total about 130 to 150 words (about 55 to 60 seconds when spoken).
- It is ONE continuous spoken story. Sentences flow from scene to scene, no scene feels like a separate paragraph.
- Scene 1 is a killer hook: a startling question or shocking fact that makes people stop scrolling. Do NOT start with a greeting.
- Then build tension scene by scene, give one jaw-dropping fact, a twist near the end, and finish with one powerful closing line that stays in the mind.
- Plain, powerful spoken Hindi. Short sentences. No English words except proper names written in Devanagari. Write numbers in words.
- Use only Devanagari letters and these marks: comma, question mark, exclamation mark, danda. No quotes, no dashes, no ellipsis, no brackets, no emoji.

Image rules (this matters a lot):
- "visual_style": ONE English sentence describing the look and the main character(s) so every image stays consistent (age, clothing, skin, key features, colors, era).
- For each scene give an English "image_prompt" of 35 to 55 words: the subject, the action, the environment, the lighting, the camera angle and the mood. Repeat the main character's key look from visual_style in every scene where they appear.
- Describe real, concrete, cinematic scenes. No text or letters in the image, no gore, no nudity. Show deities with dignity.

Return ONLY JSON:
{"topic": "...", "category": "...", "source_text": "...", "title": "Hindi curiosity title, max 70 chars", "description": "2 short Hindi lines",
 "tags": ["8 to 12 tags mixing Hindi and English"], "visual_style": "...",
 "scenes": [{"narration_hindi": "...", "image_prompt": "..."}]}"""


def build_prompt(used):
    category = CATEGORIES[len(used) % len(CATEGORIES)]
    return (PROMPT.replace("{category}", category)
                  .replace("{used}", " | ".join(used[-60:]) or "none yet"))


def _gemini_models(key):
    """Ask Google which models this key can use; newest stable Flash first."""
    import re
    import requests
    names = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                         headers={"x-goog-api-key": key}, timeout=60)
        r.raise_for_status()
        for m in r.json().get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                names.append(m["name"].split("/", 1)[1])
        _ALL_MODELS[:] = names
    except Exception as e:
        print(f"model list failed: {e}")

    def ver(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0.0

    bad = ("lite", "image", "tts", "live", "audio", "embedding", "robotics", "computer", "vision", "learnlm", "gemma")
    flash = [n for n in names if "flash" in n and not any(b in n for b in bad)]
    stable = [n for n in flash if not any(x in n for x in ("preview", "exp"))]
    other = [n for n in flash if n not in stable]
    ordered = sorted(stable, key=ver, reverse=True) + sorted(other, key=ver, reverse=True)
    forced = os.environ.get("GEMINI_MODEL")
    out = ([forced] if forced else []) + ordered + ["gemini-flash-latest", "gemini-2.5-flash"]
    seen, res = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            res.append(n)
    print("models to try:", res[:6])
    return res[:6]


def gen_script(used):
    """Try every available Gemini model; if Google is busy (503) wait and go round again."""
    import requests
    key = os.environ["GEMINI_API_KEY"]
    models = _gemini_models(key)
    last_err = None
    for rnd in range(5):
        for model in list(models):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            body = {
                "contents": [{"parts": [{"text": build_prompt(used)}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0},
            }
            try:
                r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=120)
                if r.status_code == 404:
                    models.remove(model)
                    last_err = f"{model}: HTTP 404"
                    print(f"round {rnd + 1}: {model} not found, dropping it")
                    continue
                if r.status_code in (403, 429, 500, 502, 503, 504):
                    last_err = f"{model}: HTTP {r.status_code}"
                    print(f"round {rnd + 1}: {model} busy/unavailable ({r.status_code})")
                    continue
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(text)
                assert data["topic"] and data["source_text"] and data["title"]
                assert 7 <= len(data["scenes"]) <= 12, "wrong number of scenes"
                words = sum(len(x["narration_hindi"].split()) for x in data["scenes"])
                assert 90 <= words <= 220, f"script has {words} words"
                if data["topic"].strip().lower() in [u.strip().lower() for u in used]:
                    raise ValueError("topic already used")
                print(f"script made with {model}")
                return data
            except Exception as e:
                last_err = e
                print(f"round {rnd + 1}: {model} failed: {e}")
        if not models:
            break
        wait = 30 * (rnd + 1)
        print(f"all models busy, waiting {wait}s before next round")
        time.sleep(wait)
    raise RuntimeError(f"could not generate script: {last_err}")


# ----------------------------------------------------------------- voice
async def _edge_tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="-5%").save(str(path))


def _gemini_tts(text, path):
    """Natural Hindi voice from Gemini (free tier). Returns True on success."""
    import base64
    import re as _re
    import requests
    import wave
    key = os.environ.get("GEMINI_API_KEY")
    cands = [n for n in _ALL_MODELS if "tts" in n]
    cands.sort(key=lambda n: ("preview" in n, n))
    cands = (cands or ["gemini-2.5-flash-preview-tts"])[:2]
    prompt = ("Say in a deep, calm, confident documentary narrator voice, at a steady natural pace, "
              "in one smooth continuous flow with no long pauses and no hesitation, "
              "with dramatic emphasis on the key moments: " + text)
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}}}}
    for model in cands:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(4):
            try:
                r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=180)
                if r.status_code in (400, 403, 404):
                    print(f"gemini tts {model}: HTTP {r.status_code}, skipping model")
                    break
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = 15 * (attempt + 1)
                    print(f"gemini tts {model}: HTTP {r.status_code}, waiting {wait}s")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                pcm = base64.b64decode(part["data"])
                m = _re.search(r"rate=(\d+)", part.get("mimeType", ""))
                rate = int(m.group(1)) if m else 24000
                if len(pcm) < 4000:
                    raise ValueError("audio too short")
                with wave.open(str(path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes(pcm)
                return True
            except Exception as e:
                print(f"gemini tts {model} attempt {attempt + 1} failed: {e}")
                time.sleep(5)
    return False


def make_voice(text, path):
    if _gemini_tts(text, path):
        _STATS["voice_gemini"] += 1
        return
    _STATS["voice_other"] += 1
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


def _finish_image(raw_bytes, path):
    """Save, crop the bottom strip (removes any corner watermark) and fit to 1080x1920."""
    tmp = path.with_suffix(".raw")
    tmp.write_bytes(raw_bytes)
    run(["ffmpeg", "-y", "-i", str(tmp), "-vf",
         "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
         "crop=iw:ih*0.93:0:0,scale=1080:1920", "-frames:v", "1", str(path)])
    tmp.unlink(missing_ok=True)


def _gemini_image(prompt, path):
    import base64
    import requests
    key = os.environ.get("GEMINI_API_KEY")
    cands = [n for n in _ALL_MODELS if "image" in n and "flash" in n]
    cands.sort(key=lambda n: ("preview" in n, n))
    for model in cands[:2]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {"contents": [{"parts": [{"text": prompt + ". Vertical 9:16 portrait composition."}]}],
                "generationConfig": {"responseModalities": ["IMAGE"]}}
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=180)
            if r.status_code != 200:
                print(f"gemini image {model}: HTTP {r.status_code}")
                continue
            for part in r.json()["candidates"][0]["content"]["parts"]:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline:
                    data = base64.b64decode(inline["data"])
                    if len(data) > 20000:
                        _finish_image(data, path)
                        return True
        except Exception as e:
            print(f"gemini image {model} failed: {e}")
    return False


def make_image(prompt, path, idx):
    import requests
    if not _STATE["img_gemini_off"]:
        if _gemini_image(prompt, path):
            _STATS["img_ai"] += 1
            _STATE["last_image"] = path
            return
        _STATE["img_gemini_off"] = True      # not available on this key: do not retry every scene
    for attempt in range(5):
        try:
            seed = random.randint(1, 10**6)
            url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
                   + f"?width=864&height=1536&nologo=true&model=flux&enhance=false&seed={seed}")
            r = requests.get(url, timeout=150)
            if r.status_code == 200 and len(r.content) > 20000 and r.headers.get("content-type", "").startswith("image"):
                _finish_image(r.content, path)
                _STATS["img_ai"] += 1
                _STATE["last_image"] = path
                return
            print(f"image attempt {attempt + 1}: HTTP {r.status_code}")
        except Exception as e:
            print(f"image attempt {attempt + 1} failed: {e}")
        time.sleep(10 * (attempt + 1))
    last = _STATE["last_image"]
    if last and last.exists():
        print(f"scene {idx + 1}: reusing previous scene image")
        path.write_bytes(last.read_bytes())
        _STATS["img_reused"] += 1
        return
    print(f"scene {idx + 1}: using gradient fallback image")
    make_gradient(path, idx)
    _STATS["img_plain"] += 1


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


XF = 0.5        # cross-dissolve between scenes (seconds)
END_PAD = 0.9   # silence after the last word


def process_voice(raw, out):
    """One continuous narration: trim long pauses, even out loudness, add a short tail."""
    run(["ffmpeg", "-y", "-i", str(raw), "-ar", "44100", "-ac", "2", "-af",
         "silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_duration=0.45:"
         "stop_threshold=-45dB:stop_silence=0.18,loudnorm=I=-16:TP=-1.5:LRA=11,"
         f"apad=pad_dur={END_PAD}", str(out)])
    return out


def silence_mids(path):
    r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "silencedetect=noise=-38dB:d=0.12", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return [(a + b) / 2 for a, b in zip(starts, ends)]


def plan_timeline(scenes, speech_len, total_len, mids):
    """Spread caption chunks over the narration and snap them to the real pauses in the audio."""
    chunks = []
    for i, sc in enumerate(scenes):
        for c in chunk_words(sc["narration_hindi"], 4):
            chunks.append((i, c))
    weights = [max(len(c.replace(" ", "")), 1) for _, c in chunks]
    total_w, t, bounds = sum(weights), 0.0, [0.0]
    for w in weights:
        t += speech_len * w / total_w
        bounds.append(t)
    for k in range(1, len(bounds) - 1):
        tol = 1.2 if chunks[k - 1][0] != chunks[k][0] else 0.6
        near = min(mids, key=lambda m: abs(m - bounds[k]), default=None)
        if near is not None and abs(near - bounds[k]) <= tol:
            bounds[k] = near
    for k in range(1, len(bounds)):
        bounds[k] = max(bounds[k], bounds[k - 1] + 0.25)
    caps = [(bounds[k], bounds[k + 1], chunks[k][1]) for k in range(len(chunks))]
    starts = {}
    for k, (i, _) in enumerate(chunks):
        starts.setdefault(i, bounds[k])
    scene_starts = [starts[i] for i in range(len(scenes))] + [total_len]
    scene_starts[0] = 0.0
    durs = [max(scene_starts[i + 1] - scene_starts[i], 1.5) for i in range(len(scenes))]
    return caps, durs


def build_video(scenes, voice_raw, image_files):
    """scenes: dicts with narration_hindi. One continuous voice file. Returns final.mp4 and thumb.jpg."""
    voice = process_voice(voice_raw, OUT / "voice.wav")
    total = probe_duration(voice)
    speech_len = max(total - END_PAD, 1.0)
    caps, durs = plan_timeline(scenes, speech_len, total, silence_mids(voice))

    n = len(scenes)
    clips = []
    for i in range(n):
        length = durs[i] + XF
        frames = int(length * FPS) + 2
        mode = i % 4
        if mode == 0:
            zp = f"z='min(zoom+0.0006,1.2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        elif mode == 1:
            zp = f"z='if(eq(on,0),1.2,max(zoom-0.0006,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        elif mode == 2:
            zp = f"z=1.15:x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)'"
        else:
            zp = f"z=1.15:x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)'"
        vf = (f"scale=1296:2304:force_original_aspect_ratio=increase,crop=1296:2304,"
              f"zoompan={zp}:d={frames}:s=1080x1920:fps={FPS},"
              "eq=contrast=1.06:saturation=1.12,vignette=angle=PI/5,noise=alls=5:allf=t,format=yuv420p")
        clip = OUT / f"clip_{i}.mp4"
        run(["ffmpeg", "-y", "-i", str(image_files[i]), "-vf", vf, "-t", f"{length:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-r", str(FPS), str(clip)])
        clips.append(clip)

    srt, counter = [], 1
    for a, b, text in caps:
        srt += [str(counter), f"{srt_time(a)} --> {srt_time(b)}", text, ""]
        counter += 1
    (OUT / "captions.srt").write_text("\n".join(srt), encoding="utf-8")

    bgm = BGM
    if not bgm.exists():
        bgm = OUT / "bgm_synth.mp3"
        run(["ffmpeg", "-y",
             "-f", "lavfi", "-i", "anoisesrc=color=brown:duration=120:amplitude=0.5",
             "-f", "lavfi", "-i", "sine=frequency=55:duration=120",
             "-f", "lavfi", "-i", "sine=frequency=82.4:duration=120",
             "-f", "lavfi", "-i", "sine=frequency=110:duration=120",
             "-filter_complex",
             "[0]lowpass=f=260,volume=0.9[n];[1][2][3]amix=inputs=3:normalize=0,tremolo=f=0.15:d=0.6,volume=1.2[d];"
             "[n][d]amix=inputs=2:normalize=0,aecho=0.8:0.6:700:0.35,lowpass=f=1200,volume=1.5",
             str(bgm)])

    inputs = []
    for c in clips:
        inputs += ["-i", str(c.name)]
    inputs += ["-i", "voice.wav", "-stream_loop", "-1", "-i", str(bgm)]
    vi, ai = n, n + 1
    parts, acc, offset = [], "[0:v]", 0.0
    for k in range(1, n):
        offset += durs[k - 1]
        out_l = f"[x{k}]"
        parts.append(f"{acc}[{k}:v]xfade=transition=dissolve:duration={XF}:offset={offset:.3f}{out_l}")
        acc = out_l
    style = (f"FontName={CAPTION_FONT},FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=220")
    parts.append(f"{acc}subtitles=captions.srt:force_style='{style}'[v]")
    parts.append(f"[{ai + 0}:a]volume=0.10[bg]")
    parts.append(f"[{vi}:a][bg]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[a]")
    run(["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest", "final.mp4"], cwd=OUT)

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
        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds)
    desc = (f"{data['description']}\n\nSource: {data['source_text']}\n"
            "Ye katha granthon aur lok-kathaon par adharit hai.\n\n"
            "#Shorts #Mythology #Hindi #UnseenMythology #AncientLegends")
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
              {"narration_hindi": "तीसरे दृश्य में एक पुराना राज़ खुलता है और सब चौंक जाते हैं"},
              {"narration_hindi": "अंत में सच सामने आता है और सब कुछ बदल जाता है"}]
    voice = OUT / "voice_raw.mp3"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=16", str(voice)])
    images = []
    for i in range(len(scenes)):
        img = OUT / f"img_{i}.png"
        make_gradient(img, i)
        images.append(img)
    final, thumb = build_video(scenes, voice, images)
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
        scenes = data["scenes"][:12]
        full_text = " ".join(sc["narration_hindi"].strip() for sc in scenes)
        voice_raw = OUT / "voice_raw.audio"
        make_voice(full_text, voice_raw)
        look = data.get("visual_style", "")
        images = []
        for i, sc in enumerate(scenes):
            img = OUT / f"img_{i}.png"
            make_image(f"{sc['image_prompt']} {look}. {STYLE}", img, i)
            images.append(img)
            time.sleep(3)
        final, thumb = build_video(scenes, voice_raw, images)
        link = upload_youtube(final, thumb, data)
        upload_facebook(final, data)
        save_row(data["topic"], data["source_text"], data["title"], link, "posted")
        telegram(f"✅ Short post ho gaya!\n\n{data['title']}\nSource: {data['source_text']}\n{link}\n\n"
                 f"Images: {_STATS['img_ai']} AI, {_STATS['img_reused']} dohraye, {_STATS['img_plain']} saade | "
                 f"Awaaz: {_STATS['voice_gemini']} Gemini, {_STATS['voice_other']} backup")
    except Exception as e:
        if data:
            save_row(data["topic"], data["source_text"], data["title"], "", "failed")
        telegram(f"❌ Short fail hua: {e}")
        raise


if __name__ == "__main__":
    main()
