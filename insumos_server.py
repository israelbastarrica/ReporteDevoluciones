"""
insumos_server.py — Servidor de datos compartidos para insumos.html
Estado guardado en SQL Server (DB MARKET), tablas creadas automáticamente.
En el primer inicio migra los datos del JSON anterior si existe.

Uso:
    pip install flask pyodbc
    python insumos_server.py

Acceso desde la red:
    http://192.168.130.120:5001
"""
import json
import os
from datetime import datetime

try:
    from flask import Flask, jsonify, request, Response, send_file
except ImportError:
    print("ERROR: Flask no instalado. Ejecuta:  pip install flask")
    raise

try:
    import pyodbc
except ImportError:
    print("ERROR: pyodbc no instalado. Ejecuta:  pip install pyodbc")
    raise

from config import SERVER, DB_MARKET, USER, PASSWORD

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SHARED_FILE = os.path.join(BASE_DIR, 'insumos_shared.json')   # solo para migración
app         = Flask(__name__)

# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------

def get_conn():
    return pyodbc.connect(
        f'DRIVER={{SQL Server}};SERVER={SERVER};'
        f'DATABASE={DB_MARKET};UID={USER};PWD={PASSWORD}',
        timeout=30,
        autocommit=False,
    )

# ---------------------------------------------------------------------------
# Tablas: creación automática al iniciar
# ---------------------------------------------------------------------------

CREATE_TABLES = [
    """
    IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name='InsumosUrgentes')
    CREATE TABLE InsumosUrgentes (
        Codigo   NVARCHAR(30)  NOT NULL PRIMARY KEY,
        Quien    NVARCHAR(100) NOT NULL DEFAULT '',
        Cantidad INT           NOT NULL DEFAULT 0,
        Fecha    NVARCHAR(50)  NOT NULL DEFAULT ''
    )
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name='InsumosDesuso')
    CREATE TABLE InsumosDesuso (
        Codigo NVARCHAR(30) NOT NULL PRIMARY KEY
    )
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name='InsumosStockMinimo')
    CREATE TABLE InsumosStockMinimo (
        Codigo      NVARCHAR(30) NOT NULL PRIMARY KEY,
        StockMinimo INT          NOT NULL DEFAULT 0
    )
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name='InsumosPedidos')
    CREATE TABLE InsumosPedidos (
        Codigo        NVARCHAR(30)  NOT NULL PRIMARY KEY,
        Cantidad      INT           NOT NULL DEFAULT 0,
        FechaEntrega  VARCHAR(10)   NULL,
        Proveedor     NVARCHAR(200) NOT NULL DEFAULT '',
        FechaRegistro DATETIME      NOT NULL DEFAULT GETDATE()
    )
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name='InsumosNotas')
    CREATE TABLE InsumosNotas (
        Codigo NVARCHAR(30)   NOT NULL,
        Campo  NVARCHAR(50)   NOT NULL,
        Valor  NVARCHAR(1000) NOT NULL DEFAULT '',
        PRIMARY KEY (Codigo, Campo)
    )
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name='InsumosSolicitudesCartones')
    CREATE TABLE InsumosSolicitudesCartones (
        ID          NVARCHAR(60)  NOT NULL PRIMARY KEY,
        Descripcion NVARCHAR(200) NOT NULL DEFAULT '',
        Codigo      NVARCHAR(30)  NOT NULL DEFAULT '',
        Cantidad    INT           NOT NULL DEFAULT 0,
        Nota        NVARCHAR(500) NOT NULL DEFAULT '',
        Fecha       NVARCHAR(50)  NOT NULL DEFAULT '',
        Atendido    BIT           NOT NULL DEFAULT 0
    )
    """,
    """
    IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name='InsumosHistorial')
    CREATE TABLE InsumosHistorial (
        ID          NVARCHAR(60)  NOT NULL PRIMARY KEY,
        Tipo        NVARCHAR(30)  NOT NULL DEFAULT '',
        Cod         NVARCHAR(30)  NOT NULL DEFAULT '',
        Descripcion NVARCHAR(500) NOT NULL DEFAULT '',
        Quien       NVARCHAR(100) NOT NULL DEFAULT '',
        Cantidad    FLOAT         NULL,
        Nota        NVARCHAR(500) NOT NULL DEFAULT '',
        Fecha       NVARCHAR(50)  NOT NULL DEFAULT ''
    )
    """,
]

