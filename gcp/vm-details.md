# GCP VM Reference — autotrade-497413

## Project
- **Project ID:** autotrade-497413

## VM Instance
| Field | Value |
|-------|-------|
| Name | instance-20260525-143559 |
| Zone | us-central1-a |
| Machine Type | e2-medium (2 vCPU shared, 4 GB RAM) |
| OS | Debian 12 (Bookworm) |
| Boot Disk | 50 GB persistent SSD |
| Status | RUNNING |
| Created | 2026-05-25 |

## Network
| Field | Value |
|-------|-------|
| Static External IP | **34.45.46.60** |
| Static IP Name | autotrade-static-ip |
| Internal IP | 10.128.0.2 |
| Region | us-central1 |
| Network Tier | PREMIUM |

## Access

### Web Dashboard (desktop browser)
- URL: `http://34.45.46.60`
- Username: `admin`
- Password: `autotrade2026`
- Full Hermes chat terminal + sessions, logs, config

### Telegram (phone)
- Bot token stored in `~/.hermes/.env`
- Send messages to the bot → Hermes responds via gpt-4o-mini
- Gateway runs as systemd service (`hermes-gateway`)

## Systemd Services
| Service | Purpose | Manages |
|---------|---------|---------|
| `hermes-dashboard` | Hermes web UI | Bound to localhost:9119, proxied by Caddy |
| `hermes-gateway` | Telegram bot | polling mode, auto-restarts |
| `caddy` | Reverse proxy + basic auth | Port 80 → localhost:9119 |

```bash
# Check service status
sudo systemctl status hermes-dashboard hermes-gateway caddy

# Restart a service
sudo systemctl restart hermes-gateway
```

## Installed Software
| Software | Version | Install Type | Notes |
|----------|---------|--------------|-------|
| Docker CE | 29.5.2 | apt | Includes Compose plugin |
| Caddy | 2.11.3 | apt | Reverse proxy, /etc/caddy/Caddyfile |
| Hermes Agent | 0.14.0 | direct | Binary: `~/.local/bin/hermes`, Data: `~/.hermes/` |
| Claude Code | 2.1.150 | npm global | CLI: `claude`, auth: Pro subscription |
| Node.js | 22.22.2 | nodesource apt | |
| Python | 3.11.2 | system | |
| Git | 2.39.5 | apt | |

## Hermes Config
- Model: `gpt-4o-mini` (direct OpenAI, `api.openai.com/v1`)
- API key + Telegram token: `~/.hermes/.env`
- Config: `~/.hermes/config.yaml`
- Claude Code skill: built-in, delegates coding tasks to `claude` CLI

## Planned Docker Compose Stack
| Container | Purpose |
|-----------|---------|
| `trading-app` | FastAPI backend + static HTML/CSS/JS + Playwright/Chromium |
| `postgres:16` | Database |
| `redis:7` | Cache / message broker |

## SSH
```bash
gcloud compute ssh instance-20260525-143559 --project=autotrade-497413 --zone=us-central1-a
```

## Useful gcloud Commands
```bash
# Start/stop VM
gcloud compute instances start instance-20260525-143559 --project=autotrade-497413 --zone=us-central1-a
gcloud compute instances stop instance-20260525-143559 --project=autotrade-497413 --zone=us-central1-a

# Check status
gcloud compute instances describe instance-20260525-143559 --project=autotrade-497413 --zone=us-central1-a
```
