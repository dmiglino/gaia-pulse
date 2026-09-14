# GaiaPulse v3 — qué hay, qué se hizo, qué queda de la v2

Para alguien que quiere **entender el estado de la app**, no usarla. Si lo que buscás es
cómo sacarle provecho, el otro documento es [`guia-de-uso.md`](guia-de-uso.md).

Los números de acá se midieron sobre el árbol al escribirlo, no de memoria. La fecha
importa: son de la rama `v3` con las seis fases cerradas.

---

## 1. Qué es y para quién

GaiaPulse es una app de bienestar para **un hogar de dos personas** —Diego y Rocío— que
escriben en castellano lo que comieron, lo que entrenaron, lo que pesan y lo que
compraron, y reciben a cambio sugerencias que se apoyan en eso. No es una app de
tracking con formularios: la entrada principal es una frase de texto libre, y toda la
estructura —qué comida, de quién, a qué hora, con qué alimentos— la deduce la app y la
somete a confirmación antes de guardar nada. El hogar es de dos porque el modelo de datos
lo es: una frase puede hablar de una persona, de la otra o de las dos, y la despensa y la
lista de compras son de la casa mientras el aprendizaje es de cada uno.

No hay app móvil ni superficie conversacional: es un servidor FastAPI que renderiza HTML
en el servidor, con HTMX para los intercambios parciales y Alpine para el estado local de
cada componente. Sin build de frontend, a propósito y por restricción explícita.

---

## 2. El recorrido de un dato

Es lo que hace entendible todo lo demás. Una frase escrita en Capture atraviesa seis
etapas y **ninguna de las cinco primeras es la que la persona ve**:

```
  "anoche cenamos milanesas con ensalada los dos"
                    │
                    ▼
        ┌───────────────────────┐
        │ 1. Capture            │  POST /capture/  →  nlp_ingestion_events
        │    (nada se guarda    │  status = "pending_confirmation"
        │     en el dominio)    │
        └───────────┬───────────┘
                    ▼
        ┌───────────────────────┐
        │ 2. NLP capa 1         │  reglas, siempre corre
        │    NLP capa 2         │  LLM, solo si confianza capa 1 < 0.7
        └───────────┬───────────┘  parser_layer = rules | llm | combined
                    ▼
        ┌───────────────────────┐
        │ 3. Confirmación       │  quién / qué / cuándo, con la confianza a la vista
        │    (la compuerta)     │  descartar acá no deja rastro en el dominio
        └───────────┬───────────┘
                    ▼
        ┌───────────────────────┐
        │ 4. Tablas de dominio  │  meal_events + meal_items_consumed +
        │                       │  meal_participants  (o workouts, pantry,
        └───────────┬───────────┘  body_metric_logs, según el intent)
                    ▼
        ┌───────────────────────┐
        │ 5. Señales            │  behavior_signals, una fila por sujeto y
        │    (behavior_signals) │  por persona, con semivida según su origen
        └───────────┬───────────┘
                    ▼
        ┌───────────────────────┐
        │ 6. Sugerencia         │  7:40 y 18:40 locales: contexto → generadores →
        │                       │  filtros → score → dedup → suggestions
        └───────────────────────┘
```

**1. La captura no guarda nada del dominio.** El POST escribe una fila en
`nlp_ingestion_events` con `status="pending_confirmation"` y los intents deducidos como
JSON. Ninguna comida existe todavía. Esto no es un detalle de implementación: es la regla
de arquitectura que sostiene la confianza en un parser que puede equivocarse.

**2. Dos capas, y la segunda es opcional.** La capa 1 son reglas deterministas
(`app/nlp/rules.py`) y **siempre** corre. La capa 2 es el LLM, y corre solo si hay clave
de API configurada y la confianza de la capa 1 quedó por debajo de 0.7. El resultado
declara de dónde salió: `rules`, `llm` o `combined`. Si el LLM falla o devuelve algo que
no valida, se descarta y queda lo de la capa 1 — o sea que la app funciona entera sin
clave de OpenAI, con menos precisión y sin avisos crípticos. Hay siete tipos de intent:
comida, entrenamiento, métrica corporal, alta de stock, consumo de stock, preferencia y
`mixed` para las frases que hacen dos cosas.

