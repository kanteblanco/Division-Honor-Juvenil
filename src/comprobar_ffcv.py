import requests, json

r = requests.get(
    "https://ffcv.es/competiciones/api/filtros/jornadas_fetch.php",
    params={"cod_grupo": 905431879},
    headers={"User-Agent": "Mozilla/5.0"}, timeout=30)

d = r.json()
print("claves del nivel 1:", list(d.keys()))

for k, v in d.items():
    if isinstance(v, list) and v:
        print(f"\n{k}: {len(v)} elementos")
        print("primer elemento:", json.dumps(v[0], ensure_ascii=False))
        break

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0"})
s.get("https://ffcv.es/competiciones/", timeout=30)

for cod in (9787105, 1374769):
    r = s.get("https://ffcv.es/competiciones/api/jugadores/jugador_api.php",
              params={"codigo": cod, "cod_temporada": 22}, timeout=30)
    print(cod, "->", r.text[:400])
    print()