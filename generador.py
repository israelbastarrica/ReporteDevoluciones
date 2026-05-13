import pyodbc
import pandas as pd
import warnings
import traceback
import json

SERVER     = r'marketcentral.ddns.net\ZOOLOGIC,1433'
DB_CENTRAL = 'DRAGONFISH_CENTRAL'
USER       = 'MARKET'
PASSWORD   = 'Market202020'

warnings.filterwarnings('ignore', category=UserWarning)

#
# ARQUITECTURA CORRECTA (confirmada por diagnóstico):
#
#   DEVOLUCIONES: MTRANS.MOVGEN → MSTOCK.NUMERO → MSTOCK.CODIGO → DETMSTOCK.NUMR
#   ENVIOS:       COMPROBANTEV (FLETRA='R', FPTOVEN=1) → COMPROBANTEVDET
#
# Todo vive en DRAGONFISH_CENTRAL. La fecha de devolución es MSTOCK.FECHA.
# La fecha de envío es COMPROBANTEV.FFCH. El local sale de ORIGDEST / FCLIENTE.
#


def conectar():
    return pyodbc.connect(
        f'DRIVER={{SQL Server}};SERVER={SERVER};'
        f'DATABASE={DB_CENTRAL};UID={USER};PWD={PASSWORD}'
    )


