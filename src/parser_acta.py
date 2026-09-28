import re
import unicodedata

from bs4 import BeautifulSoup

RE_JUGADOR = re.compile(r"jugador=(\d+)")


def norm(texto):
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()

def parse_alineaciones(soup):
    jugadores = []
    orden = 0  # 0 y 1 -> local; 2 y 3 -> visitante

    for tabla in soup.find_all("table"):
        filas = tabla.find_all("tr", onclick=RE_JUGADOR)
        if not filas:
            continue

        etiqueta = tabla.find_previous(["strong", "b", "h4"])
        texto = etiqueta.get_text(strip=True).lower() if etiqueta else ""
        titular = texto.startswith("titular")

        local = orden < 2
        orden += 1

        for tr in filas:
            celdas = tr.find_all("td")
            jugadores.append({
                "cod_rfef": int(RE_JUGADOR.search(tr["onclick"]).group(1)),
                "dorsal": int(celdas[0].get_text(strip=True)),
                "nombre": celdas[-1].get_text(" ", strip=True),
                "titular": titular,
                "local": local,
            })

    return jugadores


RE_MINUTO = re.compile(r"(\d+)'")


def parse_sustituciones(soup):
    subs = []

    contenedores = [t for t in soup.find_all("table")
                    if t.find("h4") and "Sustituciones" in t.get_text()]
    if not contenedores:
        return subs

    for lado, celda in enumerate(contenedores[0].find_all("td", recursive=False)
                                 or contenedores[0].tr.find_all("td", recursive=False)):
        local = lado == 0
        for tabla in celda.find_all("table"):
            filas = tabla.find_all("tr")
            if len(filas) != 2:
                continue

            m = RE_MINUTO.search(filas[0].get_text())
            if not m:
                continue
            minuto = int(m.group(1))

            def datos(tr, saltar_minuto):
                celdas = tr.find_all("td")
                if saltar_minuto:
                    celdas = celdas[1:]
                return int(celdas[0].get_text(strip=True)), celdas[1].get_text(" ", strip=True)

            dorsal_entra, nombre_entra = datos(filas[0], True)
            dorsal_sale, nombre_sale = datos(filas[1], False)

            subs.append({
                "minuto": minuto,
                "local": local,
                "dorsal_entra": dorsal_entra,
                "nombre_entra": nombre_entra,
                "dorsal_sale": dorsal_sale,
                "nombre_sale": nombre_sale,
            })

    return subs

def parse_goles(soup):
    """Minuto, goleador y tipo. El marcador acumulado se ignora: está ofuscado."""
    goles = []

    for tabla in soup.find_all("table"):
        if tabla.find("table"):
            continue
        enlaces = tabla.find_all("a", class_="lgol")
        if not enlaces:
            continue

        for tr in tabla.find_all("tr"):
            enlace = tr.find("a", class_="lgol")
            span = tr.find("span", class_="font-blue")
            if not enlace or not span:
                continue

            m = RE_MINUTO.search(span.get_text())
            celda = span.find_parent("td")
            nombre = celda.get_text(" ", strip=True).replace(span.get_text(strip=True), "").strip()

            goles.append({
                "minuto": int(m.group(1)) if m else None,
                "nombre": nombre,
                "tipo": (enlace.get("title") or "").strip(),
            })
        break

    return goles


def parse_tarjetas(soup):
    tarjetas = []

    contenedor = next((t for t in soup.find_all("table")
                       if t.find("h4") and "Tarjetas" in t.get_text()), None)
    if not contenedor:
        return tarjetas

    for lado, celda in enumerate(contenedor.tr.find_all("td", recursive=False)):
        for tr in celda.find_all("tr"):
            span = tr.find("span", class_="font-blue")
            img = tr.find("img")
            if not span or not img:
                continue

            m = RE_MINUTO.search(span.get_text())
            td_nombre = span.find_parent("td")
            nombre = td_nombre.get_text(" ", strip=True).replace(span.get_text(strip=True), "").strip()

            tarjetas.append({
                "minuto": int(m.group(1)) if m else None,
                "nombre": nombre,
                "tipo": "amarilla" if "amar" in img.get("src", "") else "roja",
                "local": lado == 0,
            })

    return tarjetas

def calcular_minutos(jugadores, sustituciones, tarjetas=None):
    """Devuelve la lista de jugadores con los minutos jugados añadidos."""
    entradas = {}
    salidas = {}

    for s in sustituciones:
        entradas[(s["dorsal_entra"], s["local"])] = s["minuto"]
        salidas[(s["dorsal_sale"], s["local"])] = s["minuto"]

    expulsiones = {}
    if tarjetas:
        for t in tarjetas:
            if t["tipo"] == "roja" and t["minuto"] is not None:
                expulsiones[(norm(t["nombre"]), t["local"])] = t["minuto"]

    for j in jugadores:
        clave = (j["dorsal"], j["local"])
        entra = entradas.get(clave)
        sale = salidas.get(clave)

        if entra is not None and sale is not None:
            mins = sale - entra
        elif j["titular"] and sale is not None:
            mins = sale
        elif j["titular"]:
            mins = 90
        elif entra is not None:
            mins = 90 - entra
        else:
            mins = 0
        roja = expulsiones.get((norm(j["nombre"]), j["local"]))
        if roja is not None:
            inicio = entra if entra is not None else 0
            mins = min(mins, roja - inicio)
        j["minutos"] = mins

    return jugadores

if __name__ == "__main__":
    with open("acta.html", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    jugadores = parse_alineaciones(soup)
    print("Total:", len(jugadores))
    for j in jugadores:
        equipo = "LOCAL" if j["local"] else "VISIT"
        rol = "T" if j["titular"] else "S"
        print(f"  {equipo} {rol}  {j['dorsal']:>2}  {j['nombre']}")

    print("\n=== SUSTITUCIONES ===")
    for s in parse_sustituciones(soup):
        equipo = "LOCAL" if s["local"] else "VISIT"
        print(f"  {equipo} {s['minuto']:>3}'  ENTRA {s['dorsal_entra']:>2} {s['nombre_entra']}"
              f"  |  SALE {s['dorsal_sale']:>2} {s['nombre_sale']}")

    print("\n=== GOLES ===")
    for g in parse_goles(soup):
        print(f"  {g['minuto']:>3}'  {g['nombre']}  [{g['tipo']}]")

    print("\n=== TARJETAS ===")
    for t in parse_tarjetas(soup):
        equipo = "LOCAL" if t["local"] else "VISIT"
        print(f"  {equipo} {t['minuto']:>3}'  {t['nombre']}  [{t['tipo']}]")

    print("\n=== MINUTOS ===")
    jugadores = calcular_minutos(jugadores, parse_sustituciones(soup), parse_tarjetas(soup))

    for j in jugadores:
        equipo = "LOCAL" if j["local"] else "VISIT"
        print(f"  {equipo} {j['dorsal']:>2} {j['nombre'][:28]:<28} {j['minutos']:>3}'")  




