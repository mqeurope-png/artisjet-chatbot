#!/usr/bin/env python3
"""
Setup script — Crea el Assistant en OpenAI y sube la KB.
Ejecutar UNA VEZ antes de desplegar la web app.

Uso:
    export OPENAI_API_KEY="sk-..."
    python setup_assistant.py
"""

import os
import sys
import time
import json

try:
    from openai import OpenAI
except ImportError:
    print("Instala openai: pip install openai")
    sys.exit(1)

KB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rag_kb")
KB_FILE = os.path.join(KB_DIR, "artisjet_kb.json")

INSTRUCTIONS = """
Eres el asistente técnico oficial de artisJet, especializado en impresoras UV de la marca.
Tu rol es ayudar a distribuidores, técnicos y clientes finales a resolver problemas técnicos,
guiar instalaciones, y dar soporte sobre el software y mantenimiento de las impresoras.

Modelos que conoces:
- artisJet 2100 / 2100U
- artisJet 3000U Pro
- artisJet 5000U
- artisJet 6090U Pro
- artisJet proV6
- artisJet Proud
- artisJet FREEBIRD
- artisJet Young
- Rotary Jig UNI360

Áreas de conocimiento:
1. Instalación: Desembalaje, montaje, conexión, configuración inicial
2. Impresión: CMYK, CMYK+W, DualKCMY, VDP, rotary, embossing, braille
3. Mantenimiento: Limpieza, reemplazo captop/wiper/dampers, tinta, refrigerante UV
4. Errores: SX001, SY001, SZ001, SG001, inicialización, dongle
5. Problemas de impresión: Líneas, ghosting, adhesión, spraying, estática
6. Software: artisJet RIP v9.0, Workstation V5.0/V5.6

Reglas:
- SIEMPRE busca en tus archivos de conocimiento antes de responder.
- Responde en el MISMO IDIOMA que el usuario.
- Para errores: Descripción → Causas → Solución paso a paso.
- Si no encuentras info exacta, dilo y sugiere contactar soporte artisJet.
- Incluye advertencias de seguridad cuando corresponda.
- Menciona videos tutoriales disponibles si existen.
- No inventes información que no esté en la KB.
""".strip()


def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: Configura OPENAI_API_KEY")
        print("  export OPENAI_API_KEY='sk-...'")
        sys.exit(1)

    if not os.path.exists(KB_FILE):
        print(f"ERROR: No se encuentra la KB en {KB_FILE}")
        print("Ejecuta primero extract_kb.py para generarla.")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    print("=" * 60)
    print("artisJet Assistant — Setup")
    print("=" * 60)

    # 1. Vector Store
    print("\n[1/4] Creando Vector Store...")
    vs = client.vector_stores.create(name="artisJet KB")
    print(f"  ID: {vs.id}")

    # 2. Subir KB
    print("\n[2/4] Subiendo Knowledge Base...")
    file_size = os.path.getsize(KB_FILE) / 1024
    print(f"  Archivo: {KB_FILE} ({file_size:.0f} KB)")
    with open(KB_FILE, 'rb') as f:
        uploaded = client.files.create(file=f, purpose="assistants")
    print(f"  File ID: {uploaded.id}")

    # 3. Indexar
    print("\n[3/4] Indexando (puede tardar 1-3 minutos)...")
    client.vector_stores.files.create(
        vector_store_id=vs.id,
        file_id=uploaded.id
    )
    while True:
        status = client.vector_stores.retrieve(vs.id)
        c = status.file_counts
        if c.completed > 0 and c.in_progress == 0:
            print(f"  Completado: {c.completed} archivo(s) indexado(s)")
            break
        if c.failed > 0:
            print(f"  ERROR: {c.failed} archivo(s) fallaron")
            break
        print(f"  En progreso... ({c.in_progress} pendiente(s))")
        time.sleep(3)

    # 4. Crear Assistant
    print("\n[4/4] Creando Assistant...")
    assistant = client.beta.assistants.create(
        name="artisJet Technical Support",
        model="gpt-4o",
        instructions=INSTRUCTIONS,
        tools=[{"type": "file_search"}],
        tool_resources={
            "file_search": {
                "vector_store_ids": [vs.id]
            }
        }
    )

    print(f"\n{'=' * 60}")
    print("¡LISTO! Copia estos valores para configurar la web app:\n")
    print(f"  OPENAI_ASSISTANT_ID = {assistant.id}")
    print(f"  OPENAI_API_KEY      = {api_key[:8]}...{api_key[-4:]}")
    print(f"\nVector Store ID: {vs.id}")
    print(f"File ID: {uploaded.id}")
    print(f"{'=' * 60}")

    # Guardar config
    config = {
        "assistant_id": assistant.id,
        "vector_store_id": vs.id,
        "file_id": uploaded.id,
        "created": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    config_path = os.path.join(os.path.dirname(__file__), "assistant_config.json")
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"\nConfig guardada en: {config_path}")


if __name__ == "__main__":
    main()
