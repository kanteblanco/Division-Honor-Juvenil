import os
import unicodedata
import re

import psycopg2
from dotenv import load_dotenv

from parser_listado import parse_jornada

load_dotenv()

TEMPORADA = "2026-2027"



def norm(texto):
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def upsert_equipo(cur, equipo, grupo):
    cur.execute(
        """
        INSERT INTO dh.equipos (cod_novanet, nombre, nombre_norm, grupo)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (cod_novanet) DO UPDATE
            SET nombre = EXCLUDED.nombre,
                nombre_norm = EXCLUDED.nombre_norm
        RETURNING id
        """,
        (equipo["cod_novanet"], equipo["nombre"], norm(equipo["nombre"]), grupo),
    )
    return cur.fetchone()[0]


def cargar_jornada(conn, jornada, grupo=7):
    partidos = parse_jornada(jornada, grupo)
    with conn.cursor() as cur:
        for p in partidos:
            local_id = upsert_equipo(cur, p["local"], grupo)
            visitante_id = upsert_equipo(cur, p["visitante"], grupo)
            cur.execute(
                """
                INSERT INTO dh.partidos (
                    cod_acta, temporada, grupo, jornada, fecha, estadio,
                    local_id, visitante_id, goles_local, goles_visitante, acta_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cod_acta) DO UPDATE SET
                    fecha           = EXCLUDED.fecha,
                    estadio         = EXCLUDED.estadio,
                    goles_local     = EXCLUDED.goles_local,
                    goles_visitante = EXCLUDED.goles_visitante,
                    actualizado_en  = now()
                """,
                (p["cod_acta"], TEMPORADA, grupo, p["jornada"], p["fecha"],
                 p["estadio"], local_id, visitante_id, p["goles_local"],
                 p["goles_visitante"], p["acta_url"]),
            )
    conn.commit()
    return len(partidos)


if __name__ == "__main__":
    conn = psycopg2.connect(os.environ["PG_DSN"])
    try:
        for j in (1, 2, 3):
            print(f"Jornada {j}: {cargar_jornada(conn, j)} partidos")
    finally:
        conn.close()