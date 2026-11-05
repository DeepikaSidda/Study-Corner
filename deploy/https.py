"""Add HTTPS to the Study Corner server: Caddy in front of the app, with a free Let's Encrypt certificate.

    python deploy/https.py

The certificate needs a host name, so the server's IP is named through
sslip.io (54.225.36.174 -> 54-225-36-174.sslip.io, a public DNS service that
resolves such names to the IP inside them). Caddy listens on 80 and 443,
redirects http to https, renews the certificate itself, and forwards to the
app, which now listens on 127.0.0.1:8000 only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deploy.remote import run  # noqa: E402
from lecture_agent import config  # noqa: E402

ec2 = config.session().client("ec2")
inst = ec2.describe_instances(Filters=[{"Name": "tag:Name", "Values": ["study-corner"]},
                                       {"Name": "instance-state-name", "Values": ["running"]}])["Reservations"][0]["Instances"][0]
ip = inst["PublicIpAddress"]
host = ip.replace(".", "-") + ".sslip.io"
sg = inst["SecurityGroups"][0]["GroupId"]

try:
    ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
         "IpRanges": [{"CidrIp": "0.0.0.0/0"}], "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}])
    print("opened port 443 on", sg)
except ec2.exceptions.ClientError as exc:
    if "Duplicate" not in str(exc):
        raise
    print("port 443 already open")

SCRIPT = f"""set -euo pipefail
# Caddy: one static binary from the official download service
if [ ! -x /usr/local/bin/caddy ]; then
  curl -fsSL -o /usr/local/bin/caddy 'https://caddyserver.com/api/download?os=linux&arch=amd64'
  chmod 755 /usr/local/bin/caddy
fi
/usr/local/bin/caddy version
id caddy >/dev/null 2>&1 || useradd --system --home-dir /var/lib/caddy --create-home --shell /sbin/nologin caddy
mkdir -p /etc/caddy
cat > /etc/caddy/Caddyfile <<'EOF'
{{
  email admin@{host}
}}
{host} {{
  encode gzip
  request_body {{
    max_size 2GB
  }}
  reverse_proxy 127.0.0.1:8000 {{
    transport http {{
      read_timeout 10m
      write_timeout 10m
    }}
  }}
}}
http://{ip} {{
  redir https://{host}{{uri}} permanent
}}
EOF
/usr/local/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
cat > /etc/systemd/system/caddy.service <<'EOF'
[Unit]
Description=Caddy (HTTPS for Study Corner)
After=network-online.target
Wants=network-online.target
[Service]
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile --adapter caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
Restart=on-failure
AmbientCapabilities=CAP_NET_BIND_SERVICE
Environment=XDG_DATA_HOME=/var/lib/caddy XDG_CONFIG_HOME=/var/lib/caddy
[Install]
WantedBy=multi-user.target
EOF
# The app moves behind Caddy: localhost only, port 8000
sed -i 's/--host 0.0.0.0 --port 80/--host 127.0.0.1 --port 8000/' /opt/study-corner/start.sh
grep -o -- '--host [^ ]* --port [0-9]*' /opt/study-corner/start.sh
systemctl daemon-reload
systemctl restart study-corner
sleep 4
systemctl enable --now caddy
systemctl restart caddy
sleep 12
systemctl is-active study-corner caddy
journalctl -u caddy --no-pager -n 40 | grep -Eio 'certificate obtained successfully|obtain.*error[^"]*|could not get certificate[^"]*' | head -3 || true
"""

status, out = run(SCRIPT, timeout=300)
print(out)
print("STATUS", status)
print("URL https://" + host)
