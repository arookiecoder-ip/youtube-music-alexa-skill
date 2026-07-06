# Plain VPS setup guide (beginner-friendly)

This guide walks through setting up the whole project — backend **and** the
Alexa skill — using a plain Python virtual environment + systemd, no Docker.
If you plan to run other projects on the same VPS too, [SETUP-DOCKER.md](SETUP-DOCKER.md)
is the better fit (keeps everything isolated). If you'd rather not deal with
Docker at all, this guide is for you.

If you get stuck, check the [Troubleshooting](README.md#troubleshooting)
section in the main README.

You'll need: a VPS with a public IP (Oracle Cloud's free tier works — Ubuntu
24.04 arm64 confirmed working), SSH access to it, and an Amazon developer
account (free) at [developer.amazon.com/alexa](https://developer.amazon.com/alexa).

---

## 1. Get the code onto the VPS

SSH into your VPS, then:

```bash
git clone https://github.com/arookiecoder-ip/youtube-music-alexa-skill.git
cd youtube-music-alexa-skill
```

> `lambda/` is **not** deployed to the VPS — those files get pasted into the
> Alexa developer console instead (steps 10+ below).

---

## 2. Install base dependencies

```bash
sudo apt update
sudo apt install -y python3-venv python3-full unzip docker.io

python3 -m venv ~/ytm
~/ytm/bin/pip install "flask[async]" ytmusicapi yt-dlp alexapy bgutil-ytdlp-pot-provider

# deno: JS runtime yt-dlp needs to solve YouTube's challenges
curl -fsSL https://deno.land/install.sh | sh -s -- -y
```

`docker.io` here is only used to run the small PO-token helper container in
the next step — you're not containerizing the app itself in this guide.

---

## 3. Start the PO token provider

```bash
sudo docker run -d --name bgutil-provider --restart unless-stopped \
  -p 127.0.0.1:4416:4416 brainicism/bgutil-ytdlp-pot-provider
```

This runs in the background and helps yt-dlp get past YouTube's bot checks.

---

## 4. Get your YouTube cookies

yt-dlp also needs a logged-in YouTube session to get past bot-blocking on
datacenter/VPS IPs.

⚠️ **Use a throwaway Google account for this, not your main one** — never
export cookies from an account you actually care about.

1. On your own PC (not the VPS), log into YouTube in your browser with that
   throwaway account.
2. Install a cookie-export extension — e.g. "Get cookies.txt LOCALLY"
   (Chrome/Firefox) — and use it on a youtube.com tab to download a
   `cookies.txt` file.
3. Copy it to the VPS and lock down its permissions:
   ```bash
   scp cookies.txt ubuntu@<your-vps-ip>:~/cookies.txt
   ```
   ```bash
   chmod 600 ~/cookies.txt
   ```

Cookies expire/rotate occasionally — if playback later starts failing with
bot-check errors, just repeat this step to refresh them.

**Smoke test** (must print a googlevideo URL):

```bash
~/ytm/bin/yt-dlp --cookies ~/cookies.txt --remote-components ejs:github \
  --extractor-args "youtube:player_client=mweb" -f ba -g J7p4bzqLvCw
```

---

## 5. Get your VPS's public hostname

```bash
curl ifconfig.me
```

Replace the dots with dashes and append `.sslip.io` — e.g. `1.2.3.4` →
`1-2-3-4.sslip.io`. sslip.io is a free wildcard DNS service: that hostname
resolves straight back to your IP, no domain registration needed.

(If you already own a real domain, point its DNS A record at this IP and use
that domain instead, everywhere a `.sslip.io` hostname is mentioned below.)

This hostname is your **API URL** — you'll need it again in step 10.

---

## 6. Open the firewall

- **Cloud firewall** (Oracle: VCN → subnet → Security List → Add Ingress
  Rules, source `0.0.0.0/0`, ports 80 and 443).
- **Instance firewall:**
  ```bash
  sudo iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
  sudo apt install -y iptables-persistent && sudo netfilter-persistent save
  ```

---

## 7. Set up the Flask server as a systemd service

Copy `flask-server/` to `~/flask-server` (it's already there if you cloned
the repo into your home directory — otherwise `cp -r flask-server ~/`).

Generate two secrets you'll need below:

```bash
openssl rand -base64 32 | tr -d '\n'   # this is your API_KEY
openssl rand -hex 32                    # this is your SECRET_KEY
```

Create `/etc/systemd/system/ytmusic.service`:

```bash
sudo nano /etc/systemd/system/ytmusic.service
```

Paste this in, filling in the placeholders (`REMOTE_*` lines are for the web
remote and optional — you can add them later):

```ini
[Unit]
Description=YT Music Flask server
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/flask-server
Environment=YTDLP_COOKIES=/home/ubuntu/cookies.txt
Environment=PUBLIC_BASE_URL=https://<your-hostname-from-step-5>
Environment=API_KEY=<the API_KEY you generated above>
Environment=AMAZON_DOMAIN=<amazon.in / amazon.com / ...>
Environment=SECRET_KEY=<the SECRET_KEY you generated above>
Environment=REMOTE_USER=<pick a username>
Environment=REMOTE_PASSWORD=<pick a strong password>
Environment=REMOTE_TOTP_SECRET=<base32 secret, optional 2FA>
Environment=PATH=/home/ubuntu/ytm/bin:/home/ubuntu/.deno/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/ubuntu/ytm/bin/python /home/ubuntu/flask-server/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`), then:

```bash
sudo chmod 600 /etc/systemd/system/ytmusic.service   # it holds secrets
sudo systemctl enable --now ytmusic
```

Check it's running: `sudo systemctl status ytmusic`.

---

## 8. Set up HTTPS (Caddy)

Install Caddy from its official apt repo:

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
  sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
  sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

Edit its config:

```bash
sudo nano /etc/caddy/Caddyfile
```

Replace the entire contents with (swap in your real hostname from step 5):

```
<your-hostname>.sslip.io {
    handle /alexa/proxy/* {
        reverse_proxy localhost:5001
    }
    handle {
        reverse_proxy localhost:5000
    }
}
```

Save and exit, then:

```bash
sudo systemctl reload caddy
```

The certificate appears within a minute — check with
`sudo journalctl -u caddy` for "certificate obtained successfully".

---

## 9. Cache cleanup cron (recommended)

```bash
(crontab -l 2>/dev/null | grep -v 'ytm_audio_cache' | grep -v 'yt-dlp'; \
 echo "*/30 * * * * find /tmp/ytm_audio_cache -type f -mmin +120 -delete"; \
 echo "0 5 * * * find ~/.cache/yt-dlp -type f -mtime +7 -delete 2>/dev/null") | crontab -
```

This sweeps audio files older than 2 hours (also clearing any leftover
partial downloads) and trims yt-dlp's own info cache weekly.

---

## 10. Verify the server works

```bash
curl https://<your-hostname>/find_stream_list/?key=<your-API_KEY>&query=blinding+lights
```

You should get back JSON with track info, not an error.

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
   API_KEY = "<the same API_KEY you put in ytmusic.service in step 7>"
   DEFAULT_API_URL = "https://<your-hostname-from-step-5>"
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
the console and `ytmusic.service`, or the server not being reachable (retry
the `curl` check from step 10).

Once it works in the Test tab, it's automatically available on any Echo
device signed into the **same Amazon account** used to create the skill — no
publishing needed. Just say: **"Alexa, ask music box to play blinding
lights."**
