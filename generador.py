import pyodbc
import pandas as pd
import warnings
import traceback
import json
from config import SERVER, DB_CENTRAL, DB_LURO, DB_PERALTA, USER, PASSWORD
from datetime import date, timedelta

warnings.filterwarnings('ignore', category=UserWarning)

#
# ARQUITECTURA CORRECTA (confirmada por diagnóstico):
#
#   DEVOLUCIONES: MTRANS.MOVGEN -> MSTOCK.NUMERO -> MSTOCK.CODIGO -> DETMSTOCK.NUMR
#   ENVIOS:       COMPROBANTEV (FLETRA='R', FPTOVEN=1) -> COMPROBANTEVDET
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
    Retorna (df_art, df_chart, df_env, remitos_art, lista_semana).
    df_art       = devoluciones aprobadas, agrupado por (Codigo, ..., Local, Anio)
    df_chart     = devoluciones por FechaStr/Local/Temporada para gráfico
    df_env       = envíos Central->locales, agrupado por (Codigo, ..., Local, Anio)
    remitos_art  = dict {Codigo: [{r, f, l, q}]} con remitos del último año
    lista_semana = lista de remitos de los últimos 7 días ordenados por cantidad
    """
    try:
        print("\n" + "="*55)
        print("  Cargando datos...")
        print("="*55)

        conn = conectar()

        print("  [1/3] Devoluciones aprobadas (MTRANS->MSTOCK->DETMSTOCK)...")
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

        print("  [2/3] Envios Central -> locales (COMPROBANTEV/COMPROBANTEVDET)...")
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

        print("  [3/4] Ventas en locales (LURO + PERALTA)...")
        df_vta_raw = pd.read_sql(f"""
            SELECT
                RTRIM(DET.FART)                                              AS Codigo,
                MAX(DET.FTXT)                                                AS Descripcion,
                CAST(COMP.FFCH AS DATE)                                      AS Fecha,
                'LURO'                                                       AS Local,
                ISNULL(MAX(FAM.DESCRIP),  'SIN FAMILIA')                   AS Familia,
                ISNULL(MAX(TIPO.DESCRIP), 'SIN TIPO')                      AS Tipo,
                ISNULL(MAX(CATE.DESCRIP), 'SIN CATEGORIA')                 AS Categoria,
                ISNULL(NULLIF(RTRIM(LTRIM(MAX(ART.ATEMPORADA))), ''), 'S/T') AS Temporada,
                SUM(DET.FCANT   * COMP.SIGNOMOV)                           AS Cantidad,
                SUM(DET.MNTPTOT * COMP.SIGNOMOV)                           AS Monto
            FROM {DB_LURO}.Zoologic.COMPROBANTEV COMP
            INNER JOIN {DB_LURO}.Zoologic.COMPROBANTEVDET DET ON COMP.CODIGO = DET.CODIGO
            LEFT JOIN {DB_CENTRAL}.Zoologic.ART ART ON RTRIM(DET.FART) = ART.ARTCOD
            LEFT JOIN {DB_CENTRAL}.Zoologic.FAMILIA FAM ON FAM.COD = ART.FAMILIA
            LEFT JOIN {DB_CENTRAL}.Zoologic.TIPOART TIPO ON TIPO.COD = ART.TIPOARTI
            LEFT JOIN {DB_CENTRAL}.Zoologic.CATEGART CATE ON CATE.COD = ART.CATEARTI
            WHERE COMP.ANULADO = 0
              AND COMP.FLETRA <> 'R'
              AND LEFT(RTRIM(DET.FART), 1) NOT IN ('Z', '9')
              AND DET.FTXT NOT LIKE '%BOLSA%'
            GROUP BY RTRIM(DET.FART), CAST(COMP.FFCH AS DATE)
            UNION ALL
            SELECT
                RTRIM(DET.FART),
                MAX(DET.FTXT),
                CAST(COMP.FFCH AS DATE),
                'PERALTA',
                ISNULL(MAX(FAM.DESCRIP),  'SIN FAMILIA'),
                ISNULL(MAX(TIPO.DESCRIP), 'SIN TIPO'),
                ISNULL(MAX(CATE.DESCRIP), 'SIN CATEGORIA'),
                ISNULL(NULLIF(RTRIM(LTRIM(MAX(ART.ATEMPORADA))), ''), 'S/T'),
                SUM(DET.FCANT   * COMP.SIGNOMOV),
                SUM(DET.MNTPTOT * COMP.SIGNOMOV)
            FROM {DB_PERALTA}.Zoologic.COMPROBANTEV COMP
            INNER JOIN {DB_PERALTA}.Zoologic.COMPROBANTEVDET DET ON COMP.CODIGO = DET.CODIGO
            LEFT JOIN {DB_CENTRAL}.Zoologic.ART ART ON RTRIM(DET.FART) = ART.ARTCOD
            LEFT JOIN {DB_CENTRAL}.Zoologic.FAMILIA FAM ON FAM.COD = ART.FAMILIA
            LEFT JOIN {DB_CENTRAL}.Zoologic.TIPOART TIPO ON TIPO.COD = ART.TIPOARTI
            LEFT JOIN {DB_CENTRAL}.Zoologic.CATEGART CATE ON CATE.COD = ART.CATEARTI
            WHERE COMP.ANULADO = 0
              AND COMP.FLETRA <> 'R'
              AND LEFT(RTRIM(DET.FART), 1) NOT IN ('Z', '9')
              AND DET.FTXT NOT LIKE '%BOLSA%'
            GROUP BY RTRIM(DET.FART), CAST(COMP.FFCH AS DATE)
        """, conn)

        print("  [4/4] Remitos con detalle (ultimo ano)...")
        df_remdet = pd.read_sql("""
            SELECT
                MT.ORIGNRO                                    AS Remito,
                CAST(MS.FECHA AS DATE)                        AS Fecha,
                UPPER(RTRIM(LTRIM(MT.ORIGDEST)))             AS Local,
                RTRIM(DET.MART)                               AS Codigo,
                MAX(DET.DESCRIP)                              AS Descripcion,
                SUM(DET.CANTI)                                AS Cantidad
            FROM DRAGONFISH_CENTRAL.Zoologic.MTRANS MT
            INNER JOIN DRAGONFISH_CENTRAL.Zoologic.MSTOCK MS
                ON MS.NUMERO = MT.MOVGEN
            INNER JOIN DRAGONFISH_CENTRAL.Zoologic.DETMSTOCK DET
                ON DET.NUMR = MS.CODIGO
            WHERE MT.ORIGLETRA = 'R'
              AND (MT.ANULADO IS NULL OR MT.ANULADO = 0)
              AND (MS.ANULADO IS NULL OR MS.ANULADO = 0)
              AND UPPER(RTRIM(LTRIM(MT.ORIGDEST))) IN ('LURO', 'PERALTA')
              AND DET.DESCRIP NOT LIKE '%BOLSA%'
              AND LEFT(RTRIM(DET.MART), 1) NOT IN ('Z', '9')
              AND MS.FECHA >= DATEADD(DAY, -365, GETDATE())
            GROUP BY MT.ORIGNRO, CAST(MS.FECHA AS DATE),
                     UPPER(RTRIM(LTRIM(MT.ORIGDEST))), RTRIM(DET.MART)
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

        # Procesar ventas
        if not df_vta_raw.empty:
            df_vta_raw['Cantidad'] = pd.to_numeric(df_vta_raw['Cantidad'], errors='coerce').fillna(0)
            df_vta_raw['Monto']    = pd.to_numeric(df_vta_raw['Monto'],    errors='coerce').fillna(0)
            df_vta_raw['Fecha']    = pd.to_datetime(df_vta_raw['Fecha'])
            df_vta_raw['Anio']     = df_vta_raw['Fecha'].dt.year.astype(int)
            df_vta = (
                df_vta_raw.groupby(['Codigo','Descripcion','Familia','Tipo','Categoria','Temporada','Local','Anio'])
                .agg(Cantidad=('Cantidad','sum'), Monto=('Monto','sum'))
                .reset_index()
            )
            df_vta['Cantidad'] = df_vta['Cantidad'].round().astype(int)
            df_vta['Monto']    = df_vta['Monto'].round(2)
        else:
            df_vta = pd.DataFrame(columns=['Codigo','Descripcion','Familia','Tipo','Categoria','Temporada','Local','Anio','Cantidad','Monto'])

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

        # Procesar remitos detalle (último año)
        remitos_art  = {}
        lista_semana = []
        if not df_remdet.empty:
            df_remdet['Cantidad'] = pd.to_numeric(df_remdet['Cantidad'], errors='coerce').fillna(0).astype(int)
            df_remdet['FechaStr'] = df_remdet['Fecha'].astype(str).str[:10]
            df_remdet = df_remdet.sort_values('FechaStr', ascending=False)

            for row in df_remdet.itertuples():
                cod = str(row.Codigo).strip()
                if cod not in remitos_art:
                    remitos_art[cod] = []
                remitos_art[cod].append({
                    'r': int(row.Remito),
                    'f': row.FechaStr,
                    'l': str(row.Local),
                    'q': int(row.Cantidad)
                })

            cutoff = (date.today() - timedelta(days=7)).isoformat()
            df_sem = (
                df_remdet[df_remdet['FechaStr'] >= cutoff]
                .groupby(['Remito', 'FechaStr', 'Local'])
                .agg(Total=('Cantidad', 'sum'), Modelos=('Codigo', 'nunique'))
                .reset_index()
                .sort_values('Total', ascending=False)
                .head(10)
            )
            lista_semana = [
                {'r': int(r.Remito), 'f': r.FechaStr, 'l': str(r.Local),
                 'q': int(r.Total), 'm': int(r.Modelos)}
                for r in df_sem.itertuples()
            ]

        total     = int(df_art['Cantidad'].sum())
        modelos   = df_art['Codigo'].nunique()
        anios     = sorted(df_art['Anio'].unique().tolist())
        total_env = int(df_env['Cantidad'].sum()) if not df_env.empty else 0
        total_vta = int(df_vta['Cantidad'].sum()) if not df_vta.empty else 0
        print(f"\n  DEVOLUCIONES: {total:,} prendas | {modelos} modelos | anos: {anios}")
        print(f"  ENVIOS:       {total_env:,} prendas")
        print(f"  VENTAS:       {total_vta:,} unidades")
        print(f"  REMITOS (sem): {len(lista_semana)} en los ultimos 7 dias")

        return df_art, df_chart, df_env, remitos_art, lista_semana, df_vta

    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        return None


