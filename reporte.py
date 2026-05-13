import streamlit as st
import pandas as pd
import pyodbc

# 1. Configuración visual del Dashboard
st.set_page_config(page_title="Monitor Dragonfish 2026", layout="wide")

# Estilo personalizado para el título
st.markdown("<h1 style='text-align: center; color: #1F497D;'>📊 Monitor de Mercadería en Tránsito - EN VIVO</h1>", unsafe_allow_html=True)
st.markdown("---")

# --- CONFIGURACIÓN DE CONEXIÓN REVISADA ---
# Intentamos con la IP local y el nombre de la instancia
SERVER = 'marketcentral.ddns.net\ZOOLOGIC,1433' 
DATABASE = 'MARKET'
USER = 'MARKET'
PASSWORD = 'Market202020'

@st.cache_data(ttl=30)
def obtener_datos():
    # Usamos una cadena de conexión más robusta para el driver viejo
    conn_str = (
        f"DRIVER={{SQL Server}};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        f"UID={USER};"
        f"PWD={PASSWORD};"
        "Connect Timeout=5;" # Para que no se quede colgado si falla
    )
    conn = pyodbc.connect(conn_str)
    # ... el resto del código (query, etc) queda igual ...
    conn_str = f'DRIVER={{SQL Server}};SERVER={SERVER};DATABASE={DATABASE};UID={USER};PWD={PASSWORD}'
    conn = pyodbc.connect(conn_str)
    
    # Consulta SQL optimizada para 2026 y rotación
    query = """
    SELECT 
        Articulo as [Código], 
        Descripcion as [Producto], 
        LocalOrigen as [Local Origen],
        SUM(Cantidad) as [Cantidad Total]
    FROM MovimientosDeStockDetalle
    WHERE LocalDestino = 'CENTRAL' 
      AND Motivo = '07' 
      AND YEAR(Fecha) = 2026
    GROUP BY Articulo, Descripcion, LocalOrigen
    ORDER BY [Cantidad Total] DESC
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df

# 3. Lógica de la Interfaz
try:
    df = obtener_datos()

    # --- FILA 1: MÉTRICAS GENERALES ---
    total_unidades = df['Cantidad Total'].sum()
    total_productos_distintos = df['Código'].nunique()
    
    m1, m2, m3 = st.columns(3)
    m1.metric("Unidades Totales (2026)", f"{total_unidades:,.0f}")
    m2.metric("Variedad de Productos", total_productos_distintos)
    m3.metric("Estado de Conexión", "🟢 En Vivo")

    st.markdown("---")

    # --- FILA 2: GRÁFICO Y TABLA ---
    col_izq, col_der = st.columns([1.2, 1])

    with col_izq:
        st.subheader("🏆 Ranking: Lo que más vuelve")
        # Tomamos los 10 que más vuelven para el gráfico
        top_10 = df.groupby('Producto')['Cantidad Total'].sum().nlargest(10)
        st.bar_chart(top_10)

    with col_der:
        st.subheader("📋 Detalle Completo")
        # Mostramos la tabla con filtro de búsqueda incluido por Streamlit
        st.dataframe(df, use_container_width=True, hide_index=True)

    # Botón manual para forzar actualización
    if st.button('🔄 Actualizar Datos Ahora'):
        st.cache_data.clear()
        st.rerun()

except Exception as e:
    st.error(f"⚠️ Error de conexión: {e}")
    st.info("Revisá que el servidor SQL esté encendido y que la instancia 'ZOOLOGIC' sea correcta.")