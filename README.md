# División de Honor Juvenil — Grupo 7

Base de datos de estadísticas de jugadores construida a partir de las actas
arbitrales oficiales de la Real Federación Española de Fútbol.

Temporada 2026-2027. Grupo 7: dieciséis clubes de la Comunidad Valenciana,
Murcia y Baleares.

| Comunidad Valenciana | Murcia | Baleares |
|---|---|---|
| Valencia CF | Real Murcia CF | RCD Mallorca |
| Villarreal C.F. | FC Cartagena | Penya Arrabal |
| Levante U.D. | UCAM Universidad Católica de Murcia CF | |
| Elche CF | Murcia Promises Club de Fútbol | |
| CD Castellón | | |
| Torrent C.F. | | |
| Kelme C.F. | | |
| C.D. Roda | | |
| Patacona C.F. | | |
| C.D. San Francisco | | |

## Por qué existe

No hay estadísticas públicas de fútbol juvenil español más allá de goles y
partidos jugados. Ningún proveedor de datos de eventos (pases, disparos,
duelos) cubre estas categorías, y las webs de resultados se limitan a
clasificaciones y goleadores.

Las actas arbitrales, en cambio, son públicas y contienen alineaciones con
dorsal, sustituciones con minuto exacto, goles y tarjetas. A partir de ahí se
pueden derivar métricas que no publica nadie: minutos reales por jugador,
goles por 90 minutos, ratio de titularidad, rotación por equipo y reparto de
minutos por año de nacimiento.

Este proyecto convierte esas actas en una base de datos relacional consultable.

## Qué contiene

Estado actual: jornadas 1 a 3, 24 partidos, 343 jugadores.

| Tabla | Contenido |
|---|---|
| `equipos` | Clubes del grupo, con su código interno federativo |
| `partidos` | Calendario y resultados, una fila por acta |
| `jugadores` | Ficha por jugador, identificado por su código RFEF |
| `participaciones` | Una fila por jugador convocado y partido, con minutos calculados |
| `eventos` | Goles, amarillas y rojas, con minuto |
| `v_jugador_temporada` | Vista agregada: minutos, goles, goles/90, titularidades |

## Arquitectura

```
rfef.es/es/resultados          →  listado de partidos por jornada
        ↓                          (equipos, marcador, fecha, enlace al acta)
resultados.rfef.es             →  acta completa de cada partido
        ↓
bronze/                        →  HTML crudo guardado en disco
        ↓
parser_acta.py                 →  alineaciones, sustituciones, goles, tarjetas
        ↓
carga_acta.py                  →  upsert idempotente en PostgreSQL
        ↓
v_jugador_temporada            →  capa de consumo (Power BI, consultas)
```

La capa `bronze/` guarda el HTML tal como llega. Cambiar la lógica de parseo y
reprocesar las 24 actas no cuesta ni una petición al servidor. Toda la carga
usa `ON CONFLICT DO UPDATE`, así que el pipeline es idempotente: se puede
relanzar sin duplicar datos.

## Instalación

Requiere Python 3.13 y PostgreSQL 17.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Crear la base y cargar el esquema:

```bash
createdb -U postgres futbol
psql -U postgres -d futbol -f sql/schema_division_honor.sql
```

Copiar `.env.example` a `.env` y rellenar la cadena de conexión.

## Uso

Actualización semanal, un solo comando:

```bash
python src/pipeline.py
```

Lee del calendario qué jornadas se han jugado, actualiza el listado, y
descarga y parsea las actas que falten. Solo pide al servidor lo que no tiene
ya en `bronze/`, con cinco segundos de pausa entre peticiones.

Enriquecimiento de posiciones y curación manual:

```bash
python src/posiciones.py --descargar
python src/posiciones.py --cargar
python src/importar_curado.py fichero.csv --simular
```

## Notas sobre los datos

Cosas que conviene saber antes de usar estas cifras.

**Minutos.** El acta no recoge el tiempo de descuento, así que 90 minutos es
una convención para quien juega el partido completo. Una expulsión recorta los
minutos al momento de la roja.

**Marcador en la tabla de goles.** La web ofusca el marcador acumulado con CSS
generado y JavaScript, de modo que el texto plano devuelve cifras falsas. El
parser ignora ese dato: el resultado real se toma del listado de partidos, y
de la tabla de goles solo se leen minuto, goleador y tipo.

**Tarjetas a técnicos.** Las actas sancionan también a entrenadores y personal
del cuerpo técnico. Esas tarjetas no cruzan contra ninguna alineación y quedan
registradas como no asignadas en lugar de descartarse en silencio.

**Identificadores.** Cada jugador se identifica por su código RFEF, estable
entre partidos y temporadas. Los equipos, por el código que aparece en la URL
de su escudo, más fiable que el nombre porque la grafía varía entre temporadas.

**Posición y año de nacimiento.** El acta no los recoge. La posición procede de
lapreferente.com, una fuente colaborativa de cobertura irregular, cruzada por
conjuntos de palabras del nombre; el año de nacimiento es curación manual. Las
columnas `posicion_origen` y `edad_origen` registran la procedencia de cada
dato, y los huecos se dejan vacíos en lugar de rellenarse con valores
inventados.

**Validación.** Los agregados se han contrastado contra las fichas oficiales de
estadísticas de jugador de la RFEF, que publican convocatorias, titularidades y
goles por temporada. Los números coinciden.

## Próximos pasos

- **Cuadro de mando en Power BI** sobre la vista agregada: reparto de minutos
  por año de nacimiento, rotación por equipo, eficacia goleadora.
- **Convocatorias de selecciones** autonómicas, territoriales y nacionales.
  Añadiría una señal de valoración externa al dataset y permitiría preguntar
  si el rendimiento medible predice la convocatoria.
- **Temporadas anteriores.** El selector de la RFEF llega hasta 1990 y el
  código de jugador es estable, así que cargar 2025-26 permitiría seguir
  trayectorias y detectar cambios de club.
- **Resto de grupos de División de Honor.** Técnicamente es cambiar un
  parámetro; el coste real está en la curación manual, que se multiplicaría
  por siete.
- **Motivos de amonestación.** El acta recoge el motivo literal de cada tarjeta
  según el reglamento. Clasificarlos daría una dimensión disciplinaria que no
  publica ninguna otra fuente.

## Aviso sobre datos personales

La competición es de categoría juvenil y los jugadores son menores de edad. La
base guarda únicamente datos deportivos. Las fotografías que vienen incrustadas
en las actas no se almacenan, y ni la base de datos ni los ficheros de curación
manual se publican en este repositorio.