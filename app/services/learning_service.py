"""Lo que la app aprendió, en la forma en que se le muestra a la persona — y se corrige.

Este servicio existe por la 4.4.8, y el motivo es una sola frase: un motor que aprende sin
que se pueda ver qué aprendió no es inteligente, es opaco. Las cuatro rutas de aprendizaje
(puntual, atributo, franja horaria y saciedad) mueven el orden de las sugerencias desde
`behavior_signals`, una tabla que hasta acá no tenía **ninguna** lectura de usuario: si el
motor deducía mal, lo único que la casa podía hacer era recibir sugerencias raras.

Dos decisiones que le dan forma a todo el archivo:

- **Se lee por el mismo camino que el motor.** El horizonte es `SIGNAL_HORIZON_DAYS` y la
  agregación es `learning.learned_subjects`, que envuelve el mismo `subject_affinities` que
  usa el scorer. Un panel con su propia aritmética sería una segunda implementación del
  aprendizaje, y la que se ve y la que decide se irían separando sin que nadie se enterara.
- **Olvidar borra filas, no escribe una excepción.** No hay columna para "olvidado" —la
  `0003` es la única migración de la v3 y ya está gastada— y tampoco haría falta: lo
  aprendido *es* el conjunto de señales, así que olvidar es sacarlas. La consecuencia hay
  que decirla en la pantalla y no esconderla: si la conducta se repite, se vuelve a
  aprender. Olvidar no es un veto; el veto son las restricciones y las actividades
  imposibles del perfil, que `apply_hard_constraints` lee sin descuento.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.signal import BehaviorSignal
from app.recommendations import learning
from app.recommendations.learning import LearnedSubject, SubjectAffinity
from app.repositories.suggestion_repo import BehaviorSignalRepository

#: El orden en que se muestran los grupos, y por qué ese: primero lo que la casa toca todos
#: los días (comida), después lo que decide una vez por semana (actividad), y al final lo
#: que se mira de a meses (músculos, marcadores, hábitos). Es una tupla y no
#: `learning.SUBJECT_TYPES` porque ese es un conjunto —sin orden— y una tabla que se
#: reordena entre dos visitas parece que estuviera aprendiendo cuando no pasó nada.
GROUP_ORDER: tuple[str, ...] = ("food", "exercise", "muscle_group", "biomarker", "habit")


@dataclass(frozen=True)
class LearnedGroup:
    """Los sujetos de un tipo, ya ordenados por cuánto están moviendo las sugerencias."""

    subject_type: str
    rows: list[LearnedSubject]


@dataclass(frozen=True)
class LearnedCategory:
    """Lo aprendido a nivel **categoría**, que no es la suma de lo puntual sino su
    generalización: es lo que hace que rechazar brócoli, coliflor y kale diga algo sobre la
    espinaca (4.4.4).

    No lleva control de olvido, y eso es una propiedad y no un pendiente: una categoría no
    tiene señales propias —`ATTRIBUTE_SUBJECT_TYPES` está fuera de `SUBJECT_TYPES` justamente
    para que `record_signal` las rechace—, se deriva del catálogo en cada lectura. Se olvida
    olvidando los alimentos que la sostienen, que es lo que la pantalla dice.
    """

    name: str
    affinity: SubjectAffinity
    #: Cuántos sujetos puntuales distintos la sostienen. Es la cifra que hace legible una
    #: generalización: "bien con las verduras, por 4 alimentos" se puede discutir; un
    #: 0.62 no.
    subjects: int


@dataclass(frozen=True)
class Forgotten:
    """Lo que pasó al olvidar: cuántas filas se fueron y con qué nombre estaban guardadas."""

    deleted: int
    #: Vacío cuando no había nada que borrar, que es cuando el mensaje no nombra nada.
    subject_name: str


@dataclass(frozen=True)
class LearnedProfile:
    """Todo el panel, ya listo para la plantilla y sin nada que ella tenga que calcular."""

    groups: list[LearnedGroup]
    categories: list[LearnedCategory]
    horizon_days: int

    @property
    def total(self) -> int:
        return sum(len(group.rows) for group in self.groups)

    @property
    def is_empty(self) -> bool:
        return self.total == 0


class LearningService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.signals = BehaviorSignalRepository(db)

    def learned_profile(self, user_id: int) -> LearnedProfile:
        """Lo aprendido sobre *user_id*, agrupado para mostrar.

        `user_id` y nada más: no hay variante de household ni la va a haber. Es la regla 4
        de `AGENTS.md` y además es el punto de la casa —Diego y Rocío no tienen los mismos
        gustos—, así que un panel que promediara los dos mostraría una persona que no
        existe.
        """
        signals = self._recent_signals(user_id)
        rows = learning.learned_subjects(signals)

        by_type: dict[str, list[LearnedSubject]] = {}
        for row in rows:
            by_type.setdefault(row.subject_type, []).append(row)
        #: `GROUP_ORDER` cubre `SUBJECT_TYPES` entero y `learned_subjects` no devuelve nada
        #: de afuera, así que acá no hace falta una rama para los tipos que falten. Lo que
        #: sí hace falta es que eso no se rompa en silencio cuando `SUBJECT_TYPES` crezca —un
        #: sujeto que el motor aprende y el panel no muestra es la opacidad que esto viene a
        #: arreglar—, y eso lo sostiene un test y no un `sorted()` que nadie ejecuta.
        groups = [
            LearnedGroup(subject_type=t, rows=by_type[t]) for t in GROUP_ORDER if t in by_type
        ]

        return LearnedProfile(
            groups=groups,
            categories=self._categories(signals),
            horizon_days=learning.SIGNAL_HORIZON_DAYS,
        )

    def forget(self, user_id: int, subject_type: str, subject_name: str) -> Forgotten:
        """Olvidar todo lo aprendido sobre un sujeto. Devuelve cuántas señales se borraron.

        Se resuelve por clave normalizada y no por igualdad de texto porque las dos mitades
        del nombre tienen origen distinto: la fila guarda lo que se escribió en minúsculas
        —"brócoli", con tilde— y el panel muestra la forma comparable —"brocoli"—. Un
        `DELETE ... WHERE entity_name = ?` con lo que la pantalla mandó borraba cero filas y
        contestaba que todo bien, que es la peor de las dos formas de fallar.

        Devuelve además el nombre **como estaba guardado**, para que el mensaje diga lo que
        se borró y no lo que se pidió: cualquier texto cuya forma normalizada coincida llega
        hasta acá, así que un `PÓLLO!!!` escrito a mano borraba las filas de "pollo" y la
        pantalla contestaba "Olvidado: PÓLLO!!!". En una acción que no se puede deshacer, la
        confirmación tiene que nombrar lo que efectivamente se fue.

        El horizonte **no** se aplica acá, a propósito: una señal de hace dos años ya no
        pesa nada y no se muestra, pero sigue siendo un dato personal sobre esa persona. Si
        pide olvidar el brócoli, se van todas las del brócoli, no las que el motor todavía
        estaba leyendo.
        """
        target = learning.subject_key(subject_type, subject_name)
        if not target[1]:
            return Forgotten(deleted=0, subject_name="")
        #: Acotado por `entity_type` —normalizado en SQL, igual que lo normaliza
        #: `subject_key`— para no traer y normalizar en Python la tabla entera de la
        #: persona en cada clic: `behavior_signals` no tiene poda y crece con cada comida,
        #: entrenamiento y compra registrada. El nombre no se puede comparar en SQL (las
        #: tildes), el tipo sí.
        candidates = [
            signal
            for signal in self.signals.get_user_signals(user_id, entity_type=target[0], limit=None)
            if learning.subject_key(signal.entity_type, signal.entity_name) == target
        ]
        #: El más reciente primero, que es el orden en que vienen: el mismo criterio con el
        #: que el panel eligió qué nombre mostrar.
        name = candidates[0].entity_name if candidates else ""
        deleted = self.signals.delete_ids(user_id, [signal.id for signal in candidates])
        self.db.commit()
        return Forgotten(deleted=deleted, subject_name=name)

    def _recent_signals(self, user_id: int) -> list[BehaviorSignal]:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=learning.SIGNAL_HORIZON_DAYS)
        return self.signals.get_user_signals(user_id, limit=None, since=cutoff)

    def _categories(self, signals: list[BehaviorSignal]) -> list[LearnedCategory]:
        index = learning.attribute_index(self.db)
        affinities = learning.attribute_affinities(signals, index)
        #: Cuántos sujetos puntuales distintos sostienen cada categoría. Se cuenta sobre el
        #: índice y las señales, no sobre `affinities`, porque la agregación ya sumó y
        #: perdió de vista de cuántas cosas distintas venía la suma — que es justo lo que
        #: hace creíble o no una generalización.
        #: Y se cuentan las mismas señales que `attribute_affinities` agregó —solo las que
        #: son una opinión—, porque si no un `ignored_suggestion` (posponer, que no dice nada
        #: del sujeto) sumaría un alimento al respaldo de una categoría sin haber movido su
        #: dirección: "4 alimentos" al lado de una conclusión sacada de 3.
        opinions = learning.POSITIVE_SIGNAL_TYPES | learning.NEGATIVE_SIGNAL_TYPES
        backing: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for signal in signals:
            if signal.signal_type not in opinions:
                continue
            key = learning.subject_key(signal.entity_type, signal.entity_name)
            attribute = index.get(key)
            if attribute is not None:
                backing.setdefault(attribute, set()).add(key)
        rows = [
            LearnedCategory(
                name=name,
                affinity=affinity,
                subjects=len(backing.get((attribute_type, name), ())),
            )
            for (attribute_type, name), affinity in affinities.items()
        ]
        rows.sort(key=lambda row: (-abs(row.affinity.strength), row.name))
        return rows
