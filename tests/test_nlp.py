"""Tests for the NLP rule-based parser (Layer 1)."""
import pytest

from app.nlp import rules as nlp_rules
from app.nlp.intents import ParseResult


class _ParserProxy:
    """Thin proxy so tests call .parse() as a method without caring about module structure."""

    def parse(self, text: str, speaking_user: str = "diego") -> ParseResult:
        return nlp_rules.parse(text, speaking_user=speaking_user)


@pytest.fixture
def parser() -> _ParserProxy:
    return _ParserProxy()


class TestStockAdd:
    def test_simple_purchase(self, parser: _ParserProxy) -> None:
        result = parser.parse("We bought 6 bananas and 4 bell peppers", speaking_user="diego")
        assert result.overall_confidence > 0.5
        assert len(result.intents) >= 1
        stock_intent = next(
            (i for i in result.intents if i.intent_type == "add_stock"), None
        )
        assert stock_intent is not None
        assert len(stock_intent.items) == 2
        names = [i.food_name.lower() for i in stock_intent.items]
        assert any("banana" in n for n in names)
        assert any("bell pepper" in n or "pepper" in n for n in names)

    def test_purchase_quantities(self, parser: _ParserProxy) -> None:
        # Este test existía con la frase de acá y una sola aserción, `result is not None`,
        # que se cumple hasta cuando el parser no entiende nada: la frase salía como `mixed`
        # con confianza 0.10 —o sea, sin nada que confirmar— y el test pasaba igual. Ahora
        # pide lo que la frase promete.
        result = parser.parse("Compramos 2 kilos de arroz y 500 gramos de pasta", speaking_user="diego")
        stock = next((i for i in result.intents if i.intent_type == "add_stock"), None)
        assert stock is not None
        by_name = {i.food_name.lower(): i for i in stock.items}
        assert set(by_name) == {"arroz", "pasta"}
        assert (by_name["arroz"].quantity, by_name["arroz"].unit) == (2.0, "kg")
        assert (by_name["pasta"].quantity, by_name["pasta"].unit) == (500.0, "g")

    def test_consume_stock(self, parser: _ParserProxy) -> None:
        result = parser.parse("We used 4 tomatoes for lunch", speaking_user="diego")
        assert result is not None
        consume = next(
            (i for i in result.intents if i.intent_type == "consume_stock"), None
        )
        assert consume is not None


class TestMealLogging:
    def test_shared_meal_same_food(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "Tonight we had milanesa with mashed potatoes for dinner",
            speaking_user="diego",
        )
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        assert meal.meal_type in ("dinner", "other")

    def test_different_foods_per_person(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "Rocío ate milanesa and a banana, Diego ate ravioli and ice cream",
            speaking_user="diego",
        )
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        # Should have per-user items
        assert meal.items_per_user
        assert "diego" in {k.lower() for k in meal.items_per_user}
        assert "rocío" in {k.lower() for k in meal.items_per_user} or \
               "rocio" in {k.lower() for k in meal.items_per_user}

    def test_meal_with_quantity(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "We are about to have ravioli with tomato sauce, around 400 grams each",
            speaking_user="diego",
        )
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None

    def test_i_refers_to_speaking_user(self, parser: _ParserProxy) -> None:
        result = parser.parse("I had eggs for breakfast", speaking_user="rocio")
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        # "I" should resolve to rocio
        participants = list(meal.items_per_user.keys()) if meal.items_per_user else []
        assert any("rocio" in p.lower() or "rocío" in p.lower() for p in participants) or \
               any("both" in p.lower() for p in participants)


class TestWorkoutLogging:
    def test_gym_session_both_users(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "We went to the gym for 1 hour and trained chest, shoulders and triceps",
            speaking_user="diego",
        )
        workout = next((i for i in result.intents if i.intent_type == "log_workout"), None)
        assert workout is not None
        assert workout.duration_minutes == 60
        assert "both" in workout.participants or len(workout.participants) > 0

    def test_single_user_workout(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "Diego rode a bike for 40 minutes",
            speaking_user="rocio",
        )
        workout = next((i for i in result.intents if i.intent_type == "log_workout"), None)
        assert workout is not None
        assert workout.duration_minutes == 40
        assert any("diego" in p.lower() for p in workout.participants)

    def test_yoga_workout(self, parser: _ParserProxy) -> None:
        result = parser.parse("Rocío did yoga today", speaking_user="diego")
        workout = next((i for i in result.intents if i.intent_type == "log_workout"), None)
        assert workout is not None


