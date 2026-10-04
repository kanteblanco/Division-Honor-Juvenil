-- (=*10)
-- División de Honor Juvenil Grupo VII 2026/2027 - esquema base
-- PostgreSQL 17
-- Ejecutar: psql -U postgres -d futbol -f schema.sql
-- (=*10)

CREATE SCHEMA IF NOT EXISTS dh;
SET search_path TO dh, public;

-- (-*10)
-- Equipos
-- cod_novanet sale de la URL del escudo(.../novanet/50x50/206021_50x50.png)
-- y es estable entre temporadas: es la mejor clave natural que hay.
-- (-*10)

CREATE TABLE IF NOT EXISTS equipos (
	id		SERIAL PRIMARY KEY,
	cod_novanet		TEXT UNIQUE,
	nombre		TEXT NOT NULL,
	nombre_norm		TEXT NOT NULL,
	grupo		SMALLINT,
	creado_en		TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_equipos_norm ON equipos (nombre_norm);

-- (-*10)
-- PARTIDOS
-- cod_acta es la clave natural del sistema de la RFEF -> upsert idempotente
-- (-*10)

CREATE TABLE IF NOT EXISTS partidos (
    cod_acta         BIGINT PRIMARY KEY,
    temporada        TEXT     NOT NULL,
    grupo            SMALLINT NOT NULL,
    competicion      TEXT NOT NULL DEFAULT 'DH',
    jornada          SMALLINT NOT NULL,
    fecha            TIMESTAMP,
    estadio          TEXT,
    local_id         INT REFERENCES equipos(id),
    visitante_id     INT REFERENCES equipos(id),
    goles_local      SMALLINT,
    goles_visitante  SMALLINT,
    arbitro          TEXT,
    acta_url         TEXT,
    acta_descargada  BOOLEAN NOT NULL DEFAULT FALSE,
    acta_parseada    BOOLEAN NOT NULL DEFAULT FALSE,
    actualizado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);
 
CREATE INDEX IF NOT EXISTS ix_partidos_jornada   ON partidos (temporada, grupo, jornada);
CREATE INDEX IF NOT EXISTS ix_partidos_pendiente ON partidos (acta_parseada) WHERE NOT acta_parseada;
 
-- ---------------------------------------------------------------------
-- JUGADORES
-- El acta NO da ID de jugador ni nombre completo en la alineación
-- (solo dorsal + apellido). La clave natural es (equipo, temporada, dorsal).
-- nombre_completo se rellena cuando aparece en amonestaciones/expulsiones.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jugadores (
    id               SERIAL PRIMARY KEY,
	cod_federativo   INTEGER UNIQUE,
    equipo_id        INT  NOT NULL REFERENCES equipos(id),
    temporada        TEXT NOT NULL,
    competicion      TEXT NOT NULL DEFAULT 'DH',
    dorsal           SMALLINT NOT NULL,
    nombre_corto     TEXT NOT NULL,
    nombre_completo  TEXT,
    posicion         TEXT,
    posicion_detalle TEXT,
    posicion_origen  TEXT,
    anyo_nacimiento  SMALLINT,
    edad_origen      TEXT,
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (equipo_id, temporada, dorsal)
);
 
-- Alias: mismo jugador con distinto nombre corto o distinto dorsal.
-- Se rellena a mano / semi-automático. Es la tabla que resuelve identidades.
CREATE TABLE IF NOT EXISTS jugadores_alias (
    jugador_id   INT NOT NULL REFERENCES jugadores(id) ON DELETE CASCADE,
    alias        TEXT NOT NULL,
    origen       TEXT,
    PRIMARY KEY (jugador_id, alias)
);
 
-- ---------------------------------------------------------------------
-- PARTICIPACIONES  (una fila por jugador convocado y partido)
-- minutos se calcula al parsear el acta: titular/suplente + sustituciones
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS participaciones (
    cod_acta    BIGINT   NOT NULL REFERENCES partidos(cod_acta) ON DELETE CASCADE,
    jugador_id  INT      NOT NULL REFERENCES jugadores(id),
    equipo_id   INT      NOT NULL REFERENCES equipos(id),
    titular     BOOLEAN  NOT NULL,
    capitan     BOOLEAN  NOT NULL DEFAULT FALSE,
    portero     BOOLEAN  NOT NULL DEFAULT FALSE,
    min_entra   SMALLINT,
    min_sale    SMALLINT,
    minutos     SMALLINT,
    PRIMARY KEY (cod_acta, jugador_id)
);
 
CREATE INDEX IF NOT EXISTS ix_part_jugador ON participaciones (jugador_id);
 
-- ---------------------------------------------------------------------
-- EVENTOS
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS eventos (
    id          BIGSERIAL PRIMARY KEY,
    cod_acta    BIGINT NOT NULL REFERENCES partidos(cod_acta) ON DELETE CASCADE,
    jugador_id  INT REFERENCES jugadores(id),
    equipo_id   INT REFERENCES equipos(id),
    tipo        TEXT NOT NULL CHECK (tipo IN
                    ('gol','amarilla','roja','entra','sale')),
    minuto      SMALLINT,
    detalle     TEXT,
    UNIQUE (cod_acta, jugador_id, tipo, minuto)
);
 
CREATE INDEX IF NOT EXISTS ix_eventos_tipo ON eventos (tipo);
 
-- ---------------------------------------------------------------------
-- VISTA: agregado por jugador y temporada
-- Es la capa que luego consumes desde Power BI
-- ---------------------------------------------------------------------
CREATE VIEW v_jugador_temporada AS
WITH part AS (
    SELECT
        jugador_id,
        COUNT(*)                            AS convocado,
        COUNT(*) FILTER (WHERE minutos > 0) AS partidos_jugados,
        COUNT(*) FILTER (WHERE titular)     AS titularidades,
        COALESCE(SUM(minutos), 0)           AS minutos
    FROM participaciones
    GROUP BY jugador_id
),
ev AS (
    SELECT
        jugador_id,
        COUNT(*) FILTER (WHERE tipo = 'gol')      AS goles,
        COUNT(*) FILTER (WHERE tipo = 'amarilla') AS amarillas,
        COUNT(*) FILTER (WHERE tipo = 'roja')     AS rojas
    FROM eventos
    WHERE jugador_id IS NOT NULL
    GROUP BY jugador_id
)
SELECT
    j.id AS jugador_id,
    j.cod_federativo,
    j.nombre_corto,
    j.nombre_completo,
    j.equipo_id,
    e.nombre AS equipo,
    e.grupo,
    j.competicion,
    j.temporada,
    j.dorsal,
    j.posicion,
    j.posicion_detalle,
    j.anyo_nacimiento,
    COALESCE(part.convocado, 0)         AS convocado,
    COALESCE(part.partidos_jugados, 0)  AS partidos_jugados,
    COALESCE(part.titularidades, 0)     AS titularidades,
    COALESCE(part.minutos, 0)           AS minutos,
    COALESCE(ev.goles, 0)               AS goles,
    COALESCE(ev.amarillas, 0)           AS amarillas,
    COALESCE(ev.rojas, 0)               AS rojas,
    ROUND(COALESCE(ev.goles, 0)::numeric * 90
          / NULLIF(part.minutos, 0), 2) AS goles_90
FROM jugadores j
JOIN equipos e ON e.id = j.equipo_id
LEFT JOIN part ON part.jugador_id = j.id
LEFT JOIN ev   ON ev.jugador_id   = j.id;