**3. La pantalla de confirmación es la compuerta.** Muestra qué entendió, **de quién** es
cada cosa (avatar y nombre por intent, no una etiqueta global) y con cuánta confianza.
Confirmar es lo único que escribe en el dominio, y solo el dueño del evento puede hacerlo,
solo mientras siga en `pending_confirmation`. Descartar no deja rastro fuera del propio
evento.

**4. Recién acá aparecen las filas de siempre.** Cada intent se despacha a su servicio, y
cada servicio escribe sus tablas: una comida son tres (`meal_events`,
`meal_items_consumed`, `meal_participants`), un entrenamiento otras tres, una compra
mueve `pantry_stock` y deja el rastro en `pantry_movements`, un pesaje es una fila en
`body_metric_logs`. Una cena "de los dos" es **una** comida con dos participaciones, no
dos comidas.

**5. Las señales son de cada persona, nunca del hogar.** `learning.record_signal()` toma
un `user_id` y no acepta un `household_id`: comer algo, comprarlo, aceptar o rechazar una
sugerencia, o escribir un motivo, escriben filas en `behavior_signals` contra un **sujeto
declarado** (`subject_type` + `subject_name`) y con un `source_type` que decide cuánto
tarda esa señal en pesar la mitad. Un dato de hace un mes cuenta menos que el de ayer, sin
que nada tenga que borrarlo.

**6. La sugerencia se arma dos veces por día**, a las 7:40 y a las 18:40 locales. La
corrida arma **una sola vez** un `UserContext` de solo lectura —tendencia de peso, sueño
reciente, RPE, balance de grupos musculares, macros del día, antigüedad del panel de
sangre—, cada generador propone candidatos declarando el sujeto de cada uno, los filtros
vetan lo que la persona bloqueó, el scorer ordena con las señales ya decaídas, se descarta
lo que ya tiene una tarjeta pendiente para el mismo sujeto, y lo que sobrevive se guarda
en `suggestions` con su explicación calculada. Lo que se ve en Home es el final de esa
cadena.

---

## 3. Por área: lo que estaba, lo que cambió, lo que sigue igual

### 3.1 Funcionamiento

| Ya estaba en la v2 | Lo que la v3 cambió | Sigue igual a propósito |
|---|---|---|
| Las nueve pantallas principales, el CRUD de comidas/entrenamientos/despensa/salud, el parser de dos capas, los cuatro generadores de sugerencias y los jobs de fondo | **Los 13 defectos de la tabla A**: cuatro acciones HTMX apuntaban a rutas inexistentes o a JSON crudo, `appStore()` tiraba un error de Alpine en cada carga, el sistema de flash era una API de Flask que no existe, las seis acciones rápidas del Home llevaban a un textarea vacío, el tab Workouts del historial renderizaba tarjetas en blanco | El modelo de datos: 20 tablas y **tres** migraciones — v3 agregó una sola (`0003`, sujeto de sugerencia) |
| `app/web/onboarding.py`, completo y migrado | **El onboarding existía y era inalcanzable**: el router no estaba registrado, la plantilla no existía y ninguna ruta miraba `onboarding_completed`. v3 escribió el wizard, registró el router y puso el gate | Que no haya recuperación de contraseña ni "recordarme": v3 **quitó las dos promesas muertas** de la UI en vez de implementarlas a medias |
| La ventana de comida, el cooldown de notificaciones, el mapeo del LLM | Tres bugs que eran bugs y no diseño: la ventana de comida se calculaba en UTC (a las 08:00 locales el motor creía que era el almuerzo), un `or_` mal armado hacía que **la notificación de uno suprimiera la del otro**, y un desajuste `name`/`food_name` hacía que *todo* `log_meal` del LLM lanzara `ValidationError` en silencio — el tipo de captura más frecuente nunca se beneficiaba de la capa 2 | Que la capa 2 sea opcional y degrade a la capa 1 sin avisar al usuario |

### 3.2 Diseño

