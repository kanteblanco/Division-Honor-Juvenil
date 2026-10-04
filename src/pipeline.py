"""
Pipeline semanal - División de Honor Juvenil Grupo 7

Un solo comando hace el ciclo completo:
  1. lee del calendario qué jornadas ya se han jugado
  2. baja el listado de cada una y actualiza partidos y equipos
  3. baja las actas que falten y las parsea

Es idempotente: se puede relanzar las veces que haga falta. Las jornadas ya
cargadas se actualizan en vez de duplicarse, y las actas que ya están en
bronze/ no se vuelven a pedir al servidor. Por eso recorre la temporada
entera cada vez: así recoge solos los partidos aplazados que se juegan
entre semana, sin tener que adivinar qué jornada toca.

Uso:
    python src/pipeline.py              # ciclo completo
    python src/pipeline.py --solo-actas # salta el listado
    python src/pipeline.py --desde 4    # ignora las jornadas anteriores
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import psycopg2
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from carga_acta import procesar_acta
from carga_listado import cargar_jornada
from parser_listado import COMPETITION, GRUPOS, HEADERS, URL

load_dotenv()

RAIZ = Path(__file__).resolve().parent.parent
RAW = RAIZ / "bronze"


PAUSA = 10
RE_JORNADA = re.compile(r"^(\d+)\s*\((\d{2})-(\d{2})-(\d{4})\)")

sesion = requests.Session()
sesion.headers.update(HEADERS)
sesion.headers["Referer"] = "https://rfef.es/"


# ------------------------------------------------------------- calendario

def jornadas_jugadas(grupo: int) -> list[int]:
    """Jornadas cuya fecha ya ha pasado, leídas del propio calendario."""
    r = sesion.get(URL, params={"competition": COMPETITION,
                        "group": GRUPOS[grupo]}, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    select = soup.find("select", attrs={"name": "journey"})
    if select is None:
        raise RuntimeError("No encuentro el selector de jornadas")

    hoy = date.today()
    jornadas = []
    for opcion in select.find_all("option"):
        m = RE_JORNADA.match(opcion.get_text(strip=True))
        if not m:
            continue
        numero = int(m.group(1))
        fecha = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
        if fecha <= hoy:
            jornadas.append(numero)

    return jornadas


# ------------------------------------------------------------------ actas

def descargar_acta(url: str) -> str | None:
    """Solo da por buena la respuesta si trae alineaciones de verdad."""
    for intento in range(4):
        try:
            r = sesion.get(url, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            if "Titulares" not in r.text:
                raise ValueError(f"respuesta vacía ({len(r.text)} bytes)")
            return r.text
        except (requests.RequestException, ValueError) as e:
            espera = 15 * (intento + 1)
            print(f"    {e}; reintento en {espera}s", file=sys.stderr)
            time.sleep(espera)
    return None


def procesar_actas(conn) -> None:
    RAW.mkdir(exist_ok=True)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cod_acta, acta_url FROM dh.partidos
            WHERE NOT acta_parseada
              AND goles_local IS NOT NULL
              AND acta_url IS NOT NULL
            ORDER BY jornada, cod_acta
            """
        )
        pendientes = cur.fetchall()

    if not pendientes:
        print("No hay actas pendientes")
        return

    print(f"\n{len(pendientes)} actas pendientes")
    for cod_acta, url in pendientes:
        fichero = RAW / f"acta_{cod_acta}.html"

        if fichero.exists():
            html = fichero.read_text(encoding="utf-8")
            origen = "disco"
        else:
            html = descargar_acta(url)
            if html is None:
                print(f"  {cod_acta}  FALLO, se omite")
                continue
            fichero.write_text(html, encoding="utf-8")
            origen = "descargada"
            time.sleep(PAUSA)

        try:
            n_j, n_g, n_t, sin_cruzar = procesar_acta(conn, cod_acta, html)
            print(f"  {cod_acta}  ({origen})  {n_j} jugadores, "
                  f"{n_g} goles, {n_t} tarjetas")
            for tipo, nombre in sin_cruzar:
                print(f"      sin cruzar ({tipo}): {nombre}")
        except Exception as e:
            conn.rollback()
            print(f"  {cod_acta}  ERROR al parsear: {e}", file=sys.stderr)


# ---------------------------------------------------------------- resumen

def resumen(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE acta_parseada),
                   MAX(jornada) FILTER (WHERE acta_parseada)
            FROM dh.partidos
            """
        )
        total, parseados, ultima = cur.fetchone()

        cur.execute("SELECT COUNT(*) FROM dh.jugadores")
        jugadores = cur.fetchone()[0]

    print(f"\n{parseados}/{total} partidos parseados | "
          f"última jornada completa: {ultima} | {jugadores} jugadores")


# ------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-actas", action="store_true")
    ap.add_argument("--grupo", type=int, default=7, choices=range(1, 8))
    ap.add_argument("--desde", type=int, default=1)
    args = ap.parse_args()

    inicio = time.time()
    conn = psycopg2.connect(os.environ["PG_DSN"])

    try:
        if not args.solo_actas:
            jornadas = [j for j in jornadas_jugadas(args.grupo) if j >= args.desde]
            print(f"Grupo {args.grupo} | jornadas jugadas: {jornadas}\n")

            for j in jornadas:
                n = cargar_jornada(conn, j, args.grupo)
                print(f"  J{j:02d}  {n} partidos")
                time.sleep(PAUSA)

        procesar_actas(conn)
        resumen(conn)
    finally:
        conn.close()

    print(f"Terminado en {time.time() - inicio:.0f}s")


if __name__ == "__main__":
    main()