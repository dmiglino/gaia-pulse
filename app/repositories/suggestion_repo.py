from collections.abc import Collection
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.signal import BehaviorSignal
from app.models.suggestion import RecommendationPreference, Suggestion
from app.repositories.base import BaseRepository


def _visible_to(user_id: int, household_id: int) -> ColumnElement[bool]:
    """Las sugerencias que *esta* persona puede ver.

    El filtro era ``or_(scope_user_id == user_id, household_id == household_id)``, y
    `engine._make_user_suggestion` escribe **las dos** columnas en una sugerencia
    personal (`household_id=user.household_id`, `scope_user_id=user.id`). O sea que la
    cláusula de hogar matcheaba las sugerencias personales del otro integrante: Diego
    abría `/suggestions/` y leía las de Rocío, con su análisis de sangre y sus hábitos
    adentro del texto.

    Una sugerencia es de todo el hogar cuando **no** nombra a nadie
    (`scope_user_id IS NULL`, que es lo que escribe `_make_household_suggestion`). Con
    eso la regla 4 de `AGENTS.md` vuelve a cumplirse: todo dato personal se filtra por
    el usuario que pregunta, incluso entre dos personas de la misma casa.
    """
    return or_(
        Suggestion.scope_user_id == user_id,
        and_(
            Suggestion.household_id == household_id,
            Suggestion.scope_user_id.is_(None),
        ),
    )


def _still_suppressed() -> ColumnElement[bool]:
    """La condición de "este sujeto no se vuelve a ofrecer todavía".

    Dos casos, y el segundo es la 4.4.7: una sugerencia **pendiente** ocupa el lugar de su
    sujeto —no tiene sentido ofrecer dos veces lo mismo sin haber recibido respuesta—, y una
    respondida con "no ahora" lo ocupa hasta que se vence su `snoozed_until`.

    Sin la segunda mitad, posponer *destrababa* el sujeto: la fila dejaba de estar `pending`,
    así que la corrida siguiente del job —7:40 o 18:40 locales, ver `scheduler._SCHEDULE`—
    volvía a escribir la misma tarjeta, con la única diferencia de los −0.045 que le baja la
    señal de descarte. El botón "Ahora no" prometía silencio y entregaba una repetición.

    Vivía en `engine._still_suppressed`, que era el motor escribiendo una cláusula `WHERE`
    contra un modelo: la regla 2 de `AGENTS.md` pide que eso viva acá.
    """
    return or_(
        Suggestion.status == "pending",
        Suggestion.snoozed_until > datetime.now(UTC),
    )