| Ya estaba en la v2 | Lo que la v3 cambió | Sigue igual a propósito |
|---|---|---|
| Una base mejor que el promedio: paleta `brand`, Inter, tarjetas redondeadas, nav responsive con la acción primaria destacada | **12 paletas paralelas** conviviendo (545 usos de `slate`, 53 de `indigo` crudo al lado de 170 de `brand`) pasaron a una capa de tokens semánticos en variables CSS. **91 SVG copiados y pegados y 0 macros** pasaron a 59 macros y 49 iconos con nombre. Cuatro `<head>` divergentes pasaron a un shell canónico único | Tailwind por CDN, sin build step: prohibido por restricción explícita. La consecuencia se asume y está anotada donde importa |
| Cero soporte de modo oscuro (0 clases `dark:`, sin toggle, sin `prefers-color-scheme`) | Modo oscuro real, y por **swap de variables** en lugar de un barrido de clases: el tema se decide antes del primer render para que no haya flash, y se persiste | Que el layout de las pantallas secundarias no se rediseñe: solo Home, Capture y Dashboard cambiaron su arquitectura de información |
| `components/empty_state.html`, usado en 3 de 9 lugares que lo necesitaban | Un `ui.empty_state` usado por **13** plantillas, y emoji-como-icono reemplazado por iconografía consistente en las seis pantallas que lo usaban | 43 plantillas y ni un framework de JS: HTMX + Alpine y nada más |

### 3.3 La capa de inteligencia

Es donde v3 cambió más, y el diagnóstico de la v2 era duro y honesto: **había reglas pero
no razonamiento, feedback pero no aprendizaje, y trabajos programados pero no sentido del
tiempo.**

| Ya estaba en la v2 | Lo que la v3 cambió | Sigue igual a propósito |
|---|---|---|
| Cuatro jobs con `IntervalTrigger`, o sea con el reloj anclado a **cuándo arrancó el proceso**: un reinicio a las 03:00 dejaba todo disparando a las 03:00 para siempre. Cero `CronTrigger` en el repo, cero horario de silencio, `Household.timezone` sin leer | **Un solo reloj local** (`app/core/clock.py`) y ocho jobs con `CronTrigger` en la timezone del hogar, cada hora elegida por una razón escrita al lado: el stock a las 9:10 porque todavía se puede comprar, la inactividad a las 13:05 porque a la noche el aviso ya es un reproche, el pesaje a las 8:20 porque sirve en ayunas. Franja de silencio 22–8 antes de crear cualquier notificación | Que la limpieza corra **dentro** de la franja de silencio (poda a las 4:15, barrido a las 6:30): no le habla a nadie, solo escribe filas |
| El único control de ruido era por **categoría**, con un cooldown de 6 h igual al intervalo del job: la leche baja hace tres semanas producía ~84 notificaciones idénticas | Dedup **por sujeto** usando `related_entity_type`/`related_entity_id` —columnas que ya existían y ningún job poblaba—: se avisa una vez por sujeto, y se vuelve a avisar solo si **empeora**. Más poda de notificaciones viejas y `COUNT(*)` en SQL donde antes se traían todas las filas a Python para hacerles `len()` | Que las notificaciones sean push-nada: siguen viviendo dentro de la app |
| El aprendizaje estaba keyeado al **título renderizado** (`suggestion.title.lower()[:200]`) y el scorer lo matcheaba por bolsa de palabras: rechazar *"Time to get moving!"* suprimía candidatos de **comida** que compartieran tokens. `snoozed` se escribía con valor 0.0 y no se leía nunca; "posponer" actuaba como rechazo; el motivo de texto libre moría en `notes` | Aprendizaje anclado al **sujeto declarado por el generador** (alimento, actividad, grupo muscular, marcador, ítem de stock), con decaimiento por semivida según el origen de la señal, evidencia saturante (`n/(n+k)`: una observación es una pista, seis son una regla), veto solo desde un "no" deliberado, y el motivo de texto libre **minado** contra los nombres que la app conoce | Que el LLM no entre al camino de recomendación: agrega latencia, costo y no-determinismo a un job de fondo, y hay mucho dato sin explotar antes de necesitarlo |
| `rationale` era un string fijo por regla: `"Dietary variety supports micronutrient balance."`, idéntico para cualquier alimento de cualquier persona en cualquier día. Lo que de verdad decidió el orden vivía en `logger.debug` | La explicación se **calcula** y tiene dos mitades escritas por dos capas: el dato lo pone el generador (que es el que tiene los números) y el aprendizaje lo pone el scorer (que es el que tiene los ejes). Ningún eje se nombra cuando su delta es 0, lo que sube y lo que baja van en frases separadas, y cuando no hay ni dato ni aprendizaje **la tarjeta lo dice** en vez de caer en una frase de catálogo | Que la confianza siga siendo un porcentaje y no una promesa: encuadre no diagnóstico explícito |
| `generate_for_household` sin un solo llamador: todo el generador de despensa y compras nunca llegaba al usuario. `BodyMetricLog`, macros, RPE, `ExerciseType` (20 ejercicios sembrados) y la antigüedad del panel de sangre, sin leer | `UserContext` armado una vez por corrida lee todo eso, la lista de compras del hogar existe, y el generador de sangre pasó a llevar la antigüedad del panel explícita y a atravesar los filtros como todos — antes `_infer_category` devolvía `None` para `category="habit"` y **esas sugerencias eludían los dos filtros de seguridad** | Que los marcadores de sangre sigan en un blob JSON: normalizarlos es un refactor de datos con su propio cambio |

