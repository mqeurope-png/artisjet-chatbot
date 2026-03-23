"""
artisJet Technical Support Chatbot — Web App
=============================================
Backend Flask que conecta con OpenAI Assistants API para RAG
sobre la Knowledge Base técnica de artisJet.
"""

import os
import time
import json
from flask import Flask, render_template, request, jsonify, session
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "artisjet-chatbot-secret-2024")

# OpenAI config (lazy init — solo se conecta cuando hay API key)
_openai_key = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_key) if _openai_key else None
ASSISTANT_ID = os.environ.get("OPENAI_ASSISTANT_ID")

# Rate limiting simple (por IP)
request_counts = {}
MAX_REQUESTS_PER_HOUR = int(os.environ.get("MAX_REQUESTS_PER_HOUR", 30))


def get_or_create_thread():
    """Obtiene el thread actual o crea uno nuevo."""
    thread_id = session.get("thread_id")
    if thread_id:
        try:
            client.beta.threads.retrieve(thread_id)
            return thread_id
        except Exception:
            pass

    thread = client.beta.threads.create()
    session["thread_id"] = thread.id
    return thread.id


def check_rate_limit(ip):
    """Rate limiting básico por IP."""
    now = time.time()
    if ip not in request_counts:
        request_counts[ip] = []

    # Limpiar requests viejas (más de 1 hora)
    request_counts[ip] = [t for t in request_counts[ip] if now - t < 3600]

    if len(request_counts[ip]) >= MAX_REQUESTS_PER_HOUR:
        return False

    request_counts[ip].append(now)
    return True


@app.route("/")
def index():
    """Página principal del chatbot."""
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """Endpoint de chat — envía mensaje y recibe respuesta del asistente."""
    # Rate limit
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip):
        return jsonify({
            "error": "Has alcanzado el límite de consultas por hora. Intenta de nuevo más tarde."
        }), 429

    data = request.json
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Mensaje vacío"}), 400

    if len(user_message) > 2000:
        return jsonify({"error": "Mensaje demasiado largo (máximo 2000 caracteres)"}), 400

    if not ASSISTANT_ID:
        return jsonify({
            "error": "El asistente no está configurado. Contacta al administrador."
        }), 500

    try:
        thread_id = get_or_create_thread()

        # Enviar mensaje del usuario
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_message
        )

        # Ejecutar el asistente
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )

        # Esperar respuesta (con timeout)
        max_wait = 60  # segundos
        start = time.time()
        while time.time() - start < max_wait:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )

            if run_status.status == "completed":
                break
            elif run_status.status in ("failed", "cancelled", "expired"):
                return jsonify({
                    "error": f"Error del asistente: {run_status.status}"
                }), 500

            time.sleep(1)
        else:
            return jsonify({"error": "Timeout — la consulta tardó demasiado"}), 504

        # Obtener respuesta
        messages = client.beta.threads.messages.list(
            thread_id=thread_id,
            order="desc",
            limit=1
        )

        response_text = ""
        sources = []

        for msg in messages.data:
            if msg.role == "assistant":
                for block in msg.content:
                    if block.type == "text":
                        text = block.text.value

                        # Extraer anotaciones/fuentes
                        if block.text.annotations:
                            for ann in block.text.annotations:
                                # Limpiar las referencias del texto
                                if hasattr(ann, 'text'):
                                    text = text.replace(ann.text, "")
                                if hasattr(ann, 'file_citation'):
                                    sources.append(ann.file_citation.file_id)

                        response_text = text.strip()
                break

        return jsonify({
            "response": response_text,
            "sources": sources,
            "thread_id": thread_id
        })

    except Exception as e:
        return jsonify({"error": f"Error: {str(e)}"}), 500


@app.route("/api/new-chat", methods=["POST"])
def new_chat():
    """Inicia una nueva conversación."""
    session.pop("thread_id", None)
    return jsonify({"status": "ok"})


@app.route("/health")
def health():
    """Health check para el hosting."""
    return jsonify({"status": "ok", "assistant_id": bool(ASSISTANT_ID)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
