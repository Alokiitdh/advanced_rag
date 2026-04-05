# Deployment Guide — Advanced RAG System

This guide walks you through deploying the RAG backend on WSL (Ubuntu) or any Linux server. Every command here works identically on WSL, Hetzner, DigitalOcean, AWS EC2, or any VPS.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Dockerfile](#2-dockerfile)
3. [Docker Ignore](#3-docker-ignore)
4. [Production Docker Compose](#4-production-docker-compose)
5. [Nginx Reverse Proxy](#5-nginx-reverse-proxy)
6. [SSL / HTTPS](#6-ssl--https)
7. [Systemd Services](#7-systemd-services)
8. [Database Backups](#8-database-backups)
9. [Firewall](#9-firewall)
10. [Monitoring (Prometheus + Grafana)](#10-monitoring-prometheus--grafana)
11. [Exposing to the Internet](#11-exposing-to-the-internet)
12. [CI/CD with GitHub Actions](#12-cicd-with-github-actions)
13. [Deploying to a Real VPS](#13-deploying-to-a-real-vps)

---

## 1. Prerequisites

### On WSL (Ubuntu)

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
# Log out and back in for group change to take effect

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Nginx
sudo apt install -y nginx

# Install mkcert (for local SSL)
sudo apt install -y libnss3-tools
curl -JLO "https://dl.filippo.io/mkcert/latest?for=linux/amd64"
chmod +x mkcert-v*-linux-amd64
sudo mv mkcert-v*-linux-amd64 /usr/local/bin/mkcert

# Verify installations
docker --version
nginx -v
uv --version
```

---

## 2. Dockerfile

Create `Dockerfile` in the project root:

```dockerfile
# ---- Stage 1: Builder ----
FROM python:3.13-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies (cached layer)
RUN uv sync --frozen --no-dev --no-editable

# ---- Stage 2: Runtime ----
FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY src/ ./src/

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Default command (overridden in docker-compose for worker)
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 3. Docker Ignore

Create `.dockerignore` in the project root:

```
.venv/
__pycache__/
*.pyc
.env
.git/
.gitignore
tests/
*.md
.pytest_cache/
ingestion_error.log
.python-version
```

---

## 4. Production Docker Compose

Create `docker-compose.prod.yml`:

```yaml
services:

  # --- Application ---

  api:
    build: .
    command: uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      - postgres
      - redis
      - qdrant
    restart: always
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  worker:
    build: .
    command: python -m arq src.worker.tasks.WorkerSettings
    env_file: .env
    depends_on:
      - postgres
      - redis
      - qdrant
    restart: always

  # --- Infrastructure ---

  qdrant:
    image: qdrant/qdrant
    ports:
      - "6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage
    restart: always

  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: raguser
      POSTGRES_PASSWORD: ragpass
      POSTGRES_DB: ragdb
    ports:
      - "5433:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: always
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U raguser -d ragdb"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7
    command: redis-server --appendonly yes
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: always

volumes:
  qdrant_data:
  postgres_data:
  redis_data:
```

Update `.env` for Docker networking (containers use service names, not localhost):

```env
POSTGRES_URL=postgresql://raguser:ragpass@postgres:5432/ragdb
REDIS_URL=redis://redis:6379
QDRANT_URL=http://qdrant:6333
OPENROUTER_API_KEY=sk-or-v1-your-key-here
JWT_SECRET=generate-a-random-secret-here
```

> **Important:** In Docker Compose, services talk to each other by service name (`postgres`, `redis`, `qdrant`), not `localhost`. The Postgres port is `5432` (internal), not `5433` (host-mapped).

### Build and Run

```bash
# Build the image
docker compose -f docker-compose.prod.yml build

# Start everything
docker compose -f docker-compose.prod.yml up -d

# Check logs
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f worker

# Stop everything
docker compose -f docker-compose.prod.yml down
```

---

## 5. Nginx Reverse Proxy

Nginx sits in front of your API, handling SSL, load balancing, and file upload limits.

Create `/etc/nginx/sites-available/rag-api`:

```nginx
server {
    listen 80;
    server_name localhost;  # Replace with your domain in production

    client_max_body_size 50M;  # Allow large file uploads

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts for slow RAG generation
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 120s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        access_log off;  # Don't log health checks
    }
}
```

### Enable the config

```bash
# Enable the site
sudo ln -s /etc/nginx/sites-available/rag-api /etc/nginx/sites-enabled/

# Remove default site
sudo rm /etc/nginx/sites-enabled/default

# Test config
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx
```

Now `http://localhost` (port 80) forwards to your API on port 8000.

---

## 6. SSL / HTTPS

### Local Development (WSL) — Self-signed with mkcert

```bash
# Install local CA
mkcert -install

# Generate certs
mkcert localhost 127.0.0.1 ::1
# Creates: localhost+2.pem and localhost+2-key.pem

# Move certs
sudo mkdir -p /etc/nginx/ssl
sudo mv localhost+2.pem /etc/nginx/ssl/cert.pem
sudo mv localhost+2-key.pem /etc/nginx/ssl/key.pem
```

Update `/etc/nginx/sites-available/rag-api`:

```nginx
server {
    listen 80;
    server_name localhost;
    return 301 https://$host$request_uri;  # Redirect HTTP to HTTPS
}

server {
    listen 443 ssl;
    server_name localhost;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 120s;
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### Production (Real Domain) — Let's Encrypt

```bash
# Install Certbot
sudo apt install -y certbot python3-certbot-nginx

# Get a free SSL certificate (replace with your domain)
sudo certbot --nginx -d yourdomain.com

# Auto-renewal is set up automatically. Test it:
sudo certbot renew --dry-run
```

---

## 7. Systemd Services

Create systemd unit files so Docker Compose starts automatically on boot.

### Create the service file

```bash
sudo nano /etc/systemd/system/rag-api.service
```

```ini
[Unit]
Description=RAG API (Docker Compose)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/YOUR_USERNAME/advanced_rag
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
```

> Replace `YOUR_USERNAME` with your actual username and adjust the path.

### Enable and start

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable on boot
sudo systemctl enable rag-api

# Start now
sudo systemctl start rag-api

# Check status
sudo systemctl status rag-api

# View logs
sudo journalctl -u rag-api -f
```

### Useful commands

```bash
sudo systemctl restart rag-api   # Restart
sudo systemctl stop rag-api      # Stop
sudo systemctl start rag-api     # Start
```

---

## 8. Database Backups

### PostgreSQL Backup Script

Create `scripts/backup.sh`:

```bash
#!/bin/bash
# Daily PostgreSQL backup

BACKUP_DIR="/home/$USER/backups/postgres"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
CONTAINER_NAME="advanced_rag-postgres-1"

mkdir -p "$BACKUP_DIR"

# Dump the database
docker exec "$CONTAINER_NAME" pg_dump -U raguser ragdb | gzip > "$BACKUP_DIR/ragdb_$TIMESTAMP.sql.gz"

# Keep only last 7 days of backups
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -delete

echo "[$(date)] Backup completed: ragdb_$TIMESTAMP.sql.gz"
```

### Qdrant Backup Script

Create `scripts/backup_qdrant.sh`:

```bash
#!/bin/bash
# Qdrant collection snapshot

BACKUP_DIR="/home/$USER/backups/qdrant"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# Create snapshot via Qdrant API
curl -s -X POST "http://localhost:6333/collections/documents/snapshots" > /dev/null

# List and download latest snapshot
SNAPSHOT=$(curl -s "http://localhost:6333/collections/documents/snapshots" | python3 -c "import sys,json; snaps=json.load(sys.stdin)['result']; print(snaps[-1]['name'])" 2>/dev/null)

if [ -n "$SNAPSHOT" ]; then
    curl -s "http://localhost:6333/collections/documents/snapshots/$SNAPSHOT" -o "$BACKUP_DIR/qdrant_$TIMESTAMP.snapshot"
    echo "[$(date)] Qdrant backup completed: qdrant_$TIMESTAMP.snapshot"
fi

# Keep only last 7 days
find "$BACKUP_DIR" -name "*.snapshot" -mtime +7 -delete
```

### Schedule with Cron

```bash
# Make scripts executable
chmod +x scripts/backup.sh scripts/backup_qdrant.sh

# Edit crontab
crontab -e

# Add these lines (runs daily at 2 AM):
0 2 * * * /home/YOUR_USERNAME/advanced_rag/scripts/backup.sh >> /home/YOUR_USERNAME/backups/backup.log 2>&1
0 3 * * * /home/YOUR_USERNAME/advanced_rag/scripts/backup_qdrant.sh >> /home/YOUR_USERNAME/backups/backup.log 2>&1
```

### Restore from Backup

```bash
# PostgreSQL restore
gunzip < backups/postgres/ragdb_20260404.sql.gz | docker exec -i advanced_rag-postgres-1 psql -U raguser ragdb
```

---

## 9. Firewall

```bash
# Install UFW (usually pre-installed on Ubuntu)
sudo apt install -y ufw

# Default policies
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (important — don't lock yourself out!)
sudo ufw allow 22/tcp

# Allow HTTP and HTTPS (Nginx)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable firewall
sudo ufw enable

# Check status
sudo ufw status verbose
```

> **Do NOT expose ports 5433, 6333, 6379, 8000 directly.** Users access through Nginx (port 80/443) only. Database ports should stay internal.

---

## 10. Monitoring (Prometheus + Grafana)

### Add to docker-compose.prod.yml

Append these services to your `docker-compose.prod.yml`:

```yaml
  prometheus:
    image: prom/prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    restart: always

  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin  # Change in production!
    restart: always
```

Add volumes:

```yaml
volumes:
  qdrant_data:
  postgres_data:
  redis_data:
  prometheus_data:
  grafana_data:
```

### Create `prometheus.yml`

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: "rag-api"
    metrics_path: /health
    static_configs:
      - targets: ["api:8000"]

  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
```

### Access Dashboards

```bash
# Start monitoring
docker compose -f docker-compose.prod.yml up -d prometheus grafana

# Access:
# Prometheus: http://localhost:9090
# Grafana:    http://localhost:3000 (admin / admin)
```

### Grafana Setup

1. Open `http://localhost:3000`
2. Login: `admin` / `admin`
3. Add Data Source → Prometheus → URL: `http://prometheus:9090`
4. Import Dashboard → ID `1860` (Node Exporter) for system metrics

---

## 11. Exposing to the Internet

### Option A: Cloudflare Tunnel (Recommended — Free)

```bash
# Install cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# Login to Cloudflare (opens browser)
cloudflared tunnel login

# Create a tunnel
cloudflared tunnel create rag-api

# Configure the tunnel
cat > ~/.cloudflared/config.yml << EOF
tunnel: <TUNNEL_ID>
credentials-file: /home/$USER/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: rag.yourdomain.com
    service: http://localhost:80
  - service: http_status:404
EOF

# Add DNS record
cloudflared tunnel route dns rag-api rag.yourdomain.com

# Run the tunnel
cloudflared tunnel run rag-api
```

Make it persistent with systemd:

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

### Option B: Ngrok (Quick Testing)

```bash
# Install
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc > /dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok

# Authenticate (get token from ngrok.com)
ngrok config add-authtoken <YOUR_TOKEN>

# Expose your app
ngrok http 80
```

---

## 12. CI/CD with GitHub Actions

Create `.github/workflows/deploy.yml` in your repo:

```yaml
name: Test & Deploy

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_USER: raguser
          POSTGRES_PASSWORD: ragpass
          POSTGRES_DB: ragdb
        ports:
          - 5433:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

      redis:
        image: redis:7
        ports:
          - 6379:6379

      qdrant:
        image: qdrant/qdrant
        ports:
          - 6333:6333

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Install dependencies
        run: uv sync

      - name: Start API and worker
        env:
          POSTGRES_URL: postgresql://raguser:ragpass@localhost:5433/ragdb
          REDIS_URL: redis://localhost:6379
          QDRANT_URL: http://localhost:6333
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          JWT_SECRET: test-secret
        run: |
          uv run python -m uvicorn src.main:app --port 8000 &
          uv run python -m arq src.worker.tasks.WorkerSettings &
          sleep 15

      - name: Run tests
        env:
          POSTGRES_URL: postgresql://raguser:ragpass@localhost:5433/ragdb
          REDIS_URL: redis://localhost:6379
          QDRANT_URL: http://localhost:6333
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          JWT_SECRET: test-secret
        run: uv run python -m pytest tests/test_pipeline.py -v

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'

    steps:
      - name: Deploy to server via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SERVER_SSH_KEY }}
          script: |
            cd ~/advanced_rag
            git pull origin main
            docker compose -f docker-compose.prod.yml build
            docker compose -f docker-compose.prod.yml up -d
            echo "Deployed at $(date)"
```

### Setup GitHub Secrets

Go to your repo → Settings → Secrets and variables → Actions → Add:

| Secret | Value |
|--------|-------|
| `OPENROUTER_API_KEY` | Your OpenRouter API key |
| `SERVER_HOST` | Your server IP (for deploy step) |
| `SERVER_USER` | SSH username |
| `SERVER_SSH_KEY` | SSH private key |

---

## 13. Deploying to a Real VPS

When you're ready to go live, everything you learned on WSL transfers directly.

### Step 1: Rent a Server

| Provider | Cheapest Plan | Specs |
|----------|--------------|-------|
| Hetzner | ~$4/month (CX22) | 2 vCPU, 4GB RAM |
| DigitalOcean | $6/month | 1 vCPU, 1GB RAM |
| Oracle Cloud | Free forever | 4 ARM cores, 24GB RAM |

For the reranker model, you need **at least 4GB RAM**. Hetzner CX22 or Oracle free tier recommended.

### Step 2: Initial Server Setup

```bash
# SSH into your server
ssh root@YOUR_SERVER_IP

# Create a non-root user
adduser deploy
usermod -aG sudo deploy
usermod -aG docker deploy

# Setup SSH key auth
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh

# Switch to new user
su - deploy
```

### Step 3: Install Dependencies

```bash
# Same as WSL prerequisites
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2 nginx
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 4: Clone and Deploy

```bash
# Clone your repo
git clone git@github.com:Alokiitdh/advanced_rag.git
cd advanced_rag

# Create .env with production values
nano .env

# Build and start
docker compose -f docker-compose.prod.yml up -d

# Setup Nginx (same config as Section 5, but with your domain)
sudo nano /etc/nginx/sites-available/rag-api
sudo ln -s /etc/nginx/sites-available/rag-api /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Get SSL certificate
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com

# Setup firewall
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# Setup backups (Section 8)
# Setup monitoring (Section 10)
# Setup systemd (Section 7)
```

### Step 5: Verify

```bash
# Check all containers are running
docker compose -f docker-compose.prod.yml ps

# Check API health
curl https://yourdomain.com/health

# Check connections
curl https://yourdomain.com/check-connections

# Test registration
curl -X POST https://yourdomain.com/register \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "securepassword"}'
```

---

## Quick Reference

### Common Commands

```bash
# Start everything
docker compose -f docker-compose.prod.yml up -d

# Stop everything
docker compose -f docker-compose.prod.yml down

# View logs (follow)
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f worker

# Rebuild after code changes
docker compose -f docker-compose.prod.yml build && docker compose -f docker-compose.prod.yml up -d

# Scale ARQ workers
docker compose -f docker-compose.prod.yml up -d --scale worker=3

# Check resource usage
docker stats

# Database shell
docker exec -it advanced_rag-postgres-1 psql -U raguser ragdb

# Redis CLI
docker exec -it advanced_rag-redis-1 redis-cli
```

### Architecture in Production

```
Internet
   │
   ▼
Cloudflare Tunnel / Domain DNS
   │
   ▼
┌──────────────────────┐
│  Nginx (port 80/443) │  ← SSL termination, rate limiting
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  FastAPI (port 8000)  │  ← 4 Uvicorn workers
└──────────┬───────────┘
           │
    ┌──────┼──────────┐
    ▼      ▼          ▼
 Qdrant  Postgres   Redis ← ARQ Worker(s)
```