def init_tables():
    conn = get_conn()
    try:
        cur = conn.cursor()
        for sql in CREATE_TABLES:
            cur.execute(sql)
        conn.commit()
        print("  Tablas SQL verificadas/creadas OK")
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def load_shared():
    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("SELECT Codigo, Quien, Cantidad, Fecha FROM InsumosUrgentes")
        urgente = [{'cod': r[0], 'quien': r[1], 'cantidad': r[2], 'fecha': r[3]}
                   for r in cur.fetchall()]

        cur.execute("SELECT Codigo FROM InsumosDesuso")
        desuso = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT Codigo, StockMinimo FROM InsumosStockMinimo")
        stock_minimo = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute("SELECT Codigo, Cantidad, FechaEntrega, Proveedor FROM InsumosPedidos")
        rows = cur.fetchall()
        pedido_realizado = [r[0] for r in rows]
        pedido_info = {
            r[0]: {'cantidad': r[1], 'fecha_entrega': r[2] or '', 'proveedor': r[3] or ''}
            for r in rows
        }

        cur.execute("SELECT Codigo, Campo, Valor FROM InsumosNotas")
        notas = {}
        for r in cur.fetchall():
            notas.setdefault(r[0], {})[r[1]] = r[2]

        cur.execute("""
            SELECT ID, Descripcion, Codigo, Cantidad, Nota, Fecha, Atendido
            FROM InsumosSolicitudesCartones ORDER BY Fecha DESC
        """)
        solicitudes = [
            {'id': r[0], 'descripcion': r[1], 'codigo': r[2], 'cantidad': r[3],
             'nota': r[4], 'fecha': r[5], 'atendido': bool(r[6])}
            for r in cur.fetchall()
        ]

        cur.execute("""
            SELECT ID, Tipo, Cod, Descripcion, Quien, Cantidad, Nota, Fecha
            FROM InsumosHistorial ORDER BY Fecha DESC
        """)
        historial = [
            {'id': r[0], 'tipo': r[1], 'cod': r[2], 'descripcion': r[3],
             'quien': r[4], 'cantidad': r[5] or 0, 'nota': r[6], 'fecha': r[7]}
            for r in cur.fetchall()
        ]

        return {
            'urgente': urgente,
            'desuso': desuso,
            'stock_minimo': stock_minimo,
            'pedido_realizado': pedido_realizado,
            'pedido_info': pedido_info,
            'notas': notas,
            'solicitudes_cartones': solicitudes,
            'historial': historial,
        }
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Escritura (patch)
# ---------------------------------------------------------------------------

