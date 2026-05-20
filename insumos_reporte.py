"""
Reporte de stock de insumos para area de compras.
Genera insumos.html con:
  - Consumo del mes actual (MSTOCK DIRMOV=2 MOTIVO=13, articulos ZZ*)
  - Stock actual en deposito (STOCKINSUMOS.135.csv)
  - Agrupado y filtrable por proveedor
  - Columna de unidad de medida editable en browser
"""
import pyodbc
import pandas as pd
import json
import warnings
from datetime import date
from config import SERVER, DB_CENTRAL, USER, PASSWORD

warnings.filterwarnings('ignore', category=UserWarning)

CSV_PATH = r'C:\Users\Usuario\Desktop\STOCKINSUMOS.135.csv'
OUTPUT   = r'C:\REPORTESDEVOLUCIONES\insumos.html'
ANIO, MES = date.today().year, date.today().month

UNIDADES_ESPECIALES = {
    'ZZ0000111': 'Pack x6',
    'ZZ0000149': 'Caja x80',
}
UNIDAD_DEFAULT = 'Unidad'

# ---------------------------------------------------------------------------
# 1. CSV — solo para nombre de proveedor por articulo
# ---------------------------------------------------------------------------
print("Leyendo CSV (proveedores)...")
csv = pd.read_csv(CSV_PATH, sep=';', dtype=str, encoding='latin-1')
csv.columns = [c.strip().lstrip('﻿') for c in csv.columns]

col_codigo = next((c for c in csv.columns if ('artículo' in c.lower() or 'articulo' in c.lower()) and 'descripci' not in c.lower()), None)
col_nombre = next((c for c in csv.columns if 'proveedor' in c.lower() and 'nombre' in c.lower()), None)
df_provs = csv[[col_codigo, col_nombre]].copy()
df_provs.columns = ['Codigo', 'Proveedor']
df_provs['Codigo']    = df_provs['Codigo'].str.strip()
df_provs['Proveedor'] = df_provs['Proveedor'].str.strip()
df_provs = df_provs.drop_duplicates('Codigo')
print(f"  Articulos con proveedor: {len(df_provs)}")

# ---------------------------------------------------------------------------
# 2. Stock actual desde COMB (misma fuente que Dragonfish) + consumo del mes
# ---------------------------------------------------------------------------
print("Consultando DB (COMB + MSTOCK)...")
conn = pyodbc.connect(
    f'DRIVER={{SQL Server}};SERVER={SERVER};'
    f'DATABASE={DB_CENTRAL};UID={USER};PWD={PASSWORD}',
    timeout=30
)

df_stock = pd.read_sql(f"""
    SELECT
        RTRIM(C.COART)                             AS Codigo,
        SUM(C.COCANT)                              AS StockActual
    FROM {DB_CENTRAL}.Zoologic.COMB C
    WHERE LEFT(RTRIM(C.COART), 2) = 'ZZ'
    GROUP BY RTRIM(C.COART)
""", conn)
df_stock['StockActual'] = df_stock['StockActual'].fillna(0).astype(int)
print(f"  Articulos ZZ* en COMB: {len(df_stock)}")

df_consumo = pd.read_sql(f"""
    WITH UltimaEntrada AS (
        SELECT RTRIM(LTRIM(DET.MART)) AS Codigo, MAX(MS.FECHA) AS FechaUltima
        FROM {DB_CENTRAL}.Zoologic.MSTOCK MS
        INNER JOIN {DB_CENTRAL}.Zoologic.DETMSTOCK DET ON DET.NUMR = MS.CODIGO
        WHERE MS.DIRMOV = 1
          AND (MS.ANULADO IS NULL OR MS.ANULADO = 0)
          AND LEFT(RTRIM(DET.MART), 2) = 'ZZ'
        GROUP BY RTRIM(LTRIM(DET.MART))
    )
    SELECT
        RTRIM(LTRIM(DET.MART))              AS Codigo,
        SUM(DET.CANTI)                      AS Consumido
    FROM {DB_CENTRAL}.Zoologic.MSTOCK MS
    INNER JOIN {DB_CENTRAL}.Zoologic.DETMSTOCK DET ON DET.NUMR = MS.CODIGO
    LEFT  JOIN UltimaEntrada UE ON RTRIM(LTRIM(DET.MART)) = UE.Codigo
    WHERE MS.DIRMOV = 2
      AND MS.MOTIVO = 13
      AND (MS.ANULADO IS NULL OR MS.ANULADO = 0)
      AND LEFT(RTRIM(DET.MART), 2) = 'ZZ'
      AND (UE.FechaUltima IS NULL OR MS.FECHA > UE.FechaUltima)
    GROUP BY RTRIM(LTRIM(DET.MART))
""", conn)
print(f"  Articulos con consumo desde ultima reposicion: {len(df_consumo)}")

# ---------------------------------------------------------------------------
# 3. Merge y limpieza
# ---------------------------------------------------------------------------
# Descripcion desde ART
df_art = pd.read_sql(f"""
    SELECT RTRIM(ARTCOD) AS Codigo, RTRIM(ARTDES) AS Descripcion
    FROM {DB_CENTRAL}.Zoologic.ART
    WHERE LEFT(RTRIM(ARTCOD), 2) = 'ZZ'
""", conn)
conn.close()

