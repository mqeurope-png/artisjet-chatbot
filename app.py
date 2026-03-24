"""
artisJet Technical Support Chatbot — Web App
=============================================
Backend Flask que conecta con OpenAI Assistants API para RAG
sobre la Knowledge Base técnica de artisJet.
"""

import os
import re
import time
import json
import requests as http_requests
from flask import Flask, render_template, request, jsonify, session
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "artisjet-chatbot-secret-2024")

# OpenAI config (lazy init — solo se conecta cuando hay API key)
_openai_key = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=_openai_key) if _openai_key else None
ASSISTANT_ID = os.environ.get("OPENAI_ASSISTANT_ID")

# WooCommerce API config — two shops depending on region
WOOCOMMERCE_SHOPS = {
    "es": {
        "url": "https://boprint.net/wp-json/wc/v3",
        "consumer_key": os.environ.get("WC_BOPRINT_KEY", "ck_b34980e265d3f6146ef21069bcb090d8612d0e14"),
        "consumer_secret": os.environ.get("WC_BOPRINT_SECRET", "cs_10cb8c05251f8f2f7afdf29fa254d9306df40574"),
        "shop_base": "https://boprint.net"
    },
    "eu": {
        "url": "https://artisjet-printers.eu/wp-json/wc/v3",
        "consumer_key": os.environ.get("WC_ARTISJET_KEY", "ck_f5b4ac4fa9027af31ca6762d39add95168bcc9b0"),
        "consumer_secret": os.environ.get("WC_ARTISJET_SECRET", "cs_222cf463669a060c50c368a3505b6ef65aef3b10"),
        "shop_base": "https://artisjet-printers.eu"
    }
}

# Rate limiting simple (por IP)
request_counts = {}
MAX_REQUESTS_PER_HOUR = int(os.environ.get("MAX_REQUESTS_PER_HOUR", 30))


def search_woocommerce(query, region="es", per_page=6):
    """Search products on the appropriate WooCommerce shop."""
    shop = WOOCOMMERCE_SHOPS.get(region, WOOCOMMERCE_SHOPS["es"])
    try:
        resp = http_requests.get(
            f"{shop['url']}/products",
            params={
                "search": query,
                "per_page": per_page,
                "status": "publish",
                "consumer_key": shop["consumer_key"],
                "consumer_secret": shop["consumer_secret"],
            },
            timeout=10
        )
        resp.raise_for_status()
        products = []
        for p in resp.json():
            img = p["images"][0]["src"] if p.get("images") else ""
            # Use thumbnail size if available
            if img and "srcset" not in str(p["images"][0]):
                img_thumb = img.replace(".jpg", "-300x200.jpg").replace(".jpeg", "-300x200.jpeg").replace(".png", "-300x200.png")
            else:
                img_thumb = img
            products.append({
                "name": p.get("name", ""),
                "sku": p.get("sku", ""),
                "price": p.get("price", ""),
                "currency": "€",
                "url": p.get("permalink", ""),
                "image": img_thumb,
                "image_full": img,
                "categories": [c["name"] for c in p.get("categories", [])],
            })
        return products
    except Exception as e:
        app.logger.error(f"WooCommerce search error: {e}")
        return []


# OpenAI function tool definition for product search
PRODUCT_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_products",
        "description": (
            "Busca productos, recambios, piezas o consumibles en la tienda online de Bomedia. "
            "Usa esta función cuando el usuario pregunte por una pieza, repuesto, tinta, cabezal, "
            "damper, placa, sensor, cable, o cualquier producto que pueda comprarse. "
            "También cuando pregunte dónde comprar algo o cuánto cuesta."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Término de búsqueda del producto (ej: 'damper', 'cabezal xp600', 'tinta cyan', 'captop Proud')"
                },
                "region": {
                    "type": "string",
                    "enum": ["es", "eu"],
                    "description": "Región del cliente: 'es' para España (boprint.net), 'eu' para resto de Europa (artisjet-printers.eu)"
                }
            },
            "required": ["query", "region"]
        }
    }
}


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


