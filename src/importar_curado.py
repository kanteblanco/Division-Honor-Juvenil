"""
Importa el CSV curado a mano -> dh.jugadores

Actualiza posicion, posicion_detalle y anyo_nacimiento cruzando por cod_rfef,
que es el identificador estable de la RFEF.

Procedencia de cada dato:
  posicion_origen = 'manual'  si la fila lleva marca en revisado_pos, o si
                              la posicion no venia de ninguna fuente previa
                              (es decir, la puso el usuario)
                    se conserva el origen anterior en el resto
  edad_origen     = 'manual'  siempre que haya anyo_nacimiento: ese dato
                              solo puede venir de curacion manual

Uso:
    python src/importar_curado.py fichero.csv --simular   # no escribe nada
    python src/importar_curado.py fichero.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

LINEAS_VALIDAS = {"POR", "DEF", "MED", "DEL"}
VACIOS = {"", "null", "none", "-"}


def limpio(valor: str | None) -> str | None:
    """Convierte celdas vacias o con la palabra NULL en None de verdad."""
    if valor is None:
        return None
    v = valor.strip()
    return None if v.lower() in VACIOS else v


def leer(ruta: Path) -> list[dict]:
    # utf-8-sig se come la marca BOM que Excel pone al guardar en UTF-8
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))

    obligatorias = {"cod_rfef", "posicion", "posicion_detalle",
                    "anyo_nacimiento"}
    faltan = obligatorias - set(filas[0].keys())
    if faltan:
        raise SystemExit(f"Al CSV le faltan columnas: {sorted(faltan)}")

    return filas


def validar(filas: list[dict]) -> list[str]:
    errores = []
    vistos = set()

    for i, fila in enumerate(filas, start=2):
        cod = limpio(fila["cod_rfef"])
        if not cod or not cod.isdigit():
            errores.append(f"linea {i}: cod_rfef invalido ({fila['cod_rfef']!r})")
            continue
        if cod in vistos:
            errores.append(f"linea {i}: cod_rfef {cod} repetido")
        vistos.add(cod)

        pos = limpio(fila["posicion"])
        if pos and pos not in LINEAS_VALIDAS:
            errores.append(f"linea {i}: posicion {pos!r} no es POR/DEF/MED/DEL")

        anyo = limpio(fila["anyo_nacimiento"])
        if anyo:
            if not anyo.isdigit() or not (2000 <= int(anyo) <= 2015):
                errores.append(f"linea {i}: anyo {anyo!r} fuera de rango")

    return errores


def importar(conn, filas: list[dict], simular: bool) -> None:
    pos_ok = anyo_ok = sin_cambio = no_encontrado = 0

    with conn.cursor() as cur:
        for fila in filas:
            cod = int(limpio(fila["cod_rfef"]))
            pos = limpio(fila["posicion"])
            detalle = limpio(fila["posicion_detalle"])
            anyo = limpio(fila["anyo_nacimiento"])
            revisado = bool(limpio(fila.get("revisado_pos")))

            cur.execute(
                "SELECT id, nombre_completo FROM dh.jugadores WHERE cod_federativo = %s",
                (cod,),
            )
            encontrado = cur.fetchone()
            if not encontrado:
                no_encontrado += 1
                print(f"  ! no esta en la base: {cod} "
                      f"({fila.get('nombre_completo', '')})")
                continue

            jid = encontrado[0]
            if not pos and not anyo:
                sin_cambio += 1
                continue

            if pos:
                if not simular:
                    cur.execute(
                        """
                        UPDATE dh.jugadores
                        SET posicion = %s,
                            posicion_detalle = %s,
                            posicion_origen = CASE
                                WHEN %s THEN 'manual'
                                ELSE COALESCE(posicion_origen, 'manual')
                            END
                        WHERE id = %s
                        """,
                        (pos, detalle, revisado, jid),
                    )
                pos_ok += 1

            if anyo:
                if not simular:
                    cur.execute(
                        """
                        UPDATE dh.jugadores
                        SET anyo_nacimiento = %s, edad_origen = 'manual'
                        WHERE id = %s
                        """,
                        (int(anyo), jid),
                    )
                anyo_ok += 1

    if simular:
        conn.rollback()
        print("\n--- SIMULACION: no se ha escrito nada ---")
    else:
        conn.commit()

    print(f"\n{pos_ok} posiciones, {anyo_ok} anyos de nacimiento")
    print(f"{sin_cambio} filas sin dato, {no_encontrado} sin correspondencia")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", type=Path)
    ap.add_argument("--simular", action="store_true",
                    help="valida y cuenta, pero no escribe en la base")
    args = ap.parse_args()

    filas = leer(args.csv)
    print(f"{len(filas)} filas leidas")

    errores = validar(filas)
    if errores:
        print("\nERRORES, no se importa nada:")
        for e in errores[:20]:
            print(" ", e)
        if len(errores) > 20:
            print(f"  ... y {len(errores) - 20} mas")
        sys.exit(1)

    conn = psycopg2.connect(os.environ["PG_DSN"])
    try:
        importar(conn, filas, args.simular)
    finally:
        conn.close()


if __name__ == "__main__":
    main()