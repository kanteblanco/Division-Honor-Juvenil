import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

URL = "https://rfef.es/es/resultados"
HEADERS = {"User-Agent": "proyecto-portfolio-analitica/0.1 (scraper educativo)"}
COMPETITION = 33836116

GRUPOS = {
    1: 33836117, 2: 33836118, 3: 33836119, 4: 33836120,
    5: 33836121, 6: 33836122, 7: 33836123,
}

RE_CODACTA = re.compile(r"CodActa=(\d+)")
RE_ESCUDO = re.compile(r"/novanet/\d+x\d+/(\d+)_")
RE_FECHA = re.compile(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}:\d{2})")


def parse_jornada(jornada, grupo=7):
    params = {"competition": COMPETITION,
              "group": GRUPOS[grupo],
              "journey": jornada}
    r = requests.get(URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    partidos = []
    for a in soup.find_all("a", href=RE_CODACTA):
        bloque = a
        for _ in range(4):
            bloque = bloque.parent

        escudos = bloque.find_all("img", src=RE_ESCUDO)
        if len(escudos) != 2:
            continue

        equipos = [
            {
                "cod_novanet": RE_ESCUDO.search(img["src"]).group(1),
                "nombre": img["alt"].strip(),
            }
            for img in escudos
        ]

        texto = bloque.get_text(" ", strip=True)

        fecha = None
        if (m := RE_FECHA.search(texto)):
            fecha = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d/%m/%Y %H:%M")
            estadio = texto[: m.start()].strip()
        else:
            estadio = None

        marcador = bloque.find("div", class_="text-lg")
        goles_l = goles_v = None
        if marcador and "-" in marcador.get_text():
            izq, der = marcador.get_text(strip=True).split("-")
            goles_l, goles_v = int(izq.strip()), int(der.strip())

        partidos.append({
            "cod_acta": int(RE_CODACTA.search(a["href"]).group(1)),
            "jornada": jornada,
            "fecha": fecha,
            "estadio": estadio,
            "local": equipos[0],
            "visitante": equipos[1],
            "goles_local": goles_l,
            "goles_visitante": goles_v,
            "acta_url": a["href"],
        })

    return partidos


if __name__ == "__main__":
    for p in parse_jornada(3):
        print(f"{p['cod_acta']}  {p['fecha']}  "
              f"{p['local']['nombre']} {p['goles_local']}-{p['goles_visitante']} "
              f"{p['visitante']['nombre']}  [{p['estadio']}]")