class TestBodyMetric:
    def test_weight_log(self, parser: _ParserProxy) -> None:
        result = parser.parse("Today I weigh 82.3 kg", speaking_user="diego")
        metric = next((i for i in result.intents if i.intent_type == "log_body_metric"), None)
        assert metric is not None
        assert metric.weight_kg == pytest.approx(82.3, abs=0.1)

    def test_weight_in_different_phrasing(self, parser: _ParserProxy) -> None:
        result = parser.parse("My weight today is 75 kg", speaking_user="rocio")
        metric = next((i for i in result.intents if i.intent_type == "log_body_metric"), None)
        assert metric is not None
        assert metric.weight_kg == pytest.approx(75.0, abs=0.1)


class TestPreferenceUpdate:
    def test_negative_preference(self, parser: _ParserProxy) -> None:
        result = parser.parse("Do not suggest swimming", speaking_user="diego")
        pref = next((i for i in result.intents if i.intent_type == "update_preference"), None)
        assert pref is not None
        assert pref.preference_signal in ("impossible", "dislikes", "avoid")
        assert "swimming" in pref.item_name.lower()

    def test_positive_preference(self, parser: _ParserProxy) -> None:
        result = parser.parse("We like biking", speaking_user="diego")
        pref = next((i for i in result.intents if i.intent_type == "update_preference"), None)
        assert pref is not None
        assert pref.preference_signal in ("likes", "preferred")
        assert "bik" in pref.item_name.lower()


class TestConfidence:
    def test_well_formed_input_high_confidence(self, parser: _ParserProxy) -> None:
        result = parser.parse("Today I weigh 82.3 kg", speaking_user="diego")
        assert result.overall_confidence >= 0.6

    def test_garbage_input_low_confidence(self, parser: _ParserProxy) -> None:
        result = parser.parse("asdfghjklqwerty random words", speaking_user="diego")
        assert result.overall_confidence < 0.5 or len(result.intents) == 0


