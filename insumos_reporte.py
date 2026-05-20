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
    SELECT
        RTRIM(LTRIM(DET.MART))              AS Codigo,
        SUM(DET.CANTI)                      AS Consumido
    FROM {DB_CENTRAL}.Zoologic.MSTOCK MS
    INNER JOIN {DB_CENTRAL}.Zoologic.DETMSTOCK DET ON DET.NUMR = MS.CODIGO
    WHERE MS.DIRMOV = 2
      AND MS.MOTIVO = 13
      AND (MS.ANULADO IS NULL OR MS.ANULADO = 0)
      AND LEFT(RTRIM(DET.MART), 2) = 'ZZ'
      AND YEAR(MS.FECHA)  = {ANIO}
      AND MONTH(MS.FECHA) = {MES}
    GROUP BY RTRIM(LTRIM(DET.MART))
""", conn)
print(f"  Articulos con consumo este mes: {len(df_consumo)}")

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
mes_str = date.today().strftime('%B %Y').capitalize()

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
:root{{--bg:#0d0d0d;--card:#141414;--border:#2a2a2a;--accent:#e8b963;--text:#f0f0f0;--muted:#888;--warn:#f97316;--ok:#22c55e;--group:#1a2a1a;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:Arial,sans-serif;font-size:13px;}}
.page-header{{background:#111;border-bottom:2px solid var(--accent);padding:16px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}}
.page-title{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:20px;color:var(--accent);letter-spacing:1px;}}
.nav-links{{display:flex;gap:8px;margin-left:auto;}}
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
.toolbar select,.toolbar input{{background:#1a1a1a;border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:4px;font-size:12px;}}
.toolbar select:focus,.toolbar input:focus{{outline:none;border-color:var(--accent);}}
.toolbar input{{width:220px;}}
.cnt{{color:var(--muted);font-size:11px;padding:8px 24px 0;}}
.tbl-wrap{{overflow-x:auto;max-height:calc(100vh - 320px);margin:8px 24px 0;border:1px solid var(--border);border-radius:6px;}}
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
.unidad-cell{{color:#aaa;font-style:italic;cursor:text;border-radius:3px;padding:2px 4px;min-width:70px;display:inline-block;}}
.unidad-cell:focus{{outline:1px solid var(--accent);color:var(--text);font-style:normal;background:#1e1e1e;}}
.nota-cell{{color:#aaa;cursor:text;border-radius:3px;padding:2px 6px;min-width:140px;display:inline-block;}}
.nota-cell:empty::before{{content:"…";color:#333;}}
.nota-cell:focus{{outline:1px solid var(--accent);color:var(--text);background:#1e1e1e;}}
.pag{{display:flex;gap:8px;align-items:center;padding:10px 24px;}}
.pag button{{background:#1a1a1a;border:1px solid var(--border);color:var(--muted);padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px;}}
.pag button:hover:not(:disabled){{border-color:var(--accent);color:var(--accent);}}
.pag button:disabled{{opacity:.3;cursor:default;}}
.pag span{{color:#555;font-size:11px;}}
</style>
</head>
<body>

<div class="page-header">
  <div>
    <div class="page-title">STOCK DE INSUMOS</div>
    <div style="color:var(--muted);font-size:11px;margin-top:3px;">Consumo {mes_str} &nbsp;·&nbsp; Generado {ahora}</div>
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
  <button class="tab-btn" onclick="switchTab('cart',this)">CARTONES</button>
</div>

<!-- ══ INSUMOS ══ -->
<div id="sec-ins" class="section active">
  <div class="toolbar">
    <select id="ins-prov" onchange="S.ins.filtrar()"><option value="">Todos los proveedores</option></select>
    <input id="ins-bus" type="text" placeholder="Buscar código o descripción..." oninput="S.ins.filtrar()">
    <button id="ins-critico" onclick="S.ins.toggleCritico()" style="background:#2a1010;border:1px solid #6b2020;color:#f97316;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;letter-spacing:.5px;">⚠ CRÍTICO</button>
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--muted);font-size:12px;">
      <input type="checkbox" id="ins-sincons" onchange="S.ins.filtrar()" style="accent-color:var(--accent);">
      Mostrar sin consumo
    </label>
    <div style="display:flex;gap:6px;margin-left:auto;">
      <button onclick="exportarNotas()" style="background:#1a1a1a;border:1px solid var(--border);color:var(--muted);padding:5px 12px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;">Exportar notas</button>
      <button onclick="importarNotas()" style="background:#1a1a1a;border:1px solid var(--border);color:var(--muted);padding:5px 12px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;">Importar notas</button>
    </div>
  </div>
  <div class="cnt" id="ins-cnt"></div>
  <div class="tbl-wrap" id="ins-wrap">
    <table>
      <thead><tr>
        <th onclick="S.ins.sortBy(0)">CÓDIGO</th>
        <th onclick="S.ins.sortBy(1)">DESCRIPCIÓN</th>
        <th onclick="S.ins.sortBy(2)">UNIDAD</th>
        <th onclick="S.ins.sortBy(3)" class="num">CONSUMIDO MES</th>
        <th onclick="S.ins.sortBy(4)" class="num">STOCK DEPÓSITO</th>
        <th style="min-width:160px;">LOGÍSTICA</th>
        <th style="min-width:160px;">COMPRAS</th>
      </tr></thead>
      <tbody id="ins-tbody"></tbody>
    </table>
  </div>
  <div class="pag" id="ins-pag"></div>
</div>

<!-- ══ CARTONES ══ -->
<div id="sec-cart" class="section">
  <div class="toolbar">
    <select id="cart-prov" onchange="S.cart.filtrar()"><option value="">Todos los proveedores</option></select>
    <input id="cart-bus" type="text" placeholder="Buscar código o descripción..." oninput="S.cart.filtrar()">
    <button id="cart-critico" onclick="S.cart.toggleCritico()" style="background:#2a1010;border:1px solid #6b2020;color:#f97316;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;letter-spacing:.5px;">⚠ CRÍTICO</button>
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--muted);font-size:12px;">
      <input type="checkbox" id="cart-sincons" onchange="S.cart.filtrar()" style="accent-color:var(--accent);">
      Mostrar sin consumo
    </label>
  </div>
  <div class="cnt" id="cart-cnt"></div>
  <div class="tbl-wrap" id="cart-wrap">
    <table>
      <thead><tr>
        <th onclick="S.cart.sortBy(0)">CÓDIGO</th>
        <th onclick="S.cart.sortBy(1)">DESCRIPCIÓN</th>
        <th onclick="S.cart.sortBy(2)">UNIDAD</th>
        <th onclick="S.cart.sortBy(3)" class="num">CONSUMIDO MES</th>
        <th onclick="S.cart.sortBy(4)" class="num">STOCK DEPÓSITO</th>
        <th style="min-width:160px;">LOGÍSTICA</th>
        <th style="min-width:160px;">COMPRAS</th>
      </tr></thead>
      <tbody id="cart-tbody"></tbody>
    </table>
  </div>
  <div class="pag" id="cart-pag"></div>
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
const PG     = 150;
const FIELDS = ['Codigo','Descripcion','Unidad','Consumido','StockActual'];

// ── Persistencia ──────────────────────────────────────────────
const STORE_KEY = 'insumos_notas_v1';
let notas = {{}};
try {{ notas = JSON.parse(localStorage.getItem(STORE_KEY) || '{{}}'); }} catch(e) {{}}
function saveNotas() {{ try {{ localStorage.setItem(STORE_KEY, JSON.stringify(notas)); }} catch(e) {{}} }}

function restoreEditable() {{
  document.querySelectorAll('[data-cod][data-field]').forEach(el => {{
    const cod = el.dataset.cod, field = el.dataset.field;
    const saved = notas[cod] && notas[cod][field];
    if (saved) el.textContent = saved;
    el.addEventListener('input', () => {{
      if (!notas[cod]) notas[cod] = {{}};
      notas[cod][field] = el.textContent.trim();
      saveNotas();
    }});
  }});
}}
function exportarNotas() {{
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(notas,null,2)],{{type:'application/json'}}));
  a.download = 'insumos_notas.json'; a.click();
}}
function importarNotas() {{
  const inp = document.createElement('input'); inp.type='file'; inp.accept='.json';
  inp.onchange = e => {{
    const r = new FileReader();
    r.onload = ev => {{ try {{ notas=JSON.parse(ev.target.result); saveNotas(); S.ins.render(); S.cart.render(); alert('Importado.'); }} catch(e){{alert('Inválido.');}} }};
    r.readAsText(e.target.files[0]);
  }}; inp.click();
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

function fmt(n) {{ return Number(n).toLocaleString('es-AR'); }}

function renderKpis(id) {{
  const k = KPI_DATA[id];
  document.getElementById('kpi-row').innerHTML = `
    <div class="kpi"><div class="kpi-val">${{k.arts}}</div><div class="kpi-label">ARTÍCULOS</div><div class="kpi-sub">${{k.provs}} proveedores</div></div>
    <div class="kpi"><div class="kpi-val">${{fmt(k.consumo)}}</div><div class="kpi-label">CONSUMIDO ESTE MES</div><div class="kpi-sub">movimientos MOTIVO=13</div></div>
    <div class="kpi"><div class="kpi-val ${{k.sinStock>0?'warn':''}}">${{k.sinStock}}</div><div class="kpi-label">SIN STOCK EN DEPÓSITO</div><div class="kpi-sub">artículos sin reposición</div></div>
  `;
}}

// ── Sección genérica ──────────────────────────────────────────
function makeSection(id) {{
  const data = DATASETS[id];
  let filt = data.filter(r => r.Consumido > 0);
  let pg = 1, sortCol = -1, sortDir = 1, critico = false;

  // Populate select
  const sel = document.getElementById(id+'-prov');
  (PROVS_MAP[id]||[]).forEach(p => {{
    const o = document.createElement('option'); o.value=p; o.textContent=p; sel.appendChild(o);
  }});

  function filtrar() {{
    const prov    = document.getElementById(id+'-prov').value;
    const bus     = document.getElementById(id+'-bus').value.toLowerCase().trim();
    const sinCons = document.getElementById(id+'-sincons').checked;
    filt = data.filter(r => {{
      if (critico && !(r.StockActual<=0 || r.Consumido>r.StockActual)) return false;
      if (!sinCons && !critico && r.Consumido===0) return false;
      if (prov && r.Proveedor!==prov) return false;
      if (bus && !(r.Codigo.toLowerCase().includes(bus) || r.Descripcion.toLowerCase().includes(bus))) return false;
      return true;
    }});
    pg=1; render();
  }}

  function toggleCritico() {{
    critico = !critico;
    const btn = document.getElementById(id+'-critico');
    btn.style.background    = critico ? '#f97316' : '#2a1010';
    btn.style.color         = critico ? '#000'    : '#f97316';
    btn.style.borderColor   = critico ? '#f97316' : '#6b2020';
    filtrar();
  }}

  function sortBy(col) {{
    const ths = document.querySelectorAll('#sec-'+id+' thead th');
    if (sortCol===col) sortDir=-sortDir; else {{sortCol=col; sortDir=1;}}
    ths.forEach((th,i)=>{{th.classList.remove('asc','desc'); if(i===col) th.classList.add(sortDir===1?'asc':'desc');}});
    filt.sort((a,b)=>{{
      const pc = String(a.Proveedor).localeCompare(String(b.Proveedor),'es');
      if (pc!==0) return pc;
      const f=FIELDS[col], av=a[f], bv=b[f];
      return typeof av==='number' ? sortDir*(av-bv) : sortDir*String(av).localeCompare(String(bv),'es');
    }});
    pg=1; render();
  }}

  function render() {{
    const provFilter = document.getElementById(id+'-prov').value;
    const total=filt.length, start=(pg-1)*PG, slice=filt.slice(start,start+PG);
    const pages=Math.max(1,Math.ceil(total/PG));
    document.getElementById(id+'-cnt').textContent =
      total+' artículo'+(total!==1?'s':'')+(total!==data.length?' (filtrado)':'');

    let html='', curProv=null;
    const agrupar = !provFilter;
    slice.forEach(r=>{{
      if (agrupar && r.Proveedor!==curProv) {{
        curProv=r.Proveedor;
        const tc=filt.filter(x=>x.Proveedor===curProv).reduce((s,x)=>s+x.Consumido,0);
        html+=`<tr class="group-row"><td colspan="3">▼ ${{curProv}}</td><td class="num">${{fmt(tc)}}</td><td colspan="3"></td></tr>`;
      }}
      const sinStock=r.StockActual<=0&&r.Consumido>0;
      const sc=sinStock?'warn-stock':(r.StockActual>0?'ok-stock':'');
      html+=`<tr>
        <td>${{r.Codigo}}</td>
        <td>${{r.Descripcion}}</td>
        <td><span class="unidad-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="unidad">${{r.Unidad}}</span></td>
        <td class="num">${{r.Consumido>0?fmt(r.Consumido):'<span style="color:#444">—</span>'}}</td>
        <td class="num ${{sc}}">${{fmt(r.StockActual)}}</td>
        <td><span class="nota-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="logistica"></span></td>
        <td><span class="nota-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="compras"></span></td>
      </tr>`;
    }});
    document.getElementById(id+'-tbody').innerHTML=html;
    restoreEditable();

    const pag=document.getElementById(id+'-pag');
    pag.innerHTML = pages<=1 ? '' : `
      <button onclick="S.${{id}}.irPag(${{pg-1}})" ${{pg===1?'disabled':''}}>&#8592; Ant.</button>
      <span>Pág. ${{pg}} / ${{pages}} (${{total}} artículos)</span>
      <button onclick="S.${{id}}.irPag(${{pg+1}})" ${{pg===pages?'disabled':''}}>Sig. &#8594;</button>`;
  }}

  function irPag(n) {{
    const pages=Math.max(1,Math.ceil(filt.length/PG));
    if(n<1||n>pages) return; pg=n; render();
    document.getElementById(id+'-wrap').scrollTop=0;
  }}

  return {{filtrar, toggleCritico, sortBy, render, irPag}};
}}

const S = {{ ins: makeSection('ins'), cart: makeSection('cart') }};
renderKpis('ins');
S.ins.render();
S.cart.render();
</script>
</body>
</html>"""

with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"\nReporte guardado: {OUTPUT}")
print(f"  Insumos: {ki['n_arts']} arts | Cartones: {kc['n_arts']} arts")
