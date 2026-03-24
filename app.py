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

# WooCommerce category IDs by model — for targeted browsing
WC_CATEGORIES = {
    "es": {
        "Young": 89, "3000U_Pro": 90, "3000U": 88, "5000U": 91,
        "2100U": 87, "Trust_6090": 260, "Proud": 340, "Freebird": 340,
        "MBO": 92, "otras": 92
    },
    "eu": {
        "Young": 52, "3000U_Pro": 50, "3000U": 48, "5000U": 51,
        "2100U": 47, "Trust_6090": 130, "Proud": 129, "Freebird": 129,
        "MBO": 54, "otras": 54
    }
}

# Load parts index for SKU-based search
_parts_index_path = os.path.join(os.path.dirname(__file__), "parts_index.json")
PARTS_INDEX = {}
try:
    with open(_parts_index_path, "r", encoding="utf-8") as f:
        PARTS_INDEX = json.load(f)
except Exception:
    pass  # Will work without it, just no SKU matching

# Rate limiting simple (por IP)
request_counts = {}
MAX_REQUESTS_PER_HOUR = int(os.environ.get("MAX_REQUESTS_PER_HOUR", 30))

# Token usage tracking
_usage_path = os.path.join(os.path.dirname(__file__), "token_usage.json")
_default_usage = {
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "total_tokens": 0,
    "total_requests": 0,
    "daily": {}  # "2026-03-24": {prompt, completion, total, requests}
}

def _load_usage():
    try:
        with open(_usage_path, "r") as f:
            return json.load(f)
    except Exception:
        return _default_usage.copy()

def _save_usage(usage):
    try:
        with open(_usage_path, "w") as f:
            json.dump(usage, f, indent=2)
    except Exception:
        pass

def track_tokens(run_status):
    """Track token usage from a completed run."""
    usage_data = getattr(run_status, 'usage', None)
    if not usage_data:
        return None

    prompt_tokens = getattr(usage_data, 'prompt_tokens', 0)
    completion_tokens = getattr(usage_data, 'completion_tokens', 0)
    total = prompt_tokens + completion_tokens

    usage = _load_usage()
    usage["total_prompt_tokens"] += prompt_tokens
    usage["total_completion_tokens"] += completion_tokens
    usage["total_tokens"] += total
    usage["total_requests"] += 1

    # Daily breakdown
    today = time.strftime("%Y-%m-%d")
    if today not in usage.get("daily", {}):
        usage.setdefault("daily", {})[today] = {
            "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "requests": 0
        }
    usage["daily"][today]["prompt_tokens"] += prompt_tokens
    usage["daily"][today]["completion_tokens"] += completion_tokens
    usage["daily"][today]["total_tokens"] += total
    usage["daily"][today]["requests"] += 1

    _save_usage(usage)

    app.logger.info(
        f"[TOKENS] This request: {prompt_tokens} prompt + {completion_tokens} completion = {total} | "
        f"Cumulative: {usage['total_tokens']} tokens, {usage['total_requests']} requests"
    )
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total": total}


