"""
Posiciones de jugadores desde lapreferente.com -> PostgreSQL

Fuente colaborativa: la cobertura es irregular (entre el 40% y el 85% segun
el equipo) y los datos los introducen voluntarios, no el club ni la RFEF.
Por eso todo lo que entra por aqui queda marcado con posicion_origen.

Uso:
    python src/posiciones.py --descargar    # baja las 16 plantillas a bronze/
    python src/posiciones.py --inspect      # enseña la primera fila parseada
    python src/posiciones.py --cargar       # cruza y actualiza la base
"""

from __future__ import annotations

import argparse
import os
import re
import time
import unicodedata
from pathlib import Path

import psycopg2
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

RAIZ = Path(__file__).resolve().parent.parent
RAW = RAIZ / "bronze" / "plantillas"
PAUSA = 3

BASE = "https://www.lapreferente.com/"
HEADERS = {
    "User-Agent": (
        "proyecto-portfolio-analitica/0.1 "
        "(scraper educativo; contacto: tu-email@dominio.com)"
    )
}

# slug de lapreferente -> palabra con la que localizar el equipo en dh.equipos
EQUIPOS = {
    "E8760C27109-11/fc-cartagena-sad-juvenil": "cartagena",
    "E5115C27109-11/cd-castellon-sad-juvenil": "castellon",
    "E5119C27109-11/elche-cf-sad-juvenil": "elche",
    "E11055C27109-11/kelme-cf-juvenil": "kelme",
    "E5112C27109-11/levante-ud-sad-juvenil": "levante",
    "E3145C27109-11/rcd-mallorca-juvenil": "mallorca",
    "E25098C27109-11/murcia-promises-cf-juvenil": "promises",
    "E26555C27109-11/patacona-cf-juvenil": "patacona",
    "E9845C27109-11/ad-penya-arrabal-juvenil": "arrabal",
    "E5111C27109-11/real-murcia-cf-sad-juvenil": "real murcia",
    "E7568C27109-11/cd-roda-juvenil": "roda",
    "E5066C27109-11/cd-san-francisco-juvenil": "san francisco",
    "E15878C27109-11/torrent-cf-juvenil": "torrent",
    "E5110C27109-11/ucam-universidad-catolica-de-murcia-cf-juvenil": "ucam",
    "E5116C27109-11/valencia-cf-juvenil": "valencia",
    "E5120C27109-11/villarreal-cf-sad-juvenil": "villarreal",
}

# demarcacion de la fuente -> linea
LINEAS = {
    "portero": "POR",
    "defensa": "DEF",
    "central": "DEF",
    "libero": "DEF",
    "lateral derecho": "DEF",
    "lateral izquierdo": "DEF",
    "carrilero derecho": "DEF",
    "carrilero izquierdo": "DEF",
    "centrocampista": "MED",
    "medio centro": "MED",
    "medio derecho": "MED",
    "medio izquierdo": "MED",
    "pivote": "MED",
    "mediapunta": "MED",
    "media punta": "MED",
    "delantero": "DEL",
    "delantero centro": "DEL",
    "segundo delantero": "DEL",
    "extremo derecho": "DEL",
    "extremo izquierdo": "DEL",
}

RE_JUGADOR = re.compile(r"J\d+C\d+/")
RE_EDAD = re.compile(r"^\d{1,2}$")


# ------------------------------------------------------------------ utils

def norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def tokens(nombre: str) -> frozenset[str]:
    """Conjunto de palabras del nombre, sin orden.

    El acta escribe 'CIVERA VERA, GILBERT' y lapreferente 'Gilbert Civera
    Vera', asi que comparar cadenas no sirve: hay que comparar conjuntos.
    """
    return frozenset(norm(nombre).split())


# -------------------------------------------------------------- descarga

def descargar() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    sesion = requests.Session()
    sesion.headers.update(HEADERS)

    for slug in EQUIPOS:
        nombre = slug.split("/")[-1]
        fichero = RAW / f"{nombre}.html"
        if fichero.exists():
            print(f"  {nombre}  (ya estaba)")
            continue

        r = sesion.get(BASE + slug, timeout=30)
        r.raise_for_status()
        r.encoding = "iso-8859-1"
        fichero.write_text(r.text, encoding="utf-8")
        print(f"  {nombre}  descargada ({len(r.text)} bytes)")
        time.sleep(PAUSA)


# ---------------------------------------------------------------- parseo