df = df_stock.merge(df_consumo, on='Codigo', how='outer')
df = df.merge(df_art,   on='Codigo', how='left')
df = df.merge(df_provs, on='Codigo', how='left')

df['Descripcion'] = df['Descripcion'].fillna(df['Codigo'])
df['Proveedor']   = df['Proveedor'].fillna('(Sin proveedor)')
df['StockActual'] = df['StockActual'].fillna(0).astype(int)
df['Consumido']   = df['Consumido'].fillna(0).astype(int)
df['Unidad']      = df['Codigo'].map(UNIDADES_ESPECIALES).fillna(UNIDAD_DEFAULT)

df = df[(df['Consumido'] > 0) | (df['StockActual'] > 0)].copy()
df = df.sort_values(['Proveedor', 'Codigo']).reset_index(drop=True)
df = df[['Proveedor', 'Codigo', 'Descripcion', 'Unidad', 'Consumido', 'StockActual']]

# Separar cartones del resto
es_carton = df['Descripcion'].str.upper().str.startswith('CART')
df_carton  = df[es_carton].copy()
df_insumos = df[~es_carton].copy()

def kpis(d):
    return dict(
        n_arts    = len(d),
        n_provs   = d['Proveedor'].nunique(),
        consumo_t = int(d['Consumido'].sum()),
        sin_stock = int(((d['StockActual'] <= 0) & (d['Consumido'] > 0)).sum()),
        proveedores = sorted(d['Proveedor'].unique().tolist()),
    )

ki = kpis(df_insumos)
kc = kpis(df_carton)

print(f"  Insumos: {ki['n_arts']} arts | Cartones: {kc['n_arts']} arts")

# ---------------------------------------------------------------------------
# 4. Serializar para JS
# ---------------------------------------------------------------------------
def clean(lst):
    out = []
    for r in lst:
        out.append({k: ('' if v is None or (isinstance(v, float) and str(v) == 'nan') else v)
                    for k, v in r.items()})
    return out

j_ins   = json.dumps(clean(df_insumos.to_dict(orient='records')), ensure_ascii=False)
j_cart  = json.dumps(clean(df_carton.to_dict(orient='records')),  ensure_ascii=False)
j_provs_ins  = json.dumps(ki['proveedores'], ensure_ascii=False)
j_provs_cart = json.dumps(kc['proveedores'], ensure_ascii=False)

ahora   = date.today().strftime('%d/%m/%Y')

