"""Let the server read YouTube captions through a Webshare residential proxy.

YouTube blocks caption requests from cloud servers (AWS), so on the live site
YouTube links fall back to "paste the transcript". With a Webshare
"Residential" proxy login, the server fetches captions through it.

    python deploy/proxy.py          # asks for the Webshare proxy username and password
    python deploy/proxy.py --off    # stop using the proxy

The login is stored as two SecureString parameters in SSM Parameter Store
(/study-corner/webshare-username, /study-corner/webshare-password). The server
reads them when the app starts, the same way as the YouTube key. The password
is typed in hidden and never printed or written to this computer.
"""

import base64
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deploy.remote import run  # noqa: E402
from lecture_agent import config  # noqa: E402

USER, PASSWORD = "/study-corner/webshare-username", "/study-corner/webshare-password"
START = "/opt/study-corner/start.sh"
MARK = "# webshare proxy"
BLOCK = f"""{MARK}
opt() {{ aws ssm get-parameter --region {config.REGION} --with-decryption --name "$1" --query Parameter.Value --output text 2>/dev/null || true; }}
proxy_user="$(opt {USER})"; proxy_pass="$(opt {PASSWORD})"
if [ -n "$proxy_user" ] && [ -n "$proxy_pass" ]; then
  printf 'WEBSHARE_PROXY_USERNAME=%s\\nWEBSHARE_PROXY_PASSWORD=%s\\n' "$proxy_user" "$proxy_pass" >> /opt/study-corner/app/.env
fi
"""
TEST_VIDEO = "KWgYha0clzw"  # Bro Code, "Learn Python for loops in 5 minutes!" (has English captions)


def add_block_to_start_script() -> None:
    status, current = run(f"cat {START}")
    if status != "Success":
        raise SystemExit(f"couldn't read {START}: {current}")
    if MARK in current:
        return
    lines = current.rstrip("\n").split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith("exec "))
    lines[at:at] = BLOCK.rstrip("\n").split("\n")
    encoded = base64.b64encode(("\n".join(lines) + "\n").encode()).decode()
    status, out = run(f"cp {START} {START}.bak && echo {encoded} | base64 -d > {START} && chmod 755 {START} && echo OK")
    if "OK" not in out:
        raise SystemExit(f"couldn't update {START}: {out}")
    print(f"updated {START} (old copy at {START}.bak)")


def restart_and_test() -> None:
    status, out = run(f"""systemctl restart study-corner
sleep 5
systemctl is-active study-corner
cd /opt/study-corner/app && sudo -u studycorner ../venv/bin/python -X utf8 -c "
from lecture_agent import youtube
try:
    c = youtube.fetch_captions('{TEST_VIDEO}')
    print('CAPTIONS-OK', len(c.segments), 'lines', c.words, 'words')
except Exception as e:
    print('CAPTIONS-FAILED', e)
" """)
    print(out.strip())


def main() -> None:
    ssm = config.session().client("ssm")
    if "--off" in sys.argv:
        for name in (USER, PASSWORD):
            try:
                ssm.delete_parameter(Name=name)
            except ssm.exceptions.ParameterNotFound:
                pass
        print("proxy login removed")
        restart_and_test()
        return
    print("Webshare: Dashboard > Residential > Proxy settings (the proxy username and password, not your account login)")
    user = input("Proxy username: ").strip()
    password = getpass.getpass("Proxy password (hidden): ").strip()
    if not user or not password:
        raise SystemExit("nothing stored: both are needed")
    ssm.put_parameter(Name=USER, Value=user, Type="SecureString", Overwrite=True)
    ssm.put_parameter(Name=PASSWORD, Value=password, Type="SecureString", Overwrite=True)
    print(f"stored {USER} and {PASSWORD} (SecureString)")
    add_block_to_start_script()
    restart_and_test()


if __name__ == "__main__":
    main()
