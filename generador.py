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

        # Monthly envíos/ventas for timeline chart in envios.html
        if not df_env_raw.empty:
            df_env_raw['AnoMes'] = df_env_raw['Fecha'].dt.to_period('M').astype(str)
            df_env_chart = (
                df_env_raw.groupby(['AnoMes', 'Local'])['Cantidad']
                .sum().reset_index().sort_values('AnoMes')
            )
            df_env_chart['Cantidad'] = df_env_chart['Cantidad'].astype(int)
        else:
            df_env_chart = pd.DataFrame(columns=['AnoMes', 'Local', 'Cantidad'])

        if not df_vta_raw.empty:
            df_vta_raw['AnoMes'] = df_vta_raw['Fecha'].dt.to_period('M').astype(str)
            df_vta_chart = (
                df_vta_raw.groupby(['AnoMes', 'Local'])['Cantidad']
                .sum().reset_index().sort_values('AnoMes')
            )
            df_vta_chart['Cantidad'] = df_vta_chart['Cantidad'].astype(int)
        else:
            df_vta_chart = pd.DataFrame(columns=['AnoMes', 'Local', 'Cantidad'])

        total     = int(df_art['Cantidad'].sum())
        modelos   = df_art['Codigo'].nunique()
        anios     = sorted(df_art['Anio'].unique().tolist())
        total_env = int(df_env['Cantidad'].sum()) if not df_env.empty else 0
        total_vta = int(df_vta['Cantidad'].sum()) if not df_vta.empty else 0
        print(f"\n  DEVOLUCIONES: {total:,} prendas | {modelos} modelos | anos: {anios}")
        print(f"  ENVIOS:       {total_env:,} prendas")
        print(f"  VENTAS:       {total_vta:,} unidades")
        print(f"  REMITOS (sem): {len(lista_semana)} en los ultimos 7 dias")

        return df_art, df_chart, df_env, remitos_art, lista_semana, df_vta, df_env_chart, df_vta_chart

    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        return None