def _wc_request(shop, params, per_page=6):
    """Single WooCommerce API request."""
    params.update({
        "per_page": per_page,
        "status": "publish",
        "consumer_key": shop["consumer_key"],
        "consumer_secret": shop["consumer_secret"],
    })
    resp = http_requests.get(f"{shop['url']}/products", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _format_products(raw_products):
    """Format raw WooCommerce products into our standard format."""
    products = []
    seen_ids = set()
    for p in raw_products:
        pid = p.get("id")
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        img = p["images"][0]["src"] if p.get("images") else ""
        products.append({
            "name": p.get("name", ""),
            "sku": p.get("sku", ""),
            "price": p.get("price", ""),
            "currency": "€",
            "url": p.get("permalink", ""),
            "image": img,
            "image_full": img,
            "categories": [c["name"] for c in p.get("categories", [])],
        })
    return products


def _find_skus_for_part(part_name, model=None):
    """Find matching SKUs from parts_index for a given part name and optional model."""
    if not PARTS_INDEX:
        return []
    part_lower = part_name.lower()
    matches = []
    for sku, info in PARTS_INDEX.items():
        name_lower = info["name"].lower()
        if part_lower in name_lower or name_lower in part_lower:
            if model:
                # Check if this SKU is for the right model
                model_lower = model.lower().replace(" ", "_")
                if any(model_lower in m.lower() for m in info["models"]):
                    matches.insert(0, sku)  # Priority
                else:
                    matches.append(sku)
            else:
                matches.append(sku)
    return matches[:5]


def search_woocommerce(query, region="es", per_page=4, model=None):
    """Search products using multiple strategies:
    1. SKU search (if query looks like a SKU or matches parts_index)
    2. Category-filtered search (if model is specified)
    3. Text search with progressive simplification
    4. Category browsing as fallback
    """
    shop = WOOCOMMERCE_SHOPS.get(region, WOOCOMMERCE_SHOPS["es"])
    categories = WC_CATEGORIES.get(region, WC_CATEGORIES["es"])
    all_results = []

    try:
        noise_words = {
            'artisjet', 'artis', 'mbo', 'impresora', 'printer', 'para',
            'for', 'de', 'the', 'pro', 'uv', 'led', 'mi', 'my', 'una',
            'un', 'el', 'la', 'los', 'las', 'del', 'con', 'como', 'how'
        }
        words = query.strip().split()
        core_words = [w for w in words if w.lower() not in noise_words and len(w) >= 2]

        # Categories that are NOT spare parts — used to filter out irrelevant products
        non_spare_cats = {
            'fluxlasers', 'impresoras uv led', 'impresoras textil', 'impresoras dtf',
            'hornos dtf', 'corte láser', 'servicios', 'uv dtf', 'consumibles textil',
            'láseres de fibra', 'smartjet', 'uv led printers', 'software', 'impresoras-uv-led'
        }

        def is_relevant(product, keywords):
            """Check if product is relevant: matches keywords AND is a spare part."""
            # Exclude printers, machines, lasers etc.
            prod_cats = [c.get('name', '').lower() for c in product.get('categories', [])]
            if any(any(nc in cat for nc in non_spare_cats) for cat in prod_cats):
                return False
            # Check keyword match in name/description/sku
            text = f"{product.get('name','')} {product.get('short_description','')} {product.get('sku','')}".lower()
            return any(kw.lower() in text for kw in keywords if len(kw) >= 3)

        # --- Strategy 1: SKU-based search ---
        # Check if query IS a SKU
        if query.upper().startswith(("SPU-", "YPB-", "YIS-", "UA3", "UA4", "U1")):
            app.logger.info(f"[SEARCH] Strategy 1a: direct SKU '{query}'")
            results = _wc_request(shop, {"sku": query}, per_page)
            if results:
                all_results.extend(results)

        # Find SKUs from parts_index matching the part name
        if not all_results:
            skus = _find_skus_for_part(' '.join(core_words), model)
            app.logger.info(f"[SEARCH] Strategy 1b: parts_index matched SKUs: {skus}")
            for sku in skus[:3]:
                results = _wc_request(shop, {"sku": sku}, per_page=2)
                all_results.extend(results)

        # If SKU search found enough results, skip other strategies
        if len(all_results) >= per_page:
            app.logger.info(f"[SEARCH] SKU strategy found {len(all_results)} — enough results")
            return _format_products(all_results)[:per_page]

        # --- Strategy 2: Category-filtered text search ---
        if model and len(all_results) < per_page:
            cat_id = None
            model_upper = model.upper().replace(" ", "_")
            for cat_name, cid in categories.items():
                if cat_name.upper() in model_upper or model_upper in cat_name.upper():
                    cat_id = cid
                    break
            if not cat_id and "MBO" in model_upper:
                cat_id = categories.get("MBO") or categories.get("otras")

            if cat_id:
                search_term = ' '.join(core_words) if core_words else query
                app.logger.info(f"[SEARCH] Strategy 2: search='{search_term}' in category {cat_id}")
                results = _wc_request(shop, {"search": search_term, "category": str(cat_id)}, per_page)
                # Only add relevant results
                for r in results:
                    if is_relevant(r, core_words):
                        all_results.append(r)

                # If still nothing, browse the whole category and filter
                if not all_results:
                    app.logger.info(f"[SEARCH] Strategy 2b: browsing full category {cat_id}")
                    results = _wc_request(shop, {"category": str(cat_id), "orderby": "title", "order": "asc"}, per_page=50)
                    for p in results:
                        if is_relevant(p, core_words):
                            all_results.append(p)

        # --- Strategy 3: Global text search (only if nothing found yet) ---
        if not all_results:
            attempts = []
            if core_words:
                attempts.append(' '.join(core_words))
                if len(core_words) > 1:
                    for w in core_words:
                        if len(w) >= 3:
                            attempts.append(w)
            else:
                attempts.append(query)

            for attempt in attempts:
                app.logger.info(f"[SEARCH] Strategy 3: global search '{attempt}'")
                results = _wc_request(shop, {"search": attempt}, per_page=15)
                # Filter for relevance
                relevant = [r for r in results if is_relevant(r, core_words)]
                if relevant:
                    all_results.extend(relevant)
                    break
                elif results:
                    # No relevant filter match, use first few results as-is
                    all_results.extend(results[:per_page])
                    break

        return _format_products(all_results)[:per_page]

    except Exception as e:
        app.logger.error(f"WooCommerce search error: {e}")
        return []


# OpenAI function tool definition for product search
PRODUCT_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_products",
        "description": (
            "Busca productos, recambios o piezas en la tienda online de Bomedia. "
            "Usa esta función SOLO cuando el usuario PIDA EXPLÍCITAMENTE comprar, buscar o ver "
            "un producto, repuesto o pieza. También cuando pregunte dónde comprar o cuánto cuesta algo. "
            "NO la uses si el usuario solo pregunta cómo solucionar un problema técnico o cómo hacer un mantenimiento. "
            "En esos casos, responde con la solución técnica basada en la Knowledge Base. "
            "IMPORTANTE: usa queries CORTAS de 1-2 palabras (ej: 'damper', 'captop', 'tinta'). "
            "Nunca incluyas la marca ni el modelo en la query — usa el parámetro 'model' para eso."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Término de búsqueda CORTO: solo el nombre de la pieza (ej: 'damper', 'captop', 'cabezal', 'tinta cyan', 'wiper', 'main board', 'sensor board')"
                },
                "region": {
                    "type": "string",
                    "enum": ["es", "eu"],
                    "description": "Región del cliente: 'es' para España (boprint.net), 'eu' para resto de Europa (artisjet-printers.eu)"
                },
                "model": {
                    "type": "string",
                    "description": "Modelo de impresora del usuario. Ej: 'Young', '3000U_Pro', '5000U', '2100U', 'Trust_6090', 'Proud', 'Freebird', 'MBO'. Ayuda a filtrar por la categoría correcta de recambios."
                }
            },
            "required": ["query", "region"]
        }
    }
}