### 3.4 i18n y accesibilidad

| Ya estaba en la v2 | Lo que la v3 cambió | Sigue igual a propósito |
|---|---|---|
| Babel/gettext con catálogo `es_AR` | `health/detail.html` tenía **cero** llamadas a `_()`; los valores de enum se mostraban con `|title` sin traducir; había plurales hardcodeados (`participant(s)`). Hoy el catálogo tiene **528 entradas y ninguna sin traducir**, y un test lo mantiene así | Un solo idioma además del inglés de los `msgid`. El catálogo se mantiene **a mano**: `pybabel update` borra los comentarios de sección y no se corre |
| El parser de reglas era **de entrada en inglés**, y nada lo decía: sin un verbo reconocido la frase no llega a ningún parser, se guarda con `status="pending_confirmation"` y nada que confirmar — sin excepción, sin log, sin nada rojo | Traducir la interfaz y traducir la **entrada** son dos trabajos, y hasta la Fase 6 solo estaba hecho el primero: la pantalla de captura ofrecía en castellano ejemplos que su propio parser devolvía como `mixed` al 0.10. v3 cerró los seis grupos de disparadores **y todos sus lectores** —la frase entraba, se clasificaba bien y el número no se leía nunca ("dormí 7 horas" daba una medición vacía)—, más las dos cosas que el castellano hace distinto: la pluralidad viaja en el verbo ("cenamos fideos" es de los dos) y el tipo de comida también ("nadie escribe 'cené la cena'") | Los **nombres de ejercicio** siguen siendo ingleses: es una columna de alias en `exercise_types`, o sea una migración. Y `docena` no se normaliza porque no hay unidad canónica a la que mandarla |
| Heroicons, foco visible, nav accesible por teclado | Botones icon-only sin `aria-label`, inputs sin label, tabs sin `role="tab"`, y **ningún** fragmento HTMX con `aria-live`. v3 los cubrió, y con la regla que importa: el `role="status"` va en el **contenedor** de la página, porque una región `aria-live` tiene que existir en el DOM antes de que su contenido cambie | Auditar leyendo el elemento y no grepeando el atributo: `grep '<button' \| grep -v aria-label` reporta como defecto todo botón multilínea |

### 3.5 La red de tests

| Ya estaba en la v2 | Lo que la v3 cambió | Sigue igual a propósito |
|---|---|---|
| 9 módulos, todos unit tests de servicio y repositorio, **117 tests**. El fixture `client` de `conftest.py` no se usaba en ningún lado: cero tests de la capa web | **783 tests en 31 módulos**, con la capa web cubierta: smoke de todas las páginas, el gate de onboarding, los fragmentos HTMX, el catálogo de traducciones, el reloj y la franja de silencio, el dedup por sujeto, las 52 pruebas del aprendizaje y las 40 del parser en castellano | PostgreSQL como sistema de registro y SQLite solo para tests. Una feature que se comporta distinto entre los dos es un bug, no una diferencia de motor |
| — | Tres tests que existen para un mismo modo de falla —**una lista mantenida a mano que deja de coincidir con el código y no avisa**—: que cada URL escrita en una plantilla resuelva contra el router con su método y sin depender de un redirect, que el smoke cubra **todas** las rutas de página (con igualdad de conjuntos en las dos direcciones, para que una entrada vieja no finja cobertura), y que la lista de jobs que "hablan" se derive del código y no de la memoria. La Fase 6 agregó los dos del parser, del mismo molde: los tests de acoplamiento **iteran** las constantes de verbos en vez de repetirlas —así un verbo agregado a una lista y no a otra falla sin que nadie escriba un caso— y el del placeholder **lee el ejemplo del catálogo** en vez de copiarlo. El primero encontró, en su primera corrida, un defecto que estaba en los dos idiomas desde la v1: `eat` recortaba adentro de `eaten`, y *"we have eaten pasta"* dejaba un alimento llamado "en pasta" | Que `alembic check` no corra en esta máquina: necesita PostgreSQL y se reporta explícitamente en cada checkpoint en lugar de saltearse en silencio |

