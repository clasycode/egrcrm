#!/usr/bin/env bash
# Repair only the confirmed parent-directory permission failure (200/CHDIR).
set -Eeuo pipefail
fail() { echo "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run as root.'
[[ $# -eq 1 ]] || fail 'Usage: repair-access.sh PUBLIC_IPV4'
server_ip=$1
[[ $server_ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || fail 'Invalid IPv4 address.'
[[ -d /opt/egrcrm && ! -L /opt/egrcrm ]] || fail 'Expected the managed application directory.'
[[ -L /opt/egrcrm/current && -f /etc/egrcrm/egrcrm.env ]] || fail 'Existing CRM installation not found.'
[[ $(stat -c '%u' /opt/egrcrm) == 0 ]] || fail 'Application code directory must be owned by root.'
release=$(readlink -f /opt/egrcrm/current)
[[ $release == /opt/egrcrm/releases/* && -f $release/manage.py ]] || fail 'Unexpected release path; no permissions changed.'
grep -Fxq "ALLOWED_HOSTS=$server_ip,localhost,127.0.0.1" /etc/egrcrm/egrcrm.env || fail 'IP does not match this CRM configuration.'
echo 'Repairing access to /opt/egrcrm for the application user.'
# Only this parent was 0750 root:root. Do not recursively relax permissions.
chmod 0755 /opt/egrcrm
runuser -u egrcrm -- sh -c 'cd /opt/egrcrm/current && test -r manage.py && test -x .venv/bin/python'
/usr/local/sbin/egrcrm-manage check
systemctl reset-failed egrcrm-web.service egrcrm-worker.service
systemctl restart egrcrm-web.service egrcrm-worker.service
for attempt in $(seq 1 25); do
    if curl --fail --silent --max-time 4 --connect-to "$server_ip:443:127.0.0.1:443" "https://$server_ip/login/" >/dev/null; then
        systemctl is-active egrcrm-web.service egrcrm-worker.service
        echo "Access repaired. HTTPS login responds successfully: https://$server_ip"
        exit 0
    fi
    sleep 1
done
systemctl status egrcrm-web.service egrcrm-worker.service --no-pager --full || true
journalctl -u egrcrm-web.service -n 60 --no-pager || true
fail 'Directory access was repaired, but another startup error remains; see the log above.'
