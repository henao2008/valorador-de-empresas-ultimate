# Control de gastos de Revolut

Dashboard Streamlit en español para importar extractos CSV/Excel, clasificar movimientos dudosos, ajustar categorías y fijar límites estrictamente mensuales. El panel muestra el avance de la semana (lunes a domingo), comparaciones con las cuatro semanas anteriores, límites del mes y gráficos de gasto.

## Privacidad y almacenamiento

- El repositorio público contiene solo código y documentación; **no** contiene extractos, movimientos, informes históricos, contraseñas ni claves.
- La app pide una contraseña antes de mostrar datos.
- El almacenamiento persistente usa un repositorio **privado** de GitHub. Crea un fine-grained personal access token limitado a ese repositorio, con `Contents: Read and write` y `Metadata: Read`, y guarda en los secretos de la app `GITHUB_TOKEN`, `GITHUB_DATA_REPO` (formato `usuario/repositorio`) y opcionalmente `GITHUB_DATA_BRANCH`.
- En los secretos de la app configura también una contraseña larga en `APP_PASSWORD`. No compartas el token ni lo pegues en el código.
- Opcional: `GEMINI_API_KEY` activa sugerencias de clasificación con Gemini 3.8 Flash. La app pide consentimiento antes de enviar **solo tipo y descripción**, sin fecha ni importe, y exige que el usuario confirme cada categoría. Gemini puede utilizar datos de solicitudes del nivel gratuito para mejorar productos según sus condiciones actuales; no configures esa opción si no quieres compartir descripciones con un proveedor externo. [Condiciones y niveles de uso de Gemini](https://ai.google.dev/gemini-api/docs/pricing).

La carga combina los movimientos por identificador y evita volver a contar transacciones idénticas ya importadas. Los registros sin categoría bloquean el dashboard hasta completar la revisión. Los límites se guardan en el repositorio privado y se comparan con el gasto del mes, sin prorrateo semanal.

## Ejecución local

```powershell
python -m pip install -r requirements.txt
$env:APP_PASSWORD = "elige-una-clave-local"
$env:FINANZAS_LOCAL_MODE = "1"
python -m streamlit run streamlit_app.py
```

En modo local, los datos se guardan en `data_private_local/`, excluida de Git.

## Publicar en Streamlit Community Cloud

1. Abre Streamlit Community Cloud y elige crear una app.
2. Selecciona el repositorio `henao2008/valorador-de-empresas-ultimate`, la rama `finanzas-revolut-dashboard` y la ruta `streamlit_app.py`.
3. Marca la app como privada para limitar quién puede abrirla.
4. En **App settings → Secrets**, configura los secretos anteriores.
5. Crea un acceso directo del navegador a la URL `*.streamlit.app`.

## Importar el extracto

Desde la app, sube el CSV o Excel de Revolut. El archivo debe incluir fecha, concepto/description e importe/amount. Se reconocen los nombres de columna habituales en español e inglés. La app añade solo movimientos nuevos; revisa todos los no clasificados antes de generar el informe.
