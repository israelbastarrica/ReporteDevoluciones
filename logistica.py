"""
logistica.py — Reporte Preliminar de Logística Entrante
Sección 1: Pedidos pendientes de proveedores, agrupados por artículo
Sección 2: Mercadería de locales en tránsito, agrupada por artículo
"""
import pyodbc
import pandas as pd
import json
import warnings
from datetime import datetime
from config import SERVER, DB_CENTRAL, DB_LURO, DB_PERALTA, USER, PASSWORD

warnings.filterwarnings('ignore', category=UserWarning)


def conectar():
    return pyodbc.connect(
        f'DRIVER={{SQL Server}};SERVER={SERVER};'
        f'DATABASE={DB_CENTRAL};UID={USER};PWD={PASSWORD}'
    )


def obtener_datos():
    print("\n" + "="*55)
    print("  Logistica Entrante - Cargando datos...")
    print("="*55)
    conn = conectar()

    # ── 1. Pedidos pendientes de proveedores (detalle por linea) ──
    print("  [1/2] Pedidos a proveedores pendientes...")
    df_prov = pd.read_sql(f"""
        WITH Recibido AS (
            SELECT AFENUMCOM, AFELETRA, AFENROITEM, SUM(FCANT) AS CantRec
            FROM {DB_CENTRAL}.Zoologic.REMCOMPRADET
            GROUP BY AFENUMCOM, AFELETRA, AFENROITEM
        )
        SELECT
            PC.FNUMCOMP                                               AS NroPedido,
            RTRIM(PC.FPERSON)                                        AS CodProveedor,
            CONVERT(VARCHAR(10), PC.FFCH, 120)                       AS Fecha,
            CASE WHEN PC.FFCHENTR <= '1901-01-01' OR PC.FFCHENTR IS NULL
                 THEN NULL
                 ELSE CONVERT(VARCHAR(10), PC.FFCHENTR, 120) END     AS FechaEntrega,
            RTRIM(PD.FART)                                           AS Codigo,
            RTRIM(PD.FTXT)                                           AS Descripcion,
            PD.FCANT                                                 AS Pedido,
            ISNULL(R.CantRec, 0)                                     AS Recibido,
            PD.FCANT - ISNULL(R.CantRec, 0)                         AS Pendiente
        FROM {DB_CENTRAL}.Zoologic.PEDCOMPRA PC
        JOIN {DB_CENTRAL}.Zoologic.PEDCOMPRADET PD ON PD.CODIGO = PC.CODIGO
        LEFT JOIN Recibido R
            ON R.AFENUMCOM = PC.FNUMCOMP
            AND R.AFELETRA = PC.FLETRA
            AND R.AFENROITEM = PD.NROITEM
        WHERE (PC.ANULADO IS NULL OR PC.ANULADO = 0)
          AND LEFT(RTRIM(PD.FART), 1) NOT IN ('Z', '9')
          AND RTRIM(PD.FART) <> ''
          AND RTRIM(PD.FTXT) <> ''
          AND PD.FCANT - ISNULL(R.CantRec, 0) > 0
    """, conn)
    for col in ('Pedido', 'Recibido', 'Pendiente'):
        df_prov[col] = pd.to_numeric(df_prov[col], errors='coerce').fillna(0).astype(int)
    print(f"    > {len(df_prov):,} lineas | {df_prov['NroPedido'].nunique():,} pedidos | "
          f"{df_prov['CodProveedor'].nunique():,} proveedores")

    # ── 2. Mercadería de locales en tránsito (detalle por artículo) ──
    print("  [2/2] Mercaderia de locales en transito...")
    df_loc_raw = pd.read_sql(f"""
        SELECT CV.FNUMCOMP                         AS Remito,
               CAST(CV.FFCH AS DATE)               AS Fecha,
               'LURO'                              AS Local,
               RTRIM(CVD.FART)                     AS Codigo,
               MAX(CVD.FTXT)                       AS Descripcion,
               ISNULL(MAX(FAM.DESCRIP),'SIN FAM')  AS Familia,
               SUM(CVD.FCANT)                      AS Prendas
        FROM {DB_LURO}.Zoologic.COMPROBANTEV CV
        INNER JOIN {DB_LURO}.Zoologic.COMPROBANTEVDET CVD ON CV.CODIGO = CVD.CODIGO
        LEFT JOIN {DB_CENTRAL}.Zoologic.MTRANS MT
            ON MT.ORIGNRO = CV.FNUMCOMP
            AND UPPER(RTRIM(LTRIM(MT.ORIGDEST))) = 'LURO'
            AND MT.ORIGLETRA = 'R'
        LEFT JOIN {DB_CENTRAL}.Zoologic.ART ART ON RTRIM(CVD.FART) = ART.ARTCOD
        LEFT JOIN {DB_CENTRAL}.Zoologic.FAMILIA FAM ON FAM.COD = ART.FAMILIA
        WHERE CV.FLETRA = 'R'
          AND (CV.ANULADO IS NULL OR CV.ANULADO = 0)
          AND MT.CODIGO IS NULL
          AND CVD.FTXT NOT LIKE '%BOLSA%'
          AND LEFT(RTRIM(CVD.FART), 1) NOT IN ('Z', '9')
        GROUP BY CV.FNUMCOMP, CAST(CV.FFCH AS DATE), RTRIM(CVD.FART)
        UNION ALL
        SELECT CV.FNUMCOMP,
               CAST(CV.FFCH AS DATE),
               'PERALTA',
               RTRIM(CVD.FART),
               MAX(CVD.FTXT),
               ISNULL(MAX(FAM.DESCRIP),'SIN FAM'),
               SUM(CVD.FCANT)
        FROM {DB_PERALTA}.Zoologic.COMPROBANTEV CV
        INNER JOIN {DB_PERALTA}.Zoologic.COMPROBANTEVDET CVD ON CV.CODIGO = CVD.CODIGO
        LEFT JOIN {DB_CENTRAL}.Zoologic.MTRANS MT
            ON MT.ORIGNRO = CV.FNUMCOMP
            AND UPPER(RTRIM(LTRIM(MT.ORIGDEST))) = 'PERALTA'
            AND MT.ORIGLETRA = 'R'
        LEFT JOIN {DB_CENTRAL}.Zoologic.ART ART ON RTRIM(CVD.FART) = ART.ARTCOD
        LEFT JOIN {DB_CENTRAL}.Zoologic.FAMILIA FAM ON FAM.COD = ART.FAMILIA
        WHERE CV.FLETRA = 'R'
          AND (CV.ANULADO IS NULL OR CV.ANULADO = 0)
          AND MT.CODIGO IS NULL
          AND CVD.FTXT NOT LIKE '%BOLSA%'
          AND LEFT(RTRIM(CVD.FART), 1) NOT IN ('Z', '9')
        GROUP BY CV.FNUMCOMP, CAST(CV.FFCH AS DATE), RTRIM(CVD.FART)
    """, conn)
    df_loc_raw['Prendas'] = pd.to_numeric(df_loc_raw['Prendas'], errors='coerce').fillna(0).astype(int)
    print(f"    > {df_loc_raw['Prendas'].sum():,} prendas | "
          f"{df_loc_raw['Remito'].nunique():,} remitos")

    conn.close()
    return df_prov, df_loc_raw