def obtener_datos():
    """
    Retorna (df_art, df_chart, df_env).
    df_art   = devoluciones aprobadas, agrupado por (Codigo, ..., Local, Anio)
    df_chart = devoluciones por FechaStr/Local/Temporada para gráfico
    df_env   = envíos Central→locales, agrupado por (Codigo, ..., Local, Anio)
    """
    try:
        print("\n" + "="*55)
        print("  Cargando datos...")
        print("="*55)

        conn = conectar()

        print("  [1/2] Devoluciones aprobadas (MTRANS->MSTOCK->DETMSTOCK)...")
        df = pd.read_sql("""
            SELECT
                RTRIM(DET.MART)                                         AS Codigo,
                MAX(DET.DESCRIP)                                        AS Descripcion,
                CAST(MS.FECHA AS DATE)                                  AS Fecha,
                UPPER(RTRIM(LTRIM(MT.ORIGDEST)))                       AS Local,
                ISNULL(MAX(FAM.DESCRIP),  'SIN FAMILIA')               AS Familia,
                ISNULL(MAX(TIPO.DESCRIP), 'SIN TIPO')                  AS Tipo,
                ISNULL(MAX(CATE.DESCRIP), 'SIN CATEGORIA')             AS Categoria,
                ISNULL(NULLIF(RTRIM(LTRIM(MAX(ART.ATEMPORADA))), ''), 'S/T') AS Temporada,
                SUM(DET.CANTI)                                          AS Cantidad
            FROM DRAGONFISH_CENTRAL.Zoologic.MTRANS MT
            INNER JOIN DRAGONFISH_CENTRAL.Zoologic.MSTOCK MS
                ON MS.NUMERO = MT.MOVGEN
            INNER JOIN DRAGONFISH_CENTRAL.Zoologic.DETMSTOCK DET
                ON DET.NUMR = MS.CODIGO
            LEFT JOIN DRAGONFISH_CENTRAL.Zoologic.ART ART
                ON RTRIM(DET.MART) = ART.ARTCOD
            LEFT JOIN DRAGONFISH_CENTRAL.Zoologic.FAMILIA FAM
                ON FAM.COD = ART.FAMILIA
            LEFT JOIN DRAGONFISH_CENTRAL.Zoologic.TIPOART TIPO
                ON TIPO.COD = ART.TIPOARTI
            LEFT JOIN DRAGONFISH_CENTRAL.Zoologic.CATEGART CATE
                ON CATE.COD = ART.CATEARTI
            WHERE MT.ORIGLETRA = 'R'
              AND (MT.ANULADO IS NULL OR MT.ANULADO = 0)
              AND (MS.ANULADO IS NULL OR MS.ANULADO = 0)
              AND UPPER(RTRIM(LTRIM(MT.ORIGDEST))) IN ('LURO', 'PERALTA')
              AND DET.DESCRIP NOT LIKE '%BOLSA%'
              AND LEFT(RTRIM(DET.MART), 1) NOT IN ('Z', '9')
            GROUP BY RTRIM(DET.MART), CAST(MS.FECHA AS DATE), UPPER(RTRIM(LTRIM(MT.ORIGDEST)))
        """, conn)

        print("  [2/2] Envios Central -> locales (COMPROBANTEV/COMPROBANTEVDET)...")
        df_env_raw = pd.read_sql("""
            SELECT
                RTRIM(DET.FART)                                              AS Codigo,
                MAX(DET.FTXT)                                                AS Descripcion,
                CAST(COMP.FFCH AS DATE)                                      AS Fecha,
                UPPER(RTRIM(LTRIM(COMP.FCLIENTE)))                          AS Local,
                ISNULL(MAX(FAM.DESCRIP),  'SIN FAMILIA')                   AS Familia,
                ISNULL(MAX(TIPO.DESCRIP), 'SIN TIPO')                      AS Tipo,
                ISNULL(MAX(CATE.DESCRIP), 'SIN CATEGORIA')                 AS Categoria,
                ISNULL(NULLIF(RTRIM(LTRIM(MAX(ART.ATEMPORADA))), ''), 'S/T') AS Temporada,
                SUM(DET.FCANT)                                               AS Cantidad
            FROM DRAGONFISH_CENTRAL.Zoologic.COMPROBANTEV COMP
            INNER JOIN DRAGONFISH_CENTRAL.Zoologic.COMPROBANTEVDET DET
                ON COMP.CODIGO = DET.CODIGO
            LEFT JOIN DRAGONFISH_CENTRAL.Zoologic.ART ART
                ON RTRIM(DET.FART) = ART.ARTCOD
            LEFT JOIN DRAGONFISH_CENTRAL.Zoologic.FAMILIA FAM
                ON FAM.COD = ART.FAMILIA
            LEFT JOIN DRAGONFISH_CENTRAL.Zoologic.TIPOART TIPO
                ON TIPO.COD = ART.TIPOARTI
            LEFT JOIN DRAGONFISH_CENTRAL.Zoologic.CATEGART CATE
                ON CATE.COD = ART.CATEARTI
            WHERE COMP.FLETRA = 'R'
              AND COMP.FPTOVEN = 1
              AND UPPER(RTRIM(LTRIM(COMP.FCLIENTE))) IN ('LURO', 'PERALTA')
              AND COMP.ANULADO = 0
              AND DET.FTXT NOT LIKE '%BOLSA%'
              AND LEFT(RTRIM(DET.FART), 1) NOT IN ('Z', '9')
            GROUP BY RTRIM(DET.FART), CAST(COMP.FFCH AS DATE), UPPER(RTRIM(LTRIM(COMP.FCLIENTE)))
        """, conn)

        conn.close()

        if df.empty:
            print("  Sin datos de devoluciones.")
            return None

        # Procesar devoluciones
        df['Cantidad'] = pd.to_numeric(df['Cantidad'], errors='coerce').fillna(0).astype(int)
        df['Fecha']    = pd.to_datetime(df['Fecha'])
        df['Anio']     = df['Fecha'].dt.year.astype(int)
        df['FechaStr'] = df['Fecha'].dt.strftime('%Y-%m-%d')

        df_art = (
            df.groupby(['Codigo', 'Descripcion', 'Familia', 'Tipo', 'Categoria', 'Temporada', 'Local', 'Anio'])
            ['Cantidad'].sum().reset_index()
        )
        df_art['Cantidad'] = df_art['Cantidad'].astype(int)

        df_chart = (
            df.groupby(['FechaStr', 'Local', 'Temporada'])['Cantidad'].sum()
            .reset_index().rename(columns={'FechaStr': 'Fecha'})
            .sort_values('Fecha')
        )
        df_chart['Cantidad'] = df_chart['Cantidad'].astype(int)

        # Procesar envíos
        if not df_env_raw.empty:
            df_env_raw['Cantidad'] = pd.to_numeric(df_env_raw['Cantidad'], errors='coerce').fillna(0).astype(int)
            df_env_raw['Fecha']    = pd.to_datetime(df_env_raw['Fecha'])
            df_env_raw['Anio']     = df_env_raw['Fecha'].dt.year.astype(int)
            df_env = (
                df_env_raw.groupby(['Codigo', 'Descripcion', 'Familia', 'Tipo', 'Categoria', 'Temporada', 'Local', 'Anio'])
                ['Cantidad'].sum().reset_index()
            )
            df_env['Cantidad'] = df_env['Cantidad'].astype(int)
        else:
            df_env = pd.DataFrame(columns=['Codigo','Descripcion','Familia','Tipo','Categoria','Temporada','Local','Anio','Cantidad'])

        total     = int(df_art['Cantidad'].sum())
        modelos   = df_art['Codigo'].nunique()
        anios     = sorted(df_art['Anio'].unique().tolist())
        total_env = int(df_env['Cantidad'].sum()) if not df_env.empty else 0
        print(f"\n  DEVOLUCIONES: {total:,} prendas | {modelos} modelos | años: {anios}")
        print(f"  ENVIOS:       {total_env:,} prendas")

        return df_art, df_chart, df_env

    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        return None


