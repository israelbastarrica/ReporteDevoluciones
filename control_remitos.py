"""
control_remitos.py — Control de Remitos del Día
Estado de remitos (PENDIENTE/ACEPTADO/RECHAZADO) por local destino.
"""
import pyodbc
import pandas as pd
import json
import warnings
from datetime import datetime, date
from config import SERVER, USER, PASSWORD

warnings.filterwarnings('ignore', category=UserWarning)

DB_MARKET   = 'MARKET'
DB_CENTRAL  = 'DRAGONFISH_CENTRAL'
DB_LURO     = 'DRAGONFISH_LURO'
DB_PERALTA  = 'DRAGONFISH_PERALTA'
OUTPUT      = r'C:\REPORTESDEVOLUCIONES\control_remitos.html'


def conectar():
    return pyodbc.connect(
        f'DRIVER={{SQL Server}};SERVER={SERVER};'
        f'DATABASE={DB_MARKET};UID={USER};PWD={PASSWORD}'
    )


def obtener_datos(fecha_desde: str) -> pd.DataFrame:
    print(f"\n{'='*55}")
    print(f"  Control de Remitos — desde {fecha_desde}")
    print(f"{'='*55}")
    conn = conectar()
    print("  Consultando remitos...")

    query = f"""
    WITH RemitosDragon AS (
        SELECT 2 AS IDLocal, 1 AS IDLocalOrigen, RTRIM(COMP.CODIGO) AS RemitoID,
               CASE WHEN TRY_CAST(LEFT(COMP.HALTAFW,2) AS INT) >= 21
                    THEN DATEADD(DAY,1,CONVERT(DATE,COMP.FFCH,112))
                    ELSE CONVERT(DATE,COMP.FFCH,112) END AS LogicalDate,
               COMP.FNUMCOMP AS NroRemito
        FROM {DB_CENTRAL}.ZooLogic.COMPROBANTEV COMP
        WHERE COMP.ANULADO = 0 AND COMP.FLETRA = 'R' AND COMP.FCLIENTE = 'LURO'
        UNION ALL
        SELECT 2, 3, RTRIM(COMP.CODIGO),
               CASE WHEN TRY_CAST(LEFT(COMP.HALTAFW,2) AS INT) >= 21
                    THEN DATEADD(DAY,1,CONVERT(DATE,COMP.FFCH,112))
                    ELSE CONVERT(DATE,COMP.FFCH,112) END,
               COMP.FNUMCOMP
        FROM [marketperalta.ddns.net].{DB_PERALTA}.ZooLogic.COMPROBANTEV COMP
        WHERE COMP.ANULADO = 0 AND COMP.FLETRA = 'R' AND COMP.FCLIENTE = 'LURO'
        UNION ALL
        SELECT 3, 1, RTRIM(COMP.CODIGO),
               CASE WHEN TRY_CAST(LEFT(COMP.HALTAFW,2) AS INT) >= 21
                    THEN DATEADD(DAY,1,CONVERT(DATE,COMP.FFCH,112))
                    ELSE CONVERT(DATE,COMP.FFCH,112) END,
               COMP.FNUMCOMP
        FROM {DB_CENTRAL}.ZooLogic.COMPROBANTEV COMP
        WHERE COMP.ANULADO = 0 AND COMP.FLETRA = 'R' AND COMP.FCLIENTE = 'PERALTA'
        UNION ALL
        SELECT 3, 2, RTRIM(COMP.CODIGO),
               CASE WHEN TRY_CAST(LEFT(COMP.HALTAFW,2) AS INT) >= 21
                    THEN DATEADD(DAY,1,CONVERT(DATE,COMP.FFCH,112))
                    ELSE CONVERT(DATE,COMP.FFCH,112) END,
               COMP.FNUMCOMP
        FROM [marketluro.ddns.net].{DB_LURO}.ZooLogic.COMPROBANTEV COMP
        WHERE COMP.ANULADO = 0 AND COMP.FLETRA = 'R' AND COMP.FCLIENTE = 'PERALTA'
    )
    SELECT
        CASE R.IDLocal WHEN 2 THEN 'LURO' WHEN 3 THEN 'PERALTA' END  AS LocalDestino,
        CASE R.IDLocalOrigen WHEN 1 THEN 'CENTRAL' WHEN 2 THEN 'LURO'
             WHEN 3 THEN 'PERALTA' END                                AS LocalOrigen,
        R.NroRemito,
        R.RemitoID,
        CONVERT(VARCHAR(10), R.LogicalDate, 120)                      AS Fecha,
        ISNULL(CR.Estado, 'PENDIENTE')                                AS Estado,
        ISNULL(CAST(CR.Comentario AS VARCHAR(500)), '')                AS Comentario,
        ISNULL(CAST(CR.UsuarioApp AS VARCHAR(100)), '')                AS Usuario,
        CASE WHEN CR.FechaAccion IS NULL THEN ''
             ELSE CONVERT(VARCHAR(16), CR.FechaAccion, 120) END       AS FechaAccion
    FROM RemitosDragon R
    LEFT JOIN ControlRemitos CR
           ON CR.RemitoID = R.RemitoID
          AND CR.IDLocal = R.IDLocal
          AND CR.IDLocalOrigen = R.IDLocalOrigen
    WHERE R.LogicalDate >= '{fecha_desde}'
    ORDER BY LocalDestino, LocalOrigen, R.LogicalDate DESC
    """

    df = pd.read_sql(query, conn)
    conn.close()
    print(f"    > {len(df):,} remitos")
    return df


