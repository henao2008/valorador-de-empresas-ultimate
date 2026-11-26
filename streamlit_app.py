from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit as st


st.set_page_config(page_title="Control de gastos", page_icon="🟠", layout="wide")

DEFAULT_CATEGORIES = [
    "Alimentación", "Comer fuera", "Compras", "Ocio nocturno", "Gestiones",
    "Ocio normal", "Transporte", "Ropa", "Vivienda", "Apuestas",
    "Ayuda a familia", "Salud y cuidado personal", "Desconocido",
    "Ingresos", "Apoyo familiar recibido", "Ingresos por trabajo", "Beca",
    "Venta de acciones", "Fondos añadidos (cuenta propia)",
    "Devoluciones / abonos", "Transferencia interna",
]
EXPENSE_EXAMPLES = [
    "Alimentación", "Comer fuera", "Compras", "Ocio nocturno", "Gestiones",
    "Ocio normal", "Transporte", "Ropa", "Vivienda", "Apuestas",
    "Ayuda a familia", "Salud y cuidado personal",
]
ALIASES = {
    "type": ["type", "tipo"], "date": ["completed date", "fecha completada", "fecha de finalizacion", "started date", "fecha inicio", "fecha"],
    "description": ["description", "descripcion", "concepto", "comercio"],
    "amount": ["amount", "importe", "cantidad"], "fee": ["fee", "comision"],
    "currency": ["currency", "moneda"], "state": ["state", "estado"],
    "product": ["product", "producto", "account", "cuenta"],
}
MERCHANT_RULES = {
    "mercadona": "Alimentación", "carrefour": "Alimentación", "lidl": "Alimentación",
    "aldi": "Alimentación", "consum": "Alimentación", "masymas": "Alimentación",
    "supermercado": "Alimentación", "restaurante": "Comer fuera", "restaurant": "Comer fuera",
    "uber eats": "Comer fuera", "just eat": "Comer fuera", "glovo": "Comer fuera",
    "mcdonald": "Comer fuera", "100 montaditos": "Comer fuera", "renfe": "Transporte",
    "uber": "Transporte", "cabify": "Transporte", "metro": "Transporte", "bus": "Transporte",
    "druni": "Salud y cuidado personal", "farmacia": "Salud y cuidado personal",
    "winamax": "Apuestas", "bet365": "Apuestas", "casino": "Apuestas",
    "netflix": "Gestiones", "spotify": "Gestiones", "disney": "Gestiones",
    "youtube premium": "Gestiones", "autoescuela": "Gestiones", "dgt": "Gestiones",
    "fourvenues": "Ocio nocturno", "discoteca": "Ocio nocturno", "vessel club": "Ocio nocturno",
    "decathlon": "Compras", "adidas": "Compras", "zara": "Ropa", "pull&bear": "Ropa",
}
REPO_DATA_PATHS = {"transactions": "data/transactions.csv", "categories": "data/categories.json", "budgets": "data/monthly_limits.json", "rules": "data/category_rules.json"}


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return os.environ.get(name, default)


def auth_gate() -> None:
    password = secret("APP_PASSWORD")
    if not password:
        st.error("Falta configurar el acceso privado. Añade APP_PASSWORD en los secretos de Streamlit antes de usar esta app en internet.")
        st.stop()
    if st.session_state.get("authenticated"):
        with st.sidebar:
            if st.button("Cerrar sesión", use_container_width=True):
                st.session_state.pop("authenticated", None)
                st.rerun()
        return
    st.title("🔒 Acceso privado")
    entered = st.text_input("Contraseña", type="password")
    if st.button("Entrar", type="primary"):
        if hmac.compare_digest(entered, password):
            st.session_state.authenticated = True
            st.rerun()
        st.error("La contraseña no coincide.")
    st.stop()


def repo_config() -> tuple[str, str, str]:
    return secret("GITHUB_TOKEN"), secret("GITHUB_DATA_REPO"), secret("GITHUB_DATA_BRANCH", "main")


