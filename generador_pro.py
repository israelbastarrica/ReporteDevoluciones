import pyodbc
import pandas as pd
import json
import warnings

# --- CONFIGURACIÓN MARKET ---
SERVER = r'marketcentral.ddns.net\ZOOLOGIC,1433'
DATABASE = 'DRAGONFISH_CENTRAL' 
USER = 'MARKET'
PASSWORD = 'Market202020'

warnings.filterwarnings('ignore', category=UserWarning)

def generar_reporte_final_market(fecha_inicio, nombre_archivo, titulo):
    try:
        print(f"🚀 Procesando {titulo}...")
        conn_str = f'DRIVER={{SQL Server}};SERVER={SERVER};DATABASE={DATABASE};UID={USER};PWD={PASSWORD}'
        conn = pyodbc.connect(conn_str)
        
        filtro_fecha = f"AND COMP.FFCH >= '{fecha_inicio}'" if fecha_inicio else ""
        
        # CONSULTA MAESTRA CON FILTRO DE CLIENTE 'CENTRAL'
        # Esto garantiza que solo vemos lo que LOS LOCALES mandaron A LA CENTRAL
        query = f"""
        SELECT 
            DET.FART as Codigo,
            MAX(DET.FTXT) as Descripcion,
            ISNULL(MAX(FAM.DESCRIP), 'SIN FAMILIA') as Familia,
            ISNULL(MAX(TIPO.DESCRIP), 'SIN TIPO') as Tipo,
            ISNULL(MAX(CATE.DESCRIP), 'SIN CATEGORIA') as Categoria,
            SUM(CASE WHEN COMP.FCLIENTE = 'CENTRAL' THEN DET.FCANT ELSE 0 END) as CantVuelto,
            SUM(CASE WHEN COMP.FCLIENTE <> 'CENTRAL' THEN DET.FCANT ELSE 0 END) as CantEnviado
        FROM {DATABASE}.Zoologic.COMPROBANTEV COMP
        INNER JOIN {DATABASE}.Zoologic.COMPROBANTEVDET DET ON COMP.CODIGO = DET.CODIGO
        LEFT JOIN {DATABASE}.Zoologic.ART ART ON DET.FART = ART.ARTCOD
        LEFT JOIN {DATABASE}.Zoologic.FAMILIA FAM ON FAM.COD = ART.FAMILIA
        LEFT JOIN {DATABASE}.Zoologic.TIPOART TIPO ON TIPO.COD = ART.TIPOARTI
        LEFT JOIN {DATABASE}.Zoologic.CATEGART CATE ON CATE.COD = ART.CATEARTI
        WHERE COMP.ANULADO = 0
          AND COMP.FLETRA = 'R' -- Remitos
          {filtro_fecha}
          AND DET.FTXT NOT LIKE '%BOLSA%'
          AND LEFT(DET.FART, 1) NOT IN ('Z', '9')
        GROUP BY DET.FART
        """
        
        df = pd.read_sql(query, conn)
        conn.close()

        if df.empty:
            print(f"⚠️ No se encontraron datos para {titulo}.")
            return

        # --- ANÁLISIS DE DATOS ---
        df_final = df[df['CantVuelto'] > 0].copy()
        total_vuelto = df_final['CantVuelto'].sum()
        total_enviado = df['CantEnviado'].sum()
        
        # Resúmenes por jerarquía
        res_fam = df_final.groupby('Familia')['CantVuelto'].sum().nlargest(5).reset_index()
        res_tipo = df_final.groupby('Tipo')['CantVuelto'].sum().nlargest(5).reset_index()
        res_cat = df_final.groupby('Categoria')['CantVuelto'].sum().nlargest(5).reset_index()
        
        # Top 15 Conciliado
        top_15 = df_final.nlargest(15, 'CantVuelto')

        # --- HTML MARKET DESIGN (ALTO CONTRASTE) ---
        def render_tabla_mini(df_in, col):
            return "".join([f"<tr><td>{r[col]}</td><td class='text-end fw-bold'>{r['CantVuelto']:.0f}</td></tr>" for _, r in df_in.iterrows()])

        filas_top = "".join([
            f"<tr><td class='fw-bold'>{r['Codigo']}</td><td>{r['Descripcion']}</td><td class='text-end text-muted'>{r['CantEnviado']:.0f}</td><td class='text-end fw-bold fs-4'>{r['CantVuelto']:.0f}</td></tr>" 
            for _, r in top_15.iterrows()
        ])

        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>MARKET | {titulo}</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            <style>
                body {{ background: #fff; color: #000; font-family: 'Arial Black', sans-serif; padding: 30px; }}
                .header-market {{ border-bottom: 15px solid #000; padding: 20px 0; margin-bottom: 40px; }}
                .logo {{ font-size: 4rem; font-weight: 900; letter-spacing: 15px; text-transform: uppercase; }}
                .kpi-box {{ border: 5px solid #000; padding: 30px; text-align: center; height: 100%; }}
                .section-title {{ background: #000; color: #fff; padding: 12px; font-weight: 900; text-transform: uppercase; letter-spacing: 3px; margin-top: 50px; }}
                .table-market {{ border: 3px solid #000; }}
            </style>
        </head>
        <body>
            <div class="container-fluid">
                <div class="header-market text-center">
                    <h1 class="logo">MARKET</h1>
                    <div class="fs-3 fw-bold">{titulo}</div>
                </div>

                <div class="row g-4 mb-5">
                    <div class="col-md-6"><div class="kpi-box"><div class="display-1 fw-bold">{total_vuelto:,.0f}</div><div class="fs-4">PRENDAS DEVUELTAS A CENTRAL</div></div></div>
                    <div class="col-md-6"><div class="kpi-box" style="background:#000; color:#fff;"><div class="display-1 fw-bold">{df_final['Codigo'].nunique()}</div><div class="fs-4">ARTÍCULOS ÚNICOS EN CAMIÓN</div></div></div>
                </div>

                <div class="row g-4 mb-5">
                    <div class="col-md-4"><div class="kpi-box"><h5>POR FAMILIA</h5><table class="table table-sm">{render_tabla_mini(res_fam, 'Familia')}</table></div></div>
                    <div class="col-md-4"><div class="kpi-box"><h5>POR TIPO</h5><table class="table table-sm">{render_tabla_mini(res_tipo, 'Tipo')}</table></div></div>
                    <div class="col-md-4"><div class="kpi-box"><h5>POR CATEGORÍA</h5><table class="table table-sm">{render_tabla_mini(res_cat, 'Categoria')}</table></div></div>
                </div>

                <h2 class="section-title">TOP 15: Conciliación Enviado vs Devuelto</h2>
                <table class="table table-hover table-market align-middle">
                    <thead class="table-dark">
                        <tr>
                            <th>CÓDIGO</th>
                            <th>DESCRIPCIÓN</th>
                            <th class="text-end">ENVIADO A LOCALES</th>
                            <th class="text-end">DEVUELTO A CENTRAL</th>
                        </tr>
                    </thead>
                    <tbody>{filas_top}</tbody>
                </table>
            </div>
        </body>
        </html>
        """
        with open(nombre_archivo, "w", encoding="utf-8") as f:
            f.write(html_template)
        print(f"✅ Reporte generado: {nombre_archivo}")

    except Exception as e:
        print(f"❌ Error crítico: {e}")

if __name__ == "__main__":
    # Generamos los dos análisis
    generar_reporte_final_market('20260101', "REPORT_MARKET_2026.html", "AUDITORÍA LOGÍSTICA 2026")
    generar_reporte_final_market(None, "REPORT_MARKET_HISTORICO.html", "AUDITORÍA LOGÍSTICA HISTÓRICA")