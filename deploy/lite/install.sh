#!/usr/bin/env bash
# Run only on the user's VPS, not inside the Codex execution environment.
set -Eeuo pipefail
umask 027
trap 'echo "Installation stopped at line $LINENO. Existing data and any before-upgrade copy are retained. See the error above." >&2' ERR
fail() { echo "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run this installer as root.'
[[ $# -eq 3 ]] || fail 'Usage: install.sh PUBLIC_IPV4 RELEASE_TAR_GZ SHA256'
server_ip=$1
archive=$2
expected=$3
[[ $server_ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || fail 'Invalid IPv4 address.'
[[ $expected =~ ^[a-f0-9]{64}$ ]] || fail 'Invalid release checksum.'
[[ -f $archive ]] || fail 'Release archive not found.'
printf '%s  %s\n' "$expected" "$archive" | sha256sum -c - >/dev/null
[[ -f /etc/os-release ]] || fail 'A systemd Ubuntu/Debian server is required.'
. /etc/os-release
[[ $ID == ubuntu || $ID == debian ]] || fail 'Supported: Ubuntu 24.04+ or Debian 12+.'
[[ -d /run/systemd/system ]] || fail 'A normal systemd VPS is required.'
[[ ! -e /opt/egrcrm/current || -L /opt/egrcrm/current ]] || fail 'Existing /opt/egrcrm/current is not a managed symlink; stopped.'
command -v python3 >/dev/null || fail 'Install Ubuntu 24.04+ or Debian 12+ with Python 3.'
python3 - "$server_ip" <<'PY'
import ipaddress,sys
if sys.version_info<(3,11):raise SystemExit('Python 3.11+ is required; use Ubuntu 24.04+ or Debian 12+.')
ip=ipaddress.ip_address(sys.argv[1])
if ip.version!=4 or not ip.is_global:raise SystemExit('A public IPv4 address is required.')
PY
# Stop before changing an unrelated web server.
command -v ss >/dev/null || fail 'The ss utility (iproute2) is required to check existing listeners safely.'
if [[ ! -f /etc/nginx/sites-available/egrcrm ]] && command -v ss >/dev/null; then
    for port in 80 443 8000; do
        [[ -z $(ss -H -ltn "sport = :$port") ]] || fail "Port $port is already occupied. Existing services were not changed."
    done
fi
if [[ -d /etc/nginx/sites-enabled ]]; then
    for site in /etc/nginx/sites-enabled/*; do
        [[ ! -e $site || $(basename "$site") == default || $(basename "$site") == egrcrm ]] || fail 'Existing nginx sites detected; automatic installation stopped.'
    done
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv python3-pip nginx ca-certificates curl openssl iproute2
if [[ $(awk '/SwapTotal:/ {print $2}' /proc/meminfo) == 0 ]]; then
    if [[ ! -e /var/egrcrm.swap ]] && [[ $(df -Pk /var | awk 'NR==2 {print $4}') -gt 3145728 ]]; then
        fallocate -l 1G /var/egrcrm.swap
        chmod 600 /var/egrcrm.swap
        mkswap /var/egrcrm.swap >/dev/null
        if swapon /var/egrcrm.swap; then
            printf '/var/egrcrm.swap none swap sw 0 0\n' >> /etc/fstab
        else
            echo 'Swap could not be enabled by this VPS. Installation continues; monitor available RAM.' >&2
        fi
    fi
fi
if ! id egrcrm >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/egrcrm --shell /usr/sbin/nologin egrcrm
else
    [[ $(getent passwd egrcrm | cut -d: -f6) == /var/lib/egrcrm ]] || fail 'An unrelated egrcrm user already exists.'
fi
install -d -m 0750 -o egrcrm -g egrcrm /var/lib/egrcrm /var/lib/egrcrm/backups
install -d -m 0755 /opt/egrcrm /opt/egrcrm/releases /var/www/egrcrm-acme
install -d -m 0700 /etc/egrcrm
if [[ -e /etc/egrcrm/egrcrm.env ]]; then
    grep -Fxq "ALLOWED_HOSTS=$server_ip,localhost,127.0.0.1" /etc/egrcrm/egrcrm.env || fail 'Existing deployment uses a different IP; configuration was not overwritten.'
else
    python3 - "$server_ip" <<'PY'
import os,secrets,sys
text='\n'.join(['DJANGO_SECRET_KEY='+secrets.token_urlsafe(64),'CRM_DATA_DIR=/var/lib/egrcrm','DEBUG=0','TRUST_PROXY_HTTPS=1','ALLOWED_HOSTS='+sys.argv[1]+',localhost,127.0.0.1','CSRF_TRUSTED_ORIGINS=https://'+sys.argv[1]])+'\n'
fd=os.open('/etc/egrcrm/egrcrm.env',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as stream:stream.write(text)
PY
fi
# New release paths never replace the database or secrets.
release="/opt/egrcrm/releases/$(date -u +%Y%m%dT%H%M%SZ)-${expected:0:12}"
mkdir -m 0755 "$release"
python3 - "$archive" "$release" <<'PY'
from pathlib import Path,PurePosixPath
import sys,tarfile,shutil
root=Path(sys.argv[2])
with tarfile.open(sys.argv[1],'r:gz') as archive:
    members=archive.getmembers()
    if sum(m.size for m in members)>200*1024*1024:raise SystemExit('Release exceeds 200 MB')
    for member in members:
        path=PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not member.isfile():raise SystemExit('Unsafe release member')
        target=root/path;target.parent.mkdir(parents=True,exist_ok=True)
        with archive.extractfile(member) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
PY
python3 -m venv "$release/.venv"
"$release/.venv/bin/pip" install --disable-pip-version-check -r "$release/requirements.txt"
# Existing application services are the only services stopped here.
for unit in egrcrm-worker.service egrcrm-web.service; do
    if systemctl cat "$unit" >/dev/null 2>&1; then systemctl stop "$unit"; fi
done
if [[ -f /var/lib/egrcrm/dev.sqlite3 ]]; then
    python3 - <<'PY'
from pathlib import Path
import sqlite3,datetime,os
source=Path('/var/lib/egrcrm/dev.sqlite3')
backup=source.parent/'backups'/('before-upgrade-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
with sqlite3.connect(source) as src,sqlite3.connect(backup) as dst:src.backup(dst)
os.chown(backup,source.stat().st_uid,source.stat().st_gid);backup.chmod(0o600)
PY
fi
set -a
. /etc/egrcrm/egrcrm.env
set +a
(cd "$release"; "$release/.venv/bin/python" manage.py migrate --noinput; "$release/.venv/bin/python" manage.py collectstatic --noinput)
chown -R egrcrm:egrcrm /var/lib/egrcrm
chmod -R a+rX "$release"
ln -sfn "$release" /opt/egrcrm/current
# Validate the same identity and working directory used by systemd before TLS setup.
runuser -u egrcrm -- sh -c 'cd /opt/egrcrm/current && .venv/bin/gunicorn config.wsgi:application --workers 1 --threads 2 --check-config'
cat > /usr/local/sbin/egrcrm-manage <<'EOF'
#!/bin/sh
set -eu
set -a
. /etc/egrcrm/egrcrm.env
set +a
cd /opt/egrcrm/current
exec runuser -u egrcrm -- /opt/egrcrm/current/.venv/bin/python manage.py "$@"
EOF
chmod 0700 /usr/local/sbin/egrcrm-manage
# HTTP serves only the ACME challenge until a trusted certificate exists.
python3 - "$release/deploy/lite/nginx-http.conf" "$server_ip" <<'PY'
from pathlib import Path
import sys
Path('/etc/nginx/sites-available/egrcrm').write_text(Path(sys.argv[1]).read_text().replace('@@HOST@@',sys.argv[2]))
PY
if [[ -L /etc/nginx/sites-enabled/default ]]; then rm /etc/nginx/sites-enabled/default; fi
ln -sfn /etc/nginx/sites-available/egrcrm /etc/nginx/sites-enabled/egrcrm
nginx -t
systemctl enable --now nginx
systemctl reload nginx
if command -v ufw >/dev/null && ufw status | grep '^Status: active' >/dev/null; then
    ufw allow 80/tcp
    ufw allow 443/tcp
fi
# Certbot 5.9 supports --ip-address and the shortlived ACME profile.
if [[ ! -x /opt/egrcrm-certbot/bin/certbot ]]; then python3 -m venv /opt/egrcrm-certbot; fi
/opt/egrcrm-certbot/bin/pip install --disable-pip-version-check --only-binary=:all: 'certbot==5.9.0'
/opt/egrcrm-certbot/bin/certbot certonly --non-interactive --agree-tos --register-unsafely-without-email --webroot -w /var/www/egrcrm-acme --ip-address "$server_ip" --preferred-profile shortlived --cert-name egrcrm-ip --keep-until-expiring
python3 - "$release/deploy/lite/nginx-https.conf" "$server_ip" <<'PY'
from pathlib import Path
import sys
Path('/etc/nginx/sites-available/egrcrm').write_text(Path(sys.argv[1]).read_text().replace('@@HOST@@',sys.argv[2]))
PY
nginx -t
systemctl reload nginx
cat > /usr/local/sbin/egrcrm-reload-nginx <<'EOF'
#!/bin/sh
set -eu
nginx -t
systemctl reload nginx
EOF
chmod 0700 /usr/local/sbin/egrcrm-reload-nginx
for unit in egrcrm-web.service egrcrm-worker.service egrcrm-cert-renew.service egrcrm-cert-renew.timer; do
    install -m 0644 "$release/deploy/lite/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now egrcrm-web.service egrcrm-worker.service egrcrm-cert-renew.timer
for attempt in $(seq 1 20); do
    if curl --fail --silent --show-error --max-time 10 --connect-to "$server_ip:443:127.0.0.1:443" "https://$server_ip/login/" >/dev/null; then
        echo "CRM HTTPS is ready: https://$server_ip"
        echo 'Create the CRM owner with: /usr/local/sbin/egrcrm-manage createsuperuser'
        echo 'Data directory: /var/lib/egrcrm (preserved on updates)'
        systemctl is-active egrcrm-web.service egrcrm-worker.service
        exit 0
    fi
    sleep 2
done
echo 'HTTPS health check failed. Collecting the actual service error:' >&2
bash "$release/deploy/lite/diagnose.sh" || true
fail 'Services were installed but the HTTPS health check failed. The service diagnostics are printed above.'