def github_request(method: str, path: str, payload: dict | None = None):
    token, repo, branch = repo_config()
    if not token or not repo:
        raise RuntimeError("Configura GITHUB_TOKEN y GITHUB_DATA_REPO para guardar los datos de forma persistente en un repositorio privado.")
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    params = {"ref": branch} if method == "GET" else None
    response = requests.request(method, url, headers=headers, params=params, json=payload, timeout=25)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"No se pudo acceder al almacenamiento privado ({response.status_code}). Comprueba el token, el repositorio y sus permisos de Contents.")
    return response.json()


def load_file(key: str, default):
    if os.environ.get("FINANZAS_LOCAL_MODE") == "1":
        local_path = Path(__file__).resolve().parent / "data_private_local" / REPO_DATA_PATHS[key]
        return local_path.read_text(encoding="utf-8") if local_path.exists() else default
    try:
        item = github_request("GET", REPO_DATA_PATHS[key])
    except RuntimeError as exc:
        if "404" in str(exc):
            return default
        raise
    return base64.b64decode(item["content"]).decode("utf-8")


def save_file(key: str, content: str) -> None:
    if os.environ.get("FINANZAS_LOCAL_MODE") == "1":
        local_path = Path(__file__).resolve().parent / "data_private_local" / REPO_DATA_PATHS[key]
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content, encoding="utf-8")
        return
    token, repo, branch = repo_config()
    if not token or not repo:
        raise RuntimeError("Falta configurar el almacenamiento privado de GitHub.")
    path = REPO_DATA_PATHS[key]
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    old = requests.get(url, headers=headers, params={"ref": branch}, timeout=25)
    sha = old.json().get("sha") if old.status_code == 200 else None
    payload = {"message": f"Update private finance {key}", "content": base64.b64encode(content.encode("utf-8")).decode("ascii"), "branch": branch}
    if sha:
        payload["sha"] = sha
    response = requests.put(url, headers=headers, json=payload, timeout=25)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"No se pudo guardar en el repositorio privado ({response.status_code}). Revisa el permiso Contents: read/write.")


def normalize(value) -> str:
    value = "" if pd.isna(value) else str(value)
    value = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def column_map(columns) -> dict[str, str]:
    norm_cols = {normalize(col): col for col in columns}
    found = {}
    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            if normalize(alias) in norm_cols:
                found[canonical] = norm_cols[normalize(alias)]
                break
    required = [key for key in ("date", "description", "amount") if key not in found]
    if required:
        raise ValueError("Faltan columnas que Revolut suele llamar Completed Date, Description y Amount (o su traducción).")
    return found


def parse_upload(upload) -> pd.DataFrame:
    if upload.name.lower().endswith((".xlsx", ".xls")):
        raw = pd.read_excel(upload)
    else:
        content = upload.getvalue()
        try:
            raw = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", sep=None, engine="python")
        except UnicodeDecodeError:
            raw = pd.read_csv(io.BytesIO(content), encoding="cp1252", sep=None, engine="python")
    cols = column_map(raw.columns)
    data = pd.DataFrame()
    for name, source in cols.items():
        data[name] = raw[source]
    for name, default in {"type": "Card Payment", "fee": 0, "currency": "EUR", "state": "COMPLETED", "product": ""}.items():
        if name not in data:
            data[name] = default
    data["date"] = pd.to_datetime(data["date"], errors="coerce", dayfirst=True, utc=True, format="mixed").dt.tz_localize(None)
    def number(value):
        raw = "" if pd.isna(value) else str(value).strip().replace(" ", "")
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
        return pd.to_numeric(raw, errors="coerce")
    data["amount"] = data["amount"].map(number)
    data["fee"] = data["fee"].map(number).fillna(0)
    data["description"] = data["description"].fillna("").astype(str).str.strip()
    data["type"] = data["type"].fillna("").astype(str).str.strip()
    data["currency"] = data["currency"].fillna("EUR").astype(str).str.upper().str.strip()
    data["state"] = data["state"].fillna("COMPLETED").astype(str).str.upper().str.strip()
    data["product"] = data["product"].fillna("").astype(str).str.strip()
    data = data[data["date"].notna() & data["amount"].notna() & data["currency"].eq("EUR") & data["state"].eq("COMPLETED")].copy()
    data = data[~data["product"].map(normalize).eq("savings")]
    data["amount"] = data["amount"] - data["fee"].abs()
    signatures = pd.Series(
        ["|".join([row["date"].isoformat(), normalize(row["type"]), normalize(row["description"]), f"{row['amount']:.2f}"]) for _, row in data.iterrows()],
        index=data.index,
        dtype="string",
    )
    counts = signatures.groupby(signatures).cumcount().add(1).astype(str)
    data["transaction_id"] = [hashlib.sha256(f"{sig}|{occ}".encode()).hexdigest() for sig, occ in zip(signatures, counts)]
    data["category"] = ""
    data["source_file"] = upload.name
    return data[["transaction_id", "date", "description", "type", "amount", "category", "source_file"]].reset_index(drop=True)