def detect_parts_in_text(text, user_message=""):
    """Detect spare part keywords in assistant response or user message.
    Returns list of search terms if parts are mentioned."""
    combined = f"{text} {user_message}".lower()

    # Map of part keywords → search query
    part_keywords = {
        'damper': 'damper',
        'amortiguador': 'damper',
        'captop': 'captop',
        'cap top': 'captop',
        'capping': 'captop',
        'wiper': 'wiper',
        'limpiador': 'wiper',
        'cabezal': 'cabezal',
        'printhead': 'cabezal',
        'print head': 'cabezal',
        'tinta': 'tinta',
        'ink': 'tinta',
        'cartucho': 'cartucho',
        'cartridge': 'cartucho',
        'placa': 'placa',
        'board': 'board',
        'sensor': 'sensor',
        'cable': 'cable',
        'bomba': 'bomba',
        'pump': 'bomba',
        'lámpara uv': 'lampara uv',
        'uv lamp': 'lampara uv',
        'correa': 'correa',
        'belt': 'correa',
        'tubo': 'tubo',
        'tube': 'tubo',
        'jeringuilla': 'jeringuilla',
        'syringe': 'jeringuilla',
        'filtro': 'filtro',
        'filter': 'filtro',
    }

    found = []
    for keyword, search_term in part_keywords.items():
        if keyword in combined and search_term not in found:
            found.append(search_term)

    return found[:2]  # Max 2 search terms to keep it focused


