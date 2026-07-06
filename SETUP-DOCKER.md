# Docker setup guide (beginner-friendly)

This guide walks through setting up the whole project — backend **and** the
Alexa skill — using **Docker Compose** for the server. Use this if you plan
to run other projects on the same VPS too, since Docker keeps everything
isolated. If you'd rather not use Docker, see [SETUP-VPS.md](SETUP-VPS.md)
instead (plain Python venv + systemd).

If you get stuck, check the [Troubleshooting](README.md#troubleshooting)
section in the main README.

You'll need: a VPS with a public IP (Oracle Cloud's free tier works — Ubuntu
24.04), SSH access to it, and an Amazon developer account (free) at
[developer.amazon.com/alexa](https://developer.amazon.com/alexa).

---

## 1. Get the code onto the VPS

SSH into your VPS, then:

```bash
git clone https://github.com/arookiecoder-ip/youtube-music-alexa-skill.git
cd youtube-music-alexa-skill
```

This brings over everything needed to run the server: `flask-server/`,
`docker-compose.yml`, `Caddyfile`.

> `lambda/` is **not** deployed here — those files get pasted into the Alexa
> developer console instead. See [SETUP-ALEXA.md](SETUP-ALEXA.md).

---

## 2. Install Docker (if not already installed)

```bash
sudo apt update
sudo apt install -y docker.io
```

Install the Docker Compose plugin (Ubuntu's apt repo doesn't carry it, so we
grab the binary directly):

```bash
mkdir -p ~/.docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m) \
  -o ~/.docker/cli-plugins/docker-compose
chmod +x ~/.docker/cli-plugins/docker-compose
docker compose version   # should print a version number
```

Let your user run Docker without `sudo`:

```bash
sudo usermod -aG docker $USER
```

Then **log out and reconnect over SSH** (or run `newgrp docker`) so the group
change takes effect. Confirm with:

```bash
docker ps   # should work without "permission denied"
```

---

## 3. Get your VPS's public hostname

Get the VPS's public IP:

```bash
curl ifconfig.me
```

Then replace the dots with dashes and append `.sslip.io` — e.g. `1.2.3.4` →
`1-2-3-4.sslip.io`. sslip.io is a free wildcard DNS service: that hostname
resolves straight back to your IP, no domain registration needed.

(If you already own a real domain, you can point its DNS A record at this IP
and use that domain instead, everywhere a `.sslip.io` hostname is mentioned
below.)

This hostname is your **API URL** — write it down, you'll need it again in
[SETUP-ALEXA.md](SETUP-ALEXA.md).

---

## 4. Open the firewall

Open TCP 80 + 443 in **both** the cloud firewall and the instance itself:

- **Cloud firewall** (Oracle: VCN → subnet → Security List → Add Ingress
  Rules, source `0.0.0.0/0`, ports 80 and 443).
- **Instance firewall:**
  ```bash
  sudo iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
  sudo apt install -y iptables-persistent && sudo netfilter-persistent save
  ```

---

## 5. Create your secrets file

`.env.example` (in the repo) is just a template — safe, checked into git, no
real values. Copy it and fill in the blanks:

```bash
cp .env.example .env
nano .env
```

Fill in at least:

- `PUBLIC_BASE_URL` — the hostname from step 3, e.g.
  `https://1-2-3-4.sslip.io`
- `API_KEY` — generate one with `openssl rand -base64 32 | tr -d '\n'`. You'll
  use this **same value** in the Alexa console later.
- `AMAZON_DOMAIN` — the Amazon site your account lives on (`amazon.in`,
  `amazon.com`, etc.)
- `SECRET_KEY` — generate with `openssl rand -hex 32`
- `REMOTE_USER` / `REMOTE_PASSWORD` — a username/password for the web remote
  (optional but recommended)

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X` in `nano`).

---

## 6. Get your YouTube cookies

yt-dlp needs a logged-in YouTube session to get past YouTube's bot-blocking on
datacenter/VPS IPs.

⚠️ **Use a throwaway Google account for this, not your main one** — never
export cookies from an account you actually care about.

1. On your own PC (not the VPS), log into YouTube in your browser with that
   throwaway account.
2. Install a cookie-export extension — e.g. "Get cookies.txt LOCALLY"
   (Chrome/Firefox) — and use it on a youtube.com tab to download a
   `cookies.txt` file.
3. Copy it to the VPS:
   ```bash
   scp cookies.txt ubuntu@<your-vps-ip>:~/cookies.txt
   ```

Cookies expire/rotate occasionally — if playback later starts failing with
bot-check errors, just repeat this step to refresh them.

---

## 7. Set up HTTPS (Caddy)

Caddy runs as its own **container** here (defined in `docker-compose.yml`) —
no separate install needed. Just edit the repo's `Caddyfile`:

```bash
nano Caddyfile
```

Replace `<your-ip-with-dashes>.sslip.io` with your real hostname from step 3.
Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

---

## 8. Build and start everything

```bash
docker compose up -d --build
```

This starts two containers: the Flask app (`ytmusic`) and Caddy, sharing a
Docker network. Caddy automatically requests a Let's Encrypt certificate for
your hostname — check progress with:

```bash
docker compose logs -f caddy
```

Look for "certificate obtained successfully". This can take up to a minute.

To update the server after future code changes:

```bash
docker compose up -d --build ytmusic
```

---

## 9. Verify it's working

```bash
curl https://<your-hostname>/find_stream_list/?key=<your-API_KEY>&query=blinding+lights
```

You should get back JSON with track info, not an error.

---

## 10. Cache cleanup cron (optional but recommended)

The audio cache lives in a Docker volume. Add a cron job on the VPS host to
sweep old files:

```bash
(crontab -l 2>/dev/null | grep -v 'ytm_audio_cache'; \
 echo "*/30 * * * * docker exec ytmusic find /tmp/ytm_audio_cache -type f -mmin +120 -delete") | crontab -
```

---

## 11. Create the Alexa skill

1. Go to [developer.amazon.com/alexa](https://developer.amazon.com/alexa) and
   sign in.
2. **Create Skill** → give it a name (e.g. "Music box") → choose **Custom**
   model → choose **Alexa-hosted (Python)** → Create.

This gives you a free, ready-to-use Lambda function — no separate AWS account
needed.

---

## 12. Set up the interaction model (what you can say)

1. In the console, go to the **Build** tab.
2. In this repo, open `skill-package/interactionModels/custom/` — there's one
   JSON file per locale (en-US, en-GB, en-IN, en-AU, en-CA).
3. For your locale (e.g. `en-IN.json` if your Echo is set to English (India)),
   open the JSON editor in the console (Build tab → JSON Editor) and paste in
   the matching file's contents.
4. Click **Build Model** (or **Build Skill**) and wait for it to finish.
5. Confirm the invocation name is **"music box"** (Invocation → Skill
   Invocation Name).

---

## 13. Turn on Audio Player

Still in the **Build** tab: go to **Interfaces**, find **Audio Player**, and
turn it **ON**, then save. Without this, the skill can't send playback
directives — music simply won't play.

---

## 14. Add your server's URL and API key to the skill

1. Go to the **Code** tab in the console.
2. Open (or create) `lambda/api_key.py` in the code editor.
3. Set its contents to:
   ```python
   API_KEY = "<the same API_KEY value you put in .env in step 5>"
   DEFAULT_API_URL = "https://<your-server-hostname-from-step-3>"
   ```
4. Don't click Deploy yet — one more step below.

---

## 15. Copy over the rest of the skill code

Still in the **Code** tab, make sure these files match what's in this repo's
`lambda/` folder (copy-paste each one over the console's version):

- `lambda_function.py`
- `requirements.txt` (replace entirely — the original upstream version has a
  dependency pin that breaks Alexa-hosted deploys)
- `data.py`
- `api_key.py` (the one you just edited in step 14)
- `mediaUtils/player.py`
- `models/player_models.py` (create this file/folder if it doesn't exist)

Once everything's copied over, click **Deploy** (top right).

---

## 16. Test it

1. Go to the **Test** tab, enable testing (switch from "Skill testing is
   disabled" to "Development").
2. Type or say: `ask music box to play blinding lights`
3. You should see a response like "Playing Blinding Lights by The Weeknd" and
   hear it start playing.

If it doesn't work, check [Troubleshooting](README.md#troubleshooting) in the
main README — most first-time issues are the API key not matching between
the console and `.env`, or the server not being reachable (retry the `curl`
check from step 9).

Once it works in the Test tab, it's automatically available on any Echo
device signed into the **same Amazon account** used to create the skill — no
publishing needed. Just say: **"Alexa, ask music box to play blinding
lights."**
