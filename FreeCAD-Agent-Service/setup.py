"""Generate concrete deployment config; no .env interpolation is required."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import secrets


def generate(folder, ip='192.168.1.100', port=8090, names=('codex', 'hermes', 'mobile')):
    folder = Path(folder)
    ipaddress.IPv4Address(ip)
    if not 1024 <= port <= 65535:
        raise ValueError('Choose a port between 1024 and 65535')
    if not names or len(set(names)) != len(names) or any(not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', name) for name in names):
        raise ValueError('Agent names must be unique letters, numbers, underscore or hyphen')
    destination = folder / 'docker-compose.yml'
    connections = folder / 'AGENT-CONNECTIONS.private.json'
    if destination.exists() or connections.exists():
        raise FileExistsError('Private configuration already exists; existing credentials were not changed')
    agents = {name: secrets.token_hex(32) for name in names}
    text = (folder / 'compose.template.yml').read_text(encoding='utf-8')
    text = text.replace('__BIND_IP__', ip).replace('__PORT__', str(port)).replace('__AGENTS_JSON__', json.dumps(agents))
    payload = {name: {'url': f'http://{ip}:{port}/mcp', 'transport': 'streamable-http',
                      'authorization_header': 'Bearer ' + token} for name, token in agents.items()}
    # Exclusive creation prevents accidental overwrite on concurrent invocations.
    with destination.open('x', encoding='utf-8', newline='\n') as output:
        output.write(text)
    try:
        with connections.open('x', encoding='utf-8', newline='\n') as output:
            json.dump(payload, output, indent=2)
    except Exception:
        destination.unlink()
        raise
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', default='192.168.1.100')
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--agents', nargs='+', default=['codex', 'hermes', 'mobile'])
    args = parser.parse_args()
    result = generate(Path(__file__).resolve().parent, args.ip, args.port, args.agents)
    print('Created deployment configuration and private agent connection file. Credentials were not printed.')