class TestCastellano:
    """La app está en castellano y hasta acá el parser entendía sobre todo inglés.

    Estos tests existen porque el hueco no se veía como un error: sin un verbo conocido la
    frase sale como `mixed` con confianza 0.10, se guarda igual en
    `nlp_ingestion_events` y la pantalla de confirmación no tiene nada que confirmar. No hay
    excepción, no hay log, no hay nada roto — solo una captura que no sirvió. Cada aserción
    de acá es una frase que una casa de dos escribe de verdad.
    """

    # ── Comidas ─────────────────────────────────────────────────────────────
    def test_first_person_singular_meal(self, parser: _ParserProxy) -> None:
        result = parser.parse("comí milanesa con puré", speaking_user="diego")
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        # El verbo no puede quedar adentro del nombre del alimento, y lo que sigue a "con"
        # tampoco: igual que "pasta with sauce" en inglés, el ítem es el primero.
        assert [i.food_name for i in meal.items_per_user["diego"]] == ["milanesa"]

    @pytest.mark.parametrize(
        ("phrase", "meal_type"),
        [
            ("desayuné avena", "breakfast"),
            ("almorcé pollo", "lunch"),
            ("cené ensalada", "dinner"),
            ("merendé tostadas", "snack"),
        ],
    )
    def test_meal_type_comes_from_the_conjugated_verb(
        self, parser: _ParserProxy, phrase: str, meal_type: str
    ) -> None:
        # En inglés el tipo de comida es un sustantivo suelto ("for dinner"); en castellano
        # está en el verbo y no se repite. Sin eso, toda comida quedaba como "other".
        result = parser.parse(phrase, speaking_user="diego")
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        assert meal.meal_type == meal_type

    def test_first_person_plural_means_both_without_a_pronoun(
        self, parser: _ParserProxy
    ) -> None:
        # "cenamos fideos" es de los dos. El plural vive en el verbo y no se escribe el
        # pronombre, así que sin la lista de verbos esto se atribuía solo a quien escribió:
        # una cena compartida entrando como comida de uno.
        result = parser.parse("cenamos fideos", speaking_user="diego")
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        assert meal.participants == ["both"]
        assert list(meal.items_per_user) == ["both"]

    def test_who_ate_what_in_spanish(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "Diego cenó fideos, Rocío cenó ensalada", speaking_user="diego"
        )
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        assert {k: [i.food_name for i in v] for k, v in meal.items_per_user.items()} == {
            "diego": ["fideo"],
            "rocio": ["ensalada"],
        }

    # ── Despensa ────────────────────────────────────────────────────────────
    def test_purchase_list_split_by_y(self, parser: _ParserProxy) -> None:
        result = parser.parse("compré 6 bananas y 1 kg de avena", speaking_user="diego")
        stock = next((i for i in result.intents if i.intent_type == "add_stock"), None)
        assert stock is not None
        # Sin el separador `y` toda la lista entraba como **un** alimento llamado
        # "6 bananas y 1 kg de avena".
        assert [(i.food_name, i.quantity, i.unit) for i in stock.items] == [
            ("banana", 6.0, None),
            ("avena", 1.0, "kg"),
        ]

    @pytest.mark.parametrize(
        ("phrase", "qty", "unit"),
        [
            ("conseguimos 2 kilos de arroz", 2.0, "kg"),
            ("compramos medio kilo de queso", 0.5, "kg"),
            ("compré 500 gramos de yerba", 500.0, "g"),
            ("compré 2 litros de leche", 2.0, "l"),
            ("compramos 3 unidades de pan", 3.0, "unit"),
            ("compré una docena de huevos", None, None),
        ],
    )
    def test_spanish_units_normalise(
        self, parser: _ParserProxy, phrase: str, qty: float | None, unit: str | None
    ) -> None:
        # "kilos" y "kg" tienen que llegar a la despensa como la misma unidad, o el mismo
        # alimento queda partido en dos filas que no se suman. `docena` no está en la lista
        # a propósito: no hay unidad canónica para mapearla, así que se deja pasar como
        # nombre en vez de inventar una — y este caso lo fija para que sea una decisión
        # visible y no un olvido.
        result = parser.parse(phrase, speaking_user="diego")
        stock = next((i for i in result.intents if i.intent_type == "add_stock"), None)
        assert stock is not None
        assert stock.items
        if unit is None:
            assert stock.items[0].unit is None
        else:
            assert (stock.items[0].quantity, stock.items[0].unit) == (qty, unit)

    def test_determiners_are_not_part_of_the_food_name(self, parser: _ParserProxy) -> None:
        # El nombre que sale de acá se busca contra el catálogo de alimentos: "la última
        # leche" no encuentra la leche y termina creando un alimento nuevo.
        result = parser.parse("usé la última leche", speaking_user="diego")
        consume = next((i for i in result.intents if i.intent_type == "consume_stock"), None)
        assert consume is not None
        assert [i.food_name for i in consume.items] == ["leche"]

    # ── Cuerpo ──────────────────────────────────────────────────────────────
    @pytest.mark.parametrize(
        ("phrase", "field", "value"),
        [
            ("pesé 82 kg", "weight_kg", 82.0),
            ("dormí 7 horas", "sleep_hours", 7.0),
            ("dormimos 8 hs", "sleep_hours", 8.0),
            ("cintura de 80 cm", "waist_cm", 80.0),
        ],
    )
    def test_body_metrics_in_spanish(
        self, parser: _ParserProxy, phrase: str, field: str, value: float
    ) -> None:
        # El caso de dormir es el que muestra que era un descuido y no una decisión:
        # `_SLEEP_RE` entendía `dormí|dormimos` desde siempre y la unidad seguía siendo solo
        # `hours`, así que la frase se clasificaba bien y el número no se leía nunca.
        result = parser.parse(phrase, speaking_user="diego")
        metric = next((i for i in result.intents if i.intent_type == "log_body_metric"), None)
        assert metric is not None
        assert getattr(metric, field) == pytest.approx(value)

    # ── Entrenamiento ───────────────────────────────────────────────────────
    @pytest.mark.parametrize("phrase", ["corrí 30 minutos", "corrí 30 min", "entrené 30'"])
    def test_workout_duration_in_spanish(self, parser: _ParserProxy, phrase: str) -> None:
        result = parser.parse(phrase, speaking_user="diego")
        workout = next((i for i in result.intents if i.intent_type == "log_workout"), None)
        assert workout is not None
        if phrase.endswith("'"):
            # No se lee: la duración con apóstrofo no está soportada, y queda fijado acá para
            # que se vea que es un hueco conocido y no una regresión.
            assert workout.duration_minutes is None
        else:
            assert workout.duration_minutes == 30

    def test_spanish_workout_has_no_named_exercise(self, parser: _ParserProxy) -> None:
        # Consecuencia aceptada y documentada: `_EXERCISE_MAP` está en inglés y ampliarlo
        # necesita una columna de alias en `exercise_types`, o sea una migración. Se
        # reconoce el entrenamiento y su duración, sin ejercicio nombrado — exactamente
        # igual que "trained for 45 minutes".
        result = parser.parse("corrí 30 minutos", speaking_user="diego")
        workout = next((i for i in result.intents if i.intent_type == "log_workout"), None)
        assert workout is not None
        assert workout.exercises == []

    # ── Preferencias ────────────────────────────────────────────────────────
    @pytest.mark.parametrize(
        ("phrase", "item_name", "signal", "participants"),
        [
            ("no me gusta el brócoli", "brócoli", "dislikes", ["diego"]),
            ("no nos gusta la remolacha", "remolacha", "dislikes", ["both"]),
            ("odio el pescado", "pescado", "dislikes", ["diego"]),
            ("no podemos comer gluten", "gluten", "dislikes", ["both"]),
        ],
    )
    def test_preference_subject_is_only_the_food(
        self,
        parser: _ParserProxy,
        phrase: str,
        item_name: str,
        signal: str,
        participants: list[str],
    ) -> None:
        # Lo que queda como `item_name` es el **sujeto** contra el que se guarda la
        # preferencia, y con eso se busca el alimento. Si sobra una palabra de la frase no
        # se encuentra nada: "no me gusta el brócoli" salía con sujeto "gusta brócoli", que
        # es una preferencia guardada contra un alimento que no existe.
        result = parser.parse(phrase, speaking_user="diego")
        pref = next((i for i in result.intents if i.intent_type == "update_preference"), None)
        assert pref is not None
        assert pref.item_name.lower() == item_name
        assert pref.preference_signal == signal
        assert pref.participants == participants

    def test_a_spanish_activity_is_a_preference_about_exercise(
        self, parser: _ParserProxy
    ) -> None:
        # El nombre puede no coincidir con el catálogo (eso necesita la columna de alias),
        # pero el **tipo** tiene que ser el correcto: guardado como preferencia de comida,
        # "correr" ensucia el filtro de alimentos con una palabra que no es comida y nadie
        # lo ve nunca.
        result = parser.parse("prefiero correr", speaking_user="diego")
        pref = next((i for i in result.intents if i.intent_type == "update_preference"), None)
        assert pref is not None
        assert pref.item_type == "exercise"

    def test_a_preference_about_an_activity_is_not_also_a_workout(
        self, parser: _ParserProxy
    ) -> None:
        # La compuerta de entrenamiento pide el verbo ("hice yoga") y no acepta la actividad
        # sola, justamente para esto: "prefiero correr" es lo que alguien quiere hacer, no lo
        # que hizo, y un entrenamiento fantasma al lado de la preferencia se guardaría igual.
        result = parser.parse("prefiero correr", speaking_user="diego")
        assert [i.intent_type for i in result.intents] == ["update_preference"]

    @pytest.mark.parametrize(
        ("phrase", "duration"),
        [
            ("hice yoga 40 minutos", 40),
            ("hicimos pilates media hora", 30),
            ("fuimos a spinning", None),
            ("entrené pesas una hora", 60),
            ("hice ejercicio 20 minutos", 20),
        ],
    )
    def test_doing_an_activity_is_a_workout(
        self, parser: _ParserProxy, phrase: str, duration: int | None
    ) -> None:
        # Antes de la 6.0 la compuerta enumeraba las actividades a mano y solo en inglés
        # (`did yoga|did pilates|did hiit`), así que "hice yoga 40 minutos" volvía `mixed` al
        # 0.10: un entrenamiento que la app no registró y sobre el que no avisó nada.
        result = parser.parse(phrase, speaking_user="diego")
        workout = next((i for i in result.intents if i.intent_type == "log_workout"), None)
        assert workout is not None, f"{phrase!r} no se leyó como entrenamiento"
        assert workout.duration_minutes == duration

    @pytest.mark.parametrize(
        "phrase",
        [
            "se acabó la leche",
            "se acabaron los huevos",
            "no queda café",
            "no hay más arroz",
            "se terminó el aceite",
        ],
    )
    def test_the_impersonal_way_of_saying_it_ran_out(
        self, parser: _ParserProxy, phrase: str
    ) -> None:
        # Así se avisa en castellano que algo se terminó: sin sujeto. Sin estas formas la
        # frase volvía `mixed` al 0.10 y el aviso de "poco stock" nunca se enteraba.
        result = parser.parse(phrase, speaking_user="diego")
        consume = next((i for i in result.intents if i.intent_type == "consume_stock"), None)
        assert consume is not None, f"{phrase!r} no se leyó como consumo"
        assert len(consume.items) == 1, f"{phrase!r} → {consume.items}"

    def test_a_pantry_fact_is_not_a_taste(self, parser: _ParserProxy) -> None:
        # `no` es la partícula de negación de **todo** en castellano, así que un `\bno\b`
        # suelto convertía "no queda café" en *"no te gusta 'queda café'"*: una preferencia
        # guardada contra un alimento que no existe. Lo explícito ("odio", "no me gusta")
        # sigue valiendo solo; lo suelto pierde contra un verbo de consumo.
        result = parser.parse("no queda café", speaking_user="diego")
        assert [i.intent_type for i in result.intents] == ["consume_stock"]

        # Y al revés: que la frase hable de comida no la convierte en un dato de despensa.
        result = parser.parse("no me gusta el café", speaking_user="diego")
        assert [i.intent_type for i in result.intents] == ["update_preference"]


