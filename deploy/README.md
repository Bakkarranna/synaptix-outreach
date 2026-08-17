# Deploying the Console to an always-free Oracle Cloud VM

This runbook puts the **Synaptix Studio Console** on Oracle Cloud's **Always
Free** tier: a real 24/7 VM where both the dashboard (`web`) and the outreach
engine (`daemon`) run headless, exactly like they do on the studio's Windows
machine — but nobody's laptop has to be on. Cost: **$0/month** (see caveats
below).

```
        browser                          VM (Oracle Always Free, ARM)
   ┌──────────────┐                 ┌───────────────────────────────────────┐
   │  dashboard   │── :8000 ───────▶│  docker compose  ┌────────┐  ┌──────┐ │
   └──────────────┘   (or tunnel)   │                  │  web   │  │daemon│ │
                                    │                  └───┬────┘  └──┬───┘ │
                                    │              volume oo-data (SQLite)  │
                                    └───────────────────────────────────────┘
```

The `daemon` does all the work (discovery → qualification → email → replies);
`web` just serves the console. Both share one SQLite file on a Docker volume,
so a restart of either picks up exactly where it left off.

---

## Fast path (recommended) — three commands total

Once the VM exists (step 1 below), the rest is nearly zero-typing:

```bash
# 1. on the STUDIO machine (Windows / Git Bash): build a bundle that carries
#    the pre-filled onboarding config (credentials included - own server only)
bash scripts/package.sh --with-config

# 2. upload it to the VM (same machine)
scp -i ~/.ssh/<your-key> dist/synaptix-studio-console-*.tar.gz ubuntu@<PUBLIC_IP>:~

# 3. on the VM: extract and run the one-shot installer - it installs Docker,
#    asks you only for the public IP, starts the stack, and shows the log
mkdir -p ~/console && tar xzf synaptix-studio-console-*.tar.gz -C ~/console
cd ~/console && bash deploy/install-on-vm.sh
```

The installer applies `deploy/onboard_config.json` (the pre-filled onboarding
config) automatically — you never type a single secret on the VM. Steps 2–5
below are the manual version of what the installer does, kept as reference.

---

## 1. Create the VM (Oracle Cloud, ~10 minutes)

1. **Sign up** at <https://www.oracle.com/cloud/free/> — a debit/credit card is
   asked for identity verification but the Always Free resources cost nothing.
   Pick a home region with **Ampere (ARM) capacity** — if instance creation
   later fails with "out of capacity", try another region or retry in an hour.
2. **Compute → Instances → Create instance.** Set:
   - **Image:** Ubuntu 24.04 (or 22.04) — the ARM build. (Oracle Linux works
     too, but the steps below assume Ubuntu.)
   - **Shape:** *Ampere A1.Flex* → **4 OCPU / 24 GB RAM** (Always Free eligible).
     This is ~30× the headroom the stack needs; the extra cores are what make
     the ML + LLM pipeline feel instant.
   - **Boot volume:** 50–100 GB (Always Free includes up to 200 GB of block
     storage).
   - **SSH keys:** add your public key so you can log in.