---

## 4. La vuelta de tuerca: el aprendizaje se puede auditar

Esto va aparte porque no cambia el comportamiento de la app: **cambia la relación con
ella.**

En la v2 el aprendizaje era **inauditable**. `behavior_signals` se escribía en cinco
lugares y no tenía una sola lectura de usuario: no había pantalla, ni ruta, ni consulta.
La app ajustaba lo que te ofrecía según cosas que había deducido de tu conducta, y no
existía ninguna forma —dentro de la app— de saber qué había deducido, de cuánto lo había
visto, ni de decirle que se equivocó. Un sistema que aprende de vos y no te deja ver lo
aprendido no es opaco por accidente: es una caja negra con tus datos adentro.

En la v3 eso se ve, se cuestiona y se borra. El perfil muestra cada cosa que la app
concluyó por su cuenta, con **cuántas veces lo vio y cuándo fue la última**, y cada línea
tiene un botón para olvidarla. Las cifras están porque sin ellas el panel sería una lista
de afirmaciones: "no te gusta el brócoli" invita a discutir, "lo vimos dos veces, la
última hace once días" invita a decidir. Y la lista es **de cada persona**: olvidar lo
tuyo no toca lo del otro, aunque compartan la casa y la despensa.

Dos aclaraciones que el panel hace explícitas porque sin ellas el gesto se malentiende:

- **Olvidar no es prohibir.** Se borran los registros que respaldan esa línea, no la
  conducta: si vuelve a pasar, se vuelve a aprender. Lo que no se quiere nunca más va en
  las restricciones alimentarias o en las actividades imposibles, que no caducan.
- **Los patrones por categoría no se guardan solos.** Salen de los ítems que los
  respaldan, así que se van cuando esos ítems se van.

Y hubo que corregir un **rótulo falso** para que todo eso fuera legible: dos pantallas
llamaban "aprendido" a lo que la persona había **declarado** en el onboarding o en el
perfil. Son dos cosas distintas y se corrigen distinto — *"lo que nos dijiste"* se corrige
diciendo otra cosa, *"lo que GaiaPulse fue aprendiendo"* se corrige olvidándolo. Mientras
las dos se llamaran igual, el panel no podía enseñar nada: la mitad de sus líneas no se
podían arreglar con el botón que tenían al lado.

---

## 5. Lo que quedó afuera, y por qué

Un documento que solo cuenta lo que se hizo es propaganda. Lo valioso para quien llega
después es el mapa de lo que falta, con nombre y razón.

**Validación de CSRF.** `csrf_token` se genera y se renderiza en los formularios, y
**ninguna ruta POST lo valida jamás**. Es un hueco real de seguridad, no una omisión
cosmética, y está afuera porque arreglarlo bien exige una revisión de seguridad propia:
meterlo de contrabando dentro de un rediseño visual es exactamente cómo se cuela un
arreglo a medias que después nadie audita.

**Cabeceras de seguridad.** La app no manda ninguna: no hay CSP, ni `X-Frame-Options`, ni
`Referrer-Policy`, ni HSTS. Y una CSP útil es imposible mientras Tailwind inyecte
`<style>` en runtime desde el CDN — haría falta `style-src 'unsafe-inline'` para siempre,
que es justo lo que una CSP viene a cerrar. O sea que está **atado al build step**, que
también está afuera. La consecuencia práctica quedó anotada donde importa: cualquier dato
de request o de base interpolado en un atributo `style` o en el parámetro `attrs` de un
macro no tiene red de contención, y por eso `components/ui.html` lleva escrita la regla de
no interpolar nunca ahí, y `avatar_color` se valida en la escritura.

**El LLM en el camino de recomendación.** Latencia, costo y no-determinismo dentro de un
job de fondo, cuando todavía hay datos recolectados sin explotar. El motor de
recomendación de v3 es 100% determinista, y eso es una decisión, no una carencia: una
sugerencia que no se puede reproducir tampoco se puede explicar, y la explicación era
justamente lo que había que arreglar.

