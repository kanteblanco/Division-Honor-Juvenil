import os
import re
import unicodedata

import psycopg2
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from parser_acta import (parse_alineaciones, parse_sustituciones,
                         parse_goles, parse_tarjetas, calcular_minutos)

load_dotenv()
TEMPORADA = "2026-2027"


def norm(texto):
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def procesar_acta(conn, cod_acta, html):
    soup = BeautifulSoup(html, "lxml")

    goles = parse_goles(soup)
    tarjetas = parse_tarjetas(soup)
    jugadores = calcular_minutos(parse_alineaciones(soup), parse_sustituciones(soup), tarjetas)
    if not jugadores:
        raise ValueError("acta sin alineaciones")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT local_id, visitante_id FROM dh.partidos WHERE cod_acta = %s",
            (cod_acta,),
        )
        fila = cur.fetchone()
        if not fila:
            raise ValueError(f"El partido {cod_acta} no está en dh.partidos")
        local_id, visitante_id = fila

        # 1. jugadores y participaciones
        por_nombre = {}
        for j in jugadores:
            equipo_id = local_id if j["local"] else visitante_id

            cur.execute(
                """
                INSERT INTO dh.jugadores
                    (cod_federativo, equipo_id, temporada, dorsal, nombre_corto, nombre_completo)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (cod_federativo) DO UPDATE
                    SET dorsal = EXCLUDED.dorsal,
                        nombre_completo = EXCLUDED.nombre_completo
                RETURNING id
                """,
                (j["cod_federativo"], equipo_id, TEMPORADA, j["dorsal"],
                 j["nombre"], j["nombre"]),
            )
            jugador_id = cur.fetchone()[0]
            por_nombre[(norm(j["nombre"]), j["local"])] = (jugador_id, equipo_id)

            cur.execute(
                """
                INSERT INTO dh.participaciones
                    (cod_acta, jugador_id, equipo_id, titular, minutos)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (cod_acta, jugador_id) DO UPDATE
                    SET titular = EXCLUDED.titular,
                        minutos = EXCLUDED.minutos
                """,
                (cod_acta, jugador_id, equipo_id, j["titular"], j["minutos"]),
            )

        # 2. eventos
        def insertar(tipo, minuto, jugador_id, equipo_id, detalle=None):
            cur.execute(
                """
                INSERT INTO dh.eventos (cod_acta, jugador_id, equipo_id, tipo, minuto, detalle)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (cod_acta, jugador_id, equipo_id, tipo, minuto, detalle),
            )

        sin_cruzar = []

        for g in goles:
            clave = norm(g["nombre"])
            encontrado = (por_nombre.get((clave, True)) or por_nombre.get((clave, False)))
            if not encontrado:
                sin_cruzar.append(("gol", g["nombre"]))
                continue
            jugador_id, equipo_id = encontrado
            insertar("gol", g["minuto"], jugador_id, equipo_id, g["tipo"])

        for t in tarjetas:
            encontrado = por_nombre.get((norm(t["nombre"]), t["local"]))
            if not encontrado:
                sin_cruzar.append(("tarjeta", t["nombre"]))
                continue
            jugador_id, equipo_id = encontrado
            insertar(t["tipo"], t["minuto"], jugador_id, equipo_id)

        cur.execute(
            "UPDATE dh.partidos SET acta_parseada = TRUE WHERE cod_acta = %s",
            (cod_acta,),
        )

    conn.commit()
    return len(jugadores), len(goles), len(tarjetas), sin_cruzar


if __name__ == "__main__":
    with open("acta.html", encoding="utf-8") as f:
        html = f.read()

    conn = psycopg2.connect(os.environ["PG_DSN"])
    try:
        n_j, n_g, n_t, sin_cruzar = procesar_acta(conn, 70694258, html)
        print(f"{n_j} jugadores, {n_g} goles, {n_t} tarjetas")
        for tipo, nombre in sin_cruzar:
            print(f"  SIN CRUZAR ({tipo}): {nombre}")
    finally:
        conn.close()