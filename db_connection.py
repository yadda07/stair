"""
Module de connexion PostgreSQL pour Police C6 v2.0
Connexion automatique via les credentials QGIS existants
"""

import time

try:
    import psycopg2
    from psycopg2 import sql as psycopg2_sql
    _HAS_PSYCOPG2 = True
except ImportError:
    psycopg2 = None
    psycopg2_sql = None
    _HAS_PSYCOPG2 = False
from dataclasses import dataclass
from typing import List, Optional, Tuple
from qgis.core import QgsSettings, QgsMessageLog, Qgis, QgsDataSourceUri
from .compat import MSG_INFO, MSG_WARNING, MSG_CRITICAL


# Configuration cible
TARGET_HOST = "10.241.228.107"
TARGET_DATABASE = "auvergne"  # Nom en minuscule pour comparaison


@dataclass
class CableSegment:
    """Segment de câble découpé par l'infrastructure"""
    gid_dc2: int          # ID segment unique
    gid_dc: int           # ID câble original
    gid: int              # GID de référence
    sro: str
    nro: str
    length: float
    cab_type: str
    cab_capa: int         # Capacité FO
    cab_modulo: int
    isole: str
    date_modif: str
    modif_par: str
    cab_nature: str
    commentaire: str
    collecte: str
    cb_etiquet: str
    fon: str
    projet: str
    dce: str
    dist_type: str
    affectation: str
    posemode: int         # 0=souterrain, 1=aérien, 2=façade
    geom_wkt: str         # Géométrie en WKT