class SuggestionRepository(BaseRepository[Suggestion]):
    def __init__(self, db: Session) -> None:
        super().__init__(Suggestion, db)

    def get_pending_for_user(self, user_id: int, household_id: int) -> list[Suggestion]:
        stmt = (
            select(Suggestion)
            .where(
                Suggestion.status == "pending",
                _visible_to(user_id, household_id),
            )
            .order_by(Suggestion.priority.desc(), Suggestion.created_at.desc())
            .limit(20)
        )
        return list(self.db.scalars(stmt).all())

    def get_owned(self, suggestion_id: int, user_id: int, household_id: int) -> Suggestion | None:
        """Una sugerencia, solo si es de quien pregunta.

        `respond_to_suggestion` hacía `self.repo.get(suggestion_id)` y nada más: con
        cualquier sesión válida se podía aceptar, descartar o rechazar **cualquier**
        fila de la tabla, incluida la de otro hogar. Y peor que cambiarle el estado a
        alguien ajeno: la señal de comportamiento se grababa contra quien apretaba el
        botón, así que el título de una sugerencia de otra persona entraba a su modelo
        de aprendizaje.

        Devuelve `None` tanto si no existe como si no le corresponde — a propósito: la
        respuesta no tiene que dejar distinguir un id inexistente de uno ajeno.

        Y solo si sigue pendiente. Sin eso, dos clics rápidos en "Buena idea" entraban
        los dos al servicio y grababan **dos** `BehaviorSignal` sobre la misma entidad,
        con lo que un doble clic pesaba el doble en el scorer. El primer swap reemplaza
        la tarjeta, pero la segunda request ya salió. Como responder algo ya respondido
        es justo lo que "esa sugerencia ya no está disponible" describe, el filtro va
        acá y la ruta no necesita una rama nueva.
        """
        stmt = select(Suggestion).where(
            Suggestion.id == suggestion_id,
            Suggestion.status == "pending",
            _visible_to(user_id, household_id),
        )
        return self.db.scalar(stmt)

    def get_stale_pending(
        self,
        user_id: int,
        *,
        subject_types: Collection[str],
        created_before: datetime,
        created_after: datetime,
    ) -> list[Suggestion]:
        """Las sugerencias personales de *user_id* que siguen pendientes y ya tienen edad.

        Es lo que lee el barrido de ausencias: una tarjeta que nombra un sujeto, que nadie
        respondió, y que ya pasó su semana de gracia. `subject_types` lo pone quien llama
        —`learning.ABSENCE_SUBJECT_TYPES`— porque el vocabulario del aprendizaje vive en
        `app/recommendations/`, y este módulo no lo puede importar sin cerrar el círculo
        (`learning` importa `BehaviorSignalRepository`).

        **Solo personales**, y no `_visible_to`: la regla 4 de `AGENTS.md` pide que toda
        señal se grabe contra quien la generó, y una sugerencia del hogar
        (`scope_user_id IS NULL`) no tiene un dueño de quien afirmar que no la usó. Que la
        vieron los dos no dice cuál de los dos no comió eso.

        La ventana tiene **dos** bordes, y el de arriba es el que importa: `created_after`
        acota la elegibilidad al ancho de una corrida, así que cada tarjeta cae en el
        barrido de un solo día y de ninguno más. Sin ese borde la tarjeta seguía elegible
        para siempre —nada la saca de `pending`—, y como la memoria de "esto ya lo conté"
        eran las señales mismas, borrar lo aprendido desde el panel lo devolvía a la mañana
        siguiente: el barrido no encontraba la señal, así que la volvía a escribir sin que
        hubiera pasado nada nuevo. Un dato que alguien pidió borrar no puede volver solo.

        El precio es explícito: un día entero de scheduler caído pierde para siempre la
        ausencia de las tarjetas que cumplían edad ese día. Es la falla barata —una
        observación débil menos contra un borrado que no se respeta—, y además la que se
        nota: si el proceso no corrió, no corrió nada.

        Ordenadas por fecha para que el log se lea en el orden en que las tarjetas
        envejecieron. No aporta determinismo: cada tarjeta se decide sola, contra datos que
        el barrido calcula antes del loop, y lo que devuelve es un conteo.
        """
        stmt = (
            select(Suggestion)
            .where(
                Suggestion.status == "pending",
                Suggestion.scope_user_id == user_id,
                Suggestion.subject_type.in_(sorted(subject_types)),
                Suggestion.created_at <= created_before,
                Suggestion.created_at > created_after,
            )
            .order_by(Suggestion.created_at)
        )
        return list(self.db.scalars(stmt).all())

    #: Acá había un `get_recent_suggestions` sin ningún llamador, con la misma fuga que
    #: `get_pending_for_user`: el motor tenía su propia copia de esa pregunta en
    #: `engine._get_recent_suggestions_for_user`. Dos implementaciones de la misma
    #: consulta, una sin usar, es exactamente cómo se filtró la primera vez, así que se
    #: borró en lugar de arreglarse por duplicado. La que quedó es `get_created_since`,
    #: acá abajo: la del motor, con el filtro correcto y un solo dueño.

    def get_created_since(self, user_id: int, since: datetime) -> list[Suggestion]:
        """Las sugerencias **personales** de esta persona creadas desde `since`.

        Es lo que el scorer usa para la penalización de diversidad: qué se le ofreció hace
        poco, para no ofrecer lo mismo arriba de nuevo.

        `scope_user_id` y no `_visible_to`: una tarjeta del hogar no se le ofreció a nadie en
        particular, y contarla como oferta reciente de las dos personas haría que la compra
        que aceptó uno le baje el score al otro.
        """
        stmt = (
            select(Suggestion)
            .where(
                Suggestion.scope_user_id == user_id,
                Suggestion.created_at >= since,
            )
            .order_by(Suggestion.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_suppressed_subjects_for_user(self, user_id: int) -> list[tuple[str, str]]:
        """Los pares (tipo, nombre) de sujeto que esta persona no puede recibir ahora.

        Devuelve los pares crudos y no las claves normalizadas de `learning.subject_key`
        porque el vocabulario del aprendizaje vive en `app/recommendations/` y este módulo no
        lo puede importar sin cerrar el círculo — el mismo motivo que documenta
        `get_stale_pending`. Normalizar es de quien llama; filtrar, de acá.
        """
        stmt = select(Suggestion.subject_type, Suggestion.subject_name).where(
            Suggestion.scope_user_id == user_id,
            _still_suppressed(),
            Suggestion.subject_type.isnot(None),
            Suggestion.subject_name.isnot(None),
        )
        return [(t, n) for t, n in self.db.execute(stmt).all()]

    def get_suppressed_subjects_for_household(self, household_id: int) -> list[tuple[str, str]]:
        """Lo mismo para las sugerencias de scope household.

        Filtra por `scope_type` además de por hogar: las sugerencias personales de Diego y
        Rocío también llevan `household_id`, y sin esa condición una tarjeta de despensa
        aceptada por uno bloqueaba la del otro.
        """
        stmt = select(Suggestion.subject_type, Suggestion.subject_name).where(
            Suggestion.household_id == household_id,
            Suggestion.scope_type == "household",
            _still_suppressed(),
            Suggestion.subject_type.isnot(None),
            Suggestion.subject_name.isnot(None),
        )
        return [(t, n) for t, n in self.db.execute(stmt).all()]

    def get_user_preferences(self, user_id: int) -> list[RecommendationPreference]:
        stmt = select(RecommendationPreference).where(RecommendationPreference.user_id == user_id)
        return list(self.db.scalars(stmt).all())

    def upsert_preference(
        self,
        user_id: int,
        item_type: str,
        item_name: str,
        signal: str,
        strength: float = 1.0,
        notes: str | None = None,
    ) -> RecommendationPreference:
        stmt = select(RecommendationPreference).where(
            RecommendationPreference.user_id == user_id,
            RecommendationPreference.item_type == item_type,
            RecommendationPreference.item_name == item_name.lower(),
        )
        pref = self.db.scalar(stmt)
        if pref is None:
            pref = RecommendationPreference(
                user_id=user_id,
                item_type=item_type,
                item_name=item_name.lower(),
                preference_signal=signal,
                strength=strength,
                notes=notes,
            )
            self.db.add(pref)
        else:
            pref.preference_signal = signal
            pref.strength = strength
            if notes:
                pref.notes = notes
            pref.updated_at = datetime.now(UTC)
        self.db.flush()
        return pref


class BehaviorSignalRepository(BaseRepository[BehaviorSignal]):
    def __init__(self, db: Session) -> None:
        super().__init__(BehaviorSignal, db)

    def get_user_signals(
        self,
        user_id: int,
        signal_type: str | None = None,
        entity_type: str | None = None,
        limit: int | None = 200,
        since: datetime | None = None,
    ) -> list[BehaviorSignal]:
        """Las señales de *user_id*, siempre y solo de *user_id* (regla 4 de `AGENTS.md`).

        *since* y `limit=None` existen para el panel de la 4.4.8, que necesita **todo** lo
        que el motor lee y no las 200 filas más nuevas: una lista recortada le mostraría a
        la persona menos de lo que le está moviendo las sugerencias, y sin decírselo. El
        horizonte lo pone quien llama, con la misma constante que usa el motor
        (`learning.SIGNAL_HORIZON_DAYS`), así que la consulta queda acotada por fecha en vez
        de por cantidad — que es como el motor ya consultaba esta tabla.

        *entity_type* se compara **normalizado** y no por igualdad cruda, que es como estaba:
        `record` baja a minúsculas el `entity_name` pero no el `entity_type`, así que un
        `WHERE entity_type = 'food'` no alcanzaba una fila vieja guardada como `'Food'`
        —mientras que `learning.subject_key`, que es quien decide de qué sujeto es una fila,
        sí la alcanza—. Con las dos mitades normalizadas igual, acotar la consulta por tipo
        no puede dejar afuera una fila que el código de arriba sí considera del sujeto.
        """
        stmt = (
            select(BehaviorSignal)
            .where(BehaviorSignal.user_id == user_id)
            .order_by(BehaviorSignal.created_at.desc())
        )
        if since is not None:
            stmt = stmt.where(BehaviorSignal.created_at >= since)
        if signal_type:
            stmt = stmt.where(BehaviorSignal.signal_type == signal_type)
        if entity_type:
            stmt = stmt.where(
                func.lower(func.trim(BehaviorSignal.entity_type)) == entity_type.strip().lower()
            )
        # El tope va último: con el `LIMIT` puesto antes que los `WHERE` el resultado sería
        # el mismo —SQLAlchemy compone la sentencia, no la ejecuta por partes— pero se leería
        # como si recortara antes de filtrar, que es lo contrario de lo que hace.
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def delete_ids(self, user_id: int, signal_ids: list[int]) -> int:
        """Borrar esas filas, y solo si son de *user_id*. Devuelve cuántas borró.

        Recibe ids y no un nombre de sujeto porque el nombre no se puede comparar en SQL:
        `record` guarda `entity_name.lower()` con los acentos que traía, y los sujetos se
        comparan **normalizados** (`learning.normalize_subject`, sin tildes). Un `WHERE
        entity_name = 'brocoli'` no encuentra la fila que dice "brócoli", así que olvidar
        el brócoli no borraba nada y la pantalla decía que sí. Quien decide qué filas son
        del sujeto es el servicio, que puede normalizar; acá queda la parte que la base
        tiene que garantizar, que es el `user_id` — el filtro va en el `DELETE` aunque el
        servicio ya haya filtrado al leer, porque es la única capa donde un id de otra
        persona no puede colarse por un bug de arriba.
        """
        if not signal_ids:
            return 0
        deleted = (
            self.db.query(BehaviorSignal)
            .filter(
                BehaviorSignal.user_id == user_id,
                BehaviorSignal.id.in_(signal_ids),
            )
            .delete(synchronize_session=False)
        )
        self.db.flush()
        return int(deleted)

    def record(
        self,
        user_id: int,
        signal_type: str,
        entity_type: str,
        entity_name: str,
        value: float = 1.0,
        source_type: str = "implicit",
        entity_id: int | None = None,
        source_entity_type: str | None = None,
        source_entity_id: int | None = None,
        context: dict | None = None,
    ) -> BehaviorSignal:
        signal = BehaviorSignal(
            user_id=user_id,
            signal_type=signal_type,
            entity_type=entity_type,
            entity_name=entity_name.lower(),
            entity_id=entity_id,
            value=value,
            source_type=source_type,
            source_entity_type=source_entity_type,
            source_entity_id=source_entity_id,
            context_json=context,
        )
        self.db.add(signal)
        self.db.flush()
        return signal