**Build step de Tailwind.** Prohibido por la restricción de no introducir build de
frontend. Arrastra consigo la CSP y el peso del CDN.

**Edición inline de los intents del NLP.** Hoy se confirma o se descarta; corregir "media
palta" a "una palta" requiere volver a escribir la frase. Está afuera porque cambia el
contrato del backend, no solo la pantalla.

**Normalizar los marcadores de sangre.** Viven en un blob JSON, lo que impide calcular
tendencia por SQL: hoy un panel se compara con el anterior a mano y no hay serie. Es un
refactor de datos con su propia migración y su propio riesgo.

**Objetivos nutricionales declarados.** Es la mejor feature que queda, y por eso vale
explicar el techo que impone su ausencia: la tarjeta de macros tuvo que comparar a cada
persona **consigo misma a la misma hora** —su propio promedio de proteína y fibra en el
desayuno— porque no hay un objetivo contra el que medir. Sabe decir "hoy vas por debajo de
tu propio promedio", no "te faltan 40 g". Con un objetivo declarado la escala tendría dos
lados, la despensa podría ordenarse por aporte al hueco del día, y el hueco sería restable
en vez de comparativo. Queda afuera por tres razones concretas: es una feature de producto
(hace falta una pantalla, validación de rangos, y decidir si son por persona o por casa),
toca terreno clínico (un objetivo que la app **propone** es prescripción nutricional), y
el dato de entrada todavía es flojo (los macros salen del catálogo y de una cantidad
estimada por el parser; restar contra un número absoluto con ese margen da precisión
falsa).

**Nombres de actividad en castellano.** La Fase 6 hizo que el parser entienda castellano,
pero el **vocabulario de ejercicios** quedó afuera a propósito, y la distinción importa:
*"prefiero correr"* ahora se clasifica bien —como preferencia **de actividad** y no de
comida, que era el error peor porque un "correr" tipado como alimento ensucia el filtro de
comidas sin que se vea—, pero de la frase sale el texto "correr" y no una clave de
ejercicio. De un nombre de actividad solo salen los préstamos: yoga, pilates, spinning,
crossfit, cardio, running, hiit, zumba. Y **no alcanza con leer `ExerciseType` de la
base**: el catálogo también está en inglés y no tiene columna de alias. Lo que falta es esa
columna, o sea una migración: es un problema de datos, no de código, y por eso no entró en
un punto que no abre migraciones.

