"""
insumos_server.py — Servidor de datos compartidos para insumos.html
Guarda urgente, desuso, stock_minimo, pedido_realizado, notas y solicitudes de cartones
en un JSON local compartido por todos los usuarios de la red.

Uso:
    pip install flask
    python insumos_server.py

Acceso desde el navegador:
    http://<IP-DE-ESTA-PC>:5001/api/shared
"""
import json
import os
from datetime import datetime

try:
    from flask import Flask, jsonify, request, Response
except ImportError:
    print("ERROR: Flask no instalado. Ejecuta:  pip install flask")
    raise

app = Flask(__name__)
SHARED_FILE = os.path.join(os.path.dirname(__file__), 'insumos_shared.json')


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


def _cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp


@app.after_request
def after(resp):
    return _cors(resp)


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


if __name__ == '__main__':
    print('=' * 50)
    print('  Servidor de insumos compartidos')
    print('  http://0.0.0.0:5001')
    print('  Datos en:', SHARED_FILE)
    print('=' * 50)
    app.run(host='0.0.0.0', port=5001, debug=False)