def generate_follow_ups(thread_id, user_question, assistant_response):
    """Genera 2-3 preguntas de seguimiento contextualmente relevantes."""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un generador de preguntas de seguimiento para un chatbot de soporte técnico de impresoras UV artisJet. "
                        "Dada la pregunta del usuario y la respuesta del asistente, genera exactamente 3 preguntas cortas de seguimiento "
                        "que el usuario podría querer hacer a continuación. Las preguntas deben ser relevantes al contexto, "
                        "prácticas y en el MISMO IDIOMA que la pregunta original. "
                        "Responde SOLO con las 3 preguntas, una por línea, sin numeración ni viñetas."
                    )
                },
                {
                    "role": "user",
                    "content": f"Pregunta del usuario: {user_question}\n\nRespuesta del asistente (resumen): {assistant_response[:500]}"
                }
            ],
            max_tokens=200,
            temperature=0.7
        )
        raw = completion.choices[0].message.content.strip()
        suggestions = [line.strip() for line in raw.split("\n") if line.strip()]
        return suggestions[:3]
    except Exception:
        return []


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
    client_thread_id = data.get("thread_id")  # Thread ID from frontend

    if not user_message:
        return jsonify({"error": "Mensaje vacío"}), 400

    if len(user_message) > 2000:
        return jsonify({"error": "Mensaje demasiado largo (máximo 2000 caracteres)"}), 400

    if not ASSISTANT_ID:
        return jsonify({
            "error": "El asistente no está configurado. Contacta al administrador."
        }), 500

    try:
        # Use thread_id from frontend if provided, otherwise create/get from session
        if client_thread_id:
            try:
                client.beta.threads.retrieve(client_thread_id)
                thread_id = client_thread_id
                session["thread_id"] = thread_id
            except Exception:
                thread_id = get_or_create_thread()
        else:
            thread_id = get_or_create_thread()

        # Enviar mensaje del usuario
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_message
        )

        # Get region from frontend (default: es)
        user_region = data.get("region", "es")

        # Ejecutar el asistente with product search tool
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID,
            tools=[
                {"type": "file_search"},
                PRODUCT_SEARCH_TOOL
            ]
        )

        # Esperar respuesta (con timeout) — handle function calls
        max_wait = 90  # segundos (más tiempo por posible WooCommerce call)
        start = time.time()
        products_found = []

        while time.time() - start < max_wait:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )

            if run_status.status == "completed":
                break
            elif run_status.status == "requires_action":
                # Handle function calls (product search)
                tool_outputs = []
                for tool_call in run_status.required_action.submit_tool_outputs.tool_calls:
                    if tool_call.function.name == "search_products":
                        args = json.loads(tool_call.function.arguments)
                        query = args.get("query", "")
                        region = args.get("region", user_region)
                        products = search_woocommerce(query, region)
                        products_found.extend(products)

                        # Return results to the assistant so it can reference them
                        tool_outputs.append({
                            "tool_call_id": tool_call.id,
                            "output": json.dumps({
                                "products": products,
                                "shop": "boprint.net" if region == "es" else "artisjet-printers.eu",
                                "total_results": len(products)
                            }, ensure_ascii=False)
                        })

                if tool_outputs:
                    client.beta.threads.runs.submit_tool_outputs(
                        thread_id=thread_id,
                        run_id=run.id,
                        tool_outputs=tool_outputs
                    )
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
                                if hasattr(ann, 'text'):
                                    text = text.replace(ann.text, "")
                                if hasattr(ann, 'file_citation'):
                                    sources.append(ann.file_citation.file_id)

                        # Clean up citation artifacts
                        text = re.sub(r'【[^】]*】', '', text)
                        text = re.sub(r'\s+([.,;:!?])', r'\1', text)
                        response_text = text.strip()
                break

        # Generar sugerencias de seguimiento basadas en el contexto
        follow_ups = generate_follow_ups(thread_id, user_message, response_text)

        return jsonify({
            "response": response_text,
            "sources": sources,
            "thread_id": thread_id,
            "follow_ups": follow_ups,
            "products": products_found
        })

    except Exception as e:
        return jsonify({"error": f"Error: {str(e)}"}), 500


@app.route("/api/products", methods=["GET"])
def search_products_api():
    """Direct product search endpoint."""
    query = request.args.get("q", "").strip()
    region = request.args.get("region", "es")
    if not query:
        return jsonify({"products": [], "error": "No search query"}), 400
    products = search_woocommerce(query, region, per_page=6)
    return jsonify({"products": products, "query": query, "region": region})


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
