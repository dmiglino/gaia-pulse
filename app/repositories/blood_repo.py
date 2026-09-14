from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.blood_analysis import BloodAnalysis
from app.repositories.base import BaseRepository


class BloodAnalysisRepository(BaseRepository[BloodAnalysis]):
    """Los análisis de sangre como filas.

    Existe porque `BloodAnalysisService` era el último lugar de `app/services/` con
    consultas inline —las tres que `AGENTS.md` cuenta en la regla de capas— y porque
    la 4.5 necesita **la fila** del último panel, no solo su `values_json`: la
    antigüedad del panel vive en `analysis_date`, y un servicio que devuelve el blob
    y descarta la fecha es exactamente por qué un panel de hace tres años podía
    dictar consejos de hoy.

    Dato personal de salud: **todos** los métodos toman `user_id` y filtran por él
    (regla 4 de `AGENTS.md`). No hay lectura por hogar acá y no debería haberla: un
    panel de sangre es de una persona, no de la casa.
    """

    def __init__(self, db: Session) -> None:
        super().__init__(BloodAnalysis, db)

    def get_for_user(self, user_id: int) -> list[BloodAnalysis]:
        """Todos los paneles de una persona, del más nuevo al más viejo.

        El orden es `analysis_date` y después `created_at`, y el segundo criterio no
        es decorativo: `analysis_date` es opcional (un archivo que el parser no pudo
        fechar la deja en `NULL`), así que sin el desempate por carga el orden entre
        paneles sin fecha lo decide el motor.
        """
        stmt = (
            select(BloodAnalysis)
            .where(BloodAnalysis.user_id == user_id)
            .order_by(BloodAnalysis.analysis_date.desc(), BloodAnalysis.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_owned(self, analysis_id: int, user_id: int) -> BloodAnalysis | None:
        """Un panel por id, **acotado a su dueño**.

        El `user_id` en el `WHERE` y no un chequeo después de traer la fila: con el
        filtro en la consulta, un id de otra persona es indistinguible de un id que
        no existe, que es lo que tiene que ver quien lo pide.
        """
        stmt = select(BloodAnalysis).where(
            and_(BloodAnalysis.id == analysis_id, BloodAnalysis.user_id == user_id)
        )
        return self.db.scalar(stmt)

    def get_latest_analyzed(self, user_id: int) -> BloodAnalysis | None:
        """El panel más reciente **que tiene marcadores**, o `None`.

        Descartar el `status == "error"` no alcanza. Un panel en error existe como fila
        —se guarda para que la persona vea que la subida falló— y esa parte la resuelve
        el `WHERE`; el caso que se escapa es el otro: `analyze_file` devuelve
        `values: dict` con `default_factory=dict`, así que un archivo que el parser
        recorrió sin encontrar nada se guarda como `analyzed` sin ninguna fila en
        `blood_markers`. Sin este filtro, una subida ilegible de hoy tapaba el panel
        bueno del mes pasado — exactamente lo que este método existe para evitar.

        No es un scan: el orden lo pone la base y los paneles de una persona se cuentan
        por unidades, así que en la práctica se lee la primera fila.

        Devuelve la fila entera. Quien solo quiera los marcadores puede pedirle
        `markers`, pero la fecha tiene que poder llegar al motor.
        """
        stmt = (
            select(BloodAnalysis)
            .where(
                and_(
                    BloodAnalysis.user_id == user_id,
                    BloodAnalysis.status == "analyzed",
                )
            )
            .order_by(BloodAnalysis.analysis_date.desc(), BloodAnalysis.created_at.desc())
        )
        return next((row for row in self.db.scalars(stmt) if row.markers), None)