def parse_plantilla(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jugadores = []

    for a in soup.find_all("a", href=RE_JUGADOR):
        fila = a.find_parent("tr")
        if fila is None:
            continue

        celdas = [c.get_text(" ", strip=True) for c in fila.find_all("td")]
        if not celdas:
            continue

        # la demarcacion es la celda cuyo texto esta en el diccionario
        detalle = None
        idx = None
        for i, texto in enumerate(celdas):
            if norm(texto) in LINEAS:
                detalle = texto.strip()
                idx = i
                break

        # la edad suele ir dos celdas despues de la demarcacion
        edad = None
        if idx is not None and idx + 2 < len(celdas):
            bruto = celdas[idx + 2]
            if RE_EDAD.match(bruto) and 14 <= int(bruto) <= 25:
                edad = int(bruto)

        jugadores.append({
            "nombre": a.get_text(" ", strip=True),
            "detalle": detalle,
            "linea": LINEAS.get(norm(detalle)) if detalle else None,
            "edad": edad,
        })

    return jugadores


# ----------------------------------------------------------------- carga

def equipo_id(cur, palabra: str) -> int | None:
    cur.execute(
        "SELECT id FROM dh.equipos WHERE nombre_norm LIKE %s",
        (f"%{palabra}%",),
    )
    filas = cur.fetchall()
    if len(filas) != 1:
        print(f"  ! '{palabra}' casa con {len(filas)} equipos, se omite")
        return None
    return filas[0][0]


def cargar(conn) -> None:
    total_ok = total_sin = total_amb = 0

    with conn.cursor() as cur:
        for slug, palabra in EQUIPOS.items():
            nombre_fich = slug.split("/")[-1]
            fichero = RAW / f"{nombre_fich}.html"
            if not fichero.exists():
                print(f"{nombre_fich}: falta el HTML, lanza --descargar")
                continue

            eq_id = equipo_id(cur, palabra)
            if eq_id is None:
                continue

            cur.execute(
                """
                SELECT id, nombre_completo FROM dh.jugadores
                WHERE equipo_id = %s
                """,
                (eq_id,),
            )
            nuestros = [(jid, nom, tokens(nom)) for jid, nom in cur.fetchall()]

            fuente = [j for j in parse_plantilla(fichero.read_text(encoding="utf-8"))
                      if j["linea"]]

            ok = sin = amb = 0
            for jid, nombre, mios in nuestros:
                puntuados = [(len(mios & tokens(j["nombre"])), j) for j in fuente]
                puntuados = [(n, j) for n, j in puntuados if n >= 3]
                puntuados.sort(key=lambda x: -x[0])

                mejor = None
                if puntuados:
                    if len(puntuados) == 1 or puntuados[0][0] > puntuados[1][0]:
                        mejor = puntuados[0][1]
                    else:
                        amb += 1
                        print(f"    ambiguo: {nombre}")

                if mejor:
                    exacto = mios <= tokens(mejor["nombre"])
                    cur.execute(
                        """
                        UPDATE dh.jugadores
                        SET posicion = %s,
                            posicion_detalle = %s,
                            posicion_origen = %s
                        WHERE id = %s
                        """,
                        (mejor["linea"], mejor["detalle"],
                         "lapreferente" if exacto else "lapreferente-aprox",
                         jid),
                    )
                    ok += 1
                elif not puntuados:
                    sin += 1

            print(f"{nombre_fich}: {ok} cruzados, {sin} sin cruzar, {amb} ambiguos"
                  f"  (fuente: {len(fuente)} con posicion)")
            total_ok += ok
            total_sin += sin
            total_amb += amb

    conn.commit()
    print(f"\nTOTAL: {total_ok} cruzados, {total_sin} sin cruzar, "
          f"{total_amb} ambiguos")


# ------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--descargar", action="store_true")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--cargar", action="store_true")
    args = ap.parse_args()

    if args.descargar:
        descargar()
        return

    if args.inspect:
        fichero = RAW / "cd-roda-juvenil.html"
        if not fichero.exists():
            print("Primero lanza --descargar")
            return
        for j in parse_plantilla(fichero.read_text(encoding="utf-8"))[:10]:
            print(j)
        return

    if args.cargar:
        conn = psycopg2.connect(os.environ["PG_DSN"])
        try:
            cargar(conn)
        finally:
            conn.close()
        return

    ap.error("indica --descargar, --inspect o --cargar")


if __name__ == "__main__":
    main()