def detect_model_in_text(text, user_message=""):
    """Detect printer model mentioned in text."""
    combined = f"{text} {user_message}".lower()
    model_map = {
        'trust': 'Trust_6090', '6090': 'Trust_6090',
        'young': 'Young',
        '3000u pro': '3000U_Pro', '3000 pro': '3000U_Pro', 'freebird': '3000U_Pro',
        '5000': '5000U',
        '2100': '2100U',
        'proud': 'Proud', 'prov6': 'Proud', 'pro v6': 'Proud',
        'mbo': 'MBO', '4060': 'MBO', '3020': 'MBO',
    }
    for keyword, model in model_map.items():
        if keyword in combined:
            return model
    return None


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

        # Ejecutar el asistente — solo file_search (RAG), sin product search
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID,
            tools=[
                {"type": "file_search"}
            ],
            additional_instructions=(
                "REGLAS DE RESPUESTA:\n"
                "1. BASA TUS RESPUESTAS EN LA KNOWLEDGE BASE. Busca SIEMPRE en los documentos antes de responder. "
                "Si no encuentras info específica, di que no tienes documentación para ese caso concreto.\n"
                "2. SÉ BREVE Y DIRECTO. Máximo 5-8 líneas para respuestas simples. "
                "No des pasos obvios como 'apaga la impresora' o 'ponte guantes'. "
                "Ve al grano con la información técnica útil.\n"
                "3. VÍDEOS: Si encuentras un vídeo en la KB, compártelo con su enlace. "
                "Si el vídeo es de otro modelo pero el procedimiento es similar, compártelo igualmente indicándolo. "
                "NUNCA inventes URLs — solo comparte enlaces que encuentres realmente en los documentos.\n"
                "4. NO repitas 'contacta con soporte de Bomedia' al final de cada respuesta. "
                "Solo menciónalo si el problema es realmente complejo o requiere intervención física.\n"
                "5. NO menciones productos ni tiendas. Tu función es dar soporte técnico, no vender.\n"
                "6. Cuando el usuario indique su modelo, responde directamente con la solución técnica.\n"
                "7. No inventes información técnica que no esté en la Knowledge Base. "
                "Es mejor una respuesta corta y precisa que una larga y genérica."
            )
        )

        # Esperar respuesta (con timeout)
        max_wait = 90
        start = time.time()
        token_info = None

        while time.time() - start < max_wait:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )

            if run_status.status == "completed":
                token_info = track_tokens(run_status)
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

        result = {
            "response": response_text,
            "sources": sources,
            "thread_id": thread_id,
            "follow_ups": follow_ups
        }
        if token_info:
            result["tokens"] = token_info
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": f"Error: {str(e)}"}), 500


@app.route("/api/products", methods=["GET"])
def search_products_api():
    """Direct product search endpoint."""
    query = request.args.get("q", "").strip()
    region = request.args.get("region", "es")
    if not query:
        return jsonify({"products": [], "error": "No search query"}), 400
    products = search_woocommerce(query, region, per_page=4)
    return jsonify({"products": products, "query": query, "region": region})


@app.route("/api/new-chat", methods=["POST"])
def new_chat():
    """Inicia una nueva conversación."""
    session.pop("thread_id", None)
    return jsonify({"status": "ok"})


@app.route("/api/usage", methods=["GET"])
def usage_api():
    """Token usage statistics. Access with ?key=admin-key for protection."""
    admin_key = os.environ.get("USAGE_ADMIN_KEY", "bomedia2024")
    if request.args.get("key") != admin_key:
        return jsonify({"error": "Unauthorized. Add ?key=YOUR_KEY"}), 401

    # Reset counter if requested
    if request.args.get("reset") == "true":
        _save_usage(_default_usage.copy())
        return jsonify({"status": "reset", "message": "Token usage counter has been reset to zero."})

    usage = _load_usage()

    # Estimate cost (GPT-4o pricing: $2.50/1M input, $10/1M output)
    prompt_cost = (usage["total_prompt_tokens"] / 1_000_000) * 2.50
    completion_cost = (usage["total_completion_tokens"] / 1_000_000) * 10.00
    total_cost = prompt_cost + completion_cost

    # Also count follow-up generation tokens (gpt-4o-mini: $0.15/1M input, $0.60/1M output)
    # These are approximate — follow-ups use ~300 tokens per call
    followup_est = usage["total_requests"] * 300
    followup_cost = (followup_est / 1_000_000) * 0.60

    return jsonify({
        "totals": {
            "prompt_tokens": usage["total_prompt_tokens"],
            "completion_tokens": usage["total_completion_tokens"],
            "total_tokens": usage["total_tokens"],
            "total_requests": usage["total_requests"]
        },
        "estimated_cost": {
            "assistant_input": f"${prompt_cost:.4f}",
            "assistant_output": f"${completion_cost:.4f}",
            "followups_est": f"${followup_cost:.4f}",
            "total_usd": f"${total_cost + followup_cost:.4f}"
        },
        "daily": usage.get("daily", {}),
        "note": "Costs are estimates based on GPT-4o pricing ($2.50/1M input, $10/1M output)"
    })


@app.route("/health")
def health():
    """Health check para el hosting."""
    return jsonify({"status": "ok", "assistant_id": bool(ASSISTANT_ID)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