def suggested_category(row: pd.Series, saved_rules: dict[str, str]) -> str:
    key = f"{normalize(row['type'])}|{normalize(row['description'])}"
    if key in saved_rules:
        return saved_rules[key]
    desc, kind = normalize(row["description"]), normalize(row["type"])
    if "refund" in kind or "devolucion" in kind or "abono" in kind:
        return "Devoluciones / abonos"
    if "transfer" in kind and any(term in desc for term in ("pocket", "bolsillo", "savings", "ahorro", "cuenta remunerada")):
        return "Transferencia interna"
    if "top up" in kind or "topup" in kind or "recarga" in kind:
        return "Apoyo familiar recibido" if "bizum" in desc else "Fondos añadidos (cuenta propia)"
    if row["amount"] > 0:
        return "Ingresos"
    for merchant, category in MERCHANT_RULES.items():
        if merchant in desc:
            return category
    return ""


def gemini_suggestions(pending: pd.DataFrame, categories: list[str], api_key: str) -> dict[str, str]:
    sample = [{"key": str(index + 1), "type": row.type, "description": row.description} for index, row in enumerate(pending.head(40).itertuples())]
    prompt = "Clasifica gastos bancarios. Devuelve solo JSON con un array predictions, elementos {key,category}. Usa exactamente una categoría de esta lista: " + json.dumps(categories, ensure_ascii=False) + ". Si falta contexto, usa Desconocido. No inventes categorías. Datos: " + json.dumps(sample, ensure_ascii=False)
    response = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}, timeout=40,
    )
    if response.status_code != 200:
        raise RuntimeError("Gemini no pudo generar sugerencias. Revisa la clave o el límite de uso.")
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    predictions = parsed if isinstance(parsed, list) else parsed.get("predictions", [])
    allowed = set(categories)
    ids = pending.head(40)["transaction_id"].tolist()
    return {ids[int(item["key"]) - 1]: item["category"] for item in predictions if item.get("category") in allowed and str(item.get("key", "")).isdigit() and 1 <= int(item["key"]) <= len(ids)}


def serialize_tx(tx: pd.DataFrame) -> str:
    data = tx.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.strftime("%Y-%m-%d")
    return data.to_csv(index=False)


def load_state() -> tuple[pd.DataFrame, list[str], dict[str, float], dict[str, str]]:
    try:
        tx_text = load_file("transactions", "")
        tx = pd.read_csv(io.StringIO(tx_text), parse_dates=["date"]) if tx_text else pd.DataFrame(columns=["transaction_id", "date", "description", "type", "amount", "category", "source_file"])
        cat_text = load_file("categories", "")
        categories = json.loads(cat_text) if cat_text else DEFAULT_CATEGORIES.copy()
        budgets_text = load_file("budgets", "")
        budgets = json.loads(budgets_text) if budgets_text else {}
        rules_text = load_file("rules", "")
        rules = json.loads(rules_text) if rules_text else {}
        return tx, categories, budgets, rules
    except Exception as exc:
        st.error(f"No se pudo cargar el almacenamiento. {exc}")
        st.stop()


