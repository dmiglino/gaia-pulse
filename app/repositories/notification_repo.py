from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    def __init__(self, db: Session) -> None:
        super().__init__(Notification, db)

    def _visible_to(self, user_id: int, household_id: int) -> ColumnElement[bool]:
        """Lo que esta persona puede ver: lo suyo, más lo que es del hogar entero.

        Es la misma condición que `NotificationService._get_owned` aplica fila por
        fila, y hasta acá las cuatro consultas de lectura no la aplicaban: usaban un
        `or_(user_id == yo, household_id == mío)` a secas. Como un aviso dirigido a una
        persona lleva **también** `household_id`, la segunda mitad daba verdadero para
        el aviso del otro: el globito del nav se lo contaba en cada carga de página,
        `/notifications` se lo listaba con el cuerpo entero — que desde la 4.2 dice
        cuántos días lleva sin pesarse o sin entrenar — y su "marcar todo como leído" se
        lo escribía. Y después `_get_owned` le rechazaba el clic para descartarlo, así
        que ni podía sacárselo de encima. Es la regla 4 de `AGENTS.md`: la consulta
        filtra por el `user_id` que actúa incluso entre convivientes.
        """
        return or_(
            Notification.user_id == user_id,
            and_(
                Notification.user_id.is_(None),
                Notification.household_id == household_id,
            ),
        )

    def get_for_user(
        self,
        user_id: int,
        household_id: int,
        include_dismissed: bool = False,
        limit: int = 50,
        category: str | None = None,
    ) -> list[Notification]:
        stmt = (
            select(Notification)
            .where(self._visible_to(user_id, household_id))
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        if not include_dismissed:
            stmt = stmt.where(Notification.dismissed_at.is_(None))
        if category:
            stmt = stmt.where(Notification.category == category)
        return list(self.db.scalars(stmt).all())

    def get_category_counts(self, user_id: int, household_id: int) -> dict[str, int]:
        """Cuántas notificaciones vivas hay por categoría.

        La pantalla dibujaba seis pastillas de filtro fijas en la plantilla, y solo
        tres categorías las escribe algún job: las otras tres eran filtros que nunca
        podían dar un resultado. Con esto las pastillas son las categorías que la
        persona realmente tiene, con su cuenta al lado, en una sola consulta agrupada.
        """
        stmt = (
            select(Notification.category, func.count())
            .where(
                self._visible_to(user_id, household_id),
                Notification.dismissed_at.is_(None),
            )
            .group_by(Notification.category)
        )
        return {category: count for category, count in self.db.execute(stmt).all()}

    def get_unread_count(self, user_id: int, household_id: int) -> int:
        """El número del globito del nav.

        Corre en **cada** carga de página — `get_template_context()` lo llama para
        todas las plantillas —, y hasta acá traía todas las filas sin leer a Python
        para hacerles `len()`: una tabla que crece sin techo, materializada entera
        para contar. Ahora cuenta la base.
        """
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                self._visible_to(user_id, household_id),
                Notification.read_at.is_(None),
                Notification.dismissed_at.is_(None),
            )
        )
        return self.db.scalar(stmt) or 0

    def mark_all_read(self, user_id: int, household_id: int) -> int:
        stmt = select(Notification).where(
            self._visible_to(user_id, household_id),
            Notification.read_at.is_(None),
            Notification.dismissed_at.is_(None),
        )
        notifications = list(self.db.scalars(stmt).all())
        #: Aware: `read_at` es `timestamptz` y un naive acá lo interpreta Postgres
        #: en la timezone de la sesión, así que "marcar todo como leído" grababa
        #: una hora corrida y "leído hace un rato" se leía con el offset de más.
        now = datetime.now(UTC)
        for n in notifications:
            n.read_at = now
        self.db.flush()
        return len(notifications)

    def _addressee_scope(
        self, household_id: int, user_id: int | None
    ) -> tuple[ColumnElement[bool], ...]:
        """A quién está dirigida la fila: a una persona, o al hogar entero.

        Los dos alcances son independientes y **no** se combinan con `or_`. Un aviso
        per-user lleva también `household_id`, así que el `or_` que había hacía que
        la fila de Diego contara como la de Rocío y le tapara el aviso durante toda
        la ventana. Es la regla 4 de `AGENTS.md`: la consulta filtra por el `user_id`
        que actúa incluso entre convivientes. Para leer, la condición es otra — lo
        propio **más** lo del hogar —: eso es `_visible_to`.

        En la rama per-user *household_id* no se usa a propósito: `user_id` ya es el
        filtro más estricto, y como la columna `household_id` es nullable, agregarla
        acá haría que una fila per-user sin hogar dejara de deduplicarse en silencio.
        Sigue en la firma porque el llamador lo tiene a mano y la rama del hogar lo
        necesita.
        """
        if user_id is not None:
            return (Notification.user_id == user_id,)
        return (
            Notification.household_id == household_id,
            Notification.user_id.is_(None),
        )

    def has_recent_for_subject(
        self,
        category: str,
        entity_type: str,
        entity_id: int,
        *,
        household_id: int,
        user_id: int | None = None,
        days: int,
        severity: int,
    ) -> bool:
        """¿Ya se avisó de *este* sujeto, sin que la cosa haya empeorado?

        Reemplaza al cooldown por categoría, que era el único control que existía y
        no alcanzaba por dos motivos distintos. Filtraba por categoría, no por
        sujeto: la leche baja desde hace tres semanas volvía a producir un aviso
        cada vez que el job la miraba, con el cuerpo recalculado y sin memoria de
        que ya se había avisado. Y su ventana — 6 h para `low_stock` — quedó más
        corta que el período de su propio job cuando la 4.1 lo pasó a cron diario,
        así que además ya no suprimía nada.

        El sujeto es la terna `(category, related_entity_type, related_entity_id)`
        dentro del alcance: "de esta cosa, en este sentido". La categoría entra en la
        clave porque el sujeto de la inactividad y el del recordatorio de pesaje son
        los dos la misma persona.

        *severity* es la escalada, y `priority` es su marca de agua: se avisó con
        prioridad 7 y hoy el estado da 8 → se vuelve a hablar, porque empeoró; da lo
        mismo o menos → silencio hasta que se venza *days*. Usar `priority` para eso
        no es un préstamo forzado: una despensa más vacía o una inactividad más larga
        **son** más urgentes, que es lo que la columna significa, y es lo único
        comparable que hay en la tabla sin agregarle una columna.

        La marca de agua es la **más alta** de la ventana, no la del último aviso. Que no
        se quede alta después de que el sujeto se recupera es lo que resuelve
        `retire_subject`: la leche que se repuso pierde su fila, así que cuando vuelva a
        acabarse el miércoles no hay nada adentro de la ventana que la calle. Sin ese
        retiro —como estaba hasta la 4.3— la fila del lunes con prioridad 9 la dejaba
        muda hasta que venciera la ventana entera.

        No mira `dismissed_at`: descartar es "lo vi", y todo el punto de esto es no
        volver a decir lo mismo mañana a la mañana.
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.category == category,
                Notification.related_entity_type == entity_type,
                Notification.related_entity_id == entity_id,
                Notification.created_at >= cutoff,
                Notification.priority >= severity,
                *self._addressee_scope(household_id, user_id),
            )
        )
        return bool(self.db.scalar(stmt))

    def _retire(
        self,
        category: str,
        household_id: int,
        user_id: int | None,
        *subject: ColumnElement[bool],
    ) -> int:
        """Borra los avisos de *category* cuyo sujeto cumple *subject*, y dice cuántos.

        Borrar y no marcar: lo que hace falta es que el aviso desaparezca de la lista
        **y** que la marca de agua de `priority` se vaya con él, y `has_recent_for_subject`
        no mira `dismissed_at` a propósito — descartar es "lo vi", no "ya no pasa". Sin
        una columna nueva para "esto se resolvió" —y la única migración de la v3 es la de
        la 4.4, sobre `suggestions`— la fila que se queda es la que sigue sosteniendo el
        watermark. Un recordatorio resuelto tampoco es historia que valga guardar: la
        poda ya tira todo a los 90 días.

        Se borra sin mirar si estaba leído o descartado, y eso es deliberado: si solo se
        retiraran los abiertos, descartar el aviso de la leche y después reponerla
        dejaría el watermark en 9, y la próxima vez que se acabe la leche la app se
        quedaría callada hasta que venza la ventana.

        `source_type == "job"` acota el borrado a las filas que escribieron estos jobs:
        el sujeto `("user", 3)` podría llegar a nombrarlo también algo que no sea un
        aviso de ausencia, y esto no tiene por qué barrerlo.
        """
        result = cast(
            "CursorResult[Any]",
            self.db.execute(
                delete(Notification).where(
                    Notification.category == category,
                    Notification.source_type == "job",
                    *subject,
                    *self._addressee_scope(household_id, user_id),
                ),
                execution_options={"synchronize_session": False},
            ),
        )
        self.db.flush()
        return result.rowcount

    def retire_subject(
        self,
        category: str,
        entity_type: str,
        entity_id: int,
        *,
        household_id: int,
        user_id: int | None = None,
    ) -> int:
        """Retira el aviso de este sujeto porque el sujeto salió del conjunto.

        El que volvió a entrenar, el que se pesó. Es la mitad que le faltaba al ciclo
        de vida por sujeto de la 4.2: ahí se resolvió no repetir el aviso mientras la
        cosa no empeore, pero nada lo retiraba cuando la cosa se **arreglaba**, así que
        "no anotaste tu peso en 4 días" seguía en la lista después del pesaje y el
        watermark de `priority` esperaba hasta el fin de la ventana de 7 días.
        """
        return self._retire(
            category,
            household_id,
            user_id,
            Notification.related_entity_type == entity_type,
            Notification.related_entity_id == entity_id,
        )

    def retire_subjects_other_than(
        self,
        category: str,
        entity_type: str,
        keep_ids: Sequence[int],
        *,
        household_id: int,
    ) -> int:
        """Ídem, cuando el conjunto se conoce entero: *keep_ids* es lo que sigue vigente.

        Es la forma de la despensa: la lista de faltantes de hoy **es** la verdad, así
        que todo aviso de `low_stock` que nombre un ítem que no está en ella es de algo
        que se repuso. Un solo `DELETE` en lugar de preguntar ítem por ítem si tiene un
        aviso abierto; con la lista vacía —despensa entera repuesta— se retiran todos.

        Los avisos sin sujeto quedan afuera (`related_entity_id IS NOT NULL`): son las
        filas de la v1, que no grababan `related_entity_*`, y sin id no hay forma de
        saber de qué ítem hablaban. Se van con la poda, no con esto.

        Sin `user_id`, a diferencia de `retire_subject`: el único conjunto que se conoce
        entero es la despensa, y la despensa es del hogar. Un `user_id` acá sería un
        parámetro sin llamador (regla 6); el día que exista un conjunto completo por
        persona, `_retire` ya sabe recibirlo.
        """
        subject: list[ColumnElement[bool]] = [
            Notification.related_entity_type == entity_type,
            Notification.related_entity_id.is_not(None),
        ]
        if keep_ids:
            subject.append(Notification.related_entity_id.notin_(keep_ids))
        return self._retire(category, household_id, None, *subject)

    def prune_older_than(self, days: int) -> int:
        """Borra las notificaciones más viejas que *days* y devuelve cuántas.

        Nada las borraba nunca: sin expiración ni poda la tabla crece sin techo, y
        con ella el trabajo de las dos consultas que corren en cada carga de página
        — el conteo del globito y las pastillas por categoría. Se van las leídas y
        las no leídas por igual: un aviso de hace tres meses que nadie abrió no es
        información pendiente, es ruido acumulado.
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        #: `synchronize_session=False`: el default hace que el ORM evalúe el `WHERE`
        #: **en Python** contra cada `Notification` que la sesión tenga cargada, y ahí
        #: `created_at` puede venir naive — es el `server_default` del motor — contra un
        #: `cutoff` aware, que en Python no se pueden comparar aunque en SQL sí. El job
        #: abre su sesión, poda y la cierra: no hay objetos vivos que sincronizar.
        #: `cast` porque `Session.execute` está tipado como `Result`, que no promete
        #: `rowcount`; un `DELETE` siempre devuelve el `CursorResult` que sí lo tiene.
        result = cast(
            "CursorResult[Any]",
            self.db.execute(
                delete(Notification).where(Notification.created_at < cutoff),
                execution_options={"synchronize_session": False},
            ),
        )
        self.db.flush()
        #: `rowcount` en vez de un `COUNT(*)` previo: el número que se quiere informar es
        #: exactamente el que el `DELETE` acaba de devolver, y contar antes era escribir
        #: el mismo predicado dos veces y pagar dos viajes.
        return result.rowcount
