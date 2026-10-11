# Unseen Mythology - Daily Hindi Shorts Bot (100% free)

Roz subah 9 baje GitHub ka free server apne aap ek 60 sec ka Hindi Short banata hai
aur YouTube par upload karta hai. Aapka phone/PC on rakhne ki zaroorat nahi.

## Setup (phone se ho jayega)

### 1. GitHub repo
1. github.com par free account banao -> **New repository** (naam: `unseen-shorts`, Private ya Public).
2. Is folder ki saari files repo mein daalo: `main.py`, `requirements.txt`, `topics.csv`, `README.md`.
   Workflow file ke liye **Add file -> Create new file**, naam mein likho
   `.github/workflows/daily-short.yml` aur andar ka content paste karo.
3. (Optional) Apna music `assets/bgm.mp3` naam se upload karo. Na daalo to bot halka ambient music khud banata hai.

### 2. Keys (sab free)
- **GEMINI_API_KEY**: aistudio.google.com -> Get API key.
- **TELEGRAM_BOT_TOKEN**: Telegram mein @BotFather -> /newbot. Phir apne bot ko ek message bhejo.
- **TELEGRAM_CHAT_ID**: @userinfobot ko message karo, wo aapki ID bata dega.
- **YouTube (YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN)**: neeche step 3.

### 3. YouTube refresh token (phone browser mein)
1. console.cloud.google.com -> naya project -> **YouTube Data API v3** enable karo.
2. **OAuth consent screen** banao. Zaroori: status ko **"In production"** (Publish app) kar do.
   Testing mode mein token 7 din mein expire ho jata hai.
3. **Credentials -> Create OAuth client ID -> Web application**.
   Authorized redirect URI: `https://developers.google.com/oauthplayground`
   Client ID aur Client secret save kar lo.
4. developers.google.com/oauthplayground kholo -> upar gear icon -> **Use your own OAuth credentials** -> ID/secret daalo.
5. Scopes mein ye do daalo:
   `https://www.googleapis.com/auth/youtube.upload` aur `https://www.googleapis.com/auth/youtube`
   -> Authorize -> apne channel wale Google account se allow -> **Exchange authorization code for tokens**.
6. **Refresh token** copy kar lo.

### 4. GitHub Secrets
Repo -> Settings -> Secrets and variables -> Actions -> New repository secret. Ye naam bilkul aise hi rakho:
`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`
(Facebook optional: `FB_PAGE_ID`, `FB_PAGE_TOKEN`)

### 5. Pehla test
Repo -> **Actions** tab -> `daily-short` -> **Run workflow**. 10-20 minute mein Telegram par message aayega.
Pehle test ke liye `YT_PRIVACY` secret/env `private` rakh sakte ho (default `public` hai).

## Kaise kaam karta hai
Gemini script likhta hai -> edge-tts Hindi voice (fail ho to gTTS) -> Pollinations AI images ->
ffmpeg zoom effect, Hindi captions, music, thumbnail -> YouTube upload -> `topics.csv` mein record -> Telegram message.
Koi step fail ho to Telegram par error aata hai.

## Dhyan rakho
- Visuals AI video nahi, slow-zoom paintings jaisi hain (Veo paid hai).
- YouTube Studio mein AI/altered content ka disclosure khud check kar lo.
- Naya channel ho to pehle 1-2 videos `private` rakh kar quality dekho.
- Instagram isme nahi hai (public video URL chahiye). Facebook tab chalta hai jab FB secrets set hon.
