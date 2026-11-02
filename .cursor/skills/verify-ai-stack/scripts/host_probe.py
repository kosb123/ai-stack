"""Read only allowlisted process identities; never emit command lines or environment."""
import json
from pathlib import Path
import sys

import psutil


def inspect_host():
    ports = (18434, 18435, 20128, 8888, 20140)
    listeners = {str(port): [] for port in ports}
    for conn in psutil.net_connections(kind='tcp'):
        if conn.status != 'LISTEN' or conn.laddr.port not in ports or conn.pid is None:
            continue
        process = psutil.Process(conn.pid)
        args = process.cmdline()
        cwd = Path(process.cwd())
        candidates = [(Path(arg) if Path(arg).is_absolute() else cwd / arg) for arg in args[1:]
                      if Path(arg).suffix.lower() in {'.py', '.js', '.mjs', '.cjs'}]
        scripts = [str(candidate.resolve()) for candidate in candidates if candidate.is_file()]
        item = {'pid': process.pid, 'created': process.create_time(), 'exe': process.exe(), 'scripts': scripts,
                'address': conn.laddr.ip}
        if item not in listeners[str(conn.laddr.port)]:
            listeners[str(conn.laddr.port)].append(item)
    models = []
    for process in psutil.process_iter(['name', 'exe']):
        if (process.info['name'] or '').lower() == 'llama-server.exe':
            models.append({'pid': process.pid, 'exe': process.info['exe'], 'created': process.create_time()})
    return {'listeners': listeners, 'model_processes': models, 'cpu_count': psutil.cpu_count(),
            'available_ram_bytes': psutil.virtual_memory().available}


if __name__ == '__main__':
    try:
        print(json.dumps(inspect_host()))
    except (OSError, psutil.Error) as exc:
        print(json.dumps({'error_type': type(exc).__name__}))
        sys.exit(1)