class DatabaseConnection:
    """
    Gère la connexion à la base PostgreSQL RIP AVG NGE.
    Récupère automatiquement les credentials depuis QGIS.
    """
    
    _RETRY_DELAYS = (1.0, 2.0)
    _CIRCUIT_OPEN_SECONDS = 60

    def __init__(self):
        self.connection = None
        self.host = None
        self.port = None
        self.database = None
        self.user = None
        self.password = None
        self._connection_name = None
        self._circuit_open_until = 0.0
    
    def find_auvergne_connection(self) -> Optional[str]:
        """
        Cherche la connexion PostgreSQL "Auvergne" dans les settings QGIS.
        Retourne le nom de la connexion si trouvée.
        """
        settings = QgsSettings()
        
        # Lister toutes les connexions PostgreSQL
        settings.beginGroup("PostgreSQL/connections")
        connections = settings.childGroups()
        settings.endGroup()
        
        for conn_name in connections:
            settings.beginGroup(f"PostgreSQL/connections/{conn_name}")
            host = settings.value("host", "")
            database = settings.value("database", "")
            settings.endGroup()
            
            # Vérifier si c'est la bonne connexion
            if host == TARGET_HOST or database.lower() == TARGET_DATABASE:
                return conn_name
            
            # Chercher aussi par nom contenant "Auvergne"
            if "auvergne" in conn_name.lower():
                return conn_name
        
        return None
    
    def get_connection_params(self, connection_name: str) -> dict:
        """
        Récupère les paramètres de connexion depuis QGIS.
        """
        settings = QgsSettings()
        prefix = f"PostgreSQL/connections/{connection_name}"
        
        params = {
            'host': settings.value(f"{prefix}/host", TARGET_HOST),
            'port': int(settings.value(f"{prefix}/port", 5432)),
            'database': settings.value(f"{prefix}/database", ""),
            'user': settings.value(f"{prefix}/username", ""),
            'password': settings.value(f"{prefix}/password", ""),
        }
        
        # Si le mot de passe n'est pas stocké, essayer authcfg
        if not params['password']:
            authcfg = settings.value(f"{prefix}/authcfg", "")
            if authcfg:
                from qgis.core import QgsApplication
                auth_manager = QgsApplication.authManager()
                config = auth_manager.configAuthMethodConfig(authcfg)
                if config:
                    params['user'] = config.config('username', params['user'])
                    params['password'] = config.config('password', '')
        
        return params
    
    def is_connected(self) -> bool:
        """Verifie si la connexion est active (ping leger)."""
        if not self.connection:
            return False
        try:
            self.connection.isolation_level
            cur = self.connection.cursor()
            cur.execute('SELECT 1')
            cur.close()
            return True
        except Exception as e:
            QgsMessageLog.logMessage(
                f"is_connected check echoue: {e}", "PoleAerien", MSG_WARNING
            )
            self.connection = None
            return False

    def connect(self) -> bool:
        """
        Etablit la connexion a la base de donnees.
        Si deja connecte, retourne True sans ouvrir une nouvelle connexion.
        Retry avec backoff exponentiel (3 tentatives : immediate, 1s, 2s).
        Circuit breaker : apres 3 echecs consecutifs, skip pendant 60s.
        """
        if not _HAS_PSYCOPG2:
            QgsMessageLog.logMessage(
                "psycopg2 non installe : connexion PostgreSQL impossible. "
                "Installer via : pip install psycopg2-binary",
                "PoleAerien", MSG_CRITICAL
            )
            return False

        if self.is_connected():
            return True

        if time.time() < self._circuit_open_until:
            remaining = int(self._circuit_open_until - time.time())
            QgsMessageLog.logMessage(
                f"PG circuit ouvert - connexion desactivee pendant {remaining}s",
                "PoleAerien", MSG_WARNING
            )
            return False

        conn_name = self.find_auvergne_connection()
        if not conn_name:
            QgsMessageLog.logMessage(
                f"Connexion PostgreSQL 'Auvergne' non trouvee. "
                f"Veuillez configurer une connexion vers {TARGET_HOST}",
                "PoleAerien", MSG_WARNING
            )
            return False

        self._connection_name = conn_name
        params = self.get_connection_params(conn_name)
        self.host = params['host']
        self.port = params['port']
        self.database = params['database']
        self.user = params['user']
        self.password = params['password']

        delays = (0.0,) + self._RETRY_DELAYS
        for attempt, delay in enumerate(delays):
            if delay > 0:
                QgsMessageLog.logMessage(
                    f"PG tentative {attempt + 1}/{len(delays)} dans {delay:.0f}s...",
                    "PoleAerien", MSG_INFO
                )
                time.sleep(delay)
            try:
                self.connection = psycopg2.connect(
                    host=self.host,
                    port=self.port,
                    database=self.database,
                    user=self.user,
                    password=self.password
                )
                return True
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"PG echec tentative {attempt + 1}/{len(delays)}: {e}",
                    "PoleAerien", MSG_WARNING
                )

        self._circuit_open_until = time.time() + self._CIRCUIT_OPEN_SECONDS
        QgsMessageLog.logMessage(
            f"PG circuit ouvert pour {self._CIRCUIT_OPEN_SECONDS}s apres 3 echecs consecutifs",
            "PoleAerien", MSG_CRITICAL
        )
        return False
    
    def ensure_connected(self) -> bool:
        """Reconnecte si la connexion a ete perdue. Alias de connect()."""
        return self.connect()

    def disconnect(self):
        """Ferme la connexion."""
        if self.connection:
            try:
                self.connection.close()
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"disconnect echoue: {e}", "PoleAerien", MSG_WARNING
                )
            self.connection = None
    
    def execute_fddcpi2(self, sro: str) -> List[CableSegment]:
        """
        Exécute la fonction fddcpi2 et retourne les câbles découpés.
        
        Args:
            sro: Code SRO (ex: '63041/B1I/PMZ/00003')
        
        Returns:
            Liste de CableSegment
        """
        if not self.connection:
            if not self.connect():
                return []
        
        cursor = None
        try:
            cursor = self.connection.cursor()
            
            # Exécuter la fonction avec le SRO
            query = psycopg2_sql.SQL("SELECT *, ST_AsText(geom) as geom_wkt FROM rip_avg_nge.fddcpiax(%s)")
            cursor.execute(query, (sro,))
            
            rows = cursor.fetchall()
            segments = []
            
            for row in rows:
                segment = CableSegment(
                    gid_dc2=row[0] or 0,
                    gid_dc=row[1] or 0,
                    gid=row[2] or 0,
                    sro=row[3] or '',
                    nro=row[4] or '',
                    length=row[5] or 0.0,
                    cab_type=row[6] or '',
                    cab_capa=row[7] or 0,
                    cab_modulo=row[8] or 0,
                    isole=row[9] or '',
                    date_modif=str(row[10]) if row[10] else '',
                    modif_par=row[11] or '',
                    cab_nature=row[13] or '',
                    commentaire=row[14] or '',
                    collecte=row[15] or '',
                    cb_etiquet=row[16] or '',
                    fon=row[17] or '',
                    projet=row[18] or '',
                    dce=row[19] or '',
                    dist_type=row[20] or '',
                    affectation=row[21] or '',
                    posemode=row[22] or 0,
                    geom_wkt=row[23] or ''
                )
                segments.append(segment)
            
            QgsMessageLog.logMessage(
                f"fddcpiax({sro}): {len(segments)} segments de câbles récupérés",
                "PoleAerien", MSG_INFO
            )
            return segments
            
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Erreur exécution fddcpiax: {e}",
                "PoleAerien", MSG_WARNING
            )
            try:
                self.connection.rollback()
            except Exception:
                pass
            return []
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
    
    def fetch_sro_list(self) -> List[str]:
        """
        Recupere la liste des SRO depuis rip_avg_nge.za_sro.
        Utilisee pour l'autocompletion dans la saisie manuelle du SRO.

        Returns:
            Liste triee de codes SRO (ex: ['63041/B1I/PMZ/00003', ...])
        """
        if not self.connection:
            if not self.connect():
                return []

        cursor = None
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT DISTINCT sro FROM rip_avg_nge.za_sro "
                "WHERE sro IS NOT NULL ORDER BY sro"
            )
            rows = cursor.fetchall()
            return [row[0] for row in rows if row[0]]
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Erreur requete za_sro: {e}",
                "PoleAerien", MSG_WARNING
            )
            try:
                self.connection.rollback()
            except Exception:
                pass
            return []
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    def get_cables_aeriens(self, sro: str) -> List[CableSegment]:
        """
        Récupère les câbles aériens et façade (posemode in (1, 2)).
        """
        all_segments = self.execute_fddcpi2(sro)
        return [s for s in all_segments if s.posemode in (1, 2)]

    def query_bpe_by_sro(self, sro: str) -> List[dict]:
        """
        Récupère les BPE d'un SRO avec leur géométrie et type.
        
        Args:
            sro: Code SRO
        
        Returns:
            Liste de dicts {gid, noe_type, noe_usage, inf_num, geom_wkt}
        """
        if not self.connection:
            if not self.connect():
                return []
        
        cursor = None
        try:
            cursor = self.connection.cursor()
            query = psycopg2_sql.SQL(
                "SELECT gid, noe_type, noe_usage, inf_num, ST_AsText(geom) as geom_wkt "
                "FROM rip_avg_nge.bpe WHERE sro = %s"
            )
            cursor.execute(query, (sro,))
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                results.append({
                    'gid': row[0],
                    'noe_type': row[1] or '',
                    'noe_usage': row[2] or '',
                    'inf_num': row[3] or '',
                    'geom_wkt': row[4] or ''
                })
            
            QgsMessageLog.logMessage(
                f"BPE({sro}): {len(results)} boîtiers récupérés",
                "PoleAerien", MSG_INFO
            )
            return results
            
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Erreur requête BPE: {e}",
                "PoleAerien", MSG_WARNING
            )
            try:
                self.connection.rollback()
            except Exception:
                pass
            return []
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass


    def query_attaches_by_sro(self, sro: str) -> List[dict]:
        """
        Recupere les attaches d'un SRO avec leur geometrie.
        
        Les attaches sont des LineString courtes reliant un appui
        a un point de connexion (cable/BPE). Elles permettent de
        detecter les cables et BPE connectes indirectement a un appui.
        
        Args:
            sro: Code SRO
        
        Returns:
            Liste de dicts {gid, geom_wkt}
        """
        if not self.connection:
            if not self.connect():
                return []
        
        cursor = None
        try:
            cursor = self.connection.cursor()
            query = psycopg2_sql.SQL(
                "SELECT gid, ST_AsText(geom) as geom_wkt "
                "FROM rip_avg_nge.attaches WHERE sro = %s"
            )
            cursor.execute(query, (sro,))
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                if row[1]:
                    results.append({
                        'gid': row[0],
                        'geom_wkt': row[1]
                    })
            
            QgsMessageLog.logMessage(
                f"Attaches({sro}): {len(results)} attaches recuperees",
                "PoleAerien", MSG_INFO
            )
            return results
            
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Erreur requete attaches: {e}",
                "PoleAerien", MSG_WARNING
            )
            try:
                self.connection.rollback()
            except Exception:
                pass
            return []
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    def query_sro_be(self, sro: str) -> Optional[str]:
        """
        Determine le bureau d'etudes (NGE ou Axione) pour un SRO.
        
        Interroge la table comac.sro_nge_axione pour identifier
        l'ingenierie responsable du SRO.
        
        Args:
            sro: Code SRO (ex: '63041/B1I/PMZ/00003')
        
        Returns:
            'nge', 'axione', ou None si non trouve
        """
        if not self.connection:
            if not self.connect():
                return None
        
        cursor = None
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT lower(be) FROM comac.sro_nge_axione "
                "WHERE sro ILIKE %s LIMIT 1",
                (f'%{sro}%',)
            )
            row = cursor.fetchone()
            be = row[0] if row else None
            QgsMessageLog.logMessage(
                f"SRO BE({sro}): {be or 'non trouve'}",
                "PoleAerien", MSG_INFO
            )
            return be
            
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Erreur requete sro_nge_axione: {e}",
                "PoleAerien", MSG_WARNING
            )
            try:
                self.connection.rollback()
            except Exception:
                pass
            return None
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
#  Shared singleton instance
# ---------------------------------------------------------------------------