**Mezclar temas en una sola frase.** Es el hueco más visible que dejó la Fase 6, y salió de
escribir la guía: `cené fideos y corrí 30 minutos` no registra **ninguna** de las dos cosas
—vuelve `mixed` al 0.10— aunque cada mitad por separado funcione perfecto. No es una compuerta
incompleta: la **precedencia de `parse()`** hace que comida excluya entrenamiento y viceversa,
así que la frase con las dos no dispara nada. Y hay un segundo caso con otra causa y otra
forma: `compré leche y pesé 80 kg` **sí** anota el peso y la leche, y además da de alta un
producto fantasma llamado *"pesé 80 kg"*, porque cada parser lee la oración entera en vez de su
propio tramo y el de stock enumera por "y". Arreglarlo es **segmentar la oración**
y correr cada parser sobre su segmento —un cambio de diseño del módulo, con riesgo sobre las 40
aserciones en castellano que ya están—, y por eso la 6.2 lo enseña en negativo ("un tema por
captura") en vez de que la Fase 6 lo abra. Lo que sí funciona son varios hechos del mismo tema:
`pesé 81 kg y dormí 7 horas` es una sola medición con las dos cosas.

**La hora que menciona la frase no se usa nunca.** Todo se sella con el momento de la
confirmación. El parser *sí* extrae una referencia temporal de la frase y la guarda en el
intent, pero **ningún servicio la lee**, y nada escribe el `timestamp` del registro: los tres
servicios de captura caen siempre al `now`. La consecuencia para el usuario es doble y no
estaba escrita en ningún lado: *"ayer cenamos pizza"* guarda una cena de hoy, y **no hay forma
de cargar nada con fecha pasada** —ni por frase ni por pantalla—. Queda afuera porque no es
solo leer el campo: hay que decidir qué se hace con una fecha ambigua, y una captura que puede
aterrizar en cualquier día necesita poder corregirse, que es justo lo que falta abajo.

**No hay cómo editar ni borrar una comida, un entrenamiento o un pesaje.** La confirmación es
la única oportunidad de que el dato quede bien: después no hay botón en ninguna de las tres
pantallas. Los `DELETE` **existen en la API** (`app/api/meals.py`, `workouts.py`,
`body_metrics.py`) y ninguna plantilla los alcanza, así que es una superficie web faltante y no
un backend faltante — probablemente el hueco más chico de cerrar de esta lista. Las dos
excepciones muestran que el patrón ya está resuelto: la despensa ajusta cantidades desde la
grilla y un análisis de sangre se borra con confirmación. Lo que lo vuelve tolerable mientras
no esté es que las señales pesan por repetición y decaen, así que un registro de más se diluye;
lo que no se puede es corregirlo.

**Umbral de "poco" en la despensa.** El modelo tiene `low_stock_threshold` y los generadores lo
leen, pero **ninguna ruta lo escribe**: no hay pantalla ni endpoint que lo fije, así que en la
práctica "bajo" significa cero. La alerta de stock bajo existe y funciona; lo que falta es poder
adelantarla.

**Anclar la fecha del panel de sangre a su etiqueta.** El parser toma la **primera**
cadena con forma de fecha del documento, así que puede devolver una fecha de nacimiento o
de impresión. Eso acota lo que la "frescura del panel" puede prometer: el generador confía
en la fecha que recibe y no tiene forma de dudar de ella. Tampoco hay ruta para
**corregirla**. Las dos cosas van juntas.

**Remember-me y recuperación de contraseña.** Tocan sesión y auth; v3 quitó las promesas
muertas de la UI en vez de implementarlas a medias.

**Superficie conversacional** ("preguntale a tus datos"). No existe ninguna ruta hoy: es
una feature nueva, no un upgrade.

---

## 6. Los números, medidos sobre el árbol

| Qué | Cuánto |
|---|---|
| Tablas | 20 |
| Migraciones de Alembic | 3 (`0001`, `0002`, `0003`) — v3 agregó una |
| Rutas expuestas | 69 (32 bajo `/api/`, 37 web) |
| Páginas web (GET) | 19 |
| Plantillas Jinja | 43 |
| Macros de componentes | 59 (38 de dominio, 19 de UI, 2 de iconos) |
| Iconos con nombre | 49 |
| Generadores de sugerencias | 4 (actividad, sangre, comida, despensa) |
| Jobs programados | 8, todos `CronTrigger` en hora local |
| Entradas del catálogo `es_AR` | 528, ninguna sin traducir |
| Tests | 783 en 31 módulos |
| Líneas de Python en `app/` | 16 494 |
| Líneas de plantillas | 5 307 |
| Líneas de tests | 13 389 |

Para contexto: la v2 tenía **117 tests en 9 módulos** y cero pruebas de la capa web.

Cómo se reprodujeron, para que el próximo que actualice esta tabla no invente números:

```bash
grep -rh "__tablename__" app/models/*.py | wc -l          # tablas
ls alembic/versions/*.py | wc -l                          # migraciones
find app/templates -name '*.html' | wc -l                 # plantillas
grep -c "^{% macro" app/templates/components/*.html       # macros
grep -c '^msgid "' app/locales/es_AR/LC_MESSAGES/messages.po   # +1 por la cabecera
.venv/bin/python -m pytest tests/ -q                      # tests
```

Las rutas salen de `app.openapi()["paths"]` y no de caminar `app.routes`: las rutas web
viven dentro de envoltorios cuyo `.routes` está vacío y cuyas rutas internas no llevan el
prefijo. `tests/test_web_routes.py` explica por qué, y lo hace verificable.

---

## 7. La compuerta de calidad

Está en `AGENTS.md` y es la única copia. Vale saber lo que no corre acá: `alembic check`
necesita PostgreSQL y esta máquina no lo tiene, así que se reporta explícitamente en cada
checkpoint en lugar de saltearse en silencio. Y hay deuda vieja de formato y tipos
—`ruff` 221 hallazgos, `black` 39 archivos, `mypy` 41 errores— que **no bajó dentro de
v3**: el barrido repo-wide es un commit aparte, porque reformatear 39 archivos adentro de
un rediseño esconde el diff que importa. Los números se miden en cada checkpoint para
distinguir deuda vieja de daño nuevo.
