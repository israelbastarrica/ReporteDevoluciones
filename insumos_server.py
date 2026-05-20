"""
insumos_server.py — Servidor de datos compartidos para insumos.html
Guarda urgente, desuso, stock_minimo, pedido_realizado, notas y solicitudes de cartones
en un JSON local compartido por todos los usuarios de la red.

Uso:
    pip install flask
    python insumos_server.py

Acceso desde el navegador (todos en la red):
    http://192.168.130.120:5001
"""
import json
import os
import shutil
from datetime import datetime

try:
    from flask import Flask, jsonify, request, Response, send_file
except ImportError:
    print("ERROR: Flask no instalado. Ejecuta:  pip install flask")
    raise

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
app         = Flask(__name__)
SHARED_FILE = os.path.join(BASE_DIR, 'insumos_shared.json')
BACKUP_DIR  = os.path.join(BASE_DIR, 'backups')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load():
    if os.path.exists(SHARED_FILE):
        with open(SHARED_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        'urgente': [],
        'desuso': [],
        'stock_minimo': {},
        'pedido_realizado': [],
        'notas': {},
        'solicitudes_cartones': [],
    }


def _save(data):
    data['_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(SHARED_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _backup()


def _backup():
    """Guarda una copia diaria en backups/insumos_shared_YYYY-MM-DD.json"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    hoy = datetime.now().strftime('%Y-%m-%d')
    dest = os.path.join(BACKUP_DIR, f'insumos_shared_{hoy}.json')
    if not os.path.exists(dest):
        shutil.copy2(SHARED_FILE, dest)


def _cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp


@app.after_request
def after(resp):
    return _cors(resp)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

@app.route('/')
@app.route('/insumos')
def index():
    """Sirve el HTML principal a todos en la red."""
    return send_file(os.path.join(BASE_DIR, 'insumos.html'))


@app.route('/api/shared', methods=['OPTIONS'])
def preflight():
    return _cors(Response(status=200))


@app.route('/api/shared', methods=['GET'])
def get_shared():
    return jsonify(_load())


@app.route('/api/shared', methods=['POST'])
def post_shared():
    patch = request.get_json(force=True, silent=True) or {}
    data = _load()
    for k, v in patch.items():
        data[k] = v
    _save(data)
    return jsonify({'ok': True, 'updated': data.get('_updated')})


@app.route('/api/backup', methods=['GET'])
def list_backups():
    """Lista los backups disponibles."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    archivos = sorted(os.listdir(BACKUP_DIR), reverse=True)
    return jsonify(archivos)


# ---------------------------------------------------------------------------
# Inicio
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print('=' * 55)
    print('  Servidor de insumos compartidos — MARKET')
    print('  Acceso desde la red:  http://192.168.130.120:5001')
    print('  Datos en:', SHARED_FILE)
    print('  Backups en:', BACKUP_DIR)
    print('=' * 55)
    app.run(host='0.0.0.0', port=5001, debug=False)
