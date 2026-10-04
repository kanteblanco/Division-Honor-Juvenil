"""
Pipeline FFCV - Liga Nacional Juvenil

La FFCV sirve los datos por una API JSON, asi que no hay que parsear HTML.
Una sola peticion por partido devuelve alineaciones con dorsal y posicion,
sustituciones con minuto, goles y tarjetas, todo con codigo de jugador.

Eso simplifica mucho respecto a la RFEF: no hay que cruzar por nombre ni
curar posiciones a mano, y el marcador viene en claro.

Uso:
    python src/ffcv.py --jornadas            # lista el calendario
    python src/ffcv.py --inspect 26476339    # vuelca una ficha sin fotos
    python src/ffcv.py                       # ciclo completo
    python src/ffcv.py --jornada 5           # solo una jornada
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

RAIZ = Path(__file__).resolve().parent.parent
RAW = RAIZ / "bronze" / "ffcv"

API = "https://ffcv.es/competiciones/api"
TEMPORADA_API = 22
TEMPORADA = "2026-2027"
COMPETICION = 905431878          # Liga Nacional Juvenil
COD_GRUPO = 905431879            # Grup VIII
GRUPO = 8
PAUSA = 4

HEADERS = {
    "User-Agent": (
        "proyecto-portfolio-analitica/0.1 "
        "(scraper educativo; contacto: tu-email@dominio.com)"
    ),
    "Accept": "application/json",
}

# codigos de la fuente. 100 esta confirmado; el resto es suposicion y el
# script avisa cuando encuentra uno que no conoce
TIPO_AMONESTACION = {"100": "amarilla", "101": "roja"}
TIPO_GOL = {"100": "Gol normal", "101": "Gol de penalti", "102": "Gol en propia"}

sesion = requests.Session()
sesion.headers.update(HEADERS)


# ------------------------------------------------------------------- api

def pedir(ruta: str, **params) -> dict:
    for intento in range(4):
        try:
            r = sesion.get(f"{API}/{ruta}", params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            espera = 10 * (intento + 1)
            print(f"    {e}; reintento en {espera}s", file=sys.stderr)
            time.sleep(espera)
    raise RuntimeError(f"No se pudo leer {ruta} con {params}")


def jornadas() -> list[dict]:
    d = pedir("filtros/jornadas_fetch.php", cod_grupo=COD_GRUPO)
    return d.get("jornadas", [])


def jornadas_jugadas() -> list[int]:
    """Jornadas cuya fecha ya ha pasado."""
    hoy = date.today()
    salida = []
    for j in jornadas():
        try:
            fecha = datetime.strptime(j["fecha_jornada"], "%d-%m-%Y").date()
        except (KeyError, ValueError):
            continue
        if fecha <= hoy:
            salida.append(int(j["codjornada"]))
    return sorted(salida)


def partidos_de(jornada: int) -> list[dict]:
    d = pedir(
        "partidos/resultados_por_grupo_jornada_data.php",
        cod_temporada=TEMPORADA_API,
        cod_competicion=COMPETICION,
        cod_grupo=COD_GRUPO,
        cod_jornada=jornada,
    )
    return d.get("partidos", [])


def ficha(cod_partido: str | int) -> dict:
    """Ficha completa de un partido, sin las fotos en base64."""
    fichero = RAW / f"ficha_{cod_partido}.json"
    if fichero.exists():
        return json.loads(fichero.read_text(encoding="utf-8"))

    d = pedir("partidos/ficha_partido_ajax.php", cod_partido=cod_partido)

    # las fotos multiplican por diez el tamano y no se guardan nunca
    for lado in ("local", "visitante"):
        for j in d.get(f"jugadores_equipo_{lado}", []):
            j.pop("foto", None)

    RAW.mkdir(parents=True, exist_ok=True)
    fichero.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    time.sleep(PAUSA)
    return d


# -------------------------------------------------------------- minutos

def calcular_minutos(jugadores: list[dict], subs: list[dict],
                     rojas: dict[str, int]) -> None:
    """Anade 'minutos' a cada jugador. Cruza por codigo, no por dorsal."""
    entra = {s["codjugador_entra"]: int(s["minuto"]) for s in subs}
    sale = {s["codjugador_sale"]: int(s["minuto"]) for s in subs}

    for j in jugadores:
        cod = j["codjugador"]
        e, s = entra.get(cod), sale.get(cod)
        titular = j.get("titular") == "1"

        if e is not None and s is not None:
            mins = s - e
        elif titular and s is not None:
            mins = s
        elif titular:
            mins = 90
        elif e is not None:
            mins = 90 - e
        else:
            mins = 0

        roja = rojas.get(cod)
        if roja is not None:
            mins = min(mins, roja - (e if e is not None else 0))

        j["minutos"] = max(mins, 0)
        j["min_entra"] = e
        j["min_sale"] = s


# ----------------------------------------------------------------- carga

def upsert_equipo(cur, cod: str, nombre: str) -> int:
    """El prefijo evita chocar con los codigos de equipo de la RFEF."""
    import re
    import unicodedata

    t = unicodedata.normalize("NFKD", nombre)
    t = "".join(c for c in t if not unicodedata.combining(c))
    norm = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t.lower())).strip()

    cur.execute(
        """
        INSERT INTO dh.equipos (cod_novanet, nombre, nombre_norm, grupo)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (cod_novanet) DO UPDATE
            SET nombre = EXCLUDED.nombre, nombre_norm = EXCLUDED.nombre_norm
        RETURNING id
        """,
        (f"FFCV-{cod}", nombre, norm, GRUPO),
    )
    return cur.fetchone()[0]


def procesar(conn, f: dict) -> tuple[int, int, int]:
    cod_acta = int(f["codacta"])
    desconocidos = set()

    with conn.cursor() as cur:
        local_id = upsert_equipo(cur, f["codigo_equipo_local"], f["equipo_local"])
        visit_id = upsert_equipo(cur, f["codigo_equipo_visitante"],
                                 f["equipo_visitante"])

        fecha = None
        try:
            fecha = datetime.strptime(f"{f['fecha']} {f['hora']}",
                                      "%d-%m-%Y %H:%M")
        except (KeyError, ValueError):
            pass

        cur.execute(
            """
            INSERT INTO dh.partidos (
                cod_acta, temporada, competicion, grupo, jornada, fecha,
                estadio, local_id, visitante_id, goles_local, goles_visitante,
                acta_descargada, acta_parseada)
            VALUES (%s,%s,'LN',%s,%s,%s,%s,%s,%s,%s,%s,TRUE,TRUE)
            ON CONFLICT (cod_acta) DO UPDATE SET
                fecha = EXCLUDED.fecha,
                goles_local = EXCLUDED.goles_local,
                goles_visitante = EXCLUDED.goles_visitante,
                acta_parseada = TRUE,
                actualizado_en = now()
            """,
            (cod_acta, TEMPORADA, GRUPO, int(f["jornada"]), fecha,
             f.get("campo"), local_id, visit_id,
             int(f["goles_local"]), int(f["goles_visitante"])),
        )

        n_jug = n_gol = n_tar = 0

        for lado, equipo_id in (("local", local_id), ("visitante", visit_id)):
            jugadores = f.get(f"jugadores_equipo_{lado}", [])
            subs = f.get(f"sustituciones_equipo_{lado}", [])
            tarjetas = f.get(f"tarjetas_equipo_{lado}", [])

            rojas = {}
            for t in tarjetas:
                tipo = TIPO_AMONESTACION.get(t["codigo_tipo_amonestacion"])
                if tipo is None:
                    desconocidos.add(t["codigo_tipo_amonestacion"])
                if tipo == "roja" or t.get("segunda_amarilla") == "1":
                    rojas[t["codjugador"]] = int(t["minuto"])

            calcular_minutos(jugadores, subs, rojas)

            for j in jugadores:
                cur.execute(
                    """
                    INSERT INTO dh.jugadores
                        (cod_federativo, equipo_id, temporada, competicion, dorsal,
                         nombre_corto, nombre_completo, posicion_detalle,
                         posicion_origen)
                    VALUES (%s,%s,%s,'LN',%s,%s,%s,%s,'ffcv')
                    ON CONFLICT (cod_federativo) DO UPDATE
                        SET dorsal = EXCLUDED.dorsal,
                            nombre_completo = EXCLUDED.nombre_completo,
                            posicion_detalle =
                                COALESCE(dh.jugadores.posicion_detalle,
                                         EXCLUDED.posicion_detalle)
                    RETURNING id
                    """,
                    (int(j["codjugador"]), equipo_id, TEMPORADA,
                     int(j["dorsal"]), j["nombre_jugador"], j["nombre_jugador"],
                     j.get("posicion")),
                )
                jid = cur.fetchone()[0]
                n_jug += 1

                cur.execute(
                    """
                    INSERT INTO dh.participaciones
                        (cod_acta, jugador_id, equipo_id, titular, capitan,
                         portero, min_entra, min_sale, minutos)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (cod_acta, jugador_id) DO UPDATE SET
                        titular = EXCLUDED.titular,
                        minutos = EXCLUDED.minutos,
                        min_entra = EXCLUDED.min_entra,
                        min_sale = EXCLUDED.min_sale
                    """,
                    (cod_acta, jid, equipo_id, j.get("titular") == "1",
                     j.get("capitan") == "1", j.get("portero") == "1",
                     j.get("min_entra"), j.get("min_sale"), j["minutos"]),
                )

            for g in f.get(f"goles_equipo_{lado}", []):
                cur.execute(
                    """
                    INSERT INTO dh.eventos
                        (cod_acta, jugador_id, equipo_id, tipo, minuto, detalle)
                    SELECT %s, id, %s, 'gol', %s, %s FROM dh.jugadores
                    WHERE cod_federativo = %s
                    ON CONFLICT DO NOTHING
                    """,
                    (cod_acta, equipo_id, int(g["minuto"]),
                     TIPO_GOL.get(g.get("tipo_gol")), int(g["codjugador"])),
                )
                n_gol += 1

            for t in tarjetas:
                tipo = TIPO_AMONESTACION.get(t["codigo_tipo_amonestacion"],
                                             "amarilla")
                if t.get("segunda_amarilla") == "1":
                    tipo = "roja"
                cur.execute(
                    """
                    INSERT INTO dh.eventos
                        (cod_acta, jugador_id, equipo_id, tipo, minuto)
                    SELECT %s, id, %s, %s, %s FROM dh.jugadores
                    WHERE cod_federativo = %s
                    ON CONFLICT DO NOTHING
                    """,
                    (cod_acta, equipo_id, tipo, int(t["minuto"]),
                     int(t["codjugador"])),
                )
                n_tar += 1

    conn.commit()

    if desconocidos:
        print(f"    ! codigos de tarjeta desconocidos: {sorted(desconocidos)}")

    return n_jug, n_gol, n_tar


# ------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jornadas", action="store_true")
    ap.add_argument("--inspect", metavar="COD_PARTIDO")
    ap.add_argument("--jornada", type=int)
    args = ap.parse_args()

    if args.jornadas:
        for j in jornadas():
                print(f"  J{j['codjornada']:>2}  {j['fecha_jornada']}")
        return

    if args.inspect:
        f = ficha(args.inspect)
        for k, v in f.items():
            if isinstance(v, list):
                print(f"{k}: lista de {len(v)}")
            else:
                print(f"{k}: {v!r}"[:100])
        return

    conn = psycopg2.connect(os.environ["PG_DSN"])
    try:
        objetivo = [args.jornada] if args.jornada else jornadas_jugadas()
        print(f"Liga Nacional Grupo {GRUPO} | jornadas: {objetivo}\n")

        for j in objetivo:
            for p in partidos_de(j):
                if p.get("estado") != "1":
                    print(f"  J{j:02d}  {p['codacta']}  sin acta cerrada, se omite")
                    continue
                f = ficha(p["codacta"])
                n_j, n_g, n_t = procesar(conn, f)
                print(f"  J{j:02d}  {f['equipo_local']} {f['goles_local']}-"
                      f"{f['goles_visitante']} {f['equipo_visitante']}  "
                      f"({n_j} jugadores, {n_g} goles, {n_t} tarjetas)")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM dh.partidos WHERE competicion = 'LN'")
            partidos = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM dh.jugadores WHERE competicion = 'LN'")
            jug = cur.fetchone()[0]
        print(f"\n{partidos} partidos, {jug} jugadores en Liga Nacional")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

