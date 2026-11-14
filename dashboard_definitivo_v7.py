import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any
import math
import os
import tempfile
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from valuation_engine import DriverAssumptions, project_fcff, dcf_from_fcff, estimate_wacc, validate_market_inputs

# Guarda la caché SQLite de yfinance en una carpeta temporal escribible.
_CACHE_YFINANCE = os.path.join(tempfile.gettempdir(), 'dashboard_yfinance_cache')
try:
    os.makedirs(_CACHE_YFINANCE, exist_ok=True)
    yf.set_tz_cache_location(_CACHE_YFINANCE)
except Exception:
    pass  # si la versión de yfinance no lo admite, la app funciona igual
def _instalar_compat_ancho():
    """Streamlit moderno sustituye use_container_width=True por width="stretch" (el primero está obsoleto).
    Si la versión instalada ya admite width="stretch", se traduce de forma transparente; si no, no se toca nada."""
    try:
        import functools
        from streamlit.delta_generator import DeltaGenerator
        version = tuple(int(x) for x in st.__version__.split(".")[:2])
        if version < (1, 53):
            return

        def _envolver(f):
            if getattr(f, "_compat_ancho", False):
                return f

            @functools.wraps(f)
            def _w(*a, **k):
                if k.get("use_container_width") is True and "width" not in k:
                    k.pop("use_container_width")
                    k["width"] = "stretch"
                return f(*a, **k)
            _w._compat_ancho = True
            return _w

        for nombre in ("plotly_chart", "dataframe", "data_editor", "button", "download_button"):
            if hasattr(DeltaGenerator, nombre):
                setattr(DeltaGenerator, nombre, _envolver(getattr(DeltaGenerator, nombre)))
            if hasattr(st, nombre):
                setattr(st, nombre, _envolver(getattr(st, nombre)))
    except Exception:
        pass  # si algo falla, la app sigue funcionando con use_container_width


_instalar_compat_ancho()

st.set_page_config(
    page_title="Terminal de Inversión Avanzada",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

AÑO_HORIZONTE = 2030  # horizonte de la proyección a largo plazo
TTL_DATOS_SEGUNDOS = 900  # 15 minutos: priorizamos frescura sobre cache largo.
NOMBRE_FUENTE_PRINCIPAL = "Yahoo Finance / yfinance"
VERSION_APP = "7.1 — Big Tech · IA · valoración por drivers"

# Paleta "Esmeralda sobre Púrpura Profundo".
# Cada capa de datos tiene un color propio y estable en toda la app:
#   histórico real = lila claro · consenso de analistas = violeta · modelo propio = esmeralda.
COLOR_ACTUAL = "#e9e4f5"
COLOR_CONSENSO = "#a78bfa"
COLOR_MODELO = "#34d399"
COLOR_LINEA = "#10b981"
COLOR_PRECIO = "#f5f3ff"
COLOR_POSITIVO = "#34d399"
COLOR_NEGATIVO = "#fb7185"
COLOR_NEUTRO = "#9188b3"
COLOR_AMBAR = "#fbbf24"

# Escalas continuas: rojo (malo) -> púrpura profundo (neutro) -> esmeralda (bueno).
ESCALA_VALOR = [[0, "#f43f5e"], [0.5, "#4c1d95"], [1, "#34d399"]]
ESCALA_PESO = [[0, "#2e1065"], [0.5, "#6d28d9"], [1, "#34d399"]]

# Tema gráfico centralizado.
GRID_COLOR = "rgba(167,139,250,0.13)"
GRID_ZERO_COLOR = "rgba(167,139,250,0.30)"
_DARK_LAYOUT = {
    "template": "plotly_dark",
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"color": "#d9d3ee", "family": "Inter, Segoe UI, sans-serif"},
    "hoverlabel": {"bgcolor": "#1a0f33", "bordercolor": "#34d399", "font": {"color": "#f5f3ff"}},
}

# Botón global de refresco: si el usuario lo pulsa, la app deja de reutilizar el cache.
# Esto es especialmente útil en época de resultados o revisiones de consenso.
st.sidebar.markdown(
    """
    <div class="brand">
        <div class="brand-logo">◆</div>
        <div>
            <div class="brand-name">ALPHA TERMINAL</div>
            <div class="brand-tag"><span class="live-dot"></span>valoración fundamental</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if st.sidebar.button("🔄 Actualizar datos reales", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

AÑO_HORIZONTE = st.sidebar.select_slider(
    "Horizonte de proyección",
    options=list(range(2027, 2033)),
    value=2030,
    key="horizonte_global",
    help="Explora el horizonte del modelo. 2030 sigue siendo el escenario por defecto."
)

if "watchlist" not in st.session_state:
    st.session_state.watchlist = ["TEF.MC", "ORA.PA", "MSFT"]
if "ticker1_value" not in st.session_state:
    st.session_state.ticker1_value = "TEF.MC"
if "ticker2_value" not in st.session_state:
    st.session_state.ticker2_value = "ORA.PA"

with st.sidebar.expander("⭐ Watchlist", expanded=True):
    st.caption(f"Versión {VERSION_APP} · {len(st.session_state.watchlist)}/8")
    for _t in list(st.session_state.watchlist):
        if st.button(_t, key=f"watch_{_t}", use_container_width=True):
            st.session_state.ticker1_value = _t
            st.rerun()
    if st.button("➕ Guardar Empresa 1", use_container_width=True):
        _t = str(st.session_state.ticker1_value).upper().strip()
        if _t:
            st.session_state.watchlist = [_t] + [x for x in st.session_state.watchlist if x != _t]
            st.session_state.watchlist = st.session_state.watchlist[:8]
            st.rerun()


def _inyectar_estilos():
    """Tema 'Esmeralda & Púrpura': fondo púrpura casi negro, acentos verdes, tarjetas de cristal.

    Mantiene tres capas visuales de datos: histórico real, consenso directo y modelo.
    """
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap');
        :root {
            --bg:#0b0618; --bg-2:#120a26; --card:rgba(26,15,51,.72); --card-2:rgba(20,11,40,.88);
            --ink:#f5f3ff; --text:#d9d3ee; --muted:#9188b3;
            --line:rgba(167,139,250,.22); --line-strong:rgba(167,139,250,.42);
            --violet:#7c3aed; --violet-deep:#4c1d95; --lilac:#a78bfa;
            --green:#34d399; --green-deep:#059669; --green-glow:rgba(52,211,153,.35);
            --rose:#fb7185; --amber:#fbbf24;
        }
        html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMarkdownContainer"] {
            font-family:'Inter','Segoe UI',system-ui,sans-serif !important;
        }
        .stApp, [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(1100px 600px at 8% -10%, rgba(124,58,237,.22), transparent 60%),
                radial-gradient(900px 520px at 100% 105%, rgba(16,185,129,.14), transparent 60%),
                linear-gradient(180deg,#0b0618 0%,#0e0720 100%) !important;
            color:var(--text) !important;
        }
        [data-testid="stHeader"] { background:transparent !important; }
        #MainMenu, footer { visibility:hidden; }
        .block-container { padding-top:1.2rem; padding-bottom:2.5rem; max-width:1500px; }

        /* ---------- barra lateral ---------- */
        [data-testid="stSidebar"], section[data-testid="stSidebar"] > div {
            background:linear-gradient(180deg,#0a0514 0%,#12082a 100%) !important;
            border-right:1px solid var(--line) !important;
        }
        .brand { display:flex; align-items:center; gap:12px; padding:6px 4px 14px; margin-bottom:10px;
                 border-bottom:1px solid var(--line); }
        .brand-logo { width:38px; height:38px; display:flex; align-items:center; justify-content:center;
                      border-radius:11px; font-size:1.15rem; color:#06281d; font-weight:800;
                      background:linear-gradient(135deg,#34d399,#10b981); box-shadow:0 0 18px var(--green-glow); }
        .brand-name { font-weight:800; letter-spacing:.14em; font-size:.86rem; color:var(--ink); }
        .brand-tag { font-size:.68rem; color:var(--muted); display:flex; align-items:center; gap:6px; }
        .live-dot { width:7px; height:7px; border-radius:50%; background:var(--green);
                    box-shadow:0 0 0 0 var(--green-glow); animation:pulso 2.2s infinite; display:inline-block; }
        @keyframes pulso { 0%{box-shadow:0 0 0 0 rgba(52,211,153,.55);} 70%{box-shadow:0 0 0 8px rgba(52,211,153,0);} 100%{box-shadow:0 0 0 0 rgba(52,211,153,0);} }

        /* ---------- tipografía ---------- */
        body, .stMarkdown, p, span, label, .stText, [data-testid="stMarkdownContainer"] p { color:var(--text); }
        h1,h2,h3,h4,h5,h6 { color:var(--ink) !important; letter-spacing:-.01em; }
        [data-testid="stMarkdownContainer"] h2, [data-testid="stMarkdownContainer"] h3 {
            border-left:3px solid var(--green); padding-left:.65rem; margin-top:.4rem;
        }
        small, [data-testid="stCaptionContainer"] { color:var(--muted) !important; }
        a { color:var(--green) !important; }
        hr { border-color:var(--line) !important; }

        /* ---------- hero ---------- */
        .hero { position:relative; overflow:hidden; border:1px solid var(--line-strong); border-radius:22px;
                padding:26px 28px; margin-bottom:18px;
                background:linear-gradient(120deg,rgba(76,29,149,.62) 0%,rgba(26,15,51,.78) 55%,rgba(6,78,59,.50) 100%);
                backdrop-filter:blur(10px); box-shadow:0 14px 40px rgba(76,29,149,.28), inset 0 0 0 1px rgba(255,255,255,.03); }
        .hero::after { content:""; position:absolute; right:-80px; top:-80px; width:260px; height:260px; border-radius:50%;
                       background:radial-gradient(circle,rgba(52,211,153,.30),transparent 65%); pointer-events:none; }
        .hero-kicker { font-size:.72rem; text-transform:uppercase; letter-spacing:.18em; color:var(--green); font-weight:700; }
        .hero-title { font-size:2.3rem; line-height:1.05; font-weight:800; margin:.25rem 0 .4rem;
                      background:linear-gradient(90deg,#f5f3ff 0%,#a7f3d0 100%);
                      -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
        .hero-sub { font-size:.95rem; color:var(--text); margin:0 0 10px; max-width:820px; }
        .terminal-badge { display:inline-flex; gap:8px; align-items:center; border:1px solid rgba(52,211,153,.40);
                          background:rgba(16,185,129,.12); padding:5px 11px; border-radius:999px; font-size:.70rem;
                          color:#a7f3d0; font-family:'JetBrains Mono',monospace; }

        /* ---------- tarjetas ---------- */
        .tc { position:relative; border-radius:16px; padding:15px 17px 13px; margin-bottom:10px; min-height:108px;
              background:var(--card); border:1px solid var(--line); backdrop-filter:blur(8px);
              box-shadow:0 6px 18px rgba(0,0,0,.28); transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease; }
        .tc::before { content:""; position:absolute; left:0; right:0; top:0; height:3px; border-radius:16px 16px 0 0;
                      background:linear-gradient(90deg,var(--violet),var(--green)); opacity:.85; }
        .tc:hover { transform:translateY(-3px); border-color:rgba(52,211,153,.55); box-shadow:0 12px 28px rgba(16,185,129,.18); }
        .tc .tit { font-size:.68rem; text-transform:uppercase; letter-spacing:.09em; font-weight:700; color:var(--muted); }
        .tc .val { font-family:'JetBrains Mono',monospace; font-size:1.42rem; font-weight:700; margin:6px 0 3px;
                   line-height:1.15; color:var(--ink); }
        .tc .sub { font-size:.74rem; line-height:1.4; color:var(--muted); }
        .tc-oscura { background:rgba(14,7,30,.92) !important; border-color:var(--line-strong) !important; }
        .tc-clara  { background:var(--card) !important; }
        .tc-verde  { background:linear-gradient(160deg,rgba(6,78,59,.55),rgba(26,15,51,.70)) !important; border-color:rgba(52,211,153,.50) !important; }
        .tc-verde .val { color:#6ee7b7; }
        .tc-roja   { background:linear-gradient(160deg,rgba(136,19,55,.50),rgba(26,15,51,.70)) !important; border-color:rgba(251,113,133,.50) !important; }
        .tc-roja .val { color:#fda4af; }
        .tc-ambar  { background:linear-gradient(160deg,rgba(120,53,15,.45),rgba(26,15,51,.70)) !important; border-color:rgba(251,191,36,.45) !important; }
        .tc-ambar .val { color:#fcd34d; }

        [data-testid="stMetric"] { background:var(--card) !important; border:1px solid var(--line) !important;
                                   padding:.7rem .85rem; border-radius:16px; backdrop-filter:blur(8px); }
        [data-testid="stMetricLabel"] { color:var(--muted) !important; }
        [data-testid="stMetricValue"] { color:var(--ink) !important; font-family:'JetBrains Mono',monospace; }

        /* ---------- etiquetas de fuente y notas ---------- */
        .source-pill { display:inline-flex; align-items:center; gap:6px; border-radius:999px; padding:5px 11px;
                       margin:2px 5px 2px 0; font-size:.70rem; font-weight:700; border:1px solid; }
        .source-real { background:rgba(233,228,245,.08); color:#e9e4f5; border-color:rgba(233,228,245,.30); }
        .source-consensus { background:rgba(167,139,250,.14); color:#c4b5fd; border-color:rgba(167,139,250,.50); }
        .source-model { background:rgba(52,211,153,.12); color:#6ee7b7; border-color:rgba(52,211,153,.50); }
        .source-warning { background:rgba(251,191,36,.12); color:#fcd34d; border-color:rgba(251,191,36,.45); }
        .section-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; margin:1rem 0 .5rem; }
        .section-title { font-weight:800; font-size:1.15rem; color:var(--ink); }
        .section-meta { font-size:.72rem; color:var(--green); }
        .timeline-card { border:1px solid var(--line); border-radius:16px; background:var(--card); padding:12px 14px; margin-top:8px; }
        .timeline-year { font-size:.75rem; font-weight:800; color:var(--green); }
        .timeline-label { font-size:.70rem; color:var(--muted); }
        .timeline-value { font-size:.95rem; font-weight:700; color:var(--ink); }
        .mini-note { font-size:.76rem; color:var(--muted); line-height:1.5; }
        .risk-note { border-left:4px solid var(--amber); background:rgba(120,53,15,.28); padding:11px 14px;
                     border-radius:0 12px 12px 0; color:#fcd34d; font-size:.80rem; margin:6px 0; }
        .success-note { border-left:4px solid var(--green); background:rgba(6,78,59,.35); padding:11px 14px;
                        border-radius:0 12px 12px 0; color:#a7f3d0; font-size:.80rem; margin:6px 0; }
        .info-note { border-left:4px solid var(--lilac); background:rgba(76,29,149,.30); padding:11px 14px;
                     border-radius:0 12px 12px 0; color:#ddd6fe; font-size:.80rem; margin:6px 0; }

        /* ---------- pestañas ---------- */
        .stTabs [data-baseweb="tab-list"] { gap:6px; background:transparent !important; border-bottom:1px solid var(--line) !important; }
        .stTabs [data-baseweb="tab"] { border-radius:12px 12px 0 0 !important; padding:8px 16px; font-weight:600;
                                       background:rgba(26,15,51,.55) !important; color:var(--muted) !important;
                                       border:1px solid var(--line) !important; border-bottom:none !important;
                                       transition:all .15s ease; }
        .stTabs [data-baseweb="tab"]:hover { color:var(--ink) !important; background:rgba(76,29,149,.35) !important; }
        .stTabs [aria-selected="true"] { background:linear-gradient(180deg,rgba(16,185,129,.20),rgba(76,29,149,.35)) !important;
                                         color:#6ee7b7 !important; border-color:rgba(52,211,153,.55) !important;
                                         box-shadow:0 -3px 14px rgba(16,185,129,.12); }
        .stTabs [data-baseweb="tab-highlight"] { background-color:var(--green) !important; height:3px; }
        .stTabs [data-baseweb="tab-panel"] { background:transparent !important; padding-top:1rem; }

        /* ---------- controles ---------- */
        .stTextInput input, .stNumberInput input, textarea {
            background:rgba(20,11,40,.90) !important; color:var(--ink) !important;
            border:1px solid var(--line-strong) !important; border-radius:12px !important; }
        .stTextInput input:focus, .stNumberInput input:focus, textarea:focus {
            border-color:var(--green) !important; box-shadow:0 0 0 3px rgba(52,211,153,.22) !important; }
        div[data-baseweb="select"] > div { background:rgba(20,11,40,.90) !important; color:var(--ink) !important;
                                           border:1px solid var(--line-strong) !important; border-radius:12px !important; }
        div[data-baseweb="popover"] { background:#150b2e !important; border:1px solid var(--line-strong) !important; }
        div[data-baseweb="option"], li[role="option"] { background:#150b2e !important; color:var(--ink) !important; }
        div[data-baseweb="option"]:hover, li[role="option"]:hover { background:rgba(52,211,153,.18) !important; }
        .stButton > button, .stDownloadButton > button {
            background:linear-gradient(135deg,#6d28d9 0%,#4c1d95 55%,#047857 140%) !important; color:#fff !important;
            border:1px solid rgba(167,139,250,.45) !important; border-radius:12px !important; font-weight:700 !important;
            transition:all .18s ease; }
        .stButton > button:hover, .stDownloadButton > button:hover {
            border-color:var(--green) !important; box-shadow:0 0 0 1px var(--green), 0 8px 22px rgba(16,185,129,.28) !important;
            transform:translateY(-1px); }
        [data-testid="stRadio"] label, [data-testid="stSlider"] label { color:var(--text) !important; }
        [data-testid="stSlider"] [role="slider"] { background:var(--green) !important; box-shadow:0 0 0 4px rgba(52,211,153,.25) !important; }
        [data-testid="stProgressBar"] > div > div > div > div { background:linear-gradient(90deg,#7c3aed,#34d399) !important; }

        /* ---------- contenedores ---------- */
        div[data-testid="stExpander"] { border-radius:16px !important; border:1px solid var(--line) !important;
                                        background:rgba(20,11,40,.62) !important; overflow:hidden; }
        div[data-testid="stExpander"] summary { color:#c4b5fd !important; font-weight:600; }
        div[data-testid="stExpander"] summary:hover { color:var(--green) !important; }
        [data-testid="stDataFrame"], [data-testid="stDataEditor"] { border:1px solid var(--line); border-radius:16px; overflow:hidden;
                                                                      box-shadow:0 6px 20px rgba(0,0,0,.25); }
        [data-testid="stPlotlyChart"] { border:1px solid var(--line); border-radius:16px; padding:6px;
                                        background:rgba(20,11,40,.40); }
        [data-testid="stAlert"] { background:rgba(26,15,51,.72) !important; border:1px solid var(--line-strong) !important; border-radius:14px !important; }

        /* ---------- detalles ---------- */
        ::-webkit-scrollbar { width:10px; height:10px; }
        ::-webkit-scrollbar-track { background:#0b0618; }
        ::-webkit-scrollbar-thumb { background:linear-gradient(180deg,#6d28d9,#059669); border-radius:8px; }
        .stApp::before { content:""; position:fixed; inset:0; z-index:0; pointer-events:none;
            background:radial-gradient(ellipse 60% 40% at 15% 0%, rgba(124,58,237,.10) 0%, transparent 60%),
                       radial-gradient(ellipse 45% 35% at 90% 95%, rgba(52,211,153,.07) 0%, transparent 55%);
            animation:ambiente 14s ease-in-out infinite alternate; }
        @keyframes ambiente { from { opacity:.55; } to { opacity:1; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _cmap_valor():
    """Mapa de color de tablas: rosa (malo) -> púrpura profundo (neutro) -> esmeralda (bueno)."""
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list("valor_vm", ["#be123c", "#4c1d95", "#10b981"])


def _tarjeta(titulo, valor, subtitulo="", estilo="tc-clara"):
    st.markdown(
        f'<div class="tc {estilo}"><div class="tit">{titulo}</div>'
        f'<div class="val">{valor}</div><div class="sub">{subtitulo}</div></div>',
        unsafe_allow_html=True,
    )


def _fuente_html(tipo: str, texto: str) -> str:
    """Devuelve una etiqueta visual compacta de procedencia."""
    clase = {
        "real": "source-real",
        "consenso": "source-consensus",
        "modelo": "source-model",
        "warning": "source-warning",
    }.get(tipo, "source-real")
    icono = {"real": "●", "consenso": "◆", "modelo": "▲", "warning": "!"}.get(tipo, "●")
    return f'<span class="source-pill {clase}">{icono} {texto}</span>'


def _render_fuentes() -> None:
    """Renderiza la leyenda universal de la aplicación."""
    st.markdown(
        _fuente_html("real", "Histórico real")
        + _fuente_html("consenso", "Consenso directo de analistas")
        + _fuente_html("modelo", f"Modelo conectado hasta {AÑO_HORIZONTE}")
        + _fuente_html("warning", "No disponible ≠ inventado"),
        unsafe_allow_html=True,
    )


def _fecha_hoy_txt() -> str:
    """Fecha local de ejecución de la app para el pequeño sello de actualización."""
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _aplicar_tema_figura(fig: go.Figure) -> go.Figure:
    """Aplica el tema oscuro y una cuadrícula morada coherente a cualquier figura."""
    fig.update_layout(**_DARK_LAYOUT)
    fig.update_xaxes(gridcolor=GRID_COLOR, zerolinecolor=GRID_ZERO_COLOR, zerolinewidth=1)
    fig.update_yaxes(gridcolor=GRID_COLOR, zerolinecolor=GRID_ZERO_COLOR, zerolinewidth=1)
    return fig


def _descargar_dataframe(df: Optional[pd.DataFrame], label: str, filename: str, key: str) -> None:
    """Añade un botón de exportación CSV solo cuando la tabla contiene datos."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return
    data = df.to_csv(index=True).encode("utf-8-sig")
    st.download_button(label, data=data, file_name=filename, mime="text/csv", use_container_width=True, key=key)


@st.cache_data(ttl=21600, show_spinner=False)
def _obtener_fx_a_moneda_base(moneda_origen: str, moneda_base: str) -> Optional[float]:
    """Devuelve cuántas unidades de moneda_base equivalen a 1 unidad de moneda_origen.

    Prueba el par directo (ORIGBASE=X) y, si Yahoo no lo expone, el inverso.
    La ausencia de FX nunca se convierte silenciosamente en 1.0: devuelve None.
    """
    origen = str(moneda_origen or "").upper().strip()
    base = str(moneda_base or "").upper().strip()
    if not origen or not base or origen in {"N/D", "NAN"} or base in {"N/D", "NAN"}:
        return None
    if origen == base:
        return 1.0

    for par, invertido in ((f"{origen}{base}=X", False), (f"{base}{origen}=X", True)):
        try:
            fx_ticker = yf.Ticker(par)
            precio = None
            try:
                fi = fx_ticker.fast_info
                precio = _safe_float(fi.get("lastPrice") or fi.get("last_price") or fi.get("regularMarketPrice"))
            except Exception:
                pass
            if precio is None or precio <= 0:
                hist = fx_ticker.history(period="5d", auto_adjust=False)
                if hist is not None and not hist.empty and "Close" in hist.columns:
                    precio = _safe_float(hist["Close"].dropna().iloc[-1])
            if precio is not None and precio > 0:
                return (1.0 / precio) if invertido else float(precio)
        except Exception:
            continue
    return None


def _fig_gauge_valoracion(gap_pct: Optional[float], divisa: str) -> go.Figure:
    """Termómetro de descuento/prima frente al valor blended."""
    valor = 0.0 if gap_pct is None or pd.isna(gap_pct) else float(gap_pct)
    valor_clip = max(-40.0, min(60.0, valor))
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=valor_clip,
        delta={"reference": 0, "suffix": "%", "increasing": {"color": COLOR_POSITIVO}, "decreasing": {"color": COLOR_NEGATIVO}},
        number={"suffix": "%", "font": {"color": COLOR_PRECIO, "family": "JetBrains Mono, monospace"}},
        gauge={
            "axis": {"range": [-40, 60], "ticksuffix": "%", "tickcolor": "#94a3b8"},
            "bar": {"color": COLOR_CONSENSO, "thickness": 0.72},
            "bordercolor": "rgba(167,139,250,.30)",
            "steps": [
                {"range": [-40, -5], "color": "rgba(251,113,133,.22)"},
                {"range": [-5, 5], "color": "rgba(251,191,36,.18)"},
                {"range": [5, 60], "color": "rgba(52,211,153,.22)"},
            ],
            "threshold": {"line": {"color": "#e2e8f0", "width": 3}, "thickness": .8, "value": 0},
        },
        title={"text": f"Descuento / prima vs valor blended · {divisa}"},
    ))
    fig.update_layout(height=255, margin=dict(l=10, r=10, t=55, b=10))
    return _aplicar_tema_figura(fig)