class TestListasQueTienenQueCoincidir:
    """Los huecos de este parser no aparecen como excepciones: aparecen como un dato feo.

    Un verbo que abre la frase, está en la compuerta y falta en el recorte no lanza nada —
    deja un alimento llamado "compré 6 bananas". Una palabra de cantidad que está en la
    tabla y no en el regex se la come el nombre: "dos banana". Estos tests recorren las
    constantes en vez de repetirlas, así que agregar un verbo a una lista y olvidarse de la
    otra falla acá.
    """

    def test_every_meal_verb_is_stripped_from_the_food_name(
        self, parser: _ParserProxy
    ) -> None:
        for verb in nlp_rules._MEAL_VERBS.split("|"):
            result = parser.parse(f"{verb} milanesa", speaking_user="diego")
            meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
            assert meal is not None, f"{verb!r} no abre la compuerta de comidas"
            names = [i.food_name.lower() for v in meal.items_per_user.values() for i in v]
            assert names == ["milanesa"], f"{verb!r} quedó adentro del alimento: {names}"

    def test_every_stock_add_verb_is_stripped_from_the_item_name(
        self, parser: _ParserProxy
    ) -> None:
        for verb in nlp_rules._STOCK_ADD_VERBS.split("|"):
            result = parser.parse(f"{verb} 2 bananas", speaking_user="diego")
            stock = next((i for i in result.intents if i.intent_type == "add_stock"), None)
            assert stock is not None, f"{verb!r} no abre la compuerta de la despensa"
            names = [i.food_name.lower() for i in stock.items]
            assert names == ["banana"], f"{verb!r} quedó adentro del ítem: {names}"

    def test_every_stock_consume_verb_is_stripped_from_the_item_name(
        self, parser: _ParserProxy
    ) -> None:
        for verb in nlp_rules._STOCK_CONSUME_VERBS.split("|"):
            result = parser.parse(f"{verb} 2 bananas", speaking_user="diego")
            consume = next(
                (i for i in result.intents if i.intent_type == "consume_stock"), None
            )
            assert consume is not None, f"{verb!r} no abre la compuerta de consumo"
            names = [i.food_name.lower() for i in consume.items]
            assert names == ["banana"], f"{verb!r} quedó adentro del ítem: {names}"

    def test_every_number_word_is_read_as_a_quantity(self) -> None:
        # `_NUMBER_WORDS` la lee `_parse_qty`, y el único que le pasa algo es el grupo `qty`
        # de `_QTY_UNIT_ITEM`. Una entrada que ese grupo no reconozca no es solo inútil: la
        # palabra se la come el nombre del alimento.
        for word, value in nlp_rules._NUMBER_WORDS.items():
            items = nlp_rules._extract_items(f"{word} bananas")
            assert len(items) == 1, f"{word!r} → {items}"
            assert items[0].qty == value, f"{word!r} no se leyó como cantidad"
            assert items[0].food_name.lower() == "banana", f"{word!r} → {items[0].food_name!r}"

    def test_every_spanish_unit_has_a_canonical_form(self) -> None:
        # Si "kilos" y "kg" no normalizan a lo mismo, el mismo alimento queda en dos filas
        # de la despensa que no se suman nunca.
        canonical = {
            "g",
            "kg",
            "ml",
            "l",
            "oz",
            "lb",
            "unit",
            "slice",
            "cup",
            "tbsp",
            "tsp",
            "serving",
        }
        for written, expected in nlp_rules._UNIT_NORMALISE.items():
            assert expected in canonical, f"{written!r} normaliza a {expected!r}: no es canónica"

    def test_every_activity_name_opens_a_workout_and_types_a_preference(self) -> None:
        # `_ACTIVITY_NAMES` tiene dos lectores que fallan distinto, y por eso se recorren los
        # dos acá: la compuerta de entrenamiento ("hice yoga") y el tipado de la preferencia
        # ("prefiero correr" es ejercicio, no comida). Una actividad agregada al set entra en
        # los dos gestos o en ninguno; antes la compuerta las enumeraba a mano y quedaban tres.
        for name in nlp_rules._ACTIVITY_NAMES:
            assert nlp_rules._WORKOUT_TRIGGERS.search(
                f"hice {name} 20 minutos"
            ), f"{name!r} no abre un entrenamiento"
            assert (
                nlp_rules._classify_item_type(name) == "exercise"
            ), f"{name!r} se tipa como comida"