def generar_html(resultado, nombre_archivo="index.html"):
    if resultado is None:
        print("Sin datos para generar el reporte.")
        return

    df_art, df_chart, df_env, remitos_art, lista_semana, df_vta = resultado

    data_art_json    = json.dumps(df_art.to_dict('records'),   ensure_ascii=False)
    data_chart_json  = json.dumps(df_chart.to_dict('records'), ensure_ascii=False)
    data_env_json    = json.dumps(df_env.to_dict('records'),   ensure_ascii=False)
    data_rem_json    = json.dumps(remitos_art,                 ensure_ascii=False)
    data_semana_json = json.dumps(lista_semana,                ensure_ascii=False)
    data_vta_json    = json.dumps(df_vta.to_dict('records'),   ensure_ascii=False)

    anios_unicos = sorted(df_art['Anio'].unique().tolist())
    tipos_unicos = sorted(df_art['Tipo'].unique().tolist())
    temp_unicas  = sorted(df_art['Temporada'].unique().tolist())
    fecha_gen    = date.today().strftime('%d/%m/%Y')

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MARKET | Auditoria Logistica Inversa</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
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
        .section-title  {{ background:#000; color:#fff; padding:10px 16px; font-weight:900;
                           text-transform:uppercase; letter-spacing:3px; margin:50px 0 20px; font-size:1rem; }}
        .chart-wrap     {{ border:var(--borde); padding:20px; }}
        .table-market   {{ border:3px solid #000; }}
        .table-market thead {{ background:#000; color:#fff; }}
        .badge-filtro   {{ background:#000; color:#fff; font-size:.7rem;
                           padding:3px 8px; font-family:inherit; font-weight:900; letter-spacing:1px; }}
        .pct-alta       {{ color:#c00; font-weight:900; }}
        .pct-media      {{ color:#e07000; font-weight:900; }}
        .pct-baja       {{ color:#060; font-weight:900; }}
        .comp-card      {{ border:var(--borde); padding:0; overflow:hidden; height:100%; }}
        .comp-header    {{ padding:16px 20px; font-size:1.5rem; font-weight:900;
                           letter-spacing:6px; text-align:center; }}
        .comp-luro      {{ background:#000; color:#fff; }}
        .comp-peralta   {{ background:#fff; color:#000; border-bottom:4px solid #000; }}
        .comp-body      {{ padding:20px; }}
        .comp-row       {{ display:flex; justify-content:space-between; align-items:baseline;
                           padding:8px 0; border-bottom:1px solid #eee; }}
        .comp-row:last-child {{ border-bottom:none; }}
        .comp-label     {{ font-size:.75rem; letter-spacing:2px; text-transform:uppercase;
                           color:#666; font-weight:700; }}
        .comp-val       {{ font-size:1.4rem; font-weight:900; }}
        .comp-tasa      {{ font-size:1.8rem; font-weight:900; }}
        tr.clickable    {{ cursor:pointer; }}
        tr.clickable:hover td {{ background:#f5f5f5; }}
        .modal-content  {{ border:3px solid #000; border-radius:0; }}
        .modal-header   {{ background:#000; color:#fff; border-radius:0; border-bottom:none; }}
        .modal-title    {{ font-weight:900; letter-spacing:2px; font-size:.95rem; }}
        .btn-close-white {{ filter: invert(1); }}
        .semana-local   {{ font-size:.7rem; padding:2px 8px; font-weight:900;
                           letter-spacing:1px; }}
        .semana-luro    {{ background:#000; color:#fff; }}
        .semana-peralta {{ border:2px solid #000; background:#fff; color:#000; }}
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
        <div class="text-muted small mt-1">Actualizado: {fecha_gen}</div>
        <div id="resumenFiltros" class="mt-2" style="font-size:.9rem;font-weight:400;"></div>
    </div>

    <!-- KPIs globales -->
    <div class="row g-4 mb-5">
        <div class="col-md-3">
            <div class="kpi-box">
                <div class="display-1 fw-bold" id="kpiEnviadas">-</div>
                <div class="fs-5 mt-2">PRENDAS ENVIADAS</div>
                <div class="text-muted small mt-1">Central a Locales</div>
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

    <!-- Comparación entre locales -->
    <h2 class="section-title">Comparacion entre Locales</h2>
    <div class="row g-4 mb-5">
        <div class="col-md-6">
            <div class="comp-card">
                <div class="comp-header comp-luro">LURO</div>
                <div class="comp-body">
                    <div class="comp-row">
                        <span class="comp-label">Prendas Enviadas</span>
                        <span class="comp-val" id="cmp_env_LURO">-</span>
                    </div>
                    <div class="comp-row">
                        <span class="comp-label">Prendas Devueltas</span>
                        <span class="comp-val" id="cmp_dev_LURO">-</span>
                    </div>
                    <div class="comp-row">
                        <span class="comp-label">Modelos Unicos</span>
                        <span class="comp-val" id="cmp_mod_LURO">-</span>
                    </div>
                    <div class="comp-row" style="border-top:3px solid #000;margin-top:8px;padding-top:12px;">
                        <span class="comp-label" style="color:#000;font-size:.9rem;">TASA DEVOLUCION</span>
                        <span class="comp-tasa" id="cmp_tasa_LURO">-</span>
                    </div>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="comp-card">
                <div class="comp-header comp-peralta">PERALTA</div>
                <div class="comp-body">
                    <div class="comp-row">
                        <span class="comp-label">Prendas Enviadas</span>
                        <span class="comp-val" id="cmp_env_PERALTA">-</span>
                    </div>
                    <div class="comp-row">
                        <span class="comp-label">Prendas Devueltas</span>
                        <span class="comp-val" id="cmp_dev_PERALTA">-</span>
                    </div>
                    <div class="comp-row">
                        <span class="comp-label">Modelos Unicos</span>
                        <span class="comp-val" id="cmp_mod_PERALTA">-</span>
                    </div>
                    <div class="comp-row" style="border-top:3px solid #000;margin-top:8px;padding-top:12px;">
                        <span class="comp-label" style="color:#000;font-size:.9rem;">TASA DEVOLUCION</span>
                        <span class="comp-tasa" id="cmp_tasa_PERALTA">-</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Resumen por jerarquía -->
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

    <!-- Evolución -->
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

    <!-- Remitos última semana -->
    <h2 class="section-title">Remitos de la Ultima Semana</h2>
    <div class="table-responsive mb-5">
        <table class="table table-hover table-market align-middle">
            <thead><tr>
                <th>#</th>
                <th>REMITO</th>
                <th>FECHA</th>
                <th>LOCAL</th>
                <th class="text-end">PRENDAS</th>
                <th class="text-end">MODELOS</th>
            </tr></thead>
            <tbody id="tbodySemana"></tbody>
        </table>
    </div>

    <!-- Top 20 más devueltos -->
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

    <!-- Detalle por artículo -->
    <h2 class="section-title">Detalle por Articulo</h2>
    <div class="d-flex align-items-center gap-3 mb-3 flex-wrap">
        <input type="text" id="filtroTabla" placeholder="Buscar codigo o descripcion..."
               oninput="filtrarTabla()"
               style="border:2px solid #000;border-radius:0;padding:6px 14px;font-family:inherit;min-width:280px;">
        <button id="btnMayor" onclick="setExceso('mayor')"
                style="border:2px solid #000;background:transparent;padding:6px 18px;font-family:inherit;font-weight:900;font-size:.85rem;cursor:pointer;white-space:nowrap;">
            DEVUELTO &gt; ENVIADO
        </button>
        <button id="btnMenor" onclick="setExceso('menor')"
                style="border:2px solid #000;background:transparent;padding:6px 18px;font-family:inherit;font-weight:900;font-size:.85rem;cursor:pointer;white-space:nowrap;">
            DEVUELTO &lt; ENVIADO
        </button>
        <span class="text-muted small">Clic en una fila para ver sus remitos</span>
    </div>
    <div class="table-responsive">
        <table class="table table-hover table-market align-middle">
            <thead><tr>
                <th onclick="sortTabla('Codigo')"      style="cursor:pointer;user-select:none;">CODIGO <span id="srt_Codigo"></span></th>
                <th onclick="sortTabla('Descripcion')" style="cursor:pointer;user-select:none;">DESCRIPCION <span id="srt_Descripcion"></span></th>
                <th onclick="sortTabla('Familia')"     style="cursor:pointer;user-select:none;">FAMILIA <span id="srt_Familia"></span></th>
                <th onclick="sortTabla('Tipo')"        style="cursor:pointer;user-select:none;">TIPO <span id="srt_Tipo"></span></th>
                <th onclick="sortTabla('Temporada')"   style="cursor:pointer;user-select:none;">TEMPORADA <span id="srt_Temporada"></span></th>
                <th onclick="sortTabla('Enviado')"     style="cursor:pointer;user-select:none;" class="text-end">ENVIADO <span id="srt_Enviado"></span></th>
                <th onclick="sortTabla('Vendido')"     style="cursor:pointer;user-select:none;" class="text-end">VENDIDO <span id="srt_Vendido"></span></th>
                <th onclick="sortTabla('Cantidad')"    style="cursor:pointer;user-select:none;" class="text-end">DEVUELTO <span id="srt_Cantidad"></span></th>
                <th onclick="sortTabla('PctEnv')"      style="cursor:pointer;user-select:none;" class="text-end">% DEV/ENV <span id="srt_PctEnv"></span></th>
                <th onclick="sortTabla('PctVta')"      style="cursor:pointer;user-select:none;" class="text-end">% DEV/VTA <span id="srt_PctVta"></span></th>
            </tr></thead>
            <tbody id="tbodyDetalle"></tbody>
        </table>
    </div>
</div>

<!-- Modal remitos por artículo -->
<div class="modal fade" id="modalRemitos" tabindex="-1">
    <div class="modal-dialog modal-lg">
        <div class="modal-content">
            <div class="modal-header">
                <div>
                    <div class="modal-title" id="modalCodigo">—</div>
                    <div class="small mt-1" style="color:#aaa;" id="modalDescripcion">—</div>
                </div>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body p-0">
                <table class="table table-hover mb-0 align-middle">
                    <thead style="background:#222;color:#fff;">
                        <tr>
                            <th class="ps-3">REMITO</th>
                            <th>FECHA</th>
                            <th>LOCAL</th>
                            <th class="text-end pe-3">PRENDAS</th>
                        </tr>
                    </thead>
                    <tbody id="tbodyModalRemitos"></tbody>
                </table>
            </div>
            <div class="modal-footer" style="border-top:2px solid #000;">
                <span class="text-muted small" id="modalNota">Datos del ultimo ano</span>
            </div>
        </div>
    </div>
</div>

<script>
const DATA_ART    = {data_art_json};
const DATA_CHART  = {data_chart_json};
const DATA_ENV    = {data_env_json};
const DATA_VTA    = {data_vta_json};
const DATA_REM    = {data_rem_json};
const DATA_SEMANA = {data_semana_json};
const ANIOS_DISP  = {json.dumps(anios_unicos)};
const TIPOS_DISP  = {json.dumps(tipos_unicos)};
const TEMP_DISP   = {json.dumps(temp_unicas)};

let estado = {{
    anios:      new Set(ANIOS_DISP),
    local:      'AMBOS',
    temporada:  'TODAS',
    agrupacion: 'DIA',
    busqueda:   '',
    exceso:     '',
    sort:       {{ col: 'PctVta', dir: -1 }}
}};
let chartInstance = null;
let datosTabla    = [];
let modalBS       = null;

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

    modalBS = new bootstrap.Modal(document.getElementById('modalRemitos'));

    // Indice inverso: Remito -> [{{cod, desc, q}}]
    const artDescMap = {{}};
    DATA_ART.forEach(d => {{ if (!artDescMap[d.Codigo]) artDescMap[d.Codigo] = d.Descripcion; }});
    window.remToArts = {{}};
    Object.entries(DATA_REM).forEach(([cod, lst]) => {{
        lst.forEach(r => {{
            if (!window.remToArts[r.r]) window.remToArts[r.r] = [];
            window.remToArts[r.r].push({{ cod, q: r.q, desc: artDescMap[cod] || cod }});
        }});
    }});
    Object.values(window.remToArts).forEach(arr => arr.sort((a, b) => b.q - a.q));

    renderSemana();
    actualizar();
}})();

// ── Utilidades ──────────────────────────────────────────────
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

// ── Filtros de datos ────────────────────────────────────────
function filtrarArt() {{
    const tipo = document.getElementById('filtroTipo').value;
    return DATA_ART.filter(d =>
        estado.anios.has(d.Anio) &&
        (estado.local     === 'AMBOS' || d.Local     === estado.local) &&
        (estado.temporada === 'TODAS' || d.Temporada === estado.temporada) &&
        (tipo             === 'TODOS' || d.Tipo       === tipo)
    );
}}
function filtrarVta() {{
    const tipo = document.getElementById('filtroTipo').value;
    return DATA_VTA.filter(d =>
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

// ── Actualizar todo ─────────────────────────────────────────
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

    renderComparacion(art, env);
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

    const vta = filtrarVta();
    const envMap = {{}};
    agrupar(env, ['Codigo']).forEach(d => {{ envMap[d.Codigo] = d.Cantidad; }});
    const vtaMap = {{}};
    agrupar(vta, ['Codigo']).forEach(d => {{ vtaMap[d.Codigo] = d.Cantidad; }});

    renderTopDevueltos(agrupar(art, ['Codigo','Descripcion','Familia','Tipo']).slice(0, 20), envMap);

    datosTabla = agrupar(art, ['Codigo','Descripcion','Familia','Tipo','Categoria','Temporada']).map(d => {{
        const env = envMap[d.Codigo] || 0;
        const vta = vtaMap[d.Codigo] || 0;
        return {{
            ...d,
            Enviado: env,
            Vendido: vta,
            PctEnv: env > 0 ? d.Cantidad / env : 0,
            PctVta: vta > 0 ? d.Cantidad / vta : 0
        }};
    }});
    renderTabla(datosTabla);
}}

// ── Comparación entre locales ───────────────────────────────
function renderComparacion(art, env) {{
    ['LURO', 'PERALTA'].forEach(local => {{
        const a = art.filter(d => d.Local === local);
        const e = env.filter(d => d.Local === local);
        const dev = a.reduce((s, d) => s + d.Cantidad, 0);
        const envi = e.reduce((s, d) => s + d.Cantidad, 0);
        const mod = new Set(a.map(d => d.Codigo)).size;
        const t = envi > 0 ? (dev / envi * 100).toFixed(1) + '%' : '—';
        document.getElementById('cmp_env_'  + local).textContent = envi.toLocaleString('es-AR');
        document.getElementById('cmp_dev_'  + local).textContent = dev.toLocaleString('es-AR');
        document.getElementById('cmp_mod_'  + local).textContent = mod.toLocaleString('es-AR');
        document.getElementById('cmp_tasa_' + local).textContent = t;
    }});
}}

// ── Remitos última semana (estático, expandible) ───────────
function renderSemana() {{
    const tbody = document.getElementById('tbodySemana');
    if (!DATA_SEMANA.length) {{
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-3">Sin remitos en los últimos 7 días</td></tr>';
        return;
    }}
    tbody.innerHTML = DATA_SEMANA.map((d, i) => {{
        const arts  = window.remToArts[d.r] || [];
        const detId = 'semdet_' + d.r;
        const artRows = arts.map(a =>
            `<tr style="background:#f8f8f8;">
                <td></td>
                <td class="font-monospace small ps-4 text-muted">${{a.cod}}</td>
                <td colspan="3" class="small">${{a.desc}}</td>
                <td class="text-end pe-2 fw-bold small">${{a.q.toLocaleString('es-AR')}}</td>
            </tr>`
        ).join('');
        return `<tr class="clickable" onclick="toggleSemDet('${{detId}}')">
                <td class="text-muted small">${{i + 1}}</td>
                <td class="fw-bold font-monospace">R ${{d.r}}</td>
                <td>${{d.f}}</td>
                <td><span class="semana-local ${{d.l === 'LURO' ? 'semana-luro' : 'semana-peralta'}}">${{d.l}}</span></td>
                <td class="text-end fw-bold">${{d.q.toLocaleString('es-AR')}}</td>
                <td class="text-end text-muted">${{d.m}} <span style="font-size:.7rem;">&#9660;</span></td>
            </tr>
            <tr id="${{detId}}" style="display:none;">
                <td colspan="6" class="p-0" style="border-top:none;">
                    <table class="table table-sm mb-0">${{artRows}}</table>
                </td>
            </tr>`;
    }}).join('');
}}
function toggleSemDet(id) {{
    const el = document.getElementById(id);
    el.style.display = el.style.display === 'none' ? '' : 'none';
}}

// ── Top 20 más devueltos ────────────────────────────────────
function renderTopDevueltos(data, envMap) {{
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

// ── Tabla de detalle ────────────────────────────────────────
const SORT_TEXT_COLS = ['Codigo','Descripcion','Familia','Tipo','Categoria','Temporada'];
function sortTabla(col) {{
    if (estado.sort.col === col) {{
        estado.sort.dir *= -1;
    }} else {{
        estado.sort.col = col;
        estado.sort.dir = SORT_TEXT_COLS.includes(col) ? 1 : -1;
    }}
    renderTabla(datosTabla);
}}
function renderTop(id, data, col) {{
    document.getElementById(id).innerHTML = data.map(d =>
        `<tr><td>${{d[col]}}</td><td class="text-end fw-bold">${{d.Cantidad.toLocaleString('es-AR')}}</td></tr>`
    ).join('');
}}
function renderTabla(data) {{
    const q = estado.busqueda;
    let filtrado = estado.exceso === 'mayor' ? data.filter(d => d.Enviado > 0 && d.Cantidad > d.Enviado)
                 : estado.exceso === 'menor' ? data.filter(d => d.Enviado > 0 && d.Cantidad < d.Enviado)
                 : data;
    if (q) filtrado = filtrado.filter(d =>
        d.Codigo.toLowerCase().includes(q) || d.Descripcion.toLowerCase().includes(q) ||
        d.Familia.toLowerCase().includes(q) || d.Tipo.toLowerCase().includes(q));

    const {{ col, dir }} = estado.sort;
    filtrado = [...filtrado].sort((a, b) => {{
        const va = a[col] ?? (SORT_TEXT_COLS.includes(col) ? '' : 0);
        const vb = b[col] ?? (SORT_TEXT_COLS.includes(col) ? '' : 0);
        return SORT_TEXT_COLS.includes(col)
            ? dir * String(va).localeCompare(String(vb))
            : dir * (vb - va);
    }});
    ['Codigo','Descripcion','Familia','Tipo','Temporada','Enviado','Vendido','Cantidad','PctEnv','PctVta'].forEach(c => {{
        const el = document.getElementById('srt_' + c);
        if (el) el.textContent = c === col ? (dir === -1 ? ' ▼' : ' ▲') : '';
    }});
    document.getElementById('tbodyDetalle').innerHTML = filtrado.map(d =>
        `<tr class="clickable"
             data-cod="${{d.Codigo}}"
             data-desc="${{d.Descripcion.replace(/"/g, '&quot;')}}"
             onclick="verRemitosEl(this)">
            <td class="fw-bold font-monospace small">${{d.Codigo}}</td>
            <td>${{d.Descripcion}}</td>
            <td class="text-muted small">${{d.Familia}}</td>
            <td class="text-muted small">${{d.Tipo}}</td>
            <td class="text-muted small">${{d.Temporada}}</td>
            <td class="text-end text-muted">${{d.Enviado > 0 ? d.Enviado.toLocaleString('es-AR') : '—'}}</td>
            <td class="text-end text-muted">${{d.Vendido > 0 ? d.Vendido.toLocaleString('es-AR') : '—'}}</td>
            <td class="text-end fw-bold">${{d.Cantidad.toLocaleString('es-AR')}}</td>
            <td class="text-end">${{fmtPct(d.Cantidad, d.Enviado)}}</td>
            <td class="text-end">${{fmtPct(d.Cantidad, d.Vendido)}}</td>
        </tr>`
    ).join('');
}}

// ── Modal: remitos por artículo ─────────────────────────────
function verRemitosEl(el) {{
    const cod  = el.dataset.cod;
    const desc = el.dataset.desc;
    const remitos = DATA_REM[cod] || [];

    document.getElementById('modalCodigo').textContent     = cod;
    document.getElementById('modalDescripcion').textContent = desc;

    const tbody = document.getElementById('tbodyModalRemitos');
    if (!remitos.length) {{
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">Sin remitos en el último año</td></tr>';
    }} else {{
        const total = remitos.reduce((s, r) => s + r.q, 0);
        tbody.innerHTML = remitos.map(r =>
            `<tr>
                <td class="ps-3 fw-bold font-monospace">R ${{r.r}}</td>
                <td>${{r.f}}</td>
                <td><span class="semana-local ${{r.l === 'LURO' ? 'semana-luro' : 'semana-peralta'}}">${{r.l}}</span></td>
                <td class="text-end pe-3 fw-bold">${{r.q.toLocaleString('es-AR')}}</td>
            </tr>`
        ).join('') +
        `<tr style="background:#f8f8f8;border-top:2px solid #000;">
            <td colspan="3" class="ps-3 fw-bold text-end">TOTAL</td>
            <td class="text-end pe-3 fw-bold">${{total.toLocaleString('es-AR')}}</td>
        </tr>`;
        document.getElementById('modalNota').textContent =
            remitos.length + ' remito/s en el último año';
    }}
    modalBS.show();
}}
</script>
</body>
</html>"""

    with open(nombre_archivo, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n  Reporte guardado: {nombre_archivo}  ({round(len(html)/1024):.0f} KB)")


def generar_dashboard(resultado, nombre_archivo="dashboard.html"):
    if resultado is None:
        return
    df_art, df_chart, df_env, remitos_art, lista_semana, df_vta = resultado

    # ── Datos pre-computados para cada slide ──────────────────────────────
    total_dev = int(df_art['Cantidad'].sum())
    total_env = int(df_env['Cantidad'].sum()) if not df_env.empty else 0
    total_vta = int(df_vta['Cantidad'].sum()) if not df_vta.empty else 0
    modelos   = int(df_art['Codigo'].nunique())
    tasa_env  = round(total_dev / total_env * 100, 1) if total_env > 0 else 0
    tasa_vta  = round(total_dev / total_vta * 100, 1) if total_vta > 0 else 0
    fecha_gen = date.today().strftime('%d/%m/%Y')

    # Tendencia últimos 90 días → SVG inline
    cutoff90 = (date.today() - timedelta(days=90)).strftime('%Y-%m-%d')
    trend = (df_chart.groupby('Fecha')['Cantidad'].sum()
             .reset_index().query('Fecha >= @cutoff90').sort_values('Fecha'))
    vw, vh = 1000, 160
    if not trend.empty:
        vals = trend['Cantidad'].tolist()
        lbls = trend['Fecha'].tolist()
        vmax = max(vals) * 1.1 if max(vals) > 0 else 1
        n    = len(vals)
        pts, fill = [], [f'0,{vh}']
        for i, v in enumerate(vals):
            x = round(vw * i / max(n - 1, 1), 1)
            y = round(vh - v / vmax * vh, 1)
            pts.append(f'{x},{y}'); fill.append(f'{x},{y}')
        fill.append(f'{vw},{vh}')
        step = max(1, n // 9)
        xlabels = ''.join(
            f'<text x="{round(vw*i/max(n-1,1),1)}" y="195" text-anchor="middle" '
            f'fill="#666" font-family="Arial" font-size="13">{lbls[i][5:]}</text>'
            for i in range(0, n, step)
        )
        trend_svg = (
            f'<svg viewBox="0 0 {vw} 205" style="width:100%;height:100%;">'
            f'<polygon points="{" ".join(fill)}" fill="rgba(255,255,255,0.07)"/>'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="#fff" stroke-width="2.5" stroke-linejoin="round"/>'
            f'{xlabels}</svg>'
        )
    else:
        trend_svg = '<p style="color:#555;">Sin datos</p>'

    def bar_rows(df_grp, col_name, max_bars=8, color='#fff'):
        df_s = df_grp.groupby(col_name)['Cantidad'].sum().reset_index().sort_values('Cantidad', ascending=False).head(max_bars)
        mx   = int(df_s['Cantidad'].max()) if not df_s.empty else 1
        rows = ''
        for _, r in df_s.iterrows():
            pct = int(r['Cantidad'] / mx * 100)
            rows += (
                f'<div style="margin-bottom:14px;">'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:5px;">'
                f'<span style="font-size:.82rem;color:#bbb;letter-spacing:.5px;">{r[col_name]}</span>'
                f'<span style="font-weight:900;font-size:.9rem;">{int(r["Cantidad"]):,}</span></div>'
                f'<div style="background:#1a1a1a;height:7px;">'
                f'<div style="background:{color};height:7px;width:{pct}%;"></div></div></div>'
            )
        return rows

    fam_rows  = bar_rows(df_art, 'Familia',   8)
    tipo_rows = bar_rows(df_art, 'Tipo',      6)
    cate_rows = bar_rows(df_art, 'Categoria', 6)

    # Locales
    loc = {}
    for local in ['LURO', 'PERALTA']:
        d = int(df_art[df_art['Local'] == local]['Cantidad'].sum())
        e = int(df_env[df_env['Local'] == local]['Cantidad'].sum()) if not df_env.empty else 0
        v = int(df_vta[df_vta['Local'] == local]['Cantidad'].sum()) if not df_vta.empty else 0
        m = int(df_art[df_art['Local'] == local]['Codigo'].nunique())
        loc[local] = {'dev': d, 'env': e, 'vta': v, 'mod': m,
                      'te': round(d/e*100, 1) if e > 0 else 0,
                      'tv': round(d/v*100, 1) if v > 0 else 0}

    # Top artículos % DEV/VTA
    art_g = df_art.groupby(['Codigo','Descripcion'])['Cantidad'].sum().reset_index().rename(columns={'Cantidad':'Dev'})
    vta_g = df_vta.groupby('Codigo')['Cantidad'].sum().reset_index().rename(columns={'Cantidad':'Vta'}) if not df_vta.empty else pd.DataFrame(columns=['Codigo','Vta'])
    if not vta_g.empty:
        top_dvt = (art_g.merge(vta_g, on='Codigo', how='inner')
                   .query('Vta >= 20')
                   .assign(Pct=lambda x: (x['Dev'] / x['Vta'] * 100).round(1))
                   .sort_values('Pct', ascending=False).head(10))
    else:
        top_dvt = pd.DataFrame()

    top_dvt_rows = ''
    max_pct = float(top_dvt['Pct'].max()) if not top_dvt.empty else 1
    for i, (_, r) in enumerate(top_dvt.iterrows()):
        bw = int(r['Pct'] / max_pct * 100)
        top_dvt_rows += (
            f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:13px;">'
            f'<div style="color:#444;width:18px;font-size:.85rem;text-align:right;">{i+1}</div>'
            f'<div style="flex:1;">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
            f'<span style="font-size:.78rem;color:#bbb;">{str(r["Descripcion"])[:42]}</span>'
            f'<span style="font-weight:900;font-size:.95rem;margin-left:10px;">{r["Pct"]:.1f}%</span></div>'
            f'<div style="background:#1a1a1a;height:6px;">'
            f'<div style="background:#fff;height:6px;width:{bw}%;"></div></div></div></div>'
        )

    # Remitos última semana
    sem_rows = ''
    for i, r in enumerate(lista_semana[:8]):
        sem_rows += (
            f'<div style="display:flex;align-items:center;gap:14px;padding:11px 0;border-bottom:1px solid #1e1e1e;">'
            f'<div style="color:#444;width:20px;font-size:.85rem;">{i+1}</div>'
            f'<div style="background:#fff;color:#000;padding:2px 10px;font-weight:900;'
            f'font-size:.65rem;letter-spacing:2px;white-space:nowrap;">R {r["r"]}</div>'
            f'<div style="flex:1;font-size:.8rem;color:#888;">{r["f"]} · {r["l"]}</div>'
            f'<div style="font-weight:900;font-size:1.05rem;">{r["q"]:,}</div>'
            f'<div style="font-size:.7rem;color:#555;white-space:nowrap;">{r["m"]} mod</div></div>'
        )

    N_SLIDES = 8
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MARKET | Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
html,body{{width:100%;height:100%;background:#000;color:#fff;
    font-family:'Arial Black','Arial',sans-serif;overflow:hidden;}}
.slide{{position:absolute;inset:0;opacity:0;transition:opacity .7s ease;
    display:flex;flex-direction:column;padding:52px 64px 72px;pointer-events:none;}}
.slide.active{{opacity:1;pointer-events:all;}}
.slide-label{{font-size:.6rem;letter-spacing:4px;color:#444;text-transform:uppercase;margin-bottom:8px;}}
.logo-sm{{font-size:.9rem;font-weight:900;letter-spacing:8px;color:#333;}}
.slide-title{{font-size:1rem;font-weight:900;letter-spacing:4px;color:#555;
    text-transform:uppercase;margin-bottom:32px;padding-bottom:16px;border-bottom:2px solid #1a1a1a;}}
.kpi-giant{{font-size:clamp(3rem,8vw,7rem);font-weight:900;line-height:1;}}
.kpi-label{{font-size:.7rem;letter-spacing:3px;text-transform:uppercase;color:#555;margin-top:8px;}}
.kpi-sub{{font-size:.85rem;color:#888;margin-top:4px;}}
.progress-wrap{{position:fixed;bottom:0;left:0;right:0;height:3px;background:#111;z-index:50;}}
.progress-bar{{height:3px;background:#fff;width:0%;transition:width linear;}}
.dot-nav{{position:fixed;bottom:14px;right:24px;display:flex;gap:8px;z-index:50;}}
.dot{{width:6px;height:6px;border-radius:50%;background:#333;cursor:pointer;transition:.2s;}}
.dot.active{{background:#fff;}}
.header-row{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:28px;}}
.divider{{width:40px;height:3px;background:#fff;margin:10px 0 20px;}}
</style>
</head>
<body>

<!-- SLIDE 1: RESUMEN EJECUTIVO -->
<div class="slide active" id="s0">
    <div class="header-row">
        <div class="logo-sm">MARKET</div>
        <div style="font-size:.6rem;letter-spacing:3px;color:#333;">ACTUALIZADO {fecha_gen}</div>
    </div>
    <div class="slide-title">Auditoria Logistica Inversa — Resumen Ejecutivo</div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:40px;flex:1;align-items:center;">
        <div>
            <div class="kpi-giant">{total_dev:,}</div>
            <div class="divider"></div>
            <div class="kpi-label">Prendas Devueltas</div>
            <div class="kpi-sub">Aprobadas en Central</div>
        </div>
        <div>
            <div class="kpi-giant" style="color:#555;">{total_env:,}</div>
            <div class="divider" style="background:#333;"></div>
            <div class="kpi-label">Prendas Enviadas</div>
            <div class="kpi-sub">Central a Locales</div>
        </div>
        <div>
            <div class="kpi-giant">{tasa_env}%</div>
            <div class="divider"></div>
            <div class="kpi-label">Tasa de Devolucion</div>
            <div class="kpi-sub">Devuelto / Enviado</div>
        </div>
    </div>
    <div style="display:flex;gap:48px;margin-top:24px;padding-top:24px;border-top:1px solid #1a1a1a;">
        <div><span style="font-size:1.6rem;font-weight:900;">{total_vta:,}</span><span style="font-size:.65rem;letter-spacing:2px;color:#555;margin-left:10px;">VENDIDAS</span></div>
        <div><span style="font-size:1.6rem;font-weight:900;">{tasa_vta}%</span><span style="font-size:.65rem;letter-spacing:2px;color:#555;margin-left:10px;">DEV / VENTAS</span></div>
        <div><span style="font-size:1.6rem;font-weight:900;">{modelos:,}</span><span style="font-size:.65rem;letter-spacing:2px;color:#555;margin-left:10px;">MODELOS UNICOS</span></div>
    </div>
</div>

<!-- SLIDE 2: DEV vs VENTAS -->
<div class="slide" id="s1">
    <div class="logo-sm" style="margin-bottom:8px;">MARKET</div>
    <div class="slide-title">Devolucion vs Ventas</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:60px;flex:1;align-items:center;">
        <div>
            <div style="font-size:.7rem;letter-spacing:3px;color:#444;margin-bottom:12px;">TASA DEV / VENTAS</div>
            <div style="font-size:clamp(5rem,15vw,11rem);font-weight:900;line-height:1;">{tasa_vta}%</div>
            <div class="divider"></div>
            <div style="font-size:.75rem;color:#666;line-height:1.8;">
                De cada 100 prendas vendidas<br>
                <strong style="color:#fff;font-size:1rem;">{tasa_vta}</strong> volvieron a Central
            </div>
        </div>
        <div>
            <div style="margin-bottom:32px;">
                <div style="font-size:.65rem;letter-spacing:3px;color:#555;margin-bottom:8px;">VENDIDAS</div>
                <div style="display:flex;align-items:center;gap:16px;">
                    <div style="flex:1;height:16px;background:#fff;"></div>
                    <div style="font-weight:900;font-size:1.1rem;white-space:nowrap;">{total_vta:,}</div>
                </div>
            </div>
            <div>
                <div style="font-size:.65rem;letter-spacing:3px;color:#555;margin-bottom:8px;">DEVUELTAS</div>
                <div style="display:flex;align-items:center;gap:16px;">
                    <div style="width:{min(int(total_dev/max(total_vta,1)*100),100)}%;height:16px;background:#fff;min-width:4px;"></div>
                    <div style="font-weight:900;font-size:1.1rem;white-space:nowrap;">{total_dev:,}</div>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- SLIDE 3: TENDENCIA -->
<div class="slide" id="s2">
    <div class="logo-sm" style="margin-bottom:8px;">MARKET</div>
    <div class="slide-title">Tendencia — Ultimos 90 Dias</div>
    <div style="flex:1;display:flex;align-items:center;">
        {trend_svg}
    </div>
</div>

<!-- SLIDE 4: RANKING POR FAMILIA -->
<div class="slide" id="s3">
    <div class="logo-sm" style="margin-bottom:8px;">MARKET</div>
    <div class="slide-title">Ranking por Familia</div>
    <div style="flex:1;overflow:hidden;">
        {fam_rows}
    </div>
</div>

<!-- SLIDE 5: TIPO / CATEGORÍA -->
<div class="slide" id="s4">
    <div class="logo-sm" style="margin-bottom:8px;">MARKET</div>
    <div class="slide-title">Por Tipo y Categoria</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:60px;flex:1;overflow:hidden;">
        <div>
            <div style="font-size:.65rem;letter-spacing:3px;color:#444;margin-bottom:20px;">TIPO DE ARTICULO</div>
            {tipo_rows}
        </div>
        <div>
            <div style="font-size:.65rem;letter-spacing:3px;color:#444;margin-bottom:20px;">CATEGORIA</div>
            {cate_rows}
        </div>
    </div>
</div>

<!-- SLIDE 6: LURO vs PERALTA -->
<div class="slide" id="s5">
    <div class="logo-sm" style="margin-bottom:8px;">MARKET</div>
    <div class="slide-title">Comparacion entre Locales</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:3px;flex:1;">
        <div style="background:#0a0a0a;padding:36px;">
            <div style="font-size:2rem;font-weight:900;letter-spacing:8px;margin-bottom:24px;">LURO</div>
            <div style="margin-bottom:18px;padding-bottom:18px;border-bottom:1px solid #1a1a1a;">
                <div style="font-size:.6rem;letter-spacing:3px;color:#444;">TASA DEV/ENV</div>
                <div style="font-size:3rem;font-weight:900;">{loc['LURO']['te']}%</div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
                <div><div style="font-size:.6rem;letter-spacing:2px;color:#444;">DEVUELTAS</div><div style="font-size:1.4rem;font-weight:900;">{loc['LURO']['dev']:,}</div></div>
                <div><div style="font-size:.6rem;letter-spacing:2px;color:#444;">ENVIADAS</div><div style="font-size:1.4rem;font-weight:900;color:#555;">{loc['LURO']['env']:,}</div></div>
                <div><div style="font-size:.6rem;letter-spacing:2px;color:#444;">VENDIDAS</div><div style="font-size:1.4rem;font-weight:900;color:#555;">{loc['LURO']['vta']:,}</div></div>
                <div><div style="font-size:.6rem;letter-spacing:2px;color:#444;">DEV/VTA</div><div style="font-size:1.4rem;font-weight:900;">{loc['LURO']['tv']}%</div></div>
            </div>
        </div>
        <div style="background:#111;padding:36px;">
            <div style="font-size:2rem;font-weight:900;letter-spacing:8px;margin-bottom:24px;">PERALTA</div>
            <div style="margin-bottom:18px;padding-bottom:18px;border-bottom:1px solid #222;">
                <div style="font-size:.6rem;letter-spacing:3px;color:#444;">TASA DEV/ENV</div>
                <div style="font-size:3rem;font-weight:900;">{loc['PERALTA']['te']}%</div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
                <div><div style="font-size:.6rem;letter-spacing:2px;color:#444;">DEVUELTAS</div><div style="font-size:1.4rem;font-weight:900;">{loc['PERALTA']['dev']:,}</div></div>
                <div><div style="font-size:.6rem;letter-spacing:2px;color:#444;">ENVIADAS</div><div style="font-size:1.4rem;font-weight:900;color:#555;">{loc['PERALTA']['env']:,}</div></div>
                <div><div style="font-size:.6rem;letter-spacing:2px;color:#444;">VENDIDAS</div><div style="font-size:1.4rem;font-weight:900;color:#555;">{loc['PERALTA']['vta']:,}</div></div>
                <div><div style="font-size:.6rem;letter-spacing:2px;color:#444;">DEV/VTA</div><div style="font-size:1.4rem;font-weight:900;">{loc['PERALTA']['tv']}%</div></div>
            </div>
        </div>
    </div>
</div>

<!-- SLIDE 7: REMITOS RECIENTES -->
<div class="slide" id="s6">
    <div class="logo-sm" style="margin-bottom:8px;">MARKET</div>
    <div class="slide-title">Remitos de la Ultima Semana</div>
    <div style="flex:1;overflow:hidden;">
        {sem_rows}
    </div>
</div>

<!-- SLIDE 8: TOP % DEV/VTA -->
<div class="slide" id="s7">
    <div class="logo-sm" style="margin-bottom:8px;">MARKET</div>
    <div class="slide-title">Top Articulos — Mayor Tasa Dev / Venta</div>
    <div style="flex:1;overflow:hidden;">
        {top_dvt_rows}
    </div>
</div>

<!-- Barra de progreso -->
<div class="progress-wrap"><div class="progress-bar" id="pgBar"></div></div>

<!-- Puntos de navegación -->
<div class="dot-nav" id="dotNav"></div>

<script>
const N = {N_SLIDES};
const DURATION = 30000;
let cur = 0, timer = null, paused = false;

const dots = document.getElementById('dotNav');
for (let i = 0; i < N; i++) {{
    const d = document.createElement('span');
    d.className = 'dot' + (i === 0 ? ' active' : '');
    d.onclick = () => goTo(i);
    dots.appendChild(d);
}}

function goTo(n) {{
    document.getElementById('s' + cur).classList.remove('active');
    document.querySelectorAll('.dot')[cur].classList.remove('active');
    cur = (n + N) % N;
    document.getElementById('s' + cur).classList.add('active');
    document.querySelectorAll('.dot')[cur].classList.add('active');
    startProgress();
    clearTimeout(timer);
    if (!paused) timer = setTimeout(() => goTo(cur + 1), DURATION);
}}

function startProgress() {{
    const bar = document.getElementById('pgBar');
    bar.style.transition = 'none';
    bar.style.width = '0%';
    requestAnimationFrame(() => requestAnimationFrame(() => {{
        bar.style.transition = `width ${{DURATION}}ms linear`;
        bar.style.width = '100%';
    }}));
}}

document.body.addEventListener('mouseenter', () => {{
    paused = true; clearTimeout(timer);
    document.getElementById('pgBar').style.transition = 'none';
}});
document.body.addEventListener('mouseleave', () => {{
    paused = false;
    timer = setTimeout(() => goTo(cur + 1), DURATION);
    startProgress();
}});
document.addEventListener('keydown', e => {{
    if (e.key === 'ArrowRight' || e.key === ' ') goTo(cur + 1);
    if (e.key === 'ArrowLeft') goTo(cur - 1);
}});

goTo(0);
</script>
</body>
</html>"""

    with open(nombre_archivo, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  Dashboard guardado: {nombre_archivo}  ({round(len(html)/1024):.0f} KB)")


if __name__ == '__main__':
    res = obtener_datos()
    generar_html(res, "index.html")
    generar_dashboard(res, "dashboard.html")