def generar_html(resultado, nombre_archivo="index.html"):
    if resultado is None:
        print("Sin datos para generar el reporte.")
        return

    df_art, df_chart, df_env, remitos_art, lista_semana, df_vta, *_ = resultado

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
        :root{{--borde:1px solid #2a2a2a;--accent:#e8b963;--bg:#0d0d0d;--card:#141414;--border:#2a2a2a;--text:#f0f0f0;--muted:#888;}}
        *,*::before,*::after{{box-sizing:border-box;}}
        body{{background:var(--bg)!important;color:var(--text)!important;font-family:'Arial Black','Arial',sans-serif;}}
        .text-muted{{color:var(--muted)!important;}}.fw-bold,.fs-4{{color:var(--text);}}
        .table{{--bs-table-color:#f0f0f0;--bs-table-bg:#141414;--bs-table-border-color:#2a2a2a;color:#f0f0f0;}}
        .table-hover>tbody>tr:hover>*{{background-color:#181818!important;color:#f0f0f0;}}
        .modal-content{{background:#141414!important;border:1px solid #2a2a2a!important;border-radius:6px!important;color:#f0f0f0!important;}}
        .modal-header{{background:#111!important;border-bottom:1px solid #2a2a2a!important;}}
        .modal-body{{background:#141414!important;}}
        .btn-close,.btn-close-white{{filter:invert(1);}}
        .filtros-bar{{position:sticky;top:0;z-index:100;background:#111;color:#fff;padding:14px 24px;display:flex;gap:32px;align-items:center;flex-wrap:wrap;border-bottom:2px solid var(--accent);}}
        .filtro-grupo{{display:flex;flex-direction:column;gap:6px;}}
        .filtro-label{{font-size:.65rem;letter-spacing:3px;text-transform:uppercase;color:var(--muted);font-weight:900;}}
        .anio-btn{{border:1px solid #444;background:transparent;color:#888;padding:4px 12px;font-family:inherit;font-weight:900;font-size:.85rem;cursor:pointer;transition:.15s;}}
        .anio-btn.activo{{background:var(--accent);color:#000;border-color:var(--accent);}}
        .tog-btn{{border:1px solid #444;background:transparent;color:#888;padding:5px 14px;font-family:inherit;font-weight:900;font-size:.85rem;cursor:pointer;transition:.15s;}}
        .tog-btn.activo{{background:var(--accent);color:#000;border-color:var(--accent);}}
        .tipo-select{{border:1px solid #444;background:#1a1a1a;color:#888;padding:5px 10px;font-family:inherit;font-weight:900;font-size:.85rem;cursor:pointer;min-width:180px;}}
        .tipo-select option{{background:#1a1a1a;color:#888;}}
        .contenido{{padding:30px 40px;background:var(--bg);}}
        .header-market{{border-bottom:2px solid var(--accent);padding:20px 0;margin-bottom:40px;}}
        .logo{{font-size:3.5rem;font-weight:900;letter-spacing:12px;color:var(--accent);}}
        .kpi-box{{border:var(--borde);background:var(--card);padding:25px;text-align:center;height:100%;}}
        .kpi-inv{{background:#111;color:#fff;}}
        .section-title{{background:#111;color:var(--muted);padding:10px 16px;font-weight:900;text-transform:uppercase;letter-spacing:3px;margin:50px 0 20px;font-size:1rem;border-left:3px solid var(--accent);}}
        .chart-wrap{{border:var(--borde);background:var(--card);padding:20px;}}
        .table-market{{border:1px solid var(--border)!important;color:var(--text);}}
        .table-market thead{{background:#1a1a1a;color:var(--muted);}}
        .table-market tbody tr:hover td{{background:#181818;}}
        .badge-filtro{{background:#111;color:var(--muted);font-size:.7rem;padding:3px 8px;font-family:inherit;font-weight:900;letter-spacing:1px;}}
        .pct-alta{{color:#ef4444;font-weight:900;}}.pct-media{{color:#f97316;font-weight:900;}}.pct-baja{{color:#22c55e;font-weight:900;}}
        .comp-card{{border:var(--borde);background:var(--card);padding:0;overflow:hidden;height:100%;}}
        .comp-header{{padding:16px 20px;font-size:1.5rem;font-weight:900;letter-spacing:6px;text-align:center;}}
        .comp-luro{{background:#1a2e1a;color:#7ef7a0;}}.comp-peralta{{background:#1e1e3a;color:#c07ef7;border-bottom:1px solid #2a2a2a;}}
        .comp-body{{padding:20px;}}
        .comp-row{{display:flex;justify-content:space-between;align-items:baseline;padding:8px 0;border-bottom:1px solid #1e1e1e;}}
        .comp-row:last-child{{border-bottom:none;}}
        .comp-label{{font-size:.75rem;letter-spacing:2px;text-transform:uppercase;color:#555;font-weight:700;}}
        .comp-val{{font-size:1.4rem;font-weight:900;}}.comp-tasa{{font-size:1.8rem;font-weight:900;}}
        tr.clickable{{cursor:pointer;}}tr.clickable:hover td{{background:#181818!important;}}
        .modal-title{{font-weight:900;letter-spacing:2px;font-size:.95rem;}}
        .semana-local{{font-size:.7rem;padding:2px 8px;font-weight:900;letter-spacing:1px;}}
        .semana-luro{{background:#1a2e1a;color:#7ef7a0;}}.semana-peralta{{background:#1e1e3a;color:#c07ef7;border:1px solid #2a2a2a;}}
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
        <div style="margin-top:14px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap;">
            <a href="home.html" style="color:#e8b963;text-decoration:none;font-size:.7rem;letter-spacing:2px;font-weight:900;padding:6px 14px;border:1px solid #e8b963;">INICIO</a>
            <a href="envios.html" style="color:#aaa;text-decoration:none;font-size:.7rem;letter-spacing:2px;font-weight:900;padding:6px 14px;border:1px solid #333;">TRIÁNGULO DE ENVÍOS</a>
            <a href="pendientes.html" style="color:#aaa;text-decoration:none;font-size:.7rem;letter-spacing:2px;font-weight:900;padding:6px 14px;border:1px solid #333;">REMITOS PENDIENTES</a>
            <a href="dashboard.html" style="color:#aaa;text-decoration:none;font-size:.7rem;letter-spacing:2px;font-weight:900;padding:6px 14px;border:1px solid #333;">DASHBOARD</a>
        </div>
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
    if (!modalBS) modalBS = new bootstrap.Modal(document.getElementById('modalRemitos'));
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
    df_art, df_chart, df_env, remitos_art, lista_semana, df_vta, *_ = resultado

    # ── Datos globales ─────────────────────────────────────────────────────
    total_dev = int(df_art['Cantidad'].sum())
    total_env = int(df_env['Cantidad'].sum()) if not df_env.empty else 0
    total_vta = int(df_vta['Cantidad'].sum()) if not df_vta.empty else 0
    modelos   = int(df_art['Codigo'].nunique())
    tasa_env  = round(total_dev / total_env * 100, 1) if total_env > 0 else 0
    tasa_vta  = round(total_dev / total_vta * 100, 1) if total_vta > 0 else 0
    fecha_gen = date.today().strftime('%d/%m/%Y')

    # ── Año actual vs año anterior ─────────────────────────────────────────
    anio_act  = date.today().year
    anio_prev = anio_act - 1
    dev_act   = int(df_art[df_art['Anio'] == anio_act]['Cantidad'].sum())
    dev_prev  = int(df_art[df_art['Anio'] == anio_prev]['Cantidad'].sum())
    delta_yoy = round((dev_act - dev_prev) / dev_prev * 100, 1) if dev_prev > 0 else 0
    yoy_sign  = '+' if delta_yoy > 0 else ''
    yoy_color = '#ef5350' if delta_yoy > 0 else '#66bb6a' if delta_yoy < 0 else '#888'

    # ── Temporada líder ────────────────────────────────────────────────────
    if not df_art.empty:
        temp_grp     = df_art.groupby('Temporada')['Cantidad'].sum().sort_values(ascending=False)
        top_temp     = temp_grp.index[0] if len(temp_grp) > 0 else 'S/T'
        top_temp_qty = int(temp_grp.iloc[0]) if len(temp_grp) > 0 else 0
    else:
        top_temp, top_temp_qty = 'S/T', 0

    # ── Tendencia 90 días → SVG con ejes y anotaciones ──────────
    cutoff90 = (date.today() - timedelta(days=90)).strftime('%Y-%m-%d')
    cutoff30 = (date.today() - timedelta(days=30)).strftime('%Y-%m-%d')
    cutoff60 = (date.today() - timedelta(days=60)).strftime('%Y-%m-%d')
    trend = (df_chart.groupby('Fecha')['Cantidad'].sum()
             .reset_index().query('Fecha >= @cutoff90').sort_values('Fecha'))

    total_90 = int(trend['Cantidad'].sum()) if not trend.empty else 0
    max_90   = int(trend['Cantidad'].max()) if not trend.empty else 0
    avg_90   = round(float(trend['Cantidad'].mean()), 1) if not trend.empty else 0
    last30   = int(trend.query(f'Fecha >= "{cutoff30}"')['Cantidad'].sum()) if not trend.empty else 0
    prev30   = int(trend.query(f'Fecha >= "{cutoff60}" and Fecha < "{cutoff30}"')['Cantidad'].sum()) if not trend.empty else 0
    delta_30 = round((last30 - prev30) / prev30 * 100, 1) if prev30 > 0 else 0

    CW, CH = 1200, 190
    PL, PR, PT, PB = 72, 20, 24, 36

    if not trend.empty:
        vals = trend['Cantidad'].tolist()
        lbls = trend['Fecha'].tolist()
        vmax = max(vals) * 1.18 if max(vals) > 0 else 1
        n    = len(vals)

        def px(i): return round(PL + (CW - PL - PR) * i / max(n - 1, 1), 1)
        def py(v): return round(PT + CH - v / vmax * CH, 1)

        grid_svg, ylab_svg = '', ''
        for ti in range(5):
            yv = vmax * ti / 4
            yp = py(yv)
            dash = 'stroke-dasharray="4,4"' if ti > 0 else ''
            grid_svg += f'<line x1="{PL}" y1="{yp:.1f}" x2="{CW-PR}" y2="{yp:.1f}" stroke="#1e1e1e" stroke-width="1" {dash}/>'
            ylab_svg += f'<text x="{PL-6}" y="{yp+4:.1f}" text-anchor="end" fill="#555" font-family="Arial" font-size="12">{int(yv):,}</text>'

        pts  = [f'{px(i)},{py(v):.1f}' for i, v in enumerate(vals)]
        fill = [f'{px(0):.1f},{PT+CH}'] + pts + [f'{px(n-1):.1f},{PT+CH}']

        step = max(1, n // 9)
        xlab_svg = ''.join(
            f'<text x="{px(i):.1f}" y="{PT+CH+PB-6}" text-anchor="middle" fill="#666" font-family="Arial" font-size="12">{lbls[i][5:]}</text>'
            for i in range(0, n, step)
        )

        mi = vals.index(max(vals))
        peak_x, peak_y = px(mi), py(vals[mi])
        peak_svg = (
            f'<line x1="{peak_x:.1f}" y1="{peak_y:.1f}" x2="{peak_x:.1f}" y2="{PT:.1f}" stroke="#e8b963" stroke-width="1" stroke-dasharray="3,3" opacity="0.6"/>'
            f'<circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="5" fill="#e8b963"/>'
            f'<text x="{peak_x:.1f}" y="{PT-6:.1f}" text-anchor="middle" fill="#e8b963" font-family="Arial Black" font-size="12">{vals[mi]:,}</text>'
        )
        last_dot = f'<circle cx="{px(n-1):.1f}" cy="{py(vals[-1]):.1f}" r="4" fill="#fff"/>'

        trend_svg = (
            f'<svg viewBox="0 0 {CW} {PT+CH+PB}" style="width:100%;height:100%;">'
            f'{grid_svg}'
            f'<polygon points="{" ".join(fill)}" fill="rgba(255,255,255,0.04)"/>'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="#fff" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
            f'{peak_svg}{last_dot}{ylab_svg}{xlab_svg}'
            f'</svg>'
        )
    else:
        trend_svg = '<p style="color:#666;font-size:1.2rem;text-align:center;">Sin datos en el período</p>'
        total_90, max_90, avg_90, last30, prev30, delta_30 = 0, 0, 0, 0, 0, 0

    delta_sign   = '+' if delta_30 > 0 else ''
    delta_color  = '#ef5350' if delta_30 > 0 else '#66bb6a' if delta_30 < 0 else '#888'
    delta_border = '#2a1a1a' if delta_30 > 0 else '#1a2a1a' if delta_30 < 0 else '#1e1e1e'

    def bar_rows(df_grp, col_name, max_bars=6):
        df_s  = df_grp.groupby(col_name)['Cantidad'].sum().reset_index().sort_values('Cantidad', ascending=False).head(max_bars)
        mx    = int(df_s['Cantidad'].max()) if not df_s.empty else 1
        tot   = int(df_grp['Cantidad'].sum()) if not df_grp.empty else 1
        rows  = ''
        for i, (_, r) in enumerate(df_s.iterrows()):
            pct     = int(r['Cantidad'] / mx * 100)
            pct_tot = round(r['Cantidad'] / tot * 100, 1)
            color   = '#e8b963' if i == 0 else '#fff'
            lclr    = '#e8b963' if i == 0 else '#ccc'
            rows += (
                f'<div style="margin-bottom:16px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:7px;">'
                f'<span style="font-size:1.6rem;color:{lclr};">{r[col_name]}</span>'
                f'<div style="display:flex;gap:22px;align-items:baseline;">'
                f'<span style="font-size:1.25rem;color:#444;">{pct_tot}%</span>'
                f'<span style="font-weight:900;font-size:1.9rem;color:{color};min-width:90px;text-align:right;">{int(r["Cantidad"]):,}</span>'
                f'</div></div>'
                f'<div style="background:#1a1a1a;height:11px;border-radius:2px;">'
                f'<div style="background:{color};height:11px;border-radius:2px;width:{pct}%;"></div></div></div>'
            )
        return rows

    fam_rows  = bar_rows(df_art, 'Familia',   7)
    tipo_rows = bar_rows(df_art, 'Tipo',      5)
    cate_rows = bar_rows(df_art, 'Categoria', 5)

    # ── Locales ────────────────────────────────────────────────────────────
    loc = {}
    for local in ['LURO', 'PERALTA']:
        d = int(df_art[df_art['Local'] == local]['Cantidad'].sum())
        e = int(df_env[df_env['Local'] == local]['Cantidad'].sum()) if not df_env.empty else 0
        v = int(df_vta[df_vta['Local'] == local]['Cantidad'].sum()) if not df_vta.empty else 0
        m = int(df_art[df_art['Local'] == local]['Codigo'].nunique())
        loc[local] = {'dev': d, 'env': e, 'vta': v, 'mod': m,
                      'te': round(d/e*100, 1) if e > 0 else 0,
                      'tv': round(d/v*100, 1) if v > 0 else 0}
    worse_local  = 'LURO' if loc['LURO']['te'] >= loc['PERALTA']['te'] else 'PERALTA'
    diff_te      = round(abs(loc['LURO']['te'] - loc['PERALTA']['te']), 1)

    # ── Remitos última semana ──────────────────────────────────────────────
    sem_total_prendas = sum(r['q'] for r in lista_semana[:8])
    sem_rows = ''
    for i, r in enumerate(lista_semana[:8]):
        is_top  = i == 0
        pct_sem = round(r['q'] / sem_total_prendas * 100) if sem_total_prendas > 0 else 0
        sem_rows += (
            f'<div style="display:grid;grid-template-columns:44px 130px 1fr 140px 70px 130px;'
            f'align-items:center;gap:20px;padding:13px 0;border-bottom:1px solid #161616;">'
            f'<div style="font-size:1.8rem;font-weight:900;text-align:center;'
            f'color:{"#e8b963" if is_top else "#3a3a3a"};">{i+1}</div>'
            f'<div style="background:{"#e8b963" if is_top else "#111"};color:{"#000" if is_top else "#666"};'
            f'padding:5px 12px;font-weight:900;font-size:1.3rem;letter-spacing:3px;text-align:center;">R {r["r"]}</div>'
            f'<div style="font-size:1.5rem;color:#777;">{r["f"]} &nbsp;·&nbsp; '
            f'<span style="color:{"#e8b963" if is_top else "#bbb"};font-weight:900;">{r["l"]}</span></div>'
            f'<div style="font-size:1.3rem;color:#444;text-align:right;">{r["m"]} modelos</div>'
            f'<div style="font-size:1.4rem;color:#444;text-align:right;">{pct_sem}%</div>'
            f'<div style="font-size:2rem;font-weight:900;text-align:right;'
            f'color:{"#e8b963" if is_top else "#fff"};">{r["q"]:,}</div>'
            f'</div>'
        )

    N_SLIDES = 8
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MARKET | Dashboard</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
:root{{--accent:#e8b963;}}
html,body{{width:100%;height:100%;background:#000;color:#fff;
    font-family:'Arial Black','Arial',sans-serif;overflow:hidden;}}
.slide{{position:absolute;inset:0;opacity:0;transition:opacity .6s ease;
    display:flex;flex-direction:column;padding:72px 60px 44px;pointer-events:none;}}
.slide.active{{opacity:1;pointer-events:all;}}
.slide-title{{font-size:1.3rem;font-weight:900;letter-spacing:5px;color:#666;
    text-transform:uppercase;margin-bottom:24px;padding:0 0 12px 16px;
    border-bottom:1px solid #1a1a1a;border-left:3px solid var(--accent);flex-shrink:0;}}
.kpi-giant{{font-size:clamp(4rem,9vw,7rem);font-weight:900;line-height:1;}}
.accent{{color:var(--accent);}}
.divider{{width:40px;height:3px;background:var(--accent);margin:14px 0 18px;}}
.stat-mini{{padding:16px 20px;border:1px solid #1e1e1e;flex:1;}}
.stat-mini .num{{font-size:2.4rem;font-weight:900;}}
.stat-mini .lbl{{font-size:1.1rem;letter-spacing:3px;color:#555;text-transform:uppercase;margin-top:6px;}}
.progress-wrap{{position:fixed;bottom:0;left:0;right:0;height:3px;background:#0a0a0a;z-index:100;}}
.progress-bar{{height:3px;background:var(--accent);width:0%;transition:width linear;}}
.dot-nav{{position:fixed;bottom:12px;right:20px;display:flex;gap:7px;z-index:100;}}
.dot{{width:6px;height:6px;border-radius:50%;background:#252525;cursor:pointer;transition:.2s;}}
.dot.active{{background:var(--accent);}}
.lbl-sm{{font-size:1.3rem;letter-spacing:4px;color:#555;text-transform:uppercase;margin-bottom:10px;}}
.val-mid{{font-size:clamp(1.8rem,3.5vw,2.8rem);font-weight:900;}}
.sep{{border-left:1px solid #1a1a1a;padding-left:32px;}}
</style>
</head>
<body>

<header style="position:fixed;top:0;left:0;right:0;height:52px;background:#000;
    border-bottom:1px solid #111;display:flex;align-items:center;
    padding:0 52px;z-index:200;gap:20px;">
    <span style="font-size:1.3rem;font-weight:900;letter-spacing:14px;">MARKET</span>
    <span style="color:#1e1e1e;font-size:1.2rem;">|</span>
    <span id="hdrTitle" style="font-size:1.1rem;letter-spacing:4px;color:#444;text-transform:uppercase;flex:1;"></span>
    <button id="btnPause" onclick="togglePause()"
        style="border:1px solid #1e1e1e;background:transparent;color:#444;
               font-family:inherit;font-size:1.1rem;letter-spacing:2px;padding:5px 16px;cursor:pointer;">
        PAUSA
    </button>
    <span style="font-size:.9rem;letter-spacing:2px;color:#2a2a2a;margin-left:14px;">{fecha_gen}</span>
</header>

<!-- S0: RESUMEN EJECUTIVO -->
<div class="slide active" id="s0">
    <div class="slide-title">Auditoria Logistica Inversa — Resumen Ejecutivo</div>
    <div style="display:grid;grid-template-columns:2fr 1.1fr 1.1fr;gap:48px;flex:1;align-items:center;">
        <div>
            <div class="lbl-sm">PRENDAS DEVUELTAS · TOTAL ACUMULADO</div>
            <div class="kpi-giant accent">{total_dev:,}</div>
            <div class="divider"></div>
            <div style="font-size:1.4rem;color:#555;">Recibidas y aprobadas en Central</div>
            <div style="margin-top:26px;display:flex;gap:0;">
                <div style="padding:14px 22px;background:#080808;border:1px solid #1a1a1a;border-right:none;">
                    <div style="font-size:1.1rem;color:#444;letter-spacing:3px;">{anio_prev}</div>
                    <div style="font-size:2.2rem;font-weight:900;color:#555;">{dev_prev:,}</div>
                </div>
                <div style="padding:14px 22px;background:#0d0d0d;border:1px solid #1a1a1a;">
                    <div style="font-size:1.1rem;color:#444;letter-spacing:3px;">{anio_act}</div>
                    <div style="font-size:2.2rem;font-weight:900;color:var(--accent);">{dev_act:,}</div>
                </div>
                <div style="padding:14px 22px;background:#060606;border:1px solid #1a1a1a;border-left:none;">
                    <div style="font-size:1.1rem;color:#444;letter-spacing:3px;">VS AÑO ANT.</div>
                    <div style="font-size:2.2rem;font-weight:900;color:{yoy_color};">{yoy_sign}{delta_yoy}%</div>
                </div>
            </div>
        </div>
        <div class="sep" style="display:flex;flex-direction:column;gap:26px;">
            <div>
                <div class="lbl-sm">ENVIADAS</div>
                <div class="val-mid" style="color:#666;">{total_env:,}</div>
            </div>
            <div>
                <div class="lbl-sm">VENDIDAS</div>
                <div class="val-mid" style="color:#666;">{total_vta:,}</div>
            </div>
            <div>
                <div class="lbl-sm">MODELOS DISTINTOS</div>
                <div class="val-mid" style="color:#666;">{modelos:,}</div>
            </div>
            <div>
                <div class="lbl-sm">TEMP. LÍDER</div>
                <div class="val-mid" style="color:var(--accent);">{top_temp}</div>
                <div style="font-size:1.2rem;color:#444;margin-top:4px;">{top_temp_qty:,} prendas</div>
            </div>
        </div>
        <div class="sep" style="display:flex;flex-direction:column;gap:26px;">
            <div>
                <div class="lbl-sm">TASA DEV / ENV</div>
                <div style="font-size:clamp(2.2rem,5vw,3.8rem);font-weight:900;">{tasa_env}%</div>
                <div style="font-size:1.2rem;color:#444;margin-top:4px;">de cada 100 enviadas</div>
            </div>
            <div>
                <div class="lbl-sm">TASA DEV / VTA</div>
                <div style="font-size:clamp(2.2rem,5vw,3.8rem);font-weight:900;">{tasa_vta}%</div>
                <div style="font-size:1.2rem;color:#444;margin-top:4px;">de cada 100 vendidas</div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:4px;">
                <div style="padding:12px 14px;background:#080808;border:1px solid #1a1a1a;">
                    <div style="font-size:1.1rem;color:#444;letter-spacing:3px;">LURO</div>
                    <div style="font-size:2rem;font-weight:900;color:{"var(--accent)" if worse_local=="LURO" else "#fff"};">{loc['LURO']['te']}%</div>
                </div>
                <div style="padding:12px 14px;background:#080808;border:1px solid #1a1a1a;">
                    <div style="font-size:1.1rem;color:#444;letter-spacing:3px;">PERALTA</div>
                    <div style="font-size:2rem;font-weight:900;color:{"var(--accent)" if worse_local=="PERALTA" else "#fff"};">{loc['PERALTA']['te']}%</div>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- S1: DEV vs VENTAS -->
<div class="slide" id="s1">
    <div class="slide-title">Devolucion vs Ventas y Envios</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:72px;flex:1;align-items:center;">
        <div>
            <div class="lbl-sm">DE CADA 100 VENDIDAS</div>
            <div style="font-size:clamp(5rem,14vw,9rem);font-weight:900;line-height:1;" class="accent">{tasa_vta}%</div>
            <div class="divider"></div>
            <div style="font-size:1.5rem;color:#555;line-height:1.9;">volvieron a Central</div>
            <div style="margin-top:22px;padding:16px 20px;background:#080808;border-left:3px solid var(--accent);">
                <span style="font-size:1.4rem;color:#555;">Por cada 100 enviadas: </span>
                <span style="font-size:1.9rem;font-weight:900;color:var(--accent);">{tasa_env}%</span>
                <span style="font-size:1.4rem;color:#555;"> retornaron</span>
            </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:22px;">
            <div>
                <div style="display:flex;justify-content:space-between;margin-bottom:9px;">
                    <span style="font-size:1.5rem;color:#666;letter-spacing:2px;">VENDIDAS</span>
                    <span style="font-size:1.6rem;font-weight:900;color:#555;">{total_vta:,}</span>
                </div>
                <div style="background:#1a1a1a;height:22px;border-radius:3px;">
                    <div style="width:100%;height:22px;background:#2a2a2a;border-radius:3px;"></div>
                </div>
            </div>
            <div>
                <div style="display:flex;justify-content:space-between;margin-bottom:9px;">
                    <span style="font-size:1.5rem;color:#666;letter-spacing:2px;">ENVIADAS</span>
                    <span style="font-size:1.6rem;font-weight:900;color:#555;">{total_env:,}</span>
                </div>
                <div style="background:#1a1a1a;height:22px;border-radius:3px;">
                    <div style="width:{min(int(total_env/max(total_vta,1)*100),100)}%;height:22px;background:#383838;border-radius:3px;min-width:4px;"></div>
                </div>
            </div>
            <div>
                <div style="display:flex;justify-content:space-between;margin-bottom:9px;">
                    <span style="font-size:1.5rem;letter-spacing:2px;color:var(--accent);">DEVUELTAS</span>
                    <span style="font-size:1.6rem;font-weight:900;color:var(--accent);">{total_dev:,}</span>
                </div>
                <div style="background:#1a1a1a;height:22px;border-radius:3px;">
                    <div style="width:{min(int(total_dev/max(total_vta,1)*100),100)}%;height:22px;background:var(--accent);border-radius:3px;min-width:4px;"></div>
                </div>
            </div>
            <div style="margin-top:10px;display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                <div style="padding:14px 18px;background:#080808;border:1px solid #1a1a1a;">
                    <div style="font-size:1.1rem;color:#444;letter-spacing:3px;">LURO · DEV/ENV</div>
                    <div style="font-size:2.2rem;font-weight:900;color:{"var(--accent)" if worse_local=="LURO" else "#fff"};">{loc['LURO']['te']}%</div>
                </div>
                <div style="padding:14px 18px;background:#080808;border:1px solid #1a1a1a;">
                    <div style="font-size:1.1rem;color:#444;letter-spacing:3px;">PERALTA · DEV/ENV</div>
                    <div style="font-size:2.2rem;font-weight:900;color:{"var(--accent)" if worse_local=="PERALTA" else "#fff"};">{loc['PERALTA']['te']}%</div>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- S2: TENDENCIA -->
<div class="slide" id="s2">
    <div class="slide-title">Tendencia — Ultimos 90 Dias</div>
    <div style="flex:1;display:flex;flex-direction:column;min-height:0;gap:16px;">
        <div style="flex:1;min-height:0;">{trend_svg}</div>
        <div style="display:flex;gap:12px;flex-shrink:0;">
            <div class="stat-mini">
                <div class="num accent">{total_90:,}</div>
                <div class="lbl">Total 90 días</div>
            </div>
            <div class="stat-mini">
                <div class="num">{last30:,}</div>
                <div class="lbl">Últ. 30 días</div>
            </div>
            <div class="stat-mini">
                <div class="num">{max_90:,}</div>
                <div class="lbl">Máximo diario</div>
            </div>
            <div class="stat-mini">
                <div class="num">{avg_90:,.0f}</div>
                <div class="lbl">Promedio / día</div>
            </div>
            <div class="stat-mini" style="border-color:{delta_border};">
                <div class="num" style="color:{delta_color};">{delta_sign}{delta_30}%</div>
                <div class="lbl">30d vs ant 30d</div>
            </div>
        </div>
    </div>
</div>

<!-- S3: RANKING FAMILIA -->
<div class="slide" id="s3">
    <div class="slide-title">Ranking por Familia de Articulo</div>
    <div style="flex:1;overflow:hidden;display:flex;flex-direction:column;justify-content:center;">
        {fam_rows}
    </div>
</div>

<!-- S4: TIPO / CATEGORIA -->
<div class="slide" id="s4">
    <div class="slide-title">Por Tipo de Articulo y Categoria</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:72px;flex:1;overflow:hidden;align-content:start;padding-top:4px;">
        <div>
            <div style="font-size:1.4rem;letter-spacing:4px;color:#555;margin-bottom:20px;
                padding-left:12px;border-left:3px solid var(--accent);">TIPO DE ARTICULO</div>
            {tipo_rows}
        </div>
        <div>
            <div style="font-size:1.4rem;letter-spacing:4px;color:#555;margin-bottom:20px;
                padding-left:12px;border-left:3px solid #2a2a2a;">CATEGORIA</div>
            {cate_rows}
        </div>
    </div>
</div>

<!-- S5: LOCALES -->
<div class="slide" id="s5">
    <div class="slide-title">Comparacion entre Locales</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:3px;flex:1;min-height:0;">
        <div style="background:#040404;padding:32px 44px;display:flex;flex-direction:column;justify-content:center;">
            <div style="display:flex;align-items:center;gap:16px;margin-bottom:20px;">
                <div style="font-size:2.8rem;font-weight:900;letter-spacing:8px;">LURO</div>
                {'<div style="font-size:1.3rem;letter-spacing:2px;color:#ef5350;border:1px solid #3a1a1a;padding:3px 10px;">MAYOR TASA</div>' if worse_local == 'LURO' else ''}
            </div>
            <div style="margin-bottom:20px;padding-bottom:20px;border-bottom:1px solid #1a1a1a;">
                <div style="font-size:1.4rem;letter-spacing:3px;color:#666;margin-bottom:6px;">TASA DEV / ENV</div>
                <div style="font-size:clamp(2.5rem,6vw,4.5rem);font-weight:900;color:{"var(--accent)" if worse_local=="LURO" else "#fff"};">{loc['LURO']['te']}%</div>
                <div style="font-size:1.3rem;color:#555;margin-top:6px;">DEV/VTA: <span style="color:#777;font-weight:900;">{loc['LURO']['tv']}%</span></div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                <div><div style="font-size:1.2rem;letter-spacing:2px;color:#444;">DEVUELTAS</div><div style="font-size:2.1rem;font-weight:900;">{loc['LURO']['dev']:,}</div></div>
                <div><div style="font-size:1.2rem;letter-spacing:2px;color:#444;">ENVIADAS</div><div style="font-size:2.1rem;font-weight:900;color:#555;">{loc['LURO']['env']:,}</div></div>
                <div><div style="font-size:1.2rem;letter-spacing:2px;color:#444;">VENDIDAS</div><div style="font-size:2.1rem;font-weight:900;color:#555;">{loc['LURO']['vta']:,}</div></div>
                <div><div style="font-size:1.2rem;letter-spacing:2px;color:#444;">MODELOS</div><div style="font-size:2.1rem;font-weight:900;color:#666;">{loc['LURO']['mod']:,}</div></div>
            </div>
        </div>
        <div style="background:#060606;padding:32px 44px;display:flex;flex-direction:column;justify-content:center;">
            <div style="display:flex;align-items:center;gap:16px;margin-bottom:20px;">
                <div style="font-size:2.8rem;font-weight:900;letter-spacing:8px;">PERALTA</div>
                {'<div style="font-size:1.3rem;letter-spacing:2px;color:#ef5350;border:1px solid #3a1a1a;padding:3px 10px;">MAYOR TASA</div>' if worse_local == 'PERALTA' else ''}
            </div>
            <div style="margin-bottom:20px;padding-bottom:20px;border-bottom:1px solid #1a1a1a;">
                <div style="font-size:1.4rem;letter-spacing:3px;color:#666;margin-bottom:6px;">TASA DEV / ENV</div>
                <div style="font-size:clamp(2.5rem,6vw,4.5rem);font-weight:900;color:{"var(--accent)" if worse_local=="PERALTA" else "#fff"};">{loc['PERALTA']['te']}%</div>
                <div style="font-size:1.3rem;color:#555;margin-top:6px;">DEV/VTA: <span style="color:#777;font-weight:900;">{loc['PERALTA']['tv']}%</span></div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                <div><div style="font-size:1.2rem;letter-spacing:2px;color:#444;">DEVUELTAS</div><div style="font-size:2.1rem;font-weight:900;">{loc['PERALTA']['dev']:,}</div></div>
                <div><div style="font-size:1.2rem;letter-spacing:2px;color:#444;">ENVIADAS</div><div style="font-size:2.1rem;font-weight:900;color:#555;">{loc['PERALTA']['env']:,}</div></div>
                <div><div style="font-size:1.2rem;letter-spacing:2px;color:#444;">VENDIDAS</div><div style="font-size:2.1rem;font-weight:900;color:#555;">{loc['PERALTA']['vta']:,}</div></div>
                <div><div style="font-size:1.2rem;letter-spacing:2px;color:#444;">MODELOS</div><div style="font-size:2.1rem;font-weight:900;color:#666;">{loc['PERALTA']['mod']:,}</div></div>
            </div>
        </div>
    </div>
    <div style="display:flex;align-items:center;gap:14px;padding:12px 0 0;flex-shrink:0;">
        <span style="font-size:1.3rem;color:#333;letter-spacing:2px;">DIFERENCIA ENTRE LOCALES:</span>
        <span style="font-size:1.6rem;font-weight:900;color:var(--accent);">{diff_te} pp</span>
        <span style="font-size:1.3rem;color:#333;">en tasa DEV/ENV &nbsp;·&nbsp; {worse_local} lidera el ranking negativo</span>
    </div>
</div>

<!-- S6: REMITOS -->
<div class="slide" id="s6">
    <div class="slide-title">Remitos de la Ultima Semana</div>
    <div style="display:flex;align-items:baseline;gap:28px;margin-bottom:16px;flex-shrink:0;">
        <span style="font-size:1.4rem;color:#444;">{len(lista_semana[:8])} remitos ingresados</span>
        <span style="font-size:1.4rem;color:#333;">·</span>
        <span style="font-size:2rem;font-weight:900;color:var(--accent);">{sem_total_prendas:,}</span>
        <span style="font-size:1.4rem;color:#444;">prendas en total</span>
    </div>
    <div style="display:grid;grid-template-columns:44px 130px 1fr 140px 70px 130px;
        gap:20px;padding-bottom:10px;border-bottom:1px solid #1e1e1e;margin-bottom:2px;flex-shrink:0;">
        <div style="font-size:1.1rem;letter-spacing:3px;color:#2a2a2a;">#</div>
        <div style="font-size:1.1rem;letter-spacing:3px;color:#2a2a2a;">REMITO</div>
        <div style="font-size:1.1rem;letter-spacing:3px;color:#2a2a2a;">FECHA · LOCAL</div>
        <div style="font-size:1.1rem;letter-spacing:3px;color:#2a2a2a;text-align:right;">MODELOS</div>
        <div style="font-size:1.1rem;letter-spacing:3px;color:#2a2a2a;text-align:right;">%</div>
        <div style="font-size:1.1rem;letter-spacing:3px;color:#2a2a2a;text-align:right;">PRENDAS</div>
    </div>
    <div style="flex:1;overflow:hidden;">{sem_rows}</div>
</div>

<!-- S7: RELACION TASAS -->
<div class="slide" id="s7">
    <div class="slide-title">Relacion de Tasas — Devolucion por Local</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:3px;flex:1;">
        <div style="background:#040404;padding:36px 52px;display:flex;flex-direction:column;justify-content:center;">
            <div style="font-size:1.4rem;letter-spacing:4px;color:#555;margin-bottom:12px;">DEV / ENVIADO</div>
            <div style="font-size:clamp(4rem,10vw,6.5rem);font-weight:900;line-height:1;" class="accent">{tasa_env}%</div>
            <div style="font-size:1.3rem;color:#444;margin:12px 0 26px;">
                Por cada 100 enviadas, {round(tasa_env)} volvieron a Central
            </div>
            <div style="display:flex;flex-direction:column;gap:18px;">
                <div>
                    <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                        <span style="font-size:1.5rem;color:#bbb;letter-spacing:2px;">LURO</span>
                        <span style="font-weight:900;font-size:1.9rem;">{loc['LURO']['te']}%</span>
                    </div>
                    <div style="background:#1a1a1a;height:12px;border-radius:2px;">
                        <div style="background:var(--accent);height:12px;border-radius:2px;width:{min(int(loc['LURO']['te']/max(loc['LURO']['te'],loc['PERALTA']['te'],0.1)*100),100)}%;"></div>
                    </div>
                </div>
                <div>
                    <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                        <span style="font-size:1.5rem;color:#bbb;letter-spacing:2px;">PERALTA</span>
                        <span style="font-weight:900;font-size:1.9rem;">{loc['PERALTA']['te']}%</span>
                    </div>
                    <div style="background:#1a1a1a;height:12px;border-radius:2px;">
                        <div style="background:#fff;height:12px;border-radius:2px;width:{min(int(loc['PERALTA']['te']/max(loc['LURO']['te'],loc['PERALTA']['te'],0.1)*100),100)}%;"></div>
                    </div>
                </div>
            </div>
        </div>
        <div style="background:#060606;padding:36px 52px;display:flex;flex-direction:column;justify-content:center;">
            <div style="font-size:1.4rem;letter-spacing:4px;color:#555;margin-bottom:12px;">DEV / VENDIDO</div>
            <div style="font-size:clamp(4rem,10vw,6.5rem);font-weight:900;line-height:1;" class="accent">{tasa_vta}%</div>
            <div style="font-size:1.3rem;color:#444;margin:12px 0 26px;">
                Por cada 100 vendidas, {round(tasa_vta)} volvieron a Central
            </div>
            <div style="display:flex;flex-direction:column;gap:18px;">
                <div>
                    <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                        <span style="font-size:1.5rem;color:#bbb;letter-spacing:2px;">LURO</span>
                        <span style="font-weight:900;font-size:1.9rem;">{loc['LURO']['tv']}%</span>
                    </div>
                    <div style="background:#1a1a1a;height:12px;border-radius:2px;">
                        <div style="background:var(--accent);height:12px;border-radius:2px;width:{min(int(loc['LURO']['tv']/max(loc['LURO']['tv'],loc['PERALTA']['tv'],0.1)*100),100)}%;"></div>
                    </div>
                </div>
                <div>
                    <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                        <span style="font-size:1.5rem;color:#bbb;letter-spacing:2px;">PERALTA</span>
                        <span style="font-weight:900;font-size:1.9rem;">{loc['PERALTA']['tv']}%</span>
                    </div>
                    <div style="background:#1a1a1a;height:12px;border-radius:2px;">
                        <div style="background:#fff;height:12px;border-radius:2px;width:{min(int(loc['PERALTA']['tv']/max(loc['LURO']['tv'],loc['PERALTA']['tv'],0.1)*100),100)}%;"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<div class="progress-wrap"><div class="progress-bar" id="pgBar"></div></div>
<div class="dot-nav" id="dotNav"></div>

<script>
const N = {N_SLIDES};
const DURATION = 30000;
const SLIDE_NAMES = ['Resumen Ejecutivo','Devolucion vs Ventas','Tendencia 90 Dias','Ranking por Familia','Por Tipo y Categoria','Comparacion Locales','Remitos Ultima Semana','Relacion de Tasas'];
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
    document.getElementById('hdrTitle').textContent = SLIDE_NAMES[cur];
    clearTimeout(timer);
    startProgress();
    if (!paused) timer = setTimeout(() => goTo(cur + 1), DURATION);
}}

function startProgress() {{
    const bar = document.getElementById('pgBar');
    bar.style.transition = 'none';
    bar.style.width = '0%';
    if (!paused) {{
        requestAnimationFrame(() => requestAnimationFrame(() => {{
            bar.style.transition = `width ${{DURATION}}ms linear`;
            bar.style.width = '100%';
        }}));
    }}
}}

function togglePause() {{
    paused = !paused;
    const btn = document.getElementById('btnPause');
    const bar = document.getElementById('pgBar');
    if (paused) {{
        clearTimeout(timer);
        bar.style.transition = 'none';
        btn.textContent = 'REANUDAR';
        btn.style.color = '#e8b963';
        btn.style.borderColor = '#e8b963';
    }} else {{
        btn.textContent = 'PAUSA';
        btn.style.color = '#444';
        btn.style.borderColor = '#1e1e1e';
        timer = setTimeout(() => goTo(cur + 1), DURATION);
        startProgress();
    }}
}}

document.addEventListener('keydown', e => {{
    if (e.key === 'ArrowRight' || e.key === ' ') {{ e.preventDefault(); goTo(cur + 1); }}
    if (e.key === 'ArrowLeft') {{ e.preventDefault(); goTo(cur - 1); }}
    if (e.key === 'p' || e.key === 'P') togglePause();
}});

goTo(0);
</script>
</body>
</html>"""

    with open(nombre_archivo, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  Dashboard guardado: {nombre_archivo}  ({round(len(html)/1024):.0f} KB)")


def obtener_pendientes():
    """Remitos emitidos por locales hacia Central que Central nunca procesó."""
    print("\n  [P] Remitos pendientes (locales -> Central, sin procesar en MTRANS)...")
    try:
        conn = conectar()

        df_luro = pd.read_sql(f"""
            SELECT CV.FNUMCOMP                                              AS Remito,
                   CAST(CV.FFCH AS DATE)                                   AS Fecha,
                   'LURO'                                                  AS Local,
                   RTRIM(CVD.FART)                                         AS Codigo,
                   MAX(RTRIM(CVD.FTXT))                                    AS Descripcion,
                   ISNULL(MAX(FAM.DESCRIP), 'SIN FAMILIA')                 AS Familia,
                   SUM(CVD.FCANT)                                          AS Cantidad
            FROM {DB_LURO}.Zoologic.COMPROBANTEV CV
            INNER JOIN {DB_LURO}.Zoologic.COMPROBANTEVDET CVD
                ON CV.CODIGO = CVD.CODIGO
            LEFT JOIN {DB_CENTRAL}.Zoologic.MTRANS MT
                ON MT.ORIGNRO = CV.FNUMCOMP
                AND UPPER(RTRIM(LTRIM(MT.ORIGDEST))) = 'LURO'
                AND MT.ORIGLETRA = 'R'
            LEFT JOIN {DB_CENTRAL}.Zoologic.ART ART ON RTRIM(CVD.FART) = ART.ARTCOD
            LEFT JOIN {DB_CENTRAL}.Zoologic.FAMILIA FAM ON FAM.COD = ART.FAMILIA
            WHERE CV.FLETRA = 'R'
              AND CV.ANULADO = 0
              AND UPPER(RTRIM(CV.FCLIENTE)) IN ('CENTRAL', 'CCENTRAL')
              AND MT.CODIGO IS NULL
              AND CVD.FTXT NOT LIKE '%BOLSA%'
              AND LEFT(RTRIM(CVD.FART), 1) NOT IN ('Z', '9')
            GROUP BY CV.FNUMCOMP, CAST(CV.FFCH AS DATE), RTRIM(CVD.FART)
        """, conn)

        df_peralta = pd.read_sql(f"""
            SELECT CV.FNUMCOMP                                             AS Remito,
                   CAST(CV.FFCH AS DATE)                                   AS Fecha,
                   'PERALTA'                                               AS Local,
                   RTRIM(CVD.FART)                                         AS Codigo,
                   MAX(RTRIM(CVD.FTXT))                                    AS Descripcion,
                   ISNULL(MAX(FAM.DESCRIP), 'SIN FAMILIA')                 AS Familia,
                   SUM(CVD.FCANT)                                          AS Cantidad
            FROM {DB_PERALTA}.Zoologic.COMPROBANTEV CV
            INNER JOIN {DB_PERALTA}.Zoologic.COMPROBANTEVDET CVD
                ON CV.CODIGO = CVD.CODIGO
            LEFT JOIN {DB_CENTRAL}.Zoologic.MTRANS MT
                ON MT.ORIGNRO = CV.FNUMCOMP
                AND UPPER(RTRIM(LTRIM(MT.ORIGDEST))) = 'PERALTA'
                AND MT.ORIGLETRA = 'R'
            LEFT JOIN {DB_CENTRAL}.Zoologic.ART ART ON RTRIM(CVD.FART) = ART.ARTCOD
            LEFT JOIN {DB_CENTRAL}.Zoologic.FAMILIA FAM ON FAM.COD = ART.FAMILIA
            WHERE CV.FLETRA = 'R'
              AND CV.ANULADO = 0
              AND UPPER(RTRIM(CV.FCLIENTE)) IN ('CENTRAL', 'CCENTRAL')
              AND MT.CODIGO IS NULL
              AND CVD.FTXT NOT LIKE '%BOLSA%'
              AND LEFT(RTRIM(CVD.FART), 1) NOT IN ('Z', '9')
            GROUP BY CV.FNUMCOMP, CAST(CV.FFCH AS DATE), RTRIM(CVD.FART)
        """, conn)

        conn.close()

        df = pd.concat([df_luro, df_peralta], ignore_index=True)
        if df.empty:
            print("  Sin remitos pendientes.")
            return pd.DataFrame()

        df['Cantidad'] = pd.to_numeric(df['Cantidad'], errors='coerce').fillna(0).round().astype(int)
        df['Fecha']    = df['Fecha'].astype(str).str[:10]
        df['Remito']   = df['Remito'].astype(int)

        total_rem  = df['Remito'].nunique()
        total_pren = int(df['Cantidad'].sum())
        print(f"  Pendientes: {total_rem:,} remitos | {total_pren:,} prendas")
        return df

    except Exception as e:
        print(f"  ERROR pendientes: {e}")
        traceback.print_exc()
        return pd.DataFrame()


def generar_pendientes_html(df, nombre_archivo="pendientes.html"):
    if df is None or df.empty:
        print("  Sin datos de pendientes para generar reporte.")
        return

    hoy       = date.today()
    fecha_gen = hoy.strftime('%d/%m/%Y')

    # ── Resumen por remito ─────────────────────────────────────────────────
    rem_grp = (df.groupby(['Remito', 'Fecha', 'Local'])
               .agg(Prendas=('Cantidad', 'sum'), Modelos=('Codigo', 'nunique'))
               .reset_index())
    rem_grp['Dias'] = rem_grp['Fecha'].apply(
        lambda f: (hoy - date.fromisoformat(f)).days)
    rem_grp = rem_grp.sort_values('Fecha', ascending=False)

    # ── KPIs globales ──────────────────────────────────────────────────────
    total_rem   = int(rem_grp['Remito'].nunique())
    total_pren  = int(rem_grp['Prendas'].sum())
    luro_rem    = int(rem_grp[rem_grp['Local']=='LURO']['Remito'].count())
    luro_pren   = int(rem_grp[rem_grp['Local']=='LURO']['Prendas'].sum())
    peralta_rem = int(rem_grp[rem_grp['Local']=='PERALTA']['Remito'].count())
    peralta_pren= int(rem_grp[rem_grp['Local']=='PERALTA']['Prendas'].sum())
    max_dias    = int(rem_grp['Dias'].max())
    avg_dias    = round(float(rem_grp['Dias'].mean()), 1)

    # ── Top artículos pendientes ───────────────────────────────────────────
    top_art = (df.groupby(['Codigo', 'Descripcion', 'Familia'])['Cantidad']
               .sum().reset_index()
               .sort_values('Cantidad', ascending=False).head(15))

    # ── Datos mensuales para gráfico ──────────────────────────────────────
    rem_grp['Mes'] = rem_grp['Fecha'].str[:7]
    monthly = (rem_grp.groupby(['Mes', 'Local'])
               .agg(Remitos=('Remito','count'), Prendas=('Prendas','sum'))
               .reset_index().sort_values(['Mes','Local']))
    meses      = sorted(monthly['Mes'].unique().tolist())
    luro_rem_m = {r['Mes']: r['Remitos'] for _, r in monthly[monthly['Local']=='LURO'].iterrows()}
    pera_rem_m = {r['Mes']: r['Remitos'] for _, r in monthly[monthly['Local']=='PERALTA'].iterrows()}
    luro_pre_m = {r['Mes']: int(r['Prendas']) for _, r in monthly[monthly['Local']=='LURO'].iterrows()}
    pera_pre_m = {r['Mes']: int(r['Prendas']) for _, r in monthly[monthly['Local']=='PERALTA'].iterrows()}

    chart_labels  = json.dumps(meses)
    chart_luro_r  = json.dumps([luro_rem_m.get(m, 0) for m in meses])
    chart_pera_r  = json.dumps([pera_rem_m.get(m, 0) for m in meses])
    chart_luro_p  = json.dumps([luro_pre_m.get(m, 0) for m in meses])
    chart_pera_p  = json.dumps([pera_pre_m.get(m, 0) for m in meses])

    # ── Detalle por remito (para modal) ───────────────────────────────────
    detail_map = {}
    for _, row in df.iterrows():
        key = (int(row['Remito']), str(row['Local']))
        if key not in detail_map:
            detail_map[key] = []
        detail_map[key].append({
            'c': str(row['Codigo']),
            'n': str(row['Descripcion']),
            'f': str(row['Familia']),
            'q': int(row['Cantidad'])
        })
    detail_json = json.dumps(
        {f"{k[0]}_{k[1]}": v for k, v in detail_map.items()},
        ensure_ascii=False
    )

    # ── Filas de la tabla principal ────────────────────────────────────────
    def dias_badge(d):
        if d > 180: color, bg = '#ef5350', '#1a0505'
        elif d > 60: color, bg = '#ffa726', '#1a1000'
        else: color, bg = '#66bb6a', '#051a05'
        return f'<span style="background:{bg};color:{color};padding:2px 10px;font-size:.8rem;border-radius:2px;font-weight:900;">{d}d</span>'

    tabla_rows = ''
    for _, r in rem_grp.iterrows():
        local_color = '#e8b963' if r['Local'] == 'LURO' else '#64b5f6'
        key_js = f'{int(r["Remito"])}_{r["Local"]}'
        tabla_rows += f'''<tr data-local="{r['Local']}" data-key="{key_js}"
            data-remito="{int(r['Remito'])}" data-fecha="{r['Fecha']}" data-dias="{int(r['Dias'])}"
            data-modelos="{int(r['Modelos'])}" data-prendas="{int(r['Prendas'])}"
            style="border-bottom:1px solid #111;cursor:pointer;" onclick="verDetalle('{key_js}','{int(r['Remito'])}','{r['Fecha']}','{r['Local']}')">
            <td style="padding:10px 12px;font-weight:900;font-size:.95rem;">{int(r['Remito'])}</td>
            <td style="padding:10px 12px;color:#666;font-size:.9rem;">{r['Fecha']}</td>
            <td style="padding:10px 12px;"><span style="color:{local_color};font-weight:900;font-size:.85rem;letter-spacing:2px;">{r['Local']}</span></td>
            <td style="padding:10px 12px;text-align:right;">{dias_badge(int(r['Dias']))}</td>
            <td style="padding:10px 12px;text-align:right;color:#888;font-size:.9rem;">{int(r['Modelos'])}</td>
            <td style="padding:10px 12px;text-align:right;font-weight:900;">{int(r['Prendas']):,}</td>
        </tr>'''

    # ── Top artículos ──────────────────────────────────────────────────────
    mx_art = int(top_art['Cantidad'].max()) if not top_art.empty else 1
    art_rows = ''
    for i, (_, a) in enumerate(top_art.iterrows()):
        pct = int(a['Cantidad'] / mx_art * 100)
        color = '#e8b963' if i == 0 else '#fff' if i < 3 else '#888'
        art_rows += f'''<div style="margin-bottom:12px;">
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
                <div>
                    <span style="color:{color};font-size:.95rem;">{a['Descripcion'][:45]}</span>
                    <span style="color:#333;font-size:.75rem;margin-left:8px;">{a['Familia']}</span>
                </div>
                <span style="font-weight:900;font-size:1rem;color:{color};min-width:60px;text-align:right;">{int(a['Cantidad']):,}</span>
            </div>
            <div style="background:#1a1a1a;height:6px;border-radius:2px;">
                <div style="background:{color};height:6px;border-radius:2px;width:{pct}%;"></div>
            </div>
        </div>'''

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MARKET | Remitos Pendientes</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {{ --accent:#e8b963; --accent2:#64b5f6; }}
*,*::before,*::after {{ box-sizing:border-box; }}
body {{ background:#060606; color:#ddd; font-family:'Segoe UI',Arial,sans-serif; margin:0; }}
.hdr {{ background:#000; border-bottom:1px solid #1a1a1a; padding:14px 40px;
        display:flex; align-items:center; gap:18px; position:sticky; top:0; z-index:100; }}
.hdr-logo {{ font-family:'Arial Black',Arial,sans-serif; font-size:1.1rem;
             font-weight:900; letter-spacing:12px; color:#fff; }}
.hdr-title {{ font-size:.75rem; letter-spacing:4px; color:#555; text-transform:uppercase; }}
.hdr-date  {{ font-size:.75rem; color:#333; letter-spacing:2px; margin-left:auto; }}
.kpi-bar {{ background:#000; border-bottom:1px solid #111; padding:18px 40px;
            display:flex; gap:0; }}
.kpi {{ padding:14px 28px; border-right:1px solid #111; }}
.kpi:last-child {{ border-right:none; }}
.kpi .n {{ font-size:1.8rem; font-weight:900; line-height:1; }}
.kpi .l {{ font-size:.7rem; letter-spacing:3px; color:#555; text-transform:uppercase; margin-top:4px; }}
.kpi.luro .n  {{ color:var(--accent); }}
.kpi.pera .n  {{ color:var(--accent2); }}
.kpi.alerta .n {{ color:#ef5350; }}
.section {{ padding:32px 40px; }}
.section-title {{ font-size:.7rem; letter-spacing:4px; color:#555; text-transform:uppercase;
                  margin-bottom:20px; padding-bottom:10px; border-bottom:1px solid #111;
                  border-left:3px solid var(--accent); padding-left:12px; }}
.card-dark {{ background:#0c0c0c; border:1px solid #1a1a1a; border-radius:2px; }}
.tog {{ background:transparent; border:1px solid #1e1e1e; color:#555; font-size:.7rem;
        letter-spacing:2px; padding:4px 14px; cursor:pointer; transition:.15s; }}
.tog.on {{ background:#111; color:#fff; border-color:#333; }}
.search-box {{ background:#0a0a0a; border:1px solid #1e1e1e; color:#ddd; font-size:.85rem;
               padding:6px 14px; width:220px; outline:none; }}
table {{ width:100%; border-collapse:collapse; font-size:.9rem; }}
thead th {{ padding:8px 12px; font-size:.65rem; letter-spacing:3px; color:#444;
            text-transform:uppercase; border-bottom:1px solid #1a1a1a; }}
.th-sort {{ cursor:pointer; user-select:none; white-space:nowrap; }}
.th-sort:hover {{ color:#e8b963; }}
.sort-ind {{ color:#e8b963; font-size:.7rem; }}
tbody tr:hover {{ background:#0e0e0e; }}
.modal-content {{ background:#0a0a0a; border:1px solid #222; border-radius:2px; }}
.modal-header {{ border-bottom:1px solid #1a1a1a; padding:16px 20px; }}
.modal-body {{ padding:20px; max-height:70vh; overflow-y:auto; }}
.det-row {{ display:grid; grid-template-columns:90px 1fr 140px 60px;
            gap:12px; align-items:center; padding:8px 0; border-bottom:1px solid #111; font-size:.85rem; }}
.det-hdr {{ color:#444; font-size:.65rem; letter-spacing:3px; }}
</style>
</head>
<body>

<div class="hdr">
    <span class="hdr-logo">MARKET</span>
    <span style="color:#222;font-size:1rem;">|</span>
    <span class="hdr-title">Remitos Pendientes — Sin Procesar en Central</span>
    <span class="hdr-date">{fecha_gen}</span>
    <div style="margin-left:auto;display:flex;gap:10px;">
        <a href="index.html" style="color:#444;text-decoration:none;font-size:.7rem;letter-spacing:2px;font-family:inherit;font-weight:900;padding:5px 10px;border:1px solid #222;">DEVOLUCIONES</a>
        <a href="envios.html" style="color:#444;text-decoration:none;font-size:.7rem;letter-spacing:2px;font-family:inherit;font-weight:900;padding:5px 10px;border:1px solid #222;">ENVÍOS</a>
        <a href="dashboard.html" style="color:#444;text-decoration:none;font-size:.7rem;letter-spacing:2px;font-family:inherit;font-weight:900;padding:5px 10px;border:1px solid #222;">DASHBOARD</a>
    </div>
</div>

<div class="kpi-bar">
    <div class="kpi"><div class="n" style="color:#fff;">{total_rem:,}</div><div class="l">Remitos pendientes</div></div>
    <div class="kpi"><div class="n" style="color:#fff;">{total_pren:,}</div><div class="l">Prendas no procesadas</div></div>
    <div class="kpi luro"><div class="n">{luro_rem:,}</div><div class="l">Remitos · LURO</div></div>
    <div class="kpi" style="padding:14px 28px;border-right:1px solid #111;"><div class="n" style="color:var(--accent);">{luro_pren:,}</div><div class="l">Prendas · LURO</div></div>
    <div class="kpi pera"><div class="n">{peralta_rem:,}</div><div class="l">Remitos · PERALTA</div></div>
    <div class="kpi" style="padding:14px 28px;border-right:1px solid #111;"><div class="n" style="color:var(--accent2);">{peralta_pren:,}</div><div class="l">Prendas · PERALTA</div></div>
    <div class="kpi alerta"><div class="n">{max_dias}</div><div class="l">Días máx. pendiente</div></div>
    <div class="kpi"><div class="n" style="color:#888;">{avg_dias}</div><div class="l">Días promedio</div></div>
</div>

<div class="section">
    <div class="section-title">Evolucion mensual — Remitos sin procesar</div>
    <div class="card-dark" style="padding:24px;">
        <div style="display:flex;gap:20px;margin-bottom:16px;font-size:.75rem;letter-spacing:2px;">
            <span><span style="display:inline-block;width:12px;height:12px;background:var(--accent);margin-right:6px;"></span>LURO</span>
            <span><span style="display:inline-block;width:12px;height:12px;background:var(--accent2);margin-right:6px;"></span>PERALTA</span>
            <button id="btnTogChart" class="tog on" style="margin-left:auto;" onclick="toggleChart()">REMITOS</button>
            <button id="btnTogChart2" class="tog" onclick="toggleChart2()">PRENDAS</button>
        </div>
        <canvas id="chartMensual" height="90"></canvas>
    </div>
</div>

<div class="section" style="padding-top:0;">
    <div class="section-title">Detalle de remitos pendientes</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:40px;">
        <div>
            <div style="display:flex;gap:8px;margin-bottom:16px;align-items:center;flex-wrap:wrap;">
                <button class="tog on" id="fAll"  onclick="filtrar('ALL')">TODOS</button>
                <button class="tog"    id="fLURO"  onclick="filtrar('LURO')">LURO</button>
                <button class="tog"    id="fPERALTA" onclick="filtrar('PERALTA')">PERALTA</button>
                <input class="search-box" id="buscar" placeholder="Buscar remito #..." oninput="buscarRemito(this.value)" style="margin-left:auto;">
            </div>
            <div class="card-dark" style="overflow:hidden;">
                <table>
                    <thead><tr>
                        <th class="th-sort" onclick="sortTable('remito','num')">REMITO <span class="sort-ind" id="sort-remito"></span></th>
                        <th class="th-sort" onclick="sortTable('fecha','str')">FECHA <span class="sort-ind" id="sort-fecha">▼</span></th>
                        <th class="th-sort" onclick="sortTable('local','str')">LOCAL <span class="sort-ind" id="sort-local"></span></th>
                        <th class="th-sort" style="text-align:right;" onclick="sortTable('dias','num')">ANTIGÜEDAD <span class="sort-ind" id="sort-dias"></span></th>
                        <th class="th-sort" style="text-align:right;" onclick="sortTable('modelos','num')">MODELOS <span class="sort-ind" id="sort-modelos"></span></th>
                        <th class="th-sort" style="text-align:right;" onclick="sortTable('prendas','num')">PRENDAS <span class="sort-ind" id="sort-prendas"></span></th>
                    </tr></thead>
                    <tbody id="tablaBody">{tabla_rows}</tbody>
                </table>
                <div id="sinResultados" style="display:none;padding:24px;text-align:center;color:#333;font-size:.8rem;letter-spacing:3px;">SIN RESULTADOS</div>
            </div>
            <div id="paginacion" style="display:flex;gap:6px;margin-top:12px;justify-content:center;"></div>
        </div>
        <div>
            <div class="section-title" style="margin-bottom:16px;">Top artículos sin procesar</div>
            <div class="card-dark" style="padding:20px 24px;">
                {art_rows}
            </div>
        </div>
    </div>
</div>

<!-- Modal detalle remito -->
<div class="modal fade" id="modalDet" tabindex="-1">
    <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
        <div class="modal-content">
            <div class="modal-header">
                <div>
                    <div style="font-size:.65rem;letter-spacing:3px;color:#555;">DETALLE DE REMITO</div>
                    <div id="modalTitulo" style="font-size:1.1rem;font-weight:900;margin-top:4px;"></div>
                </div>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <div class="det-row det-hdr">
                    <div>CÓDIGO</div><div>DESCRIPCIÓN</div><div>FAMILIA</div><div style="text-align:right;">CANT.</div>
                </div>
                <div id="modalDetalle"></div>
            </div>
        </div>
    </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
const DETAIL = {detail_json};
const LABELS  = {chart_labels};
const LURO_R  = {chart_luro_r};
const PERA_R  = {chart_pera_r};
const LURO_P  = {chart_luro_p};
const PERA_P  = {chart_pera_p};

// ── Gráfico ────────────────────────────────────────────────────────────────
let showPrendas = false;
const ctx = document.getElementById('chartMensual').getContext('2d');
const chart = new Chart(ctx, {{
    type: 'bar',
    data: {{
        labels: LABELS,
        datasets: [
            {{ label:'LURO',    data: LURO_R, backgroundColor:'rgba(232,185,99,0.85)', borderRadius:2 }},
            {{ label:'PERALTA', data: PERA_R, backgroundColor:'rgba(100,181,246,0.75)', borderRadius:2 }}
        ]
    }},
    options: {{
        responsive:true, plugins:{{ legend:{{display:false}} }},
        scales:{{ x:{{ grid:{{color:'#111'}}, ticks:{{color:'#555'}} }},
                  y:{{ grid:{{color:'#111'}}, ticks:{{color:'#555'}} }} }}
    }}
}});

function toggleChart() {{
    showPrendas = false;
    chart.data.datasets[0].data = LURO_R;
    chart.data.datasets[1].data = PERA_R;
    chart.update();
    document.getElementById('btnTogChart').classList.add('on');
    document.getElementById('btnTogChart2').classList.remove('on');
}}
function toggleChart2() {{
    showPrendas = true;
    chart.data.datasets[0].data = LURO_P;
    chart.data.datasets[1].data = PERA_P;
    chart.update();
    document.getElementById('btnTogChart2').classList.add('on');
    document.getElementById('btnTogChart').classList.remove('on');
}}

// ── Tabla + filtros + paginación ───────────────────────────────────────────
const PAGE_SIZE = 20;
let filtroLocal = 'ALL';
let filtroBusq  = '';
let pagina = 1;
let modalBS = null;

function getFilas() {{
    return Array.from(document.querySelectorAll('#tablaBody tr'));
}}

function aplicarFiltros() {{
    const filas = getFilas();
    let vis = filas.filter(f => {{
        const loc = f.dataset.local || '';
        const rem = f.querySelector('td')?.textContent || '';
        const okL = filtroLocal === 'ALL' || loc === filtroLocal;
        const okB = filtroBusq  === '' || rem.includes(filtroBusq);
        return okL && okB;
    }});
    filas.forEach(f => f.style.display = 'none');
    const total = vis.length;
    const desde = (pagina - 1) * PAGE_SIZE;
    vis.slice(desde, desde + PAGE_SIZE).forEach(f => f.style.display = '');
    document.getElementById('sinResultados').style.display = total === 0 ? '' : 'none';
    renderPaginas(total);
}}

function renderPaginas(total) {{
    const pages = Math.ceil(total / PAGE_SIZE);
    const nav = document.getElementById('paginacion');
    nav.innerHTML = '';
    if (pages <= 1) return;
    for (let i = 1; i <= pages; i++) {{
        const b = document.createElement('button');
        b.textContent = i;
        b.className = 'tog' + (i === pagina ? ' on' : '');
        b.style.cssText = 'min-width:32px;padding:3px 8px;font-size:.75rem;';
        b.onclick = () => {{ pagina = i; aplicarFiltros(); }};
        nav.appendChild(b);
    }}
}}

function filtrar(local) {{
    filtroLocal = local;
    pagina = 1;
    ['fAll','fLURO','fPERALTA'].forEach(id => document.getElementById(id)?.classList.remove('on'));
    document.getElementById(local === 'ALL' ? 'fAll' : 'f' + local)?.classList.add('on');
    aplicarFiltros();
}}

function buscarRemito(val) {{
    filtroBusq = val.trim();
    pagina = 1;
    aplicarFiltros();
}}

let sortCol = 'fecha', sortDir = -1;
function sortTable(col, type) {{
    if (sortCol === col) sortDir *= -1; else {{ sortCol = col; sortDir = -1; }}
    const tbody = document.getElementById('tablaBody');
    Array.from(tbody.querySelectorAll('tr'))
        .sort((a, b) => {{
            let va = a.dataset[col] ?? '', vb = b.dataset[col] ?? '';
            if (type === 'num') {{ va = parseFloat(va)||0; vb = parseFloat(vb)||0; }}
            return va < vb ? sortDir : va > vb ? -sortDir : 0;
        }})
        .forEach(r => tbody.appendChild(r));
    pagina = 1;
    aplicarFiltros();
    document.querySelectorAll('.sort-ind').forEach(s => s.textContent = '');
    const ind = document.getElementById('sort-' + col);
    if (ind) ind.textContent = sortDir > 0 ? '▲' : '▼';
}}

function verDetalle(key, remito, fecha, local) {{
    const items = DETAIL[key] || [];
    document.getElementById('modalTitulo').innerHTML =
        `Remito <span style="color:var(--accent);">R${{remito}}</span>` +
        ` &nbsp;·&nbsp; ${{fecha}}` +
        ` &nbsp;·&nbsp; <span style="color:${{local==='LURO'?'var(--accent)':'var(--accent2)'}};">${{local}}</span>`;
    const body = document.getElementById('modalDetalle');
    if (!items.length) {{ body.innerHTML = '<div style="color:#555;padding:20px;">Sin detalle disponible</div>'; }}
    else {{
        body.innerHTML = items.sort((a,b)=>b.q-a.q).map(it => `
            <div class="det-row">
                <div style="color:#555;font-size:.8rem;">${{it.c}}</div>
                <div>${{it.n}}</div>
                <div style="color:#444;font-size:.8rem;">${{it.f}}</div>
                <div style="text-align:right;font-weight:900;">${{it.q.toLocaleString()}}</div>
            </div>`).join('');
    }}
    if (!modalBS) modalBS = new bootstrap.Modal(document.getElementById('modalDet'));
    modalBS.show();
}}

aplicarFiltros();
</script>
</body>
</html>"""

    with open(nombre_archivo, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  Pendientes guardado: {nombre_archivo}  ({round(len(html)/1024):.0f} KB)")


def generar_envios_html(resultado, nombre_archivo="envios.html"):
    if resultado is None:
        print("Sin datos para generar envios.")
        return

    df_art, df_chart, df_env, remitos_art, lista_semana, df_vta, df_env_chart, df_vta_chart = resultado

    cols = ['Codigo', 'Descripcion', 'Familia', 'Tipo', 'Categoria', 'Temporada', 'Local', 'Anio']

    e = df_env.rename(columns={'Cantidad': 'Enviado'}) if not df_env.empty else pd.DataFrame(columns=cols + ['Enviado'])
    d = df_art[cols + ['Cantidad']].rename(columns={'Cantidad': 'Devuelto'}) if not df_art.empty else pd.DataFrame(columns=cols + ['Devuelto'])
    v = df_vta[cols + ['Cantidad']].rename(columns={'Cantidad': 'Vendido'}) if not df_vta.empty else pd.DataFrame(columns=cols + ['Vendido'])

    tri = (
        e.merge(d, on=cols, how='outer')
         .merge(v, on=cols, how='outer')
    )
    for c in ['Enviado', 'Devuelto', 'Vendido']:
        tri[c] = pd.to_numeric(tri[c], errors='coerce').fillna(0).round().astype(int)
    tri['EnLocal'] = (tri['Enviado'] - tri['Vendido'] - tri['Devuelto']).clip(lower=0).astype(int)

    # Solo artículos que tuvieron algún envío registrado
    tri_env = tri[tri['Enviado'] > 0].copy()

    total_env = int(tri_env['Enviado'].sum())
    total_vta = int(tri_env['Vendido'].sum())
    total_dev = int(tri_env['Devuelto'].sum())
    total_stk = int(tri_env['EnLocal'].sum())
    pct_v = round(total_vta / total_env * 100, 1) if total_env else 0
    pct_d = round(total_dev / total_env * 100, 1) if total_env else 0
    pct_s = round(total_stk / total_env * 100, 1) if total_env else 0

    # By family
    fam = (
        tri_env.groupby('Familia')
        .agg(Enviado=('Enviado', 'sum'), Vendido=('Vendido', 'sum'),
             Devuelto=('Devuelto', 'sum'), EnLocal=('EnLocal', 'sum'))
        .reset_index().sort_values('Enviado', ascending=False).head(12)
    )
    fam['PctV'] = (fam['Vendido'] / fam['Enviado'] * 100).round(1).where(fam['Enviado'] > 0, 0)

    # By local
    loc_tri = (
        tri_env.groupby('Local')
        .agg(Enviado=('Enviado', 'sum'), Vendido=('Vendido', 'sum'),
             Devuelto=('Devuelto', 'sum'), EnLocal=('EnLocal', 'sum'))
        .reset_index()
    )
    loc_data = {}
    for _, r in loc_tri.iterrows():
        loc_data[r['Local']] = {
            'env': int(r['Enviado']), 'vta': int(r['Vendido']),
            'dev': int(r['Devuelto']), 'stk': int(r['EnLocal']),
            'pctV': round(r['Vendido'] / r['Enviado'] * 100, 1) if r['Enviado'] else 0,
            'pctD': round(r['Devuelto'] / r['Enviado'] * 100, 1) if r['Enviado'] else 0,
        }

    # Monthly timeline JSON
    meses_env = json.dumps(df_env_chart.to_dict('records'), ensure_ascii=False)
    meses_vta = json.dumps(df_vta_chart.to_dict('records'), ensure_ascii=False)

    # Monthly devoluciones from df_chart
    dev_chart_copy = df_chart.copy()
    dev_chart_copy['AnoMes'] = dev_chart_copy['Fecha'].str[:7]
    dev_monthly = (
        dev_chart_copy.groupby(['AnoMes', 'Local'])['Cantidad']
        .sum().reset_index().sort_values('AnoMes')
    )
    dev_monthly['Cantidad'] = dev_monthly['Cantidad'].astype(int)
    meses_dev = json.dumps(dev_monthly.to_dict('records'), ensure_ascii=False)

    # Main table JSON (limit columns to keep size manageable)
    tri_out = tri_env[['Codigo', 'Descripcion', 'Familia', 'Temporada', 'Local', 'Anio',
                        'Enviado', 'Vendido', 'Devuelto', 'EnLocal']].copy()
    tri_out['PctV'] = (tri_out['Vendido'] / tri_out['Enviado'] * 100).round(1).where(tri_out['Enviado'] > 0, 0)
    tri_out['PctD'] = (tri_out['Devuelto'] / tri_out['Enviado'] * 100).round(1).where(tri_out['Enviado'] > 0, 0)
    data_tri_json = json.dumps(tri_out.to_dict('records'), ensure_ascii=False)

    anios_unicos = sorted(tri_env['Anio'].unique().tolist())
    fams_unicas  = sorted(tri_env['Familia'].unique().tolist())
    temps_unicas = sorted(tri_env['Temporada'].unique().tolist())
    fecha_gen    = date.today().strftime('%d/%m/%Y')

    luro_env  = loc_data.get('LURO',    {'env':0,'vta':0,'dev':0,'stk':0,'pctV':0,'pctD':0})
    pera_env  = loc_data.get('PERALTA', {'env':0,'vta':0,'dev':0,'stk':0,'pctV':0,'pctD':0})

    anios_opts = ''.join(f'<button class="anio-btn" onclick="setAnio({a})">{a}</button>' for a in anios_unicos)
    fam_opts   = '<option value="">Todas las familias</option>' + ''.join(f'<option value="{f}">{f}</option>' for f in fams_unicas)
    temp_opts  = '<option value="">Todas las temporadas</option>' + ''.join(f'<option value="{t}">{t}</option>' for t in temps_unicas)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MARKET | Logística — Triángulo de Envíos</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        :root {{ --accent:#e8b963; --green:#66bb6a; --red:#ef5350; --blue:#64b5f6; }}
        * {{ box-sizing:border-box; margin:0; padding:0; }}
        body {{ background:#0d0d0d; color:#e0e0e0; font-family:'Arial Black','Arial',sans-serif; }}

        /* TOP BAR */
        .top-bar {{ background:#000; border-bottom:3px solid var(--accent);
                    padding:14px 28px; display:flex; align-items:center; gap:24px; flex-wrap:wrap; }}
        .top-title {{ font-size:1.1rem; font-weight:900; letter-spacing:4px;
                      text-transform:uppercase; color:#fff; }}
        .top-sub {{ font-size:.75rem; color:#666; letter-spacing:2px; }}
        .nav-links {{ margin-left:auto; display:flex; gap:12px; }}
        .nav-links a {{ color:#555; text-decoration:none; font-size:.75rem; letter-spacing:2px;
                        text-transform:uppercase; font-weight:900; padding:6px 12px;
                        border:1px solid #222; transition:.15s; }}
        .nav-links a:hover {{ color:var(--accent); border-color:var(--accent); }}

        /* FILTER BAR */
        .filter-bar {{ background:#111; border-bottom:1px solid #1e1e1e;
                       padding:12px 28px; display:flex; gap:24px; align-items:center; flex-wrap:wrap;
                       position:sticky; top:0; z-index:100; }}
        .fg {{ display:flex; flex-direction:column; gap:5px; }}
        .fl {{ font-size:.6rem; letter-spacing:3px; text-transform:uppercase; color:#555; font-weight:900; }}
        .anio-btn {{ border:1px solid #333; background:transparent; color:#666; padding:4px 11px;
                     font-family:inherit; font-weight:900; font-size:.8rem; cursor:pointer; transition:.15s; }}
        .anio-btn.on {{ background:var(--accent); color:#000; border-color:var(--accent); }}
        .tog-btn {{ border:1px solid #333; background:transparent; color:#666; padding:5px 13px;
                    font-family:inherit; font-weight:900; font-size:.8rem; cursor:pointer; transition:.15s; }}
        .tog-btn.on {{ background:#fff; color:#000; border-color:#fff; }}
        select {{ background:#111; border:1px solid #333; color:#888; padding:5px 10px;
                  font-family:inherit; font-weight:900; font-size:.8rem; min-width:170px; }}
        select option {{ background:#111; }}
        .search-box {{ background:#111; border:1px solid #333; color:#ddd; padding:5px 12px;
                       font-family:inherit; font-size:.85rem; min-width:200px; }}
        .search-box::placeholder {{ color:#444; }}

        /* MAIN CONTENT */
        .page {{ padding:28px 28px 60px; max-width:1600px; margin:0 auto; }}

        /* KPI CARDS */
        .kpi-row {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:28px; }}
        .kpi {{ background:#111; border:1px solid #1e1e1e; padding:22px 24px; }}
        .kpi .num {{ font-size:2.6rem; font-weight:900; line-height:1; }}
        .kpi .lbl {{ font-size:.65rem; letter-spacing:3px; text-transform:uppercase; color:#555; margin-top:8px; }}
        .kpi .sub {{ font-size:.9rem; color:#444; margin-top:5px; }}

        /* LOCAL CARDS */
        .local-row {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:28px; }}
        .local-card {{ background:#111; border:1px solid #1e1e1e; padding:22px 24px; }}
        .local-name {{ font-size:1rem; font-weight:900; letter-spacing:4px; margin-bottom:18px; }}
        .local-stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
        .lst-n {{ font-size:1.8rem; font-weight:900; }}
        .lst-l {{ font-size:.6rem; letter-spacing:2px; color:#555; text-transform:uppercase; margin-top:4px; }}

        /* CHARTS */
        .charts-row {{ display:grid; grid-template-columns:1fr 1.6fr; gap:16px; margin-bottom:28px; }}
        .chart-box {{ background:#111; border:1px solid #1e1e1e; padding:22px 24px; }}
        .chart-canvas-wrap {{ position:relative; height:280px; }}
        .chart-title {{ font-size:.7rem; letter-spacing:3px; text-transform:uppercase; color:#555;
                        font-weight:900; margin-bottom:18px; padding-bottom:10px; border-bottom:1px solid #1a1a1a; }}

        /* FAMILY BARS */
        .section {{ background:#111; border:1px solid #1e1e1e; padding:22px 24px; margin-bottom:28px; }}
        .sec-title {{ font-size:.7rem; letter-spacing:3px; text-transform:uppercase; color:#555;
                      font-weight:900; margin-bottom:20px; padding-bottom:10px; border-bottom:1px solid #1a1a1a; }}
        .fam-bar-row {{ margin-bottom:14px; }}
        .fam-bar-labels {{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px; }}
        .fam-bar-name {{ font-size:.85rem; color:#bbb; }}
        .fam-bar-stats {{ font-size:.75rem; color:#555; display:flex; gap:16px; }}
        .fam-bar-track {{ background:#1a1a1a; height:8px; border-radius:2px; display:flex; overflow:hidden; }}

        /* TABLE */
        .tbl-wrap {{ overflow-x:auto; }}
        table {{ width:100%; border-collapse:collapse; font-size:.82rem; }}
        th {{ background:#111; color:#555; font-size:.6rem; letter-spacing:2px;
              text-transform:uppercase; padding:10px 12px; text-align:left;
              border-bottom:2px solid #1a1a1a; cursor:pointer; white-space:nowrap; }}
        th:hover {{ color:var(--accent); }}
        th.sorted {{ color:var(--accent); }}
        td {{ padding:9px 12px; border-bottom:1px solid #111; vertical-align:middle; }}
        tr:hover td {{ background:#111; }}
        .pct-bar {{ display:inline-block; height:4px; border-radius:1px; vertical-align:middle; margin-left:6px; }}
        .badge {{ padding:2px 8px; font-size:.65rem; font-weight:900; border-radius:1px; }}
        .pagination {{ display:flex; gap:8px; align-items:center; justify-content:center;
                       padding:20px 0; color:#555; font-size:.8rem; }}
        .pg-btn {{ border:1px solid #333; background:transparent; color:#666; padding:5px 12px;
                   font-family:inherit; font-weight:900; cursor:pointer; transition:.15s; }}
        .pg-btn:hover {{ color:var(--accent); border-color:var(--accent); }}
        .pg-btn.on {{ background:var(--accent); color:#000; border-color:var(--accent); }}
    </style>
</head>
<body>

<div class="top-bar">
    <div>
        <div class="top-title">MARKET &nbsp;/&nbsp; Triángulo de Envíos</div>
        <div class="top-sub">Enviado → Vendido · Devuelto · En Local &nbsp;·&nbsp; Generado: {fecha_gen}</div>
    </div>
    <div class="nav-links">
        <a href="index.html">Devoluciones</a>
        <a href="pendientes.html">Pendientes</a>
        <a href="dashboard.html">Dashboard</a>
    </div>
</div>

<div class="filter-bar">
    <div class="fg">
        <div class="fl">Año</div>
        <div style="display:flex;gap:6px;">
            <button class="anio-btn on" id="aTODOS" onclick="setAnio(0)">Todos</button>
            {anios_opts}
        </div>
    </div>
    <div class="fg">
        <div class="fl">Local</div>
        <div style="display:flex;gap:6px;">
            <button class="tog-btn on" id="lALL" onclick="setLocal('ALL')">Todos</button>
            <button class="tog-btn" id="lLURO" onclick="setLocal('LURO')">LURO</button>
            <button class="tog-btn" id="lPERALTA" onclick="setLocal('PERALTA')">PERALTA</button>
        </div>
    </div>
    <div class="fg">
        <div class="fl">Familia</div>
        <select onchange="setFam(this.value)">{fam_opts}</select>
    </div>
    <div class="fg">
        <div class="fl">Temporada</div>
        <select onchange="setTemp(this.value)">{temp_opts}</select>
    </div>
    <div class="fg">
        <div class="fl">Buscar</div>
        <input class="search-box" type="text" placeholder="Código o descripción..." oninput="setBusq(this.value)">
    </div>
</div>

<div class="page">

    <!-- KPIs -->
    <div class="kpi-row" id="kpiRow">
        <div class="kpi">
            <div class="num" style="color:var(--accent);" id="kEnviado">{total_env:,}</div>
            <div class="lbl">Total Enviado</div>
            <div class="sub">prendas despachadas</div>
        </div>
        <div class="kpi">
            <div class="num" style="color:var(--green);" id="kVendido">{total_vta:,}</div>
            <div class="lbl">Vendido</div>
            <div class="sub" id="kPctV">{pct_v}% del enviado</div>
        </div>
        <div class="kpi">
            <div class="num" style="color:var(--accent);" id="kDevuelto">{total_dev:,}</div>
            <div class="lbl">Devuelto a Central</div>
            <div class="sub" id="kPctD">{pct_d}% del enviado</div>
        </div>
        <div class="kpi">
            <div class="num" style="color:var(--red);" id="kEnLocal">{total_stk:,}</div>
            <div class="lbl">En Local (sin vender)</div>
            <div class="sub" id="kPctS">{pct_s}% del enviado</div>
        </div>
    </div>

    <!-- Local cards -->
    <div class="local-row">
        <div class="local-card">
            <div class="local-name" style="color:var(--accent);">LURO</div>
            <div class="local-stats" id="locLURO">
                <div><div class="lst-n" style="color:var(--accent);">{luro_env['env']:,}</div><div class="lst-l">Enviado</div></div>
                <div><div class="lst-n" style="color:var(--green);">{luro_env['vta']:,}</div><div class="lst-l">Vendido</div></div>
                <div><div class="lst-n" style="color:var(--accent);">{luro_env['dev']:,}</div><div class="lst-l">Devuelto</div></div>
                <div><div class="lst-n" style="color:var(--red);">{luro_env['stk']:,}</div><div class="lst-l">En Local</div></div>
            </div>
            <div style="margin-top:14px;display:flex;gap:24px;">
                <span style="color:var(--green);font-size:.8rem;font-weight:900;">Tasa venta: {luro_env['pctV']}%</span>
                <span style="color:var(--accent);font-size:.8rem;font-weight:900;">Tasa dev: {luro_env['pctD']}%</span>
            </div>
        </div>
        <div class="local-card">
            <div class="local-name" style="color:var(--blue);">PERALTA</div>
            <div class="local-stats" id="locPERALTA">
                <div><div class="lst-n" style="color:var(--accent);">{pera_env['env']:,}</div><div class="lst-l">Enviado</div></div>
                <div><div class="lst-n" style="color:var(--green);">{pera_env['vta']:,}</div><div class="lst-l">Vendido</div></div>
                <div><div class="lst-n" style="color:var(--accent);">{pera_env['dev']:,}</div><div class="lst-l">Devuelto</div></div>
                <div><div class="lst-n" style="color:var(--red);">{pera_env['stk']:,}</div><div class="lst-l">En Local</div></div>
            </div>
            <div style="margin-top:14px;display:flex;gap:24px;">
                <span style="color:var(--green);font-size:.8rem;font-weight:900;">Tasa venta: {pera_env['pctV']}%</span>
                <span style="color:var(--accent);font-size:.8rem;font-weight:900;">Tasa dev: {pera_env['pctD']}%</span>
            </div>
        </div>
    </div>

    <!-- Charts -->
    <div class="charts-row">
        <div class="chart-box">
            <div class="chart-title">Distribución del stock enviado</div>
            <div class="chart-canvas-wrap"><canvas id="donutChart"></canvas></div>
        </div>
        <div class="chart-box">
            <div class="chart-title">Evolución mensual — Enviado / Vendido / Devuelto</div>
            <div class="chart-canvas-wrap"><canvas id="lineChart"></canvas></div>
        </div>
    </div>

    <!-- Family breakdown -->
    <div class="section" id="famSection">
        <div class="sec-title">Tasa de venta por familia (Top 12 por volumen enviado)</div>
        <div id="famBars"></div>
    </div>

    <!-- Main table -->
    <div class="section">
        <div class="sec-title" style="display:flex;justify-content:space-between;align-items:center;">
            <span>Detalle por artículo</span>
            <span id="tblCount" style="color:#555;font-weight:400;font-size:.75rem;"></span>
        </div>
        <div class="tbl-wrap">
            <table>
                <thead>
                    <tr>
                        <th onclick="sortBy('Codigo')">Código</th>
                        <th onclick="sortBy('Descripcion')">Descripción</th>
                        <th onclick="sortBy('Familia')">Familia</th>
                        <th onclick="sortBy('Temporada')">Temp.</th>
                        <th onclick="sortBy('Local')">Local</th>
                        <th onclick="sortBy('Enviado')" style="text-align:right;">Enviado</th>
                        <th onclick="sortBy('Vendido')" style="text-align:right;">Vendido</th>
                        <th onclick="sortBy('PctV')" style="text-align:right;">% Vta</th>
                        <th onclick="sortBy('Devuelto')" style="text-align:right;">Devuelto</th>
                        <th onclick="sortBy('PctD')" style="text-align:right;">% Dev</th>
                        <th onclick="sortBy('EnLocal')" style="text-align:right;">En Local</th>
                    </tr>
                </thead>
                <tbody id="tblBody"></tbody>
            </table>
        </div>
        <div class="pagination" id="pagination"></div>
    </div>

</div>

<script>
const DATA = {data_tri_json};
const MESES_ENV = {meses_env};
const MESES_VTA = {meses_vta};
const MESES_DEV = {meses_dev};
const FAM_DATA  = {json.dumps(fam.to_dict('records'), ensure_ascii=False)};

let filtAnio  = 0;
let filtLocal = 'ALL';
let filtFam   = '';
let filtTemp  = '';
let filtBusq  = '';
let sortCol   = 'Enviado';
let sortAsc   = false;
let pagina    = 1;
const POR_PAG = 60;

function setAnio(a) {{
    filtAnio = a;
    pagina = 1;
    document.querySelectorAll('.anio-btn').forEach(b => b.classList.remove('on'));
    document.getElementById(a === 0 ? 'aTODOS' : 'a' + a)?.classList.add('on');
    aplicar();
}}
function setLocal(l) {{
    filtLocal = l;
    pagina = 1;
    ['ALL','LURO','PERALTA'].forEach(x => document.getElementById('l'+x)?.classList.remove('on'));
    document.getElementById('l'+l)?.classList.add('on');
    aplicar();
}}
function setFam(v)  {{ filtFam  = v; pagina = 1; aplicar(); }}
function setTemp(v) {{ filtTemp = v; pagina = 1; aplicar(); }}
function setBusq(v) {{ filtBusq = v.trim().toLowerCase(); pagina = 1; aplicar(); }}
function sortBy(col) {{
    if (sortCol === col) sortAsc = !sortAsc; else {{ sortCol = col; sortAsc = false; }}
    document.querySelectorAll('th').forEach(th => th.classList.remove('sorted'));
    document.querySelectorAll('th').forEach(th => {{
        if (th.getAttribute('onclick') === `sortBy('${{col}}')`) th.classList.add('sorted');
    }});
    aplicar();
}}

function filtrar() {{
    return DATA.filter(r => {{
        if (filtAnio  && r.Anio !== filtAnio)           return false;
        if (filtLocal !== 'ALL' && r.Local !== filtLocal) return false;
        if (filtFam   && r.Familia !== filtFam)          return false;
        if (filtTemp  && r.Temporada !== filtTemp)       return false;
        if (filtBusq  && !r.Codigo.toLowerCase().includes(filtBusq)
                      && !r.Descripcion.toLowerCase().includes(filtBusq)) return false;
        return true;
    }});
}}

function aplicar() {{
    const rows = filtrar();
    rows.sort((a,b) => {{
        const va = a[sortCol], vb = b[sortCol];
        return (sortAsc ? 1 : -1) * (va < vb ? -1 : va > vb ? 1 : 0);
    }});

    // KPI recalc
    let tEnv=0, tVta=0, tDev=0, tStk=0;
    rows.forEach(r => {{ tEnv+=r.Enviado; tVta+=r.Vendido; tDev+=r.Devuelto; tStk+=r.EnLocal; }});
    const pV = tEnv ? (tVta/tEnv*100).toFixed(1) : 0;
    const pD = tEnv ? (tDev/tEnv*100).toFixed(1) : 0;
    const pS = tEnv ? (tStk/tEnv*100).toFixed(1) : 0;
    document.getElementById('kEnviado').textContent  = tEnv.toLocaleString();
    document.getElementById('kVendido').textContent  = tVta.toLocaleString();
    document.getElementById('kDevuelto').textContent = tDev.toLocaleString();
    document.getElementById('kEnLocal').textContent  = tStk.toLocaleString();
    document.getElementById('kPctV').textContent     = pV + '% del enviado';
    document.getElementById('kPctD').textContent     = pD + '% del enviado';
    document.getElementById('kPctS').textContent     = pS + '% del enviado';

    // Render table
    const total = rows.length;
    const maxPag = Math.ceil(total / POR_PAG);
    pagina = Math.min(pagina, maxPag || 1);
    const slice = rows.slice((pagina-1)*POR_PAG, pagina*POR_PAG);

    document.getElementById('tblCount').textContent = total.toLocaleString() + ' artículos';
    document.getElementById('tblBody').innerHTML = slice.map(r => {{
        const pV2 = r.Enviado ? (r.Vendido/r.Enviado*100).toFixed(1) : 0;
        const pD2 = r.Enviado ? (r.Devuelto/r.Enviado*100).toFixed(1) : 0;
        const vColor = pV2 >= 60 ? '#66bb6a' : pV2 >= 30 ? '#e8b963' : '#ef5350';
        const dColor = '#e8b963';
        const sColor = r.EnLocal > 0 ? '#ef5350' : '#333';
        return `<tr>
            <td style="color:#555;font-size:.78rem;">${{r.Codigo}}</td>
            <td style="font-weight:900;">${{r.Descripcion}}</td>
            <td style="color:#888;font-size:.8rem;">${{r.Familia}}</td>
            <td style="color:#555;font-size:.8rem;">${{r.Temporada}}</td>
            <td style="color:${{r.Local==='LURO'?'var(--accent)':'var(--blue)'}};font-size:.8rem;font-weight:900;">${{r.Local}}</td>
            <td style="text-align:right;font-weight:900;color:var(--accent);">${{r.Enviado.toLocaleString()}}</td>
            <td style="text-align:right;font-weight:900;color:var(--green);">${{r.Vendido.toLocaleString()}}</td>
            <td style="text-align:right;">
                <span style="color:${{vColor}};font-weight:900;">${{pV2}}%</span>
                <span class="pct-bar" style="width:${{Math.min(pV2,100)*0.5}}px;background:${{vColor}};"></span>
            </td>
            <td style="text-align:right;font-weight:900;color:var(--accent);">${{r.Devuelto.toLocaleString()}}</td>
            <td style="text-align:right;">
                <span style="color:${{dColor}};font-weight:900;">${{pD2}}%</span>
            </td>
            <td style="text-align:right;font-weight:900;color:${{sColor}};">${{r.EnLocal > 0 ? r.EnLocal.toLocaleString() : '—'}}</td>
        </tr>`;
    }}).join('');

    // Pagination
    let pg = '';
    if (maxPag > 1) {{
        pg += `<button class="pg-btn${{pagina===1?' on':''}}" onclick="irPag(1)">1</button>`;
        if (pagina > 3) pg += `<span>…</span>`;
        for (let i=Math.max(2,pagina-1); i<=Math.min(maxPag-1,pagina+1); i++)
            pg += `<button class="pg-btn${{i===pagina?' on':''}}" onclick="irPag(${{i}})">${{i}}</button>`;
        if (pagina < maxPag-2) pg += `<span>…</span>`;
        if (maxPag > 1) pg += `<button class="pg-btn${{pagina===maxPag?' on':''}}" onclick="irPag(${{maxPag}})">${{maxPag}}</button>`;
    }}
    document.getElementById('pagination').innerHTML = pg;
}}

function irPag(p) {{ pagina = p; aplicar(); window.scrollTo(0,0); }}

// Family bars
function renderFamBars() {{
    const mx = Math.max(...FAM_DATA.map(f => f.Enviado), 1);
    document.getElementById('famBars').innerHTML = FAM_DATA.map(f => {{
        const pV = f.Enviado ? (f.Vendido/f.Enviado*100).toFixed(1) : 0;
        const pD = f.Enviado ? (f.Devuelto/f.Enviado*100).toFixed(1) : 0;
        const pS = f.Enviado ? (f.EnLocal/f.Enviado*100).toFixed(1) : 0;
        const wV = (f.Vendido/mx*100).toFixed(1);
        const wD = (f.Devuelto/mx*100).toFixed(1);
        const wS = (f.EnLocal/mx*100).toFixed(1);
        return `<div class="fam-bar-row">
            <div class="fam-bar-labels">
                <span class="fam-bar-name">${{f.Familia}}</span>
                <div class="fam-bar-stats">
                    <span style="color:var(--green);">${{pV}}% vendido</span>
                    <span style="color:var(--accent);">${{pD}}% devuelto</span>
                    <span style="color:var(--red);">${{pS}}% en local</span>
                    <span style="color:#444;">${{f.Enviado.toLocaleString()}} env</span>
                </div>
            </div>
            <div class="fam-bar-track">
                <div style="width:${{wV}}%;background:var(--green);height:8px;"></div>
                <div style="width:${{wD}}%;background:var(--accent);height:8px;margin-left:2px;"></div>
                <div style="width:${{wS}}%;background:var(--red);height:8px;margin-left:2px;"></div>
            </div>
        </div>`;
    }}).join('');
}}

// Donut chart
function buildDonut() {{
    const ctx = document.getElementById('donutChart').getContext('2d');
    new Chart(ctx, {{
        type: 'doughnut',
        data: {{
            labels: ['Vendido', 'Devuelto a Central', 'En Local (sin vender)'],
            datasets: [{{ data: [{total_vta}, {total_dev}, {total_stk}],
                backgroundColor: ['#66bb6a','#e8b963','#ef5350'],
                borderColor: '#0d0d0d', borderWidth: 3 }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{
                legend: {{ position:'bottom', labels: {{ color:'#888', font:{{size:11}}, padding:16 }} }},
                tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.label}}: ${{ctx.raw.toLocaleString()}} prendas` }} }}
            }}
        }}
    }});
}}

// Line chart — monthly (last 18 months)
function buildLine() {{
    let meses = [...new Set([
        ...MESES_ENV.map(r=>r.AnoMes),
        ...MESES_VTA.map(r=>r.AnoMes),
        ...MESES_DEV.map(r=>r.AnoMes)
    ])].sort().slice(-18);

    const get = (arr, mes, local) => {{
        const rows = arr.filter(r => r.AnoMes === mes && (local ? r.Local === local : true));
        return rows.reduce((s,r) => s + r.Cantidad, 0);
    }};

    const envL = meses.map(m => get(MESES_ENV, m, 'LURO'));
    const envP = meses.map(m => get(MESES_ENV, m, 'PERALTA'));
    const vtaL = meses.map(m => get(MESES_VTA, m, 'LURO'));
    const vtaP = meses.map(m => get(MESES_VTA, m, 'PERALTA'));
    const devL = meses.map(m => get(MESES_DEV, m, 'LURO'));
    const devP = meses.map(m => get(MESES_DEV, m, 'PERALTA'));

    const ctx = document.getElementById('lineChart').getContext('2d');
    new Chart(ctx, {{
        type: 'line',
        data: {{
            labels: meses,
            datasets: [
                {{ label:'Enviado LURO',     data:envL, borderColor:'#e8b963', backgroundColor:'transparent', tension:.3, borderWidth:2, pointRadius:2 }},
                {{ label:'Enviado PERALTA',  data:envP, borderColor:'#b8893a', backgroundColor:'transparent', tension:.3, borderWidth:2, pointRadius:2, borderDash:[4,4] }},
                {{ label:'Vendido LURO',     data:vtaL, borderColor:'#66bb6a', backgroundColor:'transparent', tension:.3, borderWidth:2, pointRadius:2 }},
                {{ label:'Vendido PERALTA',  data:vtaP, borderColor:'#3a8b3a', backgroundColor:'transparent', tension:.3, borderWidth:2, pointRadius:2, borderDash:[4,4] }},
                {{ label:'Devuelto LURO',    data:devL, borderColor:'#64b5f6', backgroundColor:'transparent', tension:.3, borderWidth:2, pointRadius:2 }},
                {{ label:'Devuelto PERALTA', data:devP, borderColor:'#3a7ab6', backgroundColor:'transparent', tension:.3, borderWidth:2, pointRadius:2, borderDash:[4,4] }},
            ]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            scales: {{
                x: {{ ticks: {{ color:'#555', maxRotation:45, font:{{size:9}} }}, grid:{{ color:'#1a1a1a' }} }},
                y: {{ ticks: {{ color:'#555', font:{{size:10}} }}, grid:{{ color:'#1a1a1a' }} }}
            }},
            plugins: {{
                legend: {{ labels: {{ color:'#888', font:{{size:10}}, boxWidth:12, padding:10 }} }},
                tooltip: {{ mode:'index', intersect:false }}
            }}
        }}
    }});
}}

renderFamBars();
buildDonut();
buildLine();
aplicar();
</script>
</body>
</html>"""

    with open(nombre_archivo, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  Envios guardado: {nombre_archivo}  ({round(len(html)/1024):.0f} KB)")


if __name__ == '__main__':
    res  = obtener_datos()
    pend = obtener_pendientes()
    generar_html(res, "index.html")
    generar_dashboard(res, "dashboard.html")
    generar_pendientes_html(pend, "pendientes.html")
    generar_envios_html(res, "envios.html")