3. **Open the firewall.** In the instance's **VCN → Security Lists → Default
   Security List → Add Ingress Rule**: allow **TCP 8000** from `0.0.0.0/0`
   (or, better, from the studio's static IP). This is the only port you need.
4. Note the instance's **public IP** — you'll use it everywhere below.

> **Two always-free caveats worth knowing.** (a) Oracle reserves the right to
> reclaim **idle** always-free instances; this stack runs real work 24/7, so it
> is never "idle" — treat it as a non-issue, but keep backups anyway. (b) The
> Always Free shape limits are per-account, not per-instance — you could run
> several of these boxes on the same allowance.

## 2. Log in and install Docker

```bash
ssh -i ~/.ssh/<your-key> ubuntu@<PUBLIC_IP>

sudo apt-get update
curl -fsSL https://get.docker.com | sudo sh          # installs Docker + compose plugin
sudo usermod -aG docker "$USER"
# log out and back in (or run: newgrp docker)
docker --version && docker compose version
```

## 3. Get the app onto the VM

*Fast path: this is exactly what `package.sh --with-config` + the installer do.*

**Option A — packaged bundle (recommended, no git needed):** on the studio
machine run `bash scripts/package.sh` (add `--with-config` to ship the
pre-filled onboarding config for the fast path), then upload and unpack:

```bash
# from the studio machine
scp -i ~/.ssh/<your-key> dist/synaptix-studio-console-*.tar.gz ubuntu@<PUBLIC_IP>:~

# back on the VM (the bundle extracts flat, so make a home for it first)
mkdir -p ~/console && tar xzf synaptix-studio-console-*.tar.gz -C ~/console
cd ~/console
```

**Option B — git checkout:** clone your private repo of this project and
`cd` into it.

Either way you should now be in a directory containing `compose.prod.yml`,
`scripts/`, and `deploy/`.

## 4. Configure: secrets + environment

The stack reads two local files, both **gitignored** — never commit them.

```bash
cp deploy/env.example .env
nano .env          # or vim
```

Fill in `.env`:

| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DJANGO_ALLOWED_HOSTS` | **The VM's public IP** (and your domain if you use one). Oracle gives the box a private `10.x` IP, but the browser hits the public one — Django rejects any Host header it doesn't recognise, so this is the most common first-boot mistake. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://your.domain` only if you add the Cloudflare Tunnel (step 7). |
| `DJANGO_DEBUG` | keep `0`. |

Then the onboarding config — this is what provisions the fresh database
headlessly (campaign → LLM → mailbox → signature → BetterContact → Lead
account), with the same live credential checks the interactive wizard runs:

```bash
cp deploy/onboard_config.example.json deploy/onboard_config.json
nano deploy/onboard_config.json
```

Replace every `REPLACE_WITH_...` with the real values (the campaign text is
already filled with the studio's copy — edit freely). Two fields deserve
attention:

- **`account.password`** — this is the **Lead's dashboard sign-in**. On a
  headless box there is no terminal to set a password later, so the onboarder
  requires it. `role: "lead"` makes the account a superuser (full console +
  team + config access); `"operator"` makes it staff-only.
- **`signature`** — if you remove the key entirely, the step stays
  unsatisfied and the daemon won't start (it's a required gate). Keep it, or
  set it to `""` to decline the sign-off deliberately.

## 5. First boot

```bash
docker compose -f compose.prod.yml up -d --build
docker compose -f compose.prod.yml logs -f daemon
```

On the first boot the daemon chain runs `migrate → setup_crm →
onboard_noninteractive → rundaemon`. You should see, in order:

```
[1] campaign ok -> pk 1
[2] llm ok -> openai_compatible:nvidia/nemotron-...
[3] mailbox ok -> outreach@synaptixstudio.com
[4] signature ok
[5] bettercontact ok
[6] account ok -> main_synaptixstudio (lead, PK)
missing steps: NONE — fully onboarded
```

Then the cycle starts logging real work. Now open
**http://<PUBLIC_IP>:8000/dashboard/** and sign in with the account email +
`account.password`. It is the exact same console you use on the studio
machine — Overview, Deals, live log, Team, Config, Django Admin.

If instead you see `missing steps: ...` or a crash-loop, see **Troubleshooting**.

## 6. Day-to-day operations

| Task | Command |
|---|---|
| Watch the engine | `docker compose -f compose.prod.yml logs -f daemon` |
| Backup (whole store) | `bash deploy/backup.sh` — a consistent SQLite snapshot in `deploy/backups/` |
| Backup on a schedule | `crontab -e` → `17 3 * * * cd ~/console && bash deploy/backup.sh` (keep a copy off the VM too) |
| Update the app | `bash deploy/update.sh` (pulls + rebuilds, then tails the log) |
| Stop / start | `docker compose -f compose.prod.yml down` / `up -d` |
| After a VM reboot | nothing — `restart: unless-stopped` + Docker's systemd unit bring the stack back |

A backup is just the `db-<timestamp>.sqlite3` file; to restore, stop the stack,
copy it onto the volume as `data/db.sqlite3`, and start again. The engine is
designed so a restart resumes mid-flight (pipeline state lives in the DB).

## 7. Recommended: put it behind Cloudflare (free, HTTPS, no open ports)

Cloudflare Tunnel gives you `https://console.synaptixstudio.com` in front of
the dashboard with **no open ports** (you can then delete the `:8000` ingress
rule) and a free TLS cert. The studio already routes its mailbox through
Cloudflare, so this fits.

```bash
# on the VM
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
# …or just grab the binary: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
sudo cloudflared service install
cloudflared tunnel login          # one-time browser auth
cloudflared tunnel create console
cloudflared tunnel route dns console console.synaptixstudio.com
```

Then in the tunnel config (`~/.cloudflared/config.yml`):

```yaml
tunnel: console
credentials-file: /root/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: console.synaptixstudio.com
    service: http://localhost:8000
  - service: http_status:404
```

…and set in `.env` (then `docker compose -f compose.prod.yml up -d web`):

```
DJANGO_ALLOWED_HOSTS=<PUBLIC_IP>,console.synaptixstudio.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://console.synaptixstudio.com
```

You can go further with **Cloudflare Access** (free for up to 50 users) to put
an extra login in front of the whole console — handy for the team.

## Security checklist

- [ ] `.env` and `deploy/onboard_config.json` are gitignored and never
      committed or bundled (`scripts/package.sh` excludes both).
- [ ] `DJANGO_ALLOWED_HOSTS` is tightened to your IP/hostname, not `*`.
- [ ] Ingress to `:8000` is restricted to your IP — or removed entirely once
      the tunnel is up.
- [ ] `account.password` is a real, strong password; treat
      `deploy/onboard_config.json` (which holds the LLM, mailbox and
      BetterContact credentials) like a backup of the database.
- [ ] Backups run on a schedule and a copy lives off the VM.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ImproperlyConfigured: DJANGO_SECRET_KEY must be set…` | `.env` missing or `DJANGO_SECRET_KEY` empty — `cp deploy/env.example .env` and fill it. |
| Container exits, logs show `missing steps: …` | Onboarding incomplete — fill the missing sections in `deploy/onboard_config.json` (a `signature` key removed entirely is a common cause) and `docker compose -f compose.prod.yml up -d --build`. |
| `[2] LLM VERIFY FAILED` / `[3] MAILBOX VERIFY FAILED` | Bad key/credentials in the config — the onboarder refuses to start a broken engine (same as the wizard). Fix and restart. |
| Browser says "DisallowedHost" or gets a 400 | `DJANGO_ALLOWED_HOSTS` doesn't include the public IP / domain you're hitting. |
| Can't reach `:8000` at all | Oracle security-list ingress rule missing (step 1.3), or a host firewall. |
| First page loads but no live data | You're looking at a stale browser tab — the Overview polls a JSON endpoint; give it one poll cycle. |
| `[6] account ok` but you can't sign in | Use the account **email** + `account.password`, not the mailbox address. |

---

**Still want it even cheaper than $0?** You can't — that's the point of this
runbook. And if Oracle ever disappoints (capacity, reclaim, account friction),
the same steps work verbatim on any ~$5 VPS (Hetzner, DigitalOcean, Contabo) —
everything above is plain Docker Compose.