def save_patch(patch):
    conn = get_conn()
    try:
        cur = conn.cursor()

        if 'urgente' in patch:
            cur.execute("DELETE FROM InsumosUrgentes")
            for u in patch['urgente']:
                if isinstance(u, dict):
                    cur.execute(
                        "INSERT INTO InsumosUrgentes (Codigo,Quien,Cantidad,Fecha) VALUES (?,?,?,?)",
                        u['cod'], u.get('quien', ''), u.get('cantidad', 0), u.get('fecha', '')
                    )
                else:
                    cur.execute(
                        "INSERT INTO InsumosUrgentes (Codigo) VALUES (?)", u
                    )

        if 'desuso' in patch:
            cur.execute("DELETE FROM InsumosDesuso")
            for cod in patch['desuso']:
                cur.execute("INSERT INTO InsumosDesuso (Codigo) VALUES (?)", cod)

        if 'stock_minimo' in patch:
            for cod, val in patch['stock_minimo'].items():
                cur.execute(
                    "UPDATE InsumosStockMinimo SET StockMinimo=? WHERE Codigo=?", val, cod
                )
                if cur.rowcount == 0:
                    cur.execute(
                        "INSERT INTO InsumosStockMinimo (Codigo,StockMinimo) VALUES (?,?)", cod, val
                    )

        if 'pedido_realizado' in patch:
            cur.execute("DELETE FROM InsumosPedidos")
            info = patch.get('pedido_info', {})
            for cod in patch['pedido_realizado']:
                pi = info.get(cod, {})
                cur.execute(
                    "INSERT INTO InsumosPedidos (Codigo,Cantidad,FechaEntrega,Proveedor) VALUES (?,?,?,?)",
                    cod,
                    pi.get('cantidad', 0),
                    pi.get('fecha_entrega') or None,
                    pi.get('proveedor', ''),
                )

        if 'notas' in patch:
            for cod, fields in patch['notas'].items():
                for campo, valor in fields.items():
                    cur.execute(
                        "UPDATE InsumosNotas SET Valor=? WHERE Codigo=? AND Campo=?",
                        valor, cod, campo
                    )
                    if cur.rowcount == 0:
                        cur.execute(
                            "INSERT INTO InsumosNotas (Codigo,Campo,Valor) VALUES (?,?,?)",
                            cod, campo, valor
                        )

        if 'solicitudes_cartones' in patch:
            cur.execute("DELETE FROM InsumosSolicitudesCartones")
            for s in patch['solicitudes_cartones']:
                cur.execute(
                    "INSERT INTO InsumosSolicitudesCartones "
                    "(ID,Descripcion,Codigo,Cantidad,Nota,Fecha,Atendido) VALUES (?,?,?,?,?,?,?)",
                    s['id'], s['descripcion'], s.get('codigo', ''), s['cantidad'],
                    s.get('nota', ''), s['fecha'], 1 if s.get('atendido') else 0
                )

        if 'historial' in patch:
            cur.execute("DELETE FROM InsumosHistorial")
            for e in patch['historial']:
                cur.execute(
                    "INSERT INTO InsumosHistorial "
                    "(ID,Tipo,Cod,Descripcion,Quien,Cantidad,Nota,Fecha) VALUES (?,?,?,?,?,?,?,?)",
                    e['id'], e['tipo'], e.get('cod', ''), e.get('descripcion', ''),
                    e.get('quien', ''), e.get('cantidad') or 0, e.get('nota', ''), e['fecha']
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Migración desde JSON (una sola vez, si las tablas están vacías)
# ---------------------------------------------------------------------------

def migrate_from_json():
    if not os.path.exists(SHARED_FILE):
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM InsumosHistorial")
        tiene_datos = cur.fetchone()[0] > 0
        conn.close()
        if tiene_datos:
            return   # ya hay datos en SQL, no reimportar
        with open(SHARED_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        keys = ['urgente', 'desuso', 'stock_minimo', 'pedido_realizado',
                'pedido_info', 'notas', 'solicitudes_cartones', 'historial']
        save_patch({k: data[k] for k in keys if k in data})
        os.rename(SHARED_FILE, SHARED_FILE + '.migrated')
        print("  Migración desde JSON completada → insumos_shared.json.migrated")
    except Exception as e:
        print(f"  Advertencia migración: {e}")

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

def _cors(resp):
    resp.headers['Access-Control-Allow-Origin']  = '*'
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
    return send_file(os.path.join(BASE_DIR, 'insumos.html'))

@app.route('/api/shared', methods=['OPTIONS'])
def preflight():
    return _cors(Response(status=200))

@app.route('/api/shared', methods=['GET'])
def get_shared():
    try:
        return jsonify(load_shared())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/shared', methods=['POST'])
def post_shared():
    patch = request.get_json(force=True, silent=True) or {}
    try:
        save_patch(patch)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ---------------------------------------------------------------------------
# Inicio
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print('=' * 58)
    print('  Servidor de insumos — MARKET  (SQL Server backend)')
    print(f'  DB: {DB_MARKET} en {SERVER}')
    print('  Acceso desde la red:  http://192.168.130.120:5001')
    print('=' * 58)
    init_tables()
    migrate_from_json()
    print('  Servidor listo.')
    app.run(host='0.0.0.0', port=5001, debug=False)
