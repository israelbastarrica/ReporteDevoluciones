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

OUTPUT = r'C:\REPORTESDEVOLUCIONES\insumos.html'

UNIDADES_ESPECIALES = {
    'ZZ0000111': 'Pack x6',
    'ZZ0000149': 'Caja x80',
}
UNIDAD_DEFAULT = 'Unidad'
DB_MARKET = 'MARKET'

# ---------------------------------------------------------------------------
# 1. Conexión a MARKET + stock actual desde COMB
# ---------------------------------------------------------------------------
print("Consultando DB...")
conn = pyodbc.connect(
    f'DRIVER={{SQL Server}};SERVER={SERVER};'
    f'DATABASE={DB_MARKET};UID={USER};PWD={PASSWORD}',
    timeout=30
)

df_stock = pd.read_sql(f"""
    SELECT RTRIM(C.COART) AS Codigo, SUM(C.COCANT) AS StockActual
    FROM {DB_CENTRAL}.Zoologic.COMB C
    WHERE LEFT(RTRIM(C.COART), 2) = 'ZZ'
    GROUP BY RTRIM(C.COART)
""", conn)
df_stock['StockActual'] = df_stock['StockActual'].fillna(0).astype(int)
print(f"  Articulos ZZ* en COMB: {len(df_stock)}")

# ---------------------------------------------------------------------------
# 2. Demanda desde PedidosInsumos (solo Codigo + Consumido)
# ---------------------------------------------------------------------------
df_consumo = pd.read_sql("""
    WITH Consumos AS (
        SELECT RTRIM(R.ARTCOD) AS Codigo, R.Cantidad
        FROM PedidosInsumosRegistro R
        WHERE R.Eliminado = 0

        UNION ALL

        SELECT RTRIM(D.ARTCOD) AS Codigo,
               ISNULL(D.CantidadEnviada, D.Cantidad) AS Cantidad
        FROM PedidosInsumosDetalle D
        INNER JOIN PedidosInsumos P ON D.IDPedido = P.ID
        WHERE D.Eliminado = 0 AND P.Eliminado = 0
          AND ISNULL(D.Existencia, 1) <> 0
          AND ISNULL(D.CantidadEnviada, D.Cantidad) > 0
          AND P.FechaEnviado IS NOT NULL
    )
    SELECT Codigo, SUM(Cantidad) AS Consumido
    FROM Consumos
    GROUP BY Codigo
""", conn)
print(f"  Articulos con demanda registrada: {len(df_consumo)}")

# ---------------------------------------------------------------------------
# 3. Descripcion y proveedor desde ART+PROV para TODOS los ZZ*
# ---------------------------------------------------------------------------
df_art = pd.read_sql(f"""
    SELECT RTRIM(A.ARTCOD)                             AS Codigo,
           RTRIM(A.ARTDES)                             AS Descripcion,
           ISNULL(RTRIM(P.CLNOM), 'SIN PROVEEDOR')    AS Proveedor
    FROM {DB_CENTRAL}.Zoologic.ART  A
    LEFT JOIN {DB_CENTRAL}.Zoologic.PROV P ON A.ARTFAB = P.CLCOD
    WHERE LEFT(RTRIM(A.ARTCOD), 2) = 'ZZ'
""", conn)
print(f"  Articulos ZZ* en ART: {len(df_art)}")

# ---------------------------------------------------------------------------
# 4. Consumo desde último ingreso (MSTOCK motivo 13, DIRMOV 1=entrada 2=salida)
# ---------------------------------------------------------------------------
df_mstock = pd.read_sql(f"""
    WITH ultimos_ingresos AS (
        SELECT RTRIM(D.MART) AS Codigo, MAX(M.FECHA) AS UltimoIngreso
        FROM {DB_CENTRAL}.Zoologic.MSTOCK M
        INNER JOIN {DB_CENTRAL}.Zoologic.DETMSTOCK D ON M.CODIGO = D.NUMR
        WHERE RTRIM(M.MOTIVO) = '13' AND M.DIRMOV = 1
          AND LEFT(RTRIM(D.MART), 2) = 'ZZ'
        GROUP BY RTRIM(D.MART)
    ),
    salidas AS (
        SELECT RTRIM(D.MART) AS Codigo, SUM(D.CANTI) AS ConsumidoDesdeIngreso
        FROM {DB_CENTRAL}.Zoologic.MSTOCK M
        INNER JOIN {DB_CENTRAL}.Zoologic.DETMSTOCK D ON M.CODIGO = D.NUMR
        INNER JOIN ultimos_ingresos UI ON RTRIM(D.MART) = UI.Codigo
                                     AND M.FECHA > UI.UltimoIngreso
        WHERE RTRIM(M.MOTIVO) = '13' AND M.DIRMOV = 2
        GROUP BY RTRIM(D.MART)
    )
    SELECT UI.Codigo,
           ISNULL(S.ConsumidoDesdeIngreso, 0) AS ConsumidoDesdeIngreso
    FROM ultimos_ingresos UI
    LEFT JOIN salidas S ON UI.Codigo = S.Codigo
""", conn)
conn.close()
print(f"  Artículos con ingreso MSTOCK: {len(df_mstock)}")

# ---------------------------------------------------------------------------
# 5. Merge y limpieza
# ---------------------------------------------------------------------------
df = df_stock.merge(df_consumo, on='Codigo', how='outer')
df = df.merge(df_art, on='Codigo', how='left')
df = df.merge(df_mstock, on='Codigo', how='left')

df['Descripcion']          = df['Descripcion'].fillna(df['Codigo'])
df['Proveedor']            = df['Proveedor'].fillna('SIN PROVEEDOR')
df['StockActual']          = df['StockActual'].fillna(0).astype(int)
df['Consumido']            = df['Consumido'].fillna(0).astype(int)
df['ConsumidoDesdeIngreso'] = df['ConsumidoDesdeIngreso'].fillna(-1).astype(int)
df['Unidad']               = df['Codigo'].map(UNIDADES_ESPECIALES).fillna(UNIDAD_DEFAULT)

df = df[(df['Consumido'] > 0) | (df['StockActual'] > 0)].copy()
df = df.sort_values(['Proveedor', 'Codigo']).reset_index(drop=True)
df = df[['Proveedor', 'Codigo', 'Descripcion', 'Unidad', 'Consumido', 'StockActual', 'ConsumidoDesdeIngreso']]

