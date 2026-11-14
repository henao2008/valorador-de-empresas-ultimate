# Terminal V7 — Big Tech, IA y tecnología

## Ejecución en Windows / VS Code

1. Instala Python 3.11 (recomendado) y abre esta carpeta en VS Code.
2. En la terminal de VS Code ejecuta:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run dashboard_definitivo_v7.py
```

Si PowerShell bloquea la activación, abre CMD y usa `.venv\\Scripts\\activate.bat`.

Para ejecutar las pruebas sintéticas:

```powershell
python -m unittest discover -s tests -v
python -m py_compile dashboard_definitivo_v7.py valuation_engine.py
```

`dashboard_definitivo_v7_original_intacto.py` es la copia sin modificar recibida en la conversación. El archivo ejecutable actualizado es `dashboard_definitivo_v7.py`; requiere `valuation_engine.py` en la misma carpeta.
