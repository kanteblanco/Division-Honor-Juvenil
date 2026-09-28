
import os
import time
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

from carga import procesar_acta

load_dotenv()

RAW = Path(__file__).resolve().parent.parent / "bronze"
HEADERS = {"User-Agent": "proyecto-portfolio-analitica/0.1 (scraper educativo)"}
PAUSA = 5
sesion = requests.Session()
sesion.headers.update(HEADERS)
sesion.headers["Referer"] = "https://rfef.es/"


def descargar(url):
    for intento in range(4):
        try:
            r = sesion.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            if "Titulares" not in r.text:
                raise ValueError(f"respuesta sin datos ({len(r.text)} bytes)")
            return r.text
        except (requests.RequestException, ValueError) as e:
            espera = 15 * (intento + 1)
            print(f"    {e}; reintento en {espera}s")
            time.sleep(espera)
    return None


def main():
    RAW.mkdir(exist_ok=True)
    conn = psycopg2.connect(os.environ["PG_DSN"])

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cod_acta, acta_url FROM dh.partidos
                WHERE NOT acta_parseada AND goles_local IS NOT NULL
                ORDER BY jornada, cod_acta
            """)
            pendientes = cur.fetchall()

        print(f"{len(pendientes)} actas pendientes\n")

        for cod_acta, url in pendientes:
            fichero = RAW / f"acta_{cod_acta}.html"

            if fichero.exists():
                html = fichero.read_text(encoding="utf-8")
                print(f"{cod_acta}  (desde disco)")
            else:
                html = descargar(url)
                if html is None:
                    print(f"{cod_acta}  FALLO, se omite")
                    continue
                fichero.write_text(html, encoding="utf-8")
                print(f"{cod_acta}  descargada")
                time.sleep(PAUSA)

            try:
                n_j, n_g, n_t, sin_cruzar = procesar_acta(conn, cod_acta, html)
                print(f"    {n_j} jugadores, {n_g} goles, {n_t} tarjetas")
                for tipo, nombre in sin_cruzar:
                    print(f"    sin cruzar ({tipo}): {nombre}")
            except Exception as e:
                conn.rollback()
                print(f"    ERROR al parsear: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()