# Separar cartones del resto
es_carton = df['Descripcion'].str.upper().str.startswith('CARTON')
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
.sol-form select{{background:#0c1a0c;border:1px solid #2a4a2a;color:var(--text);padding:6px 10px;border-radius:4px;font-size:12px;min-width:220px;}}
.sol-form select:focus{{outline:none;border-color:#7ef7a0;}}
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
/* YA PEDIDO section */
.pedido-sec{{background:#060d06;border-bottom:2px solid #1a3a1a;padding:12px 24px 14px;display:none;}}
.pedido-sec.open{{display:block;}}
.pedido-sec-title{{font-family:"Arial Black",Arial,sans-serif;font-size:11px;color:#22c55e;letter-spacing:.5px;font-weight:900;margin-bottom:10px;}}
.pedido-sec table{{width:100%;border-collapse:collapse;}}
.pedido-sec th{{background:#0a1a0a;color:#555;font-size:10px;letter-spacing:.5px;padding:5px 10px;text-align:left;font-weight:700;}}
.pedido-sec td{{padding:6px 10px;border-bottom:1px solid #0d1a0d;font-size:12px;white-space:nowrap;}}
.btn-llego{{background:#1a3a1a;border:1px solid #3a6a3a;color:#22c55e;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;}}
.btn-llego:hover{{background:#224422;border-color:#22c55e;}}
.btn-despedido{{background:none;border:1px solid #2a2a2a;color:#555;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:10px;margin-left:4px;}}
.btn-despedido:hover{{border-color:#666;color:#888;}}
/* Historial tab */
.hist-toolbar{{display:flex;gap:10px;padding:12px 24px;border-bottom:1px solid var(--border);flex-wrap:wrap;align-items:center;}}
.hist-wrap{{overflow-x:auto;max-height:calc(100vh - 220px);margin:8px 24px 0;border:1px solid var(--border);border-radius:6px;}}
.tipo-badge{{display:inline-block;font-size:9px;font-weight:900;padding:2px 7px;border-radius:2px;letter-spacing:.5px;}}
.tipo-urgente{{background:#2e0808;color:var(--err);}}
.tipo-carton{{background:#0a1e0a;color:#22c55e;}}
.tipo-pedido{{background:#0d1a2e;color:#60a5fa;}}
.tipo-llego{{background:#1a3a1a;color:#4ade80;font-family:"Arial Black",Arial,sans-serif;}}
.hist-del{{background:none;border:1px solid #2a2a2a;color:#555;padding:2px 8px;border-radius:3px;cursor:pointer;font-size:10px;}}
.hist-del:hover{{border-color:var(--err);color:var(--err);}}
/* Modal pedido */
#pedido-modal{{display:none;position:fixed;inset:0;z-index:10000;background:#0009;align-items:center;justify-content:center;}}
#pedido-modal.open{{display:flex;}}
/* Modal llegó */
#llego-modal{{display:none;position:fixed;inset:0;z-index:10000;background:#0009;align-items:center;justify-content:center;}}
#llego-modal.open{{display:flex;}}
/* Modal urgente */
#urg-modal{{display:none;position:fixed;inset:0;z-index:10000;background:#0009;align-items:center;justify-content:center;}}
#urg-modal.open{{display:flex;}}
.urg-mbox{{background:#1a1a1a;border:1px solid #5a1a1a;border-radius:8px;padding:28px 32px;min-width:320px;max-width:420px;box-shadow:0 8px 32px #000d;}}
.urg-mtitle{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:14px;color:var(--err);letter-spacing:.5px;margin-bottom:6px;}}
.urg-mart{{color:var(--muted);font-size:12px;margin-bottom:18px;padding-bottom:12px;border-bottom:1px solid #2a2a2a;}}
.urg-mfield{{display:flex;flex-direction:column;gap:4px;margin-bottom:14px;}}
.urg-mfield span{{font-size:10px;color:#555;letter-spacing:.5px;font-weight:700;}}
.urg-mfield input{{background:#111;border:1px solid #3a3a3a;color:#f0f0f0;padding:8px 12px;border-radius:4px;font-size:13px;}}
.urg-mfield input:focus{{outline:none;border-color:var(--err);}}
.urg-mbtns{{display:flex;gap:10px;justify-content:flex-end;margin-top:20px;}}
.urg-mcancel{{background:none;border:1px solid #333;color:#888;padding:7px 18px;border-radius:4px;cursor:pointer;font-size:12px;}}
.urg-mconfirm{{background:#2e0808;border:1px solid var(--err);color:var(--err);padding:7px 18px;border-radius:4px;cursor:pointer;font-size:12px;font-weight:700;letter-spacing:.5px;}}
.urg-mconfirm:hover{{background:#3e1010;}}
/* Grupo urgente en tabla */
tbody tr.group-urg{{background:#1a0808!important;border-left:3px solid var(--err);}}
tbody tr.group-urg td{{color:var(--err)!important;}}
/* Filas críticas */
tbody tr.row-critico > td{{background:rgba(239,68,68,0.08)!important;}}
tbody tr.row-critico > td:first-child{{border-left:3px solid #ef4444;}}
tbody tr.row-critico:hover > td{{background:rgba(239,68,68,0.14)!important;}}
/* Modal pedido por proveedor */
#pedido-prov-modal{{display:none;position:fixed;inset:0;z-index:10000;background:#0009;align-items:center;justify-content:center;}}
#pedido-prov-modal.open{{display:flex;}}
.ppm-box{{background:#1a1a1a;border:1px solid #1a3a1a;border-radius:8px;padding:24px 28px;min-width:480px;max-width:680px;max-height:82vh;display:flex;flex-direction:column;box-shadow:0 8px 32px #000d;}}
.ppm-title{{font-family:"Arial Black",Arial,sans-serif;font-weight:900;font-size:13px;color:#22c55e;letter-spacing:.5px;margin-bottom:4px;}}
.ppm-sub{{color:var(--muted);font-size:11px;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid #222;}}
.ppm-scroll{{overflow-y:auto;flex:1;margin-bottom:12px;border:1px solid #222;border-radius:4px;}}
.ppm-scroll table{{width:100%;border-collapse:collapse;}}
.ppm-scroll th{{font-size:10px;color:#555;letter-spacing:.5px;padding:5px 10px;text-align:left;position:sticky;top:0;background:#111;border-bottom:1px solid #222;font-weight:700;}}
.ppm-scroll td{{padding:6px 10px;border-bottom:1px solid #1a1a1a;font-size:12px;}}
.ppm-cant{{background:#111;border:1px solid #333;color:#eee;padding:4px 8px;border-radius:3px;width:80px;text-align:right;font-size:12px;}}
.ppm-cant:focus{{outline:none;border-color:var(--accent);}}
.ppm-fecha-row{{display:flex;align-items:center;gap:10px;font-size:11px;color:#555;letter-spacing:.5px;font-weight:700;margin-bottom:14px;}}
.ppm-fecha-row input{{background:#111;border:1px solid #3a3a3a;color:#f0f0f0;padding:7px 10px;border-radius:4px;font-size:12px;}}
.ppm-fecha-row input:focus{{outline:none;border-color:#22c55e;}}
.ppm-btns{{display:flex;gap:10px;justify-content:flex-end;}}
/* Botón descarga TXT */
.btn-dl-txt{{background:#0a1e3a;border:1px solid #1a4a7a;color:#60a5fa;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:10px;font-weight:700;letter-spacing:.5px;}}
.btn-dl-txt:hover{{background:#0d2a4a;border-color:#60a5fa;}}
/* YA PEDIDO agrupado por proveedor */
.pedido-prov-group{{border-top:1px solid #0d1a0d;}}
.pedido-prov-hdr{{display:flex;align-items:center;justify-content:space-between;padding:5px 10px;background:#071207;gap:10px;}}
.pedido-prov-hdr-name{{font-size:10px;font-weight:700;color:#3a6a3a;letter-spacing:.5px;}}
</style>
</head>
<body>

<!-- Context menu -->
<div id="ctx-menu">
  <button onclick="ctxToggleUrgente()">⚡ <span id="ctx-urg-lbl">Marcar como URGENTE</span></button>
  <button onclick="ctxToggleDesuso()">🗑 <span id="ctx-des-lbl">Enviar a desuso</span></button>
</div>

<!-- Modal urgente -->
<div id="urg-modal">
  <div class="urg-mbox">
    <div class="urg-mtitle">⚡ MARCAR COMO URGENTE</div>
    <div class="urg-mart" id="urg-modal-art"></div>
    <div class="urg-mfield">
      <span>¿QUIÉN LO SOLICITA?</span>
      <input id="urg-quien" type="text" placeholder="Tu nombre...">
    </div>
    <div class="urg-mfield">
      <span>CANTIDAD NECESARIA</span>
      <input id="urg-cant" type="number" min="1" placeholder="0" style="width:140px;">
    </div>
    <div class="urg-mbtns">
      <button class="urg-mcancel" onclick="cerrarModalUrg()">Cancelar</button>
      <button class="urg-mconfirm" onclick="confirmarUrg()">⚡ CONFIRMAR URGENTE</button>
    </div>
  </div>
</div>

<div class="page-header">
  <div>
    <div class="page-title">STOCK DE INSUMOS</div>
    <div style="color:var(--muted);font-size:11px;margin-top:3px;">Demanda registrada en el sistema &nbsp;·&nbsp; Generado {ahora}</div>
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
  <button class="tab-btn active" id="tab-ins-btn" onclick="switchTab('ins',this)">INSUMOS</button>
  <button class="tab-btn" id="tab-cart-btn" onclick="switchTab('cart',this)">CARTONES</button>
  <button class="tab-btn" id="tab-hist-btn" onclick="switchTab('hist',this)">HISTORIAL</button>
</div>

<!-- ══ INSUMOS ══ -->
<div id="sec-ins" class="section active">
  <div class="toolbar">
    <select id="ins-prov" onchange="S.ins.filtrar()"><option value="">Todos los proveedores</option></select>
    <input id="ins-bus" type="text" placeholder="Buscar código o descripción..." oninput="S.ins.filtrar()">
    <button id="ins-consumo" onclick="S.ins.toggleConsumo()" style="background:#0a1e0a;border:1px solid #1a4a1a;color:#22c55e;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;letter-spacing:.5px;">VER CONSUMO</button>
    <button class="desuso-toggle" onclick="toggleDesusoPanel('ins')">Ver desuso</button>
  </div>
  <div class="cnt" id="ins-cnt"></div>
  <div class="pedido-sec" id="ins-pedido">
    <div class="pedido-sec-title">✅ YA PEDIDO</div>
    <div id="ins-pedido-content"></div>
  </div>
  <div class="desuso-section" id="ins-desuso">
    <div style="color:#7ef7a0;font-size:11px;font-weight:700;letter-spacing:.5px;margin-bottom:8px;">ARTÍCULOS EN DESUSO</div>
    <table>
      <thead><tr>
        <th>CÓDIGO</th><th>DESCRIPCIÓN</th><th>STOCK</th><th></th>
      </tr></thead>
      <tbody id="ins-desuso-tbody"></tbody>
    </table>
  </div>
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
</div>

<!-- ══ CARTONES ══ -->
<div id="sec-cart" class="section">
  <!-- Barra de solicitudes de producción -->
  <div class="sol-bar">
    <div class="sol-bar-title">SOLICITUDES DE PRODUCCIÓN</div>
    <div class="sol-form">
      <label>CARTÓN
        <select id="sol-art"><option value="">Seleccionar cartón...</option></select>
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
    <button id="cart-consumo" onclick="S.cart.toggleConsumo()" style="background:#0a1e0a;border:1px solid #1a4a1a;color:#22c55e;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;letter-spacing:.5px;">VER CONSUMO</button>
    <button class="desuso-toggle" onclick="toggleDesusoPanel('cart')">Ver desuso</button>
  </div>
  <div class="cnt" id="cart-cnt"></div>
  <div class="pedido-sec" id="cart-pedido">
    <div class="pedido-sec-title">✅ YA PEDIDO</div>
    <div id="cart-pedido-content"></div>
  </div>
  <div class="desuso-section" id="cart-desuso">
    <div style="color:#7ef7a0;font-size:11px;font-weight:700;letter-spacing:.5px;margin-bottom:8px;">CARTONES EN DESUSO</div>
    <table>
      <thead><tr>
        <th>CÓDIGO</th><th>DESCRIPCIÓN</th><th>STOCK</th><th></th>
      </tr></thead>
      <tbody id="cart-desuso-tbody"></tbody>
    </table>
  </div>
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
</div>

<!-- ══ HISTORIAL ══ -->
<div id="sec-hist" class="section">
  <div class="hist-toolbar">
    <select id="hist-tipo" onchange="renderHistorial()" style="background:#1a1a1a;border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:4px;font-size:12px;">
      <option value="">Todos los eventos</option>
      <option value="urgente">Urgentes</option>
      <option value="carton">Cartones solicitados</option>
      <option value="pedido">Pedidos realizados</option>
      <option value="llego">Llegó</option>
    </select>
    <input type="text" id="hist-bus" placeholder="Buscar artículo o persona..." oninput="renderHistorial()"
      style="background:#1a1a1a;border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:4px;font-size:12px;width:240px;">
    <span id="hist-cnt" style="color:var(--muted);font-size:11px;"></span>
    <button onclick="limpiarHistorialFiltrado()" style="background:none;border:1px solid #333;color:#666;padding:5px 12px;border-radius:4px;cursor:pointer;font-size:11px;margin-left:auto;">Eliminar filtrados</button>
  </div>
  <div class="hist-wrap">
    <table>
      <thead><tr>
        <th style="min-width:130px;">FECHA</th>
        <th>TIPO</th>
        <th>ARTÍCULO / DESCRIPCIÓN</th>
        <th>QUIÉN</th>
        <th class="num" style="min-width:80px;">CANTIDAD</th>
        <th style="min-width:160px;">NOTA</th>
        <th style="width:70px;"></th>
      </tr></thead>
      <tbody id="hist-tbody"></tbody>
    </table>
  </div>
</div>

<!-- Modal pedido -->
<div id="pedido-modal">
  <div class="urg-mbox">
    <div class="urg-mtitle" style="color:#60a5fa;">📋 REGISTRAR PEDIDO</div>
    <div class="urg-mart" id="pedido-modal-art"></div>
    <div class="urg-mfield">
      <span>CANTIDAD PEDIDA</span>
      <input id="pedido-cant" type="number" min="1" placeholder="0" style="width:140px;">
    </div>
    <div class="urg-mfield">
      <span>FECHA DE ENTREGA APROX.</span>
      <input id="pedido-fecha" type="date" style="width:180px;">
    </div>
    <div class="urg-mbtns">
      <button class="urg-mcancel" onclick="cerrarModalPedido()">Cancelar</button>
      <button class="urg-mconfirm" style="background:#0a1e3a;border-color:#60a5fa;color:#60a5fa;" onclick="confirmarPedido()">📋 CONFIRMAR PEDIDO</button>
    </div>
  </div>
</div>

<!-- Modal llegó -->
<div id="llego-modal">
  <div class="urg-mbox">
    <div class="urg-mtitle" style="color:#22c55e;">✓ LLEGÓ — REGISTRAR ENTRADA</div>
    <div class="urg-mart" id="llego-modal-art"></div>
    <div class="urg-mfield">
      <span>CANTIDAD RECIBIDA</span>
      <input id="llego-cant" type="number" min="1" placeholder="0" style="width:140px;">
    </div>
    <div class="urg-mfield">
      <span>NOTA (OPCIONAL)</span>
      <input id="llego-nota" type="text" placeholder="Observaciones...">
    </div>
    <div class="urg-mbtns">
      <button class="urg-mcancel" onclick="cerrarModalLlego()">Cancelar</button>
      <button class="urg-mconfirm" style="background:#0a2e0a;border-color:#22c55e;color:#22c55e;" onclick="confirmarLlego()">✓ CONFIRMAR LLEGÓ</button>
    </div>
  </div>
</div>

<!-- Modal pedido por proveedor -->
<div id="pedido-prov-modal">
  <div class="ppm-box">
    <div class="ppm-title">📦 PEDIDO A <span id="ppm-prov"></span></div>
    <div class="ppm-sub">Revisá y ajustá las cantidades. La cantidad sugerida es el consumo desde el último ingreso.</div>
    <div class="ppm-scroll">
      <table>
        <thead><tr>
          <th style="min-width:90px;">CÓDIGO</th>
          <th>DESCRIPCIÓN</th>
          <th style="min-width:90px;text-align:right;">CANTIDAD</th>
        </tr></thead>
        <tbody id="ppm-tbody"></tbody>
      </table>
    </div>
    <div class="ppm-fecha-row">
      FECHA DE ENTREGA ESTIMADA:
      <input type="date" id="ppm-fecha">
    </div>
    <div class="ppm-btns">
      <button class="urg-mcancel" onclick="cerrarModalPedidoProv()">Cancelar</button>
      <button class="urg-mconfirm" style="background:#0a1e0a;border-color:#22c55e;color:#22c55e;" onclick="confirmarPedidoProv()">📦 CONFIRMAR PEDIDO</button>
    </div>
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
// Si se abre desde el servidor Flask, usar la misma origin; si es archivo local usar localhost
const SERVER = (window.location.protocol === 'file:' || window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
  ? 'http://localhost:5001'
  : window.location.origin;

// ── Datos compartidos (servidor + fallback localStorage) ─────
const LS_KEY = 'insumos_shared_v2';
let shared = {{ urgente:[], desuso:[], stock_minimo:{{}}, pedido_realizado:[], pedido_info:{{}}, notas:{{}}, solicitudes_cartones:[], historial:[] }};

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
    shared.historial            = shared.historial            || [];
    shared.pedido_info          = shared.pedido_info          || {{}};
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
  document.getElementById('ctx-urg-lbl').textContent = isUrgente(ctxCod) ? 'Quitar urgente' : 'Marcar como URGENTE';
  document.getElementById('ctx-des-lbl').textContent = (shared.desuso||[]).includes(ctxCod) ? 'Quitar de desuso' : 'Enviar a desuso';
  ctxMenu.style.display = 'block';
  ctxMenu.style.left = Math.min(e.pageX, window.innerWidth-210)+'px';
  ctxMenu.style.top  = Math.min(e.pageY, window.innerHeight-80)+'px';
}});
document.addEventListener('click', () => ctxMenu.style.display = 'none');
document.addEventListener('keydown', e => {{ if(e.key==='Escape') {{ ctxMenu.style.display='none'; cerrarModalUrg(); }} }});
document.getElementById('urg-modal').addEventListener('click', e => {{ if(e.target===e.currentTarget) cerrarModalUrg(); }});

function actualizarBadgesUrgente() {{
  ['ins','cart'].forEach(id => {{
    const btn = document.getElementById('tab-'+id+'-btn');
    if (!btn) return;
    const existing = btn.querySelector('.tab-badge-urg');
    if (existing) existing.remove();
    const cnt = (shared.urgente||[]).filter(u => {{
      const cod = typeof u==='string'?u:u.cod;
      return DATASETS[id].some(r=>r.Codigo===cod);
    }}).length;
    if (cnt > 0) btn.insertAdjacentHTML('beforeend',`<span class="tab-badge tab-badge-urg">${{cnt}}</span>`);
  }});
}}

function isUrgente(cod) {{
  return (shared.urgente||[]).some(u=>(typeof u==='string'?u:u.cod)===cod);
}}
function getUrgenteInfo(cod) {{
  return (shared.urgente||[]).find(u=>(typeof u==='string'?u:u.cod)===cod);
}}

function abrirModalUrg() {{
  const art = [...DATASETS['ins'],...DATASETS['cart']].find(r=>r.Codigo===ctxCod);
  document.getElementById('urg-modal-art').textContent = art ? art.Descripcion : ctxCod;
  document.getElementById('urg-quien').value='';
  document.getElementById('urg-cant').value='';
  document.getElementById('urg-modal').classList.add('open');
  setTimeout(()=>document.getElementById('urg-quien').focus(), 60);
}}
function cerrarModalUrg() {{
  document.getElementById('urg-modal').classList.remove('open');
}}
function confirmarUrg() {{
  const quien = document.getElementById('urg-quien').value.trim();
  const cant  = parseInt(document.getElementById('urg-cant').value)||0;
  if (!quien) {{ document.getElementById('urg-quien').focus(); return; }}
  if (cant<=0) {{ document.getElementById('urg-cant').focus(); return; }}
  (shared.urgente=shared.urgente||[]).push({{cod:ctxCod, quien, cantidad:cant, fecha:new Date().toLocaleDateString('es-AR')}});
  shared.desuso = (shared.desuso||[]).filter(c=>c!==ctxCod);
  const artUrg = [...DATASETS['ins'],...DATASETS['cart']].find(r=>r.Codigo===ctxCod);
  addHistorial({{tipo:'urgente', cod:ctxCod, descripcion:artUrg?artUrg.Descripcion:ctxCod, quien, cantidad:cant, nota:''}});
  saveShared({{urgente:shared.urgente, desuso:shared.desuso, historial:shared.historial}});
  cerrarModalUrg();
  S.ins.filtrar(); S.cart.filtrar(); renderDesusoTables(); actualizarBadgesUrgente();
}}

function ctxToggleUrgente() {{
  if (!ctxCod) return;
  if (isUrgente(ctxCod)) {{
    shared.urgente = (shared.urgente||[]).filter(u=>(typeof u==='string'?u:u.cod)!==ctxCod);
    saveShared({{urgente:shared.urgente}});
    S.ins.filtrar(); S.cart.filtrar(); renderDesusoTables(); actualizarBadgesUrgente();
  }} else {{
    abrirModalUrg();
  }}
}}
function ctxToggleDesuso() {{
  if (!ctxCod) return;
  if ((shared.desuso||[]).includes(ctxCod))
    shared.desuso = shared.desuso.filter(c=>c!==ctxCod);
  else {{
    shared.desuso = (shared.desuso||[]);
    shared.desuso.push(ctxCod);
    shared.urgente = (shared.urgente||[]).filter(u=>(typeof u==='string'?u:u.cod)!==ctxCod);
  }}
  saveShared({{ desuso: shared.desuso, urgente: shared.urgente }});
  S.ins.filtrar(); S.cart.filtrar(); renderDesusoTables(); actualizarBadgesUrgente();
}}

// ── Desuso panel ──────────────────────────────────────────────
function toggleDesusoPanel(id) {{
  const el = document.getElementById(id+'-desuso');
  const isOpen = el.classList.toggle('open');
  renderDesusoTables();
  const btn = document.querySelector(`#sec-${{id}} .desuso-toggle`);
  if (btn) btn.textContent = isOpen ? 'Ocultar desuso' : 'Ver desuso';
  if (isOpen) el.scrollIntoView({{behavior:'smooth', block:'nearest'}});
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
        <td><button onclick="shared.desuso=shared.desuso.filter(c=>c!=='${{r.Codigo}}');saveShared({{desuso:shared.desuso}});S.${{id}}.filtrar();renderDesusoTables();actualizarBadgesUrgente();"
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
  if (id==='hist') {{
    document.getElementById('kpi-row').innerHTML='';
    renderHistorial();
  }} else {{
    renderKpis(id);
  }}
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
    <div class="kpi"><div class="kpi-val">${{fmt(k.consumo)}}</div><div class="kpi-label">DEMANDA TOTAL</div><div class="kpi-sub">registrada en el sistema</div></div>
    <div class="kpi"><div class="kpi-val ${{k.sinStock>0?'warn':''}}">${{k.sinStock}}</div><div class="kpi-label">SIN STOCK EN DEPÓSITO</div><div class="kpi-sub">artículos con consumo activo</div></div>
  `;
}}

// ── Pedido realizado ─────────────────────────────────────────
function onChangePedido(e, cod) {{
  if (e.target.checked) {{
    e.target.checked = false;   // revertir — el modal confirma el check
    abrirModalPedido(cod);
  }} else {{
    desmarcarPedido(cod);
  }}
}}

let pedidoCod = null;
function abrirModalPedido(cod) {{
  pedidoCod = cod;
  const art = [...DATASETS['ins'],...DATASETS['cart']].find(r=>r.Codigo===cod);
  document.getElementById('pedido-modal-art').textContent = art ? art.Descripcion : cod;
  const urgInfo = getUrgenteInfo(cod);
  const cantAuto = urgInfo&&typeof urgInfo==='object' ? urgInfo.cantidad
    : (art&&art.ConsumidoDesdeIngreso>=0 ? art.ConsumidoDesdeIngreso : (art?art.Consumido:''));
  document.getElementById('pedido-cant').value = cantAuto||'';
  document.getElementById('pedido-fecha').value = '';
  document.getElementById('pedido-modal').classList.add('open');
  setTimeout(()=>document.getElementById('pedido-cant').focus(), 60);
}}
function cerrarModalPedido() {{
  document.getElementById('pedido-modal').classList.remove('open');
}}
function confirmarPedido() {{
  const cant  = parseInt(document.getElementById('pedido-cant').value)||0;
  const fecha = document.getElementById('pedido-fecha').value;
  if (cant<=0) {{ document.getElementById('pedido-cant').focus(); return; }}
  if (!fecha)  {{ document.getElementById('pedido-fecha').focus(); return; }}
  const art = [...DATASETS['ins'],...DATASETS['cart']].find(r=>r.Codigo===pedidoCod);
  const urgInfo = getUrgenteInfo(pedidoCod);
  (shared.pedido_realizado=shared.pedido_realizado||[]).push(pedidoCod);
  (shared.pedido_info=shared.pedido_info||{{}})[pedidoCod] = {{cantidad:cant, fecha_entrega:fecha}};
  addHistorial({{
    tipo:'pedido', cod:pedidoCod,
    descripcion: art ? art.Descripcion : pedidoCod,
    quien: urgInfo&&typeof urgInfo==='object' ? urgInfo.quien : '',
    cantidad: cant,
    nota: 'Entrega: '+fecha
  }});
  saveShared({{pedido_realizado:shared.pedido_realizado, pedido_info:shared.pedido_info, historial:shared.historial}});
  cerrarModalPedido();
  S.ins.filtrar(); S.cart.filtrar();
  renderPedidoSections();
}}
function desmarcarPedido(cod) {{
  shared.pedido_realizado = (shared.pedido_realizado||[]).filter(c=>c!==cod);
  if (shared.pedido_info) delete shared.pedido_info[cod];
  saveShared({{pedido_realizado:shared.pedido_realizado, pedido_info:shared.pedido_info}});
  S.ins.filtrar(); S.cart.filtrar();
  renderPedidoSections();
}}
document.getElementById('pedido-modal').addEventListener('click', e=>{{ if(e.target===e.currentTarget) cerrarModalPedido(); }});

// ── Pedido por proveedor ──────────────────────────────────────
let pedidoProvData = null;

function onCheckProveedor(e, el, secId) {{
  e.target.checked = false;
  const prov = el.dataset.prov;
  const arts = (DATASETS[secId]||[]).filter(r =>
    r.Proveedor === prov &&
    !(shared.pedido_realizado||[]).includes(r.Codigo) &&
    !(shared.desuso||[]).includes(r.Codigo)
  );
  if (!arts.length) return;
  pedidoProvData = {{ prov, secId, arts }};
  document.getElementById('ppm-prov').textContent = prov;
  document.getElementById('ppm-tbody').innerHTML = arts.map(r => {{
    const cantAuto = r.ConsumidoDesdeIngreso >= 0 ? r.ConsumidoDesdeIngreso : r.Consumido;
    return `<tr>
      <td style="color:#555;font-size:10px">${{r.Codigo}}</td>
      <td>${{r.Descripcion}}</td>
      <td style="text-align:right"><input class="ppm-cant" type="number" min="0" value="${{cantAuto||''}}" id="ppm-c-${{r.Codigo}}"></td>
    </tr>`;
  }}).join('');
  document.getElementById('ppm-fecha').value = '';
  document.getElementById('pedido-prov-modal').classList.add('open');
}}

function cerrarModalPedidoProv() {{
  document.getElementById('pedido-prov-modal').classList.remove('open');
}}

function confirmarPedidoProv() {{
  const fecha = document.getElementById('ppm-fecha').value;
  if (!fecha) {{ document.getElementById('ppm-fecha').focus(); return; }}
  const {{ prov, secId, arts }} = pedidoProvData;
  let count = 0;
  arts.forEach(r => {{
    const cant = parseInt(document.getElementById('ppm-c-'+r.Codigo)?.value)||0;
    if (cant <= 0) return;
    (shared.pedido_realizado = shared.pedido_realizado||[]).push(r.Codigo);
    (shared.pedido_info = shared.pedido_info||{{}})[r.Codigo] = {{ cantidad:cant, fecha_entrega:fecha, proveedor:prov }};
    addHistorial({{ tipo:'pedido', cod:r.Codigo, descripcion:r.Descripcion, quien:'', cantidad:cant, nota:`Entrega: ${{fecha}} · ${{prov}}` }});
    count++;
  }});
  if (!count) {{ cerrarModalPedidoProv(); return; }}
  saveShared({{ pedido_realizado:shared.pedido_realizado, pedido_info:shared.pedido_info, historial:shared.historial }});
  cerrarModalPedidoProv();
  S[secId].filtrar();
  renderPedidoSections();
}}

document.getElementById('pedido-prov-modal').addEventListener('click', e=>{{ if(e.target===e.currentTarget) cerrarModalPedidoProv(); }});

// ── Solicitudes cartones ──────────────────────────────────────
function poblarSelectCartones() {{
  const sel = document.getElementById('sol-art');
  const arts = [...DATASETS['cart']].sort((a,b)=>String(a.Descripcion).localeCompare(String(b.Descripcion),'es'));
  arts.forEach(r => {{
    const o = document.createElement('option');
    o.value = r.Codigo;
    o.textContent = r.Descripcion;
    sel.appendChild(o);
  }});
}}

function agregarSolicitud() {{
  const artSel = document.getElementById('sol-art');
  const cod  = artSel.value.trim();
  const desc = cod ? artSel.options[artSel.selectedIndex].text : '';
  const cant = parseInt(document.getElementById('sol-cant').value) || 0;
  const nota = document.getElementById('sol-nota').value.trim();
  if (!cod || cant <= 0) {{ alert('Seleccioná un cartón y la cantidad.'); return; }}
  (shared.solicitudes_cartones = shared.solicitudes_cartones||[]).push({{
    id: Date.now().toString(),
    descripcion: desc, codigo: cod, cantidad: cant, nota: nota,
    fecha: new Date().toLocaleString('es-AR'), atendido: false
  }});
  addHistorial({{tipo:'carton', cod, descripcion:desc, quien:'', cantidad:cant, nota}});
  saveShared({{ solicitudes_cartones: shared.solicitudes_cartones, historial: shared.historial }});
  artSel.value='';
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

// ── Historial ─────────────────────────────────────────────────
function addHistorial(entry) {{
  entry.id = Date.now().toString() + Math.random().toString(36).slice(2,6);
  entry.fecha = new Date().toLocaleString('es-AR');
  (shared.historial = shared.historial||[]).unshift(entry);
}}

function renderHistorial() {{
  const tipo = document.getElementById('hist-tipo')?.value || '';
  const bus  = (document.getElementById('hist-bus')?.value||'').toLowerCase().trim();
  const tipoCls   = {{urgente:'tipo-urgente',carton:'tipo-carton',pedido:'tipo-pedido',llego:'tipo-llego'}};
  const tipoLabel = {{urgente:'URGENTE',carton:'CARTON',pedido:'PEDIDO',llego:'✓ LLEGÓ'}};
  const hist = (shared.historial||[]).filter(e => {{
    if (tipo && e.tipo!==tipo) return false;
    const hay = s => String(s||'').toLowerCase().includes(bus);
    if (bus && !hay(e.descripcion) && !hay(e.quien) && !hay(e.cod) && !hay(e.nota)) return false;
    return true;
  }});
  document.getElementById('hist-cnt').textContent = hist.length+' registro'+(hist.length!==1?'s':'');
  document.getElementById('hist-tbody').innerHTML = hist.length ? hist.map(e=>`
    <tr>
      <td style="color:#555;font-size:11px;white-space:nowrap">${{e.fecha}}</td>
      <td><span class="tipo-badge ${{tipoCls[e.tipo]||''}}">${{tipoLabel[e.tipo]||e.tipo}}</span></td>
      <td>${{e.descripcion||''}} ${{e.cod?`<span style="color:#333;font-size:10px">${{e.cod}}</span>`:''}}</td>
      <td style="color:#aaa">${{e.quien||'—'}}</td>
      <td class="num" style="color:#aaa">${{e.cantidad||'—'}}</td>
      <td><span contenteditable="true" spellcheck="false"
            style="color:#666;cursor:text;border-radius:3px;padding:1px 5px;min-width:80px;display:inline-block;"
            onblur="editarNotaHist('${{e.id}}',this.textContent)"
          >${{e.nota||''}}</span></td>
      <td><button class="hist-del" onclick="eliminarHistorial('${{e.id}}')">Eliminar</button></td>
    </tr>`).join('') :
    '<tr><td colspan="7" style="color:#333;padding:20px;text-align:center;">Sin registros</td></tr>';
}}
function editarNotaHist(id, nota) {{
  const e = (shared.historial||[]).find(x=>x.id===id);
  if (e) {{ e.nota=nota.trim(); saveShared({{historial:shared.historial}}); }}
}}
function eliminarHistorial(id) {{
  shared.historial = (shared.historial||[]).filter(e=>e.id!==id);
  saveShared({{historial:shared.historial}});
  renderHistorial();
}}
function limpiarHistorialFiltrado() {{
  const tipo = document.getElementById('hist-tipo')?.value||'';
  const bus  = (document.getElementById('hist-bus')?.value||'').toLowerCase().trim();
  if (!tipo && !bus) {{ if(!confirm('¿Eliminar todo el historial?')) return; }}
  shared.historial = (shared.historial||[]).filter(e=>{{
    if(tipo && e.tipo!==tipo) return true;
    const hay=s=>String(s||'').toLowerCase().includes(bus);
    if(bus && !hay(e.descripcion)&&!hay(e.quien)&&!hay(e.cod)&&!hay(e.nota)) return true;
    return false;
  }});
  saveShared({{historial:shared.historial}});
  renderHistorial();
}}

// ── YA PEDIDO section ─────────────────────────────────────────
function renderPedidoSections() {{
  ['ins','cart'].forEach(id => {{
    const rows = DATASETS[id].filter(r =>
      (shared.pedido_realizado||[]).includes(r.Codigo) && !(shared.desuso||[]).includes(r.Codigo)
    );
    const sec = document.getElementById(id+'-pedido');
    if (!sec) return;
    if (rows.length===0) {{ sec.classList.remove('open'); return; }}
    sec.classList.add('open');

    // Agrupar por proveedor
    const byProv = {{}};
    rows.forEach(r => {{
      const p = r.Proveedor || 'SIN PROVEEDOR';
      (byProv[p] = byProv[p]||[]).push(r);
    }});

    let html = '';
    Object.keys(byProv).sort().forEach(prov => {{
      const arts = byProv[prov];
      const provAttr = prov.replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      html += `<div class="pedido-prov-group">
        <div class="pedido-prov-hdr">
          <span class="pedido-prov-hdr-name">▼ ${{prov}} (${{arts.length}})</span>
          <button class="btn-dl-txt" data-prov="${{provAttr}}" data-sec="${{id}}" onclick="downloadPedidoTxt(this)">⬇ DESCARGAR TXT</button>
        </div>
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr>
            <th style="background:#0a1a0a;color:#555;font-size:10px;padding:4px 10px;text-align:left;letter-spacing:.5px;font-weight:700;">CÓDIGO</th>
            <th style="background:#0a1a0a;color:#555;font-size:10px;padding:4px 10px;text-align:left;letter-spacing:.5px;font-weight:700;">DESCRIPCIÓN</th>
            <th style="background:#0a1a0a;color:#555;font-size:10px;padding:4px 10px;text-align:right;letter-spacing:.5px;font-weight:700;">STOCK</th>
            <th style="background:#0a1a0a;color:#555;font-size:10px;padding:4px 10px;text-align:right;letter-spacing:.5px;font-weight:700;">CANT. PEDIDA</th>
            <th style="background:#0a1a0a;color:#555;font-size:10px;padding:4px 10px;text-align:left;letter-spacing:.5px;font-weight:700;">ENTREGA</th>
            <th style="background:#0a1a0a;width:160px;"></th>
          </tr></thead>
          <tbody>`;
      arts.forEach(r => {{
        const info = (shared.pedido_info||{{}})[r.Codigo] || {{}};
        const cant  = info.cantidad !== undefined ? info.cantidad : '—';
        const fecha = info.fecha_entrega ? info.fecha_entrega.split('-').reverse().join('/') : '—';
        html += `<tr>
          <td style="color:#555;font-size:11px;padding:6px 10px;border-bottom:1px solid #0d1a0d;">${{r.Codigo}}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #0d1a0d;font-size:12px;">${{r.Descripcion}}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #0d1a0d;text-align:right;color:#aaa;font-size:12px;">${{r.StockActual}}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #0d1a0d;text-align:right;color:#22c55e;font-weight:700;font-size:12px;">${{cant}}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #0d1a0d;color:#60a5fa;font-weight:700;font-size:12px;">${{fecha}}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #0d1a0d;white-space:nowrap;">
            <button class="btn-llego" onclick="abrirModalLlego('${{r.Codigo}}')">✓ LLEGÓ</button>
            <button class="btn-despedido" onclick="desmarcarPedido('${{r.Codigo}}')">Desmarcar</button>
          </td>
        </tr>`;
      }});
      html += '</tbody></table></div>';
    }});
    document.getElementById(id+'-pedido-content').innerHTML = html;
  }});
}}

function downloadPedidoTxt(btn) {{
  const prov = btn.dataset.prov;
  const secId = btn.dataset.sec;
  const arts = (DATASETS[secId]||[]).filter(r =>
    r.Proveedor === prov && (shared.pedido_realizado||[]).includes(r.Codigo)
  );
  if (!arts.length) return;
  const hoy = new Date().toLocaleDateString('es-AR');
  const sep = '-'.repeat(64);
  const lines = [
    `PEDIDO A: ${{prov}}`,
    `Fecha: ${{hoy}}`,
    '',
    `${{('DESCRIPCIÓN').padEnd(42)}} ${{('CANT').padStart(6)}}   ENTREGA`,
    sep,
    ...arts.map(r => {{
      const info = (shared.pedido_info||{{}})[r.Codigo] || {{}};
      const cant  = String(info.cantidad !== undefined ? info.cantidad : '?').padStart(6);
      const fecha = info.fecha_entrega ? info.fecha_entrega.split('-').reverse().join('/') : '?';
      return `${{r.Descripcion.substring(0,42).padEnd(42)}} ${{cant}}   ${{fecha}}`;
    }}),
    sep,
    `Total artículos: ${{arts.length}}`,
  ];
  const blob = new Blob([lines.join('\\r\\n')], {{type:'text/plain;charset=utf-8'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `pedido_${{prov.replace(/[^a-z0-9áéíóúüñ]/gi,'_').slice(0,30)}}_${{new Date().toISOString().slice(0,10)}}.txt`;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}}

// ── Modal LLEGÓ ───────────────────────────────────────────────
let llegoCod = null;
function abrirModalLlego(cod) {{
  llegoCod = cod;
  const art = [...DATASETS['ins'],...DATASETS['cart']].find(r=>r.Codigo===cod);
  const urgInfo = getUrgenteInfo(cod);
  document.getElementById('llego-modal-art').textContent = art ? art.Descripcion : cod;
  document.getElementById('llego-cant').value = urgInfo&&typeof urgInfo==='object' ? urgInfo.cantidad : '';
  document.getElementById('llego-nota').value = '';
  document.getElementById('llego-modal').classList.add('open');
  setTimeout(()=>document.getElementById('llego-cant').focus(), 60);
}}
function cerrarModalLlego() {{
  document.getElementById('llego-modal').classList.remove('open');
}}
function confirmarLlego() {{
  const cant = parseInt(document.getElementById('llego-cant').value)||0;
  const nota = document.getElementById('llego-nota').value.trim();
  const art = [...DATASETS['ins'],...DATASETS['cart']].find(r=>r.Codigo===llegoCod);
  const urgInfo = getUrgenteInfo(llegoCod);
  addHistorial({{
    tipo:'llego', cod:llegoCod,
    descripcion: art ? art.Descripcion : llegoCod,
    quien: urgInfo&&typeof urgInfo==='object' ? urgInfo.quien : '',
    cantidad: cant, nota
  }});
  shared.pedido_realizado = (shared.pedido_realizado||[]).filter(c=>c!==llegoCod);
  shared.urgente = (shared.urgente||[]).filter(u=>(typeof u==='string'?u:u.cod)!==llegoCod);
  saveShared({{pedido_realizado:shared.pedido_realizado, urgente:shared.urgente, historial:shared.historial}});
  cerrarModalLlego();
  S.ins.filtrar(); S.cart.filtrar();
  renderPedidoSections(); actualizarBadgesUrgente();
}}
document.getElementById('llego-modal').addEventListener('click', e=>{{ if(e.target===e.currentTarget) cerrarModalLlego(); }});

// ── Sección genérica ──────────────────────────────────────────
function makeSection(id) {{
  const data = DATASETS[id];
  let filt = data.filter(r => !shared.desuso.includes(r.Codigo) && !(shared.pedido_realizado||[]).includes(r.Codigo));
  let pg = 1, sortCol = -1, sortDir = 1, verConsumo = false;

  const sel = document.getElementById(id+'-prov');
  (PROVS_MAP[id]||[]).forEach(p => {{
    const o=document.createElement('option'); o.value=p; o.textContent=p; sel.appendChild(o);
  }});

  function filtrar() {{
    const prov = document.getElementById(id+'-prov').value;
    const bus  = document.getElementById(id+'-bus').value.toLowerCase().trim();
    filt = data.filter(r => {{
      if ((shared.desuso||[]).includes(r.Codigo)) return false;
      if ((shared.pedido_realizado||[]).includes(r.Codigo)) return false;
      if (verConsumo && r.Consumido===0) return false;
      if (prov && r.Proveedor!==prov) return false;
      if (bus && !(r.Codigo.toLowerCase().includes(bus)||r.Descripcion.toLowerCase().includes(bus))) return false;
      return true;
    }});
    pg=1; render();
  }}

  function toggleConsumo() {{
    verConsumo = !verConsumo;
    const btn = document.getElementById(id+'-consumo');
    btn.style.background  = verConsumo ? '#22c55e' : '#0a1e0a';
    btn.style.color       = verConsumo ? '#000'    : '#22c55e';
    btn.style.borderColor = verConsumo ? '#22c55e' : '#1a4a1a';
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
    // Urgentes siempre primero
    const display=[...filt].sort((a,b)=>((isUrgente(b.Codigo)?1:0)-(isUrgente(a.Codigo)?1:0)));
    const total=display.length, start=(pg-1)*PG, slice=display.slice(start,start+PG);
    const pages=Math.max(1,Math.ceil(total/PG));
    document.getElementById(id+'-cnt').textContent=total+' artículo'+(total!==1?'s':'')+(total!==data.length?' (filtrado)':'');

    let html='', curProv=null, inUrgSec=false;
    const agrupar=!provFilter;
    slice.forEach(r=>{{
      const isUrg = isUrgente(r.Codigo);
      const urgInfo = isUrg ? getUrgenteInfo(r.Codigo) : null;
      if (isUrg && !inUrgSec) {{
        inUrgSec=true; curProv=null;
        html+=`<tr class="group-row group-urg"><td colspan="9">⚡ URGENTES</td></tr>`;
      }}
      if (!isUrg && inUrgSec) {{ inUrgSec=false; curProv=null; }}
      if(!isUrg && agrupar && r.Proveedor!==curProv){{
        curProv=r.Proveedor;
        const tc=filt.filter(x=>x.Proveedor===curProv&&!isUrgente(x.Codigo)).reduce((s,x)=>s+x.Consumido,0);
        const provPend=filt.filter(x=>x.Proveedor===curProv&&!isUrgente(x.Codigo)&&!(shared.pedido_realizado||[]).includes(x.Codigo)).length>0;
        const provAttr=curProv.replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        const chkHtml=provPend
          ?`<input type="checkbox" onclick="event.stopPropagation()" onchange="onCheckProveedor(event,this,'${{id}}')" data-prov="${{provAttr}}" style="width:14px;height:14px;accent-color:var(--accent);cursor:pointer;flex-shrink:0;">`
          :`<input type="checkbox" disabled style="width:14px;height:14px;opacity:.25;cursor:default;flex-shrink:0;">`;
        html+=`<tr class="group-row"><td colspan="4">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none;">
            ${{chkHtml}} ▼ ${{curProv}}
          </label></td><td class="num">${{fmt(tc)}}</td><td colspan="4"></td></tr>`;
      }}
      const isDone = (shared.pedido_realizado||[]).includes(r.Codigo);
      const sinStock = r.StockActual<=0 && r.Consumido>0;
      const sc = sinStock?'warn-stock':(r.StockActual>0?'ok-stock':'');
      const stmin = (shared.stock_minimo||{{}})[r.Codigo];
      let stminCls = '';
      if (stmin!==undefined && stmin>0) {{
        if (r.StockActual <= stmin) stminCls='stmin-bajo';
        else if (r.StockActual <= stmin*2) stminCls='stmin-alerta';
      }}
      const esCritico = r.StockActual<=0 || (stmin!==undefined && stmin>0 && r.StockActual<stmin);
      const rowCls = isUrg?'urg-row':(isDone?'pedido-done':(esCritico?'row-critico':''));
      const urgLabel = urgInfo && typeof urgInfo==='object'
        ? `${{urgInfo.quien}} · ×${{urgInfo.cantidad}}`
        : 'URGENTE';
      const urgBadge = isUrg?`<span class="badge-urg">⚡ ${{urgLabel}}</span>`:'';
      html+=`<tr data-cod="${{r.Codigo}}" class="${{rowCls}}">
        <td style="font-size:11px;color:#aaa">${{r.Codigo}}</td>
        <td>${{r.Descripcion}}${{urgBadge}}</td>
        <td><span class="unidad-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="unidad">${{r.Unidad}}</span></td>
        <td class="num">${{r.Consumido>0?fmt(r.Consumido):'<span style="color:#444">—</span>'}}</td>
        <td class="num ${{sc}} ${{stminCls}}">${{fmt(r.StockActual)}}</td>
        <td class="num"><span class="stmin-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="stmin"></span></td>
        <td><span class="nota-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="logistica"></span></td>
        <td><span class="nota-cell" contenteditable="true" spellcheck="false" data-cod="${{r.Codigo}}" data-field="compras"></span></td>
        <td style="text-align:center"><input type="checkbox" ${{isDone?'checked':''}} onchange="onChangePedido(event,'${{r.Codigo}}')" style="width:16px;height:16px;accent-color:var(--ok);cursor:pointer;"></td>
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

  return {{filtrar,toggleConsumo,sortBy,render,irPag}};
}}

// ── Init ──────────────────────────────────────────────────────
const S = {{}};
loadShared().then(() => {{
  S.ins  = makeSection('ins');
  S.cart = makeSection('cart');
  renderKpis('ins');
  S.ins.render();
  S.cart.render();
  poblarSelectCartones();
  renderSolicitudes();
  renderPedidoSections();
  actualizarBadgeTab();
  actualizarBadgesUrgente();
  renderDesusoTables();
}});

// ── Auto-refresh: sincronizar cambios de otros usuarios cada 30s ──
setInterval(async () => {{
  const snap = JSON.stringify({{
    u: shared.urgente, d: shared.desuso,
    p: shared.pedido_realizado, pi: shared.pedido_info,
    s: shared.solicitudes_cartones, h: shared.historial
  }});
  try {{
    const r = await fetch(SERVER+'/api/shared', {{signal: AbortSignal.timeout(3000)}});
    const nuevo = await r.json();
    const snap2 = JSON.stringify({{
      u: nuevo.urgente, d: nuevo.desuso,
      p: nuevo.pedido_realizado, pi: nuevo.pedido_info,
      s: nuevo.solicitudes_cartones, h: nuevo.historial
    }});
    if (snap2 === snap) return;   // nada cambió
    Object.assign(shared, nuevo);
    shared.urgente          = shared.urgente          || [];
    shared.desuso           = shared.desuso           || [];
    shared.stock_minimo     = shared.stock_minimo     || {{}};
    shared.pedido_realizado = shared.pedido_realizado || [];
    shared.pedido_info      = shared.pedido_info      || {{}};
    shared.notas            = shared.notas            || {{}};
    shared.solicitudes_cartones = shared.solicitudes_cartones || [];
    shared.historial        = shared.historial        || [];
    if (S.ins)  S.ins.filtrar();
    if (S.cart) S.cart.filtrar();
    renderPedidoSections();
    renderSolicitudes();
    actualizarBadgeTab();
    actualizarBadgesUrgente();
    renderDesusoTables();
    if (tabActual === 'hist') renderHistorial();
  }} catch(e) {{}}   // servidor no disponible, ignorar
}}, 30000);
</script>
</body>
</html>"""

with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"\nReporte guardado: {OUTPUT}")
print(f"  Insumos: {ki['n_arts']} arts | Cartones: {kc['n_arts']} arts")
