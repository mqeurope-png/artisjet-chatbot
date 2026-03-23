# Despliegue del Chatbot artisJet — Guía paso a paso

## Requisitos previos

1. Una cuenta en OpenAI con API key (https://platform.openai.com/api-keys)
2. Una cuenta en GitHub (https://github.com)
3. Una cuenta en Render (https://render.com) — plan gratuito disponible

## Paso 1: Crear el Assistant en OpenAI (5 minutos)

En tu PC, con Python instalado:

```bash
pip install openai

export OPENAI_API_KEY="sk-tu-api-key-aqui"

python setup_assistant.py
```

El script te dará un **OPENAI_ASSISTANT_ID** (algo como `asst_abc123...`). Guárdalo.

## Paso 2: Subir el código a GitHub (3 minutos)

1. Crea un repositorio nuevo en GitHub (puede ser privado)
2. Sube la carpeta `artisjet-chatbot/` completa:

```bash
cd artisjet-chatbot
git init
git add .
git commit -m "artisJet chatbot initial commit"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/artisjet-chatbot.git
git push -u origin main
```

## Paso 3: Desplegar en Render (5 minutos)

1. Ve a https://render.com y regístrate (puedes usar tu cuenta de GitHub)
2. Click en **"New +"** → **"Web Service"**
3. Conecta tu repositorio de GitHub `artisjet-chatbot`
4. Configuración:
   - **Name:** artisjet-chatbot
   - **Region:** la más cercana a ti (EU si estás en Europa)
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - **Plan:** Free

5. En **Environment Variables**, añade:
   - `OPENAI_API_KEY` = tu API key de OpenAI
   - `OPENAI_ASSISTANT_ID` = el ID del paso 1
   - `MAX_REQUESTS_PER_HOUR` = 30 (ajústalo según necesites)

6. Click en **"Create Web Service"**

Render desplegará automáticamente. En 2-3 minutos tendrás tu URL:
`https://artisjet-chatbot.onrender.com`

## Paso 4: Verificar

Abre la URL del chatbot y prueba con estas consultas:
- "Mi impresora muestra error SX001"
- "¿Cómo hago el mantenimiento semanal?"
- "La impresión tiene líneas"

## Costes estimados

- **Render Free:** $0/mes (se duerme tras 15 min inactividad, tarda ~30s en despertar)
- **Render Starter:** $7/mes (siempre activo, más rápido)
- **OpenAI API:** ~$0.01-0.05 por consulta con gpt-4o (depende de la longitud)
  - 100 consultas/día ≈ $3-5/mes
  - 30 consultas/día ≈ $1-2/mes

## Actualizar la KB

Si añades documentación nueva:

1. Ejecuta `extract_kb.py` para regenerar los archivos JSON
2. Ejecuta `setup_assistant.py` para crear un nuevo Assistant con la KB actualizada
3. Actualiza `OPENAI_ASSISTANT_ID` en Render con el nuevo ID

## Alternativa: Desplegar con Docker

Si prefieres usar tu propio servidor:

```bash
docker build -t artisjet-chatbot .
docker run -p 5000:5000 \
  -e OPENAI_API_KEY="sk-..." \
  -e OPENAI_ASSISTANT_ID="asst_..." \
  artisjet-chatbot
```

## Alternativa: Ejecutar en local

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
export OPENAI_ASSISTANT_ID="asst_..."
python app.py
```

Abre http://localhost:5000
