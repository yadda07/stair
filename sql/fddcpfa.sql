-- Youcef ADDA | GEODECI
-- FR : Plugins QGIS, solutions SIG et administration PostgreSQL/PostGIS ; audit, automatisation et accompagnement.
-- EN : QGIS plugins, GIS solutions and PostgreSQL/PostGIS administration; audits, workflow automation and business support.
-- AR : تطوير إضافات QGIS وحلول نظم المعلومات الجغرافية وإدارة PostgreSQL/PostGIS، والتدقيق والأتمتة ومرافقة المؤسسات.
-- Site / Website / الموقع : https://geodeci.xyz/
-- Contact professionnel / Business enquiries / للتواصل المهني : youcef.geodesien@gmail.com | contact@geodeci.xyz
-- Détails / Details / التفاصيل : SIGNATURE.md
-- ============================================================================
-- FUNCTION: rip_avg_nge.fddcpfa(text, integer)

-- DROP FUNCTION IF EXISTS rip_avg_nge.fddcpfa(text, integer);

CREATE OR REPLACE FUNCTION rip_avg_nge.fddcpfa(
	zasro text,
	decoupe_mode integer)
    RETURNS TABLE(gid_dc bigint, gid integer, sro character varying, nro character varying, length double precision, cab_type character varying, cab_capa integer, cab_modulo bigint, isole character varying, date_modif timestamp with time zone, modif_par character varying, geom geometry, cab_nature character varying, commentaire character varying, collecte character varying, cb_etiquet character varying, fon character varying, projet character varying, "DCE" character varying, dist_type text, affectation character varying) 
    LANGUAGE 'plpgsql'
    COST 100
    VOLATILE PARALLEL UNSAFE
    ROWS 1000

AS $BODY$
BEGIN
    RETURN QUERY
    WITH initial_selection AS (
        SELECT 
            source.gid AS bpe_gid, 
            cables.gid AS cable_gid, 
            cables.geom AS cable_geom, 
            source.geom AS bpe_geom
        FROM rip_avg_nge.cables AS cables
        JOIN (
            SELECT  bpe.gid, bpe.geom FROM rip_avg_nge.bpe bpe where bpe.sro ILIKE '%' || zasro || '%'
            UNION
            SELECT att.gid, st_endpoint(att.geom) AS geom FROM rip_avg_nge.attaches att 
				where decoupe_mode = 1 and att.sro ILIKE '%' || zasro || '%'
        ) AS source ON ST_DWithin(cables.geom, source.geom, 0.2)
        WHERE cables.sro ILIKE '%' || zasro || '%'
    ),
    snapped_geometries AS (
        SELECT
            initial.cable_gid AS cable_gid,
            ST_Snap(initial.cable_geom, ST_Collect(initial.bpe_geom), 0.2) AS snapped_geom
        FROM initial_selection AS initial
        GROUP BY initial.cable_gid, initial.cable_geom
    ),
    split_geometries AS (
        SELECT 
            snapped.cable_gid AS cable_gid,
            (ST_Dump(ST_Split(snapped.snapped_geom, ST_Collect(initial.bpe_geom)))).geom AS split_geom
        FROM snapped_geometries AS snapped
        JOIN initial_selection AS initial ON snapped.cable_gid = initial.cable_gid
        GROUP BY snapped.cable_gid, snapped.snapped_geom
    ),
    unique_segments AS (
        SELECT DISTINCT ON (split.cable_gid, md5(ST_AsText(split.split_geom)))
            ROW_NUMBER() OVER (ORDER BY split.cable_gid) AS gid_dc,
            cables.gid, cables.sro, cables.nro, ST_Length(split.split_geom) AS length, cables.cab_type,
            cables.cab_capa, cables.cab_modulo, cables.isole, cables.date_modif,
            cables.modif_par, split.split_geom AS geom, cables.cab_nature, cables.commentaire,
            cables.collecte, cables.cb_etiquet, cables.fon, cables.projet, 
            cables."DCE", cables.dist_type, cables.affectation
        FROM split_geometries AS split
        JOIN rip_avg_nge.cables AS cables ON split.cable_gid = cables.gid
    )
    SELECT * FROM unique_segments;
END;
$BODY$;

ALTER FUNCTION rip_avg_nge.fddcpfa(text, integer)
    OWNER TO yadda;

GRANT EXECUTE ON FUNCTION rip_avg_nge.fddcpfa(text, integer) TO PUBLIC;

GRANT EXECUTE ON FUNCTION rip_avg_nge.fddcpfa(text, integer) TO auvergne_sch_etudes;

GRANT EXECUTE ON FUNCTION rip_avg_nge.fddcpfa(text, integer) TO ownergrp_auvergne WITH GRANT OPTION;

GRANT EXECUTE ON FUNCTION rip_avg_nge.fddcpfa(text, integer) TO sdupays;

GRANT EXECUTE ON FUNCTION rip_avg_nge.fddcpfa(text, integer) TO yadda;

