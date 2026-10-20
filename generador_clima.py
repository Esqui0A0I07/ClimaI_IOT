import csv
import json
import math
import os
import random
import time
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# =====================================================================
# CONFIGURACIÓN GENERAL
# =====================================================================
CREDENTIAL_FILE = "serviceAccountKey.json"
CSV_FILE = "datos_ambientales_tec.csv"
JSON_FILE = "datos_ambientales_tec.json"
COLLECTION_NAME = "lecturas_ambientales"

DIAS_HISTORICOS = 10          # Mínimo 10 días continuos según especificación
INTERVALO_MINUTOS = 30        # Espacios de 30 minutos entre lecturas
SEGUNDOS_CICLO_CONTINUO = 60  # Tiempo de espera para generar nuevos datos en vivo (ej. 60s para pruebas o 1800s para 30 min reales)

# Inicializar conexión con Firebase Firestore
if not os.path.exists(CREDENTIAL_FILE):
    raise FileNotFoundError(
        f"No se encontró '{CREDENTIAL_FILE}'. Descárgalo desde Firebase -> Configuración -> Cuentas de servicio."
    )

cred = credentials.Certificate(CREDENTIAL_FILE)
firebase_admin.initialize_app(cred)
db = firestore.client()


def simular_variables_santa_clara(dt: datetime, inyectar_atipico: bool = False) -> dict:
    """
    Simula condiciones climáticas realistas para Santa Clara, San Carlos (Octubre):
    - Temperatura: 19°C a 31°C (pico al mediodía/14:00, mínimo en la madrugada)
    - Humedad relativa: 80% a 95% (inversamente proporcional a la temperatura)
    - Velocidad del viento: 5 a 12 km/h
    - Radiación solar: 0 a 880 W/m² (activa entre 6:00 a.m. y 6:00 p.m.)
    - Ruido aleatorio controlado: ±1 a 2°C
    """
    hora_decimal = dt.hour + (dt.minute / 60.0)

    # Curva sinusoidal diurna con mínimo a las 5:00 a.m. y máximo a las 2:00 p.m. (14:00)
    onda_diurna = math.sin(((hora_decimal - 8.0) / 24.0) * 2.0 * math.pi)

    # 1. Temperatura (19°C - 31°C) + ruido controlado (±1.5°C)
    temp_base = 25.0 + (5.2 * onda_diurna)
    ruido_temp = random.uniform(-1.5, 1.5)
    temperatura = round(max(18.5, min(32.0, temp_base + ruido_temp)), 2)

    # 2. Humedad Relativa (80% - 95%), inversa a la curva térmica
    hum_base = 87.5 - (6.5 * onda_diurna)
    ruido_hum = random.uniform(-2.0, 2.0)
    humedad = round(max(78.0, min(98.0, hum_base + ruido_hum)), 2)

    # 3. Velocidad del Viento (5 - 12 km/h), mayor brisa en horas de la tarde
    viento_base = 8.0 + (2.5 * max(0.0, onda_diurna))
    ruido_viento = random.uniform(-1.2, 1.2)
    viento = round(max(4.0, min(14.5, viento_base + ruido_viento)), 2)

    # 4. Radiación Solar (W/m²), ciclo solar de 6:00 a 18:00
    if 6.0 <= hora_decimal <= 18.0:
        onda_solar = math.sin(((hora_decimal - 6.0) / 12.0) * math.pi)
        nubosidad_octubre = random.uniform(0.65, 1.0)
        radiacion = round(max(0.0, 860.0 * onda_solar * nubosidad_octubre), 1)
    else:
        radiacion = 0.0

    # Inyección controlada de valores atípicos (2% de probabilidad) para probar el filtro de la App Web
    es_atipico = False
    if inyectar_atipico and random.random() < 0.02:
        es_atipico = True
        tipo_fallo = random.choice(["temperatura", "viento"])
        if tipo_fallo == "temperatura":
            temperatura = round(random.choice([12.4, 38.9, 41.2]), 2)
        else:
            viento = round(random.choice([26.5, 31.0]), 2)

    fecha_str = dt.strftime("%Y-%m-%d")
    hora_str = dt.strftime("%H:%M")
    doc_id = dt.strftime("%Y-%m-%d_%H-%M")

    return {
        "id": doc_id,
        "fecha": fecha_str,
        "hora": hora_str,
        "timestamp": int(dt.timestamp() * 1000),
        "temperatura": temperatura,
        "humedad": humedad,
        "viento": viento,
        "radiacion": radiacion,
        "es_atipico": es_atipico,
        "ubicacion": "TEC Campus San Carlos - Santa Clara"
    }