def save_state(tx: pd.DataFrame, categories: list[str], budgets: dict[str, float], rules: dict[str, str]) -> None:
    save_file("transactions", serialize_tx(tx))
    save_file("categories", json.dumps(categories, ensure_ascii=False, indent=2))
    save_file("budgets", json.dumps(budgets, ensure_ascii=False, indent=2))
    save_file("rules", json.dumps(rules, ensure_ascii=False, indent=2))


def eur(value: float) -> str:
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def run_app() -> None:
    token, repo, _ = repo_config()
    if (not token or not repo) and os.environ.get("FINANZAS_LOCAL_MODE") != "1":
        st.error("La app todavía no tiene almacenamiento persistente. Configura GITHUB_TOKEN y GITHUB_DATA_REPO en los secretos de Streamlit; el repositorio indicado debe ser privado.")
        st.stop()
    tx, categories, budgets, rules = load_state()

    st.title("🟠 Control de gastos")
    st.caption("Revolut · resumen semanal y mensual · límites mensuales configurables")
    with st.sidebar:
        st.header("Preferencias")
        with st.expander("Categorías", expanded=False):
            new_category = st.text_input("Crear categoría", placeholder="Ej. Mascotas")
            if st.button("Añadir categoría", disabled=not new_category.strip()):
                if new_category.strip() not in categories:
                    categories.append(new_category.strip())
                    save_state(tx, categories, budgets, rules)
                    st.rerun()
            st.caption("En los selectores puedes escribir para buscar.")
            chosen = st.selectbox("Categoría para añadir un límite", categories, index=None, placeholder="Buscar categoría…")
            if chosen:
                limit = st.number_input("Límite mensual (€)", min_value=0.0, value=float(budgets.get(chosen, 0.0)), step=5.0, key=f"limit-{chosen}")
                c1, c2 = st.columns(2)
                if c1.button("Guardar límite", key=f"save-limit-{chosen}"):
                    budgets[chosen] = float(limit)
                    save_state(tx, categories, budgets, rules)
                    st.rerun()
                if c2.button("Quitar límite", key=f"del-limit-{chosen}"):
                    budgets.pop(chosen, None)
                    save_state(tx, categories, budgets, rules)
                    st.rerun()
        with st.expander("Límites mensuales actuales", expanded=False):
            st.write("Sin tope semanal ni prorrateo. El aviso compara el gasto del mes con este límite mensual.")
            if budgets:
                st.dataframe(pd.DataFrame([{"Categoría": k, "Límite mensual": eur(v)} for k, v in budgets.items()]), hide_index=True, use_container_width=True)
            else:
                st.info("Aún no has fijado límites.")
        st.divider()
        st.header("Actualizar extracto")
        upload = st.file_uploader("Sube CSV o Excel de Revolut", type=["csv", "xlsx", "xls"], key="statement-upload")
        if upload:
            upload_hash = hashlib.sha256(upload.getvalue()).hexdigest()
            if st.session_state.get("last_upload_hash") != upload_hash:
                try:
                    added = parse_upload(upload)
                    if not tx.empty:
                        added = added[~added["transaction_id"].isin(set(tx["transaction_id"].astype(str)))].copy()
                    if not added.empty:
                        added["category"] = added.apply(lambda row: suggested_category(row, rules), axis=1)
                        tx = pd.concat([tx, added], ignore_index=True).drop_duplicates("transaction_id", keep="last")
                        tx = tx.sort_values("date").reset_index(drop=True)
                        save_state(tx, categories, budgets, rules)
                    st.session_state.last_upload_hash = upload_hash
                    st.success(f"Extracto procesado. {len(added)} movimientos nuevos guardados; los repetidos se han omitido.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"No pude leer ese archivo: {exc}")
        st.caption("Los movimientos y tus ajustes se guardan en el repositorio privado configurado para la app.")

    pending = tx[tx["category"].fillna("").eq("")].copy() if not tx.empty else tx.copy()
    if not pending.empty:
        st.warning(f"Hay {len(pending)} movimientos por clasificar. El informe completo se mantiene pendiente hasta que confirmes una categoría para cada uno.")
        st.subheader("Revisión de movimientos")
        st.write("Revisa el concepto y el importe. Las sugerencias automáticas son editables; no se confirman hasta pulsar el botón.")
        api_key = secret("GEMINI_API_KEY")
        if api_key:
            consent = st.checkbox("Enviar solo tipo y descripción del comercio (sin fecha ni importe) a Gemini para obtener sugerencias. Las sugerencias no se confirman automáticamente.")
            if st.button("Sugerir categorías con IA", disabled=not consent):
                try:
                    suggestions = gemini_suggestions(pending, categories, api_key)
                    tx.loc[tx["transaction_id"].isin(suggestions), "category"] = tx.loc[tx["transaction_id"].isin(suggestions), "transaction_id"].map(suggestions)
                    st.session_state["ai_suggestions"] = suggestions
                    st.info(f"La IA propuso categorías para {len(suggestions)} movimientos. Revisa todas las propuestas y pulsa Confirmar para guardarlas.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        else:
            st.caption("La clasificación manual funciona sin IA. Si configuras GEMINI_API_KEY, podrás pedir sugerencias opcionales, siempre sujetas a tu confirmación.")
        review = pending[["transaction_id", "date", "description", "type", "amount", "category"]].copy()
        review["date"] = pd.to_datetime(review["date"]).dt.strftime("%d/%m/%Y")
        suggestions = st.session_state.get("ai_suggestions", {})
        if suggestions:
            review["category"] = review.apply(lambda row: suggestions.get(row["transaction_id"], row["category"]), axis=1)
        edited = st.data_editor(
            review, hide_index=True, use_container_width=True, num_rows="fixed",
            column_config={
                "transaction_id": None,
                "date": st.column_config.TextColumn("Fecha", disabled=True),
                "description": st.column_config.TextColumn("Concepto", disabled=True),
                "type": st.column_config.TextColumn("Tipo", disabled=True),
                "amount": st.column_config.NumberColumn("Importe (€)", format="%.2f", disabled=True),
                "category": st.column_config.SelectboxColumn("Categoría · obligatoria", options=[""] + categories, required=True),
            },
            key="pending-editor",
        )
        reuse = st.checkbox("Recordar esta categoría para futuras transacciones con el mismo concepto", value=True)
        if st.button("Confirmar clasificaciones", type="primary"):
            if edited["category"].fillna("").eq("").any():
                st.error("Falta elegir una categoría en todas las filas. No se ha cerrado la revisión.")
            else:
                picked = edited.set_index("transaction_id")["category"]
                tx.loc[tx["transaction_id"].isin(picked.index), "category"] = tx.loc[tx["transaction_id"].isin(picked.index), "transaction_id"].map(picked)
                if reuse:
                    for row in pending.itertuples():
                        value = picked.get(row.transaction_id)
                        if value:
                            rules[f"{normalize(row.type)}|{normalize(row.description)}"] = value
                save_state(tx, categories, budgets, rules)
                st.session_state.pop("ai_suggestions", None)
                st.success("Clasificaciones confirmadas y guardadas.")
                st.rerun()
        st.stop()

    if tx.empty:
        st.info("Sube un extracto de Revolut CSV o Excel para empezar. El dashboard solo se genera cuando no quedan movimientos dudosos.")
        st.stop()

    tx["date"] = pd.to_datetime(tx["date"])
    tx["category"] = tx["category"].fillna("")
    tx["month"] = tx["date"].dt.to_period("M").astype(str)
    tx["week_start"] = (tx["date"] - pd.to_timedelta(tx["date"].dt.weekday, unit="D")).dt.normalize()
    expenses = tx[(tx["amount"] < 0) & ~tx["category"].eq("Transferencia interna")].copy()
    expenses["spent"] = -expenses["amount"]
    today = pd.Timestamp(date.today()).normalize()
    month_start = today.replace(day=1)
    current_month = tx[tx["date"].dt.to_period("M").astype(str).eq(today.strftime("%Y-%m"))]
    month_expenses = expenses[expenses["date"].ge(month_start)]
    week_start = today - pd.Timedelta(days=today.weekday())
    week_tx = tx[tx["date"].ge(week_start) & tx["date"].le(today)]
    week_expenses = expenses[expenses["date"].ge(week_start) & expenses["date"].le(today)]
    incomes = tx[(tx["amount"] > 0) & tx["category"].isin(["Ingresos", "Apoyo familiar recibido", "Ingresos por trabajo", "Beca"])]
    month_income = incomes[incomes["date"].ge(month_start) & incomes["date"].le(today)]["amount"].sum()
    week_income = incomes[incomes["date"].ge(week_start) & incomes["date"].le(today)]["amount"].sum()
    saved = tx[(tx["category"].eq("Transferencia interna")) & (tx["amount"] < 0) & tx["date"].ge(month_start)]["amount"].abs().sum()
    week_saved = tx[(tx["category"].eq("Transferencia interna")) & (tx["amount"] < 0) & tx["date"].ge(week_start) & tx["date"].le(today)]["amount"].abs().sum()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Gasto esta semana", eur(week_expenses["spent"].sum()))
    c2.metric("Gasto este mes", eur(month_expenses["spent"].sum()))
    c3.metric("Ingresos y apoyo esta semana", eur(week_income))
    c4.metric("Transferido a ahorro esta semana", eur(week_saved))
    c5.metric("Transferido a ahorro este mes", eur(saved))
    st.caption(f"Saldo operativo del mes (ingresos y apoyo menos gastos): {eur(month_income - month_expenses['spent'].sum())}. Las transferencias entre tus cuentas se muestran aparte.")

    st.subheader(f"Semana · lunes {week_start:%d/%m} a domingo {(week_start + pd.Timedelta(days=6)):%d/%m}")
    top3 = week_expenses.groupby("category")["spent"].sum().sort_values(ascending=False).head(3)
    st.write("**Top 3:** " + (" · ".join(f"{cat}: {eur(value)}" for cat, value in top3.items()) or "Sin gastos registrados esta semana."))
    week_table = week_expenses.groupby("category", as_index=False)["spent"].sum().sort_values("spent", ascending=False)
    if not week_table.empty:
        st.dataframe(week_table.rename(columns={"category": "Categoría", "spent": "Gasto semanal"}).style.format({"Gasto semanal": lambda x: eur(x)}), hide_index=True, use_container_width=True)
    previous_weeks = [week_start - pd.Timedelta(days=7 * step) for step in (4, 3, 2, 1)]
    prior_complete = expenses[expenses["week_start"].isin(previous_weeks) & expenses["date"].lt(week_start)]
    elapsed_days = max(1, min(7, (today - week_start).days + 1))
    current_compare = week_expenses[week_expenses["date"].lt(week_start + pd.Timedelta(days=elapsed_days))].groupby("category")["spent"].sum()
    prior_compare_rows = prior_complete[prior_complete["date"].dt.dayofweek.lt(elapsed_days)]
    prior_compare = prior_compare_rows.groupby(["week_start", "category"])["spent"].sum().groupby("category").mean()
    common = sorted(set(current_compare.index) | set(prior_compare.index))
    deviations = []
    for category in common:
        avg = float(prior_compare.get(category, 0))
        current_value = float(current_compare.get(category, 0))
        if avg > 0 and current_value > avg * 1.15:
            deviations.append(f"{category}: {eur(current_value)} frente a media comparable de {eur(avg)} (+{(current_value / avg - 1) * 100:.0f}%)")
    if deviations:
        st.warning("Por encima de la media de las cuatro semanas anteriores (mismos días): " + "; ".join(deviations))
    elif prior_compare_rows["week_start"].nunique() == 4:
        st.caption("Ninguna categoría supera en más de un 15 % la media comparable de las cuatro semanas anteriores.")
    else:
        st.caption("La comparación con cuatro semanas anteriores estará disponible cuando haya cuatro semanas completas de datos.")
    days = pd.date_range(week_start, min(today, week_start + pd.Timedelta(days=6)), freq="D")
    daily = week_expenses.assign(day=week_expenses["date"].dt.normalize()).groupby("day")["spent"].sum().reindex(days, fill_value=0).rename_axis("Día").reset_index()
    daily["Día"] = daily["Día"].dt.strftime("%a %d/%m")
    fig = px.bar(daily, x="Día", y="spent", title="Gasto por día · semana en curso", color_discrete_sequence=["#F28C28"])
    st.plotly_chart(fig, use_container_width=True)

    month_names = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    st.subheader(f"Límites y gasto del mes · {month_names[today.month - 1]} {today.year}")
    month_by_cat = month_expenses.groupby("category")["spent"].sum().to_dict()
    budget_rows = []
    for category in sorted(set(month_by_cat) | set(budgets)):
        spent = float(month_by_cat.get(category, 0))
        cap = budgets.get(category)
        budget_rows.append({"Categoría": category, "Gasto del mes": eur(spent), "Límite mensual": eur(float(cap)) if cap is not None else "Sin límite", "Disponible": eur(float(cap) - spent) if cap is not None else "—", "Estado": "Sobre el límite" if cap is not None and spent > float(cap) else "Dentro del límite" if cap is not None else "Sin límite configurado"})
    st.dataframe(pd.DataFrame(budget_rows), hide_index=True, use_container_width=True)
    over = [row["Categoría"] for row in budget_rows if row["Estado"] == "Sobre el límite"]
    if over:
        st.warning("Este mes has superado el límite en: " + ", ".join(over))

    st.subheader("Vista mensual")
    if not expenses.empty:
        monthly = expenses.groupby(["month", "category"], as_index=False)["spent"].sum()
        left, right = st.columns(2)
        totals = expenses.groupby("category", as_index=False)["spent"].sum().sort_values("spent", ascending=False)
        pie = px.pie(totals, values="spent", names="category", title="Gasto total por categoría", color_discrete_sequence=["#F28C28", "#F4C542", "#F6B26B", "#FFD966", "#E69138", "#FFF2CC"])
        trends = monthly.groupby("month", as_index=False)["spent"].sum()
        bar = px.bar(trends, x="month", y="spent", title="Tendencia del gasto por mes", color_discrete_sequence=["#F28C28"])
        left.plotly_chart(pie, use_container_width=True)
        right.plotly_chart(bar, use_container_width=True)
        cat_trend = px.bar(monthly, x="month", y="spent", color="category", barmode="stack", title="Gasto mensual por categoría", color_discrete_sequence=px.colors.qualitative.Vivid)
        st.plotly_chart(cat_trend, use_container_width=True)

    st.subheader("Movimientos clasificados")
    show = tx.sort_values("date", ascending=False).copy()
    show["date"] = show["date"].dt.strftime("%d/%m/%Y")
    edited_all = st.data_editor(show[["transaction_id", "date", "description", "type", "amount", "category"]], hide_index=True, use_container_width=True, num_rows="fixed", column_config={"transaction_id": None, "date": st.column_config.TextColumn("Fecha", disabled=True), "description": st.column_config.TextColumn("Concepto", disabled=True), "type": st.column_config.TextColumn("Tipo", disabled=True), "amount": st.column_config.NumberColumn("Importe (€)", format="%.2f", disabled=True), "category": st.column_config.SelectboxColumn("Categoría", options=categories, required=True)}, key="all-transactions")
    if st.button("Guardar cambios de categorías"):
        picked = edited_all.set_index("transaction_id")["category"]
        tx["category"] = tx["transaction_id"].map(picked).fillna(tx["category"])
        save_state(tx, categories, budgets, rules)
        st.success("Cambios guardados.")
        st.rerun()


if __name__ == "__main__":
    auth_gate()
    run_app()