def generar_html(df: pd.DataFrame, fecha_desde_display: str):
    ahora = datetime.now().strftime('%d/%m/%Y %H:%M')

    total      = len(df)
    aceptados  = int((df['Estado'] == 'ACEPTADO').sum())
    rechazados = int((df['Estado'] == 'RECHAZADO').sum())
    pendientes = int((df['Estado'] == 'PENDIENTE').sum())

    stats = {}
    for local in ['LURO', 'PERALTA']:
        sub = df[df['LocalDestino'] == local]
        stats[local] = {
            'enviados':  len(sub),
            'aceptados': int((sub['Estado'] == 'ACEPTADO').sum()),
            'rechazados':int((sub['Estado'] == 'RECHAZADO').sum()),
            'pendientes':int((sub['Estado'] == 'PENDIENTE').sum()),
        }

    records = df.to_dict('records')
    data_json = json.dumps(records, ensure_ascii=False)

    def stat_card(local):
        s = stats[local]
        return f"""
  <div class="local-card">
    <div class="local-card-title">{local}</div>
    <div class="stat-row">
      <div class="stat"><div class="stat-val neu">{s['enviados']}</div><div class="stat-label">ENVIADOS</div></div>
      <div class="stat"><div class="stat-val ok">{s['aceptados']}</div><div class="stat-label">ACEPTADOS</div></div>
      <div class="stat"><div class="stat-val err">{s['rechazados']}</div><div class="stat-label">RECHAZADOS</div></div>
      <div class="stat"><div class="stat-val warn">{s['pendientes']}</div><div class="stat-label">PENDIENTES</div></div>
    </div>
  </div>"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Control de Remitos — MARKET</title>
<style>
:root{{--bg:#0d0d0d;--card:#141414;--border:#2a2a2a;--accent:#e8b963;--text:#f0f0f0;--muted:#888;--ok:#22c55e;--warn:#f97316;--err:#ef4444;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:Arial,sans-serif;font-size:13px;}}
a{{color:var(--accent);text-decoration:none;}}
.page-header{{background:#111;border-bottom:2px solid var(--accent);padding:16px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}}
.page-title{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:20px;color:var(--accent);letter-spacing:1px;}}
.nav-links{{display:flex;gap:8px;margin-left:auto;flex-wrap:wrap;}}
.nav-links a{{background:#1e1e1e;border:1px solid var(--border);color:var(--muted);padding:5px 12px;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:.5px;}}
.nav-links a:hover{{border-color:var(--accent);color:var(--accent);}}
.kpi-row{{display:flex;gap:12px;padding:16px 24px;flex-wrap:wrap;}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:12px 18px;min-width:140px;flex:1;}}
.kpi-val{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:26px;color:var(--accent);}}
.kpi-val.ok{{color:var(--ok);}}
.kpi-val.warn{{color:var(--warn);}}
.kpi-val.err{{color:var(--err);}}
.kpi-label{{color:var(--muted);font-size:11px;margin-top:2px;}}
.local-cards{{display:flex;gap:16px;padding:0 24px 16px;flex-wrap:wrap;}}
.local-card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px 20px;flex:1;min-width:260px;}}
.local-card-title{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:16px;color:var(--accent);margin-bottom:12px;letter-spacing:.5px;}}
.stat-row{{display:flex;gap:10px;}}
.stat{{flex:1;text-align:center;padding:10px 6px;background:#111;border-radius:6px;border:1px solid var(--border);}}
.stat-val{{font-family:"Arial Black",Arial,sans-serif;font-size:22px;font-weight:900;}}
.stat-val.ok{{color:var(--ok);}}
.stat-val.warn{{color:var(--warn);}}
.stat-val.err{{color:var(--err);}}
.stat-val.neu{{color:var(--accent);}}
.stat-label{{color:var(--muted);font-size:10px;margin-top:3px;}}
.toolbar{{display:flex;gap:10px;padding:12px 24px;flex-wrap:wrap;align-items:center;border-top:1px solid var(--border);border-bottom:1px solid var(--border);}}
.toolbar select,.toolbar input{{background:#1a1a1a;border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:4px;font-size:12px;}}
.toolbar select:focus,.toolbar input:focus{{outline:none;border-color:var(--accent);}}
.toolbar input{{width:220px;}}
.cnt{{color:var(--muted);font-size:11px;padding:8px 24px 0;}}
.tbl-wrap{{overflow-x:auto;max-height:calc(100vh - 440px);margin:8px 24px 0;border:1px solid var(--border);border-radius:6px;}}
table{{width:100%;border-collapse:collapse;}}
thead th{{background:#1a1a1a;color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.5px;padding:8px 10px;text-align:left;position:sticky;top:0;z-index:2;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;}}
thead th:hover{{color:var(--accent);}}
thead th.asc::after{{content:" ▲";color:var(--accent);font-size:9px;}}
thead th.desc::after{{content:" ▼";color:var(--accent);font-size:9px;}}
tbody tr{{border-bottom:1px solid #1e1e1e;transition:background .1s;}}
tbody tr:hover{{background:#181818;}}
tbody td{{padding:7px 10px;white-space:nowrap;}}
.badge{{padding:2px 8px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:.5px;}}
.badge-ok{{background:#0f2e1a;color:#22c55e;}}
.badge-err{{background:#2e0f0f;color:#ef4444;}}
.badge-pend{{background:#2e1e0f;color:#f97316;}}
.badge-luro{{background:#1e3a5f;color:#7eb8f7;}}
.badge-per{{background:#3a1e5f;color:#c07ef7;}}
.badge-cen{{background:#1e2e1e;color:#7ef7a0;}}
.remito-id{{font-family:monospace;font-size:11px;color:#555;max-width:200px;overflow:hidden;text-overflow:ellipsis;}}
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
    <div class="page-title">CONTROL DE REMITOS</div>
    <div style="color:var(--muted);font-size:11px;margin-top:3px;">Desde {fecha_desde_display} · Actualizado {ahora}</div>
  </div>
  <div class="nav-links">
    <a href="home.html">INICIO</a>
    <a href="index.html">DEVOLUCIONES</a>
    <a href="envios.html">ENVÍOS</a>
    <a href="pendientes.html">PENDIENTES</a>
    <a href="dashboard.html">DASHBOARD</a>
    <a href="logistica.html">LOGÍSTICA</a>
    <a href="insumos.html">INSUMOS</a>
  </div>
</div>

<div class="kpi-row">
  <div class="kpi"><div class="kpi-val">{total:,}</div><div class="kpi-label">REMITOS ENVIADOS</div></div>
  <div class="kpi"><div class="kpi-val ok">{aceptados:,}</div><div class="kpi-label">ACEPTADOS</div></div>
  <div class="kpi"><div class="kpi-val err">{rechazados:,}</div><div class="kpi-label">RECHAZADOS</div></div>
  <div class="kpi"><div class="kpi-val warn">{pendientes:,}</div><div class="kpi-label">PENDIENTES</div></div>
</div>

<div class="local-cards">{stat_card('LURO')}{stat_card('PERALTA')}
</div>

<div class="toolbar">
  <select id="selLocal" onchange="filtrar()">
    <option value="">Todos los locales</option>
    <option value="LURO">LURO</option>
    <option value="PERALTA">PERALTA</option>
  </select>
  <select id="selOrigen" onchange="filtrar()">
    <option value="">Todos los orígenes</option>
    <option value="CENTRAL">CENTRAL</option>
    <option value="LURO">LURO</option>
    <option value="PERALTA">PERALTA</option>
  </select>
  <select id="selEstado" onchange="filtrar()">
    <option value="">Todos los estados</option>
    <option value="PENDIENTE">PENDIENTE</option>
    <option value="ACEPTADO">ACEPTADO</option>
    <option value="RECHAZADO">RECHAZADO</option>
  </select>
  <input type="text" id="buscar" placeholder="Buscar nº o remito ID…" oninput="filtrar()">
</div>
<div class="cnt" id="cnt"></div>
<div class="tbl-wrap"><table><thead>
  <tr>
    <th onclick="sortBy(0)">DESTINO</th>
    <th onclick="sortBy(1)">ORIGEN</th>
    <th onclick="sortBy(2)">FECHA</th>
    <th onclick="sortBy(3)">Nº REMITO</th>
    <th onclick="sortBy(4)">ESTADO</th>
    <th>REMITO ID</th>
    <th onclick="sortBy(6)">COMENTARIO</th>
    <th onclick="sortBy(7)">USUARIO</th>
    <th onclick="sortBy(8)">FECHA ACCIÓN</th>
  </tr>
</thead>
<tbody id="tbody"></tbody>
</table></div>
<div class="pag">
  <button id="btnPrev" onclick="irPag(pg-1)">&#8592; Anterior</button>
  <span id="pgInfo"></span>
  <button id="btnNext" onclick="irPag(pg+1)">Siguiente &#8594;</button>
</div>

<script>
const DATA = {data_json};
const PG_SIZE = 100;
let filt = [...DATA];
let pg = 1, sortCol = -1, sortDir = 1;
const FIELDS = ['LocalDestino','LocalOrigen','Fecha','NroRemito','Estado','RemitoID','Comentario','Usuario','FechaAccion'];

function localBadge(l){{
  if(l==='LURO')    return '<span class="badge badge-luro">LURO</span>';
  if(l==='PERALTA') return '<span class="badge badge-per">PERALTA</span>';
  return '<span class="badge badge-cen">CENTRAL</span>';
}}
function estadoBadge(e){{
  if(e==='ACEPTADO')  return '<span class="badge badge-ok">ACEPTADO</span>';
  if(e==='RECHAZADO') return '<span class="badge badge-err">RECHAZADO</span>';
  return '<span class="badge badge-pend">PENDIENTE</span>';
}}

function filtrar(){{
  const local  = document.getElementById('selLocal').value;
  const origen = document.getElementById('selOrigen').value;
  const estado = document.getElementById('selEstado').value;
  const busq   = document.getElementById('buscar').value.toLowerCase();
  filt = DATA.filter(r =>
    (!local  || r.LocalDestino === local) &&
    (!origen || r.LocalOrigen  === origen) &&
    (!estado || r.Estado       === estado) &&
    (!busq   || r.RemitoID.toLowerCase().includes(busq) || String(r.NroRemito).includes(busq))
  );
  pg = 1;
  render();
}}

function sortBy(col){{
  const ths = document.querySelectorAll('thead th');
  if(sortCol === col) sortDir = -sortDir; else {{ sortCol = col; sortDir = 1; }}
  ths.forEach((th,i) => {{ th.classList.remove('asc','desc'); if(i===col) th.classList.add(sortDir===1?'asc':'desc'); }});
  const f = FIELDS[col];
  filt.sort((a,b) => {{
    const av = a[f], bv = b[f];
    return typeof av === 'number' ? sortDir*(av-bv) : sortDir*String(av).localeCompare(String(bv),'es');
  }});
  render();
}}

function render(){{
  const start = (pg-1)*PG_SIZE, end = start+PG_SIZE;
  const page  = filt.slice(start, end);
  document.getElementById('cnt').textContent = filt.length.toLocaleString() + ' remitos';
  document.getElementById('tbody').innerHTML = page.map(r => `
    <tr>
      <td>${{localBadge(r.LocalDestino)}}</td>
      <td>${{localBadge(r.LocalOrigen)}}</td>
      <td>${{r.Fecha}}</td>
      <td style="font-weight:700;color:var(--accent)">${{r.NroRemito}}</td>
      <td>${{estadoBadge(r.Estado)}}</td>
      <td class="remito-id" title="${{r.RemitoID}}">${{r.RemitoID}}</td>
      <td style="color:var(--text)">${{r.Comentario}}</td>
      <td style="color:var(--muted)">${{r.Usuario}}</td>
      <td style="color:var(--muted)">${{r.FechaAccion}}</td>
    </tr>`).join('');
  const totalPgs = Math.max(1, Math.ceil(filt.length/PG_SIZE));
  document.getElementById('pgInfo').textContent = 'Pág ' + pg + ' / ' + totalPgs;
  document.getElementById('btnPrev').disabled = pg <= 1;
  document.getElementById('btnNext').disabled = pg >= totalPgs;
}}

function irPag(n){{
  const totalPgs = Math.max(1, Math.ceil(filt.length/PG_SIZE));
  pg = Math.max(1, Math.min(n, totalPgs));
  render();
}}

filtrar();
</script>
</body>
</html>"""

    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n  -> Generado: {OUTPUT}")


if __name__ == '__main__':
    hoy = date.today()
    df  = obtener_datos(hoy.strftime('%Y-%m-%d'))
    generar_html(df, hoy.strftime('%d/%m/%Y'))