def generar_html(resultado, nombre_archivo="AUDITORIA_MARKET.html"):
    if resultado is None:
        print("Sin datos para generar el reporte.")
        return

    df_art, df_chart, df_env = resultado

    data_art_json   = json.dumps(df_art.to_dict('records'),   ensure_ascii=False)
    data_chart_json = json.dumps(df_chart.to_dict('records'), ensure_ascii=False)
    data_env_json   = json.dumps(df_env.to_dict('records'),   ensure_ascii=False)

    anios_unicos = sorted(df_art['Anio'].unique().tolist())
    tipos_unicos = sorted(df_art['Tipo'].unique().tolist())
    temp_unicas  = sorted(df_art['Temporada'].unique().tolist())

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MARKET | Auditoria Logistica Inversa</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        :root {{ --borde: 4px solid #000; }}
        body            {{ background:#fff; color:#000; font-family:'Arial Black','Arial',sans-serif; }}
        .filtros-bar    {{ position:sticky; top:0; z-index:100; background:#000; color:#fff;
                           padding:14px 24px; display:flex; gap:32px; align-items:center;
                           flex-wrap:wrap; border-bottom:4px solid #000; }}
        .filtro-grupo   {{ display:flex; flex-direction:column; gap:6px; }}
        .filtro-label   {{ font-size:.65rem; letter-spacing:3px; text-transform:uppercase;
                           color:#aaa; font-weight:900; }}
        .anio-btn       {{ border:2px solid #fff; background:transparent; color:#fff;
                           padding:4px 12px; font-family:inherit; font-weight:900;
                           font-size:.85rem; cursor:pointer; transition:.15s; }}
        .anio-btn.activo {{ background:#fff; color:#000; }}
        .tog-btn        {{ border:2px solid #fff; background:transparent; color:#fff;
                           padding:5px 14px; font-family:inherit; font-weight:900;
                           font-size:.85rem; cursor:pointer; transition:.15s; }}
        .tog-btn.activo {{ background:#fff; color:#000; }}
        .tipo-select    {{ border:2px solid #fff; background:#000; color:#fff;
                           padding:5px 10px; font-family:inherit; font-weight:900;
                           font-size:.85rem; cursor:pointer; min-width:180px; }}
        .tipo-select option {{ background:#000; color:#fff; }}
        .contenido      {{ padding:30px 40px; }}
        .header-market  {{ border-bottom:12px solid #000; padding:20px 0; margin-bottom:40px; }}
        .logo           {{ font-size:3.5rem; font-weight:900; letter-spacing:12px; }}
        .kpi-box        {{ border:var(--borde); padding:25px; text-align:center; height:100%; }}
        .kpi-inv        {{ background:#000; color:#fff; }}
        .kpi-env        {{ background:#1a1a1a; color:#fff; }}
        .kpi-tasa       {{ border:var(--borde); background:#fff; color:#000; padding:25px;
                           text-align:center; height:100%; }}
        .kpi-tasa .display-1 {{ color:#000; }}
        .section-title  {{ background:#000; color:#fff; padding:10px 16px; font-weight:900;
                           text-transform:uppercase; letter-spacing:3px; margin:50px 0 20px; font-size:1rem; }}
        .chart-wrap     {{ border:var(--borde); padding:20px; }}
        .table-market   {{ border:3px solid #000; }}
        .table-market thead {{ background:#000; color:#fff; }}
        #filtroTabla    {{ border:2px solid #000; border-radius:0; padding:6px 14px;
                           width:320px; font-family:inherit; }}
        #filtroTabla:focus {{ outline:none; box-shadow:none; }}
        .badge-filtro   {{ background:#000; color:#fff; font-size:.7rem;
                           padding:3px 8px; font-family:inherit; font-weight:900; letter-spacing:1px; }}
        .pct-alta       {{ color:#c00; font-weight:900; }}
        .pct-media      {{ color:#e07000; font-weight:900; }}
        .pct-baja       {{ color:#060; font-weight:900; }}
    </style>
</head>
<body>

<div class="filtros-bar">
    <div class="filtro-grupo">
        <span class="filtro-label">Ano</span>
        <div id="aniosContainer" style="display:flex;gap:6px;flex-wrap:wrap;"></div>
    </div>
    <div class="filtro-grupo">
        <span class="filtro-label">Local</span>
        <div style="display:flex;gap:6px;">
            <button class="tog-btn activo" data-val="AMBOS"   onclick="setFiltro('local',this)">AMBOS</button>
            <button class="tog-btn"        data-val="LURO"    onclick="setFiltro('local',this)">LURO</button>
            <button class="tog-btn"        data-val="PERALTA" onclick="setFiltro('local',this)">PERALTA</button>
        </div>
    </div>
    <div class="filtro-grupo">
        <span class="filtro-label">Temporada</span>
        <div id="tempContainer" style="display:flex;gap:6px;flex-wrap:wrap;">
            <button class="tog-btn activo" data-val="TODAS" onclick="setFiltro('temporada',this)">TODAS</button>
        </div>
    </div>
    <div class="filtro-grupo">
        <span class="filtro-label">Tipo de Articulo</span>
        <select id="filtroTipo" class="tipo-select" onchange="actualizar()">
            <option value="TODOS">TODOS LOS TIPOS</option>
        </select>
    </div>
</div>

<div class="contenido">
    <div class="header-market text-center">
        <h1 class="logo">MARKET</h1>
        <div class="fw-bold fs-4 mt-2">AUDITORIA LOGISTICA INVERSA</div>
        <div id="resumenFiltros" class="mt-2" style="font-size:.9rem;font-weight:400;"></div>
    </div>

    <div class="row g-4 mb-5">
        <div class="col-md-3">
            <div class="kpi-box">
                <div class="display-1 fw-bold" id="kpiEnviadas">-</div>
                <div class="fs-5 mt-2">PRENDAS ENVIADAS</div>
                <div class="text-muted small mt-1">Central → Locales</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="kpi-box kpi-inv">
                <div class="display-1 fw-bold" id="kpiPrendas">-</div>
                <div class="fs-5 mt-2">PRENDAS DEVUELTAS</div>
                <div class="small mt-1" style="color:#aaa;">Aprobadas en Central</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="kpi-box">
                <div class="display-1 fw-bold" id="kpiModelos">-</div>
                <div class="fs-5 mt-2">MODELOS UNICOS</div>
                <div class="text-muted small mt-1">Devueltos</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="kpi-box kpi-inv">
                <div class="display-1 fw-bold" id="kpiTasa">-</div>
                <div class="fs-5 mt-2">TASA DEVOLUCION</div>
                <div class="small mt-1" style="color:#aaa;">Devuelto / Enviado</div>
            </div>
        </div>
    </div>

    <h2 class="section-title">Resumen por Jerarquia</h2>
    <div class="row g-4 mb-5">
        <div class="col-md-4"><div class="kpi-box">
            <h5 class="fw-bold mb-3">POR FAMILIA</h5>
            <table class="table table-sm mb-0" id="tabFamilia"></table>
        </div></div>
        <div class="col-md-4"><div class="kpi-box">
            <h5 class="fw-bold mb-3">POR TIPO</h5>
            <table class="table table-sm mb-0" id="tabTipo"></table>
        </div></div>
        <div class="col-md-4"><div class="kpi-box">
            <h5 class="fw-bold mb-3">POR CATEGORIA</h5>
            <table class="table table-sm mb-0" id="tabCategoria"></table>
        </div></div>
    </div>

    <h2 class="section-title" style="display:flex;align-items:center;justify-content:space-between;">
        <span>Evolucion de Devoluciones</span>
        <div style="display:flex;gap:6px;">
            <button class="tog-btn activo" style="font-size:.75rem;padding:3px 10px;" data-val="DIA"    onclick="setAgrupacion(this)">DIA</button>
            <button class="tog-btn"        style="font-size:.75rem;padding:3px 10px;" data-val="SEMANA" onclick="setAgrupacion(this)">SEMANA</button>
            <button class="tog-btn"        style="font-size:.75rem;padding:3px 10px;" data-val="MES"    onclick="setAgrupacion(this)">MES</button>
        </div>
    </h2>
    <div class="chart-wrap mb-5">
        <canvas id="chartEvolucion" height="75"></canvas>
    </div>

    <h2 class="section-title">Top 20 Articulos mas Devueltos</h2>
    <div class="table-responsive mb-5">
        <table class="table table-hover table-market align-middle">
            <thead><tr>
                <th style="width:40px;">#</th>
                <th>CODIGO</th>
                <th>DESCRIPCION</th>
                <th>FAMILIA</th>
                <th>TIPO</th>
                <th class="text-end">ENVIADO</th>
                <th class="text-end">DEVUELTO</th>
                <th class="text-end">% DEV</th>
            </tr></thead>
            <tbody id="tbodyEnviados"></tbody>
        </table>
    </div>

    <h2 class="section-title">Detalle por Articulo (Devoluciones)</h2>
    <div class="d-flex align-items-center gap-3 mb-3">
        <input type="text" id="filtroTabla" placeholder="Buscar codigo o descripcion..."
               oninput="filtrarTabla()" style="border:2px solid #000;border-radius:0;padding:6px 14px;font-family:inherit;">
        <button id="btnMayor" onclick="setExceso('mayor')"
                style="border:2px solid #000;background:transparent;padding:6px 18px;font-family:inherit;font-weight:900;font-size:.85rem;cursor:pointer;white-space:nowrap;">
            DEVUELTO &gt; ENVIADO
        </button>
        <button id="btnMenor" onclick="setExceso('menor')"
                style="border:2px solid #000;background:transparent;padding:6px 18px;font-family:inherit;font-weight:900;font-size:.85rem;cursor:pointer;white-space:nowrap;">
            DEVUELTO &lt; ENVIADO
        </button>
    </div>
    <div class="table-responsive">
        <table class="table table-hover table-market align-middle">
            <thead><tr>
                <th>CODIGO</th><th>DESCRIPCION</th><th>FAMILIA</th>
                <th>TIPO</th><th>TEMPORADA</th>
                <th class="text-end">ENVIADO</th>
                <th class="text-end">DEVUELTO</th>
                <th class="text-end">% DEV</th>
            </tr></thead>
            <tbody id="tbodyDetalle"></tbody>
        </table>
    </div>
</div>

<script>
const DATA_ART   = {data_art_json};
const DATA_CHART = {data_chart_json};
const DATA_ENV   = {data_env_json};
const ANIOS_DISP = {json.dumps(anios_unicos)};
const TIPOS_DISP = {json.dumps(tipos_unicos)};
const TEMP_DISP  = {json.dumps(temp_unicas)};

let estado = {{
    anios:      new Set(ANIOS_DISP),
    local:      'AMBOS',
    temporada:  'TODAS',
    agrupacion: 'DIA',
    busqueda:   '',
    exceso: ''
}};
let chartInstance = null;
let datosTabla    = [];

(function init() {{
    const ac = document.getElementById('aniosContainer');
    ANIOS_DISP.forEach(a => {{
        const b = document.createElement('button');
        b.className = 'anio-btn activo'; b.textContent = a; b.dataset.anio = a;
        b.onclick = () => toggleAnio(b, a);
        ac.appendChild(b);
    }});
    const tc = document.getElementById('tempContainer');
    TEMP_DISP.forEach(t => {{
        const b = document.createElement('button');
        b.className = 'tog-btn'; b.textContent = t; b.dataset.val = t;
        b.onclick = () => setFiltro('temporada', b);
        tc.appendChild(b);
    }});
    const sel = document.getElementById('filtroTipo');
    TIPOS_DISP.forEach(t => {{
        const o = document.createElement('option');
        o.value = t; o.textContent = t; sel.appendChild(o);
    }});
    chartInstance = new Chart(document.getElementById('chartEvolucion'), {{
        type: 'line',
        data: {{ labels: [], datasets: [{{
            label: 'Prendas devueltas', data: [],
            borderColor: '#000', backgroundColor: 'rgba(0,0,0,0.07)',
            borderWidth: 3, pointRadius: 3, pointBackgroundColor: '#000',
            fill: true, tension: 0.3
        }}] }},
        options: {{
            responsive: true,
            plugins: {{
                legend: {{ display: false }},
                tooltip: {{ callbacks: {{ label: ctx => ' ' + ctx.parsed.y.toLocaleString('es-AR') + ' prendas' }} }}
            }},
            scales: {{
                x: {{ grid: {{ color:'#eee' }}, ticks: {{ maxTicksLimit: 20, maxRotation: 45 }} }},
                y: {{ grid: {{ color:'#eee' }}, ticks: {{ callback: v => v.toLocaleString('es-AR') }} }}
            }}
        }}
    }});
    actualizar();
}})();

function toggleAnio(btn, anio) {{
    if (estado.anios.has(anio)) {{
        if (estado.anios.size === 1) return;
        estado.anios.delete(anio); btn.classList.remove('activo');
    }} else {{
        estado.anios.add(anio); btn.classList.add('activo');
    }}
    actualizar();
}}

function setAgrupacion(btn) {{
    document.querySelectorAll('[onclick*="setAgrupacion"]').forEach(b => b.classList.remove('activo'));
    btn.classList.add('activo');
    estado.agrupacion = btn.dataset.val;
    actualizar();
}}

function claveChart(fechaStr) {{
    if (estado.agrupacion === 'MES') return fechaStr.substring(0, 7);
    if (estado.agrupacion === 'SEMANA') {{
        const d = new Date(fechaStr + 'T00:00:00');
        const dia = d.getDay();
        const diff = (dia === 0) ? -6 : 1 - dia;
        d.setDate(d.getDate() + diff);
        return d.toISOString().slice(0, 10);
    }}
    return fechaStr;
}}

function labelChart(clave) {{
    if (estado.agrupacion === 'MES') {{
        const [y, m] = clave.split('-');
        const meses = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
        return meses[parseInt(m) - 1] + ' ' + y;
    }}
    if (estado.agrupacion === 'SEMANA') {{
        const d = new Date(clave + 'T00:00:00');
        return 'Sem ' + String(d.getDate()).padStart(2,'0') + '/' + String(d.getMonth()+1).padStart(2,'0');
    }}
    return clave;
}}

function setFiltro(campo, btn) {{
    const grupo = campo === 'local'
        ? document.querySelectorAll('[onclick*="local"]')
        : document.querySelectorAll('#tempContainer .tog-btn');
    grupo.forEach(b => b.classList.remove('activo'));
    btn.classList.add('activo');
    estado[campo] = btn.dataset.val;
    actualizar();
}}

function filtrarTabla() {{
    estado.busqueda = document.getElementById('filtroTabla').value.toLowerCase();
    renderTabla(datosTabla);
}}

function setExceso(modo) {{
    estado.exceso = (estado.exceso === modo) ? '' : modo;
    ['mayor','menor'].forEach(m => {{
        const btn = document.getElementById('btn' + m.charAt(0).toUpperCase() + m.slice(1));
        btn.style.background = (estado.exceso === m) ? '#000' : 'transparent';
        btn.style.color      = (estado.exceso === m) ? '#fff' : '#000';
    }});
    renderTabla(datosTabla);
}}

function filtrarArt() {{
    const tipo = document.getElementById('filtroTipo').value;
    return DATA_ART.filter(d =>
        estado.anios.has(d.Anio) &&
        (estado.local     === 'AMBOS' || d.Local     === estado.local) &&
        (estado.temporada === 'TODAS' || d.Temporada === estado.temporada) &&
        (tipo             === 'TODOS' || d.Tipo       === tipo)
    );
}}

function filtrarEnv() {{
    const tipo = document.getElementById('filtroTipo').value;
    return DATA_ENV.filter(d =>
        estado.anios.has(d.Anio) &&
        (estado.local     === 'AMBOS' || d.Local     === estado.local) &&
        (estado.temporada === 'TODAS' || d.Temporada === estado.temporada) &&
        (tipo             === 'TODOS' || d.Tipo       === tipo)
    );
}}

function filtrarChart() {{
    return DATA_CHART.filter(d =>
        estado.anios.has(parseInt(d.Fecha.substring(0, 4))) &&
        (estado.local     === 'AMBOS' || d.Local     === estado.local) &&
        (estado.temporada === 'TODAS' || d.Temporada === estado.temporada)
    );
}}

function agrupar(arr, keys) {{
    const map = {{}};
    arr.forEach(d => {{
        const k = keys.map(c => d[c]).join('§');
        if (!map[k]) {{
            const obj = {{}}; keys.forEach(c => obj[c] = d[c]); obj.Cantidad = 0; map[k] = obj;
        }}
        map[k].Cantidad += d.Cantidad;
    }});
    return Object.values(map).sort((a, b) => b.Cantidad - a.Cantidad);
}}

function fmtPct(dev, env) {{
    if (!env || env === 0) return '<span class="text-muted">—</span>';
    const p = (dev / env * 100);
    const cls = p >= 20 ? 'pct-alta' : p >= 10 ? 'pct-media' : 'pct-baja';
    return `<span class="${{cls}}">${{p.toFixed(1)}}%</span>`;
}}

function actualizar() {{
    const art = filtrarArt();
    const env = filtrarEnv();

    const totalDev = art.reduce((s, d) => s + d.Cantidad, 0);
    const totalEnv = env.reduce((s, d) => s + d.Cantidad, 0);
    const modelos  = new Set(art.map(d => d.Codigo)).size;
    const tasa     = totalEnv > 0 ? (totalDev / totalEnv * 100).toFixed(1) + '%' : '—';

    document.getElementById('kpiPrendas').textContent  = totalDev.toLocaleString('es-AR');
    document.getElementById('kpiEnviadas').textContent = totalEnv.toLocaleString('es-AR');
    document.getElementById('kpiModelos').textContent  = modelos.toLocaleString('es-AR');
    document.getElementById('kpiTasa').textContent     = tasa;

    const tipo     = document.getElementById('filtroTipo').value;
    const aniosStr = [...estado.anios].sort().join(', ');
    document.getElementById('resumenFiltros').innerHTML =
        `<span class="badge-filtro">Ano: ${{aniosStr}}</span> &nbsp;` +
        `<span class="badge-filtro">Local: ${{estado.local}}</span> &nbsp;` +
        `<span class="badge-filtro">Temporada: ${{estado.temporada}}</span> &nbsp;` +
        `<span class="badge-filtro">Tipo: ${{tipo}}</span>`;

    renderTop('tabFamilia',   agrupar(art, ['Familia']).slice(0,5),   'Familia');
    renderTop('tabTipo',      agrupar(art, ['Tipo']).slice(0,5),      'Tipo');
    renderTop('tabCategoria', agrupar(art, ['Categoria']).slice(0,5), 'Categoria');

    const chartAgrupado = {{}};
    filtrarChart().forEach(d => {{
        const k = claveChart(d.Fecha);
        chartAgrupado[k] = (chartAgrupado[k] || 0) + d.Cantidad;
    }});
    const claves = Object.keys(chartAgrupado).sort();
    chartInstance.data.labels           = claves.map(labelChart);
    chartInstance.data.datasets[0].data = claves.map(k => chartAgrupado[k]);
    chartInstance.data.datasets[0].pointRadius = estado.agrupacion === 'DIA' ? 3 : 5;
    chartInstance.update();

    // Mapa de enviado por código
    const envMap = {{}};
    agrupar(env, ['Codigo']).forEach(d => {{ envMap[d.Codigo] = d.Cantidad; }});

    // Top 20 más devueltos (sorted by Cantidad devuelto desc)
    renderTopEnviados(agrupar(art, ['Codigo','Descripcion','Familia','Tipo']).slice(0, 20), envMap);

    // Tabla de detalle ordenada por % DEV desc
    datosTabla = agrupar(art, ['Codigo','Descripcion','Familia','Tipo','Categoria','Temporada']).map(d => ({{
        ...d,
        Enviado: envMap[d.Codigo] || 0
    }})).sort((a, b) => {{
        const pA = a.Enviado > 0 ? a.Cantidad / a.Enviado : 0;
        const pB = b.Enviado > 0 ? b.Cantidad / b.Enviado : 0;
        return pB - pA;
    }});
    renderTabla(datosTabla);
}}

function renderTop(id, data, col) {{
    document.getElementById(id).innerHTML = data.map(d =>
        `<tr><td>${{d[col]}}</td><td class="text-end fw-bold">${{d.Cantidad.toLocaleString('es-AR')}}</td></tr>`
    ).join('');
}}

function renderTopEnviados(data, envMap) {{
    document.getElementById('tbodyEnviados').innerHTML = data.map((d, i) => {{
        const env = envMap[d.Codigo] || 0;
        return `<tr>
            <td class="text-muted small">${{i + 1}}</td>
            <td class="fw-bold font-monospace small">${{d.Codigo}}</td>
            <td>${{d.Descripcion}}</td>
            <td class="text-muted small">${{d.Familia}}</td>
            <td class="text-muted small">${{d.Tipo}}</td>
            <td class="text-end">${{env > 0 ? env.toLocaleString('es-AR') : '—'}}</td>
            <td class="text-end fw-bold">${{d.Cantidad.toLocaleString('es-AR')}}</td>
            <td class="text-end">${{fmtPct(d.Cantidad, env)}}</td>
        </tr>`;
    }}).join('');
}}

function renderTabla(data) {{
    const q = estado.busqueda;
    let filtrado = estado.exceso === 'mayor' ? data.filter(d => d.Enviado > 0 && d.Cantidad > d.Enviado)
                 : estado.exceso === 'menor' ? data.filter(d => d.Enviado > 0 && d.Cantidad < d.Enviado)
                 : data;
    if (q) filtrado = filtrado.filter(d =>
        d.Codigo.toLowerCase().includes(q) || d.Descripcion.toLowerCase().includes(q) ||
        d.Familia.toLowerCase().includes(q) || d.Tipo.toLowerCase().includes(q));
    document.getElementById('tbodyDetalle').innerHTML = filtrado.map(d =>
        `<tr>
            <td class="fw-bold font-monospace small">${{d.Codigo}}</td>
            <td>${{d.Descripcion}}</td>
            <td class="text-muted small">${{d.Familia}}</td>
            <td class="text-muted small">${{d.Tipo}}</td>
            <td class="text-muted small">${{d.Temporada}}</td>
            <td class="text-end text-muted">${{d.Enviado > 0 ? d.Enviado.toLocaleString('es-AR') : '—'}}</td>
            <td class="text-end fw-bold">${{d.Cantidad.toLocaleString('es-AR')}}</td>
            <td class="text-end">${{fmtPct(d.Cantidad, d.Enviado)}}</td>
        </tr>`
    ).join('');
}}
</script>
</body>
</html>"""

    with open(nombre_archivo, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n  Reporte guardado: {nombre_archivo}  ({round(len(html)/1024):.0f} KB)")


if __name__ == '__main__':
    generar_html(obtener_datos(), "AUDITORIA_MARKET.html")