class TestElPlaceholderNoPromete:
    """Lo que la pantalla de captura ofrece como ejemplo tiene que funcionar.

    Este es el test que hubiera encontrado el problema solo: el `placeholder` traducido
    ofrecía *"corrí 30 min · compré 1 kg de avena"* y las dos frases salían como `mixed` con
    confianza 0.10. La app le pedía a la casa que escribiera en castellano y después no lo
    entendía, sin decirlo. Se leen del catálogo y no se copian acá para que cambiar el
    ejemplo sin probarlo falle.
    """

    _MSGID = "e.g. we had pasta for dinner · I ran for 30 min · bought 1 kg of oats"

    @staticmethod
    def _examples(text: str) -> list[str]:
        cleaned = text.split(".", 1)[1] if text.lower().startswith(("ej.", "e.g.")) else text
        return [part.strip() for part in cleaned.split("·") if part.strip()]

    def test_the_english_examples_parse(self, parser: _ParserProxy) -> None:
        for example in self._examples(self._MSGID):
            result = parser.parse(example, speaking_user="diego")
            assert result.intents[0].intent_type != "mixed", example
            assert result.overall_confidence >= 0.5, example

    def test_the_spanish_examples_parse(self, parser: _ParserProxy) -> None:
        from babel.messages.pofile import read_po

        from tests.test_i18n_catalog import CATALOG

        with CATALOG.open(encoding="utf-8") as f:
            catalog = read_po(f)
        message = catalog.get(self._MSGID)
        assert message is not None and message.string, (
            "el placeholder de captura ya no está en el catálogo con este msgid"
        )
        examples = self._examples(str(message.string))
        assert examples, "el placeholder traducido no ofrece ningún ejemplo"
        for example in examples:
            result = parser.parse(example, speaking_user="diego")
            assert result.intents[0].intent_type != "mixed", example
            assert result.overall_confidence >= 0.5, example