def guardar_archivos_locales(registros: list):
    """Guarda el historial completo en archivos CSV y JSON en la PC."""
    campos = [
        "id", "fecha", "hora", "timestamp",
        "temperatura", "humedad", "viento", "radiacion",
        "es_atipico", "ubicacion"
    ]

    with open(CSV_FILE, mode="w", newline="", encoding="utf-8") as f_csv:
        writer = csv.DictWriter(f_csv, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)

    with open(JSON_FILE, mode="w", encoding="utf-8") as f_json:
        json.dump(registros, f_json, indent=2, ensure_ascii=False)

    print(f"[PC Local] Guardados {len(registros)} registros en '{CSV_FILE}' y '{JSON_FILE}'.")


def subir_lote_firestore(registros: list):
    """Sube múltiples registros a Firestore en lotes de hasta 450 documentos."""
    total = len(registros)
    tamano_lote = 450

    for i in range(0, total, tamano_lote):
        sub_lista = registros[i:i + tamano_lote]
        batch = db.batch()
        for reg in sub_lista:
            doc_ref = db.collection(COLLECTION_NAME).document(reg["id"])
            batch.set(doc_ref, reg)
        batch.commit()
        print(f"[Firebase] Sincronizado lote {i + 1} a {min(i + tamano_lote, total)} de {total}...")


def generar_historial_inicial() -> list:
    """Genera 10 días continuos de datos cada 30 minutos hasta el momento actual."""
    ahora = datetime.now().replace(second=0, microsecond=0)
    # Redondear al bloque de 30 minutos más cercano
    minuto_redondeado = 30 if ahora.minute >= 30 else 0
    fin_dt = ahora.replace(minute=minuto_redondeado)
    inicio_dt = fin_dt - timedelta(days=DIAS_HISTORICOS)

    registros = []
    cursor_dt = inicio_dt

    print(f"Generando datos históricos desde {inicio_dt} hasta {fin_dt}...")
    while cursor_dt <= fin_dt:
        lectura = simular_variables_santa_clara(cursor_dt, inyectar_atipico=True)
        registros.append(lectura)
        cursor_dt += timedelta(minutes=INTERVALO_MINUTOS)

    return registros


def ejecutar_sistema():
    # 1. Verificar si ya existe archivo local o generar el bloque histórico inicial
    if os.path.exists(JSON_FILE):
        print(f"Cargando historial existente desde '{JSON_FILE}'...")
        with open(JSON_FILE, mode="r", encoding="utf-8") as f:
            registros = json.load(f)
    else:
        registros = generar_historial_inicial()
        guardar_archivos_locales(registros)
        subir_lote_firestore(registros)

    # 2. Ciclo periódico continuo (simulación de sensor enviando nuevas lecturas)
    ultimo_timestamp = registros[-1]["timestamp"] / 1000.0
    cursor_dt = datetime.fromtimestamp(ultimo_timestamp)

    print("\n=== MODO PERIÓDICO ACTIVO ===")
    print(f"Generando un nuevo registro ambiental cada {SEGUNDOS_CICLO_CONTINUO} segundos (Ctrl+C para detener)...\n")

    try:
        while True:
            time.sleep(SEGUNDOS_CICLO_CONTINUO)
            cursor_dt += timedelta(minutes=INTERVALO_MINUTOS)
            nueva_lectura = simular_variables_santa_clara(cursor_dt, inyectar_atipico=True)

            # Actualizar almacenamiento local en PC
            registros.append(nueva_lectura)
            guardar_archivos_locales(registros)

            # Enviar nuevo documento a Firebase Firestore
            db.collection(COLLECTION_NAME).document(nueva_lectura["id"]).set(nueva_lectura)
            print(
                f"[Nuevo Registro -> Nube] {nueva_lectura['fecha']} {nueva_lectura['hora']} | "
                f"Temp: {nueva_lectura['temperatura']}°C | Hum: {nueva_lectura['humedad']}% | "
                f"Viento: {nueva_lectura['viento']} km/h | Rad: {nueva_lectura['radiacion']} W/m²"
            )
    except KeyboardInterrupt:
        print("\nSimulación detenida por el usuario. Archivos locales y nube sincronizados.")


if __name__ == "__main__":
    ejecutar_sistema()