_shared_instance: Optional[DatabaseConnection] = None


def get_shared_connection() -> DatabaseConnection:
    """Retourne l'instance partagee de DatabaseConnection (singleton).

    Tous les modules doivent utiliser cette fonction au lieu de
    DatabaseConnection() pour eviter de multiplier les connexions
    PostgreSQL.
    """
    global _shared_instance
    if _shared_instance is None:
        _shared_instance = DatabaseConnection()
    return _shared_instance


def close_shared_connection() -> None:
    """Ferme et libere l'instance partagee. Appeler au unload du plugin."""
    global _shared_instance
    if _shared_instance is not None:
        _shared_instance.disconnect()
        _shared_instance = None


def extract_sro_from_layer(layer) -> Optional[str]:
    """
    Extrait le code SRO depuis une couche QGIS.
    Cherche dans les attributs 'sro', 'SRO', 'code_sro', etc.
    
    Args:
        layer: QgsVectorLayer
    
    Returns:
        Code SRO ou None
    """
    if not layer or not layer.isValid():
        return None
    
    # Noms de champs possibles pour le SRO
    sro_fields = ['sro', 'SRO', 'code_sro', 'CODE_SRO', 'pt_ad_sro', 'PT_AD_SRO']
    
    field_names = [f.name() for f in layer.fields()]
    
    for sro_field in sro_fields:
        if sro_field in field_names:
            from qgis.core import QgsFeatureRequest
            from .compat import FR_NO_GEOMETRY
            req = QgsFeatureRequest().setFilterExpression(
                f'"{sro_field}" IS NOT NULL AND "{sro_field}" != \'\''
            ).setLimit(1).setFlags(FR_NO_GEOMETRY)
            for feature in layer.getFeatures(req):
                value = feature[sro_field]
                if value and str(value).strip():
                    return str(value).strip()
    
    return None


def extract_sro_from_layer_uri(layer) -> Optional[str]:
    """
    Extrait le code SRO depuis l'URI de la couche PostgreSQL.
    """
    if not layer or not layer.isValid():
        return None
    
    uri = QgsDataSourceUri(layer.source())
    
    # Le SRO peut être dans le filtre SQL
    sql_filter = uri.sql()
    if sql_filter:
        # Chercher un pattern comme sro = '...'
        import re
        match = re.search(r"sro\s*=\s*'([^']+)'", sql_filter, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return None