# ---------------------------------------------------------------------------
# 5. HTML
# ---------------------------------------------------------------------------
html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Insumos — MARKET</title>
<style>
:root{{--bg:#0d0d0d;--card:#141414;--border:#2a2a2a;--accent:#e8b963;--text:#f0f0f0;--muted:#888;--warn:#f97316;--ok:#22c55e;--err:#ef4444;--group:#1a2a1a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:Arial,sans-serif;font-size:13px;}}
.page-header{{background:#111;border-bottom:2px solid var(--accent);padding:16px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}}
.page-title{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:20px;color:var(--accent);letter-spacing:1px;}}
.nav-links{{display:flex;gap:8px;margin-left:auto;flex-wrap:wrap;}}
.nav-links a{{background:#1e1e1e;border:1px solid var(--border);color:var(--muted);padding:5px 12px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:.5px;text-decoration:none;}}
.nav-links a:hover{{border-color:var(--accent);color:var(--accent);}}
.kpi-row{{display:flex;gap:12px;padding:16px 24px;flex-wrap:wrap;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:12px 18px;min-width:140px;flex:1;}}
.kpi-val{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:26px;color:var(--accent);}}
.kpi-val.warn{{color:var(--warn);}}
.kpi-label{{color:var(--muted);font-size:11px;margin-top:2px;}}
.kpi-sub{{color:#555;font-size:11px;margin-top:2px;}}
.tabs{{display:flex;gap:0;padding:0 24px;border-bottom:1px solid var(--border);}}
.tab-btn{{background:none;border:none;color:var(--muted);font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:13px;letter-spacing:.5px;padding:12px 22px;cursor:pointer;border-bottom:3px solid transparent;transition:.15s;}}
.tab-btn.active{{color:var(--accent);border-bottom-color:var(--accent);}}
.section{{display:none;}}.section.active{{display:block;}}
.toolbar{{display:flex;gap:10px;padding:12px 24px;flex-wrap:wrap;align-items:center;border-bottom:1px solid var(--border);}}
.toolbar select,.toolbar input[type=text]{{background:#1a1a1a;border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:4px;font-size:12px;}}
.toolbar select:focus,.toolbar input:focus{{outline:none;border-color:var(--accent);}}
.toolbar input[type=text]{{width:200px;}}
.cnt{{color:var(--muted);font-size:11px;padding:8px 24px 0;}}
.tbl-wrap{{overflow-x:auto;max-height:calc(100vh - 340px);margin:8px 24px 0;border:1px solid var(--border);border-radius:6px;}}
table{{width:100%;border-collapse:collapse;}}
thead th{{background:#1a1a1a;color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.5px;padding:8px 10px;text-align:left;position:sticky;top:0;z-index:2;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;}}
thead th:hover{{color:var(--accent);}}
thead th.asc::after{{content:" ▲";color:var(--accent);font-size:9px;}}
thead th.desc::after{{content:" ▼";color:var(--accent);font-size:9px;}}
tbody tr.group-row{{background:var(--group);}}
tbody tr.group-row td{{padding:6px 10px;font-weight:700;font-size:11px;letter-spacing:.5px;color:#7ef7a0;border-bottom:1px solid #2a3a2a;}}
tbody tr{{border-bottom:1px solid #1e1e1e;transition:background .1s;}}
tbody tr:hover:not(.group-row){{background:#181818;}}
tbody td{{padding:7px 10px;white-space:nowrap;}}
.num{{text-align:right;font-variant-numeric:tabular-nums;}}
.warn-stock{{color:var(--warn);font-weight:700;}}
.ok-stock{{color:var(--ok);}}
/* Urgente */
tbody tr.urg-row{{background:#1a0808!important;border-left:3px solid var(--err);}}
tbody tr.urg-row:hover:not(.group-row){{background:#220a0a!important;}}
.badge-urg{{display:inline-block;background:#2e0808;color:var(--err);font-size:9px;font-weight:700;padding:1px 5px;border-radius:2px;letter-spacing:.5px;margin-left:5px;vertical-align:middle;}}
/* Pedido realizado */
tbody tr.pedido-done td{{opacity:.5;}}
/* Celdas editables */
.unidad-cell{{color:#aaa;font-style:italic;cursor:text;border-radius:3px;padding:2px 4px;min-width:60px;display:inline-block;}}
.unidad-cell:focus{{outline:1px solid var(--accent);color:var(--text);font-style:normal;background:#1e1e1e;}}
.nota-cell{{color:#aaa;cursor:text;border-radius:3px;padding:2px 6px;min-width:130px;display:inline-block;}}
.nota-cell:empty::before{{content:"…";color:#333;}}
.nota-cell:focus{{outline:1px solid var(--accent);color:var(--text);background:#1e1e1e;}}
.stmin-cell{{color:#aaa;cursor:text;border-radius:3px;padding:2px 4px;min-width:40px;display:inline-block;text-align:right;}}
.stmin-cell:empty::before{{content:"—";color:#333;}}
.stmin-cell:focus{{outline:1px solid var(--accent);color:var(--text);background:#1e1e1e;}}
.stmin-bajo{{color:var(--err)!important;font-weight:700;}}
.stmin-alerta{{color:var(--warn)!important;font-weight:700;}}
/* Paginacion */
.pag{{display:flex;gap:8px;align-items:center;padding:10px 24px;}}
.pag button{{background:#1a1a1a;border:1px solid var(--border);color:var(--muted);padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px;}}
.pag button:hover:not(:disabled){{border-color:var(--accent);color:var(--accent);}}
.pag button:disabled{{opacity:.3;cursor:default;}}
.pag span{{color:#555;font-size:11px;}}
/* Desuso */
.desuso-toggle{{background:#1a1a1a;border:1px solid var(--border);color:#555;padding:5px 12px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;}}
.desuso-toggle:hover{{border-color:#555;color:var(--muted);}}
.desuso-section{{padding:8px 24px 16px;border-top:1px solid #1e1e1e;display:none;}}
.desuso-section.open{{display:block;}}
.desuso-section table{{opacity:.5;}}
/* Context menu */
#ctx-menu{{display:none;position:fixed;z-index:9999;background:#1e1e1e;border:1px solid #3a3a3a;border-radius:6px;box-shadow:0 6px 20px #000d;min-width:200px;overflow:hidden;}}
#ctx-menu button{{display:block;width:100%;text-align:left;background:none;border:none;color:var(--text);padding:11px 16px;cursor:pointer;font-size:12px;border-bottom:1px solid #2a2a2a;}}
#ctx-menu button:last-child{{border-bottom:none;}}
#ctx-menu button:hover{{background:#2a2a2a;color:var(--accent);}}
/* Solicitudes de produccion (CARTONES) */
.sol-bar{{background:#060f06;border-bottom:2px solid #2a4a2a;padding:14px 24px;}}
.sol-bar-title{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:11px;color:#7ef7a0;letter-spacing:1px;margin-bottom:10px;}}
.sol-form{{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px;}}
.sol-form label{{display:flex;flex-direction:column;gap:4px;font-size:10px;color:#555;letter-spacing:.5px;}}
.sol-form input{{background:#0c1a0c;border:1px solid #2a4a2a;color:var(--text);padding:6px 10px;border-radius:4px;font-size:12px;width:160px;}}
.sol-form input[type=number]{{width:80px;}}
.sol-form input:focus{{outline:none;border-color:#7ef7a0;}}
.sol-btn{{background:#1a3a1a;border:1px solid #3a6a3a;color:#7ef7a0;padding:7px 16px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;letter-spacing:.5px;}}
.sol-btn:hover{{background:#224422;border-color:#7ef7a0;}}
.sol-cards{{display:flex;gap:8px;flex-wrap:wrap;}}
.sol-card{{background:#0f1e0f;border:1px solid #2a4a2a;border-left:3px solid var(--err);border-radius:4px;padding:8px 12px;display:flex;align-items:center;gap:10px;font-size:12px;}}
.sol-card-urg{{font-family:"Arial Black",Arial,sans-serif;font-size:9px;color:var(--err);font-weight:900;letter-spacing:.5px;}}
.sol-card-atender{{background:#1a3a1a;border:1px solid #2a5a2a;color:#22c55e;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:10px;font-weight:700;margin-left:auto;}}
.sol-card-atender:hover{{background:#224422;}}
.sol-vacas{{color:#2a4a2a;font-size:12px;font-style:italic;}}
/* Badge solicitudes en tab */
.tab-badge{{display:inline-block;background:var(--err);color:#fff;font-size:9px;font-weight:900;padding:1px 5px;border-radius:10px;margin-left:5px;vertical-align:middle;}}
</style>
</head>
<body>

<!-- Context menu -->
<div id="ctx-menu">
  <button onclick="ctxToggleUrgente()">⚡ <span id="ctx-urg-lbl">Marcar como URGENTE</span></button>
  <button onclick="ctxToggleDesuso()">🗑 <span id="ctx-des-lbl">Enviar a desuso</span></button>
</div>

<div class="page-header">
  <div>
    <div class="page-title">STOCK DE INSUMOS</div>
    <div style="color:var(--muted);font-size:11px;margin-top:3px;">Consumo desde última reposición &nbsp;·&nbsp; Generado {ahora}</div>
  </div>
  <div class="nav-links">
    <a href="home.html">INICIO</a>
    <a href="index.html">DEVOLUCIONES</a>
    <a href="logistica.html">LOGÍSTICA</a>
    <a href="dashboard.html">DASHBOARD</a>
  </div>
</div>

<div class="kpi-row" id="kpi-row"></div>

<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('ins',this)">INSUMOS</button>
  <button class="tab-btn" id="tab-cart-btn" onclick="switchTab('cart',this)">CARTONES</button>
</div>

<!-- ══ INSUMOS ══ -->
<div id="sec-ins" class="section active">
  <div class="toolbar">
    <select id="ins-prov" onchange="S.ins.filtrar()"><option value="">Todos los proveedores</option></select>
    <input id="ins-bus" type="text" placeholder="Buscar código o descripción..." oninput="S.ins.filtrar()">
    <button id="ins-critico" onclick="S.ins.toggleCritico()" style="background:#2a1010;border:1px solid #6b2020;color:#f97316;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;letter-spacing:.5px;">⚠ CRÍTICO</button>
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--muted);font-size:12px;">
      <input type="checkbox" id="ins-sincons" onchange="S.ins.filtrar()" style="accent-color:var(--accent);">
      Sin consumo
    </label>
    <button class="desuso-toggle" onclick="toggleDesusoPanel('ins')">Ver desuso</button>
  </div>
  <div class="cnt" id="ins-cnt"></div>
  <div class="tbl-wrap" id="ins-wrap">
    <table>
      <thead><tr>
        <th onclick="S.ins.sortBy(0)">CÓDIGO</th>
        <th onclick="S.ins.sortBy(1)">DESCRIPCIÓN</th>
        <th onclick="S.ins.sortBy(2)">UNIDAD</th>
        <th onclick="S.ins.sortBy(3)" class="num">CONSUMIDO</th>
        <th onclick="S.ins.sortBy(4)" class="num">STOCK DEP.</th>
        <th class="num" style="min-width:70px;">STOCK MÍN.</th>
        <th style="min-width:130px;">LOGÍSTICA</th>
        <th style="min-width:130px;">COMPRAS</th>
        <th style="text-align:center;min-width:90px;">PEDIDO ✓</th>
      </tr></thead>
      <tbody id="ins-tbody"></tbody>
    </table>
  </div>
  <div class="pag" id="ins-pag"></div>
  <div class="desuso-section" id="ins-desuso">
    <div style="color:#555;font-size:11px;font-weight:700;letter-spacing:.5px;margin-bottom:8px;">ARTÍCULOS EN DESUSO</div>
    <table>
      <thead><tr>
        <th>CÓDIGO</th><th>DESCRIPCIÓN</th><th>STOCK</th><th></th>
      </tr></thead>
      <tbody id="ins-desuso-tbody"></tbody>
    </table>
  </div>
</div>

<!-- ══ CARTONES ══ -->
<div id="sec-cart" class="section">
  <!-- Barra de solicitudes de producción -->
  <div class="sol-bar">
    <div class="sol-bar-title">SOLICITUDES DE PRODUCCIÓN</div>
    <div class="sol-form">
      <label>DESCRIPCIÓN / TALLE
        <input type="text" id="sol-desc" placeholder="Ej: CARTON T38">
      </label>
      <label>CANTIDAD
        <input type="number" id="sol-cant" placeholder="0" min="1">
      </label>
      <label>NOTA
        <input type="text" id="sol-nota" placeholder="Para qué pedido...">
      </label>
      <button class="sol-btn" onclick="agregarSolicitud()">+ SOLICITAR</button>
    </div>
    <div class="sol-cards" id="sol-cards"></div>
  </div>
  <div class="toolbar">
    <select id="cart-prov" onchange="S.cart.filtrar()"><option value="">Todos los proveedores</option></select>
    <input id="cart-bus" type="text" placeholder="Buscar código o descripción..." oninput="S.cart.filtrar()">
    <button id="cart-critico" onclick="S.cart.toggleCritico()" style="background:#2a1010;border:1px solid #6b2020;color:#f97316;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;letter-spacing:.5px;">⚠ CRÍTICO</button>
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--muted);font-size:12px;">
      <input type="checkbox" id="cart-sincons" onchange="S.cart.filtrar()" style="accent-color:var(--accent);">
      Sin consumo
    </label>
    <button class="desuso-toggle" onclick="toggleDesusoPanel('cart')">Ver desuso</button>
  </div>
  <div class="cnt" id="cart-cnt"></div>
  <div class="tbl-wrap" id="cart-wrap">
    <table>
      <thead><tr>
        <th onclick="S.cart.sortBy(0)">CÓDIGO</th>
        <th onclick="S.cart.sortBy(1)">DESCRIPCIÓN</th>
        <th onclick="S.cart.sortBy(2)">UNIDAD</th>
        <th onclick="S.cart.sortBy(3)" class="num">CONSUMIDO</th>
        <th onclick="S.cart.sortBy(4)" class="num">STOCK DEP.</th>
        <th class="num" style="min-width:70px;">STOCK MÍN.</th>
        <th style="min-width:130px;">LOGÍSTICA</th>
        <th style="min-width:130px;">COMPRAS</th>
        <th style="text-align:center;min-width:90px;">PEDIDO ✓</th>
      </tr></thead>
      <tbody id="cart-tbody"></tbody>
    </table>
  </div>
  <div class="pag" id="cart-pag"></div>
  <div class="desuso-section" id="cart-desuso">
    <div style="color:#555;font-size:11px;font-weight:700;letter-spacing:.5px;margin-bottom:8px;">CARTONES EN DESUSO</div>
    <table>
      <thead><tr>
        <th>CÓDIGO</th><th>DESCRIPCIÓN</th><th>STOCK</th><th></th>
      </tr></thead>
      <tbody id="cart-desuso-tbody"></tbody>
    </table>
  </div>
</div>

<script>
const DATASETS = {{
  ins:  {j_ins},
  cart: {j_cart}
}};
const PROVS_MAP = {{
  ins:  {j_provs_ins},
  cart: {j_provs_cart}
}};
const KPI_DATA = {{
  ins:  {{ arts:{ki['n_arts']}, provs:{ki['n_provs']}, consumo:{ki['consumo_t']}, sinStock:{ki['sin_stock']} }},
  cart: {{ arts:{kc['n_arts']}, provs:{kc['n_provs']}, consumo:{kc['consumo_t']}, sinStock:{kc['sin_stock']} }}
}};
const PG = 150;
const FIELDS = ['Codigo','Descripcion','Unidad','Consumido','StockActual'];
const SERVER = 'http://localhost:5001';

// ── Datos compartidos (servidor + fallback localStorage) ─────
const LS_KEY = 'insumos_shared_v2';
let shared = {{ urgente:[], desuso:[], stock_minimo:{{}}, pedido_realizado:[], notas:{{}}, solicitudes_cartones:[] }};

async function loadShared() {{
  try {{
    const r = await fetch(SERVER+'/api/shared', {{signal: AbortSignal.timeout(2000)}});
    shared = await r.json();
    shared.urgente          = shared.urgente          || [];
    shared.desuso           = shared.desuso           || [];
    shared.stock_minimo     = shared.stock_minimo     || {{}};
    shared.pedido_realizado = shared.pedido_realizado || [];
    shared.notas            = shared.notas            || {{}};
    shared.solicitudes_cartones = shared.solicitudes_cartones || [];
  }} catch(e) {{
    try {{ Object.assign(shared, JSON.parse(localStorage.getItem(LS_KEY)||'{{}}')); }} catch(e2) {{}}
  }}
}}
async function saveShared(patch) {{
  Object.assign(shared, patch);
  try {{
    await fetch(SERVER+'/api/shared', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(patch), signal: AbortSignal.timeout(2000)
    }});
  }} catch(e) {{
    try {{ localStorage.setItem(LS_KEY, JSON.stringify(shared)); }} catch(e2) {{}}
  }}
}}

// ── Notas (logistica/compras) ─────────────────────────────────
const LS_NOTAS = 'insumos_notas_v1';
let notas_local = {{}};
try {{ notas_local = JSON.parse(localStorage.getItem(LS_NOTAS)||'{{}}'); }} catch(e) {{}}

function getNota(cod, field) {{
  return (shared.notas[cod] && shared.notas[cod][field]) ||
         (notas_local[cod]  && notas_local[cod][field])  || '';
}}
function setNota(cod, field, val) {{
  if (!shared.notas[cod]) shared.notas[cod] = {{}};
  shared.notas[cod][field] = val;
  saveShared({{ notas: shared.notas }});
}}

function restoreEditable() {{
  document.querySelectorAll('[data-cod][data-field]').forEach(el => {{
    const cod = el.dataset.cod, field = el.dataset.field;
    if (field === 'unidad') {{
      const saved = notas_local[cod] && notas_local[cod]['unidad'];
      if (saved) el.textContent = saved;
      el.addEventListener('input', () => {{
        if (!notas_local[cod]) notas_local[cod] = {{}};
        notas_local[cod]['unidad'] = el.textContent.trim();
        try {{ localStorage.setItem(LS_NOTAS, JSON.stringify(notas_local)); }} catch(e) {{}}
      }});
    }} else if (field === 'stmin') {{
      const saved = shared.stock_minimo[cod];
      if (saved !== undefined) el.textContent = saved;
      el.addEventListener('input', () => {{
        const v = parseInt(el.textContent.trim()) || 0;
        shared.stock_minimo[cod] = v;
        saveShared({{ stock_minimo: shared.stock_minimo }});
      }});
    }} else {{
      const saved = getNota(cod, field);
      if (saved) el.textContent = saved;
      el.addEventListener('input', () => setNota(cod, field, el.textContent.trim()));
    }}
  }});
}}

// ── Context menu ──────────────────────────────────────────────
let ctxCod = null;
const ctxMenu = document.getElementById('ctx-menu');

document.addEventListener('contextmenu', e => {{
  const row = e.target.closest('tr[data-cod]');
  if (!row) return;
  e.preventDefault();
  ctxCod = row.dataset.cod;
  const isUrg = shared.urgente.includes(ctxCod);
  const isDes = shared.desuso.includes(ctxCod);
  document.getElementById('ctx-urg-lbl').textContent = isUrg ? 'Quitar urgente' : 'Marcar como URGENTE';
  document.getElementById('ctx-des-lbl').textContent = isDes ? 'Quitar de desuso' : 'Enviar a desuso';
  ctxMenu.style.display = 'block';
  ctxMenu.style.left = Math.min(e.pageX, window.innerWidth-210)+'px';
  ctxMenu.style.top  = Math.min(e.pageY, window.innerHeight-80)+'px';
}});
document.addEventListener('click', () => ctxMenu.style.display = 'none');
document.addEventListener('keydown', e => {{ if(e.key==='Escape') ctxMenu.style.display='none'; }});

function ctxToggleUrgente() {{
  if (!ctxCod) return;
  if (shared.urgente.includes(ctxCod))
    shared.urgente = shared.urgente.filter(c=>c!==ctxCod);
  else {{
    shared.urgente.push(ctxCod);
    shared.desuso = shared.desuso.filter(c=>c!==ctxCod);
  }}
  saveShared({{ urgente: shared.urgente, desuso: shared.desuso }});
  S.ins.render(); S.cart.render(); renderDesusoTables();
}}
function ctxToggleDesuso() {{
  if (!ctxCod) return;
  if (shared.desuso.includes(ctxCod))
    shared.desuso = shared.desuso.filter(c=>c!==ctxCod);
  else {{
    shared.desuso.push(ctxCod);
    shared.urgente = shared.urgente.filter(c=>c!==ctxCod);
  }}
  saveShared({{ desuso: shared.desuso, urgente: shared.urgente }});
  S.ins.render(); S.cart.render(); renderDesusoTables();
}}

// ── Desuso panel ──────────────────────────────────────────────
function toggleDesusoPanel(id) {{
  const el = document.getElementById(id+'-desuso');
  el.classList.toggle('open');
  renderDesusoTables();
}}
function renderDesusoTables() {{
  ['ins','cart'].forEach(id => {{
    const src = DATASETS[id];
    const rows = src.filter(r => shared.desuso.includes(r.Codigo));
    const el = document.getElementById(id+'-desuso-tbody');
    if (!el) return;
    el.innerHTML = rows.map(r => `
      <tr>
        <td style="color:#555">${{r.Codigo}}</td>
        <td style="color:#555">${{r.Descripcion}}</td>
        <td class="num" style="color:#555">${{r.StockActual}}</td>
        <td><button onclick="shared.desuso=shared.desuso.filter(c=>c!=='${{r.Codigo}}');saveShared({{desuso:shared.desuso}});S.${{id}}.render();renderDesusoTables();"
          style="background:none;border:1px solid #444;color:#888;padding:2px 8px;border-radius:3px;cursor:pointer;font-size:10px;">Restaurar</button></td>
      </tr>`).join('') || '<tr><td colspan="4" style="color:#333;padding:10px;">Sin artículos en desuso</td></tr>';
  }});
}}

// ── Tabs ──────────────────────────────────────────────────────
let tabActual = 'ins';
function switchTab(id, btn) {{
  tabActual = id;
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('sec-'+id).classList.add('active');
  renderKpis(id);
}}
function actualizarBadgeTab() {{
  const pend = (shared.solicitudes_cartones||[]).filter(s=>!s.atendido).length;
  const btn = document.getElementById('tab-cart-btn');
  const existing = btn.querySelector('.tab-badge');
  if (existing) existing.remove();
  if (pend > 0) btn.insertAdjacentHTML('beforeend',`<span class="tab-badge">${{pend}}</span>`);
}}

function fmt(n) {{ return Number(n).toLocaleString('es-AR'); }}
function renderKpis(id) {{
  const k = KPI_DATA[id];
  document.getElementById('kpi-row').innerHTML = `
    <div class="kpi"><div class="kpi-val">${{k.arts}}</div><div class="kpi-label">ARTÍCULOS</div><div class="kpi-sub">${{k.provs}} proveedores</div></div>
    <div class="kpi"><div class="kpi-val">${{fmt(k.consumo)}}</div><div class="kpi-label">CONSUMIDO DESDE REPOSICIÓN</div><div class="kpi-sub">reset al ingresar stock</div></div>
    <div class="kpi"><div class="kpi-val ${{k.sinStock>0?'warn':''}}">${{k.sinStock}}</div><div class="kpi-label">SIN STOCK EN DEPÓSITO</div><div class="kpi-sub">artículos con consumo activo</div></div>
  `;
}}

// ── Pedido realizado checkbox ─────────────────────────────────
function togglePedido(cod) {{
  if (shared.pedido_realizado.includes(cod))
    shared.pedido_realizado = shared.pedido_realizado.filter(c=>c!==cod);
  else
    shared.pedido_realizado.push(cod);
  saveShared({{ pedido_realizado: shared.pedido_realizado }});
  const row = document.querySelector(`tr[data-cod="${{cod}}"]`);
  if (row) row.classList.toggle('pedido-done', shared.pedido_realizado.includes(cod));
}}

// ── Solicitudes cartones ──────────────────────────────────────
function agregarSolicitud() {{
  const desc = document.getElementById('sol-desc').value.trim();
  const cant = parseInt(document.getElementById('sol-cant').value) || 0;
  const nota = document.getElementById('sol-nota').value.trim();
  if (!desc || cant <= 0) {{ alert('Completá descripción y cantidad.'); return; }}
  (shared.solicitudes_cartones = shared.solicitudes_cartones||[]).push({{
    id: Date.now().toString(),
    descripcion: desc, cantidad: cant, nota: nota,
    fecha: new Date().toLocaleString('es-AR'), atendido: false
  }});
  saveShared({{ solicitudes_cartones: shared.solicitudes_cartones }});
  document.getElementById('sol-desc').value='';
  document.getElementById('sol-cant').value='';
  document.getElementById('sol-nota').value='';
  renderSolicitudes();
  actualizarBadgeTab();
}}
function atenderSolicitud(id) {{
  const s = (shared.solicitudes_cartones||[]).find(x=>x.id===id);
  if (s) {{ s.atendido=true; saveShared({{solicitudes_cartones:shared.solicitudes_cartones}}); renderSolicitudes(); actualizarBadgeTab(); }}
}}
function renderSolicitudes() {{
  const pend = (shared.solicitudes_cartones||[]).filter(s=>!s.atendido);
  const div = document.getElementById('sol-cards');
  if (!div) return;
  if (pend.length===0) {{ div.innerHTML='<span class="sol-vacas">Sin solicitudes pendientes</span>'; return; }}
  div.innerHTML = pend.map(s=>`
    <div class="sol-card">
      <span class="sol-card-urg">URGENTE</span>
      <strong>${{s.descripcion}}</strong> &nbsp;×${{s.cantidad}}
      ${{s.nota?`<span style="color:#555"> · ${{s.nota}}</span>`:''}}
      <span style="color:#333;font-size:11px;"> · ${{s.fecha}}</span>
      <button class="sol-card-atender" onclick="atenderSolicitud('${{s.id}}')">Atendido ✓</button>
    </div>`).join('');
}}

// ── Sección genérica ──────────────────────────────────────────
function makeSection(id) {{
  const data = DATASETS[id];
  let filt = data.filter(r => r.Consumido > 0 && !shared.desuso.includes(r.Codigo));
  let pg = 1, sortCol = -1, sortDir = 1, critico = false;

  const sel = document.getElementById(id+'-prov');
  (PROVS_MAP[id]||[]).forEach(p => {{
    const o=document.createElement('option'); o.value=p; o.textContent=p; sel.appendChild(o);
  }});

  function filtrar() {{
    const prov    = document.getElementById(id+'-prov').value;
    const bus     = document.getElementById(id+'-bus').value.toLowerCase().trim();
    const sinCons = document.getElementById(id+'-sincons').checked;
    filt = data.filter(r => {{
      if (shared.desuso.includes(r.Codigo)) return false;
      if (critico && !(r.StockActual<=0 || r.Consumido>r.StockActual)) return false;
      if (!sinCons && !critico && r.Consumido===0) return false;
      if (prov && r.Proveedor!==prov) return false;
      if (bus && !(r.Codigo.toLowerCase().includes(bus)||r.Descripcion.toLowerCase().includes(bus))) return false;
      return true;
    }});
    pg=1; render();
  }}

  function toggleCritico() {{
    critico=!critico;
    const btn=document.getElementById(id+'-critico');
    btn.style.background  = critico?'#f97316':'#2a1010';
    btn.style.color       = critico?'#000':'#f97316';
    btn.style.borderColor = critico?'#f97316':'#6b2020';
    filtrar();
  }}

  function sortBy(col) {{
    const ths=document.querySelectorAll('#sec-'+id+' thead th');
    if(sortCol===col) sortDir=-sortDir; else {{sortCol=col;sortDir=1;}}
    ths.forEach((th,i)=>{{th.classList.remove('asc','desc');if(i===col)th.classList.add(sortDir===1?'asc':'desc');}});
    filt.sort((a,b)=>{{
      const pc=String(a.Proveedor).localeCompare(String(b.Proveedor),'es');
      if(pc!==0) return pc;
      const f=FIELDS[col],av=a[f],bv=b[f];
      return typeof av==='number'?sortDir*(av-bv):sortDir*String(av).localeCompare(String(bv),'es');
    }});
    pg=1; render();
  }}

  function render() {{
    const provFilter=document.getElementById(id+'-prov').value;
    const total=filt.length, start=(pg-1)*PG, slice=filt.slice(start,start+PG);
    const pages=Math.max(1,Math.ceil(total/PG));
    document.getElementById(id+'-cnt').textContent=total+' artículo'+(total!==1?'s':'')+(total!==data.length?' (filtrado)':'');

    let html='', curProv=null;
    const agrupar=!provFilter;
    slice.forEach(r=>{{
      if(agrupar && r.Proveedor!==curProv){{
        curProv=r.Proveedor;
        const tc=filt.filter(x=>x.Proveedor===curProv).reduce((s,x)=>s+x.Consumido,0);
        html+=`<tr class="group-row"><td colspan="4">▼ ${{curProv}}</td><td class="num">${{fmt(tc)}}</td><td colspan="4"></td></tr>`;
      }}
      const isUrg = shared.urgente.includes(r.Codigo);
      const isDone = shared.pedido_realizado.includes(r.Codigo);
      const sinStock = r.StockActual<=0 && r.Consumido>0;
      const sc = sinStock?'warn-stock':(r.StockActual>0?'ok-stock':'');
      const stmin = shared.stock_minimo[r.Codigo];
      let stminCls = '';
      if (stmin!==undefined && stmin>0) {{
        if (r.StockActual <= stmin) stminCls='stmin-bajo';
        else if (r.StockActual <= stmin*2) stminCls='stmin-alerta';
      }}
      const rowCls = isUrg?'urg-row':(isDone?'pedido-done':'');
      const urgBadge = isUrg?'<span class="badge-urg">URGENTE</span>':'';
      html+=`<tr data-cod="${{r.Codigo}}" class="${{rowCls}}">
        <td style="font-size:11px;color:#aaa">${{r.Codigo}}</td>
        <td>${{r.Descripcion}}${{urgBadge}}</td>
        <td><span class="unidad-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="unidad">${{r.Unidad}}</span></td>
        <td class="num">${{r.Consumido>0?fmt(r.Consumido):'<span style="color:#444">—</span>'}}</td>
        <td class="num ${{sc}} ${{stminCls}}">${{fmt(r.StockActual)}}</td>
        <td class="num"><span class="stmin-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="stmin"></span></td>
        <td><span class="nota-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="logistica"></span></td>
        <td><span class="nota-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="compras"></span></td>
        <td style="text-align:center"><input type="checkbox" ${{isDone?'checked':''}} onchange="togglePedido('${{r.Codigo}}')" style="width:16px;height:16px;accent-color:var(--ok);cursor:pointer;"></td>
      </tr>`;
    }});
    document.getElementById(id+'-tbody').innerHTML=html;
    restoreEditable();

    const pag=document.getElementById(id+'-pag');
    pag.innerHTML=pages<=1?'':`
      <button onclick="S.${{id}}.irPag(${{pg-1}})" ${{pg===1?'disabled':''}}>&#8592; Ant.</button>
      <span>Pág. ${{pg}} / ${{pages}} (${{total}} artículos)</span>
      <button onclick="S.${{id}}.irPag(${{pg+1}})" ${{pg===pages?'disabled':''}}>Sig. &#8594;</button>`;
  }}

  function irPag(n){{
    const pages=Math.max(1,Math.ceil(filt.length/PG));
    if(n<1||n>pages) return; pg=n; render();
    document.getElementById(id+'-wrap').scrollTop=0;
  }}

  return {{filtrar,toggleCritico,sortBy,render,irPag}};
}}

// ── Init ──────────────────────────────────────────────────────
const S = {{}};
loadShared().then(() => {{
  S.ins  = makeSection('ins');
  S.cart = makeSection('cart');
  renderKpis('ins');
  S.ins.render();
  S.cart.render();
  renderSolicitudes();
  actualizarBadgeTab();
  renderDesusoTables();
}});
</script>
</body>
</html>"""

with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"\nReporte guardado: {OUTPUT}")
print(f"  Insumos: {ki['n_arts']} arts | Cartones: {kc['n_arts']} arts")