def generar_html(df_prov, df_loc_raw, nombre="logistica.html"):
    ahora = datetime.now().strftime("%d/%m/%Y %H:%M")

    # ── Agrupar proveedores por artículo ─────────────────────────
    def prox_entrega(s):
        vals = s.dropna().replace('', None).dropna()
        return vals.min() if not vals.empty else ''

    df_prov_art = (
        df_prov
        .groupby(['Codigo', 'Descripcion'])
        .agg(
            Pedido=('Pedido', 'sum'),
            Recibido=('Recibido', 'sum'),
            Pendiente=('Pendiente', 'sum'),
            Pedidos=('NroPedido', 'nunique'),
            Proveedores=('CodProveedor', 'nunique'),
            UltimoPedido=('Fecha', 'max'),
            EntregaProxima=('FechaEntrega', prox_entrega),
        )
        .reset_index()
        .sort_values('Pendiente', ascending=False)
    )

    # Mapa proveedor -> set de codigos (para filtrar en JS)
    prov_map = (
        df_prov.groupby('CodProveedor')['Codigo']
        .apply(lambda x: list(set(x)))
        .to_dict()
    )
    proveedores = sorted(prov_map.keys())

    # ── Agrupar locales por artículo con columnas LURO/PERALTA ───
    pivot = (
        df_loc_raw
        .groupby(['Codigo', 'Descripcion', 'Familia', 'Local'])['Prendas']
        .sum()
        .unstack(fill_value=0)
        .reset_index()
    )
    pivot.columns.name = None
    for col in ('LURO', 'PERALTA'):
        if col not in pivot.columns:
            pivot[col] = 0
    pivot['Total'] = pivot['LURO'] + pivot['PERALTA']
    remitos_art = (
        df_loc_raw.groupby('Codigo')['Remito']
        .nunique()
        .reset_index()
        .rename(columns={'Remito': 'Remitos'})
    )
    df_loc_art = (
        pivot.merge(remitos_art, on='Codigo', how='left')
        .fillna({'Remitos': 0})
        .assign(Remitos=lambda d: d['Remitos'].astype(int),
                LURO=lambda d: d['LURO'].astype(int),
                PERALTA=lambda d: d['PERALTA'].astype(int),
                Total=lambda d: d['Total'].astype(int))
        .sort_values('Total', ascending=False)
    )

    # ── KPIs ─────────────────────────────────────────────────────
    n_arts_prov  = len(df_prov_art)
    n_pedidos    = df_prov['NroPedido'].nunique()
    n_prov       = df_prov['CodProveedor'].nunique()
    u_prov       = int(df_prov_art['Pendiente'].sum())
    n_arts_loc   = len(df_loc_art)
    n_remitos    = df_loc_raw['Remito'].nunique()
    u_loc        = int(df_loc_art['Total'].sum())

    # ── Serializar ───────────────────────────────────────────────
    def clean(lst):
        out = []
        for r in lst:
            out.append({k: ('' if v is None or (isinstance(v, float) and str(v) == 'nan') else v)
                        for k, v in r.items()})
        return out

    j_prov_art  = json.dumps(clean(df_prov_art.to_dict(orient='records')),  ensure_ascii=False)
    j_loc_art   = json.dumps(clean(df_loc_art.to_dict(orient='records')),   ensure_ascii=False)
    j_prov_list = json.dumps(proveedores, ensure_ascii=False)
    j_prov_map  = json.dumps(prov_map,    ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Logística Entrante — MARKET</title>
<style>
:root{{--bg:#0d0d0d;--card:#141414;--border:#2a2a2a;--accent:#e8b963;--text:#f0f0f0;--muted:#888;--pend:#f97316;--ok:#22c55e;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:Arial,sans-serif;font-size:13px;}}
a{{color:var(--accent);text-decoration:none;}}
.page-header{{background:#111;border-bottom:2px solid var(--accent);padding:16px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}}
.page-title{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:20px;color:var(--accent);letter-spacing:1px;}}
.nav-links{{display:flex;gap:8px;margin-left:auto;}}
.nav-links a{{background:#1e1e1e;border:1px solid var(--border);color:var(--muted);padding:5px 12px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:.5px;}}
.nav-links a:hover{{border-color:var(--accent);color:var(--accent);}}
.kpi-row{{display:flex;gap:12px;padding:16px 24px;flex-wrap:wrap;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:12px 18px;min-width:150px;flex:1;}}
.kpi-val{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:26px;color:var(--accent);}}
.kpi-label{{color:var(--muted);font-size:11px;margin-top:2px;}}
.kpi-sub{{color:#555;font-size:11px;margin-top:2px;}}
.tabs{{display:flex;gap:0;padding:0 24px;border-bottom:1px solid var(--border);}}
.tab-btn{{background:none;border:none;color:var(--muted);font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:13px;letter-spacing:.5px;padding:12px 20px;cursor:pointer;border-bottom:3px solid transparent;transition:.15s;}}
.tab-btn.active{{color:var(--accent);border-bottom-color:var(--accent);}}
.section{{padding:16px 24px;display:none;}}
.section.active{{display:block;}}
.filters{{display:flex;gap:10px;margin-bottom:10px;flex-wrap:wrap;align-items:center;}}
.filters select,.filters input{{background:#1a1a1a;border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:4px;font-size:12px;}}
.filters select:focus,.filters input:focus{{outline:none;border-color:var(--accent);}}
.filters input{{width:220px;}}
.loc-toggle{{display:flex;gap:0;}}
.loc-btn{{background:#1a1a1a;border:1px solid var(--border);color:var(--muted);padding:5px 14px;cursor:pointer;font-size:11px;font-weight:700;}}
.loc-btn:first-child{{border-radius:4px 0 0 4px;}}
.loc-btn:last-child{{border-radius:0 4px 4px 0;}}
.loc-btn.active{{background:var(--accent);color:#000;border-color:var(--accent);}}
.tbl-wrap{{overflow-x:auto;max-height:calc(100vh - 310px);border:1px solid var(--border);border-radius:6px;}}
table{{width:100%;border-collapse:collapse;}}
thead th{{background:#1a1a1a;color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.5px;padding:8px 10px;text-align:left;position:sticky;top:0;z-index:2;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;}}
thead th:hover{{color:var(--accent);}}
thead th.asc::after{{content:" ▲";color:var(--accent);font-size:9px;}}
thead th.desc::after{{content:" ▼";color:var(--accent);font-size:9px;}}
tbody tr{{border-bottom:1px solid #1e1e1e;transition:background .1s;}}
tbody tr:hover{{background:#181818;}}
tbody td{{padding:7px 10px;white-space:nowrap;}}
.num{{text-align:right;font-variant-numeric:tabular-nums;}}
.pend{{color:var(--pend);font-weight:700;}}
.ok{{color:var(--ok);}}
.cnt{{color:var(--muted);font-size:11px;margin-bottom:8px;}}
.pag{{display:flex;gap:8px;align-items:center;padding:10px 0;}}
.pag button{{background:#1a1a1a;border:1px solid var(--border);color:var(--muted);padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px;}}
.pag button:hover:not(:disabled){{border-color:var(--accent);color:var(--accent);}}
.pag button:disabled{{opacity:.3;cursor:default;}}
.pag span{{color:#555;font-size:11px;}}
.badge-prov{{background:#1e2e1e;color:#7ef7a0;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;}}
.badge-luro{{background:#1e3a5f;color:#7eb8f7;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;}}
.badge-per{{background:#3a1e5f;color:#c07ef7;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;}}
</style>
</head>
<body>

<div class="page-header">
  <div>
    <div class="page-title">LOGÍSTICA ENTRANTE</div>
    <div style="color:var(--muted);font-size:11px;margin-top:3px;">Versión preliminar · {ahora}</div>
  </div>
  <div class="nav-links">
    <a href="index.html">DEVOLUCIONES</a>
    <a href="envios.html">ENVÍOS</a>
    <a href="pendientes.html">PENDIENTES</a>
    <a href="dashboard.html">DASHBOARD</a>
  </div>
</div>

<div class="kpi-row">
  <div class="kpi">
    <div class="kpi-val">{n_arts_prov:,}</div>
    <div class="kpi-label">ARTÍCULOS PENDIENTES</div>
    <div class="kpi-sub">de proveedores · {n_pedidos} pedidos · {n_prov} provs.</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">{u_prov:,}</div>
    <div class="kpi-label">PRENDAS POR LLEGAR</div>
    <div class="kpi-sub">de proveedores externos</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">{n_arts_loc:,}</div>
    <div class="kpi-label">ARTÍCULOS EN TRÁNSITO</div>
    <div class="kpi-sub">de locales · {n_remitos} remitos</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">{u_loc:,}</div>
    <div class="kpi-label">PRENDAS DE LOCALES</div>
    <div class="kpi-sub">sin procesar en Central</div>
  </div>
</div>

<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('prov',this)">PROVEEDORES</button>
  <button class="tab-btn" onclick="switchTab('loc',this)">DE LOCALES</button>
</div>

<!-- PROVEEDORES -->
<div id="sec-prov" class="section active">
  <div class="filters">
    <select id="sel-prov" onchange="filtrarProv()">
      <option value="">Todos los proveedores</option>
    </select>
    <input id="bus-prov" type="text" placeholder="Buscar código / descripción..." oninput="filtrarProv()">
  </div>
  <div class="cnt" id="cnt-prov"></div>
  <div class="tbl-wrap">
    <table>
      <thead><tr>
        <th onclick="sortProv(0)">CÓDIGO</th>
        <th onclick="sortProv(1)">DESCRIPCIÓN</th>
        <th onclick="sortProv(2)">ÚLT. PEDIDO</th>
        <th onclick="sortProv(3)">ENTREGA EST.</th>
        <th onclick="sortProv(4)" class="num">PEDIDOS</th>
        <th onclick="sortProv(5)" class="num">PROVEEDORES</th>
        <th onclick="sortProv(6)" class="num">PEDIDO</th>
        <th onclick="sortProv(7)" class="num">RECIBIDO</th>
        <th onclick="sortProv(8)" class="num">PENDIENTE</th>
      </tr></thead>
      <tbody id="body-prov"></tbody>
    </table>
  </div>
  <div class="pag" id="pag-prov"></div>
</div>

<!-- DE LOCALES -->
<div id="sec-loc" class="section">
  <div class="filters">
    <div class="loc-toggle">
      <button class="loc-btn active" onclick="setLocal('AMBOS',this)">AMBOS</button>
      <button class="loc-btn" onclick="setLocal('LURO',this)">LURO</button>
      <button class="loc-btn" onclick="setLocal('PERALTA',this)">PERALTA</button>
    </div>
    <input id="bus-loc" type="text" placeholder="Buscar código / descripción..." oninput="filtrarLoc()">
  </div>
  <div class="cnt" id="cnt-loc"></div>
  <div class="tbl-wrap">
    <table>
      <thead><tr>
        <th onclick="sortLoc(0)">CÓDIGO</th>
        <th onclick="sortLoc(1)">DESCRIPCIÓN</th>
        <th onclick="sortLoc(2)">FAMILIA</th>
        <th onclick="sortLoc(3)" class="num">REMITOS</th>
        <th onclick="sortLoc(4)" class="num" id="th-luro">LURO</th>
        <th onclick="sortLoc(5)" class="num" id="th-per">PERALTA</th>
        <th onclick="sortLoc(6)" class="num">TOTAL</th>
      </tr></thead>
      <tbody id="body-loc"></tbody>
    </table>
  </div>
  <div class="pag" id="pag-loc"></div>
</div>

<script>
const DATA_PROV = {j_prov_art};
const DATA_LOC  = {j_loc_art};
const PROVS     = {j_prov_list};
const PROV_MAP  = {j_prov_map};

const PG = 150;
const PROV_FIELDS = ['Codigo','Descripcion','UltimoPedido','EntregaProxima','Pedidos','Proveedores','Pedido','Recibido','Pendiente'];
const LOC_FIELDS  = ['Codigo','Descripcion','Familia','Remitos','LURO','PERALTA','Total'];

let localActivo = 'AMBOS';
let filtProv = [...DATA_PROV];
let filtLoc  = [...DATA_LOC];
let pgProv = 1, pgLoc = 1;
const sd = {{}};

// Tabs
function switchTab(id, btn) {{
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('sec-'+id).classList.add('active');
}}

// Populate provider select
const sel = document.getElementById('sel-prov');
PROVS.forEach(p => {{
  const o = document.createElement('option'); o.value = p;
  o.textContent = 'Prov. ' + p; sel.appendChild(o);
}});

// ── PROVEEDORES ──────────────────────────────────────────────
function filtrarProv() {{
  const prov = document.getElementById('sel-prov').value;
  const bus  = document.getElementById('bus-prov').value.toLowerCase();
  const codes = prov ? new Set(PROV_MAP[prov]||[]) : null;
  filtProv = DATA_PROV.filter(r => {{
    if (codes && !codes.has(r.Codigo)) return false;
    if (bus && !(r.Codigo.toLowerCase().includes(bus) ||
                 r.Descripcion.toLowerCase().includes(bus))) return false;
    return true;
  }});
  pgProv = 1; renderProv();
}}

function renderProv() {{
  const n = filtProv.length;
  const maxPg = Math.ceil(n/PG)||1;
  pgProv = Math.max(1, Math.min(pgProv, maxPg));
  const page = filtProv.slice((pgProv-1)*PG, pgProv*PG);
  const totPend = filtProv.reduce((a,r)=>a+(r.Pendiente||0),0);
  const totPed  = filtProv.reduce((a,r)=>a+(r.Pedido||0),0);
  document.getElementById('cnt-prov').innerHTML =
    n.toLocaleString()+' artículos &nbsp;·&nbsp; '+
    totPend.toLocaleString()+' pendientes de '+totPed.toLocaleString()+' pedidos'+
    (maxPg>1?' &nbsp;<span style="color:#555">pág '+pgProv+'/'+maxPg+'</span>':'');
  document.getElementById('body-prov').innerHTML = page.map(r => {{
    const pct = r.Pedido>0 ? Math.round(r.Recibido/r.Pedido*100) : 0;
    const entrega = r.EntregaProxima || '';
    const hoy = new Date().toISOString().slice(0,10);
    const vencido = entrega && entrega < hoy;
    const entStyle = vencido ? 'color:#ef4444;font-weight:700' : 'color:#e8b963';
    return `<tr>
      <td style="font-family:monospace;font-size:11px;color:var(--accent)">${{r.Codigo}}</td>
      <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis" title="${{r.Descripcion}}">${{r.Descripcion}}</td>
      <td style="color:#aaa">${{r.UltimoPedido||'—'}}</td>
      <td style="${{entStyle}}">${{entrega||'<span style="color:#555">—</span>'}}</td>
      <td class="num"><span style="color:#aaa">${{r.Pedidos}}</span></td>
      <td class="num"><span style="color:#aaa">${{r.Proveedores}}</span></td>
      <td class="num">${{r.Pedido.toLocaleString()}}</td>
      <td class="num ok">${{r.Recibido.toLocaleString()}}${{pct>0?' <span style="color:#555;font-size:10px">('+pct+'%)</span>':''}}</td>
      <td class="num pend">${{r.Pendiente.toLocaleString()}}</td>
    </tr>`;
  }}).join('');
  renderPag('pag-prov', pgProv, maxPg, ()=>{{pgProv++;renderProv();}}, ()=>{{pgProv--;renderProv();}});
}}

function sortProv(col) {{
  applySortHeaders('prov', col);
  const f = PROV_FIELDS[col];
  const asc = sd['p'+col] !== true; sd['p'+col] = asc;
  filtProv.sort((a,b) => cmp(a[f], b[f], asc));
  pgProv=1; renderProv();
}}

// ── DE LOCALES ───────────────────────────────────────────────
function setLocal(l, btn) {{
  document.querySelectorAll('.loc-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active'); localActivo=l;
  document.getElementById('th-luro').style.opacity  = l==='PERALTA' ? '0.3' : '1';
  document.getElementById('th-per').style.opacity   = l==='LURO'    ? '0.3' : '1';
  filtrarLoc();
}}

function filtrarLoc() {{
  const bus = document.getElementById('bus-loc').value.toLowerCase();
  filtLoc = DATA_LOC.filter(r => {{
    if (localActivo==='LURO'    && !(r.LURO>0))    return false;
    if (localActivo==='PERALTA' && !(r.PERALTA>0)) return false;
    if (bus && !(r.Codigo.toLowerCase().includes(bus) ||
                 r.Descripcion.toLowerCase().includes(bus) ||
                 (r.Familia||'').toLowerCase().includes(bus))) return false;
    return true;
  }});
  pgLoc=1; renderLoc();
}}

function renderLoc() {{
  const n = filtLoc.length;
  const maxPg = Math.ceil(n/PG)||1;
  pgLoc = Math.max(1, Math.min(pgLoc, maxPg));
  const page = filtLoc.slice((pgLoc-1)*PG, pgLoc*PG);
  const totLuro    = filtLoc.reduce((a,r)=>a+(r.LURO||0),0);
  const totPeralta = filtLoc.reduce((a,r)=>a+(r.PERALTA||0),0);
  const totTotal   = filtLoc.reduce((a,r)=>a+(r.Total||0),0);
  document.getElementById('cnt-loc').innerHTML =
    n.toLocaleString()+' artículos &nbsp;·&nbsp; '+
    `<span class="badge-luro">LURO ${{totLuro.toLocaleString()}}</span> &nbsp;`+
    `<span class="badge-per">PERALTA ${{totPeralta.toLocaleString()}}</span> &nbsp;`+
    `<b style="color:var(--pend)">${{totTotal.toLocaleString()}} total</b>`+
    (maxPg>1?' &nbsp;<span style="color:#555">pág '+pgLoc+'/'+maxPg+'</span>':'');
  document.getElementById('body-loc').innerHTML = page.map(r => `<tr>
    <td style="font-family:monospace;font-size:11px;color:var(--accent)">${{r.Codigo}}</td>
    <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis" title="${{r.Descripcion}}">${{r.Descripcion}}</td>
    <td style="color:#aaa">${{r.Familia||'—'}}</td>
    <td class="num"><span style="color:#aaa">${{r.Remitos}}</span></td>
    <td class="num" style="${{localActivo==='PERALTA'?'opacity:.3':''}}">${{(r.LURO||0).toLocaleString()}}</td>
    <td class="num" style="${{localActivo==='LURO'?'opacity:.3':''}}">${{(r.PERALTA||0).toLocaleString()}}</td>
    <td class="num pend">${{r.Total.toLocaleString()}}</td>
  </tr>`).join('');
  renderPag('pag-loc', pgLoc, maxPg, ()=>{{pgLoc++;renderLoc();}}, ()=>{{pgLoc--;renderLoc();}});
}}

function sortLoc(col) {{
  applySortHeaders('loc', col);
  const field = localActivo==='LURO'?'LURO':localActivo==='PERALTA'?'PERALTA':LOC_FIELDS[col];
  const f = col>=4 ? field : LOC_FIELDS[col];
  const asc = sd['l'+col] !== true; sd['l'+col] = asc;
  filtLoc.sort((a,b) => cmp(a[f], b[f], asc));
  pgLoc=1; renderLoc();
}}

// ── Helpers ──────────────────────────────────────────────────
function cmp(va, vb, asc) {{
  if (typeof va==='number'||typeof vb==='number') return asc?(va-vb):(vb-va);
  const na=parseFloat(String(va).replace(/[^\\d.]/g,'')),nb=parseFloat(String(vb).replace(/[^\\d.]/g,''));
  if (!isNaN(na)&&!isNaN(nb)) return asc?na-nb:nb-na;
  return asc?String(va).localeCompare(String(vb),'es'):String(vb).localeCompare(String(va),'es');
}}

function applySortHeaders(prefix, col) {{
  const ths = document.querySelectorAll('#sec-'+prefix+' thead th');
  const key = prefix+col;
  const asc = sd[key] !== true; sd[key] = asc;
  ths.forEach((t,i)=>{{t.classList.remove('asc','desc');if(i===col)t.classList.add(asc?'asc':'desc');}});
}}

function renderPag(id, pg, maxPg, onNext, onPrev) {{
  const el = document.getElementById(id);
  if (maxPg<=1){{el.innerHTML='';return;}}
  el.innerHTML = '';
  const prev = document.createElement('button');
  prev.textContent='‹ Anterior'; prev.disabled=(pg<=1);
  prev.onclick = onPrev; el.appendChild(prev);
  const sp = document.createElement('span');
  sp.textContent = 'pág '+pg+' / '+maxPg; el.appendChild(sp);
  const next = document.createElement('button');
  next.textContent='Siguiente ›'; next.disabled=(pg>=maxPg);
  next.onclick = onNext; el.appendChild(next);
}}

// Init
filtrarProv(); filtrarLoc();
</script>
</body>
</html>"""

    with open(nombre, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n  OK: {nombre} generado ({round(len(html)/1024)} KB)")


if __name__ == '__main__':
    df_prov, df_loc_raw = obtener_datos()
    generar_html(df_prov, df_loc_raw)