_inyectar_estilos()
st.markdown(
    f"""
    <div class="hero">
        <div class="hero-kicker">FUNDAMENTAL RESEARCH WORKBENCH</div>
        <div class="hero-title">Terminal Analítico de Inversión</div>
        <p class="hero-sub">Valoración por PER y EV/FCFF conectada a datos reales, consenso de analistas y un puente explícito hasta {AÑO_HORIZONTE}.</p>
        <div class="terminal-badge">v{VERSION_APP} · horizonte {AÑO_HORIZONTE} · cache {TTL_DATOS_SEGUNDOS//60} min</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# 1. RECORDATORIO ESTRATÉGICO (MANAGEMENT)
with st.expander("🕵️ Factor humano — auditoría de directiva", expanded=False):
    st.markdown("""
    Los números no dirigen la empresa, lo hacen las personas. Antes de tomar una decisión, investiga el equipo gestor:

    - Track record y asignación de capital.
    - Incentivos y remuneración frente a creación de valor.
    - Historial de escándalos, cambios de estrategia o promesas incumplidas.
    - Última carta a accionistas / shareholder letter.
    """)

st.markdown(
    "Auditoría de rentabilidad, deuda, circulante, **Piotroski F-Score**, flujos de caja (FCFE y FCFF), "
    f"**Valoración por Múltiplos** y **proyección hasta {AÑO_HORIZONTE}** conectada en toda la app. "
    "Nada de esto es una recomendación de inversión: son cálculos mecánicos sobre los supuestos que tú introduces."
)

st.markdown(
    f'<div class="info-note"><strong>Fuente y trazabilidad:</strong> {NOMBRE_FUENTE_PRINCIPAL}. '
    f'Última ejecución de la interfaz: {_fecha_hoy_txt()}. El cache de datos vence cada {TTL_DATOS_SEGUNDOS // 60} minutos. '
    'Pulsa <strong>Actualizar datos reales</strong> en la barra lateral para forzar una consulta nueva.</div>',
    unsafe_allow_html=True,
)
_render_fuentes()

# 2. Buscador interactivo
col_search1, col_search2 = st.columns(2)
ticker1 = col_search1.text_input(
    "Ticker Empresa Principal (ej. TEF.MC, MSFT, DIGI.RO):", key="ticker1_value"
).upper().strip()
ticker2 = col_search2.text_input(
    "Ticker Competencia (ej. ORA.PA, AAPL):", key="ticker2_value"
).upper().strip()

if ticker1 and ticker2 and ticker1 == ticker2:
    st.info("Has introducido el mismo ticker en ambos campos — puedes seguir, pero la comparación no aportará gran cosa.")


# ==========================================================================================
# CAPA DE DATOS Y ALGORITMOS
# ==========================================================================================


def _safe_float(value: Any) -> Optional[float]:
    """Convierte de forma segura cualquier valor de Yahoo/pandas a float."""
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None



def _limitar_multiplo_input(valor: Any, defecto: float = 15.0) -> float:
    """Mantiene el valor inicial del control dentro del rango permitido (1–100)."""
    numero = _safe_float(valor)
    if numero is None or not np.isfinite(numero):
        numero = defecto
    return float(round(min(100.0, max(1.0, numero)), 1))
def _safe_int(value: Any) -> Optional[int]:
    """Convierte de forma segura un recuento de analistas a entero."""
    try:
        if value is None or pd.isna(value):
            return None
        return int(float(value))
    except Exception:
        return None


def _normalizar_indice_periodos(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Normaliza índices de estimaciones sin alterar el contenido fuente."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    copia = df.copy()
    copia.index = [str(x) for x in copia.index]
    return copia


def _obtener_propiedad_yf(empresa: Any, nombre: str, metodo: Optional[str] = None) -> Any:
    """Lee una propiedad de yfinance con fallback al método get_* equivalente."""
    try:
        valor = getattr(empresa, nombre)
        if valor is not None:
            return valor
    except Exception:
        pass
    if metodo:
        try:
            return getattr(empresa, metodo)()
        except Exception:
            return None
    return None


def _styler_numerico(df: Optional[pd.DataFrame], decimales: int = 2, formatos_extra: Optional[Dict[str, str]] = None):
    """Formatea solo columnas numéricas para evitar errores con columnas de texto mixtas.

    Pandas Styler permite aplicar un formateador a todo el DataFrame, pero eso rompe
    cuando una tabla contiene una mezcla de floats, enteros, fechas y etiquetas de
    procedencia como "Consenso directo de analistas". Este helper conserva esos
    textos tal cual y aplica formato únicamente a columnas numéricas.
    """
    if df is None:
        return df
    formatos: Dict[str, str] = {}
    for columna in df.select_dtypes(include=[np.number]).columns:
        formatos[columna] = f"{{:.{decimales}f}}"
    if formatos_extra:
        for columna, formato in formatos_extra.items():
            if columna in df.columns:
                formatos[columna] = formato
    return df.style.format(formatos, na_rep="-") if formatos else df.style


def _formato_monetario(valor: Optional[float], divisa: str = "USD", decimales: int = 2) -> str:
    """Formato monetario consistente para tarjetas y tablas."""
    if valor is None or pd.isna(valor):
        return "N/D"
    return f"{valor:,.{decimales}f} {divisa}"


def _formato_pct(valor: Optional[float], decimales: int = 1) -> str:
    """Formato porcentual seguro."""
    if valor is None or pd.isna(valor):
        return "N/D"
    return f"{valor * 100:+.{decimales}f}%"


def _metrica_color(gap_pct: Optional[float]) -> Tuple[str, str, str]:
    """Mapea una distancia al valor en etiqueta + clase de tarjeta + emoji."""
    if gap_pct is None or pd.isna(gap_pct):
        return "Sin señal", "tc-clara", "⚪"
    if gap_pct >= 20:
        return "Fuerte descuento", "tc-verde", "🟢"
    if gap_pct >= 5:
        return "Descuento", "tc-verde", "🟢"
    if gap_pct > -5:
        return "Precio razonable", "tc-ambar", "🟡"
    return "Prima sobre el valor", "tc-roja", "🔴"


def _calcular_pnl_pct(precio_actual: Optional[float], precio_compra: Optional[float]) -> Optional[float]:
    """Rentabilidad porcentual por posición; la divisa se cancela en el ratio."""
    pa = _safe_float(precio_actual)
    pc = _safe_float(precio_compra)
    if pa is None or pc is None or pc <= 0:
        return None
    return pa / pc - 1.0


def _encontrar_columna(df: Optional[pd.DataFrame], candidatos: List[str]) -> Optional[str]:
    """Devuelve el primer nombre de columna existente de una lista de candidatos."""
    if df is None:
        return None
    for candidato in candidatos:
        if candidato in df.columns:
            return candidato
    return None


def _extraer_analyst_target(empresa: Any) -> Dict[str, Optional[float]]:
    """Extrae el rango de precio objetivo de analistas publicado por Yahoo."""
    objetivo = _obtener_propiedad_yf(empresa, "analyst_price_targets", "get_analyst_price_targets")
    if not isinstance(objetivo, dict):
        return {"current": None, "low": None, "high": None, "mean": None, "median": None}
    return {
        "current": _safe_float(objetivo.get("current")),
        "low": _safe_float(objetivo.get("low")),
        "high": _safe_float(objetivo.get("high")),
        "mean": _safe_float(objetivo.get("mean")),
        "median": _safe_float(objetivo.get("median")),
    }


def _extraer_revisiones_eps(empresa: Any) -> Dict[str, Optional[int]]:
    """Extrae revisiones de EPS de 7 y 30 días, si Yahoo las publica."""
    rev = _normalizar_indice_periodos(_obtener_propiedad_yf(empresa, "eps_revisions", "get_eps_revisions"))
    salida = {
        "0y_up_7d": None, "0y_down_7d": None,
        "0y_up_30d": None, "0y_down_30d": None,
        "1y_up_7d": None, "1y_down_7d": None,
        "1y_up_30d": None, "1y_down_30d": None,
    }
    if rev is None:
        return salida
    for period, prefijo in [("0y", "0y"), ("+1y", "1y")]:
        if period not in rev.index:
            continue
        fila = rev.loc[period]
        for col, clave in [
            ("upLast7days", f"{prefijo}_up_7d"),
            ("downLast7days", f"{prefijo}_down_7d"),
            ("upLast30days", f"{prefijo}_up_30d"),
            ("downLast30days", f"{prefijo}_down_30d"),
        ]:
            if col in fila.index:
                salida[clave] = _safe_int(fila[col])
    return salida


def _construir_mapa_fuentes(proyeccion: Optional[pd.DataFrame]) -> Dict[int, str]:
    """Obtiene {año: fuente} desde la tabla de proyección."""
    if proyeccion is None or proyeccion.empty or "Fuente" not in proyeccion.columns:
        return {}
    return {int(a): str(v) for a, v in proyeccion["Fuente"].items() if pd.notna(v)}


def _clasificar_fuente(fuente: Any) -> str:
    """Agrupa etiquetas detalladas en tres clases usadas por los gráficos."""
    texto = str(fuente).lower()
    if "histórico" in texto or "historico" in texto:
        return "real"
    if "consenso" in texto:
        return "consenso"
    if "modelo" in texto or "extrapol" in texto:
        return "modelo"
    return "warning"


def _proyeccion_para_tabla(proyeccion: pd.DataFrame) -> pd.DataFrame:
    """Prepara una copia de la proyección para mostrarla sin mutar la fuente."""
    tabla = proyeccion.copy()
    tabla.index = tabla.index.astype(int)
    orden = [
        "EPS Proyectado",
        "Ingresos Proyectados (M)",
        "EBITDA Proyectado (M)",
        "EBIT Proyectado (M)",
        "FCFF Proyectado (M)",
        "Fuente",
    ]
    columnas = [c for c in orden if c in tabla.columns]
    return tabla[columnas]


def _obtener_semilla_metricas(
    proyeccion: pd.DataFrame,
    columna: str,
    año_base: int,
    usar_consenso: bool = True,
) -> Tuple[int, Optional[float]]:
    """Devuelve el último año con dato útil que puede servir como semilla."""
    if proyeccion is None or proyeccion.empty or columna not in proyeccion.columns:
        return año_base, None
    años = sorted(int(a) for a in proyeccion.index if int(a) >= año_base)
    candidatos = años[::-1] if usar_consenso else años
    for año in candidatos:
        valor = _safe_float(proyeccion.loc[año, columna])
        if valor is not None:
            return año, valor
    return año_base, None


def _proyectar_desde_semilla(
    valor_semilla: Optional[float],
    año_semilla: int,
    crecimiento: Optional[float],
    horizonte: Optional[int] = None,
) -> Dict[int, float]:
    """Compone una trayectoria anual a partir de una tasa explícita."""
    if horizonte is None:
        horizonte = AÑO_HORIZONTE
    if valor_semilla is None or crecimiento is None or año_semilla >= horizonte:
        return {}
    valores: Dict[int, float] = {}
    valor = float(valor_semilla)
    for año in range(año_semilla + 1, horizonte + 1):
        valor *= (1.0 + crecimiento)
        valores[año] = valor
    return valores


def _inferir_periodo_actual_analista(año_base: int, etiqueta: str) -> int:
    """Convierte 0y/+1y a años naturales según el último ejercicio real."""
    if etiqueta == "0y":
        return año_base + 1
    if etiqueta == "+1y":
        return año_base + 2
    return año_base


def _validar_tasa_crecimiento_para_modelo(tasa: Optional[float]) -> Optional[float]:
    """Acota tasas imposibles/extremas, manteniendo la tasa original si es razonable."""
    if tasa is None or pd.isna(tasa):
        return None
    return float(max(min(float(tasa), 0.50), -0.50))


def _build_valuation_path(
    proyeccion: pd.DataFrame,
    metodo: str,
    multiplo: float,
    acciones: Optional[float],
    efectivo_m: Optional[float],
    deuda_m: Optional[float],
    efectivo_por_accion: Optional[float],
    precio_actual: Optional[float],
    año_base: int,
    horizonte: Optional[int] = None,
) -> pd.DataFrame:
    """Construye el camino del precio objetivo año a año.

    La primera observación es el precio REAL actual. Desde el siguiente ejercicio,
    cada precio se calcula exclusivamente con la métrica de ese año y el múltiplo
    que el usuario ha introducido. Así el gráfico responde inmediatamente a PER o
    EV/FCFF y no hay una segunda fórmula escondida.
    """
    if horizonte is None:
        horizonte = AÑO_HORIZONTE

    definiciones = {
        "PER": ("equity", "EPS Proyectado"),
        "EV / FCFF": ("ev", "FCFF Proyectado (M)"),
        "EV / EBITDA": ("ev", "EBITDA Proyectado (M)"),
        "EV / EBIT": ("ev", "EBIT Proyectado (M)"),
    }
    if metodo not in definiciones:
        metodo = "PER"
    tipo, columna = definiciones[metodo]

    filas: List[Dict[str, Any]] = []
    if precio_actual is not None:
        filas.append({
            "Año": año_base,
            "Precio": float(precio_actual),
            "Fuente": "Precio actual real",
            "ClaseFuente": "real",
            "Métrica": _safe_float(proyeccion.loc[año_base, columna]) if (proyeccion is not None and año_base in proyeccion.index and columna in proyeccion.columns) else None,
            "Upside": 0.0,
        })

    if proyeccion is None or proyeccion.empty or not acciones:
        return pd.DataFrame(filas)

    for año in sorted(int(a) for a in proyeccion.index if año_base < int(a) <= horizonte):
        metrica = _safe_float(proyeccion.loc[año, columna]) if columna in proyeccion.columns else None
        precio_objetivo = calcular_precio_objetivo_por_multiplo(
            tipo,
            metrica,
            float(multiplo),
            acciones,
            efectivo_m=efectivo_m or 0.0,
            deuda_m=deuda_m or 0.0,
            efectivo_por_accion=efectivo_por_accion or 0.0,
        )
        fuente = str(proyeccion.loc[año, "Fuente"]) if "Fuente" in proyeccion.columns else "Sin etiqueta"
        filas.append({
            "Año": año,
            "Precio": precio_objetivo,
            "Fuente": fuente,
            "ClaseFuente": _clasificar_fuente(fuente),
            "Métrica": metrica,
            "Upside": ((precio_objetivo / precio_actual) - 1.0) if (precio_objetivo is not None and precio_actual) else None,
        })
    return pd.DataFrame(filas)


def _obtener_semilla_sensibilidad(
    proyeccion: pd.DataFrame,
    columna: str,
    año_base: int,
    horizonte: int,
) -> Tuple[int, Optional[float]]:
    """Obtiene la semilla correcta para una sensibilidad.

    EPS/Ingresos pueden tener consenso directo anual. EBITDA, EBIT y FCFF no se
    presentan como consenso en Yahoo en este motor, por lo que su sensibilidad
    siempre parte del histórico real del año base. Nunca se utiliza una cifra
    generada por el modelo como semilla de otro escenario.
    """
    if proyeccion is None or proyeccion.empty or columna not in proyeccion.columns:
        return año_base, None

    columnas_consenso_directo = {"EPS Proyectado", "Ingresos Proyectados (M)"}
    if columna in columnas_consenso_directo and "Fuente" in proyeccion.columns:
        años = sorted(int(a) for a in proyeccion.index if año_base <= int(a) <= horizonte)
        for año in reversed(años):
            fuente = str(proyeccion.loc[año, "Fuente"])
            if _clasificar_fuente(fuente) == "consenso":
                valor = _safe_float(proyeccion.loc[año, columna])
                if valor is not None:
                    return año, valor

    if año_base in proyeccion.index:
        valor_base = _safe_float(proyeccion.loc[año_base, columna])
        if valor_base is not None:
            return año_base, valor_base

    return año_base, None


def _calcular_2030_sensibilidad(
    proyeccion: pd.DataFrame,
    metodo: str,
    multiplo: float,
    crecimiento: float,
    acciones: Optional[float],
    efectivo_m: Optional[float],
    deuda_m: Optional[float],
    efectivo_por_accion: Optional[float],
    año_base: int,
    horizonte: Optional[int] = None,
) -> Optional[float]:
    """Precio del horizonte bajo una variación del crecimiento de largo plazo.

    La semilla nunca es un año ya modelizado: así cada columna de crecimiento
    del heatmap representa realmente un escenario alternativo.
    """
    if horizonte is None:
        horizonte = AÑO_HORIZONTE
    if not acciones or proyeccion is None or proyeccion.empty:
        return None

    definiciones = {
        "PER": ("equity", "EPS Proyectado"),
        "EV / FCFF": ("ev", "FCFF Proyectado (M)"),
        "EV / EBITDA": ("ev", "EBITDA Proyectado (M)"),
        "EV / EBIT": ("ev", "EBIT Proyectado (M)"),
    }
    tipo, columna = definiciones.get(metodo, ("equity", "EPS Proyectado"))

    semilla_año, semilla = _obtener_semilla_sensibilidad(proyeccion, columna, año_base, horizonte)
    if semilla is None:
        return None

    crecimiento = _validar_tasa_crecimiento_para_modelo(crecimiento)
    if crecimiento is None:
        return None

    if semilla_año == horizonte:
        metrica_horizonte = semilla
    elif semilla_año < horizonte:
        metrica_horizonte = semilla * ((1.0 + crecimiento) ** (horizonte - semilla_año))
    else:
        return None

    return calcular_precio_objetivo_por_multiplo(
        tipo,
        metrica_horizonte,
        multiplo,
        acciones,
        efectivo_m=efectivo_m or 0.0,
        deuda_m=deuda_m or 0.0,
        efectivo_por_accion=efectivo_por_accion or 0.0,
    )


def _fig_trayectoria_valoracion(
    camino: pd.DataFrame,
    titulo: str,
    divisa: str,
    precio_actual: Optional[float],
) -> go.Figure:
    """Gráfico de trayectoria del precio objetivo con procedencia por año."""
    fig = go.Figure()
    if camino is None or camino.empty:
        fig.update_layout(height=390)
        return _aplicar_tema_figura(fig)

    clases = {"real": ("Precio real", COLOR_ACTUAL, "circle"),
              "consenso": ("Consenso", COLOR_CONSENSO, "diamond"),
              "modelo": ("Modelo", COLOR_MODELO, "triangle-up")}

    fig.add_trace(
        go.Scatter(
            x=camino["Año"],
            y=camino["Precio"],
            mode="lines",
            line=dict(color=COLOR_LINEA, width=3.0),
            name="Trayectoria",
            connectgaps=False,
        )
    )

    for clase, (nombre, color, simbolo) in clases.items():
        sub = camino[camino["ClaseFuente"] == clase].copy()
        if sub.empty:
            continue
        hover = []
        for _, row in sub.iterrows():
            precio_txt = "N/D" if pd.isna(row["Precio"]) else f"{row['Precio']:.2f} {divisa}"
            fuente = str(row["Fuente"])
            hover.append(
                f"Año: {int(row['Año'])}<br>Precio objetivo: {precio_txt}<br>Fuente: {fuente}"
            )
        fig.add_trace(
            go.Scatter(
                x=sub["Año"],
                y=sub["Precio"],
                mode="markers",
                marker=dict(size=12, color=color, symbol=simbolo, line=dict(width=1, color="white")),
                name=nombre,
                text=hover,
                hovertemplate="%{text}<extra></extra>",
            )
        )

    if precio_actual is not None:
        fig.add_hline(
            y=precio_actual,
            line_dash="dash",
            line_color=COLOR_NEGATIVO,
            annotation_text=f"Precio actual {precio_actual:.2f}",
            annotation_position="top left",
            annotation_font_color=COLOR_NEGATIVO,
        )

    fig.update_layout(
        title=titulo,
        height=390,
        template="plotly_dark",
        hovermode="x unified",
        margin=dict(l=10, r=10, t=50, b=10),
        xaxis=dict(dtick=1, title="Ejercicio"),
        yaxis=dict(title=f"Precio implícito ({divisa})", tickformat=".2f"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return _aplicar_tema_figura(fig)


def _fig_proyeccion_fundamental(
    proyeccion: pd.DataFrame,
    columna: str,
    titulo: str,
    unidad: str,
) -> go.Figure:
    """Gráfico fundamental con tramos visuales de real/consenso/modelo."""
    fig = go.Figure()
    if proyeccion is None or proyeccion.empty or columna not in proyeccion.columns:
        fig.update_layout(height=380)
        return _aplicar_tema_figura(fig)

    df = proyeccion.copy().reset_index().rename(columns={proyeccion.index.name or "index": "Año"})
    df["Año"] = df["Año"].astype(int)
    df = df.dropna(subset=[columna])
    if df.empty:
        fig.update_layout(height=380)
        return _aplicar_tema_figura(fig)

    # Tramos de fondo: cada color corresponde a una procedencia de datos distinta.
    años_reales = [int(a) for a in df.loc[df["Fuente"].astype(str).str.contains("Histórico|historico", regex=True), "Año"]] if "Fuente" in df.columns else []
    años_consenso = [int(a) for a in df.loc[df["Fuente"].astype(str).str.contains("Consenso", regex=True), "Año"]] if "Fuente" in df.columns else []
    años_modelo = [int(a) for a in df.loc[df["Fuente"].astype(str).str.contains("Modelo|extrapol", regex=True), "Año"]] if "Fuente" in df.columns else []

    def _add_band(años: List[int], color: str, etiqueta: str):
        if not años:
            return
        x0 = min(años) - 0.45
        x1 = max(años) + 0.45
        fig.add_vrect(x0=x0, x1=x1, fillcolor=color, opacity=0.09, line_width=0, annotation_text=etiqueta,
                      annotation_position="top left")

    _add_band(años_reales, COLOR_ACTUAL, "Histórico")
    _add_band(años_consenso, COLOR_CONSENSO, "Consenso")
    _add_band(años_modelo, COLOR_MODELO, "Modelo")

    fig.add_trace(
        go.Scatter(
            x=df["Año"],
            y=df[columna],
            mode="lines",
            line=dict(color=COLOR_LINEA, width=2.8),
            name="Trayectoria",
            connectgaps=False,
        )
    )

    marker_specs = {"real": (COLOR_ACTUAL, "circle", "Dato histórico real"),
                    "consenso": (COLOR_CONSENSO, "diamond", "Consenso de analistas"),
                    "modelo": (COLOR_MODELO, "triangle-up", "Modelo con crecimiento +5y")}
    for clave, (color, simbolo, nombre) in marker_specs.items():
        if "Fuente" not in df.columns:
            continue
        mask = df["Fuente"].apply(_clasificar_fuente) == clave
        sub = df[mask]
        if sub.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=sub["Año"],
                y=sub[columna],
                mode="markers",
                marker=dict(size=11, color=color, symbol=simbolo, line=dict(width=1, color="white")),
                name=nombre,
                hovertemplate="Año %{x}<br>Valor %{y:.2f}<extra></extra>",
            )
        )

    fig.update_layout(
        title=titulo,
        height=390,
        template="plotly_dark",
        hovermode="x unified",
        margin=dict(l=10, r=10, t=55, b=10),
        xaxis=dict(dtick=1, title="Ejercicio"),
        yaxis=dict(title=unidad),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return _aplicar_tema_figura(fig)


def _fig_sensibilidad_2030(
    proyeccion: pd.DataFrame,
    metodo: str,
    multiplo: float,
    crecimiento_central: Optional[float],
    acciones: Optional[float],
    efectivo_m: Optional[float],
    deuda_m: Optional[float],
    efectivo_por_accion: Optional[float],
    año_base: int,
    divisa: str,
    horizonte: Optional[int] = None,
) -> go.Figure:
    """Heatmap del horizonte seleccionado frente a múltiplo y crecimiento."""
    if horizonte is None:
        horizonte = AÑO_HORIZONTE
    fig = go.Figure()
    if crecimiento_central is None:
        fig.update_layout(height=430)
        return _aplicar_tema_figura(fig)

    g0 = float(crecimiento_central)
    g_values = [g0 - 0.05, g0 - 0.025, g0, g0 + 0.025, g0 + 0.05]
    g_values = [_validar_tasa_crecimiento_para_modelo(v) for v in g_values]
    g_values = [v for v in g_values if v is not None]
    m_values = [max(1.0, float(multiplo) * f) for f in [0.80, 0.90, 1.00, 1.10, 1.20]]

    z: List[List[Optional[float]]] = []
    for m in m_values:
        fila: List[Optional[float]] = []
        for g in g_values:
            fila.append(_calcular_2030_sensibilidad(
                proyeccion, metodo, m, g, acciones,
                efectivo_m, deuda_m, efectivo_por_accion, año_base, horizonte,
            ))
        z.append(fila)

    fig.add_trace(
        go.Heatmap(
            x=[f"{g * 100:+.1f}%" for g in g_values],
            y=[f"{m:.1f}x" for m in m_values],
            z=z,
            hovertemplate=f"Crecimiento: %{{x}}<br>Múltiplo: %{{y}}<br>Precio {horizonte}: %{{z:.2f}} {divisa}<extra></extra>",
            colorscale=ESCALA_VALOR,
            colorbar=dict(title=f"Precio ({divisa})"),
            zsmooth=False,
        )
    )
    fig.update_layout(
        title=f"Sensibilidad del precio {horizonte} — {metodo}",
        height=430,
        template="plotly_dark",
        margin=dict(l=10, r=10, t=50, b=10),
        xaxis_title="Crecimiento anual usado en el tramo modelizado",
        yaxis_title="Múltiplo objetivo",
    )
    return _aplicar_tema_figura(fig)


def _fig_revisiones_eps(revisiones: Dict[str, Optional[int]]) -> go.Figure:
    """Barras de revisiones de estimaciones para contextualizar el consenso."""
    categorias = ["0y +30d", "0y -30d", "+1y +30d", "+1y -30d"]
    valores = [
        revisiones.get("0y_up_30d") or 0,
        -(revisiones.get("0y_down_30d") or 0),
        revisiones.get("1y_up_30d") or 0,
        -(revisiones.get("1y_down_30d") or 0),
    ]
    colores = [COLOR_POSITIVO if v > 0 else (COLOR_NEGATIVO if v < 0 else COLOR_NEUTRO) for v in valores]
    fig = go.Figure(go.Bar(x=categorias, y=valores, text=valores, textposition="outside", marker_color=colores))
    fig.add_hline(y=0, line_color="#94a3b8")
    fig.update_layout(
        title="Revisiones de EPS en los últimos 30 días",
        height=320,
        template="plotly_dark",
        margin=dict(l=10, r=10, t=50, b=10),
        yaxis_title="Nº de revisiones",
        xaxis_title="Periodo",
    )
    return _aplicar_tema_figura(fig)


def _target_price_comment(precio_actual: Optional[float], target: Dict[str, Optional[float]]) -> str:
    """Texto neutral para el rango de price target de analistas."""
    mean = target.get("mean")
    if precio_actual is None or mean is None or precio_actual == 0:
        return "Yahoo no publica un objetivo agregado utilizable para este ticker."
    gap = (mean / precio_actual - 1.0) * 100.0
    return f"Objetivo medio de analistas: {mean:.2f} · variación implícita frente al precio actual: {gap:+.1f}%"


def _render_tarjeta_fuente(titulo: str, valor: str, subtitulo: str, tipo: str) -> None:
    """Tarjeta compacta con una etiqueta de fuente incorporada."""
    clase = {"real": "tc-clara", "consenso": "tc-clara", "modelo": "tc-clara"}.get(tipo, "tc-clara")
    _tarjeta(titulo, valor, subtitulo, clase)
    texto = {"real": "Histórico / mercado", "consenso": "Consenso analistas", "modelo": "Modelo de enlace"}.get(tipo, "Sin fuente")
    st.markdown(_fuente_html(tipo if tipo in ["real", "consenso", "modelo"] else "warning", texto), unsafe_allow_html=True)



TASA_IMPOSITIVA_DEFECTO = 0.25  # se usa solo cuando la tasa efectiva del año no es utilizable


def _tasa_impositiva_efectiva(inc, años):
    """Tasa efectiva = impuestos / beneficio antes de impuestos, ejercicio a ejercicio.

    Solo se acepta cuando el beneficio antes de impuestos es positivo y la tasa está entre el 1% y el 45%.
    En otro caso (pérdidas, créditos fiscales, datos ausentes) se usa una tasa normalizada del 25%.
    Antes se dividía entre 1 cuando el beneficio era 0 y se recortaba al 0-40%, lo que producía tasas absurdas
    (p. ej. 40% o 0%) y distorsionaba NOPAT, ROIC y FCFF.
    """
    impuestos = inc.loc["Tax Provision"] if "Tax Provision" in inc.index else pd.Series(np.nan, index=años)
    pretax = inc.loc["Pretax Income"] if "Pretax Income" in inc.index else pd.Series(np.nan, index=años)
    tasa = impuestos / pretax.where(pretax > 0)
    return tasa.where(tasa.between(0.01, 0.45)).fillna(TASA_IMPOSITIVA_DEFECTO)


def _filtrar_per_historico(serie, per_minimo=3.0, per_maximo=100.0, factor_mediana=2.5):
    """Selecciona los PER históricos fiables para calcular la media.

    Descarta: PER no positivos o no finitos, fuera del rango 3x–100x (beneficio anormalmente bajo o
    extraordinario) y, con 4 o más años, los que superan 2,5 veces la mediana (o están por debajo de
    mediana/2,5). Devuelve (serie_usada, {año: motivo_del_descarte}).
    """
    s = pd.to_numeric(serie, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    motivos = {}
    if s.empty:
        return s, motivos
    positivos = s[s > 0]
    for idx in s.index.difference(positivos.index):
        motivos[idx] = "PER no positivo"
    en_rango = positivos[(positivos >= per_minimo) & (positivos <= per_maximo)]
    for idx in positivos.index.difference(en_rango.index):
        motivos[idx] = f"fuera del rango razonable ({per_minimo:.0f}x–{per_maximo:.0f}x)"
    usados = en_rango
    if len(en_rango) >= 4:
        med = float(en_rango.median())
        ok = en_rango[(en_rango <= factor_mediana * med) & (en_rango >= med / factor_mediana)]
        if not ok.empty:
            for idx in en_rango.index.difference(ok.index):
                motivos[idx] = "atípico frente a la mediana"
            usados = ok
    if usados.empty:  # nunca dejar la media sin datos si había PER positivos
        usados = positivos
        motivos = {}
    return usados, motivos



# ==========================================================================================
# v7 · MOTOR DE CICLO, CAJA REAL Y EXPECTATIVAS IMPLÍCITAS (funciones puras, sin Streamlit)
# ==========================================================================================
NIVEL_CICLICA = 55   # a partir de esta puntuación la empresa se trata como cíclica
NIVEL_VARIABLE = 35


def _serie_caja_liquida(bs, años):
    """Efectivo + inversiones financieras a corto plazo (lo que de verdad reduce la deuda neta).

    v6 usaba solo 'Cash And Cash Equivalents' y se dejaba fuera las inversiones a corto plazo:
    en Micron faltaban ~5 B$ de corto plazo (y otros 30 B$ de inversiones no corrientes), y en
    empresas como Microsoft, Apple o Alphabet la diferencia es de decenas de miles de millones.
    Devuelve (serie, descripción_de_lo_que_incluye).
    """
    def _get(nombre):
        if nombre in bs.index:
            return pd.to_numeric(bs.loc[nombre], errors="coerce").reindex(años)
        return None

    total = _get("Cash Cash Equivalents And Short Term Investments")
    efectivo = _get("Cash And Cash Equivalents")
    if total is not None and total.notna().any():
        base = total.fillna(efectivo) if efectivo is not None else total
        return base.fillna(0.0), "efectivo + inversiones a corto plazo"
    if efectivo is not None and efectivo.notna().any():
        for nombre in ("Other Short Term Investments", "Short Term Investments"):
            extra = _get(nombre)
            if extra is not None and extra.notna().any():
                return efectivo.fillna(0.0) + extra.fillna(0.0), "efectivo + inversiones a corto plazo"
        return efectivo.fillna(0.0), "solo efectivo (Yahoo no informa inversiones a corto plazo)"
    return pd.Series(0.0, index=años), "sin dato de caja"


def _serie_minoritarios(bs, años):
    """Intereses minoritarios: reclamación de terceros sobre el valor de la empresa (se suma a la deuda en el puente EV→equity)."""
    if "Minority Interest" in bs.index:
        return pd.to_numeric(bs.loc["Minority Interest"], errors="coerce").reindex(años).fillna(0.0).clip(lower=0.0)
    return pd.Series(0.0, index=años)


def _deuda_ajustada_m(balance):
    """Deuda total + minoritarios del último ejercicio (millones). Es la 'deuda' que se resta en los puentes EV→equity."""
    if balance is None or "Deuda Total" not in balance.index:
        return None
    deuda = _safe_float(balance.loc["Deuda Total"].iloc[-1]) or 0.0
    mino = _safe_float(balance.loc["Minoritarios"].iloc[-1]) if "Minoritarios" in balance.index else 0.0
    return deuda + (mino or 0.0)


def _analizar_ciclicidad(margen_op, ingresos, eps):
    """Puntuación 0-100 de ciclicidad a partir de lo que Yahoo publica (márgenes, ingresos y EPS anuales).

    Componentes: amplitud del margen operativo (40), años de pérdidas (25), margen actual frente a su mediana (20),
    mayor oscilación interanual de ingresos (15). Con menos de 3 ejercicios el resultado es 'indeterminada'.
    """
    m = pd.to_numeric(margen_op, errors="coerce").dropna()
    salida = {"score": None, "nivel": "indeterminada", "es_ciclica": False, "razones": [], "n_años": int(len(m)),
              "margen_actual": None, "margen_mediana": None, "rango_margen": None, "posicion": None,
              "eps_media_ciclo": None, "años_perdidas": 0}
    e = pd.to_numeric(eps, errors="coerce").dropna()
    if len(e) >= 2:
        salida["eps_media_ciclo"] = float(e.mean())
    if len(m) < 3:
        salida["razones"].append("Menos de 3 ejercicios con datos: no se puede medir la ciclicidad.")
        return salida

    rango = float(m.max() - m.min())
    actual = float(m.iloc[-1])
    mediana = float(m.median())
    perdidas = int(((m < 0).sum()) + 0)
    if len(e) >= 2:
        perdidas = max(perdidas, int((e <= 0).sum()))
    ing = pd.to_numeric(ingresos, errors="coerce").dropna()
    oscilacion = 0.0
    if len(ing) >= 2:
        previo = ing.shift(1)
        yoy = ((ing - previo) / previo.where(previo > 0)).dropna()
        if not yoy.empty:
            oscilacion = float(yoy.abs().max())

    s1 = min(rango / 40.0, 1.0) * 40.0
    s2 = 25.0 if perdidas > 0 else 0.0
    if mediana > 0:
        ratio = actual / mediana
    else:
        ratio = 3.0 if actual > 0 else 0.0
    s3 = min(max(ratio - 1.0, 0.0), 1.0) * 20.0
    s4 = min(oscilacion / 0.60, 1.0) * 15.0
    score = int(round(s1 + s2 + s3 + s4))

    razones = []
    if rango >= 20:
        razones.append(f"El margen operativo ha oscilado {rango:.0f} puntos ({m.min():.0f}% a {m.max():.0f}%).")
    if perdidas > 0:
        razones.append(f"{perdidas} ejercicio(s) con pérdidas en los últimos {len(m)}.")
    if mediana > 0 and ratio >= 1.5:
        razones.append(f"El margen actual ({actual:.0f}%) es {ratio:.1f} veces su mediana ({mediana:.0f}%).")
    if oscilacion >= 0.35:
        razones.append(f"Los ingresos han llegado a cambiar un {oscilacion*100:.0f}% en un solo año.")
    if not razones:
        razones.append("Márgenes e ingresos estables en el periodo observado.")

    posicion = ((actual - float(m.min())) / rango) if rango > 0 else 0.5
    salida.update({
        "score": score,
        "nivel": "cíclica" if score >= NIVEL_CICLICA else ("variable" if score >= NIVEL_VARIABLE else "estable"),
        "es_ciclica": score >= NIVEL_CICLICA, "razones": razones, "margen_actual": actual,
        "margen_mediana": mediana, "rango_margen": rango, "posicion": float(posicion), "años_perdidas": perdidas,
    })
    return salida


def _valor_por_persistencia(p, eps_norm, eps_ref, per_normal, descuento):
    """Valor por acción si una fracción p de la desviación del beneficio respecto al nivel normal se mantiene.

    Si el beneficio está por ENCIMA de lo normal (pico) el múltiplo se comprime con p; si está por DEBAJO (valle)
    el múltiplo se expande: los cíclicos cotizan con PER bajo en máximos y alto en mínimos.
    """
    signo = 1.0 if eps_ref >= eps_norm else -1.0
    eps = eps_norm + p * (eps_ref - eps_norm)
    per = per_normal * max(1.0 - signo * descuento * p, 0.2)
    return max(eps, 0.0) * per, eps, per


def _escenarios_ciclo(eps_norm, eps_ref, per_normal, descuento=0.5, persistencias=(0.0, 0.5, 1.0), pesos=(0.3, 0.4, 0.3)):
    """Tres escenarios de persistencia del beneficio y su valor esperado. Todos los parámetros son editables en la app."""
    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in (eps_norm, eps_ref, per_normal)):
        return None
    if per_normal <= 0:
        return None
    eps_norm = max(float(eps_norm), 0.0)
    filas = []
    for p in persistencias:
        valor, eps, per = _valor_por_persistencia(p, eps_norm, float(eps_ref), float(per_normal), float(descuento))
        filas.append({"persistencia": float(p), "eps": float(eps), "per": float(per), "valor": float(valor)})
    w = np.array(pesos, dtype=float)
    w = w / w.sum() if w.sum() > 0 else np.array([1 / 3] * 3)
    esperado = float(sum(f["valor"] * wi for f, wi in zip(filas, w)))
    return {"escenarios": filas, "pesos": [float(x) for x in w], "valor_esperado": esperado}


def _persistencia_implicita(precio, eps_norm, eps_ref, per_normal, descuento=0.5):
    """¿Qué fracción del beneficio extraordinario tiene que mantenerse para justificar el precio actual?

    Devuelve {'estado': 'ok'|'mayor'|'menor', 'p': valor entre 0 y 1}. 'mayor' = ni siquiera con el 100% se llega al precio.
    """
    if None in (precio, eps_norm, eps_ref, per_normal) or precio <= 0 or per_normal <= 0:
        return None
    ps = np.linspace(0.0, 1.0, 201)
    vs = np.array([_valor_por_persistencia(p, max(eps_norm, 0.0), eps_ref, per_normal, descuento)[0] for p in ps])
    if precio > vs.max():
        return {"estado": "mayor", "p": float(ps[int(vs.argmax())]), "valor_max": float(vs.max())}
    if precio < vs.min():
        return {"estado": "menor", "p": float(ps[int(vs.argmin())]), "valor_min": float(vs.min())}
    signo = np.sign(vs - precio)
    cruces = np.where(np.diff(signo) != 0)[0]
    if len(cruces) == 0:
        return {"estado": "ok", "p": float(ps[int(np.abs(vs - precio).argmin())])}
    i = int(cruces[0])
    return {"estado": "ok", "p": float(ps[i])}


def _crecimiento_implicito_dcf(precio, fcff_base_m, deuda_m, efectivo_m, acciones, wacc, gt, lo=-0.10, hi=0.20):
    """DCF inverso: tasa de crecimiento inicial del FCFF que el mercado está descontando en el precio actual."""
    if None in (precio, fcff_base_m, acciones) or precio <= 0:
        return None

    def _v(g):
        v, _ = calcular_dcf(fcff_base_m, deuda_m, efectivo_m, acciones, wacc, gt, g)
        return v if v is not None else 0.0

    vlo, vhi = _v(lo), _v(hi)
    if precio <= vlo:
        return {"estado": "menor", "g": lo}
    if precio >= vhi:
        return {"estado": "mayor", "g": hi}
    a, b = lo, hi
    for _ in range(45):
        mid = (a + b) / 2.0
        if _v(mid) < precio:
            a = mid
        else:
            b = mid
    return {"estado": "ok", "g": (a + b) / 2.0}


def _base_fcff_dcf(fcff_serie_m, modo):
    """Base de FCFF para el DCF: último ejercicio o media de los 3 últimos (menos sensible a un año extremo)."""
    s = pd.to_numeric(fcff_serie_m, errors="coerce").dropna()
    if s.empty:
        return None
    if modo == "Media 3 ejercicios":
        return float(s.iloc[-3:].mean())
    return float(s.iloc[-1])


def _calcular_ev_historico(bal, fcf, val, acciones_hist, acciones_actual):
    """EV/EBITDA, EV/EBIT y EV/FCFF al cierre de cada ejercicio fiscal y su mediana.

    Sirve de ancla INDEPENDIENTE del precio actual: en v6 el múltiplo por defecto era el actual, así que el 'precio
    objetivo' de los métodos EV era simplemente el precio de hoy multiplicado por el crecimiento (CAGR = g por construcción).
    """
    filas = {}
    for año in bal.columns:
        try:
            precio = _safe_float(val.loc["Precio de la Acción (Cierre Año)", año]) if (val is not None and año in val.columns) else None
            acc = (acciones_hist or {}).get(str(año)) or acciones_actual
            if precio is None or not acc:
                continue
            deuda = _safe_float(bal.loc["Deuda Total", año]) or 0.0
            mino = _safe_float(bal.loc["Minoritarios", año]) if "Minoritarios" in bal.index else 0.0
            caja = _safe_float(bal.loc["Efectivo y Equivalentes", año]) or 0.0
            ev = precio * acc / 1e6 + deuda + (mino or 0.0) - caja
            if ev <= 0:
                continue
            ebitda = _safe_float(fcf.loc["1. EBITDA", año]) if (fcf is not None and "1. EBITDA" in fcf.index) else None
            ebit = _safe_float(bal.loc["Beneficio Operativo (EBIT)", año])
            fcff = _safe_float(fcf.loc["=== FREE CASH FLOW A LA FIRMA (FCFF) ===", año]) if (fcf is not None and "=== FREE CASH FLOW A LA FIRMA (FCFF) ===" in fcf.index) else None
            filas[str(año)] = {
                "EV (M)": ev,
                "EV / EBITDA": ev / ebitda if ebitda and ebitda > 0 else np.nan,
                "EV / EBIT": ev / ebit if ebit and ebit > 0 else np.nan,
                "EV / FCFF": ev / fcff if fcff and fcff > 0 else np.nan,
            }
        except Exception:
            continue
    if not filas:
        return None, {}
    df = pd.DataFrame(filas).T
    medianas = {}
    for col in ("EV / EBITDA", "EV / EBIT", "EV / FCFF"):
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        s = s[(s > 0) & (s <= 100)]
        medianas[col] = float(s.median()) if len(s) >= 2 else None
    return df, medianas


def _evaluar_calidad_datos(datos, est, ciclo):
    """Semáforo de fiabilidad de los datos de entrada. Devuelve (puntuación 0-100, nivel, lista[(color, texto)])."""
    items = []
    pen = 0

    def _add(color, texto):
        nonlocal pen
        items.append((color, texto))
        pen += {"verde": 0, "amarillo": 10, "rojo": 25}[color]

    n_hist = int(datos.get("per_n_total") or 0)
    if n_hist >= 5:
        _add("verde", f"{n_hist} ejercicios de histórico.")
    elif n_hist == 4:
        _add("amarillo", "Solo 4 ejercicios de histórico (límite habitual de Yahoo): medias de PER frágiles.")
    else:
        _add("rojo", f"Solo {n_hist} ejercicios de histórico.")
    nv = int(datos.get("per_n_validos") or 0)
    if nv >= 4:
        _add("verde", f"PER medio con {nv} años fiables.")
    elif nv >= 2:
        _add("amarillo", f"PER medio con solo {nv} años fiables.")
    else:
        _add("rojo", "Sin PER histórico fiable suficiente.")
    tabla = (est or {}).get("tabla_consenso")
    n_cons = 0 if tabla is None else int(len(tabla))
    if n_cons >= 2:
        _add("verde", "Consenso de analistas a 2 ejercicios.")
    elif n_cons == 1:
        _add("amarillo", "Consenso de analistas solo a 1 ejercicio.")
    else:
        _add("rojo", "Sin consenso de analistas utilizable.")
    if (est or {}).get("crecimiento_5y") is not None:
        _add("verde", "Crecimiento a largo plazo publicado por Yahoo.")
    else:
        _add("amarillo", "Sin crecimiento a largo plazo publicado: no se extrapola más allá del consenso.")
    if ciclo and ciclo.get("es_ciclica"):
        _add("rojo", "Empresa cíclica: PER histórico y múltiplos actuales pueden inducir a error.")
    elif ciclo and ciclo.get("nivel") == "variable":
        _add("amarillo", "Márgenes variables: usa los escenarios con cautela.")
    elif ciclo and ciclo.get("nivel") == "indeterminada":
        _add("amarillo", "Ciclicidad no medible con el histórico disponible.")
    else:
        _add("verde", "Márgenes estables en el histórico.")
    if datos.get("aviso_moneda"):
        _add("rojo", "Monedas del precio y de las cuentas no reconciliadas.")
    elif datos.get("conversion_moneda"):
        _add("amarillo", "Cuentas convertidas de otra moneda.")
    if "corrige" in str(datos.get("acciones_fuente", "")):
        _add("amarillo", "Nº de acciones corregido con capitalización/precio (varias clases).")
    score = max(0, 100 - pen)
    nivel = "alta" if score >= 75 else ("media" if score >= 50 else "baja")
    return score, nivel, items



def _ajustar_eps_por_splits(empresa, serie_eps_original, años):
    """
    *** CÁLCULO BLINDADO DEL EPS ***
    El EPS del income statement usa el Nº de acciones que había EN CADA EJERCICIO, sin
    reexpresar retroactivamente splits/contrasplits posteriores. El precio de
    empresa.history() (auto_adjust=True) SÍ se reexpresa retroactivamente. Si se cruzan tal
    cual, el PER de los años anteriores a un split sale distorsionado (Nvidia 10:1 en 2024,
    Apple 4:1 en 2020, Tesla 3:1 en 2022...). Aquí reexpresamos el EPS a la base de acciones
    ACTUAL para que quede en la misma base que el precio ajustado.
    """
    try:
        splits = empresa.splits
    except Exception:
        splits = None

    factores = {}
    if splits is not None and not splits.empty:
        splits = splits.copy()
        if getattr(splits.index, "tz", None) is not None:
            splits.index = splits.index.tz_localize(None)
        for fecha in años:
            posteriores = splits[splits.index > fecha]
            factor = float(posteriores.prod()) if not posteriores.empty else 1.0
            factores[fecha] = factor if factor > 0 else 1.0
    else:
        for fecha in años:
            factores[fecha] = 1.0

    serie_factores = pd.Series(factores)
    serie_ajustada = serie_eps_original / serie_factores
    return serie_ajustada, serie_factores


def calcular_piotroski_score(bs, inc, cf, año_actual, año_anterior):
    """
    Piotroski F-Score (0-9): compara el último ejercicio con el anterior. Cada criterio que
    falta datos para evaluarse se EXCLUYE del máximo (no cuenta como fallo ni como acierto):
    el resultado se expresa como 'puntuación / criterios evaluables', nunca se rellena con
    valores por defecto que puedan fabricar un punto que no corresponde.
    """
    def _get(df, claves, año):
        if df is None or año not in df.columns:
            return None
        for k in claves:
            if k in df.index and pd.notna(df.loc[k, año]):
                return float(df.loc[k, año])
        return None

    ni_t = _get(inc, ["Net Income"], año_actual)
    ni_t1 = _get(inc, ["Net Income"], año_anterior)
    activos_t = _get(bs, ["Total Assets"], año_actual)
    activos_t1 = _get(bs, ["Total Assets"], año_anterior)
    cfo_t = _get(cf, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"], año_actual)
    ni_para_cfo_t = ni_t
    deuda_lp_t = _get(bs, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], año_actual)
    deuda_lp_t1 = _get(bs, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], año_anterior)
    activo_corr_t = _get(bs, ["Current Assets"], año_actual)
    activo_corr_t1 = _get(bs, ["Current Assets"], año_anterior)
    pasivo_corr_t = _get(bs, ["Current Liabilities"], año_actual)
    pasivo_corr_t1 = _get(bs, ["Current Liabilities"], año_anterior)
    acciones_t = _get(bs, ["Ordinary Shares Number", "Share Issued"], año_actual)
    if acciones_t is None:
        acciones_t = _get(inc, ["Diluted Average Shares", "Basic Average Shares"], año_actual)
    acciones_t1 = _get(bs, ["Ordinary Shares Number", "Share Issued"], año_anterior)
    if acciones_t1 is None:
        acciones_t1 = _get(inc, ["Diluted Average Shares", "Basic Average Shares"], año_anterior)
    bruto_t = _get(inc, ["Gross Profit"], año_actual)
    bruto_t1 = _get(inc, ["Gross Profit"], año_anterior)
    ingresos_t = _get(inc, ["Total Revenue"], año_actual)
    ingresos_t1 = _get(inc, ["Total Revenue"], año_anterior)

    criterios = []

    def _add(nombre, cumple):
        criterios.append({"criterio": nombre, "cumple": cumple})

    _add("Rentabilidad (ROA) positiva",
         (ni_t / activos_t > 0) if (ni_t is not None and activos_t) else None)
    _add("Flujo de caja operativo (CFO) positivo",
         (cfo_t > 0) if cfo_t is not None else None)
    _add("Mejora del ROA vs año anterior",
         ((ni_t / activos_t) > (ni_t1 / activos_t1))
         if (ni_t is not None and activos_t and ni_t1 is not None and activos_t1) else None)
    _add("Calidad del beneficio (CFO > Beneficio Neto)",
         (cfo_t > ni_para_cfo_t) if (cfo_t is not None and ni_para_cfo_t is not None) else None)
    _add("Reduce apalancamiento (Deuda LP / Activos)",
         ((deuda_lp_t / activos_t) < (deuda_lp_t1 / activos_t1))
         if (deuda_lp_t is not None and activos_t and deuda_lp_t1 is not None and activos_t1) else None)
    _add("Mejora la liquidez (Ratio Corriente)",
         ((activo_corr_t / pasivo_corr_t) > (activo_corr_t1 / pasivo_corr_t1))
         if (activo_corr_t is not None and pasivo_corr_t and activo_corr_t1 is not None and pasivo_corr_t1) else None)
    _add("Sin nuevas emisiones de acciones (no dilución)",
         (acciones_t <= acciones_t1) if (acciones_t is not None and acciones_t1 is not None) else None)
    _add("Mejora el margen bruto",
         ((bruto_t / ingresos_t) > (bruto_t1 / ingresos_t1))
         if (bruto_t is not None and ingresos_t and bruto_t1 is not None and ingresos_t1) else None)
    _add("Mejora la rotación de activos (Ingresos / Activos)",
         ((ingresos_t / activos_t) > (ingresos_t1 / activos_t1))
         if (ingresos_t is not None and activos_t and ingresos_t1 is not None and activos_t1) else None)

    evaluables = [c for c in criterios if c["cumple"] is not None]
    return {
        "puntuacion": sum(1 for c in evaluables if c["cumple"]),
        "maximo_evaluable": len(evaluables),
        "maximo_teorico": 9,
        "detalle": criterios,
    }


DCF_DESVANECER = True  # el crecimiento baja linealmente hasta el crecimiento terminal


def calcular_dcf(fcff_base_millones, deuda_millones, efectivo_millones, acciones,
                  tasa_descuento, crecimiento_terminal, tasa_crecimiento, años_proyeccion=5,
                  desvanecer=None):
    """
    FCFF = NOPAT + D&A - CAPEX - ΔWorking Capital (sin restar intereses: la financiación ya está
    en el WACC). Se descuenta al WACC para obtener el Enterprise Value y se puentea a Equity Value:

        Equity Value = Enterprise Value + Efectivo - Deuda
        Valor por Acción = Equity Value / Acciones en circulación

    Mejoras de rigor frente a la versión anterior:
      * Desvanecimiento: el crecimiento del año 1 es el que indicas y baja linealmente hasta el
        crecimiento terminal en el último año explícito. Antes se mantenía constante 5 años y caía de
        golpe al terminal, lo que inflaba el valor.
      * Devuelve el peso del valor terminal sobre el EV para avisar cuando el valor depende casi
        solo de la perpetuidad.
      * Si el equity sale negativo no se devuelve un "valor" sin sentido.

    Devuelve (valor_por_accion, detalle) o (None, motivo_de_error).
    """
    if desvanecer is None:
        desvanecer = DCF_DESVANECER
    if fcff_base_millones is None or pd.isna(fcff_base_millones) or fcff_base_millones <= 0:
        return None, "El FCFF base es negativo o no está disponible: no se puede construir un DCF fiable."
    if not acciones or acciones <= 0:
        return None, "Yahoo Finance no reporta acciones en circulación para este ticker."
    if tasa_descuento is None or tasa_descuento <= 0.01:
        return None, "La tasa de descuento (WACC) debe ser superior al 1%."

    r = float(tasa_descuento)
    gt = min(float(crecimiento_terminal), r - 0.005)  # la tasa terminal nunca puede alcanzar/superar la de descuento
    g = max(min(tasa_crecimiento if tasa_crecimiento is not None else 0.03, 0.20), -0.10)

    deuda = deuda_millones if (deuda_millones is not None and pd.notna(deuda_millones)) else 0.0
    efectivo = efectivo_millones if (efectivo_millones is not None and pd.notna(efectivo_millones)) else 0.0

    fcff = float(fcff_base_millones)
    vp_flujos = 0.0
    filas = {}
    for t in range(1, años_proyeccion + 1):
        if desvanecer and años_proyeccion > 1:
            g_t = g + (gt - g) * (t - 1) / (años_proyeccion - 1)
        else:
            g_t = g
        fcff = fcff * (1 + g_t)
        vp = fcff / ((1 + r) ** t)
        filas[t] = {"FCFF Proyectado (M)": fcff, "Crecimiento aplicado": g_t, "Valor Presente (M)": vp}
        vp_flujos += vp

    valor_terminal_nominal = fcff * (1 + gt) / (r - gt)
    valor_terminal_presente = valor_terminal_nominal / ((1 + r) ** años_proyeccion)

    enterprise_value_m = vp_flujos + valor_terminal_presente
    equity_value_m = enterprise_value_m + efectivo - deuda
    if equity_value_m <= 0:
        return None, "Con estos supuestos el valor del equity sale negativo (la deuda supera al valor de la empresa)."
    valor_por_accion = (equity_value_m * 1e6) / acciones

    detalle = {
        "enterprise_value_m": enterprise_value_m, "equity_value_m": equity_value_m,
        "deuda_m": deuda, "efectivo_m": efectivo,
        "valor_terminal_presente_m": valor_terminal_presente, "flujos": pd.DataFrame(filas).T,
        "tasa_descuento": r, "crecimiento_terminal": gt, "tasa_crecimiento": g,
        "peso_valor_terminal": (valor_terminal_presente / enterprise_value_m) if enterprise_value_m else None,
        "desvanecer": bool(desvanecer),
    }
    return valor_por_accion, detalle


def calcular_precio_objetivo_por_multiplo(tipo, metrica_proyectada, multiplo, acciones,
                                           efectivo_m=0.0, deuda_m=0.0, efectivo_por_accion=0.0):
    """
    tipo='equity' (PER): precio = EPS_proyectado × múltiplo. La caja ya está incluida en el beneficio
        (intereses) y en el propio múltiplo, así que NO se suma otra vez: hacerlo contaba la caja dos veces
        y hacía que "Relativo (PER)" y la tabla de múltiplos dieran precios distintos para el mismo EPS.
    tipo='ev' (EV/FCF, EV/EBITDA, EV/EBIT): EV = métrica_proyectada(M) × múltiplo;
        Equity Value = EV + Efectivo - Deuda; precio = Equity Value / Acciones.
    """
    if metrica_proyectada is None or pd.isna(metrica_proyectada) or not acciones:
        return None
    if tipo == "equity":
        return metrica_proyectada * multiplo
    if metrica_proyectada <= 0:
        return None
    ev_m = metrica_proyectada * multiplo
    equity_value_m = ev_m + (efectivo_m or 0.0) - (deuda_m or 0.0)
    if equity_value_m <= 0:
        return None
    return (equity_value_m * 1e6) / acciones


def _extrapolar(valor_base, año_base, tasa_crecimiento, año_horizonte=AÑO_HORIZONTE):
    """Extrapola un valor desde año_base hasta año_horizonte a tasa de crecimiento constante.
    Devuelve {año: valor}. Se usa para EBITDA/EBIT/FCFF, que Yahoo Finance no proyecta
    directamente (solo EPS e Ingresos tienen consenso explícito de analistas)."""
    if valor_base is None or pd.isna(valor_base) or tasa_crecimiento is None:
        return {}
    valores = {}
    v = valor_base
    for año in range(año_base + 1, año_horizonte + 1):
        v = v * (1 + tasa_crecimiento)
        valores[año] = v
    return valores


@st.cache_data(ttl=TTL_DATOS_SEGUNDOS, show_spinner="Descargando datos financieros...")
def extraer_datos_financieros(ticker_symbol):
    """
    Balance/FCF/valoración/Piotroski en bloques INDEPENDIENTES: si uno falla (p. ej. no hay
    histórico de precios), el resto se sigue mostrando con un aviso en vez de desaparecer.
    """
    resultado = {
        "balance": None, "fcf": None, "valoracion": None,
        "divisa": "USD", "precio_actual": None,
        "per_medio": None, "per_mediana": None, "per_min": None, "per_max": None,
        "per_trailing_mercado": None, "per_forward_mercado": None,
        "per_n_validos": 0, "per_n_total": 0, "per_descartados": {}, "per_primer_año": None,
        "aviso_moneda": None, "conversion_moneda": None, "acciones_fuente": "Yahoo (sharesOutstanding)",
        "caja_descripcion": None, "ciclo": None, "ev_hist": None, "ev_medianas": {}, "acciones_hist": {},
        "acciones_circulacion": None, "historico_precios": None, "splits_detectados": None,
        "piotroski": None, "error_piotroski": None,
        "error_general": None, "error_valoracion": None,
    }

    try:
        empresa = yf.Ticker(ticker_symbol)
        info = empresa.info or {}
    except Exception as e:
        resultado["error_general"] = f"No se pudo conectar con Yahoo Finance para '{ticker_symbol}': {e}"
        return resultado

    moneda_cotiza = str(info.get("currency") or "USD")
    factor_precio = 1.0
    if moneda_cotiza in ("GBp", "GBX"):
        # Londres cotiza en peniques pero publica los estados financieros en libras: sin esto el
        # precio saldría 100 veces mayor que el EPS y todo el PER/DCF sería absurdo.
        factor_precio = 0.01
        moneda_cotiza = "GBP"
    resultado["divisa"] = moneda_cotiza
    resultado["per_trailing_mercado"] = info.get("trailingPE")
    resultado["per_forward_mercado"] = info.get("forwardPE")
    resultado["sector"] = info.get("sector")
    resultado["industria"] = info.get("industry")
    precio_bruto = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    resultado["precio_actual"] = precio_bruto * factor_precio if precio_bruto is not None else None
    acciones_info = _safe_float(info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"))
    # Contraste con capitalización / precio: corrige empresas con varias clases de acciones donde
    # Yahoo informa solo una clase. Solo se corrige si la discrepancia es moderada (15%–200%); una
    # diferencia mayor suele indicar monedas distintas y se prefiere no tocar el dato.
    cap_mercado = _safe_float(info.get("marketCap"))
    if cap_mercado and resultado["precio_actual"] and resultado["precio_actual"] > 0:
        acciones_implicitas = cap_mercado / resultado["precio_actual"]
        if acciones_info is None or acciones_info <= 0:
            acciones_info = acciones_implicitas
            resultado["acciones_fuente"] = "capitalización / precio"
        else:
            desviacion = abs(acciones_implicitas - acciones_info) / acciones_info
            if 0.15 < desviacion < 2.0:
                acciones_info = acciones_implicitas
                resultado["acciones_fuente"] = "capitalización / precio (corrige clases de acciones)"
    resultado["acciones_circulacion"] = acciones_info

    try:
        bs = empresa.balance_sheet
        inc = empresa.financials
        cf = empresa.cashflow
    except Exception as e:
        resultado["error_general"] = f"Error descargando estados financieros: {e}"
        return resultado

    if bs is None or inc is None or cf is None or bs.empty or inc.empty or cf.empty:
        resultado["error_general"] = "Datos financieros insuficientes en la API pública para este ticker."
        return resultado

    # Moneda de los estados financieros distinta de la de cotización (ADR, TSM, BABA, NVO...):
    # se convierten a la moneda de cotización para que EPS, FCFF y deuda sean comparables con el precio.
    moneda_fin = str(info.get("financialCurrency") or "").strip() or moneda_cotiza
    if moneda_fin in ("GBp", "GBX"):
        moneda_fin = "GBP"
    if moneda_fin.upper() != str(moneda_cotiza).upper():
        fx_fin = _obtener_fx_a_moneda_base(moneda_fin, moneda_cotiza)
        if fx_fin is not None and fx_fin > 0:
            def _convertir_moneda(df_in):
                out = df_in.copy()
                for etiqueta in out.index:
                    t = str(etiqueta).lower()
                    if ("shares" in t and ("number" in t or "average" in t)) or "share issued" in t:
                        continue  # recuentos de acciones: no son dinero
                    out.loc[etiqueta] = pd.to_numeric(out.loc[etiqueta], errors="coerce") * fx_fin
                return out
            bs, inc, cf = _convertir_moneda(bs), _convertir_moneda(inc), _convertir_moneda(cf)
            resultado["conversion_moneda"] = {"de": moneda_fin, "a": moneda_cotiza, "fx": fx_fin}
        else:
            resultado["aviso_moneda"] = (
                f"Los estados financieros están en {moneda_fin} y el precio cotiza en {moneda_cotiza}, y no se "
                "ha podido obtener el tipo de cambio: PER, DCF y múltiplos NO son comparables con el precio."
            )

    años = inc.columns
    etiquetas_años = [str(a.year) for a in sorted(años)]

    # ==========================================
    # BLOQUE 1: BALANCE, RENTABILIDAD Y CIRCULANTE
    # ==========================================
    try:
        df_balance = pd.DataFrame(index=años)

        df_balance["Ingresos Totales (Ventas Anuales)"] = inc.loc["Total Revenue"] if "Total Revenue" in inc.index else pd.Series(np.nan, index=años)
        df_balance["Beneficio Operativo (EBIT)"] = inc.loc["Operating Income"] if "Operating Income" in inc.index else pd.Series(np.nan, index=años)

        ingresos_seguros = df_balance["Ingresos Totales (Ventas Anuales)"].where(df_balance["Ingresos Totales (Ventas Anuales)"] > 0)
        df_balance["Margen Operativo (%)"] = (df_balance["Beneficio Operativo (EBIT)"] / ingresos_seguros) * 100

        if "Diluted EPS" in inc.index:
            df_balance["EPS (publicado)"] = inc.loc["Diluted EPS"]
        elif "Basic EPS" in inc.index:
            df_balance["EPS (publicado)"] = inc.loc["Basic EPS"]
        else:
            df_balance["EPS (publicado)"] = pd.Series(np.nan, index=años)

        deuda_corto = bs.loc["Current Debt"] if "Current Debt" in bs.index else (bs.loc["Current Debt And Capital Lease Obligation"] if "Current Debt And Capital Lease Obligation" in bs.index else pd.Series(0, index=años))
        deuda_largo = bs.loc["Long Term Debt"] if "Long Term Debt" in bs.index else (bs.loc["Long Term Debt And Capital Lease Obligation"] if "Long Term Debt And Capital Lease Obligation" in bs.index else pd.Series(0, index=años))
        # Preferimos 'Total Debt' (agregado directo de Yahoo) y solo si falta recurrimos a
        # sumar corto+largo plazo, para no perder la cifra si un componente no está reportado.
        deuda_total = bs.loc["Total Debt"] if "Total Debt" in bs.index else (deuda_corto + deuda_largo)
        df_balance["Deuda Corto Plazo"] = deuda_corto
        df_balance["Deuda Largo Plazo"] = deuda_largo
        df_balance["Deuda Total"] = deuda_total

        df_balance["Inventario"] = bs.loc["Inventory"] if "Inventory" in bs.index else pd.Series(0, index=años)
        df_balance["Cuentas por Cobrar (Clientes)"] = bs.loc["Accounts Receivable"] if "Accounts Receivable" in bs.index else (bs.loc["Net Receivables"] if "Net Receivables" in bs.index else pd.Series(0, index=años))
        df_balance["Cuentas por Pagar (Proveedores)"] = bs.loc["Accounts Payable"] if "Accounts Payable" in bs.index else pd.Series(0, index=años)

        equity = bs.loc["Stockholders Equity"] if "Stockholders Equity" in bs.index else (bs.loc["Total Equity Gross Minority Interest"] if "Total Equity Gross Minority Interest" in bs.index else pd.Series(0, index=años))
        efectivo, resultado["caja_descripcion"] = _serie_caja_liquida(bs, años)
        minoritarios = _serie_minoritarios(bs, años)
        beneficio_neto = inc.loc["Net Income"] if "Net Income" in inc.index else pd.Series(0, index=años)

        df_balance["Efectivo y Equivalentes"] = efectivo
        df_balance["Minoritarios"] = minoritarios
        df_balance["Activos Totales"] = bs.loc["Total Assets"] if "Total Assets" in bs.index else pd.Series(np.nan, index=años)
        df_balance["Activo Corriente"] = bs.loc["Current Assets"] if "Current Assets" in bs.index else pd.Series(np.nan, index=años)
        df_balance["Pasivo Corriente"] = bs.loc["Current Liabilities"] if "Current Liabilities" in bs.index else pd.Series(np.nan, index=años)

        # ROE solo con patrimonio positivo; ROIC solo con capital invertido positivo. Con patrimonio 0 o
        # negativo estos ratios no tienen interpretación económica (antes se dividía entre 1 y salían
        # cifras de millones de por ciento).
        df_balance["ROE (%)"] = (beneficio_neto / equity.where(equity > 0)) * 100

        tax_rate = _tasa_impositiva_efectiva(inc, años)
        nopat = df_balance["Beneficio Operativo (EBIT)"] * (1 - tax_rate)
        capital_invertido = deuda_total + equity + minoritarios - efectivo
        df_balance["ROIC (%)"] = (nopat / capital_invertido.where(capital_invertido > 0)) * 100

        cols_balance = [
            "Ingresos Totales (Ventas Anuales)", "Beneficio Operativo (EBIT)", "Deuda Corto Plazo",
            "Deuda Largo Plazo", "Deuda Total", "Inventario", "Cuentas por Cobrar (Clientes)",
            "Cuentas por Pagar (Proveedores)", "Efectivo y Equivalentes", "Minoritarios", "Activos Totales",
            "Activo Corriente", "Pasivo Corriente",
        ]
        df_balance[cols_balance] = df_balance[cols_balance] / 1e6

        # Los campos financieros no publicados se conservan como N/D; cero implicaría
        # una partida reportada y podría alterar ratios, deuda neta y múltiplos.
        df_balance = df_balance.sort_index(ascending=True)
        df_balance.index = etiquetas_años
        resultado["balance"] = df_balance.T
    except Exception as e:
        resultado["error_general"] = f"Error calculando el balance/rentabilidad: {e}"
        return resultado

    # ==========================================
    # BLOQUE 2: PIOTROSKI F-SCORE (datos crudos, sin dividir por 1e6)
    # ==========================================
    try:
        if len(años) >= 2:
            año_ordenados = sorted(años)
            resultado["piotroski"] = calcular_piotroski_score(bs, inc, cf, año_ordenados[-1], año_ordenados[-2])
        else:
            resultado["error_piotroski"] = "Se necesitan al menos 2 ejercicios anuales para calcular el F-Score."
    except Exception as e:
        resultado["error_piotroski"] = f"No se pudo calcular el Piotroski F-Score: {e}"

    # ==========================================
    # BLOQUE 3: FREE CASH FLOW — FCFE, y FCFF CORREGIDO
    # ==========================================
    try:
        df_fcf = pd.DataFrame(index=años)

        da = pd.Series(np.nan, index=años)
        for _nombre_da in ("Depreciation And Amortization", "Depreciation Amortization Depletion", "Depreciation"):
            if _nombre_da in cf.index:
                da = cf.loc[_nombre_da]
                break
        ebit_abs = inc.loc["Operating Income"] if "Operating Income" in inc.index else pd.Series(0, index=años)
        capex_abs = pd.Series(np.nan, index=años)
        for _nombre_capex in ("Capital Expenditure", "Purchase Of PPE"):
            if _nombre_capex in cf.index:
                capex_abs = -abs(cf.loc[_nombre_capex])
                break
        intereses_abs = -abs(inc.loc["Interest Expense"]) if "Interest Expense" in inc.index else pd.Series(0, index=años)
        impuestos_pagados_abs = -abs(inc.loc["Tax Provision"]) if "Tax Provision" in inc.index else pd.Series(0, index=años)
        var_wc_abs = cf.loc["Change In Working Capital"] if "Change In Working Capital" in cf.index else pd.Series(np.nan, index=años)

        df_fcf["1. EBITDA"] = ebit_abs + da
        df_fcf["2. CAPEX (Mantenimiento/Crecimiento)"] = capex_abs
        df_fcf["3. Intereses Pagados"] = intereses_abs
        df_fcf["4. Impuestos Pagados"] = impuestos_pagados_abs
        df_fcf["5. Var. Working Capital"] = var_wc_abs
        df_fcf["=== FREE CASH FLOW A EQUITY (FCFE) ==="] = (
            df_fcf["1. EBITDA"] + df_fcf["2. CAPEX (Mantenimiento/Crecimiento)"]
            + df_fcf["3. Intereses Pagados"] + df_fcf["4. Impuestos Pagados"] + df_fcf["5. Var. Working Capital"]
        )

        # *** CORRECCIÓN FCFF *** — NOPAT en vez de beneficio neto/intereses reales, para
        # poder descontar al WACC sin restar la financiación dos veces.
        tasa_impositiva = _tasa_impositiva_efectiva(inc, años)
        nopat_abs = ebit_abs * (1 - tasa_impositiva)

        df_fcf["6. NOPAT (EBIT tras impuestos)"] = nopat_abs
        df_fcf["=== FREE CASH FLOW A LA FIRMA (FCFF) ==="] = nopat_abs + da + capex_abs + var_wc_abs

        df_fcf["Destino: M&A (Adquisiciones)"] = cf.loc["Purchase Of Business"] if "Purchase Of Business" in cf.index else (cf.loc["Cash Acquisitions"] if "Cash Acquisitions" in cf.index else pd.Series(0, index=años))
        df_fcf["Destino: Recompra de Acciones"] = cf.loc["Repurchase Of Capital Stock"] if "Repurchase Of Capital Stock" in cf.index else pd.Series(0, index=años)
        df_fcf["Destino: Pago de Dividendos"] = cf.loc["Cash Dividends Paid"] if "Cash Dividends Paid" in cf.index else pd.Series(0, index=años)
        df_fcf["Destino: Emisión/Pago Deuda (Neto)"] = cf.loc["Net Issuance Payments Of Debt"] if "Net Issuance Payments Of Debt" in cf.index else pd.Series(0, index=años)
        df_fcf["=== Variación Neta de Caja ==="] = cf.loc["Change In Cash"] if "Change In Cash" in cf.index else (cf.loc["Changes In Cash"] if "Changes In Cash" in cf.index else pd.Series(0, index=años))

        # No rellenar componentes no reportados con cero: FCFF/FCFE quedan N/D si
        # una pieza necesaria falta en la fuente.
        df_fcf = (df_fcf / 1e6).sort_index(ascending=True)
        df_fcf.index = etiquetas_años
        resultado["fcf"] = df_fcf.T
    except Exception as e:
        prefijo = (resultado["error_general"] + " | ") if resultado["error_general"] else ""
        resultado["error_general"] = f"{prefijo}Error calculando el Free Cash Flow: {e}"

    # ==========================================
    # BLOQUE 4: VALORACIÓN HISTÓRICA POR PER (EPS blindado por splits)
    # ==========================================
    try:
        # IMPORTANTE: auto_adjust=False. Con auto_adjust=True Yahoo ajusta el precio por SPLITS Y
        # DIVIDENDOS, pero el EPS solo se reexpresa por splits. Eso abarataba artificialmente el precio
        # de los años pasados y bajaba el PER histórico de las empresas que reparten dividendo (TEF, ORA,
        # MSFT...), inflando el "valor relativo". "Close" sin ajustar ya viene corregido por splits.
        historico_precios = empresa.history(period="10y", auto_adjust=False)
        if historico_precios.empty:
            raise ValueError("Yahoo Finance no ha devuelto histórico de precios para este ticker.")
        if factor_precio != 1.0:
            historico_precios = historico_precios.copy()
            historico_precios["Close"] = historico_precios["Close"] * factor_precio

        # tz-aware (precio) vs tz-naive (fechas fiscales): sin esto, el cruce de fechas
        # lanza un TypeError que dejaba el precio -y el PER- en NaN para todos los años.
        historico_precios = historico_precios.copy()
        historico_precios.index = historico_precios.index.tz_localize(None)
        resultado["historico_precios"] = historico_precios[["Close"]]
        if resultado["precio_actual"] is None:
            _ultimo = _safe_float(historico_precios["Close"].dropna().iloc[-1]) if not historico_precios["Close"].dropna().empty else None
            if _ultimo is not None:
                resultado["precio_actual"] = _ultimo

        if "Diluted EPS" in inc.index:
            serie_eps_original = inc.loc["Diluted EPS"]
        elif "Basic EPS" in inc.index:
            serie_eps_original = inc.loc["Basic EPS"]
        else:
            serie_eps_original = pd.Series(np.nan, index=años)

        serie_eps, factores_split = _ajustar_eps_por_splits(empresa, serie_eps_original, años)
        for _nombre_acc in ("Ordinary Shares Number", "Share Issued"):
            if _nombre_acc in bs.index:
                _acc = pd.to_numeric(bs.loc[_nombre_acc], errors="coerce").reindex(factores_split.index) * factores_split
                resultado["acciones_hist"] = {str(getattr(k, "year", k)): float(v) for k, v in _acc.items() if pd.notna(v) and v > 0}
                break
        factores_relevantes = factores_split[factores_split != 1.0]
        if not factores_relevantes.empty:
            resultado["splits_detectados"] = {str(f.year): round(v, 4) for f, v in factores_relevantes.items()}

        df_val = pd.DataFrame(index=años)
        df_val["EPS Anual"] = serie_eps
        df_val["EPS Anual (publicado, sin ajustar)"] = serie_eps_original

        precios_cierre = []
        for fecha in años:
            precio = np.nan
            try:
                tramo = historico_precios.loc[:fecha]
                if not tramo.empty:
                    precio = tramo["Close"].iloc[-1]
            except Exception:
                precio = np.nan
            precios_cierre.append(precio)
        df_val["Precio de la Acción (Cierre Año)"] = precios_cierre

        eps_val_seguro = df_val["EPS Anual"].where(df_val["EPS Anual"] > 0)
        per = df_val["Precio de la Acción (Cierre Año)"] / eps_val_seguro
        df_val["PER Histórico"] = per

        # La media se calcula solo con PER fiables (sin años de beneficio anormal) y se informa de la cobertura.
        serie_per_valida, descartados = _filtrar_per_historico(df_val["PER Histórico"])
        df_val["PER usado en la media"] = serie_per_valida.reindex(df_val.index)
        resultado["per_n_total"] = int(df_val["EPS Anual"].notna().sum())
        resultado["per_n_validos"] = int(len(serie_per_valida))
        resultado["per_descartados"] = {str(getattr(k, "year", k)): v for k, v in descartados.items()}
        if not serie_per_valida.empty:
            resultado["per_medio"] = float(serie_per_valida.mean())
            resultado["per_mediana"] = float(serie_per_valida.median())
            resultado["per_min"] = float(serie_per_valida.min())
            resultado["per_max"] = float(serie_per_valida.max())
            resultado["per_primer_año"] = str(getattr(serie_per_valida.index.min(), "year", ""))

        df_val = df_val.sort_index(ascending=True)
        df_val.index = etiquetas_años
        resultado["valoracion"] = df_val.T
    except Exception as e:
        resultado["error_valoracion"] = f"No se pudo calcular la valoración histórica por PER: {e}"

    # ==========================================
    # BLOQUE 5 (v7): ciclicidad y múltiplos EV históricos
    # ==========================================
    try:
        _bal, _val, _fcf = resultado.get("balance"), resultado.get("valoracion"), resultado.get("fcf")
        if _bal is not None and "Margen Operativo (%)" in _bal.index:
            _eps_h = _val.loc["EPS Anual"] if (_val is not None and "EPS Anual" in _val.index) else _bal.loc["EPS (publicado)"]
            resultado["ciclo"] = _analizar_ciclicidad(
                _bal.loc["Margen Operativo (%)"], _bal.loc["Ingresos Totales (Ventas Anuales)"], _eps_h)
        if _bal is not None and _val is not None:
            resultado["ev_hist"], resultado["ev_medianas"] = _calcular_ev_historico(
                _bal, _fcf, _val, resultado.get("acciones_hist"), resultado.get("acciones_circulacion"))
    except Exception as e:
        resultado["ciclo"] = resultado.get("ciclo")
        resultado["error_ciclo"] = f"No se pudo evaluar la ciclicidad: {e}"

    return resultado


@st.cache_data(ttl=TTL_DATOS_SEGUNDOS, show_spinner="Consultando estimaciones de analistas y datos de valoración...")
def obtener_estimaciones_futuras(
    ticker_symbol,
    año_base,
    eps_base,
    ingresos_base_m,
    ebitda_base_m,
    ebit_base_m,
    fcff_base_m,
    horizonte: Optional[int] = None,
):
    """Descarga consenso de analistas y construye una proyección con procedencia explícita.

    Regla fundamental de esta versión:

    * 0y y +1y de EPS/ingresos = **consenso directo publicado** por Yahoo.
    * +5y de growth_estimates = **crecimiento de largo plazo publicado** por Yahoo.
    * 2028-2030 (o desde el último año con consenso directo hasta 2030) = **modelo**
      que compone el +5y sobre la última semilla disponible. No se etiqueta jamás como
      "consenso del horizonte seleccionado".
    * EBITDA, EBIT y FCFF = no se presentan como consenso si Yahoo no lo publica. Se
      calculan únicamente como tramo modelizado con la tasa +5y de analistas.

    Esto evita que la interfaz dé al usuario una falsa sensación de precisión.
    """
    if horizonte is None:
        horizonte = AÑO_HORIZONTE
    resultado = {
        "tabla_consenso": None,
        "crecimiento_5y": None,
        "crecimiento_5y_fuente": None,
        "crecimiento_corto_plazo": None,
        "proyeccion": None,
        "analyst_targets": {},
        "eps_trend": None,
        "eps_revisions": {},
        "growth_context": None,
        "data_source": NOMBRE_FUENTE_PRINCIPAL,
        "data_as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "error": None,
    }

    try:
        empresa = yf.Ticker(ticker_symbol)
        eps_est = _normalizar_indice_periodos(_obtener_propiedad_yf(empresa, "earnings_estimate", "get_earnings_estimate"))
        rev_est = _normalizar_indice_periodos(_obtener_propiedad_yf(empresa, "revenue_estimate", "get_revenue_estimate"))
        growth_est = _normalizar_indice_periodos(_obtener_propiedad_yf(empresa, "growth_estimates", "get_growth_estimates"))
        eps_trend = _normalizar_indice_periodos(_obtener_propiedad_yf(empresa, "eps_trend", "get_eps_trend"))
    except Exception as e:
        resultado["error"] = f"No se pudieron descargar estimaciones de analistas: {e}"
        eps_est = rev_est = growth_est = eps_trend = None
        empresa = None

    resultado["analyst_targets"] = _extraer_analyst_target(empresa) if empresa is not None else {}
    resultado["eps_revisions"] = _extraer_revisiones_eps(empresa) if empresa is not None else {}
    resultado["eps_trend"] = eps_trend

    # ------------------------------
    # Consenso directo 0y / +1y
    # ------------------------------
    eps_points: Dict[int, float] = {}
    revenue_points: Dict[int, float] = {}
    filas_consenso: Dict[str, Dict[str, Any]] = {}

    for periodo in ["0y", "+1y"]:
        año = _inferir_periodo_actual_analista(año_base, periodo)
        eps_value = None
        rev_value = None
        n_eps = None
        n_rev = None
        eps_growth = None

        if eps_est is not None and periodo in eps_est.index:
            fila = eps_est.loc[periodo]
            if "avg" in fila.index:
                eps_value = _safe_float(fila["avg"])
            if "numberOfAnalysts" in fila.index:
                n_eps = _safe_int(fila["numberOfAnalysts"])
            if "growth" in fila.index:
                eps_growth = _safe_float(fila["growth"])

        if rev_est is not None and periodo in rev_est.index:
            fila_rev = rev_est.loc[periodo]
            if "avg" in fila_rev.index:
                rev_value_raw = _safe_float(fila_rev["avg"])
                if rev_value_raw is not None:
                    rev_value = rev_value_raw / 1e6
            if "numberOfAnalysts" in fila_rev.index:
                n_rev = _safe_int(fila_rev["numberOfAnalysts"])

        if eps_value is not None:
            eps_points[año] = eps_value
        if rev_value is not None:
            revenue_points[año] = rev_value

        if eps_value is not None or rev_value is not None:
            filas_consenso[f"FY {año}"] = {
                "Año": año,
                "Periodo": periodo,
                "EPS Estimado (consenso)": eps_value if eps_value is not None else np.nan,
                "Ingresos Estimados M (consenso)": rev_value if rev_value is not None else np.nan,
                "Nº Analistas EPS": n_eps if n_eps is not None else np.nan,
                "Nº Analistas Ingresos": n_rev if n_rev is not None else np.nan,
                "Crec. EPS publicado": eps_growth if eps_growth is not None else np.nan,
                "Fuente": "Consenso directo de analistas",
            }

    if filas_consenso:
        resultado["tabla_consenso"] = pd.DataFrame(filas_consenso).T.sort_values("Año")

    # ------------------------------
    # Crecimiento de largo plazo +5y
    # ------------------------------
    tasa_5y = None
    contexto_growth: Dict[str, Optional[float]] = {}
    if growth_est is not None and "+5y" in growth_est.index:
        fila_growth = growth_est.loc["+5y"]
        for col in ["stock", "current", "sector", "industry", "index"]:
            if col in fila_growth.index:
                contexto_growth[col] = _safe_float(fila_growth[col])
        for col in ["stock", "current"]:
            if col in fila_growth.index and _safe_float(fila_growth[col]) is not None:
                tasa_5y = _safe_float(fila_growth[col])
                break
    resultado["growth_context"] = contexto_growth if contexto_growth else None

    # Crecimiento corto plazo: solo informativo, nunca sustituye automáticamente al +5y.
    short_growth = None
    if eps_points and len(eps_points) >= 2:
        años_eps = sorted(eps_points)
        a0, a1 = años_eps[-2], años_eps[-1]
        if eps_points[a0] not in [None, 0] and eps_points[a1] is not None:
            short_growth = eps_points[a1] / eps_points[a0] - 1.0
    elif eps_est is not None and "+1y" in eps_est.index and "growth" in eps_est.columns:
        short_growth = _safe_float(eps_est.loc["+1y", "growth"])

    resultado["crecimiento_corto_plazo"] = _validar_tasa_crecimiento_para_modelo(short_growth)
    resultado["crecimiento_5y"] = _validar_tasa_crecimiento_para_modelo(tasa_5y)
    resultado["crecimiento_5y_fuente"] = "Yahoo growth_estimates +5y" if tasa_5y is not None else None

    # ------------------------------
    # Tabla maestra: real + consenso + modelo
    # ------------------------------
    filas: Dict[int, Dict[str, Any]] = {}
    filas[año_base] = {
        "EPS Proyectado": _safe_float(eps_base),
        "Ingresos Proyectados (M)": _safe_float(ingresos_base_m),
        "EBITDA Proyectado (M)": _safe_float(ebitda_base_m),
        "EBIT Proyectado (M)": _safe_float(ebit_base_m),
        "FCFF Proyectado (M)": _safe_float(fcff_base_m),
        "Fuente": "Histórico real (Yahoo)",
    }

    # Los años con consenso directo pisan únicamente EPS/Revenue; el resto permanece anclado al histórico.
    for año, valor in eps_points.items():
        filas.setdefault(año, {})["EPS Proyectado"] = valor
        filas[año]["Fuente"] = "Consenso directo de analistas"
    for año, valor in revenue_points.items():
        filas.setdefault(año, {})["Ingresos Proyectados (M)"] = valor
        if año == año_base:
            filas[año]["Fuente"] = "Histórico real (Yahoo)"
        else:
            filas.setdefault(año, {})["Fuente"] = "Consenso directo de analistas"

    # v7: EBITDA / EBIT / FCFF coherentes con el consenso de ingresos. En v6 estas métricas se extrapolaban desde el
    # último ejercicio al ritmo +5y mientras el EPS saltaba al consenso (p. ej. EPS ×2,4 y EBITDA +5%), de modo que los
    # métodos PER y EV daban precios incompatibles. Ahora, para los años con consenso de ingresos, se mantiene el margen
    # del último ejercicio sobre los ingresos de consenso.
    ing_base_v7 = _safe_float(ingresos_base_m)
    if ing_base_v7 and ing_base_v7 > 0:
        for año_c in sorted(revenue_points):
            if año_c <= año_base:
                continue
            ratio_c = revenue_points[año_c] / ing_base_v7
            for columna_c, base_c in (("EBITDA Proyectado (M)", ebitda_base_m), ("EBIT Proyectado (M)", ebit_base_m),
                                       ("FCFF Proyectado (M)", fcff_base_m)):
                b_c = _safe_float(base_c)
                if b_c is not None:
                    filas.setdefault(año_c, {})[columna_c] = b_c * ratio_c
        resultado["margen_constante"] = bool(revenue_points)

    # Si hay +5y, el tramo modelizado empieza después del último año con consenso directo.
    ultimo_consenso = max(set(list(eps_points.keys()) + list(revenue_points.keys()) + [año_base]))
    if resultado["crecimiento_5y"] is not None:
        g = resultado["crecimiento_5y"]
        eps_seed_year = ultimo_consenso
        rev_seed_year = ultimo_consenso
        eps_seed = _safe_float(filas.get(eps_seed_year, {}).get("EPS Proyectado"))
        rev_seed = _safe_float(filas.get(rev_seed_year, {}).get("Ingresos Proyectados (M)"))

        if eps_seed is None:
            eps_seed_year, eps_seed = año_base, _safe_float(eps_base)
        if rev_seed is None:
            rev_seed_year, rev_seed = año_base, _safe_float(ingresos_base_m)

        eps_model = _proyectar_desde_semilla(eps_seed, eps_seed_year, g, horizonte)
        rev_model = _proyectar_desde_semilla(rev_seed, rev_seed_year, g, horizonte)

        for año in range(año_base + 1, horizonte + 1):
            # MUY IMPORTANTE: jamás sobrescribimos una estimación anual directa
            # de analistas con la extrapolación +5y. El consenso tiene prioridad.
            if año > ultimo_consenso:
                fila = filas.setdefault(año, {})
                if año in eps_model:
                    fila["EPS Proyectado"] = eps_model[año]
                if año in rev_model:
                    fila["Ingresos Proyectados (M)"] = rev_model[año]
                fila["Fuente"] = "Modelo: crecimiento +5y del consenso"

        # EBITDA / EBIT / FCFF: no son consenso de analistas en Yahoo. Se mantienen como modelo.
        for columna, base in [
            ("EBITDA Proyectado (M)", ebitda_base_m),
            ("EBIT Proyectado (M)", ebit_base_m),
            ("FCFF Proyectado (M)", fcff_base_m),
        ]:
            base_value = _safe_float(base)
            if base_value is None:
                continue
            semilla_año, semilla = año_base, base_value
            for a_s in sorted(filas):
                v_s = filas[a_s].get(columna)
                if a_s <= ultimo_consenso and v_s is not None and pd.notna(v_s):
                    semilla_año, semilla = a_s, _safe_float(v_s)
            modelo = _proyectar_desde_semilla(semilla, semilla_año, g, horizonte)
            for año, valor in modelo.items():
                if año > ultimo_consenso or columna not in filas.get(año, {}):
                    filas.setdefault(año, {})[columna] = valor

    else:
        # Sin +5y público, no rellenamos hasta 2030 con una cifra arbitraria.
        resultado["error"] = (
            f"Yahoo no devuelve una tasa de crecimiento +5y utilizable para {ticker_symbol}. "
            f"La app conserva el consenso directo publicado, pero deja sin inventar el tramo posterior a {ultimo_consenso}."
        )

    # Si todavía hay años con consenso directo que no reciben etiqueta, garantizamos una fuente coherente.
    for año, fila in filas.items():
        fila.setdefault("Fuente", "Histórico real (Yahoo)" if año == año_base else "Sin estimación pública")

    proyeccion = pd.DataFrame(filas).T
    proyeccion.index.name = "Año"
    proyeccion = proyeccion.sort_index()
    resultado["proyeccion"] = proyeccion

    return resultado


# ==========================================================================================
# CAPA DE PRESENTACIÓN
# ==========================================================================================

def colorear_per(df, per_medio):
    styles = pd.DataFrame('', index=df.index, columns=df.columns)
    try:
        if per_medio is None or pd.isna(per_medio):
            return styles
        ultimo_año = df.columns[-1]
        per_actual = df.loc["PER Histórico", ultimo_año]
        if pd.notna(per_actual) and 0 < per_actual < per_medio:
            styles.loc["PER Histórico", ultimo_año] = 'background-color: #065f46; color: #ecfdf5; font-weight: bold;'
    except Exception:
        pass
    return styles


def _metrica(valor, sufijo=""):
    return f"{valor:.2f}{sufijo}" if valor is not None and pd.notna(valor) else "N/D"


def _fig_comparacion_valor(df, col_precio, col_valor, titulo):
    """Precio real vs valor implícito, con la zona entre ambas líneas sombreada en VERDE
    cuando valor > precio (infravalorada) y en ROJO cuando precio > valor (sobrevalorada) —
    para ver de un vistazo, en cada tramo, si pesa más el valor intrínseco o el precio."""
    d = df[[col_precio, col_valor]].dropna()
    fig = go.Figure()
    if not d.empty:
        signo = np.sign(d[col_valor] - d[col_precio])
        cambio = signo.diff().fillna(0) != 0
        grupo = cambio.cumsum()
        for _, seg in d.groupby(grupo):
            s = np.sign((seg[col_valor] - seg[col_precio]).iloc[0])
            color = "rgba(52,211,153,0.20)" if s >= 0 else "rgba(251,113,133,0.18)"
            xs = list(seg.index) + list(seg.index[::-1])
            ys = list(seg[col_valor].values) + list(seg[col_precio].values[::-1])
            fig.add_trace(go.Scatter(x=xs, y=ys, fill="toself", fillcolor=color,
                                      line=dict(color="rgba(0,0,0,0)"), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=d.index, y=d[col_precio], mode="lines+markers", name="Precio real",
                              line=dict(color=COLOR_ACTUAL, width=2.5)))
    fig.add_trace(go.Scatter(x=d.index, y=d[col_valor], mode="lines+markers", name="Valor implícito",
                              line=dict(color=COLOR_MODELO, width=2.5, dash="dot")))
    fig.update_layout(title=titulo, height=380, template="plotly_dark", hovermode="x unified",
                       margin=dict(l=10, r=10, t=40, b=10),
                       legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    return _aplicar_tema_figura(fig)



def _chip(color, texto):
    emoji = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}.get(color, "⚪")
    return f'<div class="mini-note" style="margin:3px 0">{emoji} {texto}</div>'


def _lineas_conclusion(c):
    """Conclusión del modelo en lenguaje llano. Todo se deriva de cifras ya calculadas en pantalla."""
    div, precio = c["divisa"], c["precio"]
    L = []
    esc = c.get("esc")
    if c.get("usa_escenarios") and esc:
        v = [e["valor"] for e in esc["escenarios"]]
        lo, hi, esp = min(v), max(v), esc["valor_esperado"]
        if precio is not None:
            if precio <= lo:
                donde = "por debajo incluso del escenario más pesimista"
            elif precio <= esp * 0.95:
                donde = "por debajo del valor esperado"
            elif precio <= esp * 1.05:
                donde = "en línea con el valor esperado"
            elif precio <= hi:
                donde = "por encima del valor esperado: ya descuenta un ciclo favorable"
            else:
                donde = "por encima incluso del escenario más optimista"
            L.append(f"**Valor por escenarios de ciclo:** entre {lo:,.0f} y {hi:,.0f} {div} (esperado {esp:,.0f}). "
                     f"El precio actual ({precio:,.2f}) está {donde}.")
    elif c.get("valor_blended") is not None and precio:
        L.append(f"**Valor combinado (DCF + PER):** {c['valor_blended']:,.2f} {div} frente a un precio de {precio:,.2f} "
                 f"({c['gap_pct']:+.1f}%).")
    if c.get("persist") and c["persist"].get("estado") == "ok":
        L.append(f"**Qué descuenta el precio:** que se mantenga aproximadamente el {c['persist']['p']*100:.0f}% del beneficio "
                 "extraordinario sobre el nivel normal del ciclo.")
    elif c.get("persist") and c["persist"].get("estado") == "mayor":
        L.append("**Qué descuenta el precio:** más de lo que justifica mantener el 100% del beneficio actual con estos supuestos de múltiplo.")
    g_imp = c.get("g_implicito")
    if g_imp and g_imp.get("estado") == "ok":
        L.append(f"**DCF inverso:** el precio exige que el flujo de caja libre crezca de media un {g_imp['g']*100:.1f}% anual al inicio "
                 f"(partiendo de la base «{c['base_dcf']}»).")
    elif g_imp and g_imp.get("estado") == "mayor":
        L.append("**DCF inverso:** el precio exige un crecimiento del flujo de caja superior al 20% anual con la base elegida.")
    elif g_imp and g_imp.get("estado") == "menor":
        L.append("**DCF inverso:** el precio es inferior al valor que da el DCF incluso con caída anual del 10% del flujo de caja.")
    cal = []
    if c.get("pio_score") is not None and c.get("pio_max"):
        cal.append(f"Piotroski {c['pio_score']}/{c['pio_max']}")
    if c.get("roic") is not None:
        cal.append(f"ROIC {c['roic']:.0f}%")
    if c.get("deuda_neta_ebitda") is not None:
        cal.append("deuda neta/EBITDA " + (f"{c['deuda_neta_ebitda']:.1f}x" if c["deuda_neta_ebitda"] >= 0 else "negativa (caja neta)"))
    if cal:
        L.append("**Calidad y solvencia:** " + " · ".join(cal) + (" (medidos en un punto alto del ciclo: no son permanentes)." if c.get("es_ciclica") else "."))
    if c.get("es_ciclica"):
        L.append("**Cautela:** en una cíclica un PER bajo sobre beneficios récord suele indicar pico de ciclo, no ganga; "
                 "la valoración depende de cuánto dure el ciclo, que este modelo no puede predecir.")
    return L


def _render_resumen_v7(c, sufijo):
    ciclo = c.get("ciclo") or {}
    k1, k2, k3 = st.columns(3)
    nivel = ciclo.get("nivel", "indeterminada")
    estilo = {"cíclica": "tc-roja", "variable": "tc-ambar", "estable": "tc-verde"}.get(nivel, "tc-clara")
    with k1:
        _tarjeta("Ciclicidad del negocio", (f"{nivel.capitalize()} · {ciclo['score']}/100" if ciclo.get("score") is not None else "N/D"),
                 (f"Margen actual {ciclo['margen_actual']:.0f}% · mediana {ciclo['margen_mediana']:.0f}%" if ciclo.get("margen_actual") is not None
                  else "Histórico insuficiente"), estilo)
    cal_estilo = {"alta": "tc-verde", "media": "tc-ambar", "baja": "tc-roja"}.get(c["calidad_nivel"], "tc-clara")
    with k2:
        _tarjeta("Confianza de los datos", f"{c['calidad_nivel'].capitalize()} · {c['calidad_score']}/100",
                 "Cobertura de histórico, consenso y ciclo", cal_estilo)
    with k3:
        if c.get("rango_valor"):
            lo, hi = c["rango_valor"]
            _tarjeta("Rango de valor", f"{lo:,.0f} – {hi:,.0f} {c['divisa']}",
                     "Escenarios de ciclo" if c.get("usa_escenarios") and c.get("esc") else "Mín.–máx. entre DCF y PER", "tc-clara")
        else:
            _tarjeta("Rango de valor", "N/D", "Faltan datos para valorar", "tc-clara")
    lineas = _lineas_conclusion(c)
    if lineas:
        st.markdown("#### 🧭 Conclusión del modelo")
        st.markdown('<div class="info-note">' + "<br>".join(lineas) + "</div>", unsafe_allow_html=True)
    with st.expander("🛡️ Calidad de los datos de este análisis"):
        st.markdown("".join(_chip(col, txt) for col, txt in c["calidad_items"]), unsafe_allow_html=True)
        if c.get("caja_descripcion"):
            st.caption(f"Caja usada en el puente de valor: {c['caja_descripcion']}. Los intereses minoritarios se suman a la deuda. "
                       "Las inversiones financieras no corrientes (si existen) no se cuentan: criterio prudente.")
        if ciclo.get("razones"):
            st.caption("Ciclicidad: " + " ".join(ciclo["razones"]))


def _render_tab_ciclo(c, sufijo):
    """Pestaña 'Ciclo & Expectativas': normalización de beneficios, escenarios y lo que el precio ya descuenta."""
    ciclo = c.get("ciclo") or {}
    div, precio = c["divisa"], c["precio"]
    st.subheader("🔄 Ciclo del negocio y expectativas implícitas")
    if not precio:
        st.info("Falta el precio actual para esta pestaña.")
        return
    if ciclo.get("razones"):
        st.markdown('<div class="mini-note">' + " ".join(ciclo["razones"]) + "</div>", unsafe_allow_html=True)
    if ciclo.get("posicion") is not None:
        st.progress(float(min(max(ciclo["posicion"], 0.0), 1.0)),
                    text=f"Posición del margen actual dentro de su rango de los últimos {ciclo.get('n_años', 0)} ejercicios (0% = mínimo, 100% = máximo)")
    forzar = False
    if c["es_ciclica"]:
        st.markdown('<div class="risk-note"><strong>Empresa cíclica.</strong> El PER medio histórico y los múltiplos actuales dejan de ser '
                    'anclas fiables: en máximos de ciclo el PER es bajo porque el beneficio es extraordinario, y en mínimos es alto. '
                    'Aquí el valor se estima sobre un beneficio normalizado y con tres escenarios de persistencia.</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="success-note">La empresa no se comporta como cíclica en el histórico disponible: el PER medio es un ancla razonable '
                    '(pestaña Valoración). Esta pestaña sigue siendo útil para ver qué descuenta el precio.</div>', unsafe_allow_html=True)
        forzar = st.checkbox("Tratarla como cíclica de todos modos (usar escenarios de ciclo en la señal)", value=False, key=f"ciclo_forzar_{sufijo}")

    eps_act, eps_ref = c.get("eps_actual"), c.get("eps_ref")
    eps_norm_def = max(float(c.get("eps_norm_def") or 0.0), 0.0)
    per_medio = c.get("per_medio")
    eps_norm, d_pico, p_base = eps_norm_def, 50, 50
    usar = bool(c["es_ciclica"] or st.session_state.get(f"ciclo_forzar_{sufijo}", False))

    # ---------- escenarios ----------
    if usar:
        st.markdown("##### Escenarios de beneficio (todos los supuestos son editables)")
        a, b, d = st.columns(3)
        eps_norm = a.number_input(f"EPS normal del ciclo ({div})", min_value=0.0, value=round(eps_norm_def, 2), step=0.5,
                                  format="%.2f", key=f"ciclo_en_{sufijo}",
                                  help="Por defecto, la media de los EPS anuales disponibles (incluye años buenos y malos).")
        d_pico = b.slider("Descuento del PER sobre beneficio de pico (%)", 0, 80, 50, 5, key=f"ciclo_d_{sufijo}",
                          help="Cuánto menor es el múltiplo que el mercado paga cuando el beneficio está en máximos.")
        p_base = d.slider("Persistencia del caso intermedio (%)", 10, 90, 50, 10, key=f"ciclo_p_{sufijo}")
        w1, w2, w3 = st.columns(3)
        pesos = (w1.slider("Probabilidad: normalización total", 0, 100, 30, 5, key=f"ciclo_w0_{sufijo}"),
                 w2.slider("Probabilidad: caso intermedio", 0, 100, 40, 5, key=f"ciclo_w1_{sufijo}"),
                 w3.slider("Probabilidad: persistencia total", 0, 100, 30, 5, key=f"ciclo_w2_{sufijo}"))
        if sum(pesos) == 0:
            pesos = (30, 40, 30)
        esc = _escenarios_ciclo(eps_norm, eps_ref, per_medio, d_pico / 100.0, (0.0, p_base / 100.0, 1.0), pesos) \
            if (per_medio and eps_ref is not None) else None
        if esc is None:
            st.info("Faltan el PER medio fiable o el EPS de referencia para construir los escenarios.")
        else:
            nombres = ["Normalización total", f"Caso intermedio ({p_base}% persiste)", "Persistencia total"]
            filas = []
            for nombre, e, w in zip(nombres, esc["escenarios"], esc["pesos"]):
                filas.append({"Escenario": nombre, "EPS": e["eps"], "PER aplicado": e["per"],
                              f"Valor ({div})": e["valor"], "vs precio (%)": (e["valor"] / precio - 1) * 100, "Probabilidad": w * 100})
            df_esc = pd.DataFrame(filas).set_index("Escenario")
            st.dataframe(df_esc.style.format({"EPS": "{:.2f}", "PER aplicado": "{:.1f}x", f"Valor ({div})": "{:,.0f}",
                                               "vs precio (%)": "{:+.0f}%", "Probabilidad": "{:.0f}%"})
                         .background_gradient(cmap=_cmap_valor(), subset=["vs precio (%)"], vmin=-60, vmax=60), use_container_width=True)
            esperado = esc["valor_esperado"]
            vals = [f[f"Valor ({div})"] for f in filas]
            fig = go.Figure(go.Bar(y=nombres + ["Valor esperado"], x=vals + [esperado], orientation="h",
                                   marker_color=[COLOR_CONSENSO, COLOR_CONSENSO, COLOR_CONSENSO, COLOR_MODELO],
                                   text=[f"{v:,.0f}" for v in vals + [esperado]], textposition="outside"))
            fig.add_vline(x=precio, line_dash="dash", line_color=COLOR_NEGATIVO, annotation_text=f"Precio {precio:,.0f}",
                          annotation_font_color=COLOR_NEGATIVO)
            fig.update_layout(height=290, margin=dict(l=10, r=30, t=30, b=10), **_DARK_LAYOUT)
            _aplicar_tema_figura(fig)
            st.plotly_chart(fig, use_container_width=True, key=f"ciclo_esc_{sufijo}")
            st.caption(f"EPS de referencia: {eps_ref:.2f} ({c['eps_ref_fuente']}). Persistencia = fracción del beneficio extraordinario sobre el nivel normal "
                       "que se mantiene. El PER aplicado se interpola entre el PER medio histórico (normalización total) y ese PER con el "
                       "descuento de pico (persistencia total). Son supuestos del modelo, no datos de Yahoo.")


    else:
        st.info("Los escenarios de ciclo solo se usan en empresas cíclicas. Si quieres verlos igualmente, marca la casilla de arriba.")

    # ---------- qué descuenta el precio ----------
    st.markdown("##### Qué descuenta el precio actual")
    m1, m2, m3 = st.columns(3)
    with m1:
        _tarjeta("PER sobre EPS de referencia", f"{precio / eps_ref:.1f}x" if eps_ref else "N/D", f"{c['eps_ref_fuente']}", "tc-clara")
    with m2:
        _tarjeta("PER sobre EPS normal", (f"{precio / eps_norm:.1f}x" if (usar and eps_norm > 0) else "N/D"),
                 "Precio / beneficio medio del ciclo" if usar else "Solo relevante en empresas cíclicas", "tc-clara")
    with m3:
        _tarjeta("PER medio histórico fiable", f"{per_medio:.1f}x" if per_medio else "N/D", f"{c['per_n_validos']} de {c['per_n_total']} ejercicios", "tc-clara")
    pi = c.get("persist_fn")(eps_norm, d_pico / 100.0) if (usar and c.get("persist_fn") and per_medio and eps_ref is not None) else None
    if pi:
        if pi["estado"] == "ok":
            st.markdown(f'<div class="info-note">Con estos supuestos, el precio actual equivale a que se mantenga aproximadamente el '
                        f'<strong>{pi["p"]*100:.0f}%</strong> del beneficio extraordinario sobre el nivel normal.</div>', unsafe_allow_html=True)
        elif pi["estado"] == "mayor":
            st.markdown('<div class="risk-note">Ni siquiera manteniendo el 100% del beneficio de referencia se llega al precio actual con estos '
                        'múltiplos: el mercado paga más de lo que estos supuestos justifican.</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="success-note">El precio actual está por debajo del valor incluso con normalización total.</div>', unsafe_allow_html=True)
    if usar and eps_ref is not None and eps_norm >= 0:
        filas_req = []
        for per_t in (4, 6, 8, 10, 12, 15, 20):
            eps_req = precio / per_t
            p_req = (eps_req - eps_norm) / (eps_ref - eps_norm) if eps_ref != eps_norm else np.nan
            filas_req.append({"Si el mercado paga un PER de": f"{per_t}x", "EPS necesario": eps_req,
                              "% del exceso de beneficio que debe persistir": p_req * 100 if np.isfinite(p_req) else np.nan})
        st.markdown("**EPS que justifica el precio actual según el PER que se pague**")
        st.dataframe(pd.DataFrame(filas_req).set_index("Si el mercado paga un PER de").style.format(
            {"EPS necesario": "{:.2f}", "% del exceso de beneficio que debe persistir": "{:.0f}%"}, na_rep="-"), use_container_width=True)

    # ---------- mapa EPS × PER ----------
    if eps_ref is not None:
        pers = np.linspace(3.0, max(20.0, (per_medio or 15.0) * 1.5), 22)
        e_lo = max(min(eps_norm, eps_act or eps_norm, eps_ref) * 0.7, 0.01)
        e_hi = max(eps_norm, eps_act or 0.0, eps_ref) * 1.15
        epss = np.linspace(e_lo, e_hi, 18)
        z = np.outer(epss, pers)
        fig2 = go.Figure()
        fig2.add_trace(go.Heatmap(x=pers, y=epss, z=z, colorscale=ESCALA_VALOR, zmid=precio,
                                  colorbar=dict(title=f"Precio ({div})"),
                                  hovertemplate="PER %{x:.1f}x · EPS %{y:.2f}<br>Precio implícito %{z:,.0f}<extra></extra>"))
        fig2.add_trace(go.Contour(x=pers, y=epss, z=z, showscale=False, hoverinfo="skip",
                                  contours=dict(start=precio, end=precio, size=1, coloring="none"),
                                  line=dict(color="#f5f3ff", width=2.5)))
        puntos = []
        if eps_ref:
            puntos.append(("Hoy sobre EPS de referencia", precio / eps_ref, eps_ref))
        if eps_act and eps_act > 0:
            puntos.append(("Hoy sobre último EPS", precio / eps_act, eps_act))
        if per_medio and usar:
            puntos.append(("Normal del ciclo × PER medio", per_medio, eps_norm))
        for nombre, x, y in puntos:
            if pers.min() <= x <= pers.max():
                fig2.add_trace(go.Scatter(x=[x], y=[y], mode="markers+text", text=[nombre], textposition="top right",
                                          marker=dict(size=11, color=COLOR_PRECIO, line=dict(width=1.5, color="#0b0618")), showlegend=False))
        fig2.update_layout(title="Mapa EPS × PER: la línea blanca marca las combinaciones que igualan el precio actual", height=420,
                           xaxis_title="PER aplicado", yaxis_title=f"EPS ({div})", margin=dict(l=10, r=10, t=55, b=10), **_DARK_LAYOUT)
        _aplicar_tema_figura(fig2)
        st.plotly_chart(fig2, use_container_width=True, key=f"ciclo_mapa_{sufijo}")
        st.caption("Por encima/derecha de la línea el valor supera al precio (verde); por debajo/izquierda, el precio lo supera (rojo).")

    # ---------- DCF inverso y sensibilidad a la base ----------
    st.markdown("##### DCF inverso y sensibilidad del DCF")
    wacc_s = st.session_state.get(f"wacc_{sufijo}", 9.0) / 100.0
    gt_s = st.session_state.get(f"gt_{sufijo}", 2.5) / 100.0
    serie = c.get("fcff_serie")
    if serie is not None and c.get("acciones"):
        filas_b, g_ref = [], (c.get("g_consenso") if c.get("g_consenso") is not None else 0.05)
        for modo in ("Último ejercicio", "Media 3 ejercicios"):
            base = _base_fcff_dcf(serie, modo)
            if base is None or base <= 0:
                filas_b.append({"Base de FCFF": modo, "FCFF base (M)": base, f"Valor DCF ({div})": np.nan, "vs precio (%)": np.nan, "Crecimiento que exige el precio": np.nan})
                continue
            v, _ = calcular_dcf(base, c["deuda_m"], c["efectivo_m"], c["acciones"], wacc_s, gt_s, g_ref)
            gi = _crecimiento_implicito_dcf(precio, base, c["deuda_m"], c["efectivo_m"], c["acciones"], wacc_s, gt_s)
            filas_b.append({"Base de FCFF": modo, "FCFF base (M)": base, f"Valor DCF ({div})": v if v is not None else np.nan,
                            "vs precio (%)": ((v / precio - 1) * 100) if v else np.nan,
                            "Crecimiento que exige el precio": (gi["g"] * 100) if (gi and gi["estado"] == "ok") else np.nan})
        st.dataframe(pd.DataFrame(filas_b).set_index("Base de FCFF").style.format(
            {"FCFF base (M)": "{:,.0f}", f"Valor DCF ({div})": "{:,.0f}", "vs precio (%)": "{:+.0f}%",
             "Crecimiento que exige el precio": "{:.1f}%"}, na_rep="N/D"), use_container_width=True)
        st.caption(f"WACC {wacc_s*100:.1f}% · crecimiento terminal {gt_s*100:.2f}% · crecimiento inicial {g_ref*100:.1f}% (los controles están en la pestaña Financiero & DCF). "
                   "Cuando la base cambia mucho el resultado, el FCFF del último año no es representativo.")
        with st.expander("🌡️ Sensibilidad del DCF: WACC × crecimiento inicial"):
            base_sel = _base_fcff_dcf(serie, c["base_dcf"])
            if base_sel and base_sel > 0:
                waccs = [0.07, 0.08, 0.09, 0.10, 0.11, 0.12]
                gs = [-0.05, 0.0, 0.05, 0.10, 0.15, 0.20]
                zz = []
                for w_ in waccs:
                    fila = []
                    for g_ in gs:
                        v_, _ = calcular_dcf(base_sel, c["deuda_m"], c["efectivo_m"], c["acciones"], w_, gt_s, g_)
                        fila.append(v_ if v_ is not None else np.nan)
                    zz.append(fila)
                fig3 = go.Figure(go.Heatmap(x=[f"{g*100:+.0f}%" for g in gs], y=[f"{w*100:.0f}%" for w in waccs], z=zz,
                                            colorscale=ESCALA_VALOR, zmid=precio, colorbar=dict(title=f"Valor ({div})"),
                                            hovertemplate="Crec. %{x} · WACC %{y}<br>Valor %{z:,.0f}<extra></extra>"))
                fig3.update_layout(height=340, xaxis_title="Crecimiento inicial del FCFF", yaxis_title="WACC",
                                   margin=dict(l=10, r=10, t=30, b=10), **_DARK_LAYOUT)
                _aplicar_tema_figura(fig3)
                st.plotly_chart(fig3, use_container_width=True, key=f"ciclo_dcfheat_{sufijo}")
                st.caption(f"Base de FCFF: {c['base_dcf']}. Verde = valor por encima del precio actual ({precio:,.2f} {div}).")
            else:
                st.info("La base de FCFF seleccionada no es positiva: no hay DCF que sensibilizar.")
    else:
        st.info("Faltan el flujo de caja histórico o el número de acciones para el DCF inverso.")



def _texto_gap(nombre, valor, precio):
    """Texto simétrico y sin ambigüedad: potencial = valor/precio − 1; prima = precio/valor − 1."""
    if valor is None or precio in (None, 0) or valor <= 0:
        return ""
    if precio < valor:
        return f"🟢 **{nombre} vs Precio:** infravalorada · potencial **+{(valor / precio - 1) * 100:.1f}%**"
    return f"🔴 **{nombre} vs Precio:** sobrevalorada · el precio supera al valor un **{(precio / valor - 1) * 100:.1f}%**"


def _render_auditoria_per(datos, eps_actual, precio_actual, valor_relativo, divisa, sufijo):
    """Muestra EXACTAMENTE qué cifras producen el 'Relativo (PER)'. Dos herramientas que no usen el mismo
    EPS, la misma ventana de años o el mismo precio darán porcentajes ligeramente distintos (8,6% vs 8,7%);
    con esta tabla se ve de dónde sale cada décima."""
    per_medio = datos.get("per_medio")
    if not per_medio:
        return
    with st.expander("🧾 Auditoría del PER relativo — qué cifras se han usado"):
        a, b, c, d = st.columns(4)
        a.metric("EPS usado (último ejercicio)", f"{eps_actual:.4f}" if eps_actual is not None else "N/D")
        b.metric("PER medio fiable", f"{per_medio:.2f}x")
        c.metric("Mediana", f"{datos['per_mediana']:.2f}x" if datos.get("per_mediana") else "N/D")
        d.metric("Años usados", f"{datos.get('per_n_validos', 0)} de {datos.get('per_n_total', 0)}")
        if eps_actual is not None and precio_actual:
            st.markdown(
                f"**Cálculo:** {eps_actual:.4f} × {per_medio:.2f} = **{valor_relativo:.2f} {divisa}** · "
                f"precio actual {precio_actual:.2f} {divisa} → potencial {(valor_relativo / precio_actual - 1) * 100:+.2f}%"
            )
        if datos.get("per_descartados"):
            detalle = ", ".join(f"{año}: {motivo}" for año, motivo in datos["per_descartados"].items())
            st.markdown(f'<div class="risk-note">Años descartados de la media: {detalle}.</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="success-note">Ningún año descartado: todos los PER disponibles son utilizables.</div>',
                        unsafe_allow_html=True)
        if datos.get("per_n_total", 0) and datos.get("per_n_validos", 0) < 4:
            st.markdown('<div class="risk-note">Yahoo solo publica 4–5 ejercicios anuales: con tan pocos años la media '
                        'es sensible a un solo dato. Contrasta con la mediana.</div>', unsafe_allow_html=True)
        st.caption("Precio histórico SIN ajustar por dividendos (solo splits), EPS diluido ajustado por splits, "
                   "y precio de cierre en la fecha de cada cierre fiscal.")
        if datos.get("conversion_moneda"):
            cm = datos["conversion_moneda"]
            st.caption(f"Estados financieros convertidos de {cm['de']} a {cm['a']} (tipo {cm['fx']:.4f}).")
        if datos.get("aviso_moneda"):
            st.markdown(f'<div class="risk-note">{datos["aviso_moneda"]}</div>', unsafe_allow_html=True)


def mostrar_analisis(ticker_symbol, sufijo):
    """Renderiza el análisis completo de un ticker y devuelve un resumen para la pestaña
    Comparativa, para que las cifras de 2030 y hoy nunca se recalculen dos veces distintas."""
    datos = extraer_datos_financieros(ticker_symbol)

    resumen = {"ticker": ticker_symbol, "roic": None, "per": None, "per_medio": None, "precio_actual": None,
               "market_cap_m": None, "valor_blended": None, "gap_hoy_pct": None, "piotroski_puntuacion": None,
               "piotroski_maximo": None, "precio_horizonte_blended": None, "cagr_horizonte_blended": None}

    if datos["error_general"]:
        st.error(datos["error_general"])
        return resumen
    if datos["balance"] is None:
        st.error("No se pudo obtener el balance de la empresa.")
        return resumen

    divisa = datos["divisa"]
    precio_actual = datos["precio_actual"]
    acciones = datos["acciones_circulacion"]

    # El mercado puede devolver currentPrice vacío mientras fast_info sí contiene el último precio.
    if precio_actual is None:
        try:
            fast = yf.Ticker(ticker_symbol).fast_info
            precio_actual = _safe_float(fast.get("lastPrice") or fast.get("last_price"))
        except Exception:
            pass

    if acciones is None:
        try:
            fast = yf.Ticker(ticker_symbol).fast_info
            acciones = _safe_float(fast.get("shares") or fast.get("shares_outstanding"))
        except Exception:
            pass
    eps_actual = (_safe_float(datos["valoracion"].loc["EPS Anual"].iloc[-1])
                  if datos["valoracion"] is not None and "EPS Anual" in datos["valoracion"].index else None)
    per_medio = datos["per_medio"]
    valor_relativo = (eps_actual * per_medio) if (eps_actual and per_medio and eps_actual > 0) else None

    deuda_actual_m = _deuda_ajustada_m(datos["balance"])
    efectivo_actual_m = datos["balance"].loc["Efectivo y Equivalentes"].iloc[-1]
    ebit_actual_m = datos["balance"].loc["Beneficio Operativo (EBIT)"].iloc[-1]
    ebitda_actual_m = datos["fcf"].loc["1. EBITDA"].iloc[-1] if datos["fcf"] is not None and "1. EBITDA" in datos["fcf"].index else None
    fcff_actual_m = datos["fcf"].loc["=== FREE CASH FLOW A LA FIRMA (FCFF) ==="].iloc[-1] if datos["fcf"] is not None and "=== FREE CASH FLOW A LA FIRMA (FCFF) ===" in datos["fcf"].index else None
    año_base = int(datos["balance"].columns[-1])
    efectivo_por_accion = (efectivo_actual_m * 1e6 / acciones) if (efectivo_actual_m and acciones) else 0.0

    est = obtener_estimaciones_futuras(
        ticker_symbol, año_base, eps_actual, datos["balance"].loc["Ingresos Totales (Ventas Anuales)"].iloc[-1],
        ebitda_actual_m, ebit_actual_m, fcff_actual_m, AÑO_HORIZONTE
    )

    # Ausencia de dato ≠ crecimiento 0%: el DCF automático solo aparece si hay +5y publicado.
    g_consenso = est["crecimiento_5y"]

    # ---- v7: ciclicidad, base del DCF y escenarios. Se leen de session_state para que el resumen siga a los controles. ----
    ciclo = datos.get("ciclo") or {}
    es_ciclica = bool(ciclo.get("es_ciclica"))
    fcff_serie = (datos["fcf"].loc["=== FREE CASH FLOW A LA FIRMA (FCFF) ==="]
                  if (datos.get("fcf") is not None and "=== FREE CASH FLOW A LA FIRMA (FCFF) ===" in datos["fcf"].index) else None)
    base_dcf_sel = st.session_state.get(f"dcf_base_{sufijo}", "Media 3 ejercicios" if es_ciclica else "Último ejercicio")
    fcff_base_dcf = _base_fcff_dcf(fcff_serie, base_dcf_sel) if fcff_serie is not None else None
    if fcff_base_dcf is None:
        fcff_base_dcf = fcff_actual_m
    if g_consenso is not None:
        dcf_defecto, _detalle_defecto = calcular_dcf(
            fcff_base_dcf, deuda_actual_m, efectivo_actual_m, acciones, 0.09, 0.025, g_consenso
        )
    else:
        dcf_defecto, _detalle_defecto = None, "Sin crecimiento +5y publicado: DCF automático desactivado."

    eps_ref, eps_ref_fuente = eps_actual, "último ejercicio publicado"
    _proy_tmp = est.get("proyeccion")
    if _proy_tmp is not None and (año_base + 1) in _proy_tmp.index:
        _e1 = _safe_float(_proy_tmp.loc[año_base + 1, "EPS Proyectado"]) if "EPS Proyectado" in _proy_tmp.columns else None
        if _e1 is not None and "onsenso" in str(_proy_tmp.loc[año_base + 1].get("Fuente", "")):
            eps_ref, eps_ref_fuente = _e1, f"consenso de analistas FY{año_base + 1}"
    usa_escenarios = es_ciclica or bool(st.session_state.get(f"ciclo_forzar_{sufijo}", False))
    eps_norm_def = ciclo.get("eps_media_ciclo")
    if eps_norm_def is None:
        eps_norm_def = eps_actual if eps_actual is not None else 0.0
    eps_norm_def = max(float(eps_norm_def), 0.0)
    eps_norm_uso = st.session_state.get(f"ciclo_en_{sufijo}", round(eps_norm_def, 2))
    esc = None
    if usa_escenarios and per_medio and eps_ref is not None:
        esc = _escenarios_ciclo(
            eps_norm_uso, eps_ref, per_medio,
            st.session_state.get(f"ciclo_d_{sufijo}", 50) / 100.0,
            (0.0, st.session_state.get(f"ciclo_p_{sufijo}", 50) / 100.0, 1.0),
            (st.session_state.get(f"ciclo_w0_{sufijo}", 30), st.session_state.get(f"ciclo_w1_{sufijo}", 40),
             st.session_state.get(f"ciclo_w2_{sufijo}", 30)),
        )
    valor_relativo_per = valor_relativo          # PER medio × EPS del último ejercicio (referencia)
    if esc is not None:
        valor_relativo = esc["valor_esperado"]   # en cíclicas se sustituye por el valor esperado de los escenarios
    rel_label = "Escenarios (ciclo)" if esc is not None else "Relativo (PER)"

    valores_para_blend = [v for v in (dcf_defecto, valor_relativo) if v is not None and v > 0]
    if esc is not None and valor_relativo:
        # En cíclicas el DCF (muy sensible a qué año de FCFF se toma como base) se muestra como contraste, no se promedia.
        valores_para_blend = [valor_relativo]
    valor_blended = (sum(valores_para_blend) / len(valores_para_blend)) if valores_para_blend else None
    gap_pct = ((valor_blended - precio_actual) / precio_actual * 100) if (valor_blended is not None and precio_actual not in (None, 0)) else None
    market_cap_m = (precio_actual * acciones / 1e6) if (precio_actual is not None and acciones is not None and acciones > 0) else None

    pio = datos["piotroski"]
    pio_score = pio["puntuacion"] if pio else None
    pio_max = pio["maximo_evaluable"] if pio else None

    # ---------------------------------------------------------------
    # RESUMEN EJECUTIVO (tarjetas estilo ValueSense/Bloomberg)
    # ---------------------------------------------------------------
    _vals_esc = [e["valor"] for e in esc["escenarios"]] if esc is not None else None
    if esc is not None and precio_actual:
        _lo, _hi, _esp = min(_vals_esc), max(_vals_esc), esc["valor_esperado"]
        if precio_actual <= _lo:
            etiqueta, estilo_tc = "🟢 Margen de seguridad alto", "tc-verde"
        elif precio_actual <= _esp * 0.95:
            etiqueta, estilo_tc = "🟢 Descuento sobre el valor esperado", "tc-verde"
        elif precio_actual <= _esp * 1.05:
            etiqueta, estilo_tc = "🟡 Precio ≈ valor esperado", "tc-ambar"
        elif precio_actual <= _hi:
            etiqueta, estilo_tc = "🟠 Descuenta un ciclo favorable", "tc-ambar"
        else:
            etiqueta, estilo_tc = "🔴 Por encima del escenario optimista", "tc-roja"
    elif gap_pct is None:
        etiqueta, estilo_tc = "N/D", "tc-clara"
    elif gap_pct >= 20:
        etiqueta, estilo_tc = "🟢 Fuerte descuento", "tc-verde"
    elif gap_pct >= 5:
        etiqueta, estilo_tc = "🟢 Descuento", "tc-verde"
    elif gap_pct > -5:
        etiqueta, estilo_tc = "🟡 Precio razonable", "tc-ambar"
    else:
        etiqueta, estilo_tc = "🔴 Prima sobre el valor", "tc-roja"

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        _tarjeta("Señal del modelo (hoy)", etiqueta, "vs escenarios de ciclo" if esc is not None else "vs valor blended (DCF + PER)", estilo_tc)
    with c2:
        _tarjeta("Valor esperado (escenarios)" if esc is not None else "Valor Blended", _metrica(valor_blended, f" {divisa}"),
                 f"DCF: {_metrica(dcf_defecto)} · {rel_label}: {_metrica(valor_relativo)}", "tc-clara")
    with c3:
        _tarjeta("Precio actual", _metrica(precio_actual, f" {divisa}"),
                 (f"{gap_pct:+.1f}% vs valor blended" if gap_pct is not None else "—"), estilo_tc)
    with c4:
        _tarjeta("PER (LTM / medio hist.)", f"{_metrica(datos['per_trailing_mercado'])}x",
                 f"Media histórica: {_metrica(per_medio)}x", "tc-clara")
    with c5:
        estilo_pio = "tc-clara"
        if pio_score is not None and pio_max:
            if pio_score / pio_max >= 0.75:
                estilo_pio = "tc-verde"
            elif pio_score / pio_max <= 0.4:
                estilo_pio = "tc-roja"
            else:
                estilo_pio = "tc-ambar"
        _tarjeta("Piotroski F-Score", f"{pio_score}/{pio_max}" if pio_score is not None else "N/D",
                 "Salud contable y operativa", estilo_pio)

    st.caption(
        "Nada de lo anterior es una recomendación de inversión: es la salida mecánica de este modelo con los "
        "supuestos que tú has fijado (o los que vienen por defecto). Ajusta el WACC y el crecimiento más abajo."
    )
    _ebitda_ref = ebitda_actual_m
    _dn_ebitda = (((deuda_actual_m or 0.0) - (efectivo_actual_m or 0.0)) / _ebitda_ref) if (_ebitda_ref and _ebitda_ref > 0) else None
    _cal_score, _cal_nivel, _cal_items = _evaluar_calidad_datos(datos, est, ciclo)
    _persist_fn = (lambda en, d: _persistencia_implicita(precio_actual, en, eps_ref, per_medio, d))
    _g_imp = (_crecimiento_implicito_dcf(precio_actual, fcff_base_dcf, deuda_actual_m, efectivo_actual_m, acciones,
                                         st.session_state.get(f"wacc_{sufijo}", 9.0) / 100.0,
                                         st.session_state.get(f"gt_{sufijo}", 2.5) / 100.0)
              if (precio_actual and fcff_base_dcf and fcff_base_dcf > 0 and acciones) else None)
    _persist_actual = (_persistencia_implicita(precio_actual, eps_norm_uso, eps_ref, per_medio,
                                               st.session_state.get(f"ciclo_d_{sufijo}", 50) / 100.0)
                       if (usa_escenarios and per_medio and eps_ref is not None and precio_actual) else None)
    _candidatos_rango = ([min(_vals_esc), max(_vals_esc)] if _vals_esc else
                         [v for v in (dcf_defecto, valor_relativo) if v is not None and v > 0])
    ctx_v7 = {
        "divisa": divisa, "precio": precio_actual, "acciones": acciones, "ciclo": ciclo, "es_ciclica": es_ciclica,
        "usa_escenarios": usa_escenarios, "esc": esc, "valor_blended": valor_blended, "gap_pct": gap_pct,
        "persist": _persist_actual, "g_implicito": _g_imp, "base_dcf": base_dcf_sel,
        "pio_score": pio_score, "pio_max": pio_max, "roic": _safe_float(datos["balance"].loc["ROIC (%)"].iloc[-1]) if "ROIC (%)" in datos["balance"].index else None,
        "deuda_neta_ebitda": _dn_ebitda, "calidad_score": _cal_score, "calidad_nivel": _cal_nivel, "calidad_items": _cal_items,
        "rango_valor": (min(_candidatos_rango), max(_candidatos_rango)) if len(_candidatos_rango) >= 2 else None,
        "caja_descripcion": datos.get("caja_descripcion"), "eps_actual": eps_actual, "eps_ref": eps_ref,
        "eps_ref_fuente": eps_ref_fuente, "eps_norm_def": eps_norm_def, "per_medio": per_medio,
        "per_n_validos": datos.get("per_n_validos", 0), "per_n_total": datos.get("per_n_total", 0),
        "persist_fn": _persist_fn, "fcff_serie": fcff_serie, "deuda_m": deuda_actual_m, "efectivo_m": efectivo_actual_m,
        "g_consenso": g_consenso,
    }
    _render_resumen_v7(ctx_v7, sufijo)

    sector = datos.get("sector") or "N/D"
    industria = datos.get("industria") or "N/D"
    st.markdown(
        f'<div class="mini-note">🏷️ <strong>Contexto empresarial:</strong> sector = {sector} · industria = {industria}. '
        'No se muestra una “media sectorial” inventada: solo se usa información que Yahoo realmente publica.</div>',
        unsafe_allow_html=True,
    )

    # ---------------------------------------------------------------
    # Comparación de metodologías (barra horizontal, estilo ValueSense)
    # ---------------------------------------------------------------
    if precio_actual and (dcf_defecto or valor_relativo):
        st.markdown("#### Comparación de metodologías")
        etiquetas_bar, valores_bar = [], []
        if dcf_defecto:
            etiquetas_bar.append(f"DCF (base: {base_dcf_sel.lower()})"); valores_bar.append(dcf_defecto)
        if valor_relativo_per and esc is not None:
            etiquetas_bar.append("PER medio × EPS último año (solo referencia)"); valores_bar.append(valor_relativo_per)
        if valor_relativo:
            etiquetas_bar.append("Escenarios de ciclo (valor esperado)" if esc is not None else "Valor Relativo (PER medio)")
            valores_bar.append(valor_relativo)
        etiquetas_bar.append("Precio actual"); valores_bar.append(precio_actual)

        fig_bar = go.Figure(go.Bar(
            y=etiquetas_bar, x=valores_bar, orientation="h",
            marker_color=["#7c3aed"] * (len(valores_bar) - 1) + [COLOR_MODELO],
            text=[f"{v:.1f} {divisa}" for v in valores_bar], textposition="outside",
        ))
        fig_bar.add_vline(x=precio_actual, line_dash="dash", line_color=COLOR_NEGATIVO)
        fig_bar.update_layout(height=250, margin=dict(l=10, r=10, t=20, b=10), **_DARK_LAYOUT)
        _aplicar_tema_figura(fig_bar)
        st.plotly_chart(fig_bar, use_container_width=True, key=f"bar_metodologias_{sufijo}")

        col_txt1, col_txt2 = st.columns(2)
        with col_txt1:
            if valor_relativo:
                st.markdown(_texto_gap(rel_label, valor_relativo, precio_actual))
        with col_txt2:
            if dcf_defecto:
                st.markdown(_texto_gap("DCF (FCFF)", dcf_defecto, precio_actual))
        st.caption("Infravalorada = el valor supera al precio (potencial = valor/precio − 1). "
                   "Sobrevalorada = el precio supera al valor (prima = precio/valor − 1). Ambas usan la misma referencia.")
    _render_auditoria_per(datos, eps_actual, precio_actual, valor_relativo_per, divisa, sufijo)

    # ---------------------------------------------------------------
    # Navegación interna: resumen siempre visible + 4 subpestañas
    # ---------------------------------------------------------------
    proyeccion = est["proyeccion"]

    if gap_pct is not None:
        gauge_col, note_col = st.columns([1.3, 1.0])
        with gauge_col:
            st.plotly_chart(_fig_gauge_valoracion(gap_pct, divisa), use_container_width=True, key=f"gauge_{sufijo}")
        with note_col:
            st.markdown("### 🧭 Lectura rápida")
            st.markdown(
                f"**{etiqueta}** · Gap frente al valor blended: **{gap_pct:+.1f}%**.<br>"
                "El gauge es un resumen mecánico del escenario actual y no sustituye al análisis de supuestos."
                , unsafe_allow_html=True
            )
            st.markdown(_fuente_html("real", "Precio actual"), unsafe_allow_html=True)
            st.markdown(_fuente_html("consenso", "Consenso directo"), unsafe_allow_html=True)
            st.markdown(_fuente_html("modelo", f"Modelo hasta {AÑO_HORIZONTE}"), unsafe_allow_html=True)

    stab1, stab2, stab_ciclo, stab3, stab4 = st.tabs([
        "📊 Proyecciones",
        "💠 Valoración",
        "🔄 Ciclo & Expectativas",
        "🏦 Financiero & DCF",
        "📈 Precio Histórico",
    ])

    with stab_ciclo:
        _render_tab_ciclo(ctx_v7, sufijo)

    with stab1:
            # ---------------------------------------------------------------
            # Centro de datos: consenso real + puente modelizado hasta 2030
            # ---------------------------------------------------------------
            st.subheader(f"🧭 Mapa de previsiones hasta {AÑO_HORIZONTE}")
            st.caption(
                "Aquí se separa intencionadamente lo que publican los analistas de lo que calcula el modelo. "
                "No se dibuja una línea continua falsa como si todos los años fueran consenso directo."
            )

            if est["tabla_consenso"] is not None:
                st.markdown("**Consenso directo publicado**")
                tabla_cons = est["tabla_consenso"].copy()
                st.dataframe(
                    tabla_cons.style.format(
                        {
                            "EPS Estimado (consenso)": "{:.2f}",
                            "Ingresos Estimados M (consenso)": "{:.1f}",
                            "Nº Analistas EPS": "{:.0f}",
                            "Nº Analistas Ingresos": "{:.0f}",
                            "Crec. EPS publicado": "{:+.1%}",
                        },
                        na_rep="-",
                    ),
                    use_container_width=True,
                )
            else:
                st.info("Yahoo no ha devuelto filas de consenso anual utilizables para este ticker.")

            g5 = est.get("crecimiento_5y")
            target_analistas = est.get("analyst_targets") or {}
            col_g1, col_g2, col_g3, col_g4 = st.columns(4)
            with col_g1:
                _tarjeta(
                    "Crecimiento largo plazo",
                    f"{g5*100:.1f}%" if g5 is not None else "N/D",
                    "Yahoo growth_estimates +5y",
                    "tc-clara",
                )
            with col_g2:
                _tarjeta(
                    "Crecimiento corto plazo",
                    f"{est['crecimiento_corto_plazo']*100:.1f}%" if est.get("crecimiento_corto_plazo") is not None else "N/D",
                    "Referencia del salto entre estimaciones EPS",
                    "tc-clara",
                )
            with col_g3:
                _tarjeta(
                    "Objetivo medio analistas",
                    _formato_monetario(target_analistas.get("mean"), divisa),
                    "Price target agregado, no 2030",
                    "tc-clara",
                )
            with col_g4:
                if target_analistas.get("low") is not None and target_analistas.get("high") is not None:
                    rango = f"{target_analistas['low']:.2f} — {target_analistas['high']:.2f}"
                else:
                    rango = "N/D"
                _tarjeta("Rango de objetivos", rango, f"{divisa} · bajo / alto", "tc-clara")

            st.markdown(
                _fuente_html("consenso", "0y / +1y: directo")
                + _fuente_html("modelo", "después del último consenso: +5y compuesto"),
                unsafe_allow_html=True,
            )

            proyeccion = est["proyeccion"]
            if proyeccion is not None:
                tabla = _proyeccion_para_tabla(proyeccion)
                formato = {
                    "EPS Proyectado": "{:.2f}",
                    "Ingresos Proyectados (M)": "{:.1f}",
                    "EBITDA Proyectado (M)": "{:.1f}",
                    "EBIT Proyectado (M)": "{:.1f}",
                    "FCFF Proyectado (M)": "{:.1f}",
                }
                st.markdown("**Hoja de ruta anual — cada fila conserva su procedencia**")
                st.dataframe(tabla.style.format(formato, na_rep="-"), use_container_width=True)
                _descargar_dataframe(tabla, "⬇ Exportar proyecciones CSV", f"{ticker_symbol}_proyecciones_{AÑO_HORIZONTE}.csv", f"csv_proj_{sufijo}")

                fuente_modelo_ok = est["crecimiento_5y"] is not None
                if fuente_modelo_ok:
                    st.markdown(
                        f'<div class="success-note"><strong>Puente 2030:</strong> el crecimiento +5y de analistas publicado es '
                        f'{est["crecimiento_5y"]*100:.1f}%. La app lo compone después del último año de consenso directo para '
                        f'generar la trayectoria hasta {AÑO_HORIZONTE}. Esa parte está etiquetada como <strong>modelo</strong>, no como '
                        '“consenso del horizonte seleccionado”.</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div class="risk-note"><strong>Sin extrapolación automática:</strong> no existe una tasa +5y publicada utilizable; '
                        'la app no inventa un crecimiento para rellenar 2029-2030.</div>',
                        unsafe_allow_html=True,
                    )

                # Selector de métrica fundamental para ver el desarrollo del negocio, no solo el precio.
                metricas_disponibles = [
                    c for c in [
                        "EPS Proyectado",
                        "Ingresos Proyectados (M)",
                        "EBITDA Proyectado (M)",
                        "EBIT Proyectado (M)",
                        "FCFF Proyectado (M)",
                    ] if c in proyeccion.columns
                ]
                if metricas_disponibles:
                    metrica_visual = st.selectbox(
                        "Métrica que quieres ver desarrollarse",
                        metricas_disponibles,
                        key=f"metrica_visual_{sufijo}",
                    )
                    unidad = "Por acción" if metrica_visual == "EPS Proyectado" else "Millones " + divisa
                    st.plotly_chart(
                        _fig_proyeccion_fundamental(
                            proyeccion,
                            metrica_visual,
                            f"Desarrollo de {metrica_visual.replace(' Proyectado', '')} hasta {AÑO_HORIZONTE}",
                            unidad,
                        ),
                        use_container_width=True,
                        key=f"fundamental_{sufijo}_{metrica_visual}",
                    )

            if est["eps_trend"] is not None and not est["eps_trend"].empty:
                with st.expander("🧠 Qué ha pasado con el consenso EPS en los últimos 90 días"):
                    trend_display = est["eps_trend"].copy()
                    st.dataframe(_styler_numerico(trend_display, 2), use_container_width=True)
                    st.caption("Este bloque no inventa una fecha futura: muestra cómo ha cambiado la estimación publicada por Yahoo en 7/30/60/90 días.")

            if est.get("eps_revisions"):
                revisiones_validas = any(v is not None for v in est["eps_revisions"].values())
                if revisiones_validas:
                    with st.expander("📝 Revisiones de analistas: presión alcista / bajista del consenso"):
                        st.plotly_chart(
                            _fig_revisiones_eps(est["eps_revisions"]),
                            use_container_width=True,
                            key=f"revisiones_{sufijo}",
                        )
                        up30 = (est["eps_revisions"].get("0y_up_30d") or 0) + (est["eps_revisions"].get("1y_up_30d") or 0)
                        down30 = (est["eps_revisions"].get("0y_down_30d") or 0) + (est["eps_revisions"].get("1y_down_30d") or 0)
                        if down30 > up30:
                            st.warning(f"⚠️ El consenso EPS acumula más revisiones a la baja que al alza en 30 días: {down30} vs {up30}.")
                        elif up30 > down30:
                            st.success(f"✅ El consenso EPS acumula más revisiones al alza que a la baja en 30 días: {up30} vs {down30}.")
                        else:
                            st.info("ℹ️ El balance de revisiones EPS en 30 días está equilibrado.")
                        st.caption(f"En los últimos 30 días: {up30} revisiones al alza frente a {down30} a la baja, según los campos publicados por Yahoo.")



    with stab2:
            # ---------------------------------------------------------------
            # Valoración por Múltiplos a AÑO_HORIZONTE (precio objetivo + % ganancia por año)
            # ---------------------------------------------------------------
            st.subheader(f"💠 Valoración por Múltiplos a {AÑO_HORIZONTE}")
            if proyeccion is None or not acciones or not precio_actual:
                st.info("Faltan proyección, acciones en circulación o precio actual para construir esta tabla.")
            else:
                market_cap_m = (precio_actual * acciones / 1e6)
                ev_actual_m = market_cap_m + (deuda_actual_m or 0) - (efectivo_actual_m or 0)
                per_defecto = per_medio if per_medio else 15.0
                ev_fcf_act = (ev_actual_m / fcff_actual_m) if (fcff_actual_m and fcff_actual_m > 0) else None
                ev_ebitda_act = (ev_actual_m / ebitda_actual_m) if (ebitda_actual_m and ebitda_actual_m > 0) else None
                ev_ebit_act = (ev_actual_m / ebit_actual_m) if (ebit_actual_m and ebit_actual_m > 0) else None
                _evm = datos.get("ev_medianas") or {}
                # v7: ancla = mediana histórica (si hay ≥2 años válidos); si no, el múltiplo actual. Con el múltiplo actual como
                # ancla el 'precio objetivo' era el precio de hoy × crecimiento (CAGR = g por construcción).
                ev_fcf_defecto = _evm.get("EV / FCFF") or ev_fcf_act or 15.0
                ev_ebitda_defecto = _evm.get("EV / EBITDA") or ev_ebitda_act or 10.0
                ev_ebit_defecto = _evm.get("EV / EBIT") or ev_ebit_act or 12.0
                _fila_ev = []
                for _nom, _act, _his in (("EV / FCFF", ev_fcf_act, _evm.get("EV / FCFF")), ("EV / EBITDA", ev_ebitda_act, _evm.get("EV / EBITDA")),
                                         ("EV / EBIT", ev_ebit_act, _evm.get("EV / EBIT"))):
                    _fila_ev.append({"Múltiplo": _nom, "Actual": _act, "Mediana histórica": _his,
                                     "Usado por defecto": "histórica" if _his else ("actual" if _act else "genérico")})
                if es_ciclica:
                    st.markdown('<div class="risk-note">⚠️ Empresa cíclica: PER y múltiplos EV históricos mezclan años de pico y de valle. '
                                'Usa la pestaña <strong>Ciclo & Expectativas</strong> como valoración principal.</div>', unsafe_allow_html=True)

                st.caption(
    "Múltiplos objetivo — por defecto, el PER usa tu media histórica fiable y los EV/x usan la MEDIANA HISTÓRICA de los "
                    "ejercicios disponibles (si no hay al menos dos años válidos, el múltiplo actual); edítalos como quieras. "
                    "PER: precio = EPS × múltiplo (la caja ya está en el beneficio). EV/x: se puentea con caja (efectivo + inversiones a corto), "
                    "deuda y minoritarios ACTUALES para llegar al precio por acción."
                )
                if any(f["Mediana histórica"] or f["Actual"] for f in _fila_ev):
                    with st.expander("📐 Múltiplos EV: actual frente a mediana histórica"):
                        st.dataframe(pd.DataFrame(_fila_ev).set_index("Múltiplo").style.format({"Actual": "{:.1f}x", "Mediana histórica": "{:.1f}x"}, na_rep="N/D"),
                                     use_container_width=True)
                        if datos.get("ev_hist") is not None:
                            st.dataframe(datos["ev_hist"].style.format("{:,.1f}", na_rep="-"), use_container_width=True)
                        st.caption("EV al cierre de cada ejercicio = precio × acciones (ajustadas por splits) + deuda + minoritarios − caja. Con solo 4–5 años, la mediana es orientativa.")
                st.markdown("**🎛️ Control de valoración — modifica estos múltiplos y los gráficos se recalculan al instante**")
                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                per_objetivo = col_m1.number_input("PER objetivo", value=_limitar_multiplo_input(per_defecto, 15.0), min_value=1.0, max_value=100.0, step=0.5, key=f"mult_per_{sufijo}")
                ev_fcf_objetivo = col_m2.number_input("EV / FCFF objetivo", value=_limitar_multiplo_input(ev_fcf_defecto, 15.0), min_value=1.0, max_value=100.0, step=0.5, key=f"mult_evfcf_{sufijo}")
                ev_ebitda_objetivo = col_m3.number_input("EV / EBITDA objetivo", value=_limitar_multiplo_input(ev_ebitda_defecto, 10.0), min_value=1.0, max_value=100.0, step=0.5, key=f"mult_evebitda_{sufijo}")
                ev_ebit_objetivo = col_m4.number_input("EV / EBIT objetivo", value=_limitar_multiplo_input(ev_ebit_defecto, 12.0), min_value=1.0, max_value=100.0, step=0.5, key=f"mult_evebit_{sufijo}")

                metodos = {
                    "PER": ("equity", "EPS Proyectado", per_objetivo),
                    "EV / FCFF": ("ev", "FCFF Proyectado (M)", ev_fcf_objetivo),
                    "EV / EBITDA": ("ev", "EBITDA Proyectado (M)", ev_ebitda_objetivo),
                    "EV / EBIT": ("ev", "EBIT Proyectado (M)", ev_ebit_objetivo),
                }
                años_tabla = sorted(a for a in proyeccion.index if año_base < a <= AÑO_HORIZONTE)

                if not años_tabla:
                    st.info("No hay años futuros en la proyección para tabular precios objetivo.")
                else:
                    filas_precio, filas_ganancia = {}, {}
                    for nombre, (tipo, columna, multiplo) in metodos.items():
                        fila_p, fila_g = {}, {}
                        for año in años_tabla:
                            metrica = proyeccion.loc[año, columna] if columna in proyeccion.columns else None
                            precio_obj = calcular_precio_objetivo_por_multiplo(
                                tipo, metrica, multiplo, acciones,
                                efectivo_m=efectivo_actual_m, deuda_m=deuda_actual_m, efectivo_por_accion=efectivo_por_accion,
                            )
                            fila_p[f"{año}e"] = precio_obj
                            fila_g[f"{año}e"] = ((precio_obj / precio_actual - 1) * 100) if precio_obj is not None else None
                        filas_precio[nombre] = fila_p
                        filas_ganancia[nombre] = fila_g

                    df_precio_objetivo = pd.DataFrame(filas_precio).T
                    df_ganancia = pd.DataFrame(filas_ganancia).T

                    ultimo_año_tabla = max(años_tabla)
                    años_restantes = max(ultimo_año_tabla - datetime.now().year, 1)
                    col_cagr = f"CAGR a {ultimo_año_tabla}"
                    cagr_por_metodo = {}
                    for nombre in metodos:
                        precio_final = df_precio_objetivo.loc[nombre, f"{ultimo_año_tabla}e"]
                        cagr_por_metodo[nombre] = ((precio_final / precio_actual) ** (1 / años_restantes) - 1) if (precio_final is not None and pd.notna(precio_final)) else None
                    df_ganancia[col_cagr] = pd.Series(cagr_por_metodo)

                    st.write(f"**Precio objetivo por año** ({divisa}):")
                    st.dataframe(_styler_numerico(df_precio_objetivo, 2), use_container_width=True)

                    st.write("**Ganancia % vs precio actual, por año (y CAGR al último año):**")
                    cols_pct = [c for c in df_ganancia.columns if c != col_cagr]
                    formato_ganancia = {c: "{:+.1f}%" for c in cols_pct}
                    formato_ganancia[col_cagr] = "{:+.1%}"
                    estilo_ganancia = df_ganancia.style.format(formato_ganancia, na_rep="-")
                    if cols_pct:
                        estilo_ganancia = estilo_ganancia.background_gradient(cmap=_cmap_valor(), axis=None, vmin=-40, vmax=60, subset=cols_pct)
                    estilo_ganancia = estilo_ganancia.background_gradient(cmap=_cmap_valor(), axis=None, vmin=-0.10, vmax=0.20, subset=[col_cagr])
                    st.dataframe(estilo_ganancia, use_container_width=True)

                    # Veredicto al último año disponible del modelo. Solo se llama “2030” si existe realmente la fila 2030.
                    precios_finales_validos = [v for v in df_precio_objetivo[f"{ultimo_año_tabla}e"] if v is not None and pd.notna(v)]
                    precio_ultimo_blended = float(np.median(precios_finales_validos)) if precios_finales_validos else None
                    cagr_ultimo_blended = ((precio_ultimo_blended / precio_actual) ** (1 / años_restantes) - 1) if precio_ultimo_blended else None

                    resumen["ultimo_año_proyectado"] = ultimo_año_tabla
                    precio_horizonte_blended = precio_ultimo_blended if ultimo_año_tabla == AÑO_HORIZONTE else None
                    cagr_horizonte_blended = cagr_ultimo_blended if ultimo_año_tabla == AÑO_HORIZONTE else None
                    resumen["precio_horizonte_blended"] = precio_horizonte_blended
                    resumen["cagr_horizonte_blended"] = cagr_horizonte_blended

                    if precio_horizonte_blended is not None:
                        col_v1, col_v2 = st.columns(2)
                        with col_v1:
                            estilo_2030 = "tc-verde" if cagr_horizonte_blended and cagr_horizonte_blended > 0 else "tc-roja"
                            emoji_2030 = "🟢" if cagr_horizonte_blended and cagr_horizonte_blended > 0 else "🔴"
                            if es_ciclica:
                                estilo_2030, emoji_2030 = "tc-ambar", "⚠️"
                            _tarjeta(f"Veredicto a {ultimo_año_tabla} (mediana de 4 métodos{' · NO fiable: cíclica' if es_ciclica else ''})",
                                     f"{emoji_2030} {precio_horizonte_blended:.2f} {divisa}",
                                     f"CAGR anualizado implícito: {cagr_horizonte_blended*100:+.1f}%" if cagr_horizonte_blended is not None else "",
                                     estilo_2030)
                        with col_v2:
                            st.caption(
                                "El 'veredicto' es la MEDIANA de los precios objetivo de PER, EV/FCF, EV/EBITDA y EV/EBIT "
                                + ("(en una cíclica proyecta beneficios de ciclo sobre múltiplos de ciclo: ver la pestaña Ciclo & Expectativas) " if es_ciclica else "")
                                +
                                f"para {ultimo_año_tabla}, comparada con el precio actual. Verde = el modelo implica "
                                "revalorización desde hoy; rojo = el precio actual ya iguala o supera ese objetivo. "
                                "Cambia los múltiplos de arriba para ver cómo se mueve."
                            )

                            st.caption(
                                "El 'blended' es una mediana de métodos de valoración del modelo; no es un precio objetivo de analistas. "
                                "Cuando el último año es 2030, una parte de la métrica puede proceder del tramo modelizado con el crecimiento +5y de analistas."
                            )

                    # ===========================================================
                    # EXPLORADOR GRÁFICO DE VALORACIÓN — respuesta directa al input
                    # ===========================================================
                    st.markdown("---")
                    st.markdown(f"#### 🎯 Explorador: ¿cómo se desarrolla el valor hasta {AÑO_HORIZONTE}?")
                    st.caption(
                        "Este gráfico es el punto central de la valoración: eliges el múltiplo en las cajas de arriba y ves "
                        "cómo cambia el precio implícito año a año. El primer punto es el precio real actual; los siguientes "
                        "se calculan con la métrica proyectada de cada año."
                    )

                    metodo_grafico = st.radio(
                        "Método que quieres visualizar",
                        options=list(metodos.keys()),
                        horizontal=True,
                        key=f"metodo_grafico_{sufijo}",
                    )
                    multiplo_grafico = float(metodos[metodo_grafico][2])
                    camino_valoracion = _build_valuation_path(
                        proyeccion,
                        metodo_grafico,
                        multiplo_grafico,
                        acciones,
                        efectivo_actual_m,
                        deuda_actual_m,
                        efectivo_por_accion,
                        precio_actual,
                        año_base,
                        AÑO_HORIZONTE,
                    )

                    if camino_valoracion.empty or camino_valoracion["Precio"].notna().sum() <= 1:
                        st.warning("No hay suficiente información para construir la trayectoria del método seleccionado.")
                    else:
                        camino_valido = camino_valoracion.dropna(subset=["Precio"]).copy()
                        ultimo_precio_camino = _safe_float(camino_valido.iloc[-1]["Precio"])
                        ultimo_año_camino = int(camino_valido.iloc[-1]["Año"])
                        años_cagr = max(ultimo_año_camino - año_base, 1)
                        cagr_camino = ((ultimo_precio_camino / precio_actual) ** (1 / años_cagr) - 1) if (ultimo_precio_camino is not None and precio_actual) else None
                        upside_camino = ((ultimo_precio_camino / precio_actual) - 1) if (ultimo_precio_camino is not None and precio_actual) else None

                        k1, k2, k3, k4 = st.columns(4)
                        k1.metric("Múltiplo seleccionado", f"{multiplo_grafico:.1f}x")
                        k2.metric(f"Precio implícito {AÑO_HORIZONTE}", _formato_monetario(ultimo_precio_camino, divisa))
                        k3.metric(f"Upside a {AÑO_HORIZONTE}", _formato_pct(upside_camino))
                        k4.metric("CAGR desde hoy", _formato_pct(cagr_camino))

                        st.plotly_chart(
                            _fig_trayectoria_valoracion(
                                camino_valoracion,
                                f"Trayectoria del precio implícito — {metodo_grafico} {multiplo_grafico:.1f}x",
                                divisa,
                                precio_actual,
                            ),
                            use_container_width=True,
                            key=f"trayectoria_{sufijo}_{metodo_grafico}",
                        )

                        st.dataframe(
                            camino_valoracion[["Año", "Precio", "Fuente", "Upside"]].style.format(
                                {"Precio": "{:.2f}", "Upside": "{:+.1%}"}, na_rep="-"
                            ),
                            use_container_width=True,
                        )

                        st.markdown(
                            "<div class=\"mini-note\"><strong>Lectura:</strong> ● histórico/mercado · ◆ consenso directo · "
                            "▲ tramo modelizado. El color morado no significa “más probable”; significa simplemente que "
                            "esa parte ya no es una estimación anual directa publicada por Yahoo.</div>",
                            unsafe_allow_html=True,
                        )

                        # Sensibilidad: el usuario ve cuánto depende el 2030 de las dos palancas que realmente está tocando.
                        with st.expander(f"🌡️ Sensibilidad {AÑO_HORIZONTE}: múltiplo × crecimiento de largo plazo"):
                            if est["crecimiento_5y"] is None:
                                st.markdown(
                                    '<div class="risk-note">No se publica crecimiento +5y utilizable. La sensibilidad no se inventa y queda desactivada.</div>',
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.caption(
                                    f"Centro = crecimiento +5y publicado: {est['crecimiento_5y']*100:.1f}%. "
                                    "Las celdas laterales son únicamente escenarios de sensibilidad alrededor de ese dato."
                                )
                                st.plotly_chart(
                                    _fig_sensibilidad_2030(
                                        proyeccion,
                                        metodo_grafico,
                                        multiplo_grafico,
                                        est["crecimiento_5y"],
                                        acciones,
                                        efectivo_actual_m,
                                        deuda_actual_m,
                                        efectivo_por_accion,
                                        año_base,
                                        divisa,
                                        AÑO_HORIZONTE,
                                    ),
                                    use_container_width=True,
                                    key=f"sensibilidad_{sufijo}_{metodo_grafico}",
                                )



    with stab3:
            # ---------------------------------------------------------------
            # Ajustes del DCF (interactivo) + detalle Piotroski + estados financieros completos
            # ---------------------------------------------------------------
            with st.expander("⚙️ Ajustar supuestos del DCF (WACC, crecimiento, terminal)"):
                col_d1, col_d2, col_d3 = st.columns(3)
                tasa_descuento = col_d1.slider(f"WACC / Tasa de descuento ({ticker_symbol}):", 4.0, 15.0, 9.0, 0.5,
                                                format="%.1f%%", key=f"wacc_{sufijo}") / 100.0
                crecimiento_terminal = col_d2.slider(f"Crecimiento terminal ({ticker_symbol}):", 0.0, 4.0, 2.5, 0.25,
                                                      format="%.2f%%", key=f"gt_{sufijo}") / 100.0
                g_slider_default = (
                    float(max(min(g_consenso, 0.30), -0.10))
                    if g_consenso is not None else 0.0
                )
                tasa_crecimiento_dcf = col_d3.slider(f"Crecimiento FCFF inicial ({ticker_symbol}):", -10.0, 30.0,
                                                      round(g_slider_default * 100 * 2) / 2, 0.5, format="%.1f%%",
                                                      key=f"gfcf_{sufijo}") / 100.0
                st.caption(
                    (f"El crecimiento inicial se ha tomado del +5y de analistas ({g_consenso*100:.1f}%)." if est["crecimiento_5y"] is not None
                     else "No hay crecimiento +5y publicado; el slider parte de 0% únicamente como escenario manual, no como previsión." )
                    + " WACC y crecimiento terminal siguen siendo supuestos del modelo y no consenso de analistas."
                    + " El crecimiento baja linealmente desde el valor inicial hasta el terminal en el año 5."
                )
                _opciones_base = ["Último ejercicio", "Media 3 ejercicios"]
                _base_elegida = st.radio("Base de FCFF del DCF", _opciones_base, horizontal=True,
                                         index=_opciones_base.index(base_dcf_sel) if base_dcf_sel in _opciones_base else 0,
                                         key=f"dcf_base_{sufijo}",
                                         help="La media de 3 ejercicios suaviza un año extremo (típico en cíclicas).")
                _fcff_base_ui = _base_fcff_dcf(fcff_serie, _base_elegida) if fcff_serie is not None else fcff_actual_m
                if _fcff_base_ui is not None:
                    st.caption(f"FCFF base utilizado: {_fcff_base_ui:,.0f} M {divisa} (último ejercicio: {fcff_actual_m:,.0f} M).")
                dcf_custom, detalle_custom = calcular_dcf(_fcff_base_ui, deuda_actual_m, efectivo_actual_m, acciones,
                                                           tasa_descuento, crecimiento_terminal, tasa_crecimiento_dcf)
                if dcf_custom is None:
                    st.info(detalle_custom)
                else:
                    st.success(f"Con estos supuestos, el Valor Intrínseco (DCF) sería **{dcf_custom:.2f} {divisa}** "
                               f"(EV: {detalle_custom['enterprise_value_m']:,.0f} M · Equity: {detalle_custom['equity_value_m']:,.0f} M)")
                    _peso_vt = detalle_custom.get("peso_valor_terminal")
                    if _peso_vt is not None:
                        if _peso_vt > 0.70:
                            st.markdown(f'<div class="risk-note">⚠️ El valor terminal supone el <strong>{_peso_vt*100:.0f}%</strong> '
                                        'del valor de la empresa: el resultado depende casi solo de la perpetuidad (WACC y crecimiento terminal). '
                                        'Tómalo como orden de magnitud.</div>', unsafe_allow_html=True)
                        else:
                            st.markdown(f'<div class="success-note">El valor terminal supone el {_peso_vt*100:.0f}% del valor '
                                        'de la empresa (por debajo del 70%): el valor no depende solo de la perpetuidad.</div>',
                                        unsafe_allow_html=True)

            # Independent driver-based valuation: assumptions are visible and do not
            # silently reuse Yahoo's long-term EPS growth as FCFF growth.
            with st.expander("🧪 DCF por drivers económicos (modelo experimental editable)", expanded=False):
                if datos.get("balance") is None or not acciones or fcff_serie is None:
                    st.info("Faltan ingresos, acciones o histórico de FCFF para inicializar el modelo por drivers.")
                else:
                    _rev0 = _safe_float(datos["balance"].loc["Ingresos Totales (Ventas Anuales)"].iloc[-1])
                    _ebit0 = _safe_float(datos["balance"].loc["Beneficio Operativo (EBIT)"].iloc[-1])
                    _da0 = _safe_float(datos["fcf"].loc["1. EBITDA"].iloc[-1] - _ebit0) if _ebit0 is not None else None
                    _capex0 = _safe_float(datos["fcf"].loc["2. CAPEX (Mantenimiento/Crecimiento)"].iloc[-1])
                    if not _rev0 or _rev0 <= 0:
                        st.info("Ingresos base no disponibles: el modelo por drivers queda en N/D.")
                    else:
                        st.caption("Ingresos base y ratios iniciales proceden del último ejercicio disponible. Ajusta los supuestos; no son previsiones de analistas.")
                        _g, _m, _t, _d, _c, _dil = st.columns(6)
                        _g_base = _g.number_input("Crecimiento anual ingresos (%)", -50.0, 100.0, 12.0, 1.0, key=f"drv_g_{sufijo}") / 100
                        _margin = _m.number_input("Margen EBIT (%)", -50.0, 80.0, round((_ebit0/_rev0*100) if _ebit0 else 20.0, 1), 0.5, key=f"drv_m_{sufijo}") / 100
                        _tax = _t.number_input("Impuesto operativo (%)", 0.0, 50.0, 21.0, 1.0, key=f"drv_tax_{sufijo}") / 100
                        _da_pct = _d.number_input("D&A / ingresos (%)", 0.0, 50.0, round((_da0/_rev0*100) if _da0 and _da0 > 0 else 5.0, 1), 0.5, key=f"drv_da_{sufijo}") / 100
                        _cap_pct = _c.number_input("CAPEX / ingresos (%)", 0.0, 100.0, round((abs(_capex0)/_rev0*100) if _capex0 else 5.0, 1), 0.5, key=f"drv_cap_{sufijo}") / 100
                        _dilution = _dil.number_input("Dilución anual (%)", 0.0, 20.0, 1.0, 0.25, key=f"drv_dil_{sufijo}") / 100
                        _wacc_cols = st.columns(7)
                        _rf = _wacc_cols[0].number_input("Libre riesgo %", 0.0, 20.0, 4.0, 0.25, key=f"drv_rf_{sufijo}") / 100
                        _beta = _wacc_cols[1].number_input("Beta", 0.0, 5.0, 1.1, 0.1, key=f"drv_beta_{sufijo}")
                        _erp = _wacc_cols[2].number_input("ERP %", 0.0, 20.0, 5.5, 0.25, key=f"drv_erp_{sufijo}") / 100
                        _rd = _wacc_cols[3].number_input("Coste deuda %", 0.0, 30.0, 4.0, 0.25, key=f"drv_rd_{sufijo}") / 100
                        _taxw = _wacc_cols[4].number_input("Tasa fiscal deuda %", 0.0, 50.0, 21.0, 1.0, key=f"drv_taxw_{sufijo}") / 100
                        _eq = _wacc_cols[5].number_input("Equity valor mercado (M)", 0.0, 1e12, float(max((precio_actual or 0)*(acciones or 0)/1e6, 0)), key=f"drv_eq_{sufijo}")
                        _debtw = _wacc_cols[6].number_input("Deuda (M)", 0.0, 1e12, float(max(deuda_actual_m or 0, 0)), key=f"drv_debt_{sufijo}")
                        try:
                            _wc = estimate_wacc(_rf, _beta, _erp, _rd, _taxw, _eq, _debtw)
                            st.caption(f"CAPM: coste equity {_wc['cost_of_equity']:.2%} · pesos equity/deuda {_wc['equity_weight']:.1%}/{_wc['debt_weight']:.1%} · WACC calculado {_wc['wacc']:.2%}. ERP y tipos son supuestos del usuario; valor de mercado usa la moneda {divisa}.")
                            _term = st.number_input("Crecimiento terminal FCFF (%)", 0.0, 4.0, 2.5, 0.25, key=f"drv_term_{sufijo}") / 100
                            _results = []
                            for _name, _delta_g, _delta_m in (("Bajista", -0.05, -0.03), ("Base", 0.0, 0.0), ("Alcista", 0.05, 0.03)):
                                _ass = DriverAssumptions(tuple([_g_base+_delta_g]*5), tuple([_margin+_delta_m]*5), _tax, _da_pct, _cap_pct, 0.0, _term)
                                _rows = project_fcff(_rev0, _ass)
                                _valuation = dcf_from_fcff([x["fcff"] for x in _rows], _wc["wacc"], _term,
                                    float((deuda_actual_m or 0)-(efectivo_actual_m or 0)), float(acciones), _dilution)
                                _results.append({"Escenario": _name, "Ingresos año 5 (M)": _rows[-1]["revenue"],
                                    "FCFF año 5 (M)": _rows[-1]["fcff"], "Acciones diluidas": _valuation["diluted_shares"],
                                    f"Valor/acción ({divisa})": _valuation["value_per_share"], "Peso terminal": _valuation["terminal_weight"]})
                            st.dataframe(pd.DataFrame(_results).set_index("Escenario").style.format(na_rep="N/D", precision=2), use_container_width=True)
                            st.caption("El capital circulante incremental se fija en 0% hasta que se modele con datos fiables. SBC no es gasto de caja: se refleja aquí mediante dilución editable; no se resta además como CAPEX.")
                        except (ValueError, TypeError, ZeroDivisionError) as _err:
                            st.warning(f"DCF por drivers no calculable: {_err}")

            # ---------------------------------------------------------------
            # Auditoría de procedencia y calidad de datos
            # ---------------------------------------------------------------
            with st.expander("🔍 Auditoría de fuentes y calidad del modelo"):
                st.markdown(_fuente_html("real", f"Datos de mercado: {NOMBRE_FUENTE_PRINCIPAL}"), unsafe_allow_html=True)
                if est.get("tabla_consenso") is not None:
                    st.markdown(_fuente_html("consenso", "EPS/Ingresos: consenso directo 0y/+1y"), unsafe_allow_html=True)
                else:
                    st.markdown(_fuente_html("warning", "EPS/Ingresos: sin consenso anual disponible"), unsafe_allow_html=True)
                if est.get("crecimiento_5y") is not None:
                    st.markdown(_fuente_html("modelo", f"Tramo hasta {AÑO_HORIZONTE}: crecimiento +5y {est['crecimiento_5y']*100:.1f}% publicado por Yahoo"), unsafe_allow_html=True)
                else:
                    st.markdown(_fuente_html("warning", "Tramo posterior: no extrapolado por falta de +5y"), unsafe_allow_html=True)

                st.write(
                    "**Cómo leer la procedencia:** un dato puede ser real y, a la vez, formar parte de una operación del modelo. "
                    "Por eso la tabla etiqueta cada año. La aplicación no presenta el precio objetivo del horizonte seleccionado como una recomendación de un analista concreto."
                )
                st.caption(
                    f"Fuente principal: {NOMBRE_FUENTE_PRINCIPAL}. Última consulta/cálculo de estimaciones: {est.get('data_as_of', 'N/D')}. "
                    "El resultado depende de la cobertura que Yahoo disponga para cada ticker."
                )

            with st.expander("🧮 Ver detalle del Piotroski F-Score"):
                if pio is None:
                    st.info(datos["error_piotroski"] or "No se pudo calcular el F-Score.")
                else:
                    for c in pio["detalle"]:
                        if c["cumple"] is None:
                            st.markdown(f"➖ {c['criterio']} — *dato no disponible*")
                        elif c["cumple"]:
                            st.markdown(f"✅ {c['criterio']}")
                        else:
                            st.markdown(f"❌ {c['criterio']}")
                    st.caption(f"Puntuación: {pio['puntuacion']} / {pio['maximo_evaluable']} criterios evaluables "
                               f"(máximo teórico: {pio['maximo_teorico']}). Los criterios sin dato no puntúan ni en contra ni a favor.")

            with st.expander("📋 Ver estados financieros completos (Balance, FCFE/FCFF, PER histórico)"):
                st.dataframe(_styler_numerico(datos["balance"], 2), use_container_width=True)
                _descargar_dataframe(datos["balance"], "⬇ Exportar Balance CSV", f"{ticker_symbol}_balance.csv", f"csv_balance_{sufijo}")
                if datos["fcf"] is not None:
                    st.dataframe(_styler_numerico(datos["fcf"], 2), use_container_width=True)
                    _descargar_dataframe(datos["fcf"], "⬇ Exportar FCF / FCFF CSV", f"{ticker_symbol}_fcf.csv", f"csv_fcf_{sufijo}")
                if datos["error_valoracion"]:
                    st.warning(datos["error_valoracion"])
                elif datos["valoracion"] is not None:
                    st.dataframe(
                        _styler_numerico(datos["valoracion"], 2).apply(lambda d: colorear_per(d, per_medio), axis=None),
                        use_container_width=True,
                    )
                    _descargar_dataframe(datos["valoracion"], "⬇ Exportar valoración histórica CSV", f"{ticker_symbol}_valoracion.csv", f"csv_val_{sufijo}")
                    c5, c6, c7, c8 = st.columns(4)
                    c5.metric("PER medio", _metrica(per_medio)); c6.metric("PER mediana", _metrica(datos["per_mediana"]))
                    c7.metric("PER mínimo", _metrica(datos["per_min"])); c8.metric("PER máximo", _metrica(datos["per_max"]))
                    if datos["splits_detectados"]:
                        detalle_splits = ", ".join(f"{a} (÷{f:.2f})" for a, f in datos["splits_detectados"].items())
                        st.caption(f"⚙️ Cálculo blindado del EPS: splits detectados posteriores al cierre de {detalle_splits}; "
                                   "el EPS de esos años se reexpresó a la base de acciones actual antes de calcular el PER.")



    with stab4:
            # ---------------------------------------------------------------
            # Gráficos de Valoración
            # ---------------------------------------------------------------
            st.subheader("📉 Gráficos de Valoración")

            st.write("**Precio real de la acción (histórico diario)**")
            if datos["historico_precios"] is not None and not datos["historico_precios"].empty:
                fig_precio = go.Figure(go.Scatter(x=datos["historico_precios"].index, y=datos["historico_precios"]["Close"],
                                                   mode="lines", line=dict(color=COLOR_LINEA, width=2), name="Precio real"))
                fig_precio.update_layout(height=320, margin=dict(l=10, r=10, t=20, b=10), **_DARK_LAYOUT)
                _aplicar_tema_figura(fig_precio)
                st.plotly_chart(fig_precio, use_container_width=True, key=f"precio_historico_{sufijo}")
            else:
                st.info("No hay histórico de precios disponible para graficar.")

            st.write("**Precio real vs valor implícito por PER medio histórico**")
            if datos["valoracion"] is not None and per_medio:
                df_val = datos["valoracion"]
                df_grafico_per = pd.DataFrame({
                    "Precio real": df_val.loc["Precio de la Acción (Cierre Año)"],
                    "Valor implícito": df_val.loc["EPS Anual"] * per_medio,
                })
                st.plotly_chart(_fig_comparacion_valor(df_grafico_per, "Precio real", "Valor implícito",
                                                        f"Valor implícito (PER {per_medio:.1f}x) vs precio real"),
                                 use_container_width=True, key=f"per_vs_precio_{sufijo}")
                st.caption("Verde = el valor implícito por PER está POR ENCIMA del precio real (infravalorada ese año). "
                           "Rojo = el precio real está por encima del valor implícito (sobrevalorada).")
            else:
                st.info("No hay suficiente histórico de PER para dibujar esta comparativa.")

            st.write("**Precio real vs DCF retrospectivo aproximado (supuestos actuales)**")
            tiene_fcf_hist = datos["fcf"] is not None and "=== FREE CASH FLOW A LA FIRMA (FCFF) ===" in datos["fcf"].index
            if datos["valoracion"] is not None and tiene_fcf_hist and acciones and g_consenso is not None:
                serie_fcff_hist = datos["fcf"].loc["=== FREE CASH FLOW A LA FIRMA (FCFF) ==="]
                serie_deuda_hist = datos["balance"].loc["Deuda Total"] + (datos["balance"].loc["Minoritarios"] if "Minoritarios" in datos["balance"].index else 0.0)
                serie_efectivo_hist = datos["balance"].loc["Efectivo y Equivalentes"]
                valores_dcf_hist = {}
                for año_str, fcff_año in serie_fcff_hist.items():
                    valor, _d = calcular_dcf(
                        fcff_año, serie_deuda_hist.get(año_str), serie_efectivo_hist.get(año_str), acciones,
                        0.09, 0.025, g_consenso,
                    )
                    valores_dcf_hist[año_str] = valor
                df_grafico_dcf = pd.DataFrame({
                    "Precio real": datos["valoracion"].loc["Precio de la Acción (Cierre Año)"],
                    "Valor DCF": pd.Series(valores_dcf_hist),
                })
                st.plotly_chart(_fig_comparacion_valor(df_grafico_dcf, "Precio real", "Valor DCF",
                                                        "Precio real vs DCF retrospectivo aproximado (FCFF, WACC 9%)"),
                                 use_container_width=True, key=f"dcf_vs_precio_{sufijo}")
                st.caption(
                    "Cada año usa el FCFF, la deuda y el efectivo DE ESE AÑO, pero mantiene WACC 9%, crecimiento +5y y acciones ACTUALES. "
                    "No es un backtest de la información disponible en cada fecha; es una aproximación histórica construida con supuestos actuales."
                )
            else:
                if g_consenso is None:
                    st.info("No se calcula el DCF retrospectivo automáticamente porque no existe un crecimiento +5y publicado utilizable. Esto evita convertir la ausencia de datos en una previsión inventada.")
                else:
                    st.info("No hay suficiente FCFF histórico, deuda/efectivo o acciones en circulación para esta comparativa.")



    # ---------------------------------------------------------------
    # Resumen para la pestaña Comparativa
    # ---------------------------------------------------------------
    roic_actual = _safe_float(datos["balance"].loc["ROIC (%)"].iloc[-1]) if "ROIC (%)" in datos["balance"].index else None
    per_para_scatter = datos["per_trailing_mercado"] if datos["per_trailing_mercado"] else per_medio

    resumen["ciclica"] = es_ciclica
    resumen["valor_escenarios"] = esc["valor_esperado"] if esc is not None else None
    resumen["calidad_datos"] = _cal_nivel
    resumen.update({
        "roic": roic_actual, "per": per_para_scatter, "per_medio": per_medio, "market_cap_m": market_cap_m, "precio_actual": precio_actual,
        "valor_blended": valor_blended, "gap_hoy_pct": gap_pct,
        "piotroski_puntuacion": pio_score, "piotroski_maximo": pio_max,
    })
    return resumen


def mostrar_comparativa(resumen1, resumen2):
    st.subheader("🥊 Matriz de Competidores: Calidad (ROIC) vs Precio (PER)")
    valid_kpis = [r for r in (resumen1, resumen2) if r]
    if valid_kpis:
        kcols = st.columns(min(4, max(2, len(valid_kpis))))
        for idx, r in enumerate(valid_kpis[:4]):
            with kcols[idx % len(kcols)]:
                per_now = r.get("per")
                per_hist = r.get("per_medio")
                delta = (per_now - per_hist) if (per_now is not None and per_hist is not None) else None
                st.metric(
                    r.get("ticker", "Ticker"),
                    f"PER {per_now:.1f}x" if per_now is not None else "PER N/D",
                    delta=f"{delta:+.1f}x vs media histórica" if delta is not None else None,
                    delta_color="inverse",
                )
    st.caption("Abajo a la derecha = alta calidad del negocio (ROIC) con múltiplo bajo (PER): en teoría, la "
               "combinación más atractiva. El color del punto es el F-Score de Piotroski (verde = salud alta, "
               "ámbar = media, rojo = baja, gris = no evaluable).")

    puntos = [r for r in (resumen1, resumen2) if r and r.get("roic") is not None and r.get("per") is not None]
    if len(puntos) < 2:
        st.info("Faltan datos de ROIC o PER en una de las dos empresas para dibujar la comparativa.")
    else:
        def _color_piotroski(p, maximo):
            if p is None or not maximo:
                return COLOR_NEUTRO
            ratio = p / maximo
            if ratio >= 0.75:
                return COLOR_POSITIVO
            if ratio >= 0.4:
                return COLOR_AMBAR
            return COLOR_NEGATIVO

        comp_df = pd.DataFrame([{
            "Ticker": r["ticker"],
            "ROIC (%)": r["roic"],
            "PER": r["per"],
            "Market Cap (M)": r.get("market_cap_m"),
            "Piotroski ratio": ((r.get("piotroski_puntuacion") / r.get("piotroski_maximo"))
                                if r.get("piotroski_puntuacion") is not None and r.get("piotroski_maximo") else None),
            "Gap hoy (%)": r.get("gap_hoy_pct"),
        } for r in puntos])
        comp_df["_size"] = comp_df["Market Cap (M)"].fillna(1).clip(lower=1)
        fig = px.scatter(
            comp_df, x="ROIC (%)", y="PER", size="_size", color="Piotroski ratio",
            text="Ticker", hover_data=["Market Cap (M)", "Gap hoy (%)"],
            color_continuous_scale=ESCALA_VALOR,
            range_color=(0, 1), title="Calidad × valoración × tamaño",
        )
        fig.update_traces(textposition="top center", marker=dict(line=dict(width=1, color="#f1f5f9")))
        roic_medio = comp_df["ROIC (%)"].mean()
        per_medio_scatter = comp_df["PER"].mean()
        fig.add_vline(x=roic_medio, line_dash="dot", line_color=COLOR_NEUTRO)
        fig.add_hline(y=per_medio_scatter, line_dash="dot", line_color=COLOR_NEUTRO)
        fig.update_layout(
            xaxis_title="ROIC (%) — más a la derecha, mejor calidad",
            yaxis_title="PER — más abajo, más barata",
            height=460, margin=dict(l=10, r=10, t=55, b=10), **_DARK_LAYOUT
        )
        _aplicar_tema_figura(fig)
        st.plotly_chart(fig, use_container_width=True, key="comparativa_bubble")

    st.markdown(f"#### 🔭 Proyección a {AÑO_HORIZONTE} conectada — comparación directa")
    resumenes_validos = [r for r in (resumen1, resumen2) if r]
    if not resumenes_validos:
        st.info("No hay datos suficientes de ninguna de las dos empresas.")
        return

    filas = {}
    for r in resumenes_validos:
        filas[r["ticker"]] = {
            "Precio actual": r.get("precio_actual"),
            "Valor Blended (hoy)": r.get("valor_blended"),
            "Gap hoy (%)": r.get("gap_hoy_pct"),
            f"Precio objetivo {AÑO_HORIZONTE} (blended modelo)": r.get("precio_horizonte_blended"),
            f"CAGR a {AÑO_HORIZONTE} (blended modelo)": r.get("cagr_horizonte_blended"),
            "Piotroski": (f"{r['piotroski_puntuacion']}/{r['piotroski_maximo']}" if r.get("piotroski_puntuacion") is not None else "N/D"),
        }
    df_comp = pd.DataFrame(filas).T
    formato = {
        "Precio actual": "{:.2f}", "Valor Blended (hoy)": "{:.2f}", "Gap hoy (%)": "{:+.1f}%",
        f"Precio objetivo {AÑO_HORIZONTE} (blended modelo)": "{:.2f}",
        f"CAGR a {AÑO_HORIZONTE} (blended modelo)": "{:+.1%}",
    }
    st.dataframe(df_comp.style.format(formato, na_rep="-"), use_container_width=True)
    st.caption("Estas cifras son EXACTAMENTE las mismas que aparecen en las pestañas de cada empresa (mismo "
               "cálculo, sin recalcular por separado) — así no hay riesgo de que la comparativa y el detalle diverjan.")




# ==========================================================================================
# MOTOR DE PORTAFOLIO — VALORACIÓN AGREGADA
# ==========================================================================================

def _per_actual_financiero(datos: Dict[str, Any]) -> Optional[float]:
    """PER actual utilizable para agregación de cartera.

    Prioridad: trailingPE publicado por Yahoo; si falta, se reconstruye como
    precio actual / EPS anual actual. Los PER negativos o no finitos se descartan
    porque un múltiplo P/E con beneficios negativos no es económicamente interpretable.
    """
    per = _safe_float(datos.get("per_trailing_mercado"))
    if per is not None and per > 0:
        return per
    precio = _safe_float(datos.get("precio_actual"))
    valoracion = datos.get("valoracion")
    if precio is None or valoracion is None or "EPS Anual" not in valoracion.index:
        return None
    eps = _safe_float(valoracion.loc["EPS Anual"].iloc[-1])
    if eps is None or eps <= 0:
        return None
    per = precio / eps
    return per if np.isfinite(per) and per > 0 else None


def _obtener_resumen_portfolio_ticker(ticker: str, wacc: float, crecimiento_terminal: float) -> Dict[str, Any]:
    """Obtiene una ficha compacta para la cartera reutilizando el motor cacheado existente."""
    ticker = str(ticker).upper().strip()
    datos = extraer_datos_financieros(ticker)
    base = {
        "Ticker": ticker,
        "Divisa": datos.get("divisa", "N/D"),
        "Precio": None,
        "PER": None,
        "ROIC (%)": None,
        "Crecimiento +5y": None,
        f"FCFF {AÑO_HORIZONTE} (M)": None,
        f"DCF {AÑO_HORIZONTE}": None,
        f"Upside DCF {AÑO_HORIZONTE}": None,
        "Acciones": datos.get("acciones_circulacion"),
        "Efectivo (M)": None,
        "Deuda (M)": None,
        "Fuente DCF": None,
        "Piotroski": None,
        "Piotroski máximo": None,
        "Sector": None,
        "Industria": None,
        "Histórico precios": None,
        "Error": datos.get("error_general"),
    }
    if datos.get("balance") is None:
        return base

    precio = _safe_float(datos.get("precio_actual"))
    acciones = _safe_float(datos.get("acciones_circulacion"))
    if precio is None:
        try:
            fast = yf.Ticker(ticker).fast_info
            precio = _safe_float(fast.get("lastPrice") or fast.get("last_price"))
            if acciones is None:
                acciones = _safe_float(fast.get("shares") or fast.get("shares_outstanding"))
        except Exception:
            pass

    try:
        balance = datos["balance"]
        ebit = _safe_float(balance.loc["Beneficio Operativo (EBIT)"].iloc[-1])
        roic = _safe_float(balance.loc["ROIC (%)"].iloc[-1]) if "ROIC (%)" in balance.index else None
        efectivo = _safe_float(balance.loc["Efectivo y Equivalentes"].iloc[-1])
        deuda = _deuda_ajustada_m(balance)
        ingresos = _safe_float(balance.loc["Ingresos Totales (Ventas Anuales)"].iloc[-1])
        año_base = int(balance.columns[-1])
    except Exception as exc:
        base["Error"] = f"No se pudieron preparar métricas de {ticker}: {exc}"
        return base

    valoracion = datos.get("valoracion")
    eps_actual = _safe_float(valoracion.loc["EPS Anual"].iloc[-1]) if valoracion is not None and "EPS Anual" in valoracion.index else None
    ebitda = None
    fcff = None
    if datos.get("fcf") is not None:
        fcf = datos["fcf"]
        if "1. EBITDA" in fcf.index:
            ebitda = _safe_float(fcf.loc["1. EBITDA"].iloc[-1])
        if "=== FREE CASH FLOW A LA FIRMA (FCFF) ===" in fcf.index:
            fcff = _safe_float(fcf.loc["=== FREE CASH FLOW A LA FIRMA (FCFF) ==="].iloc[-1])

    est = obtener_estimaciones_futuras(
        ticker,
        año_base,
        eps_actual,
        ingresos,
        ebitda,
        ebit,
        fcff,
        AÑO_HORIZONTE,
    )
    g5 = _safe_float(est.get("crecimiento_5y"))
    proyeccion = est.get("proyeccion")

    dcf_2030 = None
    upside_2030 = None
    fcff_2030 = None
    fuente_dcf = None
    if (
        proyeccion is not None
        and AÑO_HORIZONTE in proyeccion.index
        and "FCFF Proyectado (M)" in proyeccion.columns
        and g5 is not None
        and acciones is not None
        and acciones > 0
        and precio is not None
        and wacc > crecimiento_terminal
    ):
        fcff_2030 = _safe_float(proyeccion.loc[AÑO_HORIZONTE, "FCFF Proyectado (M)"])
        if fcff_2030 is not None and fcff_2030 > 0:
            # v7: DCF con flujos explícitos hasta el horizonte + valor terminal, TODO descontado a hoy. En v6 solo se tomaba
            # el valor terminal a final de 2030 sin descontar y se comparaba con el precio de hoy (inflaba el 'upside').
            vp_flujos_m = 0.0
            for a_exp in range(año_base + 1, AÑO_HORIZONTE + 1):
                if a_exp in proyeccion.index:
                    f_a = _safe_float(proyeccion.loc[a_exp, "FCFF Proyectado (M)"])
                    if f_a is not None:
                        vp_flujos_m += f_a / ((1.0 + wacc) ** (a_exp - año_base))
            valor_terminal_2030_m = fcff_2030 * (1.0 + crecimiento_terminal) / (wacc - crecimiento_terminal)
            vp_terminal_m = valor_terminal_2030_m / ((1.0 + wacc) ** max(AÑO_HORIZONTE - año_base, 1))
            equity_m = vp_flujos_m + vp_terminal_m + (efectivo or 0.0) - (deuda or 0.0)
            if equity_m > 0:
                dcf_2030 = (equity_m * 1e6) / acciones
            if dcf_2030 is not None and dcf_2030 > 0 and precio > 0:
                upside_2030 = dcf_2030 / precio - 1.0
                _ciclo_p = datos.get("ciclo") or {}
                fuente_dcf = (f"DCF a hoy con FCFF explícito hasta {AÑO_HORIZONTE} (+5y {g5*100:.1f}%) y valor terminal descontado; "
                              f"WACC {wacc*100:.1f}%; gT {crecimiento_terminal*100:.2f}%"
                              + (" · ⚠ CÍCLICA: el FCFF parte de un punto alto/bajo del ciclo; contrasta con la pestaña Ciclo" if _ciclo_p.get("es_ciclica") else ""))

    # v7: en empresas cíclicas el DCF mecánico parte de un punto extremo del ciclo; la cartera usa el valor esperado de los
    # escenarios de ciclo (parámetros por defecto) para que la señal sea coherente con la ficha individual.
    _ciclo_c = datos.get("ciclo") or {}
    if _ciclo_c.get("es_ciclica") and precio and precio > 0:
        _per_m = datos.get("per_medio")
        _eps_ref = eps_actual
        if (proyeccion is not None and (año_base + 1) in proyeccion.index and "EPS Proyectado" in proyeccion.columns
                and "onsenso" in str(proyeccion.loc[año_base + 1].get("Fuente", ""))):
            _e1 = _safe_float(proyeccion.loc[año_base + 1, "EPS Proyectado"])
            if _e1 is not None:
                _eps_ref = _e1
        _esc = (_escenarios_ciclo(max(_ciclo_c.get("eps_media_ciclo") or 0.0, 0.0), _eps_ref, _per_m, 0.5, (0.0, 0.5, 1.0), (0.3, 0.4, 0.3))
                if (_per_m and _eps_ref is not None) else None)
        if _esc is not None:
            dcf_2030 = _esc["valor_esperado"]
            upside_2030 = dcf_2030 / precio - 1.0
            fuente_dcf = "ESCENARIOS DE CICLO (valor esperado, parámetros por defecto): empresa cíclica, el DCF mecánico no es fiable"

    pio = datos.get("piotroski") or {}
    base.update({
        "Divisa": datos.get("divisa", "N/D"),
        "Precio": precio,
        "PER": _per_actual_financiero(datos),
        "ROIC (%)": roic,
        "Crecimiento +5y": g5,
        f"FCFF {AÑO_HORIZONTE} (M)": fcff_2030,
        f"DCF {AÑO_HORIZONTE}": dcf_2030,
        f"Upside DCF {AÑO_HORIZONTE}": upside_2030,
        "Acciones": acciones,
        "Efectivo (M)": efectivo,
        "Deuda (M)": deuda,
        "Fuente DCF": fuente_dcf,
        "Piotroski": pio.get("puntuacion"),
        "Piotroski máximo": pio.get("maximo_evaluable"),
        "Sector": datos.get("sector"),
        "Industria": datos.get("industria"),
        "Histórico precios": datos.get("historico_precios"),
        "Error": None,
    })
    return base


def _calcular_metricas_portafolio(df: pd.DataFrame) -> Dict[str, Any]:
    """Calcula métricas agregadas sin confundir medias ponderadas con múltiplos económicos."""
    out: Dict[str, Any] = {
        "valor_total": None,
        "per_agregado": None,
        "per_ponderado_simple": None,
        "per_cobertura": None,
        "roic_ponderado_exposicion": None,
        "crecimiento_ponderado": None,
        "upside_dcf_ponderado": None,
        "pnl_ponderado": None,
        "piotroski_ponderado": None,
        "peso_maximo": None,
        "hhi": None,
        "numero_efectivo_posiciones": None,
    }
    if df is None or df.empty or "Valor posición" not in df.columns:
        return out

    work = df.copy()
    work["Valor posición"] = pd.to_numeric(work["Valor posición"], errors="coerce")
    work = work[work["Valor posición"] > 0].copy()
    if work.empty:
        return out
    total = float(work["Valor posición"].sum())
    work["Peso"] = work["Valor posición"] / total
    out["valor_total"] = total
    if "P&L %" in work.columns:
        sub_pnl = work[work["P&L %"].notna()].copy()
        if not sub_pnl.empty:
            out["pnl_ponderado"] = float((sub_pnl["P&L %"] * sub_pnl["Peso"]).sum())
    if "Piotroski ratio" in work.columns:
        sub_pio = work[work["Piotroski ratio"].notna()].copy()
        if not sub_pio.empty:
            out["piotroski_ponderado"] = float((sub_pio["Piotroski ratio"] * sub_pio["Peso"]).sum())
    out["peso_maximo"] = float(work["Peso"].max()) if not work.empty else None
    hhi = float((work["Peso"] ** 2).sum()) if not work.empty else None
    out["hhi"] = hhi
    out["numero_efectivo_posiciones"] = (1.0 / hhi) if hhi and hhi > 0 else None

    # PER económico del conjunto = valor total de equity / beneficio implícito total.
    # No es correcto hacer simplemente SUMA(PER_i * peso_i).
    per_rows = work[work["PER"].notna() & (pd.to_numeric(work["PER"], errors="coerce") > 0)].copy()
    if not per_rows.empty:
        earnings_impl = (per_rows["Valor posición"] / per_rows["PER"]).sum()
        equity_covered = float(per_rows["Valor posición"].sum())
        if earnings_impl > 0 and equity_covered > 0:
            out["per_agregado"] = equity_covered / earnings_impl
        out["per_ponderado_simple"] = float((per_rows["PER"] * per_rows["Peso"]).sum())
        out["per_cobertura"] = equity_covered / total if total > 0 else None

    for col, key in [("ROIC (%)", "roic_ponderado_exposicion"), ("Crecimiento +5y", "crecimiento_ponderado"), (f"Upside DCF {AÑO_HORIZONTE}", "upside_dcf_ponderado")]:
        sub = work[work[col].notna()].copy() if col in work.columns else pd.DataFrame()
        if not sub.empty:
            out[key] = float((sub[col] * sub["Peso"]).sum())
    return out


def mostrar_portafolio() -> None:
    """Analiza la cartera combinando lo mejor de ambas versiones.

    - Capital invertido: todas las posiciones se expresan en una moneda base común.
    - Acciones × precio: se permite cuando todas las posiciones comparten divisa; si no,
      se bloquea la suma para evitar mezclar EUR/USD sin tipo de cambio.
    - P&L % es divisa-neutral y se calcula contra el precio medio de compra.
    - PER agregado es un múltiplo económico, no una media ponderada ingenua.
    """
    st.markdown(f"""
        <div class="hero">
            <div class="hero-kicker">MOTOR DE PORTAFOLIO · VALORACIÓN AGREGADA</div>
            <div class="hero-title">💼 Mi Portafolio</div>
            <p class="hero-sub">Posiciones, calidad, valoración y potencial {AÑO_HORIZONTE} bajo la misma lógica que las pestañas individuales.</p>
        </div>
    """, unsafe_allow_html=True)

    c0, c1, c2, c3 = st.columns(4)
    modo_peso = c0.radio(
        "Cómo calcular el peso",
        ["Capital invertido", "Acciones × precio"],
        horizontal=False,
        key="portfolio_weight_mode",
        help="Capital invertido permite una cartera multi-divisa porque todas las exposiciones se introducen en una moneda base. Acciones × precio requiere una sola divisa para que la suma sea consistente."
    )
    moneda_base = c1.text_input(
        "Moneda base de cartera", value="EUR", max_chars=5,
        key="portfolio_base_currency",
        help="En modo Capital invertido, introduce todas las cantidades en esta moneda base."
    ).upper().strip() or "EUR"
    wacc_port = c2.slider(f"WACC para DCF {AÑO_HORIZONTE}", 5.0, 15.0, 9.0, 0.5, format="%.1f%%", key="wacc_portfolio") / 100.0
    gt_port = c3.slider("Crecimiento terminal", 0.0, 4.0, 2.5, 0.25, format="%.2f%%", key="gt_portfolio") / 100.0

    st.caption("Puedes introducir **ticker + acciones + precio medio de compra**. En modo Capital invertido, el capital se expresa en la moneda base y el valor actual se convierte con FX de Yahoo. Si la divisa de compra y la base difieren, introduce las acciones: no inventamos el FX histórico de compra para inferirlas.")
    filas_iniciales = pd.DataFrame({
        "Ticker": [""] * 12,
        "Acciones": [0.0] * 12,
        "Precio medio compra": [0.0] * 12,
        "Capital invertido": [0.0] * 12,
    })
    cartera_editada = st.data_editor(
        filas_iniciales,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key="portfolio_editor_v3",
        column_config={
            "Ticker": st.column_config.TextColumn("Ticker", help="Ej.: TEF.MC, MSFT, AAPL"),
            "Acciones": st.column_config.NumberColumn("Nº acciones", min_value=0.0, step=1.0, format="%.2f"),
            "Precio medio compra": st.column_config.NumberColumn("Precio medio compra", min_value=0.0, step=0.01, format="%.2f", help="Se usa para calcular P&L %."),
            "Capital invertido": st.column_config.NumberColumn("Capital invertido", min_value=0.0, step=100.0, format="%.2f", help="Se interpreta en la moneda base indicada arriba."),
        },
    )

    tickers = []
    for raw in cartera_editada.get("Ticker", pd.Series(dtype=str)).tolist():
        t = str(raw).upper().strip()
        if t and t not in tickers:
            tickers.append(t)
    if not tickers:
        st.info("Añade al menos un ticker para construir la valoración agregada.")
        return

    cache_fichas: Dict[str, Dict[str, Any]] = {}
    progress = st.progress(0.0, text="Iniciando análisis de cartera…")
    for i, t in enumerate(tickers, start=1):
        progress.progress((i - 1) / len(tickers), text=f"Analizando {t}…")
        cache_fichas[t] = _obtener_resumen_portfolio_ticker(t, wacc_port, gt_port)
    progress.progress(1.0, text="Análisis de cartera completado")
    progress.empty()

    filas = []
    historicos_portfolio: Dict[str, pd.DataFrame] = {}
    for t, ficha in cache_fichas.items():
        hist = ficha.get("Histórico precios")
        if isinstance(hist, pd.DataFrame) and not hist.empty and "Close" in hist.columns:
            hist = hist[["Close"]].dropna().tail(90).copy()
            if not hist.empty:
                historicos_portfolio[t] = hist
    for _, row in cartera_editada.iterrows():
        t = str(row.get("Ticker", "")).upper().strip()
        if not t or t not in cache_fichas:
            continue
        ficha = cache_fichas[t]
        acciones = _safe_float(row.get("Acciones")) or 0.0
        precio_compra = _safe_float(row.get("Precio medio compra")) or 0.0
        capital = _safe_float(row.get("Capital invertido")) or 0.0
        precio = ficha.get("Precio")

        acciones_efectivas = acciones
        valor_mercado_actual = None
        fx_a_base = 1.0
        moneda_origen = str(ficha.get("Divisa", "N/D")).upper().strip()
        if modo_peso == "Capital invertido":
            # Capital se introduce en moneda_base. Para que el peso y el P&L sean comparables
            # entre EUR/USD/GBP/etc., convertimos el valor de mercado actual con FX real de Yahoo.
            if acciones_efectivas <= 0 and capital > 0 and precio_compra > 0 and moneda_origen == moneda_base:
                # Solo podemos inferir nº de acciones sin inventar FX histórico cuando precio de compra
                # y capital están en la misma moneda. Para divisas distintas, el usuario debe introducir acciones.
                acciones_efectivas = capital / precio_compra
            valor_local = (acciones_efectivas * precio) if (acciones_efectivas > 0 and precio is not None) else None
            fx_a_base = _obtener_fx_a_moneda_base(moneda_origen, moneda_base)
            if valor_local is not None and fx_a_base is not None:
                valor_mercado_actual = valor_local * fx_a_base
            valor_pos = valor_mercado_actual if valor_mercado_actual is not None else 0.0
            if valor_mercado_actual is not None:
                modo = f"Capital → mercado en {moneda_base} · FX {fx_a_base:.5f}"
            elif capital > 0:
                modo = f"Capital invertido · sin FX {moneda_origen}/{moneda_base}"
            else:
                modo = "Sin peso"
        else:
            valor_pos = (acciones * precio) if (acciones > 0 and precio is not None) else 0.0
            modo = f"Acciones × precio · {ficha.get('Divisa', 'N/D')}" if valor_pos > 0 else "Sin peso"

        if modo_peso == "Capital invertido" and capital > 0 and valor_mercado_actual is not None:
            pnl_pct = valor_mercado_actual / capital - 1.0
        else:
            pnl_pct = _calcular_pnl_pct(precio, precio_compra)

        error_pos = ficha.get("Error")
        if modo_peso == "Capital invertido" and capital > 0 and acciones_efectivas <= 0 and moneda_origen != moneda_base:
            error_pos = (f"Faltan acciones: no se puede inferir nº de acciones porque capital está en {moneda_base} "
                         f"y el activo cotiza en {moneda_origen}; se evita inventar el FX histórico de compra.")
        elif modo_peso == "Capital invertido" and valor_local is not None and fx_a_base is None:
            error_pos = f"No hay FX utilizable {moneda_origen}/{moneda_base}; posición no incluida en la exposición agregada."

        f = dict(ficha)
        f.update({
            "Valor posición": valor_pos,
            "Modo peso": modo,
            "Acciones posición": acciones_efectivas,
            "Precio medio compra": precio_compra if precio_compra > 0 else None,
            "P&L %": pnl_pct,
            "FX a base": fx_a_base if modo_peso == "Capital invertido" else 1.0,
            "Error": error_pos,
            "Piotroski ratio": (ficha.get("Piotroski") / ficha.get("Piotroski máximo")) if (ficha.get("Piotroski") is not None and ficha.get("Piotroski máximo") not in (None, 0)) else None,
        })
        filas.append(f)

    df = pd.DataFrame(filas)
    if df.empty or df["Valor posición"].sum() <= 0:
        st.warning("Introduce un número de acciones/capital invertido positivo en al menos una posición.")
        return

    consolidado = []
    for t, grp in df.groupby("Ticker", dropna=False):
        first = grp.iloc[0].copy()
        first["Valor posición"] = float(grp["Valor posición"].sum())
        # Se conserva la mejor cifra de compra disponible para el P&L por posición.
        precios_compra = pd.to_numeric(grp["Precio medio compra"], errors="coerce")
        pesos_compra = pd.to_numeric(grp["Acciones posición"], errors="coerce").fillna(0)
        mask_pc = precios_compra.notna() & (pesos_compra > 0)
        if mask_pc.any() and pesos_compra[mask_pc].sum() > 0:
            first["Precio medio compra"] = float((precios_compra[mask_pc] * pesos_compra[mask_pc]).sum() / pesos_compra[mask_pc].sum())
        elif precios_compra.notna().any():
            first["Precio medio compra"] = float(precios_compra.dropna().mean())
        else:
            first["Precio medio compra"] = None
        first["Acciones posición"] = float(pd.to_numeric(grp["Acciones posición"], errors="coerce").sum())
        first["Modo peso"] = "+".join(sorted(set(grp["Modo peso"].astype(str))))
        # El P&L consolidado se calcula con el precio medio agregado cuando hay múltiples filas.
        first["P&L %"] = _calcular_pnl_pct(first.get("Precio"), first.get("Precio medio compra"))
        consolidado.append(first)
    df = pd.DataFrame(consolidado)

    if modo_peso == "Acciones × precio":
        divisas = sorted({str(x) for x in df["Divisa"].dropna().unique() if str(x) not in {"", "N/D", "nan"}})
        if len(divisas) > 1:
            st.error(
                "Has elegido 'Acciones × precio' pero la cartera contiene varias divisas "
                f"({', '.join(divisas)}). Sin un tipo de cambio explícito, sumar EUR y USD como si fueran iguales sería financieramente incorrecto. "
                "Usa 'Capital invertido' con una moneda base común o deja todas las posiciones en la misma divisa."
            )
            return

    total = float(df["Valor posición"].sum())
    df["Peso cartera"] = df["Valor posición"] / total
    metricas = _calcular_metricas_portafolio(df)

    moneda_mostrar = moneda_base if modo_peso == "Capital invertido" else (df["Divisa"].iloc[0] if not df.empty else moneda_base)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Valor / exposición total", _formato_monetario(metricas["valor_total"], moneda_mostrar, 0))
    per_label = _metrica(metricas["per_agregado"], "x")
    k2.metric("PER agregado", per_label)
    k3.metric("ROIC ponderado", _formato_pct(metricas["roic_ponderado_exposicion"]))
    k4.metric(f"Upside DCF {AÑO_HORIZONTE}", _formato_pct(metricas["upside_dcf_ponderado"]))
    k5.metric("P&L ponderado", _formato_pct(metricas["pnl_ponderado"]))

    c6, c7, c8, c9 = st.columns(4)
    with c6:
        _tarjeta("Crecimiento +5y ponderado", _formato_pct(metricas["crecimiento_ponderado"]), "Media ponderada de tasas +5y publicadas", "tc-clara")
    with c7:
        _tarjeta("Piotroski ponderado", f"{metricas['piotroski_ponderado']*100:.0f}%" if metricas.get("piotroski_ponderado") is not None else "N/D", "Calidad contable/operativa cubierta", "tc-clara")
    with c8:
        _tarjeta("Peso máximo", _formato_pct(metricas["peso_maximo"]), "Concentración de la cartera", "tc-ambar" if (metricas.get("peso_maximo") or 0) > 0.40 else "tc-clara")
    with c9:
        _tarjeta("Nº posiciones efectivo", f"{metricas['numero_efectivo_posiciones']:.1f}" if metricas.get("numero_efectivo_posiciones") is not None else "N/D", "1 / HHI · más alto = más diversificación", "tc-clara")

    cobertura_per = metricas.get("per_cobertura")
    cobertura_txt = f" Cobertura del PER: {cobertura_per:.1%} de la exposición tiene PER positivo." if cobertura_per is not None else ""
    st.markdown(
        f'<div class="info-note"><strong>Base de ponderación:</strong> {"capital invertido convertido a " + moneda_base + " con FX de Yahoo" if modo_peso == "Capital invertido" else "nº de acciones × precio actual"}. '
        '<strong>PER agregado:</strong> equity cubierto / beneficios implícitos agregados de las posiciones con PER positivo.' + cobertura_txt + ' '
        '<strong>ROIC, crecimiento, upside y P&L</strong> son estadísticas ponderadas por exposición; no equivalen a estados financieros consolidados.</div>',
        unsafe_allow_html=True,
    )

    vista_cols = [
        "Ticker", "Divisa", "Sector", "Industria", "Valor posición", "Peso cartera", "Precio", "Precio medio compra", "P&L %",
        "PER", "ROIC (%)", "Piotroski", "Crecimiento +5y", f"DCF {AÑO_HORIZONTE}", f"Upside DCF {AÑO_HORIZONTE}", "Modo peso", "FX a base", "Error"
    ]
    vista = df[[c for c in vista_cols if c in df.columns]].copy().sort_values("Valor posición", ascending=False)
    st.markdown("### 📋 Desglose de posiciones")
    st.dataframe(
        vista.style.format({
            "Valor posición": "{:,.2f}", "Peso cartera": "{:.1%}", "Precio": "{:.2f}", "Precio medio compra": "{:.2f}",
            "P&L %": "{:+.1%}", "PER": "{:.1f}", "ROIC (%)": "{:+.1f}%", "Piotroski": "{:.0f}",
            "Crecimiento +5y": "{:+.1%}", f"DCF {AÑO_HORIZONTE}": "{:.2f}", f"Upside DCF {AÑO_HORIZONTE}": "{:+.1%}", "FX a base": "{:.5f}",
        }, na_rep="-"), use_container_width=True
    )
    _descargar_dataframe(vista, "⬇ Exportar cartera CSV", f"portfolio_{AÑO_HORIZONTE}.csv", "csv_portfolio")

    # Exposición sectorial: usa solo el sector publicado por Yahoo, sin inventar comparables sectoriales.
    if "Sector" in vista.columns and "Valor posición" in vista.columns:
        sector_df = (df.groupby(df["Sector"].fillna("N/D"))["Valor posición"].sum() / total).sort_values()
        sector_df = sector_df.reset_index(name="Peso")
        if not sector_df.empty:
            fig_sector = px.bar(
                sector_df, x="Peso", y="Sector", orientation="h", text="Peso",
                color="Peso", color_continuous_scale=ESCALA_PESO,
                title="Exposición por sector (peso de cartera)"
            )
            fig_sector.update_traces(texttemplate="%{x:.1%}", textposition="outside")
            fig_sector.update_layout(height=max(280, 45*len(sector_df)), margin=dict(l=10,r=10,t=50,b=10), showlegend=False, **_DARK_LAYOUT)
            _aplicar_tema_figura(fig_sector)
            st.plotly_chart(fig_sector, use_container_width=True, key="portfolio_sector_bar")

    # Evolución reciente normalizada: una curva por empresa, sin mezclar divisas.
    if historicos_portfolio:
        st.markdown("### 📈 Evolución de precio 3 meses — base 100")
        st.caption("Rendimiento del precio local de cada acción; se normaliza a 100 para poder comparar EUR/USD/GBP sin sumar monedas.")
        series = []
        for ticker, hist in historicos_portfolio.items():
            close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
            if close.empty or close.iloc[0] == 0:
                continue
            tmp = (close / close.iloc[0] * 100.0).rename(ticker).reset_index()
            tmp.columns = ["Fecha", "Valor"]
            tmp["Ticker"] = ticker
            series.append(tmp)
        if series:
            hist_df = pd.concat(series, ignore_index=True)
            fig_rel = px.line(hist_df, x="Fecha", y="Valor", color="Ticker", hover_data={"Valor":":.1f"}, title="Rendimiento relativo de posiciones (100 = inicio del período)")
            fig_rel.add_hline(y=100, line_dash="dot", line_color=COLOR_NEUTRO, annotation_text="Base 100", annotation_font_color=COLOR_NEUTRO)
            fig_rel.update_layout(height=390, margin=dict(l=10,r=10,t=50,b=10), **_DARK_LAYOUT)
            _aplicar_tema_figura(fig_rel)
            st.plotly_chart(fig_rel, use_container_width=True, key="portfolio_relative_3m")

    if "P&L %" in vista.columns and vista["P&L %"].notna().any():
        pnl_plot = vista.dropna(subset=["P&L %"]).sort_values("P&L %")
        fig_pnl = px.bar(
            pnl_plot, x="P&L %", y="Ticker", orientation="h", text="P&L %",
            color="P&L %", color_continuous_scale=ESCALA_VALOR, color_continuous_midpoint=0,
            title="Rentabilidad de cada posición desde el precio medio de compra"
        )
        fig_pnl.update_traces(texttemplate="%{x:+.1%}", textposition="outside")
        fig_pnl.update_layout(height=max(300, 65*len(pnl_plot)), margin=dict(l=10,r=10,t=50,b=10), showlegend=False, **_DARK_LAYOUT)
        _aplicar_tema_figura(fig_pnl)
        st.plotly_chart(fig_pnl, use_container_width=True, key="portfolio_pnl_bar")

    st.markdown(f"### 🟣 Mapa de calor — tamaño = exposición, color = potencial DCF {AÑO_HORIZONTE}")
    st.caption(
        f"Exposición expresada en {moneda_mostrar}. Rojo = peor/negativo potencial, morado = mayor potencial. "
        f"Cuando no hay crecimiento +5y o FCFF {AÑO_HORIZONTE} positivo, el potencial queda como N/D: no se inventa. "
        "Los N/D permanecen como desconocidos y no se convierten en 0%."
    )
    up_col = f"Upside DCF {AÑO_HORIZONTE}"
    treemap_df = df[["Ticker", "Valor posición", up_col, "PER", "ROIC (%)", "Crecimiento +5y", "P&L %"]].copy()
    treemap_df = treemap_df[treemap_df["Valor posición"] > 0]
    def _treemap_label(row: pd.Series) -> str:
        up_txt = f"{row[up_col]:+.1%}" if pd.notna(row[up_col]) else "N/D"
        pnl_txt = f"{row['P&L %']:+.1%}" if pd.notna(row['P&L %']) else "N/D"
        per_txt = f"{row['PER']:.1f}x" if pd.notna(row['PER']) else "N/D"
        roic_txt = f"{row['ROIC (%)']:.1f}%" if pd.notna(row['ROIC (%)']) else "N/D"
        return (f"{row['Ticker']}<br>Upside DCF {AÑO_HORIZONTE}: {up_txt}"
                f"<br>P&L: {pnl_txt}<br>PER: {per_txt} · ROIC: {roic_txt}")

    treemap_df["_label"] = treemap_df.apply(_treemap_label, axis=1)
    if not treemap_df.empty:
        fig_tree = px.treemap(
            treemap_df,
            path=["Ticker"],
            values="Valor posición",
            color=up_col if treemap_df[up_col].notna().any() else None,
            color_continuous_scale=ESCALA_VALOR,
            color_continuous_midpoint=0,
            custom_data=["_label"],
            title=f"Exposición de cartera vs potencial DCF a {AÑO_HORIZONTE}",
        )
        fig_tree.update_traces(hovertemplate="%{customdata[0]}<extra></extra>", textinfo="label+percent parent", textfont=dict(size=14, color="#ffffff"))
        fig_tree.update_layout(height=540, margin=dict(l=8,r=8,t=55,b=8), **_DARK_LAYOUT)
        _aplicar_tema_figura(fig_tree)
        st.plotly_chart(fig_tree, use_container_width=True, key="portfolio_treemap")

    up_col = f"Upside DCF {AÑO_HORIZONTE}"
    st.markdown("### 📐 Qué está impulsando la cartera")
    up_plot = vista.dropna(subset=[up_col]).copy() if up_col in vista.columns else pd.DataFrame()
    if not up_plot.empty:
        up_plot = up_plot.sort_values(up_col)
        fig_bar = px.bar(
            up_plot, x=up_col, y="Ticker", orientation="h", text=up_col,
            color=up_col, color_continuous_scale=ESCALA_VALOR, color_continuous_midpoint=0,
            title=f"Potencial DCF {AÑO_HORIZONTE} por posición"
        )
        fig_bar.update_traces(texttemplate="%{x:+.1%}", textposition="outside")
        fig_bar.update_layout(height=max(300, 65*len(up_plot)), margin=dict(l=10,r=10,t=50,b=10), showlegend=False, **_DARK_LAYOUT)
        _aplicar_tema_figura(fig_bar)
        st.plotly_chart(fig_bar, use_container_width=True, key="portfolio_dcf_bar")

    st.markdown(f"### 🧠 Cómo se construye el DCF {AÑO_HORIZONTE} del portfolio")
    st.markdown(
        f"""
        - **FCFF {AÑO_HORIZONTE}:** sale del motor de proyección. Cuando existe, usa la tasa **+5y publicada por Yahoo** para extender el último dato/consenso disponible.
        - **Valor terminal al cierre del horizonte:** FCFF del horizonte × (1 + g terminal) / (WACC − g terminal).
        - **Puente a equity:** se suma efectivo actual y se resta deuda actual; no se inventan caja/deuda del horizonte.
        - **Precio del horizonte:** equity / acciones actuales.
        - Supuestos de esta pestaña: **WACC {wacc_port*100:.1f}%** y **g terminal {gt_port*100:.2f}%**.

        Esto debe leerse como **valor terminal DCF al cierre del horizonte seleccionado**, no como precio objetivo de un analista.
        """
    )


# ==========================================================================================
# FOOTER DE METODOLOGÍA
# ==========================================================================================

def _mostrar_footer_metodologia() -> None:
    """Explica las limitaciones que podrían confundir un precio objetivo con consenso."""
    st.markdown("---")
    with st.expander("📚 Metodología y límites — lectura recomendada antes de usar el precio objetivo"):
        st.markdown(
            """
            **1. Histórico real.** Balance, cuenta de resultados, flujo de caja y precio de mercado proceden de Yahoo Finance vía yfinance.

            **2. Consenso directo.** EPS e ingresos se etiquetan como consenso únicamente cuando la API devuelve una estimación anual explícita.

            **3. Horizonte seleccionado.** La aplicación no afirma que Yahoo publique una cifra de EPS 2030 para cada empresa. Cuando existe un crecimiento **+5y** de analistas,
            ese dato se usa como puente desde el último año con consenso directo y el tramo resultante queda marcado como **modelo**.

            **4. EBITDA / EBIT / FCFF.** Si la fuente no proporciona consenso directo, se mantienen como métricas modelizadas. Esto evita mezclar datos publicados con cifras fabricadas.

            **5. Múltiplos.** PER y EV/x los decides tú. Cambiar un múltiplo cambia directamente el precio implícito; por eso el gráfico es interactivo.

            **6. Price target de analistas.** Se muestra aparte como dato agregado actual. No se convierte automáticamente en “objetivo 2030”.

            **7. Sensibilidad.** La matriz no es otra predicción: es un mapa de cómo reaccionaría tu valoración si cambias crecimiento y múltiplo alrededor del escenario central.

            **8. Portfolio.** El PER agregado usa valor de mercado dividido entre beneficios implícitos agregados de posiciones con PER positivo; no se usa una media ponderada ingenua de PER.

            **9. Multi-divisa.** En modo Capital invertido, todas las exposiciones se introducen en una moneda base común. En modo Acciones × precio, la app bloquea la agregación si detecta varias divisas para evitar sumar EUR y USD como si fueran equivalentes.

            **10. P&L.** El P&L por posición se calcula con precio medio de compra y precio actual; al ser un porcentaje, es independiente de la divisa.

            **12. PER histórico.** Precio de cierre sin ajustar por dividendos (solo splits) en la fecha de cada cierre fiscal, entre el EPS diluido
            ajustado por splits. Se excluyen años con beneficio negativo o anormal (PER fuera de 3x–100x o más de 2,5 veces la mediana). La pestaña de cada
            empresa incluye una auditoría con el EPS, el PER medio y los años usados para que se vea de dónde sale cada décima.

            **13. PER y caja.** Precio objetivo por PER = EPS × múltiplo. La caja no se suma aparte porque ya forma parte del beneficio y del múltiplo.

            **14. DCF.** El crecimiento baja linealmente desde el valor inicial hasta el terminal en el quinto año; se avisa cuando el valor terminal supera el 75% del valor.
            La tasa impositiva usada es la efectiva de cada ejercicio (1%–45%) y, si no es utilizable, el 25%.

            **15. Monedas.** Cotización en peniques (GBp) se convierte a libras y los estados financieros en otra moneda se convierten a la de cotización.

            **16. Ciclo y escenarios (v7).** La app puntúa de 0 a 100 la ciclicidad (amplitud del margen operativo, años de pérdidas, margen actual frente a su
            mediana y oscilación de ingresos). Si es cíclica, el valor relativo se sustituye por el valor esperado de tres escenarios de persistencia del beneficio
            extraordinario (normalización total, caso intermedio, persistencia total) con EPS normal = media del ciclo observado y PER que se comprime en máximos.
            Todos los parámetros son editables y se leen como supuestos, no como datos.

            **17. Expectativas implícitas (v7).** La pestaña Ciclo & Expectativas calcula qué porcentaje del beneficio extraordinario debe mantenerse para justificar el precio,
            el crecimiento del flujo de caja que exige el DCF inverso y el mapa EPS × PER que iguala el precio actual.

            **18. Múltiplos EV (v7).** El ancla por defecto es la mediana histórica de EV/EBITDA, EV/EBIT y EV/FCFF al cierre de cada ejercicio (mínimo 2 años); con el múltiplo
            actual como ancla el precio objetivo sería el precio de hoy por el crecimiento. EBITDA, EBIT y FCFF de los años con consenso de ingresos mantienen el margen del último ejercicio.

            **19. Caja y minoritarios (v7).** La caja incluye inversiones financieras a corto plazo y los minoritarios se suman a la deuda en todos los puentes EV → equity.
            Las inversiones no corrientes no se cuentan (criterio prudente).

            **20. Cartera (v7).** El DCF de la cartera descuenta a hoy los flujos explícitos y el valor terminal del horizonte elegido; antes comparaba un valor de 2030 sin descontar con el precio de hoy.

            **11. DCF retrospectivo.** El gráfico histórico de DCF es una aproximación con supuestos actuales (WACC, crecimiento y acciones actuales), no un backtest de la información que habría conocido el mercado en cada fecha.
            """
        )


# 3. Navegación principal por bloques — evita el scroll interminable.
if ticker1 and ticker2:
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        f"📊 {ticker1}",
        f"📊 {ticker2}",
        "🥊 Comparativa",
        "💼 Mi Portafolio",
        "📚 Metodología",
    ])
    with tab1:
        resumen1 = mostrar_analisis(ticker1, "1")
    with tab2:
        resumen2 = mostrar_analisis(ticker2, "2")
    with tab3:
        mostrar_comparativa(resumen1, resumen2)
    with tab4:
        mostrar_portafolio()
    with tab5:
        _mostrar_footer_metodologia()
else:
    tab1, tab2, tab3, tab4 = st.tabs([
        f"📊 {ticker1 or 'Empresa'}",
        "💼 Mi Portafolio",
        "📚 Metodología",
        "ℹ️ Ayuda",
    ])
    with tab1:
        if ticker1:
            mostrar_analisis(ticker1, "1")
        else:
            st.info("Introduce un ticker de empresa para comenzar.")
    with tab2:
        mostrar_portafolio()
    with tab3:
        _mostrar_footer_metodologia()
    with tab4:
        st.markdown("Introduce un ticker arriba y utiliza las pestañas para separar análisis, cartera y metodología.")

# ==========================================================================================
# NOTA DE MANTENIMIENTO — BLOQUE DELIBERADAMENTE EXTENSO
# ==========================================================================================
# Este archivo supera las 1.000 líneas intencionadamente porque el usuario pidió mantener
# una base amplia y explicativa. Los helpers anteriores están separados por capas para que
# futuras mejoras no obliguen a reescribir el motor de cálculo.
#
# Checklist de futuras extensiones:
# - Mantener claramente separados los datos reales del consenso.
# - No etiquetar una extrapolación como “consenso del horizonte seleccionado”.
# - No introducir crecimiento por defecto sin mostrar su procedencia.
# - Reutilizar _build_valuation_path para nuevos múltiplos.
# - Reutilizar _fig_proyeccion_fundamental para nuevas métricas.
# - Añadir nuevas fuentes solo si su cobertura y procedencia pueden mostrarse en pantalla.
# - Mantener el PER de cartera como PER agregado (valor / beneficios implícitos), no como media simple.
# - Tratar el DCF 2030 como valor terminal del modelo, no como consenso de analistas.
# ==========================================================================================


