#!/usr/bin/env bash
# Read-only application diagnostics. Never print Environment/secret files.
set -u
exec 2>&1
printf '\n=== Server and resources ===\n'
uname -sr
free -m
df -h /
printf '\n=== Service state ===\n'
systemctl status egrcrm-web.service egrcrm-worker.service --no-pager --full || true
printf '\n=== Web startup log ===\n'
journalctl -u egrcrm-web.service -n 100 --no-pager -o short-iso || true
printf '\n=== Collector startup log ===\n'
journalctl -u egrcrm-worker.service -n 45 --no-pager -o short-iso || true
printf '\n=== Application path permissions ===\n'
namei -l /opt/egrcrm/current/.venv/bin/gunicorn || true
printf '\n=== Data directory permissions ===\n'
stat -c '%A %U:%G %n' /var/lib/egrcrm /var/lib/egrcrm/dev.sqlite3 || true
printf '\n=== Listener on port 8000 ===\n'
ss -ltnp 'sport = :8000' || true
printf '\n=== Python and Gunicorn as app user ===\n'
runuser -u egrcrm -- /opt/egrcrm/current/.venv/bin/python --version || true
runuser -u egrcrm -- /opt/egrcrm/current/.venv/bin/gunicorn --version || true
printf '\n=== Django configuration check as app user ===\n'
/usr/local/sbin/egrcrm-manage check || true
printf '\n=== WSGI import check as app user ===\n'
(
    set -a
    . /etc/egrcrm/egrcrm.env
    set +a
    cd /opt/egrcrm/current || exit 1
    runuser -u egrcrm -- /opt/egrcrm/current/.venv/bin/gunicorn config.wsgi:application --workers 1 --threads 2 --check-config
) || true
printf '\n=== Nginx upstream errors ===\n'
tail -n 25 /var/log/nginx/error.log || true
printf '\n=== Diagnostics complete (no configuration or database changes) ===\n'
