# GaiaPulse v3 — Plan de trabajo

> Producto **bonito, intuitivo, dinámico, inmersivo, moderno e inteligente**.
> Branch: `v3`. Entrega en 5 fases con checkpoint al final de cada una, más una fase 6
> de documentación de cierre que se escribe cuando las cinco están terminadas.
> Commits parciales y atómicos por funcionalidad (ver [Estrategia de commits](#estrategia-de-commits)).

---

## Índice

1. [Resumen ejecutivo](#resumen-ejecutivo)
2. [Decisiones de alcance](#decisiones-de-alcance)
3. [Diagnóstico](#diagnóstico)
   - [A. Cosas que hoy están rotas](#a-cosas-que-hoy-están-rotas)
   - [B. Feature huérfana: el onboarding](#b-feature-huérfana-el-onboarding)
   - [C. Deuda del sistema de diseño](#c-deuda-del-sistema-de-diseño)
   - [D. i18n y accesibilidad](#d-i18n-y-accesibilidad)
   - [E. Sin red de seguridad](#e-sin-red-de-seguridad)
   - [F. La capa de inteligencia](#f-la-capa-de-inteligencia)
4. [Plan de implementación](#plan-de-implementación)
   - [Fase 1 — Que funcione y que guíe](#fase-1--que-funcione-y-que-guíe)
   - [Fase 2 — Fundación del sistema de diseño](#fase-2--fundación-del-sistema-de-diseño)
   - [Fase 3 — Barrido visual + rediseño de IA](#fase-3--barrido-visual--rediseño-de-ia)
   - [Fase 4 — Inteligencia oportuna, con memoria y explicable](#fase-4--inteligencia-oportuna-con-memoria-y-explicable)
   - [Fase 5 — i18n, accesibilidad y red de seguridad](#fase-5--i18n-accesibilidad-y-red-de-seguridad)
   - [Fase 6 — Los dos documentos de cierre](#fase-6--los-dos-documentos-de-cierre)
5. [Estrategia de commits](#estrategia-de-commits)
6. [Verificación](#verificación)
7. [Fuera de alcance de v3](#fuera-de-alcance-de-v3)

---

## Resumen ejecutivo

GaiaPulse v1.0 tiene una base de diseño mejor de lo habitual (paleta `brand`, Inter,
tarjetas `rounded-2xl`, HTMX + Alpine, empty states reutilizables, nav responsive con
acción primaria destacada). Pero una auditoría completa de las **29 plantillas**, las
rutas web y el JS/CSS global reveló que el problema **no es principalmente estético**:

- hay **13 defectos funcionales visibles** (badges que nunca cargan, botones que dan 404,
  quick actions que llevan a un textarea vacío, un error de Alpine en cada carga de página);
- hay una **feature completa huérfana** (el onboarding: backend hecho, migración aplicada,
  router sin registrar y template inexistente);
- el sistema de diseño está **a medio migrar** (12 paletas paralelas, 53 usos de `indigo`
  crudo, 91 SVG copiados, 0 macros de Jinja, 0 soporte de modo oscuro);
- la **inteligencia** tiene reglas pero no razonamiento, feedback pero no aprendizaje, y
  jobs programados pero sin sentido del tiempo (ninguna notificación sabe qué hora es
  localmente, y ninguna sabe que ya avisó de eso mismo hace 6 horas).

El objetivo de v3 es que la mejora sea **notoria**: que la app funcione, que guíe, que se
sienta moderna, y que su inteligencia sea apropiada, oportuna y explicable.

**Una sola migración** en todo v3 (`0003`, en la Fase 4). Todo lo demás reutiliza columnas
que ya existen y están migradas.

---

## Decisiones de alcance

Tomadas explícitamente por el usuario antes de empezar:

| # | Decisión | Detalle |
|---|---|---|
| 1 | **Modo oscuro: sí** | Vía tokens semánticos en variables CSS + toggle persistido. No un barrido de clases `dark:`. |
| 2 | **Ambición visual** | Re-skin de las 29 pantallas **+ rediseño de la arquitectura de información** de Home, Capture y Dashboard (las tres más usadas). El resto conserva su layout y recibe los componentes nuevos. |
| 3 | **Preview del NLP** | Mostrar bien la atribución (avatar + nombre por intent) y una leyenda que explique el % de confianza y qué pasa al confirmar. Quitar el estado `editing` muerto. **La edición inline completa queda fuera de v3.** |
| 4 | **Entrega** | Por fases, con checkpoint al final de cada una. |

### Restricciones permanentes

- **No introducir build step de frontend** (ni bundler, ni framework JS). Tailwind sigue
  por CDN, Alpine y HTMX por CDN.
- **Regla de enrutamiento dual** (`AGENTS.md`): `/api/...` devuelve JSON y 401; toda otra
  ruta devuelve SSR HTML y redirige a `/login`. Nunca mezclar los dos estilos en una ruta.
- **Layering unidireccional**: `api/` y `web/` → `services/` → `repositories/` → modelos
  solo en `repositories/`. **Es el objetivo, no la foto del árbol**, y decirlo es el punto:
  al empezar v3 había 14 consultas inline en `app/recommendations/` (`engine.py` 5,
  `pantry_generator` 5, `activity_generator` 2, `meal_generator` 2) y 3 en
  `blood_analysis_service.py:58,66,74`. Hoy quedan **cero**: bajaron de a un módulo por vez,
  cada una dentro del cambio que ya estaba tocando ese archivo, y las últimas cinco con la
  4.5.7. Lo que es no-negociable es ese **trinquete**: código nuevo no agrega consultas fuera
  de `repositories/`, y un cambio que toca un módulo con consultas inline se lleva las suyas
  al repositorio. Una regla que el código contradice en dieciocho lugares se obedece a medias
  y no frena nada; escrita como trinquete, frena lo único que importa —que la deuda crezca—.
- **Compuerta de confirmación NLP**: ningún dato extraído por NLP/LLM llega a una tabla de
  dominio antes de que un humano confirme (`NLPIngestionEvent.status == "pending_confirmation"`).
- **Aislamiento por usuario**: toda consulta a una tabla de datos personales filtra por el
  `user_id` que actúa, incluso entre miembros del mismo hogar.
- **No construir para un tercer miembro del hogar hipotético**, un segundo proveedor de LLM
  ni un cliente mobile. GaiaPulse es una herramienta para dos personas.

---

## Diagnóstico

### A. Cosas que hoy están rotas

Todas verificadas leyendo el código, con archivo y línea.

| # | Qué | Dónde | Efecto para el usuario |
|---|---|---|---|
| 1 | Badge de notificaciones pide `/api/notifications/unread-count` (falta `/v1`) y el endpoint devuelve JSON, no HTML | `base.html:172,180,271` → `app/api/notifications.py:29` | El badge nunca funciona (404); si se arreglara solo el prefijo, inyectaría JSON crudo en el DOM |
| 2 | Descartar sugerencia desde Home apunta a `/api/suggestions/{id}/respond`, ruta inexistente | `home.html:226` | 404 silencioso: el botón no hace nada (la tarjeta desaparece por Alpine y reaparece al recargar) |
| 3 | Ajustar stock de pantry apunta a `/api/pantry/{id}/adjust`; la real es `POST /api/v1/pantry/adjust` con body | `pantry/partials/stock_grid.html:52` | 404: no se puede ajustar stock desde la grilla |
| 4 | "Refresh" de sugerencias es un `<form>` normal a una API JSON | `suggestions/index.html:12` | El navegador **navega** a una página de JSON crudo |
| 5 | `x-data="appStore()"` en `<body>`; la función no existe (`app.js` define `Alpine.store('app')`) | `base.html:65` | Alpine lanza error en **cada** carga de página |
| 6 | `get_flashed_messages()` es una API de Flask, no existe en Jinja/FastAPI | `base.html:214` | El sistema de flash messages está muerto por completo |
| 7 | Prefill de las quick actions asigna `''` en lugar de `prefillMap[prefill]` | `capture/index.html:167-170` | Las 6 acciones rápidas del Home llevan a un textarea **vacío** |
| 8 | El tab Workouts de History incluye `workout_card.html` (espera `workout`) con la variable de loop `event` | `history/index.html:45` | Tarjetas vacías en el tab Workouts |
| 9 | `auth/login.html` trae su propia `tailwind.config` **sin** la paleta `brand`, pero usa clases `brand-*` | `login.html:11-18` | Los colores de marca del login no se aplican (fallback a transparente/inherit) |
| 10 | `email_value` nunca se pasa desde la ruta | `login.html:101` ← `app/web/auth.py:38-42` | El email se borra tras un login fallido |
| 11 | `href="#"` en "Forgot password?" y checkbox `remember` que el backend ignora | `login.html:112,143` | Dos promesas que la app no cumple |
| 12 | `scrollbar-hide` no está definido en ningún lado | `pantry/index.html:71` | Barra de scroll horizontal visible en los filtros |
| 13 | `x-cloak` usado sin regla CSS | `base.html:65`, `stock_grid.html:50` | Flash de contenido sin estilar al cargar |
| 14 | Los cuatro `new Chart(...)` del dashboard corren **inline en tiempo de parseo**, pero Chart.js se carga con `defer` | `dashboard/index.html:149,168,179,190` ← `base.html:50` | `Chart is not defined`: **ninguno de los cuatro gráficos del dashboard se dibujó nunca**. Encontrado durante la Fase 2; se arregla en la Fase 3, que reescribe esos configs para usar los tokens |

### B. Feature huérfana: el onboarding

`app/web/onboarding.py` tiene un flujo completo (año de nacimiento, sexo, altura, peso,
peso objetivo, nivel de actividad, restricciones alimentarias), escribe
`User.onboarding_completed`, y la columna existe y está migrada
(`alembic/versions/0002_onboarding_blood_analysis.py:21`). Pero:

- el router **no está registrado** en `app/web/router.py`;
- el template `onboarding/index.html` **no existe** (daría 500 si se registrara el router);
- ninguna ruta chequea `onboarding_completed`, así que un usuario nuevo cae directo en `/`
  sin que nadie le explique qué es la app ni qué hacer.

Es el mayor hueco de "que el usuario sepa qué hacer y por qué", **y el backend ya está hecho**.

Contrato existente que el template debe respetar (`POST /onboarding/complete`, un solo submit):

| Campo | Tipo | Validación en el backend |
|---|---|---|
| `birth_year` | str (max 4) | 1900..año actual → setea `birth_date = date(year, 1, 1)` |
| `sex` | str (max 30) | `male` / `female` / `other` / `prefer_not_to_say` |
| `height_cm` | str (max 10) | 50.0 – 280.0 |
| `weight_kg` | str (max 10) | 20.0 – 500.0 → además crea un `BodyMetricLog` |
| `target_weight_kg` | str (max 10) | 20.0 – 500.0 |
| `baseline_activity_level` | str (max 20) | `sedentary` / `light` / `moderate` / `active` / `very_active` (default `moderate`) |
| `dietary_restrictions` | str (max 500) | Split por comas, strip, 100 chars por ítem, máx 20 ítems |

Todos los campos son opcionales/best-effort (`try/except ValueError: pass`). El flujo termina
con `onboarding_completed = True` y redirige a `/`. Los "4 pasos" son navegación de cliente
(Alpine); el POST es uno solo.

### C. Deuda del sistema de diseño

- **12 paletas paralelas** en uso: `slate` (545 usos), `rose` (65), `indigo` (53),
  `emerald` (45), `amber` (35), `blue` (24), `violet` (23), + `pink`/`green`/`teal`/
  `orange`/`sky` como one-offs.
- **53 usos de `indigo-*` crudo** en 14 archivos conviviendo con 170 de `brand-*` —
  incluido el CTA principal del flujo core (`capture/preview_partial.html:95`).
- **Sin escala de radios**: `rounded-xl` (57), `rounded-lg` (55), `rounded-2xl` (54),
  `rounded-full` (51) usados indistintamente para el mismo tipo de objeto.
- **Dos lenguajes de tarjeta** conviviendo: `rounded-2xl border-slate-200` vs
  `rounded-xl border-slate-100 shadow-sm`.
- **91 SVG inline copiados y pegados, 0 macros de Jinja.**
- **Emoji como iconografía primaria** en 6 pantallas (history, notifications,
  meals/detail, pantry/movements, errors/500, capture/confirm_result) mientras el resto
  usa Heroicons.
- **La paleta `brand` vive solo inline en `base.html:13-36`**; las 3 páginas standalone
  (login, 404, 500) tienen configs divergentes o ninguna.
- **Modo oscuro: 0 soporte** (0 clases `dark:`, sin `darkMode` en config, sin
  `prefers-color-scheme`, sin toggle).
- **`components/empty_state.html` se usa en solo 3 de 9 lugares** que tienen empty state.
- **`capture/preview.html` (254 líneas) es un archivo huérfano** — el real es
  `preview_partial.html`.

### D. i18n y accesibilidad

- `health/detail.html` tiene **cero** llamadas a `_()`; `suggestions/partials/dismissed.html`
  tiene texto hardcodeado.
- Valores de enum de la DB se muestran con `|title` sin traducir (`movement_type`,
  `meal_type`, `workout_type`, `intent_type`, `status`, `category`).
- Plurales hardcodeados (`participant(s)`, `item(s)`), unidades sin traducir, y
  `_('✓ Confirm & Save')` mete un glifo dentro del msgid.
- Botones icon-only sin `aria-label` (✓/✕ de notificaciones, submit de `stock_grid`,
  cerrar de flash, back links).
- Inputs sin label (filtro de fecha de workouts, cantidad y select de `stock_grid`).
- Grupos de tabs sin `role="tab"`/`aria-selected`.
- Ningún fragmento HTMX con `role="status"`/`aria-live`.
- `errors/500.html` sin meta viewport.

### E. Sin red de seguridad

`tests/` tiene 9 módulos, todos unit tests de servicio/repositorio. El fixture `client` de
`tests/conftest.py:51` **nunca se usa**: hay **cero** tests de la capa web. Un refactor de
este tamaño sin tests de humo es imprudente.

### F. La capa de inteligencia

> **Este bloque es una foto del arranque de v3, no del estado actual.** Los números y las
> líneas citadas describen lo que había cuando se escribió el plan; lo que ya se arregló
> queda marcado en las sub-fases de abajo. En particular, desde la 4.1 los jobs corren una
> o dos veces por día a hora local fija, así que las cuentas de F2 que se apoyan en el
> intervalo de 6 horas ("~84 notificaciones", "cada 6h se escriben hasta 5 filas") hay que
> leerlas como el diagnóstico original: hoy serían ~21 y dos veces por día. Peor todavía
> para el punto que hace F2: con el job una vez al día, **los tres cooldowns por categoría
> (6h, 48h, 72h) son más cortos que el período de su propio job y ya no suprimen nada.**

El diagnóstico honesto: **hay reglas, pero no razonamiento; hay feedback, pero no
aprendizaje; hay trabajos programados, pero no sentido del tiempo.** Todo el motor es
100% determinista (`grep -r openai app/recommendations/` → 0 resultados) y su vocabulario
son ~35 plantillas de texto fijas, de las cuales 22 son cadenas congeladas de marcadores
de sangre.

#### F1. No sabe *cuándo* (el problema de oportunidad)

- Los 4 jobs son `IntervalTrigger` (`app/jobs/scheduler.py:37,43,49,55`); **no hay un solo
  `CronTrigger` en el repo.** `IntervalTrigger` sin `start_date` dispara la primera vez en
  `now + interval`, así que el reloj queda anclado al arranque del proceso: un reinicio a
  las 03:00 hace que todo dispare a las 03:00 para siempre.
- **Sin horario de silencio.** Nada impide una notificación a las 4am.
- `settings.timezone` (`America/Argentina/Buenos_Aires`) se usa solo en
  `BackgroundScheduler(timezone=...)`, que sin cron no tiene efecto observable.
  `Household.timezone` existe y **no se lee en ningún lado.**
- La ventana de comida se calcula en **UTC** (`meal_generator.py:35`), 3h corridas: a las
  08:00 locales el motor cree que es "lunch".
- Mezcla de datetimes naive y aware: `datetime.utcnow()` en repositorios vs
  `datetime.now(timezone.utc)` en servicios, y `.replace(tzinfo=utc)` sobre columnas
  `timestamptz` (`notification_jobs.py:131`) — que **afirma** UTC en lugar de convertir.
- `notification_job_interval_minutes` está en config, en el README y en `.env.example`, y
  **no lo lee ningún código.**

#### F2. Repite en vez de recordar (el problema de ruido)

- El único control es `has_recent_by_category` (`notification_repo.py:69-93`): filtra **por
  categoría**, no por sujeto. El cooldown de `low_stock` (6h) es igual al intervalo del job
  (6h) → **la leche baja hace 3 semanas produce ~84 notificaciones**, cada una con el cuerpo
  recalculado, sin memoria de que ya se avisó.
- **Bug real** en ese mismo query: el `or_` matchea la cláusula de household aun cuando se
  pasa `user_id`. Como los recordatorios per-user también setean `household_id`, **la
  notificación de un miembro suprime la del otro** durante toda la ventana.
- Sin expiración, sin poda, sin límite diario. `get_unread_count`
  (`notification_repo.py:39-48`) trae **todas** las filas a Python y hace `len()` — y corre
  en cada carga de página sobre una tabla que crece sin techo.
- En el motor: `generate_for_user` nunca chequea si ya existe una sugerencia pendiente con
  el mismo contenido. La penalización de diversidad (−0.20, `scorer.py:150-161`) es un
  **ajuste de score, no un filtro**: un candidato de confianza 0.95 baja a 0.75 y sigue
  saliendo primero. Cada 6h se escriben hasta 5 filas nuevas.

#### F3. Las notificaciones son callejones sin salida

`Notification` **ya tiene** `related_entity_type` y `related_entity_id`
(`app/models/notification.py:32-33`), `NotificationCreate` los acepta
(`schemas/notification.py:33-34`) — y los tres jobs los omiten, `NotificationRead` no los
expone, y la plantilla renderiza título+cuerpo sin ningún ancla. El usuario recibe
"Running low on: leche, huevos" y **no tiene a dónde tocar**.

#### F4. El aprendizaje está keyeado al string equivocado

- Lo que se guarda como entidad aprendida es **el título renderizado**:
  `suggestion.title.lower()[:200]` (`suggestion_service.py:52`).
- El scorer matchea eso por bolsa-de-palabras sobre `title + text + rationale`
  (`scorer.py:46-59`), con umbral de solape 0.3. Consecuencia: rechazar
  *"Time to get moving!"* suprime cualquier candidato futuro que comparta 60% de esos
  tokens, **cruzando categorías**.
- `snoozed` guarda `value=0.0` → no entra ni en la lista positiva ni en la negativa:
  **escrito y jamás leído.** `dismissed` guarda `-0.3` y cae en la lista negativa por el
  `or float(s.value) < 0` (`scorer.py:109`), así que **"posponer" actúa como rechazo.**
- El motivo de texto libre ("no es para nosotros") va a `notes`, y `context_json` también:
  **ambos escritos, nunca leídos.**
- `RecommendationEngine.record_feedback` (`engine.py:159-216`) es **código muerto, cero
  llamadores**.
- `repeated_meal_choice` se escribe con `value=1.0` por ítem por comida, así que los
  alimentos de todos los días acumulan decenas de señales positivas y **dominan el boost de
  forma monótona: el loop empuja a repetir, no a variar.**
- `repeated_purchase` está en la lista positiva del scorer (`scorer.py:98`) y **ningún
  código lo escribe nunca.**

Constantes del scorer, para referencia: `_POSITIVE_SIGNAL_BOOST = 0.12`,
`_NEGATIVE_SIGNAL_PENALTY = 0.15`, `_DIVERSITY_PENALTY = 0.20`,
`_RECENT_SUGGESTION_DAYS = 7`, `_RECENT_SIGNAL_DAYS = 30`.
`final_score = clamp(confidence + Σboosts − Σpenalties − diversity, 0, 1)`.

#### F5. La explicación es decorativa

`rationale` — lo que se muestra bajo *"¿Por qué esta sugerencia?"*
(`suggestions/index.html:58-63`) — es un **string estático por regla**, idéntico para todos
los candidatos de esa regla: `"Dietary variety supports micronutrient balance."`,
`"General wellness activity recommendation."`. Nada explica el score: qué señal lo subió o
lo bajó vive solo en `logger.debug`. La app dice por qué, pero no es la razón real.

#### F6. Ignora casi todos los datos que ya tiene

| Dato | Estado |
|---|---|
| `BodyMetricLog` completo (peso, grasa, cintura, **sueño**) | modelo entero **sin leer** por el motor; cero análisis de tendencia |
| Macros y calorías | los 30 alimentos del seed **sí** tienen kcal/proteína/carbos/grasa/fibra, y **nada los agrega nunca** a una comida |
| `WorkoutExercise.perceived_effort` (RPE), `sets/reps/load_kg` | sin leer |
| `MealParticipant.hunger_before` / `satiety_after` | sin leer |
| `ExerciseType` (20 ejercicios sembrados, con grupo muscular e intensidad) | ignorado: el motor usa una lista **hardcodeada de 8 actividades** (`activity_generator.py:41-50`) |
| `_MUSCLE_RECOVERY` (ventanas de recuperación) | se usa solo como lista de claves, nunca como ventana temporal; el músculo sugerido sale de `sorted(...)[0]` → **casi siempre "back"** |
| `BloodAnalysis.analysis_date` | **la antigüedad nunca se chequea**: un panel de hace 3 años dicta consejos hoy |
| `user.recommendation_context_json`, `goals_json`, `target_weight_kg`, `height_cm`, `birth_date`, `sex` | sin leer |
| `typical_shelf_days`, `serving_size_g`, `micro_json`, `macro_json` | columnas que **ningún código escribe jamás** |
| Vencimientos de pantry | **no existe columna de vencimiento** en todo el esquema |
| `generate_for_household` | **cero llamadores** → todo el generador de pantry/compras nunca llega al usuario |
| `pantry_generator` | idem: sin callers |
| `BloodAnalysis.ai_summary` | siempre `None` |

#### F7. Dos cosas que además dañan la confianza

1. **Consejo médico directivo sin control.** El generador de sangre emite 22 cadenas fijas
   del tipo *"Your LDL (bad cholesterol) is high. Reduce butter, fatty meats…"* sin
   disclaimer y sin chequear antigüedad del panel. Y peor: `_infer_category`
   (`filters.py:84-97`) devuelve `None` para `category="habit"`, así que **esas sugerencias
   eluden los dos filtros de seguridad** (restricciones alimentarias y señales negativas).
   Además `filters.py` tiene un atajo `return candidates` que saltea el filtro cuando no hay
   bloqueos de alimentos ni de actividades.
2. **La Capa 2 del NLP está rota justo para comidas.** El schema de la función pide
   `items_per_user[user][].name` (`openai_adapter.py:88`) y el deserializador construye
   `FoodItemRef(name=...)` (`:195-199`), pero el campo del modelo es **`food_name`**
   (`app/nlp/intents.py:36`) sin alias. Todo `log_meal` producido por el LLM lanza
   `ValidationError`, se descarta en silencio (`:304-306`) y cae a Capa 1. **El tipo de
   captura más frecuente nunca se beneficia del LLM** — y la UI no da ninguna señal.

---

## Plan de implementación

### Fase 1 — Que funcione y que guíe

Nada nuevo de diseño: arreglar lo roto y desbloquear el onboarding. Es la fase que hace que
la app **cumpla lo que ya promete**.

**1.1 — Rutas rotas (los 4 `hx-*`/`action` mal apuntados)**

Se redirigen a las rutas **web** que ya existen y ya devuelven parciales HTML, en vez de a
las rutas `/api/v1` que devuelven JSON — así se respeta la regla de enrutamiento dual sin
tocar el backend donde no hace falta.

- [ ] `home.html:226` → `POST /suggestions/{id}/feedback` con `hx-vals` `{"status": "dismissed"}`,
      target la tarjeta, swap por `suggestions/partials/dismissed.html`.
- [ ] `suggestions/index.html:12` → el "Refresh" pasa a `hx-post` contra una ruta web nueva
      que regenera y devuelve la lista, en vez de un `<form>` que navega a JSON.
- [ ] `pantry/partials/stock_grid.html:52` → nueva ruta web `POST /pantry/{stock_id}/adjust`
      que devuelve la tarjeta del ítem como fragmento.
- [ ] `base.html:172,180,271` → nueva ruta web `GET /notifications/badge` que devuelve el
      badge como fragmento HTML (no JSON).

**1.2 — Dead code del layout**

- [ ] `base.html:65`: quitar `x-data="appStore()"` (usar `x-data` vacío + `$store.app` donde
      haga falta). Elimina el error de Alpine en cada carga.
- [ ] `base.html:214`: reemplazar `get_flashed_messages()` por un mecanismo propio — los
      mensajes viajan en el contexto de plantilla, inyectados desde
      `get_template_context()` (`app/web/helpers.py:50-57`), que es el único punto de
      inyección global que existe.
- [ ] `app/static/css/app.css`: regla `[x-cloak]{display:none!important}` y clase
      `.scrollbar-hide`.

**1.3 — Onboarding (la feature huérfana)**

- [ ] Nuevo `app/templates/onboarding/index.html`: wizard de 4 pasos con navegación Alpine y
      **un solo submit** contra `POST /onboarding/complete`, respetando los nombres de campo
      y rangos de la tabla de la sección B.
- [ ] Registrar el router en `app/web/router.py` (`prefix="/onboarding"`).
- [ ] Gate: redirigir a `/onboarding/` cuando `onboarding_completed` es falso.
- [ ] **Sin migración** — la columna ya está en `0002`.

**1.4 — Prefill e includes**

- [ ] `capture/index.html:167-170`: asignar `prefillMap[prefill]` de verdad. Desbloquea las
      6 quick actions del Home.
- [ ] `history/index.html:45`: pasar la variable correcta al include de `workout_card.html`.

**1.5 — Login**

- [ ] `login.html:11-18`: unificar la config de Tailwind (queda resuelto de raíz en la Fase 2
      con el shell compartido; acá basta con que la paleta `brand` esté presente).
- [ ] `app/web/auth.py`: pasar `email_value` al re-render tras login fallido.
- [ ] Quitar las promesas muertas: `href="#"` de "Forgot password?" y el checkbox `remember`
      que el backend ignora.

**1.6 — Tres bugs de inteligencia que son bugs, no diseño**

- [ ] `meal_generator.py:35`: la ventana de comida se calcula en hora local, no UTC.
- [ ] `notification_repo.py:78-82`: arreglar el `or_` que hace que la notificación de un
      miembro suprima la del otro.
- [ ] `openai_adapter.py:195-199`: el desajuste `name` / `food_name` que rompe la Capa 2 del
      NLP para comidas.

**Archivos:** `app/templates/{base,home}.html`, `capture/index.html`, `history/index.html`,
`pantry/partials/stock_grid.html`, `suggestions/index.html`, `auth/login.html`,
`app/static/{css/app.css,js/app.js}`, `app/web/{router,auth,notifications,pantry,suggestions}.py`,
`app/repositories/notification_repo.py`, `app/recommendations/generators/meal_generator.py`,
`app/nlp/providers/openai_adapter.py`, nuevo `app/templates/onboarding/index.html`.

---

### Fase 2 — Fundación del sistema de diseño

Sin cambios visuales masivos todavía: se construye la infraestructura de la que vive la
Fase 3.

- [x] **Capa de tokens** en `app/static/css/app.css`: 8 neutrales (`--surface`, `--card`,
      `--card-alt`, `--line`, `--line-strong`, `--ink`, `--ink-muted`, `--ink-subtle`), la
      escala `brand` de 10 pasos, 4 familias de acento de dominio (food / move / body /
      stock) y 4 de estado (ok / warn / danger / info), cada una con `soft` / `ink` /
      `solid`. Se referencian desde la config de Tailwind como
      `rgb(var(--x) / <alpha-value>)`, de modo que **el modo oscuro es un swap de variables,
      no un barrido de clases `dark:`**. Elimina también los hex hardcodeados de `app.css`
      (scrollbar, toasts, `recordPulse`) y la regla dañina `nav a.nav-active`.
      **Desvío del plan:** *no* hay un bloque `@media (prefers-color-scheme: dark)`
      duplicado. El script síncrono de `shell.html` **siempre** estampa `data-theme` (de
      `localStorage`, y si no hay nada, de la preferencia del sistema) y el servidor
      renderiza `data-theme="light"` por default, así que no existe un estado sin tema:
      el media query solo agregaría un segundo lugar donde editar los mismos 40 valores.
- [x] **`app/templates/layouts/shell.html`**: un `<head>` canónico único (meta, viewport,
      theme-color por esquema, **una** config de Tailwind, Inter, HTMX/Alpine/Chart,
      `app.css`, y el script de inicialización de tema **antes** del render para evitar el
      flash). `base.html`, `auth/login.html`, `errors/404.html` y `errors/500.html` lo
      extienden. Es necesario porque los handlers de error pasan solo `{"request": request}`
      (`app/main.py`), que es la razón por la que hoy son documentos HTML sueltos con configs
      divergentes. `darkMode: 'class'` (no la forma de array, que exige Tailwind ≥3.4.1 y
      rompería toda la config en silencio sobre un build viejo del CDN), y el script setea
      tanto `data-theme` como la clase `dark`.
- [x] **`app/templates/components/icons.html`** con macros `icon(name, class, stroke)` y
      `spinner(class)`. Los paths **no** se escribieron de memoria: salieron de un script
      sobre `app/templates/**/*.html`, que encontró **44 cuerpos distintos entre 95 SVG
      inline** — varios eran copias *truncadas* del mismo ícono (el rayo de workouts sin su
      mitad inferior, la caja de pantry cortada al medio). Un nombre desconocido renderiza
      el glifo `question`, así que si algo se rompe, se ve.
- [x] **`app/templates/components/ui.html`** — los primeros macros del proyecto:
      `card`, `card_link`, `section_title`, `page_header`, `stat`, `badge`, `dot`,
      `progress`, `btn`, `avatar`, `attribution`, `tabs`, `notice`, `field`, `field_wrap`,
      `empty_state`, `theme_toggle`.
- [x] Toggle de tema en `app/static/js/app.js` (detección de sistema + persistencia en
      `localStorage`, con try/catch), por delegación sobre `[data-theme-toggle]` para que
      funcione también dentro de un fragmento que llega por HTMX. Chart.js se tinta desde
      los mismos tokens vía `tokenColor()` y `applyChartTheme()`, que re-tinta los gráficos
      ya montados con `Chart.getChart(canvas)`.
- [x] Escala de radios con nombre por tipo de objeto (`card` / `control` / `chip` / `pill`)
      y **una sola** gramática de tarjeta, en el macro `card`.
      **Alcance real:** consolidar las 12 paletas paralelas de las 29 páginas es el barrido
      de la Fase 3. La Fase 2 construye la maquinaria y migra el chrome, el login y las
      páginas de error — **el modo oscuro está completo solo al terminar la Fase 3.**
- [x] Borrar el huérfano `app/templates/capture/preview.html` (254 líneas; el que se usa es
      `preview_partial.html`).
- [x] **Los macros como globals del entorno**, no como `{% import %}` por plantilla
      (`app/web/helpers.py`). `{% import %}` **no se hereda**: un import en `base.html` no
      existe dentro de los bloques de los hijos, así que sin esto las 29 plantillas repiten
      las mismas dos líneas y una olvidada es un `UndefinedError` en tiempo de request, no
      en el arranque. Como globals, `ui.*` y `ic.*` resuelven también en los parciales de
      HTMX y en las páginas de error, cuyo contexto es solo `{"request": request}`.
- [x] **`tests/test_components.py`** (46 tests): cada macro se rinde una vez con sus
      defaults. Existe porque la revisión encontró que el docstring de `ui.html` documentaba
      una firma de `tabs()` que el macro nunca tuvo, y nada lo atrapó: **ningún test rendía
      un solo macro**, y 14 de los 18 no tenían ni un caller cuando se escribieron. Incluye
      el test que ata los tonos que los macros aceptan (`bg-{tone}-soft` y compañía, armadas
      por concatenación) a las familias que `app.css` define de verdad — un tono inexistente
      rinde una clase que Tailwind no genera y el elemento sale transparente **sin ningún
      error**.
- [x] **Validar `avatar_color` en la escritura** (`app/schemas/user.py`). `ui.avatar()` lo
      rinde dentro de un `style="background-color: …"`: el autoescape de Jinja impide salir
      del atributo pero **no** impide inyectar propiedades CSS extra, y la app no tiene
      ninguna CSP que sirva de segunda línea. El patrón va en `UserCreate`/`UserUpdate` y no
      en `UserBase`, para que `UserRead` no explote leyendo una fila vieja fuera de forma.

Además, dos piezas de código muerto verificadas por `grep`, borradas porque estaban en
archivos que la fase reescribía de punta a punta:

- `window.VoiceRecorder` (70 líneas de grabación de audio en `app.js`, **cero llamadores**:
  la grabación real la implementa `capture/index.html` en su propio componente de Alpine,
  que es el que habla con `/capture/transcribe`).
- `<div id="toast-container">` en `base.html` (nadie escribía nunca dentro; `app.js` crea el
  suyo).

`#htmx-indicator` en cambio **se conservó y se cableó**. De los once `hx-indicator` de la
app solo uno lo nombra (el "Refresh" de `suggestions/index.html:18`); los otros diez
apuntan a spinners locales, así que en el resto de las pantallas la barra era markup
muerto: la única regla que la mostraba, `.htmx-request .htmx-indicator`, exige un
**ancestro** con `.htmx-request`, y el div vive al tope del `<body>`. Ahora `app.js` la
marca a sí misma contando requests en vuelo (`htmx:beforeRequest` / `htmx:afterRequest`,
con un contador para que dos pedidos superpuestos no la apaguen antes de tiempo), y
`app.css` agrega `.htmx-request.htmx-indicator` para que los dos caminos la muestren
igual. No es una barra de porcentaje: XHR no reporta progreso de una respuesta chunked,
así que es indeterminada (`@keyframes indeterminate` sobre `transform: scaleX`, con
`origin-left`), y bajo `prefers-reduced-motion: reduce` se queda quieta en `scaleX(1)`
en vez de animarse.

Un cambio de comportamiento, deliberado: `htmx.config.selfRequestsOnly` estaba forzado a
`false` en el script inline de la v1. La reescritura **no lo sobrescribe**, así que aplica
el default de HTMX (`true`). Ningún `hx-*` de la app apunta a otro origen — ya hay un test
que lo afirma — y la config ahora vive donde las páginas standalone también la ven.

---

### Fase 3 — Barrido visual + rediseño de IA

- [ ] Las 29 plantillas pasan a macros y tokens (patrón repetido; las que no se rediseñan
      conservan su layout). El barrido va por área, un commit por área:
  - [x] `dashboard`
  - [x] `home`
  - [x] `meals` (index + detalle + tarjeta + parcial de lista)
  - [ ] `workouts`, `pantry`, `health` + `body_metrics`, `history`, `notifications`,
        `suggestions`, `onboarding`, `profile`
- [x] **Home** — rediseño de arquitectura de información: de "lista de tarjetas" a una
      jerarquía con un **estado de hoy** arriba, la acción primaria dominante, y las
      sugerencias accionables en un toque.
- [ ] **Los enums de la base se muestran desde un solo lugar**, `components/domain.html`
      (`dm`, tercer global de Jinja junto a `ui` e `ic`). `{{ _(x|title) }}` le pide al
      catálogo un msgid que `pybabel extract` no puede encontrar, así que los valores
      salían siempre en inglés, y cada pantalla repetía su propio mapa de tonos. Hecho para
      `meal_type` y `context`; faltan `workout_type`, `movement_type`, `intent_type`,
      `status` y `category`.
- [ ] **Cada filtro de pantalla es un `<form method="get">` real**, no un espejo del estado
      en Alpine: funciona sin JS, HTMX intercambia solo la lista, `hx-push-url` mantiene la
      URL compartible y el chip activo se pinta con `peer-checked` desde el valor de la
      query. Los parámetros vacíos que manda un formulario (`?user_id=`) se parsean con
      `query_int`/`query_date` de `app/web/helpers.py`, porque con `int | None` FastAPI
      responde 422 a su propia URL. Hecho en `meals`; falta en `workouts` (que además tiene
      el filtro de fecha sin label) y en `pantry`.
- [ ] **Capture** — el flujo core:
  - entrada más prominente,
  - chips de ejemplo con lenguaje natural real (hoy son plantillas con `[placeholders]`),
  - **preview de confirmación rediseñado**: atribución correcta (avatar + nombre por
    intent), leyenda que explica qué significa el % de confianza y qué pasa al confirmar,
    eliminación del estado `editing` muerto, y la rama `{% else %}` que hoy vuelca
    `{{ intent | tojson }}` crudo al usuario reemplazada por un fallback legible.
  - Edición inline completa **fuera de v3** (requiere cambiar el contrato del backend).
- [ ] **Dashboard** — Chart.js con los tokens y colores que funcionen en ambos temas, y
      **arreglar el defecto 14**: los cuatro `new Chart(...)` corren inline en tiempo de
      parseo contra un Chart.js `defer`, así que hoy tiran `Chart is not defined` y no se
      dibuja ninguno. Van dentro de un `DOMContentLoaded` (o de un `<script defer>`), que
      es además el orden que necesitan para leer los tokens vía `GP.tokenColor`.
- [ ] `components/empty_state.html` en los ~9 lugares que lo necesitan, no en 3.
- [ ] `errors/404.html` y `errors/500.html` pasan a ser páginas reales de la app.

---

### Fase 4 — Inteligencia oportuna, con memoria y explicable

Cinco cambios, en este orden. **Ninguno introduce un LLM en el camino de recomendación.**

**4.1 — Un solo reloj local**

- [x] Nuevo `app/core/clock.py` con `household_tz()`, `local_now()`, `local_today()` y
      `to_local()`. **Se adelantó a la Fase 2**: `home.html` ya llamaba a `now()`, un global
      de Jinja que nadie había registrado, así que el guardia `{% if now is defined %}` caía
      siempre al `else` y el saludo decía "buenas tardes" a las 7 de la mañana. Un
      `TIMEZONE` mal escrito degrada a UTC con un warning en vez de tumbar cada página.
      `to_local()` **interpreta** un naive como UTC y convierte, en vez de afirmarle la
      timezone local encima — que es el bug que hoy tiene `notification_jobs.py:131`.
- [x] `is_quiet_hours()` sobre ese mismo módulo, más `as_utc()` y `local_day_bounds()`.
      La franja se define por horas enteras locales (`QUIET_HOURS_START` /
      `QUIET_HOURS_END`) y **cruza la medianoche** cuando el inicio es posterior al fin,
      que es el caso normal (22 → 8); con inicio igual a fin no hay silencio, que es la
      forma de apagarlo sin agregar un flag aparte.
- [x] `scheduler.py` pasa de `IntervalTrigger` a `CronTrigger` en la timezone configurada
      (stock a la mañana, inactividad al mediodía, peso al arrancar el día), de modo que la
      hora deje de depender de cuándo arrancó el proceso. Los cuatro horarios viven en una
      sola tabla, `_SCHEDULE`, con el motivo de cada hora escrito al lado.
- [x] Gate de horario de silencio antes de crear cualquier notificación. Va en la puerta de
      los tres jobs y **no** dentro de `NotificationService.create`, porque esos tres son
      sus únicos llamadores: `app/web/` y `app/api/` solo leen, marcan, descartan y
      posponen. El job se saltea entero en vez de postergarse — `CronTrigger` lo vuelve a
      llamar mañana a la misma hora local.
- [x] Unificar naive/aware en repositorios y servicios: `datetime.utcnow()` y
      `.replace(tzinfo=utc)` (que **afirma** en vez de convertir) salen de
      `notification_jobs`, `notification_repo`, `body_metric_repo`, `workout_repo`,
      `pantry_repo` y `suggestion_repo`, reemplazados por `datetime.now(UTC)` y `as_utc()`.
- [x] **"Un día" todavía es un día UTC, y ahora se ve en pantalla.** Detectado durante el
      rediseño de las comidas (Fase 3): `MealService.get_today_meals` pasa `date.today()`,
      que es la fecha del *reloj del proceso*, y `MealRepository.get_household_meals` arma
      los límites del día con `datetime.combine(...)` **naive** para compararlos contra una
      columna `timestamptz`. Con el proceso en UTC, una comida de las 22:00 de acá cae en el
      "hoy" de mañana: el contador del Home la pierde y el filtro por día de `/meals` —
      que hereda esos mismos límites vía `on_date` — la muestra en el día equivocado.
      Se arregla acá y no en la Fase 3 porque el arreglo es el reloj (`local_today()` +
      límites aware convertidos a UTC), no la plantilla. Alcanza también a
      `get_today_workouts` y al resto de los `get_today_*`. Arreglado con
      `local_day_bounds()` en `meal_repo` y `workout_repo`, `local_today()` en
      `meal_service`, `workout_service` y las tres fechas de `dashboard_service`, y
      `to_local()` sobre el día de cada sesión del calendario de días activos, que pintaba
      el cuadradito del día siguiente. De paso, `get_today_sessions` pasa `end_date`: sin
      él "hoy" era "desde el arranque de hoy en adelante" y una sesión con fecha futura
      contaba como de hoy. Último `date.today()` vivo de la app: el año máximo del año de
      nacimiento en `onboarding.py`, que el 31 de diciembre a la noche ofrecía un año que
      todavía no había empezado acá.
- [x] **Los tres cooldowns por categoría quedaron inertes** — consecuencia del cambio de
      trigger que no estaba en el plan. `low_stock` mira 6h, `inactivity` 48h y
      `metric_reminder` 72h, pero entre dos corridas de su propio job ahora pasan 24h, así
      que ninguno puede suprimir nada. No se toca acá: el chequeo que hace falta es por
      sujeto y lo trae la 4.2. Queda anotado en el código para que nadie lea esas ventanas
      como una defensa activa.
- [x] Conectar `notification_job_interval_minutes` o eliminarlo del config, del README y de
      `.env.example`. **Eliminados los dos**, junto con `suggestion_job_interval_minutes`:
      al primero no lo leía ningún código y al segundo solo el trigger que reemplazamos.
      Una perilla que ya no controla nada es la misma clase de mentira que vino a sacar esta
      sub-fase. En su lugar `.env.example` y el README documentan `QUIET_HOURS_START` /
      `QUIET_HOURS_END`, y la descripción de `TIMEZONE` deja de ser decorativa.
- [x] `tests/test_clock.py`: 29 tests sobre la franja que cruza la medianoche, la diferencia
      entre convertir y afirmar, los límites del día local, que la hora de los cuatro jobs no
      dependa del arranque del proceso, que un job silenciado **no abra ni la sesión** de
      base y — el control negativo, sin el cual una app que no vuelve a avisar nada pasa la
      suite entera — que fuera de la franja el job sí escriba su notificación. El módulo fija
      `TIMEZONE` y la franja en vez de heredarlas del entorno: varias horas están escritas a
      mano y con otra zona fallaban por el `.env` y no por el código. Un detalle que costó
      encontrar: en SQLite, `DateTime(timezone=True)` descarta la tzinfo y guarda la hora de
      pared tal cual, así que el test del día tiene que escribir lo que escribe la app — el
      instante en UTC — o no detecta la regresión.
- [x] **Lo que la revisión encontró que el barrido había salteado.** El rótulo de la serie de
      peso hacía `timestamp.strftime(...)` sobre la columna cruda en `dashboard_service` y en
      `body_metric_service`: un pesaje de las 22:00 salía rotulado con la fecha de mañana, y
      el rótulo cambiaba según el motor de base — lo mismo que el diff arreglaba 57 líneas más
      abajo en el mismo archivo. Además, los tres `log_*` guardaban `data.timestamp` tal cual,
      así que un POST a la API con offset propio quedaba guardado con la hora de pared de
      *esa* zona: ahora pasan por `as_utc()`, que es de lo que dependen los límites del día.
      `meal_generator` dejó de armar la zona a mano (era el último lector de
      `settings.timezone` fuera del reloj, y con un `TIMEZONE` mal escrito explotaba en vez de
      degradar), y `scheduler` pasa `household_tz()` al trigger y al scheduler por la misma
      razón. De paso: el tercer elemento de `_SCHEDULE` era prosa que ningún código leía —
      la razón de cada hora vive en el comentario de su entrada — y los tres
      `user_repo = UserRepository(db)` de `notification_jobs` no los usaba nadie.
      Queda anotado como advisory, sin acción: en una zona cuyo cambio de horario cae a las
      24:00 (Chile), la hora repetida no pertenece a ningún día local y `local_day_bounds`
      deja un hueco. Buenos Aires no tiene DST desde 2009.

**4.2 — Dedup por sujeto, con escalada en vez de repetición**

- [x] Los jobs pasan a poblar `related_entity_type`/`related_entity_id` (columnas que **ya
      existen**). El sujeto de `low_stock` es `("pantry_stock", stock.id)`; el de
      `inactivity` y `metric_reminder`, `("user", user.id)`.
- [x] Nuevo `has_recent_for_subject` reemplaza el chequeo por categoría: se avisa **una vez
      por sujeto**, y se vuelve a avisar solo si **empeora** (stock que baja más, inactividad
      que pasa de 4 a 8 días). El sujeto es la terna
      `(category, related_entity_type, related_entity_id)` dentro del alcance del
      destinatario. **La categoría entra en la clave** porque el sujeto de la inactividad y
      el del recordatorio de pesaje son los dos la misma persona: sin ella, avisarle de una
      le taparía la otra. **No mira `dismissed_at`**: descartar es "lo vi", no "avisame de
      nuevo mañana". La ventana de 7 días es un **techo al silencio**, no el mecanismo: un
      faltante que sigue ahí a la semana siguiente vuelve a aparecer una vez.
- [x] **`priority` es la marca de agua de la escalada.** La 4.4 es la única sub-fase con
      migración, así que acá no se agrega una columna: se compara la severidad que el estado
      da hoy contra el `priority` con el que se avisó. No es un préstamo forzado — una
      despensa más vacía o una inactividad más larga **son** más urgentes, que es lo que la
      columna significa, y es lo único comparable que hay en la tabla. La escalada es en un
      solo sentido: si el estado mejora, la severidad baja y eso no es novedad.
      **Límite conocido, anotado en el docstring:** la marca de agua es la **más alta de la
      ventana**, no la del último aviso, y no se reinicia cuando el sujeto se recupera — si la
      leche se acabó el lunes (9), se repuso el martes y se acabó otra vez el miércoles, el
      miércoles no se habla, porque la fila del lunes sigue adentro de la ventana con prioridad
      9. El precio es un faltante repetido que espera hasta el fin de la ventana. Reiniciarla
      pide retirar el aviso abierto cuando el sujeto sale del conjunto, o sea el ciclo de vida
      por sujeto: queda para la 4.3, donde ese ciclo ya vive, en vez de entrar de contrabando
      en un checkpoint.
- [x] **`_addressee_scope()`: los dos alcances no se combinan con `or_`.** Un aviso per-user
      lleva también `household_id`, así que el `or_` que había hacía que la fila de Diego
      contara como la de Rocío y le tapara el aviso durante toda la ventana. Es la regla 4 de
      `AGENTS.md` — filtrar por el `user_id` que actúa incluso entre convivientes — y estaba
      anotada en la 4.1 como el bug que la 4.2 tenía que cerrar.
      En la rama per-user `household_id` **no se usa a propósito**: `user_id` ya es el filtro
      más estricto, y como la columna es nullable, agregarla haría que una fila per-user sin
      hogar dejara de deduplicarse en silencio.
- [x] **La misma regla 4, del lado de la lectura: `_visible_to()`.** El `or_` no estaba solo en
      el chequeo de dedup — las **cuatro** consultas de lectura (`get_for_user`,
      `get_category_counts`, `get_unread_count`, `mark_all_read`) usaban
      `or_(user_id == yo, household_id == mío)` a secas, y un aviso dirigido a una persona lleva
      **también** `household_id`. O sea: el globito del nav le contaba el aviso del otro en cada
      carga de página, `/notifications` se lo listaba **con el cuerpo entero** — que desde esta
      sub-fase dice cuántos días lleva sin pesarse o sin entrenar —, y su "marcar todo como
      leído" se lo escribía; y después `_get_owned` le rechazaba el clic para descartarlo, así
      que lo veía y no podía sacárselo. La condición correcta es lo propio **más** lo del hogar,
      que es exactamente lo que `NotificationService._get_owned` ya aplicaba fila por fila:
      ahora vive en un solo lugar y las cuatro la usan. `_addressee_scope` (escribir/deduplicar)
      y `_visible_to` (leer) son **dos predicados distintos**, y confundirlos era el mismo bug a
      los dos lados.
- [x] **Ningún centinela llega al texto que lee una persona.** Las dos ramas "nunca registró
      nada" dejaban `days_since` en el umbral para poder comparar, y ese número entraba en el
      título y en el cuerpo: a alguien que nunca entrenó — una cuenta creada ayer, por ejemplo —
      la app le anunciaba *"No workouts logged in 4 days"*, y al que nunca se pesó, *"you
      haven't logged your weight in 3 days"*. Ahora `days_since` es `int | None` y el texto se
      bifurca: sin historial no hay número que informar (*"No workouts logged yet"*), y la
      severidad se queda en el escalón base porque no hay antigüedad que escalar.
- [x] **`low_stock` deja de ser un resumen y pasa a ser una notificación por ítem.** Era una
      sola fila por hogar con `"Running low on: leche, huevos"`: no hay sujeto que deduplicar
      ni nada a lo que llevar el toque de la 4.3, y el cuerpo se recalculaba cada corrida. Un
      aviso por ítem es lo que hace posibles las dos cosas. Para que la primera corrida contra
      una despensa recién cargada no sea una pared, `_MAX_NEW_STOCK_ALERTS = 5` por corrida y
      `_most_urgent_first()` reparte por severidad — explícito, porque si el orden lo decidiera
      el motor (regla 5 de `AGENTS.md`) los dos ítems que se acabaron podrían quedar para
      mañana. El resto drena a cinco por día.
- [x] Poda de notificaciones viejas: `prune_older_than(days)` y un **quinto job de cron**,
      `notification_pruning` a las 04:15. Nada las borraba nunca — ni expiración, ni poda, ni
      tope —, y la tabla crece sin techo debajo de las dos consultas que corren en cada carga
      de página. Corre **a propósito adentro de la franja de silencio** y **a propósito sin
      pasar por `_muted`**: es limpieza, no le habla a nadie. Se van las leídas y las no leídas
      por igual: un aviso de hace tres meses que nadie abrió no es información pendiente.
- [x] `get_unread_count` con `COUNT(*)` en SQL en vez de traer todas las filas: lo llama
      `get_template_context()`, o sea **cada** carga de página, y materializaba la tabla entera
      en Python para hacerle `len()`.
- [x] **Deuda de capas pagada de paso**, porque estaba en las líneas que había que reescribir:
      `app/jobs/` ya no arma consultas sobre modelos. De ahí salen el nuevo
      `HouseholdRepository.list_all()` — con `ORDER BY id` explícito, que la regla 5 exige
      cuando un tope por corrida depende del orden — y `UserRepository.list_active()`, que
      reemplaza los `select(User)` de `notification_jobs` y de `suggestion_jobs` y el
      `db.get(Household, ...)` de este último. También `WorkoutRepository.get_last_session_start()`:
      lo que había respondía "¿entrenó en los últimos N días?" pero no *cuánto hace*, que es
      justo lo que la escalada necesita saber.
- [x] `tests/test_notifications.py`: `TestSubjectDedup` (9 tests) contra el repositorio — el
      mismo sujeto no se anuncia dos veces, otro sujeto de la misma categoría sí pasa, se
      vuelve a hablar cuando empeora, mejorar no gana nada, la ventana vence, descartar no lo
      trae de vuelta, dos categorías sobre la misma persona son dos sujetos, el aviso de uno no
      tapa el del otro, y un chequeo del hogar ignora las filas dirigidas a una persona — más
      `TestVisibility` (3) para la lectura: el aviso de una persona es invisible para la otra en
      las tres consultas, "marcar todo como leído" no le toca las filas, y — como control
      negativo, para que un filtro por `user_id` a secas no pueda pasar — el aviso del hogar
      llega a los dos. Más `TestPruning` (2).
- [x] Nuevo `tests/test_notification_jobs.py` (14 tests): los jobs de punta a punta, que es
      donde se ve lo que la persona recibe. Uno por ítem y no uno más mañana; los tres escalones
      de la despensa, incluido el del medio; el tope y su drenaje; los más vacíos primero; el
      recordatorio que escala con la brecha y el que no inventa un número cuando no hay
      historial; la inactividad reciente que se deja en paz, la vieja que escala, y la brecha de
      uno que no es la del otro (o sea `get_last_session_start` filtrando por participante y no
      solo por hogar); y la poda, que **commitea** — observado espiando `db.commit`, porque
      contar filas después no distingue: el `flush()` del repositorio ya saca la fila de la
      vista de esta sesión, así que sin el spy sacarle el commit al job deja la suite verde — y
      que corre **adentro** de la franja de silencio, que es lo único que sostiene la decisión
      de no ponerle el gate `_muted` (ponérselo la apagaría para siempre sin romper nada).

**4.3 — Todo lo que la app dice lleva a algún lado**

- [x] Exponer `related_entity_*` en `NotificationRead`. Las columnas existían desde el
      principio y desde la 4.2 los jobs las llenan, pero el schema no las exponía: un cliente
      de `/api/v1` recibía "no queda leche" sin nada que identifique **qué** leche, o sea sin
      poder ofrecer la acción que la pantalla web sí ofrece.
- [x] Renderizar cada notificación y cada sugerencia con **una acción primaria** que aterrice
      en la pantalla correcta con la captura preprellenada — aprovechando el prefill que la
      Fase 1 arregla. Nuevo `app/web/actions.py` con `notification_action()` y
      `suggestion_action()`, más dos rutas `POST .../act`.
  - El mapa de destinos vive en **Python y no en una plantilla** porque el mismo destino lo
    leen tres lugares: la plantilla para dibujar el botón, y los dos `/act` para saber a dónde
    mandar a la persona después de anotar que actuó. Calculado dos veces, el botón y el
    redirect podrían no coincidir.
  - `low_stock` aterriza en `/pantry/?low=1#stock-item-<id>`: el ancla es el `id` de la raíz de
    `stock_card.html`, que ya era el contrato de `hx-target` del ajuste de stock. El `?low=1`
    no es decoración — garantiza que la tarjeta esté en la página para que el navegador
    encuentre el ancla, y si el ítem ya se repuso la página abre igual en la lista de
    faltantes, que es la degradación correcta.
  - En una sugerencia, la acción **reemplaza** al "Me parece bien" en vez de sumarse: dos
    botones afirmativos al lado del otro le harían elegir entre aprobar y hacer, cuando hacer
    ya implica aprobar. La señal positiva no se pierde porque `/act` graba `accepted` **antes**
    de redirigir.
  - **Lo que sale del panel de sangre no recibe acción, y eso se decide por `source_type`, no
    por categoría.** La primera versión de esto excluía `category="habit"` creyendo que era la
    categoría del generador de sangre; es falso: `blood_generator` emite 19 filas de `meal` y 5
    de `activity`, y solo 4 de `habit`. O sea que "comé menos carne roja" recibía un botón
    "Anotar una comida" — y como la acción **reemplaza** al "Me parece bien", la única respuesta
    afirmativa que quedaba abría la captura con "Comimos " escrito. La app contestaba un consejo
    de *no* comer algo pidiendo que se anote una comida, en la parte del producto que más
    cuidado pide. Ahora `suggestion_action(category, source_type)` pide el `source_type` de
    forma obligatoria y devuelve `None` para `blood_analysis` antes de mirar nada más; un
    llamador nuevo que lo olvide falla en el test, no en la pantalla de alguien.
  - Las categorías con acción son las tres que algún generador realmente emite (`meal`,
    `activity`, `shopping`). `Suggestion.category` documenta también `variety`, `recovery`,
    `pantry` y `reminder`: ninguna se escribe nunca, y ponerlas en el mapa era una rama
    inalcanzable y ocho tests que parecían cobertura sin proteger nada (regla 6 de `AGENTS.md`).
    `shopping` aterriza en `/pantry/?low=1` por lo mismo que el aviso de stock: las dos
    sugerencias de `shopping` que existen **son** la lista de faltantes.
  - Actuar sobre un aviso **también lo marca leído**: sin eso el globito del nav seguiría
    contando algo que la persona acaba de hacer, o sea que la app pediría dos veces lo mismo.
    Leído y no descartado — descartar es de la persona.
  - Los cinco rótulos nuevos entran al catálogo `es_AR` en el momento, no en el backlog de la
    Fase 5: son los rótulos del único botón nuevo de la sub-fase, y en inglés al lado de
    "Buena idea" se leen como un olvido.
  - Tests (34, `tests/test_actions.py`): el mapa como función pura; que **todo** destino
    termine en `/` antes del `?` (sin eso cada toque paga un 307 de `redirect_slashes`, y el
    fragmento no sobrevive a todos los clientes en un redirect); que todo destino conteste 200
    contra la app de verdad; que `prefillMap` de `capture/index.html` conozca cada clave que
    mandamos (una clave que no esté ahí abre el textarea vacío — el defecto de la Fase 1
    volviendo por otra puerta, sin error visible); que `stock_card.html` siga llevando el `id`
    del ancla; que la tarjeta renderice **sola** (el parcial de `POST /read` recibe
    `{request, n}` y el botón usa dos globals, uno de los cuales necesita el `request`); y las
    dos rutas, incluido que un aviso o una sugerencia ajenos no se puedan tocar.
  - Tres de esos tests existen porque sin ellos el cambio se rompe **en silencio**: que un
    candidato de `blood_analysis` no reciba acción en ninguna de las categorías que el
    generador emite (más un test que verifica que esas son las que emite, para que el primero
    no envejezca solo); que `/act` escriba el `BehaviorSignal` `accepted_suggestion` de +1.0
    contra la sugerencia — que es la razón entera por la que la acción puede reemplazar al "Me
    parece bien", y sin la afirmación alguien puede grabar `status="accepted"` a mano y dejar
    al motor sin señales positivas sin que se caiga nada ni cambie ninguna pantalla —; y que
    `GET /api/v1/notifications/` devuelva el sujeto, que es el único consumidor de los dos
    campos nuevos del schema (la pantalla web lee la fila del ORM, no el schema, así que sin
    esto los dos campos parecen muertos y borrarlos no rompe ningún test).
- [x] Detectar más ausencias además de las dos actuales (hoy solo entrenamiento y pesaje):
      comidas no registradas, sueño ausente.
  - Las cuatro ausencias por persona pasan a ser **datos y un solo driver**: un
    `_Absence` congelado (categoría, umbral, severidad base, cómo leer el último registro,
    cómo redactar) y `_run_absence_job()`. Antes eran dos funciones casi iguales; con dos
    más, copiarlas otra vez era garantizar que el sujeto, la marca de agua y el retiro se
    separaran en alguna de las cuatro. Los cuatro puntos de entrada que el scheduler
    importa quedan como envoltorios de una línea.
  - Umbrales distintos porque los huecos no significan lo mismo: comidas a los **2 días**
    (severidad base 4) porque no anotar qué se comió en dos días ya deja al motor sin la
    mitad de su entrada; sueño a los **3** (base 3) porque una noche sin anotar es normal.
    La escalada es la de la 4.2: un punto por cada tanda entera de umbrales.
  - **Ninguna de las dos habla cuando no hay registro previo** (`first_message=None`),
    a diferencia de inactividad y pesaje. La cuenta recién creada no tiene nada anotado de
    nada: si las cuatro ausencias saludaran, el primer día de uso serían cuatro
    notificaciones antes de que la persona escriba una línea. Entrenamiento y pesaje
    saludan porque son el pedido inicial del producto; comidas y sueño esperan a tener una
    primera marca contra la cual medir.
  - Los dos lectores nuevos son de repositorio y devuelven un instante, no una lista:
    `MealRepository.get_last_meal_at` hace `max(timestamp)` **con join a
    `MealParticipant`** — sin la mitad del participante, la cena que Rocío anotó sola
    taparía el hueco de Diego (regla 4) — y `BodyMetricRepository.get_last_sleep_at` filtra
    `sleep_hours IS NOT NULL`, porque las horas son una columna opcional de la fila de
    peso: el sujeto es *la última noche con horas*, no el último pesaje. Un pesaje de ayer
    sin horas no cuenta como sueño anotado, y hay un test que lo fija.
  - Horarios elegidos por cuándo el dato existe, no por repartir la agenda:
    **20:45** para comidas (después de cenar el día de comidas ya está completo; a la
    mañana el hueco todavía no existe y a media tarde el aviso sale mientras la persona
    está por almorzar) y **10:25** para sueño (el sueño se anota cuando uno ya se levantó,
    y a las 8:20 competiría con el aviso del pesaje). El de comidas es el que pasa más
    cerca de la franja de silencio, y por eso es el que la deja documentada.
  - Las dos categorías nuevas entran también en las tres superficies que ya existían:
    acción primaria en `actions.py` ("Anotar una comida" / "Anotar cuánto dormiste" —
    `sleep` suma una clave al `prefillMap` de `capture/index.html` sin sumar un quinto chip,
    porque el sueño se anota al lado del peso), ícono y tono de dominio en `domain.html`
    (tono `food` y `body`, no `reminder`: lo que se pide es de comida y de cuerpo), y
    rótulo traducido. Un test nuevo (`TestEveryCategoryHasAFace`) lee las categorías de los
    `_Absence` del módulo de jobs y exige que los tres mapas de `domain.html` las cubran:
    una quinta ausencia sin cara no se puede mergear en silencio.
  - Los textos de estos jobs quedan en inglés como los dos que ya estaban: son cuerpos
    armados en Python y su traducción es el backlog de i18n de la Fase 5, no de acá.
- [x] **Ciclo de vida por sujeto**, que es lo que la 4.2 dejó pendiente: retirar el aviso
      abierto cuando su sujeto sale del conjunto — la leche que se repuso, el que volvió a
      entrenar. Con eso la marca de agua de `priority` se reinicia sola en la recuperación, en
      vez de esperar a que venza la ventana de 7 días, y es también lo que le da sentido a la
      acción primaria: tocar el aviso y que el aviso se vaya.
  - **Retirar es un `DELETE`, no una bandera.** No es una elección estética: la marca de
    agua vive en la fila, y `has_recent_for_subject` ignora `dismissed_at` a propósito
    (así descartar no vuelve a abrir la canilla). O sea que marcar la fila —con la columna
    que fuera— la dejaría igual de suprimida y la recuperación no reiniciaría nada. Sacar
    la fila es lo único que reinicia el escalón, y evita además una columna nueva: la
    única migración de la v3 es la `0003` y es de la 4.4.
  - Por eso mismo **el retiro no mira leído ni descartado**: el aviso que la persona ya
    leyó es exactamente el que hay que sacar cuando la cosa se resolvió.
  - Dos formas en el repositorio, porque los dos sujetos son distintos:
    `retire_subject(...)` para lo de una persona (volvió a entrenar, se pesó, comió, durmió)
    y `retire_subjects_other_than(..., keep_ids)` para la despensa, donde **la lista de
    faltantes de hoy *es* la verdad** y el complemento —lo que ya no falta— es lo que se
    va. El conjunto vacío tiene que borrar todo: un `if keep_ids:` de más, o un `notin_([])`
    —que en SQL no matchea nada—, dejaría los avisos puestos justo cuando ya no falta nada,
    y hay un test para ese caso.
  - Las dos formas filtran `source_type == "job"` y exigen `related_entity_id IS NOT NULL`:
    **lo que escribió una persona no lo borra un job**, y una fila de la v1 sin sujeto no se
    puede declarar resuelta porque no se sabe de qué hablaba (esas se van con la poda).
    El scope es el mismo `_addressee_scope` de la 4.2, o sea que el `DELETE` lleva la regla
    4 adentro: retirar el pesaje de Diego no puede llevarse el de Rocío, que perdería su
    recordatorio **y** su marca de agua sin haberse pesado.
  - El retiro queda **después** del gate de silencio, no antes: si el job entero se saltea
    por horario, nada corre — y eso está bien, porque un retiro tardío no molesta a nadie,
    mientras que abrir la sesión para borrar durante la franja de silencio es trabajo con
    riesgo y sin beneficio.
  - Una corrida que **solo** retira necesita su propio `commit()`: `retire_subject` hace
    `flush()`, y si no se creó ninguna notificación no hay `NotificationService.create` que
    commitee por nosotros; sin eso el borrado se iría con el `close()` del `finally`. Es una
    trampa que un test no puede detectar contando filas —el `flush()` ya las saca de la
    vista de la sesión—, así que el test cuenta los `commit()` con un monkeypatch, igual que
    el de la poda.
  - Tests (31 nuevos entre `test_notification_jobs.py` y `test_notifications.py`, 406 en
    total): que el hueco se mida contra el último registro y no contra la creación de la
    cuenta; que quien nunca anotó quede en silencio; que las comidas de uno no sean las del
    otro; que un pesaje sin horas no cuente como sueño; que registrar la cosa saque el aviso
    abierto; que reponer un ítem deje el aviso del otro; que la marca de agua se reinicie en
    la recuperación (9 días → prioridad 6, se pesa, 5 días → prioridad 5 y no 6); que el
    retiro alcance a un aviso ya leído y descartado; que una corrida de solo-retiro
    commitee; y que una fila escrita a mano sobreviva.
  - **Lo que encontró la revisión de `code-health-qa`, y que era un defecto de verdad:**
    "dormí 7 horas" contaba como pesaje. `weight_kg` también es opcional —el parser acepta
    el intent de medición con cualquiera de sus cuatro campos, y `BodyMetricService` escribe
    la fila con el peso en `NULL`—, y el lector del recordatorio de peso preguntaba por la
    última fila cualquiera fuera su contenido. Antes de esta sub-fase eso *atrasaba* el
    aviso; con el retiro por sujeto **lo borra**, y con él la marca de agua: quien anota
    sueño cada dos días no volvía a recibir el aviso del peso **nunca**, y en silencio.
    El camino entero está adentro de lo que la 4.3 construye —tocar el aviso de sueño lleva
    a la captura con "I slept " puesto—, así que la sub-fase se estaba comiendo su propia
    cola. Arreglado con `get_last_weight_at`, el espejo exacto de `get_last_sleep_at`
    (`weight_kg IS NOT NULL`, `max()` en la base), que es además el filtro que
    `get_weight_series` ya tenía tres líneas más abajo. El test que lo fija se cae con el
    lector viejo: verificado, no supuesto.
  - Otras cuatro cosas de la misma revisión: el `commit()` del retiro pasó a estar
    **adentro** del `for` de personas, donde ya estaba el del job de stock, porque el
    `except` está afuera del loop y una excepción con la segunda persona se llevaba el
    retiro de la primera; se fue el `assert absence.first_message is not None`, que era
    control de flujo dependiente de `assert` —bajo `python -O` se convertía en
    `None(user)`— y que existía solo para arrastrar un centinela hasta después del dedup
    (ahora cada rama arma su texto y desaparecen el centinela y su segundo `if`); se fue el
    parámetro `user_id` de `retire_subjects_other_than`, que no tenía un solo llamador
    (regla 6: el único conjunto que se conoce entero es la despensa, y la despensa es del
    hogar); y `TestEveryDestinationExists` dejó de listar las categorías a mano para
    derivarlas de los `_Absence`, porque si no una quinta ausencia rompía el test de los
    mapas de `domain.html`, alguien completaba los mapas, y el aviso salía lindo y **sin
    botón** con la suite en verde.
  - Dos tests negativos más un tercero pasaron a distinguir "calló porque no tenía nada que
    decir" de "el job explotó": su única afirmación era `== []`, y como cada job termina en
    `except Exception: logger.exception(...)`, un typo en un helper de mensajes los dejaba
    pasar igual. Ahora un helper compartido exige que no haya nada logueado en `ERROR`, y
    está verificado haciendo fallar un job a propósito.
  - **Queda anotada una consecuencia del gate de silencio**, no arreglada: saltear el job
    entero saltea también el retiro, que no le habla a nadie. Se acepta porque el retiro es
    idempotente y la corrida de mañana lo hace igual; el caso raro —poner
    `QUIET_HOURS_START=20`, con lo cual el job de comidas de las 20:45 queda mudo siempre y
    su tarjeta abierta no se va hasta la poda de 90 días— está escrito en el docstring de
    `_muted`. Mover una hora de `_SCHEDULE` adentro de la franja es apagar ese aviso, no
    postergarlo, y eso ahora está dicho donde se lo va a leer.

**4.4 — Que la app aprenda de verdad los gustos de cada uno con el uso**

> Esta sub-fase creció respecto de la versión original del plan, a pedido explícito del
> usuario: *"si es posible que la inteligencia vaya aprendiendo con el uso sobre los gustos
> y preferencias del usuario, implementemoslo tambien"*. Lo que había acá antes era solo el
> arreglo del bug de keying (guardar el feedback contra el sujeto y no contra el título
> renderizado). Ese arreglo sigue siendo el **prerrequisito**, pero por sí solo no es
> aprendizaje: la app seguiría dependiendo de que alguien toque los botones de aceptar o
> descartar, que es la interacción más rara de todas. Los nueve puntos que siguen convierten
> eso en un bucle de aprendizaje real.
>
> **Sigue fuera de alcance:** entrenar un modelo, y meter un LLM en el camino de
> recomendación. Todo esto es aritmética determinista sobre `behavior_signals`, que ya es la
> tabla diseñada para exactamente esto.

**Prerrequisito — anclar al sujeto (el bug de keying)** ✅ hecho

- [x] **Migración `0003`**: `subject_type` + `subject_name` en `suggestions` (la única
      migración de todo v3). `behavior_signals` **no necesita columnas nuevas**: ya tiene
      `entity_type`, `entity_name`, `value`, `context_json` y `created_at` indexado.
      Las filas viejas quedan con las dos columnas en `NULL` a propósito: el sujeto no se
      puede derivar del título sin volver a cometer el error, así que responder una
      sugerencia pre-`0003` no enseña nada y lo deja anotado en el log.
- [x] Cada generador declara el sujeto de su candidato (alimento, actividad, grupo muscular,
      marcador, ítem de stock). Que ninguno se lo olvide lo cuida
      `TestEveryCandidateDeclaresItsSubject`, que recorre los cuatro y exige un
      `subject_type` del vocabulario más un `subject_name` no vacío —con un mínimo de
      candidatos por generador, para que no pase en falso si un fixture deja de cubrir una
      rama—. Es la red que reemplaza al `or title` que `candidate_subject` deliberadamente
      no tiene.
- [x] El feedback se guarda contra ese sujeto en lugar de `title.lower()[:200]`.
- [x] El scorer matchea por sujeto normalizado en vez de bolsa-de-palabras, así rechazar
      *"Time to get moving!"* deja de suprimir todo candidato que comparta 60% de esos tokens
      cruzando categorías. El mismo cambio en `filters.apply_signal_constraints`, que era
      **peor** que el scorer: filtraba con 60% de solape de tokens y sin penalización
      reversible —el candidato desaparecía antes de puntuarse— y miraba solo `value < 0`, así
      que el −0.3 de un descarte borraba la tarjeta en lugar de bajarla. Ahora saca de la
      lista solo un "no" explícito (`rejected_suggestion`).
- [x] Dejar **un solo punto de escritura** de señales: nuevo `app/recommendations/learning.py`
      con `record_signal()` y `net_affinity()`. `SuggestionService`, `MealService` y
      `WorkoutService` ya no tienen `BehaviorSignalRepository` a mano —esa era la puerta por
      la que se escribía salteando la normalización—, y por eso el vocabulario no puede
      volver a divergir. (`net_affinity()` **ya no existe**: la 4.4.3 lo partió en
      `SubjectAffinity` + `subject_affinities()`, porque un solo número mezclaba *para qué
      lado* con *cuánto lo sostiene*. `record_signal()` sigue siendo el único punto de
      escritura, y `PantryService` se sumó a la lista en la 4.4.1.)
      **Corrección al diagnóstico original:** de los tres tipos que el
      scorer leía sin escritor, `repeated_meal_choice` y `repeated_activity` **sí** tenían
      quién los escribiera (`MealService` y `WorkoutService`); el único realmente huérfano
      era `repeated_purchase`, y sigue sin escritor hasta la 4.4.1.
      El `record_feedback` muerto de `engine.py` **ya se había borrado en la 4.3**, no acá.
- [x] **Dedup antes de persistir**: no crear una sugerencia pendiente cuyo sujeto ya tiene
      una pendiente. El tope (`limit`) se aplica **después** del dedup, no antes: al revés,
      un lote con duplicados devolvía menos sugerencias de las pedidas. Para el hogar el
      filtro exige `scope_type == "household"`, porque las personales también llevan
      `household_id` y sin eso la tarjeta de despensa que aceptó uno bloqueaba la del otro.

**4.4.1 — Aprender de lo que hacen, no solo de lo que tocan** ✅ hecho

Hoy la única entrada real de aprendizaje son los botones de aceptar/descartar de una
sugerencia. Un household que simplemente *usa* la app —registra comidas, entrenamientos y
compras— no le enseña nada. Cada captura confirmada pasa a emitir señales implícitas:

- [x] Comidas: cada `MealItemConsumed` confirmado emite señal por alimento, con el
      `meal_type` en `context_json` — `MealEvent.meal_type` se guardaba desde la v1 y nadie
      lo leía para aprender, así que "café" era un gusto y no un gusto *del desayuno*, y la
      4.4.5 hubiera tenido que volver a buscar la comida para averiguar algo que estaba a
      mano al escribir. **La señal por `FoodItem.category` se difiere a la 4.4.4**, que es
      donde se construye el lector de sujetos de nivel atributo: escribir filas de categoría
      ahora, sin ningún candidato que declare ese `subject_type`, es exactamente el bug de
      `repeated_purchase` —escritor sin lector, o lector sin escritor— que este mismo commit
      viene a cerrar.
      → **Al llegar la 4.4.4 esa señal no se escribió, y no se va a escribir**: el atributo
      se deriva del catálogo en cada lectura, que es retroactivo y no tiene escritor que se
      pueda olvidar. El razonamiento está en la 4.4.4; lo que esta línea dejó anotado quedó
      resuelto, no pendiente.
- [x] Entrenamientos: `WorkoutExercise` emite señal por ejercicio **y** por su
      `muscle_group`, y el `perceived_effort` (RPE) modula el valor: de 9 en adelante pesa la
      mitad (`_HIGH_EFFORT_WEIGHT`), porque haber hecho algo es evidencia de que se puede
      repetir y una serie al límite dice lo contrario. La escala es de un solo lado a
      propósito: un RPE bajo no se castiga, una sesión liviana es perfectamente repetible.
      El grupo muscular sale de la columna de la captura y no del catálogo — cuando el
      ejercicio viene sin grupo no se inventa uno; resolverlo contra `ExerciseType` es 4.5.
      La señal de `workout_type` que ya existía **no** se reemplazó: `workout_type` es el
      *lugar* ("gym"/"home"/"outdoor"), no la actividad, y era la única que había — cien
      sesiones de gimnasio registradas no enseñaban nada sobre press de banca.
- [x] Compras: `PantryMovement(movement_type="purchase")` emite `repeated_purchase`, que el
      scorer **ya leía** y nadie escribía: el circuito que motivó todo el módulo `learning`
      queda cerrado. Pesa 0.5 y no 1.0 porque comprar dice menos que comer —se compra para
      el otro, se compra y se tira— y porque se atribuye a quien registró la compra, que en
      una casa de dos es quien fue al súper. Hizo falta un `flush` dentro del bucle:
      `_make_movement` solo hace `add`, así que sin eso `movement.id` era `None` y la señal
      quedaba sin rastro de dónde salió, que es lo que la 4.4.8 necesita para explicarla y
      para poder olvidarla.
- [x] Ausencia como señal débil: un alimento sugerido que no aparece en ninguna comida en N
      días es un negativo suave, no un neutro. Es lo que distingue "no me interesa" de
      "todavía no lo vi". Se difirió con dos razones y las dos se cumplieron en la **4.4.10**:
      el job de barrido existe (`run_absence_sweep`, nada corre "N días después" por sí solo)
      y el veto pasó a pedir peso negativo acumulado, así que una sola sugerencia no comida
      ya no puede vetar un alimento.
- [x] `source_type="implicit"` en todas estas, para poder distinguirlas de las explícitas al
      explicar y al permitir corregir. La 4.4.2 además les va a dar semividas distintas.
- [x] **Se saca `rejected_activity` de `NEGATIVE_SIGNAL_TYPES`.** Es el mismo bug una capa
      más arriba: el scorer y el filtro de la v1 lo leían y ningún código lo escribió nunca,
      así que no puede haber filas. Rechazar una sugerencia de actividad graba
      `rejected_suggestion` con `subject_type="exercise"`, que es la misma información sin un
      segundo tipo que mantener sincronizado. La lista de ejemplos de `app/models/signal.py`
      —que también nombraba `repeated_recipe`, `ingredient_pairing`, `feasibility_signal`,
      ninguno escrito jamás— pasa a apuntar a `learning.py` en vez de ser una segunda copia
      del vocabulario que se separa sola.
- [x] Tests: nuevo `tests/test_learning_signals.py` (15). Uno por escritor —qué sujeto,
      qué valor, qué contexto, contra qué entidad de origen— más aislamiento por persona en
      comidas y entrenamientos compartidos (regla 4), el fin del recorrido (comprar y comer
      algo lo sube en el ranking; una señal de hace 200 días deja de contar), y sobre todo
      `test_every_signal_type_the_reader_knows_has_a_writer`: recorre las fuentes de `app/`
      por AST —no por grep, porque un comentario que menciona un tipo no es un escritor— y
      falla si algún tipo de `POSITIVE_SIGNAL_TYPES | NEGATIVE_SIGNAL_TYPES` no aparece en
      ningún otro módulo. Es la red que hace que este bug falle en el commit que lo
      introduce y no meses después.

**4.4.2 — Decaimiento temporal: lo que hace que sea aprender y no acumular** ✅ hecho

- [x] Una señal de hace ocho meses hoy pesa exactamente igual que la de ayer, así que un
      gusto que cambió **no se puede desaprender nunca**. El peso pasa a ser
      `value * 0.5 ** (edad_en_días / SEMIVIDA)`, con semivida distinta por tipo: las
      explícitas duran más que las implícitas.
      → `learning.half_life_days()` / `decay_factor()` / `signal_weight()`, y la lectura de
      afinidad suma pesos descontados en vez de `value` crudo —era `net_affinity`, que la
      4.4.3 reemplazó por `subject_affinities()`—. Las semividas:
      **explícita 90 días, implícita 21**. La explícita dura más porque dice algo sobre la
      persona ("no me gusta el hígado") y la implícita algo sobre la semana ("comí pollo el
      martes"). Un `source_type` desconocido —hoy `"inferred"`, que desde la 4.4.10 es lo
      que graba el barrido de ausencias— cae en la más corta: si no sabemos de dónde salió,
      que se desvanezca rápido es el error más barato.
      Lo que *no* decae son las preferencias duras: viven en `RecommendationPreference` y
      `apply_hard_constraints` las lee sin descuento. `behavior_signals` es la parte blanda,
      la que tiene derecho a quedar vieja.
- [x] `created_at` ya está indexado, así que esto no cuesta una migración ni un query nuevo.
      → Sí costó **ampliar la ventana de lectura**: una semivida de 90 días dentro de una
      ventana de 30 no significa nada, porque el corte seguiría decidiendo en lugar del
      decaimiento. Ahora el horizonte es `SIGNAL_HORIZON_DAYS = 4 × la semivida más larga`
      (360 días) y está **derivado**, no escrito: a cuatro semividas una señal conserva el
      6% de su valor, que con el tope de boost de 0.12 mueve el score menos de una
      centésima. El corte existe para acotar la consulta, no para decidir.
- [x] Y de paso, las dos copias del umbral. Era un `30` escrito a mano en `engine.py` **y**
      otro en `scorer.py`: dos copias del mismo número, que es exactamente cómo empiezan a
      discrepar. Los dos leen ahora `learning.SIGNAL_HORIZON_DAYS`, del mismo lado que las
      semividas de las que se deriva, y `test_the_read_horizon_is_derived_and_not_copied` lo
      fija.
- [x] El "no" también caduca (`_FILTER_DECAY_FLOOR = 0.5` en `rejected_subjects`). Ampliar
      la ventana para que el decaimiento tenga de qué decaer, sin tocar el filtro, habría
      convertido un rechazo de hace once meses en un **veto permanente** — el problema que
      la 4.4.2 vino a resolver, al revés. Un rechazo saca al sujeto de la lista mientras
      conserve al menos la mitad de su peso, o sea durante exactamente una semivida; después
      vuelve a la lista y sigue contando, pero solo en el score. El umbral no es un número
      nuevo: es la semivida ya declarada, leída de otra forma. Filtrar es más caro que
      puntuar —el candidato no llega a existir—, así que deja de hacerse antes.
- [x] Siete tests nuevos en `tests/test_recommendations.py::TestTemporalDecay`: que una señal
      en su semivida vale exactamente la mitad, que lo dicho sobrevive a lo hecho, que un
      `source_type` desconocido se apaga como el más rápido, que una señal sin volcar (sin
      `created_at`, porque lo pone la base) cuenta entera, que un gusto reciente le gana a
      uno viejo, que un "no" de 30 días filtra y uno de 300 solo pesa, y el guard del
      horizonte derivado. El helper `_signal()` acepta `age_days` opcional: sin él la señal
      queda sin fecha y por lo tanto sin descuento, así que los tests que no hablan del
      tiempo siguen midiendo lo que medían.
      En `tests/test_learning_signals.py`, `test_signals_older_than_the_window_stop_counting`
      pasó a ser `test_an_old_meal_barely_counts_next_to_a_recent_one`: con decaimiento una
      comida de hace 200 días ya no queda **afuera**, queda en el ruido del redondeo
      (`< 0.501`). La intención del test sobrevive; el mecanismo que la sostiene cambió de un
      corte duro a una semivida.

**4.4.3 — Confianza por sujeto: un toque no es una regla** ✅ hecho

- [x] El boost/penalización escala con la **cantidad de observaciones** y satura, en vez de
      ser un valor fijo: un descarte es una pista, seis descartes es una regla. Hoy un solo
      tap accidental puede vetar un alimento para siempre.
      → `learning.SubjectAffinity` parte en dos lo que era un solo número: `direction` (el
      promedio de las señales, en `[-1, 1]`: **para qué lado**) y `confidence`
      (`evidencia / (evidencia + 2)`: **cuánto lo sostiene**), y el scorer usa el producto.
      Una observación vale 0.33 de la certeza, dos 0.5, seis 0.75, veinte 0.91: satura, y
      nunca llega a 1 porque la app no termina de estar segura de nada.
      La "evidencia" es la suma de los pesos **en valor absoluto** —descontados por edad,
      así que la 4.4.2 sigue mandando—: una señal de 1.0 de hoy cuenta como una
      observación, la misma de hace una semivida como media, y un `repeated_purchase`
      (0.5) como media desde el principio.
- [x] Con eso se fue el `min(suma, 1.0)` del scorer, que era un recorte puesto para que la
      suma no se desbordara —cada comida registrada escribe un `repeated_meal_choice` de
      1.0— y no para modelar cuánto sabe la app. El efecto real de ese tope era que **un
      único tap movía el score exactamente igual que diez observaciones consistentes**.
      Ahora el ajuste está acotado por construcción, no por un `min()` a mano.
- [x] La fuerza declarada de una preferencia explícita entra como fuerza:
      `SuggestionService.save_preference` escribía la señal con un ±1 fijo aunque
      `strength` (0–1) ya viajaba en el schema y ya se guardaba en la preferencia, así que
      un "no me encanta" pesaba igual que un "no lo como". Hoy ningún llamador manda
      `strength` (los tres usan el default 1.0), así que el cambio no altera nada de lo que
      pasa hoy: lo que arregla es que la columna deje de escribirse para nada.
- [x] Cuatro tests nuevos (`TestConfidenceByEvidence`): que una observación mueve menos que
      seis, que la certeza satura —el salto de 1 a 6 mueve más del doble que el de 6 a 24, y
      el ajuste nunca pasa el knob—, que lo aprendido es un **promedio y no un conteo**
      ("diez sí y un no" sigue siendo sí, con más certeza que "un sí" solo), y que un solo
      tap ya no descarga la penalización entera.
- [x] **Umbral mínimo de evidencia antes de que una señal filtre** (en vez de solo puntuar),
      junto con la **ausencia como señal débil** que la 4.4.1 dejó pendiente. **Hecho en la
      4.4.10**, en un commit. Van juntos, y por eso no entraron acá: el único tipo que
      filtraba era `rejected_suggestion`, que
      siempre se escribe con `-1.0` desde un tap deliberado en "no" —y pedirle a alguien que
      lo apriete dos veces para ser escuchado es peor producto, no mejor—. El umbral existe
      para dejar entrar negativos *débiles* sin darles poder de veto, y el único negativo
      débil previsto es la ausencia. Escribir el umbral ahora sería un lector sin escritor;
      escribir la ausencia sin el umbral sería un veto por una sugerencia no comida. Se
      hacen en el mismo commit, con el barrido que la ausencia necesita.
      Lo que ya está resuelto de ese "para siempre" es la parte temporal: desde la 4.4.2 un
      rechazo vale como veto una semivida y después solo pesa en el score.

**4.4.4 — Aprender el atributo, no solo el nombre exacto** ✅ hecho (los alimentos; los
ejercicios esperan la 4.5)

- [x] Si alguien rechaza brócoli, coliflor y kale, lo aprendible es la **categoría**, y
      `FoodItem.category` ya viene en el seed. Aprendizaje en dos niveles: el sujeto puntual
      y su atributo (`FoodItem.category`, `ExerciseType.muscle_group`/`intensity`), con el
      nivel de atributo exigiendo más evidencia que el puntual.
      → El atributo **se deriva en cada lectura del catálogo**, no se graba:
      `FoodRepository.name_categories()` + `learning.attribute_index(db)` arman
      `("food", "espinaca") → ("food_category", "vegetable")` una vez por corrida del motor
      y el scorer lo recibe como diccionario, así que sigue siendo una función de sus
      argumentos y se testea sin sesión. Los alias del catálogo entran con la categoría de
      su canónico, porque el texto libre de una captura escribe el alias ("palta", no
      "avocado") y la señal quedó guardada con **ese** nombre. Las filas sin categoría
      —justamente las que crea `get_or_create` con texto libre— no entran: de esas no
      sabemos el atributo, y adivinarlo es el match difuso que la 4.4 vino a sacar.
      La vara más alta es un solo número y está **derivada**, no escrita:
      `_ATTRIBUTE_EVIDENCE_HALF_SATURATION = 3 × _EVIDENCE_HALF_SATURATION`. El factor es 3
      porque es el mínimo que se lee como patrón y no como coincidencia: un rechazo alcanza
      para aprender del brócoli, y hacen falta tres verduras distintas para concluir algo de
      las verduras. Con dos, cualquier semana rara reescribiría una categoría entera —y una
      categoría son treinta alimentos, no uno—.
- [x] Con eso la app puede acertar con algo que el usuario **nunca vio antes**, que es la
      diferencia entre recordar y aprender.
      → Medido: tres rechazos frescos de verduras mueven una espinaca que nunca apareció en
      una sugerencia −0.025 (evidencia 3 → certeza 0.33), diez la mueven −0.047 y noventa
      −0.070. Un solo rechazo **del propio sujeto** vale −0.050, o sea que ni una categoría
      saturada le gana a una sola opinión directa. Eso lo sostienen dos cosas distintas, y a
      propósito: la vara de evidencia decide **cuándo** se le cree a una generalización, y
      `_ATTRIBUTE_SIGNAL_SCALE = 0.5` en el scorer decide **cuánto**, para siempre. Hace
      falta el segundo porque la certeza satura hacia 1: con cien verduras registradas
      —meses, no años— una verdura no comida llegaría al mismo ajuste que la comida
      favorita. Una categoría es una de las razones por las que algo gusta, nunca la razón
      entera.
      Y el atributo **no se cuenta a sí mismo**: `SubjectAffinity.without` le descuenta al
      grupo lo que aportó el candidato, porque un alimento de todos los días es el que más
      aporta a su categoría y sin la resta cobraría el ajuste puntual y otra vez, en chico,
      por su propia evidencia — un favorito con un ajuste más grande que el knob, por
      partida doble, sin que hubiera aparecido ni un dato nuevo.
      El atributo **no filtra**: `rejected_subjects` sigue mirando solo el sujeto puntual.
      Generalizar para ordenar una lista es útil; sacar la espinaca porque la persona
      rechazó tres **otras** verduras es ponerle en la boca un "no" que no dijo.
- [x] Acá entra la señal por `FoodItem.category` que la 4.4.1 dejó anotada: el escritor y el
      lector del nivel atributo se hacen juntos, en el mismo commit, porque separarlos es
      cómo nació `repeated_purchase`.
      → **No hay escritor, y ahí está la corrección al plan.** Grabar una segunda fila por
      comida con la categoría del alimento es peor por dos razones que no se veían al
      anotarlo: congelaría la categoría del día en que se comió —recategorizar la palta de
      `fat` a `fruit` no arreglaría nada de lo ya aprendido— y solo aprendería de las comidas
      **futuras**, cuando lo que la app ya tiene son meses de señales de alimentos cuya
      categoría el catálogo sabe hoy. Derivar en cada lectura es retroactivo y se corrige
      solo. Y el riesgo que el plan quería evitar desaparece por otra vía: sin escritor no
      hay nada que se pueda olvidar de escribir, que es exactamente cómo nació
      `repeated_purchase`. Para que no quede una puerta abierta, `food_category` vive en un
      `ATTRIBUTE_SUBJECT_TYPES` aparte y **no** está en `SUBJECT_TYPES`, así que
      `record_signal` lo rechaza y no puede existir una fila con ese tipo; un test lo fija.
- [x] Ni un generador ni un servicio cambian. El scorer resuelve el atributo desde el
      `subject_name` que los candidatos ya declaran desde la 4.4a, así que el cambio entra
      por el lado del lector solo: `learning.py`, `scorer.py`, una línea en `engine.py` y un
      método de repositorio.
- [x] Ocho tests nuevos (`TestAttributeLevelLearning`): el ejemplo del plan tal cual —tres
      verduras rechazadas mueven una cuarta que nunca se sugirió, y sin el índice no la
      mueven—, que una generalización queda por debajo de la evidencia directa aun con 90
      observaciones, que un favorito no se impulsa a sí mismo por su propia categoría, que
      tres verduras mueven más del doble que una y una mueve menos de un tercio que el
      sujeto propio, que una categoría **nunca** es un veto, que un alimento que el catálogo
      no conoce no tiene categoría, que el índice lee los alias, y que `food_category` no es
      un sujeto grabable.
- [ ] **Los ejercicios no están en el índice todavía, y no es un olvido.** Los candidatos de
      actividad salen de una lista de ocho actividades escrita a mano en
      `activity_generator`, cuyos nombres en su mayoría no existen en el catálogo de
      `ExerciseType` ("biking" contra "Cycling", "gym" y "swimming" que no están), así que el
      atributo resolvería para unos y para otros no, **en silencio**. Reemplazar esa lista
      por el catálogo es la 4.5, y ahí los ejercicios entran con el mismo shape: una entrada
      más en `attribute_index`, ninguna otra cosa cambia. Cuál de sus dos atributos
      discrimina depende de ese mismo reemplazo —para una actividad es la intensidad, para un
      ejercicio de gimnasio es el grupo muscular—, porque el seed le pone
      `muscle_group="full_body"` a casi todo el cardio.

**4.4.5 — Gusto con contexto horario** ✅ hecho

- [x] `MealEvent.timestamp` y `meal_type` ya se guardan: aprender *cuándo* les gusta algo
      (café en el desayuno, no en la cena) usando el `meal_type` que 4.4.1 mete en
      `context_json`. Es lo que hace que las sugerencias se sientan propias y no genéricas.
      → Hecho como **una diferencia entre franjas**, no como un promedio dentro de una:
      `slot_contrast` compara la fuerza del sujeto **en** la franja contra la del mismo
      sujeto **fuera** de ella (`in_slot.strength − off_slot.strength`, recortado a
      `[-1, 1]`). Medido con el ejemplo del plan: veinte cafés al desayuno dan `+0.109` al
      desayuno y `−0.136` a la cena, mientras que un plato comido diez veces al almuerzo y
      diez a la cena da exactamente `0` en las dos — un plato indiferente a la hora no se
      mueve por la hora. Ese último caso es lo que obliga a que sea una diferencia: medir
      "cuánto gusta el café al desayuno" cobraría por segunda vez lo que el nivel puntual ya
      cobró.
      El recorte no es decorativo: la resta de dos fuerzas vive en `[-2, 2]`, y sin tope
      este eje podría mover el score el doble de su perilla, que es la invariante que
      sostiene que una perilla acote un eje.
- [x] **Sin perilla de escala propia**, a diferencia del nivel atributo. Una categoría pesa
      la mitad porque es una *generalización* y tiene que quedar por debajo de la evidencia
      directa; una franja es un *recorte más específico*, así que no hay razón para creerle
      sistemáticamente menos. Lo que sabe de menos ya se lo descuenta su propia confianza,
      que con menos observaciones es más baja: una sola observación al desayuno mueve `+0.04`
      y veinte mueven `+0.109`.
- [x] **La franja la declara el generador, no la re-deriva el scorer.** `meal_generator` ya
      calcula el `meal_type` (y acepta que se lo pasen por parámetro), así que lo pone en
      cada candidato. Re-derivarlo en el scorer duplicaría las ventanas horarias de
      `_current_meal_type`, acoplaría el scorer al reloj del sistema y discreparía con el
      generador justo cuando alguien usa el override. Los candidatos que no son de comida no
      declaran franja, y por eso el eje se limita solo.
- [x] `"other"` **no es una franja**: es el default de `MealEvent.meal_type`, o sea lo que
      queda cuando la captura no dijo la hora. Contarlo como una franja más haría que cada
      comida sin hora argumentara contra todas las franjas reales — un plato bajaría de score
      a todas las horas por el solo hecho de estar registrado. `"brunch"` sí está, aunque
      ningún generador lo sugiera: lo produce `app/nlp/rules.py` y es una hora real del día.
- [x] La franja **no filtra**, por la razón del nivel atributo y una más fuerte: lo que hay
      del otro lado es una **ausencia**, y que nunca se haya registrado un café a la cena no
      es un "no". Usar la ausencia como señal es lo que la 4.4.3 dejó postergado a propósito,
      junto con el umbral mínimo de evidencia para filtrar.
- [x] Siete tests nuevos (`TestTimeOfDayLearning`): el ejemplo del plan tal cual, el plato
      indiferente a la hora, que una observación es una pista y veinte son una regla, que una
      comida sin hora (y una con `"other"`) no dice nada del reloj, que el contraste nunca
      excede una perilla, que una hora nunca es un veto, y que el generador declara la franja
      que está ofreciendo —incluido el override—.
- [x] Este eje aprende **solo de lo implícito**, y es una corrección al plan que conviene
      dejar escrita: el único escritor de una hora es `MealService.log_meal`. El feedback
      explícito no puede cargar una franja porque **`Suggestion` no tiene columna de
      contexto** ni de `meal_type`, y agregarla es una migración — la v3 gastó la única que
      tenía en la `0003`. No es un lector sin escritor: el escritor existe desde la 4.4.1.

**4.4.6 — Separar "me gusta" de "lo comí ayer"** ✅ hecho

- [x] Hoy `repeated_meal_choice` se escribe con `value=1.0` por ítem por comida, así que los
      alimentos de todos los días acumulan decenas de positivos y **dominan de forma
      monótona: el bucle empuja a repetir, no a variar**. Se parte en dos señales con
      ventanas distintas: afinidad (estable, con decaimiento) y saciedad reciente (ventana
      corta, supresiva).
      → **Corrección al plan, y es el corazón del eje:** no se parte en dos señales, se
      parte en dos **lecturas de la misma fila**. La fila que dice "esto le gusta" dice
      también "esto lo comió ayer", y lo que las separa no es el dato: es el reloj. La
      saciedad lee exactamente las mismas filas con semivida de **día y medio** contra los
      21 de la afinidad implícita — catorce veces más corta. Es la misma decisión que la del
      nivel atributo en la 4.4.4: un lector nuevo no puede olvidarse de nada, funciona
      retroactivo sobre los meses de señales que ya están escritas, y no cuesta ni una
      columna ni una migración (la única de la v3 se gastó en la `0003`). Escribir una
      segunda fila por comida habría necesitado que los tres escritores se acordaran, para
      guardar dos veces el mismo hecho.
      → `learning.satiety_pressure()`, `_SATIETY_HALF_LIFE_DAYS = 1.5`, y el parámetro
      `half_life=` que se le agregó a `decay_factor()`/`signal_weight()` para que la segunda
      lectura no sea una segunda copia de `0.5 ** (edad/vida)` — dos copias es cómo empiezan
      a discrepar.
- [x] **La saciedad la producen los actos, nunca los dichos.** `CONSUMPTION_SIGNAL_TYPES` es
      el subconjunto `{repeated_meal_choice, repeated_purchase, repeated_activity}`: decir
      que algo te gusta, o aceptar la sugerencia de comerlo, no te llena. Si contara,
      registrar una preferencia la suprimiría. Los tres actos cuentan y no solo la comida:
      comprar leche ayer es una razón para no sugerir comprar leche hoy, y repetir el mismo
      ejercicio tres días seguidos es el mismo error con otro cuerpo. Un test guarda la
      invariante de que el subconjunto siga siendo un subconjunto de los positivos.
- [x] **No devuelve una `SubjectAffinity` porque no tiene dirección.** Los otros tres ejes
      responden "¿le gusta?" con distinto nivel de detalle; este responde "¿cuánto ya hubo?",
      y es el único que solo puede restar. Un favorito sigue siendo un favorito: lo que deja
      de ser es una buena idea para hoy. Las señales se suman **en valor absoluto** —importa
      el volumen, no el signo— y saturan con la misma curva `n/(n+k)` que la confianza del
      gusto, con la misma `k = 2.0`: en una casa que come tres veces por día, dos raciones
      descontadas son aproximadamente un día de haber comido eso. Sin saturar, la
      penalización se desbordaría y un alimento frecuente quedaría suprimido para siempre —
      el problema original con el signo dado vuelta.
- [x] Un favorito sigue siendo favorito, pero deja de aparecer tres días seguidos.
      → Medido, con la perilla en `_SATIETY_PENALTY = 0.15`. Un favorito con ocho comidas
      hace un mes sale en **0.5717**; el mismo favorito comido **ayer** sale en **0.5437**,
      así que pierde contra una alternativa igual de querida y **sigue arriba de 0.5**; y el
      mismo con una comida más hace **una semana** sale en **0.5755**, o sea *más* que el
      original, porque a siete días la saciedad ya se apagó y lo único que agregó esa comida
      es gusto. Ese es el reparto entero que pedía la 4.4.6, en tres números.
      La presión sola, para tener la escala a la vista: una comida hace 12 h da `0.284`
      (resta `-0.043`), tres dan `0.543` (`-0.081`), treinta dan `0.923` (`-0.138`); las
      mismas tres comidas hace 3 días dan `0.273`, hace una semana `0.056` y hace dos
      semanas `0.002`. Se apaga por decaimiento, no por un corte: no hay ninguno.
- [x] **La perilla vale 0.15 —lo mismo que un "no"— y no por simetría estética.** Tiene que
      poder **cancelar** el boost acumulado de un favorito, cuyo techo es
      `_POSITIVE_SIGNAL_BOOST = 0.12`, o el bucle sigue empujando a repetir; y no más que
      eso, o un favorito comido ayer se caería de la lista en vez de bajar un puesto. La
      consecuencia que conviene tener a la vista: para señales del **mismo** día la saciedad
      y el boost puntual crecen con la misma curva, así que la resta neta queda en
      `-0.03 * presión` — comer algo hoy lo hace, hoy, apenas menos sugerible.
- [x] **No filtra**, como los otros dos ejes nuevos: haber comido milanesas ayer no es un
      "no" a las milanesas, es un "hoy otra cosa". Y un veto duraría más que su propia causa,
      que se apaga en un par de días.
      Tampoco es la penalización por diversidad, aunque se parezcan: esa mira lo que la app
      **sugirió** y es un escalón fijo de `-0.20` por siete días; esta mira lo que la persona
      **hizo** y se apaga sola. Una comida que la app nunca sugirió no lleva la primera.
- [x] Ocho tests nuevos (`TestSatiety`): el favorito comido ayer que pierde contra uno que
      no, el favorito que a la semana vuelve a ser el favorito entero, los dos relojes
      medidos sobre la misma fila, que un dicho no llena, que los tres actos sí, que nunca es
      un veto, que la presión satura, y que no es la penalización por diversidad.
- [x] **Cinco tests viejos había que arreglar, y el arreglo dice algo:** los cinco creaban
      señales de consumo **sin fecha** —el helper las deja así a propósito, porque
      `created_at` lo pone la base— y desde este eje una señal de consumo sin fecha es
      consumo que está pasando *ahora*, o sea presión máxima. Es el caso correcto, no un
      accidente: un acto en curso llena. Así que los tres tests de integración
      (`TestWhatIsLearnedChangesWhatIsSuggested`) ahora corren las señales al pasado con un
      helper `_backdate()` que explica por qué —en la vida real la sugerencia se calcula
      cuando corre el job, no en la transacción que registra la cena—, y
      `test_a_recent_taste_outranks_an_old_one` pasó su señal "reciente" de 1 día a 10.
      El quinto es el interesante: `test_confidence_saturates_instead_of_growing` medía la
      forma de la curva de saturación **a través del score**, y ahora la mide sobre la fuerza
      aprendida. El ajuste es `knob * fuerza`, o sea lineal, así que la forma es idéntica en
      los dos lados — pero el score suma además decaimiento y saciedad, y ponerle una fecha
      para apagar la saciedad apaga también la mitad de la evidencia: la curva medida así ya
      no es la de la saturación. El test estaba midiendo tres ejes y afirmando algo sobre
      uno.
- [x] **Postergado a propósito: la saciedad cruda que ya existe en `meal_generator`.**
      **Cerrado en 4.5.8**, y de las tres cosas que este párrafo nombra se fue **una**: el
      descuento de confianza. La regla de variedad y el `recent_count >= 3` **quedan** y no por
      olvido — la primera *elige* el alimento del que la tarjeta puede afirmar "hace tiempo que
      no comés esto" y el segundo hace que la sección se calle cuando el empujón no aporta nada;
      ninguna de las dos es un descuento, y el docstring de `generate()` traza la línea. El
      "ningún test afirma hoy ese comportamiento" también quedó cerrado: `TestTheConfidenceLadder`
      y `TestThePantryCardCountsWhatItNames` lo afirman ahora.
      El generador descontaba la confianza del candidato por frecuencia
      reciente (`confidence = max(0.5, 0.85 - 0.05 * freq_penalty)`), y hay además una regla
      de variedad que excluye lo muy repetido y otra de preferencia que se saltea con
      `recent_count >= 3`. Es el mismo problema resuelto peor —un escalón plano de 7 días,
      sobre el contenido de la *tarjeta* en vez del sujeto, y sin decaimiento— y hoy convive
      con el eje nuevo. No se toca en la 4.4.6 porque cambiarlo mueve la confianza base de
      todos los candidatos de comida, que es lo que muchos tests usan como referencia, y
      **ningún test afirma hoy ese comportamiento**. Va con la 4.5, junto con el
      `UserContext`, que es donde el generador se reescribe de todos modos.

**4.4.7 — El "no" que hoy se pierde** ✅ hecho

- [x] `snoozed` guardaba `value=0.0` y no entraba ni en la lista positiva ni en la negativa:
      **escrito y jamás leído**; y `dismissed` guarda `-0.3` y cae en la negativa, así que
      **"posponer" actuaba como rechazo**. `snoozed` es ahora una supresión acotada en el
      tiempo vía `snoozed_until`. **Corrección:** ese campo existía en `Notification`, no en
      `Suggestion`; en `Suggestion` lo agregó la `0003` del prerrequisito, junto con las dos
      columnas de sujeto. Del lado del aprendizaje el prerrequisito ya había hecho su parte:
      `snoozed` no escribe ninguna señal y `dismissed` escribe −0.3 que **baja el score sin
      borrar la tarjeta**. Lo que faltaba acá era la supresión temporal en sí y la ruta que la
      escriba, y es lo que se hizo.
- [x] **Suprimir es un filtro en la generación, no un ajuste de score — y esa distinción es
      todo el punto de la 4.4.7.** Hasta acá lo único que reservaba un sujeto era una fila en
      `pending`, así que "Ahora no" *despendía* la fila: el sujeto quedaba libre, y en la corrida
      siguiente del job volvía a escribirse la misma tarjeta apenas **−0.20** más abajo, el escalón
      fijo de la penalización por diversidad (`scorer._DIVERSITY_PENALTY`, que no se multiplica por
      nada y se cobra igual porque `recent_subjects` mira **toda** sugerencia de los últimos 7 días
      sin importar su status). Si la respuesta había sido `dismissed` se sumaban otros −0.045 de la
      señal de descarte (`_NEGATIVE_SIGNAL_PENALTY` × fuerza); un `snoozed` puro no escribe señal,
      así que ahí el único descuento era el −0.20. Y eso es lo que hace el argumento más fuerte, no
      más débil: −0.20 sobre una confianza de 0.95 **seguía saliendo primera**. El gesto de la
      persona producía exactamente lo que quería evitar. Ahora `RecommendationEngine._still_suppressed()` es
      `or_(status == "pending", snoozed_until > ahora)` y se evalúa **antes de persistir**, no
      sobre el ranking.
- [x] **Qué respuestas callan al sujeto, y por qué solo esas dos.** `snoozed` es supresión pura
      —no escribe señal— y `dismissed` escribe −0.3 *y* suprime: las dos dicen "no ahora", y lo
      que las separa es lo que dicen **además**. `accepted` no suprime porque el candidato ya se
      cumplió y puede volver a proponerse. `rejected` no lo necesita: `learning.rejected_subjects`
      lo saca de la lista mientras el "no" conserve la mitad de su peso —90 días de vida media—,
      que es muchísimo más que cualquier ventana.
- [x] **`_SNOOZE_DAYS = 3` tiene piso y techo, y los dos importan.** El job de sugerencias corre
      dos veces por día a hora local fija —7:40 y 18:40, `scheduler._SCHEDULE`, desde la 4.1—,
      así que una ventana más corta que el hueco **más largo** entre dos corridas (13 h, el de
      18:40 a 7:40 — el otro es de 11 h) sería **invisible**;
      y tiene que quedar por debajo de los 7 días de la penalización por diversidad
      (`scorer._RECENT_SUGGESTION_DAYS`), que así queda como el escalón siguiente: primero el
      sujeto no aparece, después aparece pero más abajo, y al final vuelve a competir de igual a
      igual. **No hace falta ningún job que resucite nada**: la condición se evalúa contra el
      reloj en cada corrida, así que el sujeto se destraba solo.
- [x] El motivo de texto libre —que moría en `notes`— ahora **se lee**: se pasa por el matcher de
      nombres conocidos —el catálogo de alimentos (`FoodRepository.known_names`, con alias y sin
      filtrar por categoría, porque los alimentos sin categoría son justamente los que creó
      `get_or_create` con lo que la casa escribió) y las actividades de `app/nlp/rules.py`— y se
      graba una señal por cada nombre encontrado. *"No me gusta el brócoli"* sobre una tarjeta
      titulada "Cená algo verde" enseña sobre **el brócoli**, no sobre las cosas verdes. La frase
      en sí sigue viviendo en un solo lugar, `suggestions.feedback_notes`; ninguna señal la copia
      (ver el punto de los límites de seguridad).
- [x] **El matcher busca nombres, no interpreta la frase.** Vocabulario cerrado, frase completa
      con espacios alrededor (así "té" no aparece dentro de "tenemos"), del más largo al más corto
      y **consumiendo** cada coincidencia (así "queso crema" no enseña además sobre "queso"). Y
      deliberadamente **no** reusa `_parse_preference`, que se queda con `" ".join(words[:3])` y
      de *"no es para nosotros, el yoga nos aburre"* inventaría el sujeto "para nosotros"; la
      función nueva `rules.find_known_activities` recorre el mismo `_EXERCISE_RE` anclado que ya
      existía.
- [x] **Se busca por cualquiera de sus nombres y se graba por el canónico**, y esto no es un
      detalle: el catálogo se escribe con el canónico en inglés y el castellano como alias
      —`FoodItem.aliases_json`, `["tomato", "tomate"]`— y los candidatos declaran su sujeto con
      `food.canonical_name`. Con un vocabulario de nombres planos, *"no nos gusta la palta"*
      grababa una señal sobre `palta` que ningún candidato llamado `avocado` iba a encontrar
      nunca: **aprendida y jamás leída**, y justo en el caso normal de una casa que escribe en
      castellano, no en un borde. Por eso `FoodRepository.known_names` devuelve un mapa
      nombre → canónico —los canónicos se escriben último, así que un nombre que es canónico de
      uno y alias de otro se resuelve a sí mismo— y `learning.subjects_in_text` graba el canónico.
      Con dedup: *"ni palta ni aguacate"* es un sujeto, no dos observaciones del mismo peso.
- [x] **El signo lo pone la respuesta, y lo minado nunca veta.** Una señal `explicit_preference`
      por sujeto minado, con el mismo valor que la respuesta (−1.0 un rechazo, −0.3 un descarte,
      y positivo si el motivo viene junto a un "sí" por la ruta JSON) — no una perilla nueva: un
      "no" tibio nombrando el brócoli es un "no" tibio al brócoli. Y como
      `learning.rejected_subjects` solo mira `rejected_suggestion`, un sujeto minado **baja el
      score y jamás filtra**: leer texto libre puede equivocarse, y el costo de equivocarse
      ordenando es que algo salga tercero, el de equivocarse filtrando es que no salga nunca y
      nadie entienda por qué. Hay un test que existe para que eso siga siendo gratis.
- [x] **El sujeto de la propia tarjeta se saltea** —ya lo grabó el bloque de arriba, nombrarlo en
      el motivo no lo hace pesar el doble—, pero **la minería corre antes del chequeo de sujeto**:
      una fila anterior a la `0003` no tiene sujeto propio que grabar y antes no aprendía nada;
      ahora, si la persona escribió por qué, eso sí se aprende. Y **`snoozed` no mina**, que
      también es a propósito: "más tarde" es una afirmación sobre *el momento*, no sobre la cosa
      —"hoy no, comimos brócoli al mediodía" explica la demora—, y leerlo como un veto sería
      inventar una opinión que nadie dio. El formulario de la tarjeta manda `rejected` justamente
      para no depender de esa distinción.
- [x] **Dónde se escribe el motivo:** un `<details>` colapsado en la tarjeta de sugerencia
      (`suggestions/partials/list.html`) — el único lugar de la app que puede mandar
      `feedback_notes`— que postea `status=rejected` a la ruta que ya existía. **Dos guardas de
      largo, no una:** la ruta web recorta a 500 (así un pegado largo sin JS no se convierte en un
      422 para una persona) y el schema tiene `max_length=500` (así la ruta JSON contesta el 422
      que le corresponde). El `maxlength` del input dice lo mismo, y estas dos son las que lo
      hacen cierto: `feedback_notes` es una columna `Text` sin tope, y HTMX manda el formulario
      sin validar `maxlength` del lado del cliente.
- [x] **Tres límites que puso la revisión de seguridad, los tres sobre el mismo eje: cuánto de
      una frase personal se copia y a dónde.** (1) `_MAX_MINED_SUBJECTS = 5`: el largo del texto
      no acota el trabajo —500 caracteres alcanzan para nombrar decenas de alimentos del
      catálogo, y sin tope una sola respuesta escribía decenas de filas en `behavior_signals`,
      una tabla sin poda, repetible a la velocidad de un POST—; y una frase que nombra veinte
      cosas no es una preferencia sobre veinte cosas. (2) **Ninguna señal copia el motivo**, ni la
      minada (`context={"mined_from": "feedback_notes"}`) ni la de la propia tarjeta
      (`{"reason_written": True}`): las dos guardan un marcador y nada más. Copiarlo en la de la
      tarjeta parecía gratis —una sola fila, no una por sujeto—, pero el problema no es la
      multiplicidad sino la retención: `behavior_signals` no tiene job de poda, así que la frase
      que la persona escribió se quedaba para siempre en una segunda tabla y **borrar
      `feedback_notes` no la borraba**. Queda una sola vez, en la fila a la que apunta
      `source_entity_id`, que es donde se puede borrar. (3) **El sujeto minado con su signo va a DEBUG,
      no a INFO**: producción corre en INFO (`app/main.py`), así que un `docker compose logs`
      mostraba los gustos alimentarios y de entrenamiento de la casa sin permiso sobre la base.
      El INFO que queda lleva solo la cuenta, que es la convención del repo.
- [x] **Dos límites que quedan escritos en vez de descubrirse.** El signo es uno para toda la
      frase, así que *"no nos gusta el brócoli, preferimos el pollo"* graba −1.0 para los dos:
      separarlos pide leer el alcance de la negación, que es la interpretación que este matcher
      no hace, y se aguanta porque lo minado ordena y no filtra —el pollo baja un puesto y
      vuelve a subir con la primera compra o comida que lo confirme—. Y el vocabulario de
      actividades es de **claves en inglés** (`rules._EXERCISE_MAP`), así que de una frase en
      castellano solo salen los nombres que se escriben igual en los dos idiomas: son varios,
      porque el castellano rioplatense los toma prestados —"yoga", "pilates", "spinning",
      "crossfit", "cardio", "running", "hiit", "zumba"—, pero lo que la casa escribiría en
      castellano y el mapa no tiene no aparece: *"odio correr"*, *"caminar"*, *"pesas"* no
      enseñan nada hoy. Ensancharlo se hace en la 4.5, donde `ExerciseType` reemplaza esa
      lista fija de todos modos y los nombres salen de la base.
- [x] 31 tests nuevos: 18 en `test_learning_signals.py` (el matcher solo —nombre largo que se
      come al corto, nombre desconocido que no enseña nada, nombre escondido dentro de otra
      palabra, la actividad que el parser viejo hubiera inventado, el vocabulario que incluye lo
      que la casa inventó y devuelve el canónico de cada nombre, el alias que enseña sobre el
      canónico, los dos nombres del mismo alimento que son un solo sujeto— el camino del servicio
      incluida una frase en castellano que llega a un candidato nombrado en inglés, y el tope de
      cinco sujetos por motivo), 9 en
      `test_recommendations.py`
      (`TestNotNowMeansNotNow`: que posponer escriba una ventana y no solo conteste la fila, que
      la ventana dure más que un ciclo del job y menos que el escalón de diversidad, que el sujeto
      no vuelva a ofrecerse, que al vencer vuelva a competir, que aceptar no calle nada, que
      rechazar se apoye en el filtro y no en la ventana, y **que el silencio del otro miembro no
      sea el tuyo** — la regla 4 de `AGENTS.md` del lado de la lectura, incluida la lista de
      compras del household), y 4 en `test_web_pages_populated.py` (el formulario como escritor
      real, el recorte en el borde, y el 422 de la ruta JSON).

**4.4.8 — Que se pueda ver y corregir lo aprendido** ✅ hecho (menos
`evidence_summary`, que es de la 4.5)

- [x] Un panel *"lo que GaiaPulse fue aprendiendo"* en el perfil
      (`app/templates/profile/partials/learned.html`), agrupado por tipo de sujeto y ordenado
      por `GROUP_ORDER`, con las **tres cifras que hacen discutible una deducción**: cuántos
      registros la respaldan, cuántos de esos fueron **palabras** —`explicit_preference`, o sea
      una preferencia declarada o un nombre minado del motivo escrito, que es lo que se corrige
      hablando— y hace cuánto fue el último. Hasta acá
      `behavior_signals` no tenía **ninguna** lectura de usuario: las cuatro rutas de
      aprendizaje movían el orden de las sugerencias y lo único que la casa podía hacer con
      una deducción equivocada era recibir sugerencias raras.
- [x] Nuevo `app/services/learning_service.py` (`LearnedProfile`, `LearnedGroup`,
      `LearnedCategory`) como única entrada de lectura: la ruta web no toca
      `app/recommendations/learning.py` ni los repositorios. `LearnedSubject.days_since` es un
      **campo** y no una propiedad con su propio reloj, porque la recencia impresa y el
      descuento por edad de la afinidad tienen que hablar del mismo instante.
- [x] `POST /profile/learned/forget`: borra las señales del sujeto y devuelve el panel
      recalculado por HTMX (`hx-target="#learned-panel"`, `hx-swap="outerHTML"`), o un 302 a
      `/profile/` con el flash puesto cuando no hay JS —el `<form>` lleva `action` además de
      `hx-post`—. Confirmación en dos pasos con Alpine y **un `x-data` por fila**, igual que
      el borrado del panel de sangre: sin `confirm()` ni `hx-confirm`, que no se pueden
      traducir desde una plantilla y no dicen qué se pierde.
- [x] Olvidar **borra filas**: lo aprendido *es* el conjunto de señales, así que no hace falta
      una columna de "olvidado" (y no habría: la `0003` ya está gastada). El `DELETE` resuelve
      los ids en Python y no en SQL, porque `record` guarda `entity_name.lower()` con tildes
      ("brócoli") y el formulario manda la forma comparable de `normalize_subject` ("brocoli"):
      un `WHERE entity_name = ?` con lo que el formulario manda borraba cero filas y contestaba
      que todo bien, que es la peor de las dos formas de fallar. Cero borrados se dice como
      cero borrados, no como éxito. Y el mensaje nombra el sujeto **como estaba guardado**
      (`Forgotten.subject_name`) y no lo que llegó: un `PÓLLO!!!` escrito a mano borra las filas
      de "pollo", y contestar "Olvidado: PÓLLO!!!" sería devolver a la pantalla un texto que la
      persona nunca guardó, sobre una acción que no se puede deshacer.
- [x] El panel entero vive **adentro** del target del swap —título, mensaje, grupos,
      categorías y la nota final—, porque `outerHTML` reemplaza todo eso; y la región viva
      (`role="status" aria-live="polite"`) envuelve al fragmento desde `profile/index.html` y
      **no** se intercambia: un `aria-live` que se reemplaza a sí mismo no anuncia su contenido
      nuevo (el mismo motivo que en `suggestions/partials/list.html`).
- [x] El bloque de categorías (la generalización de la 4.4.3, lo que hace que rechazar
      brócoli, coliflor y kale diga algo sobre la espinaca) va **sin botón de olvido y con la
      explicación de por qué**: una categoría no tiene señales propias —`ATTRIBUTE_SUBJECT_TYPES`
      está fuera de `SUBJECT_TYPES`, así que `record_signal` la rechaza—, se deriva del catálogo
      en cada lectura. Se olvida olvidando los alimentos que la sostienen.
- [x] El panel dice los dos límites que no puede callar sin mentir: olvidar **no es un veto**
      (si la conducta se repite se vuelve a aprender; el veto real son las restricciones
      alimentarias y las actividades imposibles, que filtran en vez de reordenar) y el
      horizonte de lectura (`SIGNAL_HORIZON_DAYS`), para que "no aparece" no se confunda con
      "se olvidó".
- [x] **Los dos rótulos falsos, corregidos juntos.** `/profile/` titulaba
      `recommendation_preferences` como *"Learned Preferences / Preferences learned from your
      feedback on suggestions"* y `/suggestions/` como *"What we learned about you / Comes from
      your answers and from what you log"*: esa tabla es lo que la persona **dijo** (captura de
      texto o formulario), nunca el feedback de una sugerencia, que va a `behavior_signals`.
      Ahora las dos son *"Lo que nos dijiste"* —se corrige diciendo otra cosa— frente a *"Lo
      que GaiaPulse fue aprendiendo"* —se corrige olvidando—, y `/suggestions/` linkea al panel
      del perfil para que la otra mitad no la encuentre solo quien ya sabe que existe.
- [x] Nuevo macro `dm.learned_recency_label(days)` junto a los de dirección y confianza, para
      que la forma de decir la recencia viva en el único módulo de rótulos.
- [x] Los **41** `msgid` nuevos traducidos al castellano rioplatense en el mismo commit, con
      `ngettext` en los cuatro plurales (el catálogo pasa de 526 a 567 entradas). El resto del
      atraso de i18n (re-medido con la 4.4.8 ya aplicada: **126 de 517** `msgid` del repo sin
      entrada en `es_AR`) queda como el punto de la 5.1.
- [x] 16 tests nuevos en `tests/test_web_learned_panel.py`: que el panel muestre lo que el
      motor efectivamente lee con las cifras detrás, que el respaldo de una categoría cuente
      **solo las señales que son una opinión** (posponer no suma un ítem a una conclusión que
      no movió), que el estado vacío **siga estando**
      (un panel que solo aparece cuando ya aprendió algo no se puede encontrar antes de
      aprender nada, que es cuando alguien se pregunta si la app lo está mirando), que las dos
      pantallas ya no llamen "aprendido" a lo declarado, que olvidar borre y devuelva el
      fragmento sin la fila, **que el nombre impreso encuentre la fila que lo guarda** (el bug
      de las tildes), que cero borrados lo diga, que olvidar **no toque las señales del otro
      miembro** (regla 4 de `AGENTS.md` en el caso que la vuelve concreta), el camino sin HTMX
      con la barra final del destino, el recorte del nombre largo en vez del 422 que devuelve
      el texto recibido, y que la recencia se mida contra el mismo instante que la afinidad.
- [x] **Los dos bloqueos que encontró la revisión del punto, y cómo se cerraron.** El panel
      estaba listo y decía dos cosas falsas, las dos por el mismo motivo: la pantalla afirmaba
      una separación que los datos no sostenían.
      - **B1 — una preferencia declarada aparecía adentro del panel de "lo que aprendimos
        solos", con un botón de olvido que no podía cumplir.** `save_preference` escribe la fila
        de `recommendation_preferences` **y** una señal `explicit_preference` en la misma
        transacción, así que un "no me gusta el hígado" declarado entraba a la agregación como
        cualquier otra señal. Olvidarlo borraba las señales y dejaba la preferencia —que si es un
        "no me gusta" **filtra** (`filters.py:57-63`), no reordena— sin ninguna ruta que la borre:
        un "listo, lo olvidé" sobre algo que sigue vetando. Ahora `LearnedSubject.declared`
        (detectado por el `explicit_preference` **sin** sugerencia de origen, que es el único
        camino que escribe las dos filas) suprime el botón y el formulario de confirmación, y en
        su lugar la fila apunta a `#what-you-told-us`, que es donde eso sí se puede cambiar. Las
        señales **siguen** en la agregación: sacarlas haría que el panel discrepe del scorer, que
        es exactamente lo que el panel vino a evitar. Y el subtítulo dejó de decir "no de lo que
        nos dijiste" y "podés borrar cualquier cosa de acá", porque ninguna de las dos era cierta.
      - **B2 — "N de lo que dijiste" contaba toques de botón.** Contaba
        `source_type == "explicit"`, que es también como `respond_to_suggestion` marca el
        accepted/rejected de un **tap** en una tarjeta. Un solo descarte imprimía "1 de lo que
        dijiste" al lado de un alimento sobre el que nadie escribió una palabra — y esa distinción
        es la razón de ser de la línea. Ahora cuenta `signal_type == "explicit_preference"`.
- [x] **Los cortes salieron de la plantilla.** `SubjectAffinity.direction_band`
      (`toward`/`away`/`mixed`, borde en ±0.2) y `.confidence_band` (`plenty`/`some`/`new`, en 3×
      y 1× `half_saturation`) devuelven **palabras**, y los macros de `components/domain.html`
      solo mapean palabra → rótulo. Una banda desconocida rinde **nada** a propósito: un
      `{% else %}` que la rotulara con la etiqueta más cercana la mostraría mal y en silencio,
      mientras que un hueco se ve mirando la pantalla.
- [x] **La fila muestra un nombre y manda otro**, y está bien: se imprime
      `LearnedSubject.display_name` —la grafía con tildes de la señal más reciente, porque
      "brocoli" impreso en una app en castellano se lee como un error de la app— y el campo
      oculto lleva la clave normalizada, que es la que matchea las dos grafías.
- [x] **El panel muestra solo los cinco `SUBJECT_TYPES`** que el scorer sabe leer, con dos tests
      que lo atan: `set(GROUP_ORDER) == learning.SUBJECT_TYPES` en `test_learning_signals.py`
      —la obligación que reemplaza a la rama defensiva que se sacó de `learned_profile`— y uno
      parametrizado en `test_components.py` que exige rótulo, ícono y tono **propios** por tipo,
      así que un sexto tipo no puede salir titulado "Muscle Group" en inglés por el fallback.
      Otros cuatro tests de componentes cubren las bandas: una por badge, una por rótulo de
      confianza, la banda desconocida que no rinde nada, y la recencia que dice los dos primeros
      días con palabras.
- [ ] `Suggestion.evidence_summary` ya existe en el modelo y hoy nadie lo escribe: es el lugar
      natural para guardar la explicación computada que 4.5 produce.

**4.4.9 — El aprendizaje es por persona, siempre** ✅ hecho

- [x] **El punto de partida era peor que "se promedia": no se filtraba nada.**
      `generate_for_household` tenía un comentario que decía *"for household suggestions we
      skip user-specific filtering"* y era literal — cero filtros, ni los duros ni los
      aprendidos. Como `pantry_generator` propone alimentos concretos
      (`subject_type="food"`), la lista de compras podía traer justo lo que una de las dos
      personas no puede comer. La 4.5 le pone llamadores a esa función; convenía que cuando
      se prenda ya no lo haga.
- [x] Nuevo `filters.HouseholdMember` (dataclass congelada: `user` + sus `preferences` + sus
      `signals`) y `filters.apply_household_constraints`. Los datos entran **ya separados por
      persona**, no como un conjunto "de la casa": una intersección solo se puede calcular
      sobre conjuntos que nunca se mezclaron, así que juntarlos primero y desarmarlos después
      no era una opción.
- [x] **Las dos reglas van al revés a propósito, y esa asimetría es todo el punto.** Los
      **bloqueos declarados se unen**: alcanza que una persona tenga el maní bloqueado para
      que la casa no compre maní —una restricción declarada no pide evidencia ni admite
      promedio, y el costo de equivocarse no es simétrico: de un lado una compra de más, del
      otro una comida que alguien no puede comer—. Los **"no" aprendidos se intersectan**: un
      rechazo de conducta de una sola persona no es un "no" de la casa, porque unirlos dejaría
      que un rechazo de Rocío borre de la lista el alimento que Diego come todos los días.
      Solo se saca un sujeto si **todas** lo rechazaron; hasta entonces sigue compitiendo, más
      abajo si corresponde —eso es trabajo del score, no de este filtro—.
- [x] `_household_members` en el engine reusa `_get_preferences` y `_get_signals`, que filtran
      por `user_id`: tres consultas por persona en lugar de una por casa, a propósito. Es la
      regla 4 de `AGENTS.md` —cada consulta de datos personales filtra por la persona, aunque
      quien pregunte viva en la misma casa— y además es lo que hace posible la intersección: un
      `WHERE household_id = ?` traería las señales de los dos revueltas.
- [x] **La falla segura elegida a mano:** `set.intersection()` sobre cero conjuntos es un
      `TypeError`, y con un solo miembro la intersección es su propio conjunto. Sin miembros el
      filtro devuelve los candidatos tal cual y loguea un warning —no hay nadie de quien
      proteger a nadie—; la alternativa silenciosa habría sido borrar la lista entera y dejar a
      una casa sin sugerencias en vez de con sugerencias sin filtrar.
- [x] **Un costo conocido, escrito como decisión y no como accidente.** La tarjeta de "se
      acabaron estas cosas" nombra hasta cinco alimentos en su texto y el matcher de bloqueos
      duros mira `title + text` —así funciona `apply_hard_constraints` desde antes, en el camino
      personal también—, así que si uno de los cinco está bloqueado se cae la tarjeta entera.
      Se acepta en esa dirección: la alternativa es dejar pasar una tarjeta que nombra lo que
      alguien no puede comer, y el aviso por ítem de la 4.3 no depende de esta tarjeta. Hay un
      test que lo fija con ese razonamiento adentro.
- [x] **Duplicación sacada de paso, dentro de lo que el cambio ya tocaba.** El descarte en sí
      salió a `filters._drop_blocked`, que ahora usan los dos caminos —el personal y el de la
      casa—: la única diferencia entre ellos es de dónde salen los conjuntos bloqueados, no cómo
      se comparan, y dos copias de esa comparación es cómo un bloqueo empieza a valer en una
      pantalla y no en la otra. Y las señales que bloquean estaban escritas **dos veces**
      (`("dislikes", "impossible", "avoid")` para comida y las mismas tres en otro orden para
      actividad): ahora son un `frozenset` único, `_BLOCKING_SIGNALS`. Esa es la forma de
      duplicación que más cara sale —dos listas que tienen que coincidir y que nada obliga a
      coincidir—.
- [x] **Lo que encontró la revisión de instrucciones, y por qué era un bug y no una
      prolijidad.** `_household_members` había escrito su propia consulta de usuarios de la
      casa, cuando `UserRepository.get_household_users` ya existía con el mismo `ORDER BY id`
      **y** un `is_active` que la copia no tenía. Esa diferencia apagaba en silencio media
      4.4.9: una persona desactivada entraba a la lista sin señales, y como los "no"
      aprendidos se **intersectan**, un conjunto vacío hacía que ningún rechazo sacara nunca
      nada. Es exactamente la forma de duplicación que el commit de instrucciones acababa de
      nombrar —una regla escrita dos veces, las dos copias en desacuerdo, nada que falle—, así
      que la regla nueva se estrenó contra el mismo diff que la introdujo. Ahora el engine
      llama al repositorio (un `db.query()` directo menos en `app/recommendations/`, de 15 a
      14) y hay un test que fija la dirección.
- [x] 14 tests nuevos en `tests/test_household_learning.py`, escritos para que **unificar las
      dos reglas rompa la mitad del archivo**: que el bloqueo de uno alcance (y que dé igual de
      quién sea), que el rechazo de uno no borre la comida del otro, que el rechazo de las dos
      sí, que dos "no" de sujetos distintos no se sumen a uno, que diez rechazos de una persona
      sigan siendo una persona, la falla segura sin miembros, el candidato sin sujeto, que
      `_household_members` traiga a cada uno solo lo suyo, y **tres end-to-end** por
      `generate_for_household` —incluido el control de que el mismo stock sí llega a la lista
      cuando nadie lo bloquea, sin el cual "no salió nada" podría ser que el generador no
      produjo nada— y el de la persona desactivada, que es el que ata el `is_active`.

**4.4.10 — Umbral de evidencia para filtrar, y la ausencia como el "no" que nadie aprieta**
✅ hecho

- [x] **Los tres van en un commit porque cada uno solo tiene sentido con los otros dos**, y eso
      ya estaba escrito en la 4.4.3: el umbral sin la ausencia es un lector sin escritor —el
      único negativo que filtraba era `rejected_suggestion`, siempre `-1.0` desde un tap
      deliberado—, la ausencia sin el umbral es un veto por no haber comido lentejas, y la
      ausencia sin barrido no existe, porque el dato que la produce es el paso del tiempo.
- [x] **Umbral mínimo de evidencia antes de vetar.** `learning.rejected_subjects` pasó de una
      comprensión por señal a un **agregado por sujeto**: suma `abs(signal_weight(...))` de las
      señales negativas que además pasan la guardia de frescura de la 4.4.2, y veta solo si el
      total llega a `_FILTER_EVIDENCE_FLOOR = 0.5`. La firma no cambió, así que
      `apply_signal_constraints` y `apply_household_constraints` no se tocaron.
      La aritmética elegida a mano, no derivada: un rechazo deliberado (`-1.0`, fresco) sigue
      vetando solo, **exactamente como antes**; una ausencia (`-0.2`) suma 0.2, dos 0.4, tres
      0.6 → hacen falta **tres** para vetar. El piso no se calcula desde `ABSENCE_VALUE` ni
      desde `_FILTER_DECAY_FLOOR` a propósito: son unidades distintas (peso acumulado vs.
      fracción de decaimiento), y atarlos haría que mover uno moviera el otro sin querer.
- [x] **La guardia de frescura no se derogó, se le sumó el piso.** Diez ausencias de hace tres
      meses siguen sin vetar nada, porque cada señal tiene que pasar primero
      `decay_factor >= 0.5` para *entrar* a la suma. Sin eso, "acumular" volvería a ser lo que
      la 4.4.2 vino a arreglar. Hay un test con ese nombre.
- [x] **La ausencia como señal débil.** Nuevo `learning.ABSENCE_SIGNAL_TYPE =
      "unused_suggestion"`, plegado por nombre en `NEGATIVE_SIGNAL_TYPES`, con
      `ABSENCE_VALUE = -0.2` —un quinto de un "no" explícito— y `source_type="inferred"`, que
      es la vida media corta. No apretar nada es información, pero es la más débil de todas:
      puede ser que no gustó, o que ese día no había, o que nadie miró la tarjeta.
- [x] **`ABSENCE_SUBJECT_TYPES = {"food", "exercise"}`**, y el recorte sale del censo de
      escritores: solo esos dos tienen un acto que pueda **desmentir** la ausencia. Con
      `muscle_group` la señal se escribe solo si la captura trae la columna, así que su
      ausencia sería falsa la mayoría de las veces; `habit` y `biomarker` no tienen escritor de
      acto ninguno, así que su ausencia sería infalsable.
- [x] **El barrido: `suggestion_jobs.run_absence_sweep`**, a las 6:30 locales en
      `scheduler._SCHEDULE`, **antes** de la generación de las 7:40 —lo que se aprendió anoche
      reordena las tarjetas de hoy, no las de mañana— y sin gate de horario de silencio, porque
      escribe filas y no avisa a nadie. Nuevo
      `SuggestionRepository.get_stale_pending(user_id, subject_types, created_before,
      created_after)`, personal por regla 4; `subject_types` viaja como parámetro porque el
      repositorio no puede importar `learning` (circular), y `created_after` es obligatorio a
      propósito —el punto siguiente explica por qué la ventana no puede tener un borde por
      omisión—.
- [x] **Tres decisiones del barrido que un lector futuro va a querer explicadas:** la
      idempotencia sale de las propias señales y no de un `NOT EXISTS` —el barrido ya tiene que
      traer esas filas para el chequeo de "¿lo hizo igual?", y una segunda copia de la misma
      pregunta obligaría además a bajarle el vocabulario del aprendizaje al repositorio—; el
      `since = min(created_at)` de las tarjetas viejas es **carga útil, no una optimización**,
      porque hace que un acto anterior a la tarjeta no pueda cancelar la ausencia (haber comido
      lentejas el mes pasado es justamente **por qué** se sugirieron); y el nombre se compara en
      Python con `learning.subject_key` y no en SQL, porque `Suggestion.subject_name` guarda
      "brócoli" y `BehaviorSignal.entity_name` guarda "brocoli".
- [x] El tipo se escribe como **literal** (`signal_type="unused_suggestion"`) y no como la
      constante, con un comentario que dice por qué: es la única forma que
      `test_every_signal_type_the_reader_knows_has_a_writer` puede detectar, y ese test es el
      único mecanismo que atrapa un tipo que se lee y nadie escribe —el bug original de
      `repeated_purchase`—.
- [x] **El panel de 4.4.8 no miente sobre de dónde viene lo aprendido.** `LearnedSubject` suma
      un cuarto contador, `absence_observations`, y las ausencias quedan **fuera** de
      `observations`, `said_observations`, `last_seen` y `days_since`: un sujeto que solo
      acumuló ausencias muestra "3 sugerencias sin usar" y ninguna fecha, porque la persona
      efectivamente nunca hizo nada con él. Los msgids nuevos los toma la 5.1 con los otros 126.
- [x] 16 tests nuevos: 12 en `TestAbsenceSweep` (el período de gracia, la señal con su puntero
      `source_*`, idempotencia entre dos corridas, "lo hizo igual" y su espejo temporal, la
      tarjeta respondida y la de la casa que no se barren, `habit` que no se barre,
      `ABSENCE_SUBJECT_TYPES <= SUBJECT_TYPES`, la aritmética de una-vs-tres, que un rechazo
      deliberado sigue vetando solo, y la fila del panel sin fechas); 3 en
      `TestSignalConstraints` que fijan el umbral desde el otro lado —una ausencia no alcanza
      pero sí baja el score, tres sí filtran, y diez viejas no—; y 1 en `tests/test_clock.py`
      que fija el orden contra la generación de la mañana. El total sube 17 y no 16 porque el
      test parametrizado que exige `CronTrigger` en la timezone de la casa **se llenó solo** al
      entrar el job nuevo al `_SCHEDULE`: es la forma que tiene ese archivo de no dejar que un
      job nazca sin reloj local.

- [x] **Corrección del propio punto, antes de commitearlo: la idempotencia estaba puesta en el
      lugar equivocado.** Derivarla solo de las señales tiene un caso que la vuelve del revés:
      el botón "Olvidalo" del panel de 4.4.8 **borra esas mismas filas**, así que olvidar
      "lentejas" volvía la tarjeta barrible y el barrido de la mañana siguiente reescribía la
      ausencia que la persona acababa de sacar — todos los días, para siempre, porque la
      tarjeta sigue pendiente. El arreglo es que la elegibilidad pase a ser una **ventana** y no
      un umbral: `get_stale_pending` recibe además `created_after`, y una tarjeta es barrible
      solo si nació dentro de `(corte − _SWEEP_WINDOW_DAYS, corte]` —un día de ancho, porque el
      job corre una vez por día—, así que se barre la mañana en que cumple siete días y ninguna
      otra. El conjunto `swept` **queda**, degradado a segunda línea: cubre el caso que la
      ventana no puede cubrir, dos corridas el mismo día, y no cuesta una consulta porque esas
      filas ya hay que traerlas. Lo que **no** se arregla acá es el doble scheduler de dos
      workers de uvicorn: eso se arregla en el `Dockerfile` (`--workers 1`), que es donde está
      la causa.
- [x] **La aritmética "hacen falta tres" es cierta y a la vez inalcanzable, y decir solo la
      primera mitad era lo que hacía sonar el umbral como una política.** Dos ausencias del
      mismo sujeto no pueden estar a menos de `ABSENCE_GRACE_DAYS + SuggestionService._SNOOZE_DAYS`
      = **10 días** una de otra (la tarjeta que produjo la primera sigue pendiente hasta que
      algo la mueva, y lo único que la mueve sin escribir su propia señal es el snooze), y
      `_FILTER_DECAY_FLOOR` descarta todo lo más viejo que una vida media (21 días): a lo sumo
      **tres** están vivas al mismo tiempo y suman `0.2 · (1 + 0.5^(10/21) + 0.5^(20/21))` ≈
      **0.447 < 0.5**. O sea que `_FILTER_EVIDENCE_FLOOR = 0.5` es **preservador de conducta por
      construcción**: el barrido nunca puede sacar un sujeto de la lista por sí solo, solo
      bajarlo de orden. Eso ahora es un test —`test_the_sweep_can_never_veto_a_subject_on_its_own`,
      que camina ocho ausencias al espaciado mínimo real— y el test que fija la cuenta de
      "tres cruzan el piso" dice en su docstring que **no** describe un camino alcanzable.
      Acortar la gracia, subir `ABSENCE_VALUE` o alargar la vida media rompe el test de la
      conducta, no el de la aritmética.
- [x] **Y dos cosas del propio andamio de tests, que es código igual.** La fábrica `_card`
      estaba escrita dos veces con las mismas once columnas obligatorias —la forma de
      duplicación que nada obliga a coincidir—: queda una a nivel módulo con un parámetro
      `days_old`, y `TestAbsenceSweep._card` sobrevive como delegación de dos líneas que solo
      aporta el sujeto lentejas. Y `TestQuietHoursGate` parametrizaba una **lista escrita a
      mano** de los jobs que hablan, que es exactamente la trampa que el punto vino a cerrar en
      otro lado: un job nuevo que notifica no aparece en la lista y el gate queda sin probar,
      en verde. Ahora la lista la **deriva** `_jobs_that_speak()` leyendo el AST de
      `app/jobs/*.py` con propagación transitiva (los cuatro recordatorios hablan a través de
      `_run_absence_job`), y `test_the_search_finds_the_jobs_that_do_speak_and_only_those` es el
      piso contra el clásico "computó vacío, parametrizó cero casos, pasó".
- [x] 6 tests más en `TestAbsenceSweep`/`TestSignalConstraints` (la tarjeta más vieja que la
      ventana, el olvido que sobrevive a los barridos siguientes, que la ausencia es de quien
      es la tarjeta y no de la casa, la persona desactivada, el veto imposible del barrido, y el
      rechazo que veta hasta que deja de ser fresco y ni un día más) y el gate de silencio
      derivado: **569 passed**.

**4.5 — Razonar con los datos que ya están, y explicar de verdad**

**Antes de implementar: tres cosas que el diagnóstico F5/F7 de este plan dice y que leer el
código desmiente.** El punto se implementa contra el código, no contra la descripción, y las
correcciones cambian *qué* hay que hacer, no solo cómo se cuenta:

1. **`evidence_summary` no está huérfano.** F6 lo agrupa con `serving_size_g` y `micro_json`
   entre las columnas que "ningún código escribe jamás", y es falso: los dos
   `_make_*_suggestion` de `engine.py` lo persisten (`item.get("evidence_summary")`),
   `blood_generator` lo llena para los 16 marcadores, y
   `suggestions/partials/list.html:82` lo renderiza cuando existe. Lo que **sí** es un string
   fijo por regla es `rationale`, y solo él. Así que 4.5.4 tiene un lugar donde poner la
   explicación computada y un renderizador que ya la muestra: no hay que inventar la columna.
2. **El atajo `return candidates` de `_drop_blocked` (`filters.py:215-216`) es preservador de
   conducta por construcción, y esa es la razón para borrarlo.** Con los dos conjuntos de
   bloqueos vacíos las dos ramas de abajo no pueden descartar nada, así que hoy no se escapa
   ni un candidato por ahí. Es una **trampa**, no un agujero: el día que el filtro tenga que
   mirar algo que no sean esos dos conjuntos —una restricción del hogar, un umbral del
   contexto— la línea lo saltea en silencio y en verde. Se borra porque es una optimización de
   cuatro comparaciones que compra un modo de falla silencioso, no porque hoy deje pasar algo.
3. **"Las sugerencias de sangre eluden los dos filtros" quedó a medias con la 4.4.** Desde que
   `apply_signal_constraints` matchea por `learning.candidate_subject`, las tarjetas de sangre
   **sí** atraviesan el filtro de señales aprendidas: tienen `subject_type="biomarker"` y
   sujeto propio. El que sigue esquivándose es `apply_hard_constraints`, y el alcance real es
   **exactamente tres tarjetas**: TSH alta, TSH baja y creatinina alta, las únicas del repo con
   `category="habit"`. Las tarjetas de *sujeto* `habit` de `activity_generator` y
   `pantry_generator` viajan con `category="activity"`/`"shopping"` y ya se filtran. Tres
   tarjetas no es "todo el generador de sangre", y decirlo bien es lo que evita arreglar el
   bypass en el lugar equivocado.

- [x] **4.5.1 — `UserContext`: un solo lector por corrida.** Nuevo
      `app/recommendations/context.py` con una dataclass congelada y `build_user_context(db,
      user)`, armada **una vez** en `engine.generate_for_user` y pasada a los cuatro
      generadores, al scorer y a los filtros. Campos: tendencia de peso y grasa, sueño
      reciente, días desde el último entrenamiento, **días desde el último estímulo por grupo
      muscular**, RPE reciente, contador de alimentos recientes, macros de hoy contra la línea
      de base de la persona, catálogo de `ExerciseType` y panel de sangre con su fecha y su
      antigüedad. **El contexto no decide nada**: es de solo lectura, no opina, y cada
      generador sigue siendo el dueño de su regla — si el contexto empieza a decidir,
      volvimos a tener la lógica en dos lados.
      El **stock del hogar queda afuera**, y eso cambió respecto de cómo estaba escrito acá:
      `UserContext` es estrictamente personal —cada campo sale de una consulta filtrada por
      `user_id`, que es la regla 4 de `AGENTS.md`— y la despensa no es de nadie en particular.
      Meterla en un contexto por persona sería la lectura mezclada que la regla prohíbe, así
      que quien la necesite la pide a `PantryStockRepository`, que ya filtra por hogar.
      El scorer y los filtros **no** reciben el contexto en este punto: lo reciben en 4.5.4 y
      4.5.5, que son los que lo leen. Un parámetro que nadie lee es peor que no tenerlo — se
      ve implementado y no está probado por nada.
      Todo se lee **por repositorio**, y eso es la mitad del trabajo: hacen falta un lector de
      último estímulo por grupo muscular, un contador de alimentos recientes con `since` (hoy
      `MealRepository.get_recent_foods_for_user` trae las últimas 30 filas sin filtro de fecha
      y `meal_generator._get_recent_food_names` cuenta 7 días por `MealEvent.timestamp`: dos
      respuestas distintas a "qué comió last week" y nada que las obligue a coincidir), una
      agregación de macros, un listador de `ExerciseType` y un `app/repositories/blood_repo.py`
      que hoy no existe.
      Con eso se cierra el **trinquete** de `AGENTS.md` regla 2 sobre todo lo que el punto
      toca: las 14 consultas inline de `app/recommendations/` (engine 5, pantry 5, activity 2,
      meal 2) y las 3 de `blood_analysis_service.py:58,66,74` bajan a repositorios, y los
      números *medidos* de `AGENTS.md` se actualizan en el mismo commit — un número en un doc
      es una copia de una regla, y desactualizado miente con aire de precisión.
- [x] **Lo medido al cerrarlo, que no es lo que este punto predecía.** Las consultas inline
      bajaron: `engine` 5 → 0, `meal_generator` 2 → 0, `activity_generator` 2 → 0 y
      `blood_analysis_service` 3 → 0, así que `app/services/` quedó **limpio** y en
      `app/recommendations/` sobreviven 5, todas en `pantry_generator.py` — las de la 4.5.7.
      Pero el otro número de `AGENTS.md` fue para el otro lado: los módulos de
      `app/recommendations/` que importan `app.models` pasaron de 8 de 10 a **9 de 11**, porque
      `context.py` es un módulo nuevo que los importa para anotar sus campos. No es una
      violación —la regla dice explícitamente que ese import es esperado— y por eso el número
      medido va con el matiz al lado en vez de solo.
- [x] **Un agujero que apareció escribiendo el test de `get_latest_analyzed`, y era real.**
      Filtrar `values_json IS NOT NULL` no alcanza: `analyze_file` devuelve su `values` con
      `default_factory=dict`, así que un archivo que el parser recorrió sin encontrar
      marcadores se guarda como `analyzed` con `values_json = {}` — y `{}` pasa el `IS NOT
      NULL`. O sea que una subida ilegible de hoy tapaba el panel bueno del mes pasado, que es
      exactamente lo que ese método existe para evitar. El "no vacío" se evalúa en Python
      —ordenando en la base— porque "este JSON tiene claves" no se escribe igual en SQLite y en
      Postgres, y la regla 5 de `AGENTS.md` pide que las dos se comporten igual.
- [x] 24 tests nuevos en `tests/test_user_context.py`: cada consulta nueva por separado (el
      instante por grupo muscular con `"Chest"`/`" chest "` colapsando a una clave, el RPE
      `NULL` que no cuenta como cero, la ventana de comidas por `MealEvent.timestamp` y no por
      id de ítem —con la comida vieja insertada **última** para que su id sea el más alto—, la
      fila de sueño sin peso que sobrevive, el panel vacío que no tapa al bueno, el panel del
      otro integrante indistinguible de uno que no existe, las tres supresiones de sujeto, el
      stock en cero que no es stock) y el armado entero (cuenta vacía → ausencias y no ceros,
      cobertura de macros con un ítem en "unidades" que no se puede convertir, catálogo de
      ejercicios vacío). El que más importa es el del borde de día: una comida de las 22:00
      locales le sumaba los macros al día UTC siguiente, y el test lo fija derivando el instante
      del offset configurado en vez de asumir que la app corre en Buenos Aires.
- [x] **4.5.2 — Catálogo de ejercicios real y ventanas de recuperación que sean ventanas.**
      Fuera `_DEFAULT_ACTIVITIES` (las 8 hardcodeadas), dentro `ExerciseType` (20 filas que
      `seed.py:160-180` siembra y que `docker-compose.yml:44` corre al arrancar). Y
      `_MUSCLE_RECOVERY` deja de ser una lista de claves: hoy `rested_muscles = set(claves) −
      recientes` con un `_OVERTRAINING_DAYS = 2` plano para todos y `sorted(...)[0]`, que es
      por qué el músculo sugerido es **casi siempre "back"**. Pasa a comparar *días desde el
      estímulo de ese grupo* contra *la ventana de ese grupo*, eligiendo el que hace más tiempo
      que pasó su ventana, con desempate estable y no alfabético.
      **Lo que hay que resolver primero es que hay tres vocabularios de grupo muscular y no
      coinciden**: `_MUSCLE_RECOVERY` y `nlp/rules.py:_EXERCISE_MAP` usan los mismos ocho
      (`chest, shoulders, triceps, biceps, back, legs, core, cardio`), pero `seed.py` escribe
      `arms` y `full_body`, que no tienen ventana, y nunca escribe `triceps`, `biceps` ni
      `cardio`. Hoy no molesta porque **nadie lee `ExerciseType.muscle_group`** y lo que llega
      a la columna `WorkoutExercise.muscle_group` lo pone el NLP; el día que el catálogo entre
      en la lógica de recuperación, `arms` y `full_body` entran con él. Vocabulario único en
      `learning.py` (que ya es el módulo del vocabulario), el seed y el mapa del NLP
      conformando a él, y un default documentado para un grupo desconocido en vez de un
      `KeyError` o un silencio.
      Y hay un cambio de conducta que hay que nombrar: el catálogo es de **ejercicios**, la
      lista era de **actividades**. "Andá en bici" y "hacé Barbell Row" no son la misma clase
      de tarjeta; el eje de actividad es `ExerciseType.category` (`strength`/`cardio`/
      `flexibility`), no el nombre. En los tests el catálogo está **vacío** —ningún fixture lo
      siembra—, así que hace falta un camino honesto para catálogo vacío: no emitir tarjeta con
      nombre de actividad (las de descanso, constancia y balance muscular no necesitan nombre)
      y loguearlo una vez. Eso permite borrar las 8 sin reponerlas disfrazadas de fallback.
- [x] **El vocabulario único se resolvió como la *unión*, y por eso el seed no cambió una
      fila.** `learning.MUSCLE_GROUPS` son los ocho del NLP más `arms` y `full_body` menos
      `triceps`/`biceps`, o sea `chest, shoulders, arms, back, legs, core, cardio, full_body`, con
      `normalize_muscle_group()` y una tabla de alias al lado. Bajar el catálogo a los ocho del
      NLP habría obligado a partir `arms` en tríceps y bíceps —una distinción que ningún dato de
      esta casa sostiene—, mientras subir el NLP a la unión son **dos entradas** de
      `_EXERCISE_MAP` (`"triceps"` y `"biceps"` pasan a emitir `arms`) y cero filas de `seed.py`.
      La normalización tiene dos direcciones distintas a propósito: **leyendo** un estímulo un
      grupo desconocido se queda con su propio nombre (mapearlo a `"other"` fusionaría grupos
      distintos, y `"other"` ya significa `muscle_group IS NULL` en
      `get_user_muscle_groups_trained`), **proponiendo** una rotación solo salen los ocho.
      Las ventanas **no** viven en `learning.py`: el vocabulario es vocabulario y cuántos días
      tarda un grupo en recuperarse es una regla del generador.
- [x] **El default de un grupo desconocido son 2 días y está en un test, no en un `assert` de
      import.** Tres lugares tienen que coincidir —`_RECOVERY_DAYS`, `_ROTATION_PRIORITY` y
      `MUSCLE_GROUPS`— y lo fija `TestVocabularyAndWindowsAgree` (incluido el seed, parseado del
      archivo con `ast.literal_eval` porque el seed no corre en los tests). Un `assert` al
      importar el módulo se lleva la app entera puesta al arrancar; un test rojo no.
      `_ROTATION_PRIORITY` se declara y **no** se deriva del orden del dict: reusar la
      inserción convertiría en silencio "cuánto tarda en recuperarse" en "cuánto importa".
- [x] **El bug de determinismo estaba en dos lugares, no en uno.** El `sorted(rested)[0]` del
      músculo era el conocido. El otro es que `ExerciseTypeRepository.list_all()` ordena por
      nombre, así que elegir la primera fila del catálogo habría dado *siempre* "Barbell Row" y
      "Bench Press" —la misma tarjeta para siempre, el mismo bug con otra cara—. Las dos
      elecciones ordenan ahora por *cuánto hace que el grupo pasó su ventana*, con desempate por
      prioridad declarada, y el catálogo emite **una fila por `category`** (dos ejercicios de
      fuerza son la misma propuesta con distinto nombre).
- [x] **"Nunca entrenaste esto" y "hace ocho días" son dos tarjetas distintas.** El contexto
      distingue el caso —la clave no está— y el generador lo respeta: decirle "volvé" a alguien
      que nunca fue es cómo una app revela que no está mirando. Un grupo nunca entrenado gana
      sobre todo lo atrasado.
- [x] **`ExerciseType` no tiene `aliases_json`, y eso cambia por dónde entra la 4.5.8.** El plan
      daba por hecho que reemplazar la lista del NLP por el catálogo era lo correcto "porque ahí
      viven los nombres". No: el catálogo también está en inglés ("Bench Press", "Cycling")
      mientras las capturas de esta casa están en castellano ("press de banca", "bicicleta"), y a
      diferencia de `FoodItem` —que sí tiene `aliases_json`, y es por qué del lado de la comida
      el catálogo resuelve— `ExerciseType` no tiene dónde poner las dos formas. Sin esa columna
      el match por nombre no acierta casi nunca, y agregarla es una migración que v3 no tiene
      (la `0003` está gastada). Consecuencia concreta: **la 4.5.8 entra por el grupo muscular y
      no por el nombre del ejercicio**, que es justo lo que este punto unificó. Corregido en los
      tres lugares que afirmaban lo otro: `rules.find_known_activities`,
      `workout_service.log_workout` y `suggestion_service._mine_reason`.
- [x] **Los nombres del catálogo en inglés son un problema de datos, no de msgids.** "Bench
      Press" y "Barbell Row" ahora se muestran, así que la Fase 5 se los va a encontrar: no se
      arreglan con `_()` —son filas, no literales— sino traduciendo el seed o agregando la
      columna de alias. Queda anotado para las Fases 5 y 6 en vez de descubrirse extrayendo.
- [x] 26 tests nuevos en `tests/test_activity_generator.py`, en su propio módulo porque lo que
      cambió es una **conducta** y no un contador de candidatos: qué grupo se propone y por qué
      ese, el empate que rompe por prioridad y no alfabéticamente (armado sumando los mismos días
      a la ventana de cada grupo, porque el mismo `days_since` para todos **no** es empate), el
      alias que colapsa —un `triceps` capturado aparece como `arms` a 0 días—, y el catálogo
      vacío que no emite tarjeta con nombre y lo loguea. El fixture del catálogo es **opt-in a
      propósito**: uno `autouse` haría que ningún test volviera a medir el caso real de la base
      recién creada, que es el que fija `test_user_context.py::test_the_exercise_catalog_can_be_empty`.
      La suite pasa de 593 a **619**, y los 4 errores de mypy que `activity_generator` arrastraba
      (reuso de variable de loop) desaparecen con la reescritura: 46 → 42.
- [x] **4.5.3 — Macros: contar lo que se puede contar, y decir cuánto se contó.** Un
      `MacroTotals` que lleve `items_counted`/`items_total`, porque el total honesto no es el
      total: sumar necesita `food_item_id` **y** una cantidad convertible a gramos, y
      `MealItemConsumed` tiene los dos como nullable (las capturas de texto libre solo dejan
      `normalized_free_text_name`). Los macros de `FoodItem` son **por 100 g** y
      `serving_size_g` no lo escribe nadie, así que un ítem sin gramos no se puede convertir y
      hay que declararlo en vez de sumarlo como cero. Y como la app **no tiene objetivo de
      macros** (`goals_json` y `target_weight_kg` no se leen en ningún lado), toda afirmación
      es relativa a la línea de base de la propia persona — "hoy vas más liviano de proteína
      que tu promedio", nunca "te faltan 40 g".
- [x] **La mitad de datos ya estaba, y no la leía nadie: el punto era el lector.** La 4.5.1 había
      dejado `macros_today` y `macros_baseline` con su cobertura, y `grep` sobre
      `app/recommendations/` daba **cero** lectores. Un contexto que se calcula y nadie lee es
      una consulta de más por corrida, no una feature. Lo que faltaba —y es lo que se hizo— es la
      tarjeta: `meal_generator` sección 5, `_macro_gap_card`.
- [x] **La comparación que el plan proponía era, tal como estaba escrita, sistemáticamente
      falsa.** "Hoy vas más liviano de proteína que tu promedio" medía el día **a medio andar**
      contra un promedio de días **completos**. El job corre 7:40 y 18:40 (`scheduler._SCHEDULE`):
      a las 18:40 la cena de hoy no pasó, así que la diferencia no habla de lo que se comió, habla
      de qué hora es — y le dice "te falta proteína" todas las tardes a quien cena fuerte, que es
      exactamente el consejo que esta app no debería dar. `_macro_totals` pasa a contar de cada día
      anterior solo lo anotado **hasta esta hora del día**, así que los dos lados son "hasta acá".
      Dos consecuencias que se esperan en vez de descubrirse: a las 7:40 casi ningún día anterior
      tiene algo antes de esa hora, la base sale vacía y el lector se calla; y un día cuyos
      registros son todos posteriores al corte **no cuenta como día registrado**, que es correcto
      porque de ese día, hasta esta hora, no hay dato.
      Descartadas, para el registro: prorratear por fracción de día transcurrido (asume
      distribución uniforme de las comidas, que es falsa), comparar contra ayer (necesita otro
      campo y una muestra de uno), y llevar **dos** bases —día completo y hasta esta hora— que es
      YAGNI: había cero lectores, así que el campo era gratis de cambiar.
- [x] **`days_counted` es lo que separa un promedio de una anécdota.** Séptimo contador de
      `MacroTotals`, poblado en las dos puntas: `1` si el día sumó algo, `n` si el promedio dividió
      por `n`. Sin él, el único día que alguien anotó hace diez días es indistinguible de una
      costumbre de dos semanas, y las dos cosas habilitarían la misma tarjeta. Un día cuyos ítems
      no se convirtieron a gramos **no** cuenta: sería denominador sin numerador.
- [x] **Cuatro guardias, y cada uno tapa una forma distinta de mentir con un número.** La base
      tiene que ser una base (≥3 días registrados); las dos puntas tienen que estar medidas
      (cobertura ≥0.6, o la tarjeta mide la captura y no la comida — le avisaría "te falta
      proteína" justo a quien escribe "cené milanesas" en vez de pesar); la diferencia tiene que
      ser una diferencia (<70% de la base, porque un promedio de pocos días oscila solo); y algo
      de la despensa tiene que poder llenarlo, con piso por 100 g, o no hay tarjeta. Sin el
      último, "te falta proteína" con la despensa vacía es un empujón que no se puede accionar,
      que es lo que v3 viene a dejar de ser.
- [x] **La escala tiene un solo lado, como el RPE de la 4.5.2.** Solo proteína y fibra, solo hacia
      abajo, en un orden **declarado** (`_MACRO_TRACKED`) y no derivado del orden de un `dict`. Sin
      objetivo de macros, "hoy vas más pesado de grasa" no propone nada —no hay nada que agregar,
      solo algo que dejar de comer—, y eso es consejo dietario sin objetivo. Una tarjeta por
      corrida aunque los dos macros estén cortos: son la misma cena. Y el texto dice **los dos
      números medidos** en vez de afirmar un déficit; el sujeto es el alimento y no el macro,
      porque "proteína" no es algo que se acepte o se rechace y `learning.SUBJECT_TYPES` no lo
      conoce.
- [x] **Hallazgo con alcance recortado a propósito: las secciones 1, 2 y 4 pueden emitir tres
      tarjetas del mismo sujeto.** "Use your banana today", "Try Banana for variety" y "Use your
      last banana" son tres títulos distintos, así que el de-dup final —que es **por título**— las
      deja pasar las tres: se estorban en la lista y el feedback de una enseña sobre las otras. No
      se arregló acá, y no por comodidad: un de-dup genérico por sujeto con "gana la primera"
      silenciaría la tarjeta de stock bajo, que es la **más** informativa de las tres. Arreglarlo
      bien pide una **prioridad declarada entre las cinco secciones**, que hoy no existe y que es
      también lo que decide si `unique[:limit]` puede truncar la sección 5 cuando la despensa tiene
      muchos ítems bajos (la 4 no tiene techo). Lo que sí garantiza la 5 es que *ella* nunca se
      lleva un sujeto ya tomado: va última justamente para poder ver los cuatro conjuntos
      anteriores, y es la única con libertad de elegir su sujeto, así que es la que puede ceder.
- [x] **El orden del portador está declarado, y esa es la lección de la 4.5.2 aplicada de
      entrada.** Mayor aporte por 100 g primero, y a igual aporte el nombre alfabético: sin la
      segunda mitad la tarjeta cambiaría de alimento entre dos corridas idénticas según cómo
      ordene la base, que es el mismo bug que la 4.5.2 encontró en
      `ExerciseTypeRepository.list_all`.
- [x] 20 tests nuevos en `tests/test_meal_generator.py` (propio módulo, misma razón que
      `test_activity_generator.py`: lo que se mide es conducta, no un contador de candidatos) y 4
      más en `tests/test_user_context.py` para el corte por hora. Uno de los hallazgos salió de los
      propios tests: cinco casos "sale tarjeta" fallaban porque con **un solo** alimento en la
      despensa las secciones 1 y 2 se lo llevan y la única fuente posible es un sujeto ya tomado —
      pasaban por la cesión y no por el guardia que querían medir. De ahí el fixture `filler`, que
      declara la intención una vez: un alimento que ocupa las otras secciones y no aporta nada.
      La suite pasa de 619 a **643**; mypy queda en 42 errores, los mismos de antes.
- [x] **4.5.4 — `rationale` computado.** Dos mitades que el `engine` compone: la razón de
      *dato* que pone el generador desde el contexto, y la razón de *aprendizaje* que pone el
      scorer. Hoy el scorer calcula el delta de sus cuatro ejes y lo tira a `logger.debug`: pasa
      a devolver un `_score_parts` estructurado, que es el mismo cálculo dejando de descartarse.
      Ningún eje se nombra cuando su delta es 0 —enumerar los ceros es cómo una explicación
      vuelve a ser decorativa—, y cuando no hay ni dato ni aprendizaje la tarjeta **lo dice**
      en vez de caer en una frase de catálogo. Se van las 9 cadenas fijas de
      `activity_generator`/`meal_generator`.
      **Interacción con i18n que hay que dejar escrita:** `rationale` se persiste ya
      renderizado, así que queda congelado en el idioma en que se generó — un cambio de idioma
      no reescribe las sugerencias viejas. Es un tradeoff, no un bug, y la Fase 5 tiene que
      saberlo antes de contar msgids.
- [x] **La composición vive en un módulo propio, `app/recommendations/explain.py`, y no en el
      scorer ni en el motor.** El scorer puntúa y el motor orquesta; el vocabulario de cómo se
      dice cada eje es una tercera cosa. La razón concreta: `_make_user_suggestion` y
      `_make_household_suggestion` tenían **dos** `item.get("rationale", "")`, y con dos copias
      la explicación de una tarjeta de la casa y la de una persona pueden divergir sin que nada
      falle. Ahora las dos llaman `explain.rationale(item)`. Que una sugerencia de la casa
      llegue con **una sola** mitad no es un caso degradado: `generate_for_household` no
      puntúa, así que su explicación es la medición del generador y nada más.
- [x] **El orden en que se nombran los ejes es declarado (`AXIS_ORDER`), no el de cómputo.** La
      misma lección de la 4.5.2 y de `_MACRO_TRACKED`: un orden que sale de dónde quedó una
      línea se cambia sin querer al agregar otra, y la explicación de dos corridas idénticas
      dejaría de leerse igual. Y saciedad y diversidad no tienen forma positiva porque por
      construcción solo restan — si llegara una con delta positivo se saltea en vez de
      inventarle una frase.
- [x] **El problema real no era escribir frases, era que los tres campos se pisaban.**
      `evidence_summary` ya hacía parte del trabajo de `rationale`. La división quedó escrita en
      el docstring de `explain.py`: `text` es la propuesta dirigida a la persona,
      `evidence_summary` el rastro auditable con los números crudos, y `rationale` contesta
      **por qué esta tarjeta y no otra** — la regla de selección instanciada. Así que cada
      `rationale` nuevo nombra un número o una regla que los otros dos **no** dicen: el umbral
      de stock bajo que la persona configuró, `_REST_DAY_THRESHOLD`, la ventana de recuperación
      del grupo, la señal de preferencia con su fuerza, la ventana de recencia, cuál de los dos
      macros ganó el desempate declarado.
- [x] **Tres frases plausibles se descartaron por ser falsas, y eso es la mitad del trabajo.**
      "El grupo que más pasó su ventana" es cierto de la **primera** tarjeta de catálogo y no de
      la segunda —`_catalog_rows` ordena todo el pool y después toma una por `category`—, así que
      la frase dice "de las opciones de {category} abiertas hoy". "La mejor fuente de tu
      despensa" es falso cuando una sección anterior ya se llevó un portador mejor. Y
      `_recency_phrase` existe porque "it appears 1 times" es la clase de detalle que hace que un
      texto se lea como generado, y `ngettext` no sirve acá: no hay locale de request en un job.
- [x] **La red que impide que esto se deshaga de a una cadena por vez.** `tests/test_explain.py`
      (12 tests): las tres reglas, `AXIS_ORDER` contra el orden de cómputo, el eje desconocido, y
      uno **estructural** que recorre el AST de los dos generadores y falla ante un `rationale`
      literal, con una única excepción nombrada —la tarjeta de descanso, que se dispara con
      `days_since == 0` y no tiene número que interpolar—. `blood_generator` (22 cadenas) y
      `pantry_generator` (4) quedan fuera de la lista a propósito: son la 4.5.6 y la 4.5.7.
      Suite de 643 a **655**; mypy sigue en 42 errores, los mismos. De paso, `engine.py` quedó
      formateado con `black` —ya estaba sucio en HEAD y es un archivo que este cambio toca.
- [x] **4.5.5 — `_infer_category` y el atajo.** Una categoría desconocida deja de saltear los
      dos chequeos y pasa a mirar **los dos** conjuntos de bloqueos; el atajo de
      `_drop_blocked` se borra (ver corrección 2). El costo aceptado es más falsos positivos
      del match por substring de `_any_token_matches`, y se acepta en esa dirección a
      propósito: mostrar una tarjeta de menos es preferible a mostrarle carne a quien declaró
      que no come carne.
- [x] **La función cambió de pregunta, y por eso cambió de nombre.** `_infer_category` devolvía
      `str | None` y contestaba "qué es esto"; `_sides_to_check` devuelve una tupla de lados y
      contesta "contra qué se compara". Con la vieja firma el arreglo se escribía como un `None`
      que significaba "los dos", que es la clase de valor centinela que nadie recuerda al leer el
      llamador. Con la nueva, el caso de duda es `("food", "activity")` y el bucle de
      `_drop_blocked` no tiene ninguna rama especial: itera los lados que le dan. Las cuatro
      listas de palabras y categorías salieron a constantes de módulo (`_FOOD_CATEGORIES`,
      `_ACTIVITY_CATEGORIES`, `_FOOD_WORDS`, `_ACTIVITY_WORDS`) porque son vocabulario, no lógica.
- [x] **El alcance real del bypass son tres tarjetas, y son las que peor conviene que lo tengan.**
      No es "cualquier categoría desconocida" en abstracto: las únicas que hoy llegan con una
      categoría que ningún lado reconoce son las tres de sangre con `category="habit"` —TSH alta,
      TSH baja, creatinina alta—, que son justo las que llevan consejo de salud y las que la
      4.5.6 va a reencuadrar. Medir eso antes de arreglarlo evitó escribir el hallazgo como un
      agujero genérico: `apply_signal_constraints` nunca tuvo el problema, porque compara por
      sujeto y no por categoría.
- [x] **Que un lado conocido siga mirando solo lo suyo no es una inconsistencia.** Ampliar el
      chequeo a los dos lados para *todo* candidato haría que el match por substring borre por
      accidente: Diego no puede nadar y "bread swimming in olive oil" no es una propuesta de
      natación. La ampliación es para lo que no se pudo clasificar. Hay un test por cada mitad de
      esa frase: el `habit` que nombra un alimento evitado desaparece, y el `meal` que nombra una
      actividad imposible sobrevive.
- [x] **El atajo se borró por ser una trampa, no un agujero** (corrección 2). Con los dos
      conjuntos vacíos las comparaciones no pueden descartar nada, así que borrarlo no cambia
      ninguna conducta de hoy — y esa es la razón: el día que este filtro tenga que mirar algo que
      no sean esos dos conjuntos, el atajo lo saltea en silencio y con la suite en verde. Lo que
      el atajo garantizaba ahora está medido en un test
      (`test_with_no_blocks_at_all_every_candidate_survives`) en vez de cortocircuitado. De paso,
      el log de descarte pasa a decir **cuál** de los dos lados bloqueó: en un filtro que borra
      sin dejar rastro en la UI, es la única forma de auditar por qué desapareció una tarjeta.
      Cuatro tests nuevos en `TestHardConstraints`, suite de 655 a **659**; mypy sigue en 42.
- [x] **4.5.6 — Sangre: antigüedad del panel y encuadre no diagnóstico.** El contexto lleva
      fecha y antigüedad —hoy `BloodAnalysisService.get_latest_values` ordena por
      `analysis_date` y devuelve **solo** `values_json`, que es exactamente por qué la
      antigüedad nunca se chequea—. Umbral de frescura (~180 días) y techo de obsolescencia
      (~365) pasado el cual **no se emite consejo** y en su lugar sale una sola tarjeta de
      "repetí el panel" con sujeto propio. Las 22 cadenas fijas pasan a llevar la fecha del
      panel y un encuadre de observación-más-sugerencia-de-alimentos en vez de imperativo
      médico; las derivaciones de TSH y creatinina **se quedan** (decirle a alguien que
      consulte es lo correcto) pero atraviesan los filtros como todas. **Revisión de
      `security-privacy` obligatoria**, por dato de salud y por encuadre.
- [x] **No son tres bandas sino cuatro, y la cuarta es "sin fecha".** El plan hablaba de
      frescura y techo; el panel sin fecha no es ninguno de los dos y es un caso real, porque
      `blood_analysis_parser._extract_date` no siempre encuentra la fecha. Se decidió que
      **una antigüedad desconocida pesa como vieja y no como nueva** —el costo de aconsejar
      sobre un número que podría ser de hace años es peor que una tarjeta de menos, y el
      docstring de `BloodPanel` ya lo decía—, pero con mensaje propio: un panel de dos años se
      repite, uno cuya fecha no se pudo leer **se vuelve a subir**. Compartir la banda haría
      que la tarjeta le pidiera un análisis nuevo a quien acaba de subir uno. Y hay un límite
      que la frescura no puede prometer: `_extract_date` toma la **primera** fecha del
      documento, así que puede ser una fecha de nacimiento o de impresión. Anclarla a la
      etiqueta es un cambio del parser, queda anotado abajo.
- [x] **Un panel obsoleto con todo en rango no produce nada, ni el aviso de repetirlo.** La
      razón para hacerse uno nuevo es que había algo que se dejó sin leer; sin eso, el aviso
      sería una nota al pie con forma de alarma. Y la tarjeta dice **cuántos** marcadores
      quedaron sin leer, no cuáles: nombrarlos sería dar exactamente el consejo que esa rama
      existe para no dar.
- [x] **La tarjeta de "repetí el panel" nombra la pantalla en el texto en vez de llevar botón.**
      `web.actions.suggestion_action` devuelve `None` para **todo** `source_type ==
      "blood_analysis"`, y esa regla existe porque ponerle "Anotar una comida" a un consejo de
      *no* comer algo fue un bug real. Darle acción a esta tarjeta necesitaría un
      discriminador que no sea `source_type` (un `category="reminder"`, por ejemplo) y
      reordenar `suggestion_action`; no vale abrir esa puerta por una tarjeta. Queda anotado.
- [x] **La regla de la 4.5.4 un nivel más abajo: `_Advice` tiene tres campos y no un `text`.**
      Reescribir a mano las 22 cadenas habría dejado 22 lugares donde el encuadre puede
      divergir. Cada entrada declara ahora solo lo que **sabe únicamente ella** —`action` (el
      alimento o el movimiento), `mechanism` (por qué ese alimento se relaciona con ese
      marcador: una afirmación sobre la comida, nunca sobre el cuerpo de quien lee) y un título
      que nombra la ruta y no la orden: "Oats at breakfast", no "Limit saturated fats"—, y la
      observación, la fecha, el reparo por antigüedad y la confianza los compone `_compose` una
      vez. `_Reading` existe por la misma razón del otro lado: las lecturas del blob del parser
      (`.get` con default) quedaron en un solo lugar y `_compose` toma cuatro argumentos en vez
      de siete. Los alimentos y los movimientos son los mismos de antes; lo que se fue son los
      imperativos y las condiciones nombradas.
- [x] **Hallazgo de la revisión de `security-privacy`: el aviso de no-diagnóstico quedaba tres
      veces en la misma pantalla.** La primera versión lo cerraba en el `text` de cada tarjeta,
      y `suggestions/partials/list.html` y `home.html` **ya lo renderizan** con `ui.notice`,
      traducido y condicionado a que haya una tarjeta de sangre en la lista. O sea la misma
      regla escrita dos veces, y la copia de adentro de la tarjeta es la que no se puede
      traducir ni corregir sin migrar datos, porque `text` y `rationale` se persisten
      renderizados. Se quitó la copia del generador: la tarjeta observa y propone, la pantalla
      dice una vez quién puede leer el número. El costo es que el encuadre pasa a depender de
      dos plantillas, así que hay un test que las lee y falla si dejan de llevarlo
      (`test_the_screens_that_show_these_cards_carry_the_disclaimer`), más otro que recorre las
      22 entradas y falla ante vocabulario diagnóstico ("anemia", "deficiency", "may
      indicate"…), que es la forma en que el encuadre viejo volvería sin que nada lo note.
      El resto de la revisión: los dos `logger` registran `user_id`, banda y **cantidad** de
      marcadores, nunca un valor ni un nombre de marcador ni la fecha; las tarjetas son de
      scope personal (`_visible_to` las filtra por `scope_user_id`), así que el valor de
      laboratorio que ahora aparece en `rationale` no cruza a la otra persona de la casa —y ya
      aparecía en `evidence_summary`, que la pantalla renderiza desde la 4.5.4—. Verdicto:
      `APPROVE`.
- [x] **Fechas en ISO y no en `%d %b %Y`, y por qué estas cadenas no pasan por `_()`.** El
      nombre del mes depende del locale del proceso, y esto corre en un job de fondo sin locale
      de request — la misma razón por la que `rationale` se persiste ya renderizado (4.5.4).
      Un `2026-01-17` es legible en cualquier locale; un `17 Jan 2026` es una decisión de idioma
      tomada por accidente.
- [x] **La antigüedad no cambia solo el texto: cambia el score.** Un panel viejo advierte con
      el reparo y con la confianza multiplicada por `_STALE_CONFIDENCE_FACTOR`, que es la
      respuesta graduada entre tratarlo como si fuera de ayer y callarse. Los tres umbrales
      están declarados como constantes de módulo para que moverlos sea una decisión y no un
      literal escondido en un `if`.
- [x] **`blood_generator.py` entra a `TestNoFixedRationalesLeft`** — sus 22 razones ahora citan
      el valor medido y la fecha del panel, así que ya no hay motivo para la exclusión que la
      4.5.4 dejó anotada. Queda `pantry_generator` (4 cadenas), que es la 4.5.7. Ocho tests
      nuevos en `TestBloodPanelBands` (uno por banda, el silencio del panel obsoleto sin nada
      fuera de rango, las dos derivaciones atravesando `apply_hard_constraints`, y las dos
      mitades del encuadre), suite de 659 a **667**; mypy sigue en 42. Dos tests existentes
      cambiaron de panel: el de `TestEveryCandidateDeclaresItsSubject` y el de `test_actions.py`
      pasaban un panel **sin fecha** y esperaban consejo, que es justo lo que dejó de pasar —
      ahora usan uno fresco y con fecha, y un helper `_panel(values, age_days)` mantiene la
      fecha y la antigüedad contando la misma historia. De paso, `blood_generator.py` quedó sin
      los 26 `E501` (22 ya estaban en HEAD) y el comentario de `test_actions.py` que decía "19
      filas de `meal` y 5 de `activity`" pasa a decir las cantidades reales: 15 y 4.
- [x] **4.5.7 — Conectar `generate_for_household`.** Existe, filtra bien la asimetría
      unión/intersección de 4.4.9 y tiene **cero llamadores**, así que todo el generador de
      pantry y compras nunca llegó a nadie. Un loop de hogares en `suggestion_jobs` sobre
      `HouseholdRepository.list_all()`, y la línea del `README` que dice que no hay llamador en
      producción deja de ser cierta el mismo día.
- [x] **El loop de hogares va al lado del de personas, no adentro.** Anidado, una casa de dos
      escribe la misma lista de compras dos veces por corrida, y el dedup por sujeto no la
      salva: mira las pendientes que dejó una corrida **anterior**, así que las dos de la misma
      corrida pasan las dos. Cada casa con su propio `try` —una despensa ilegible no cancela la
      siguiente—, y cuatro tests que llaman al **job** y no al método: los tres que ya había
      llamaban a `generate_for_household` ellos mismos, que es exactamente por qué la suite
      quedaba en verde con cero llamadores.
- [x] **Un bug encontrado de paso: al `except` del loop de personas le faltaba el
      `db.rollback()`.** Lo que una corrida fallida dejaba pendiente en la sesión lo commiteaba
      la entidad siguiente — contaminación cruzada entre usuarios. `_sweep_user` ya tenía ese
      reparto; el job de generación no. Se arregló antes de agregar el loop de hogares, para no
      copiar la falta a la mitad nueva.
- [x] **`pantry_generator` entra a `TestNoFixedRationalesLeft`, y con eso están los cuatro
      generadores.** Sus cuatro razones eran idénticas para todas las tarjetas de su regla; hoy
      citan cuántos ítems de la despensa están en cero sobre el total, cuál es el que **menos
      margen** tiene con su cantidad y su umbral, cuántas veces se compró el que más se repite y
      cuántas veces dos cosas se compraron el mismo día. Mientras faltaba uno, el test decía
      "casi ninguna razón es fija". Cinco tests nuevos en `TestPantryCardsCiteWhatTheyMeasured`,
      que es lo que el test de AST no puede ver: que el número interpolado sea **el** número.
- [x] **"El mismo día" y no "juntos".** La tarjeta de co-compra agrupaba por
      `strftime("%Y-%m-%d")` y decía que las dos cosas se compraban juntas. Un día no es un
      ticket, y la diferencia importa justo cuando la tarjeta se equivoca: dos compras
      independientes del mismo martes no son un patrón. Ahora el texto dice lo que el agrupado
      mide.
- [x] **Cinco `db.query(...)` dentro del generador pasaron a dos llamadas a repositorio.** Tres
      eran para resolver **un** nombre de alimento por vez; desaparecieron sin agregar un
      `FoodRepository.get_many` porque el nombre ya viene en el `joinedload` de
      `PantryMovement.food_item`. La consulta de compras es nueva
      (`PantryMovementRepository.get_purchases_since`) y deliberadamente **sin `LIMIT`**:
      `get_household_movements` pagina en 50 y ordena de lo nuevo a lo viejo, y con eso "cuántas
      veces se compró café" se convierte en "cuántas de las últimas cincuenta fueron café".
      Acotada por fecha y con `ORDER BY` explícito, porque el agrupado por día la recorre.
- [x] **Dos duplicaciones más, borradas en el mismo paso.** La comparación de stock bajo estaba
      escrita a mano acá y también en `PantryStock.is_low`; queda la del modelo, que es donde la
      pantalla de despensa ya la lee — dos copias es cómo la grilla y la tarjeta empiezan a
      contar cosas distintas. Y el `limit=5` estaba dos veces como literal en el job: pasa a
      `_SUGGESTIONS_PER_RUN`, uno solo, porque Home mezcla las personales con las del hogar y un
      número más alto en un lado se ve como una lista que se llenó de compras.
- [x] **Suite de 667 a 676; mypy de 42 errores en 7 archivos a 41 en 6** —
      `pantry_generator.py` salió de la lista al reescribirse. También quedó sin sus 7 errores de
      `ruff` (cuatro imports muertos, un `UP017`, dos `E501`), y contra eso el punto suma **uno**:
      el `timezone.utc` del helper de compras nuevo, que es la forma que usan los otros siete de
      ese archivo. `black` baja de 40 archivos a **39**: `pantry_repo.py` tenía una línea sucia
      previa a este cambio y se formateó porque el punto ya estaba editando ese archivo.
- [x] **Revisión de privacidad: la despensa es del hogar, y por eso esta consulta no filtra por
      persona.** Es la excepción explícita a la regla 4, no un olvido: `PantryStock` y
      `PantryMovement` llevan `household_id`, la pantalla de movimientos ya los muestra a las dos
      personas, y la tarjeta sale con `scope_user_id = NULL` justamente para que sea **una** lista
      de compras. Lo que sigue siendo por persona es el filtro: `apply_household_constraints` lee
      las preferencias y las señales de cada miembro por separado (4.4.9). Ninguno de los tres
      `logger` nuevos escribe un nombre de alimento: el `debug` del generador registra
      `household_id` y cantidades, y los dos del job registran cantidades y el id del hogar.
- [ ] **Anotado, no arreglado: los `logger.exception` de los jobs pueden volcar parámetros
      ligados.** Un `IntegrityError` de SQLAlchemy trae el `INSERT` con sus valores en el
      `__str__`, así que un traceback de la corrida puede terminar con nombres de alimentos —o,
      en el loop de personas, con un dato de salud— en el log. El loop de hogares hereda la
      forma que los otros tres ya tenían; unificarlos en un helper que registre tipo y entidad y
      no el mensaje del driver es un cambio de `app/jobs/` entero, no de este punto.
- [x] **4.5.8 — Los dos arrastres de la 4.4.** Los ejercicios entran a
      `learning.attribute_index` con el **grupo muscular** como atributo (va después de 4.5.2
      porque necesita el vocabulario unificado). `ExerciseType.category` queda afuera y no es un
      olvido: llegar a la categoría pide resolver el nombre capturado contra el catálogo, y
      4.5.2 midió que eso no se puede sin `aliases_json` — el grupo, en cambio, las dos puntas ya
      lo escriben normalizado. Y se unifica el
      `freq_penalty` de `meal_generator` con el eje de saciedad del scorer: hoy un
      alimento de todos los días se penaliza **dos veces** con dos números que no se conocen.
      **Cerrado.** Lo que salió distinto de como estaba escrito, en el orden en que apareció:
    - **El nivel atributo dejó de tener un solo vocabulario, y eso se vio en la pantalla antes
      que en el motor.** `learning.ATTRIBUTE_TYPES` (`food_category`, `muscle_group`) ahora se
      **declara** y `ATTRIBUTE_SUBJECT_TYPES` se **deriva** por resta: hasta acá un solo conjunto
      contestaba dos preguntas distintas —"¿qué atributos hay?" y "¿cuáles no se pueden
      grabar?"— y con `muscle_group`, que sí se graba, dejaron de coincidir. El rótulo del panel
      pasó a despachar por tipo (`dm.learned_attribute_label`) más un rótulo de **clase**
      (`learned_attribute_kind_label`), porque un grupo muscular puede aparecer **dos veces** en
      `/profile/`: arriba como sujeto con botón de olvido, por lo que se entrenó, y abajo como
      conclusión sin botón, por lo que se opinó de los ejercicios de ese grupo.
    - **Un rótulo traducido y mal**, encontrado por el test que ata `MUSCLE_GROUPS` al mapa:
      `_('Back')` ya estaba en el catálogo como el "Volver" de los dos botones de la app, así que
      el grupo `back` salía rotulado "Volver". Los ocho pasaron a `pgettext('muscle group', …)`.
      Para la **fase 5** eso significa dos cosas: que la extracción tiene que llevar el keyword
      `pgettext:1c,2` (está en los defaults de Babel, pero hay que confirmarlo contra el mapping
      file) y que los ocho msgids con contexto están sin traducir, igual que `muscle group` y
      `food group`.
    - **La unificación del `freq_penalty` es la eliminación de un eje duplicado, no un ajuste de
      números.** El generador ya no se descuenta la confianza por recencia
      (`max(0.5, 0.85 - 0.05 * freq_penalty)`): era el mismo eje de saciedad del scorer escrito
      dos veces, con dos ventanas distintas y —del lado del generador— sin decaimiento, un
      escalón plano de siete días que no se apagaba nunca. Un alimento de todos los días pasó de
      perder hasta 0.50 (0.35 acá + 0.15 allá) a perder 0.15, y decayendo. Los otros dos lectores
      de `recent_foods` **quedan**, y el docstring de `generate()` dice por qué: la sección 2 lo
      usa para **elegir** un alimento del que la tarjeta pueda afirmar "hace tiempo que no comés
      esto" (sin eso la tarjeta sería falsa, no floja) y la 3 para **callarse** cuando un favorito
      ya se come todos los días. La línea es: el generador decide *si hay algo que decir*, el
      scorer *cuánto compite*.
    - **Las cuatro confianzas de sección son ahora una escalera declarada**
      (`_PANTRY_CONFIDENCE` 0.85 > `_LOW_STOCK_CONFIDENCE` 0.8 > `_VARIETY_CONFIDENCE` 0.7 >
      `_MACRO_CONFIDENCE` 0.6), ordenada por qué tan directo es el hecho que la tarjeta afirma —
      cantidad medida, umbral puesto por una persona, **ausencia**, **comparación**—. Estaban
      sueltas dentro de cada `dict` y el comentario de `_MACRO_CONFIDENCE` repetía dos de memoria.
      Un test fija que las cuatro sean distintas y el orden, porque `_macro_cards()` de los tests
      identifica la tarjeta de macros **por su valor de confianza**: con el descuento viejo, una
      despensa de cinco alimentos muy repetidos daba exactamente 0.6 y el helper se la confundía.
    - **Un bug de la 4.5.6 que salió al mover eso:** el `rationale` de la sección 1 decía
      "y {alimento} aparece N veces" con N siendo la **suma sobre los cinco** destacados, así que
      podía afirmar "12" de algo que la persona no comió nunca. Ahora cuenta el sujeto que nombra.
- [ ] **Extensión anotada (no un olvido de 4.5.8): las señales de `("muscle_group", g)` no caen
      en su propio balde de atributo.** Una captura de entrenamiento escribe el grupo con la
      clave ya normalizada, y un grupo no es un ejercicio, así que no es clave de
      `attribute_index` y su señal pesa solo en el nivel **puntual**. Hacerla entrar pide una
      entrada identidad —`("muscle_group", g) → ("muscle_group", g)`— que es aritméticamente sana
      (`generalized_affinity` le resta al balde lo que el sujeto puso, así que no se contaría dos
      veces) pero se lee como un error en el índice y le pone al scorer un rótulo de atributo
      igual al sujeto que está explicando. Lo que ganaría: hoy "entrené pecho tres veces" no
      empuja "Incline Press", y "rechacé Bench Press" sí enseña sobre `chest`, o sea que el balde
      se llena de un solo lado. Cuesta una línea en `attribute_index` y una decisión sobre cómo
      se nombra eso en el panel, donde la misma fila ya aparece dos veces.

**Archivos:** `app/jobs/{scheduler,notification_jobs,suggestion_jobs}.py`,
`app/repositories/{notification_repo,user_repo,workout_repo,meal_repo,body_metric_repo,pantry_repo}.py`
+ nuevos `app/repositories/{household_repo,blood_repo}.py`, `app/schemas/notification.py`,
`app/services/{suggestion_service,meal_service,workout_service,pantry_service,blood_analysis_service}.py`,
`app/recommendations/{engine,scorer,filters,learning}.py` + `generators/*.py`,
`app/models/suggestion.py`, `app/nlp/rules.py`, `seed.py`, `app/web/profile.py` + template del
panel de 4.4.8, nuevos `app/core/clock.py` y `app/recommendations/context.py`, nueva revisión
`alembic/versions/0003_*.py`, `AGENTS.md` (los números medidos del trinquete) y `README.md`.

---

### Fase 5 — i18n, accesibilidad y red de seguridad

**5.1 — i18n**

- [ ] Traducir `health/detail.html` (hoy con **cero** `_()`) y
      `suggestions/partials/dismissed.html`.
- [ ] Mapear los valores de enum a etiquetas traducibles en vez de `|title`.
- [ ] Plurales con `ngettext`.
- [ ] Sacar el glifo de `_('✓ Confirm & Save')`.
- [ ] **Los `msgid` que las fases 3 y 4 agregaron y que no tienen entrada en el `.po`.** No
      son plantillas sin `_()` —esas son las dos de arriba—: son llamadas correctas cuyo
      texto castellano nunca se escribió, así que `gettext` devuelve el inglés y no falla
      nada, que es justamente por lo que se pasan de largo. La 4.4.7 dejó cinco
      (`Prefer to say why?`, `What put you off?`, `e.g. we do not like broccoli`, el `hint`
      del campo, y el `aria-label` `Not for us, with this reason`). Se buscan comparando los
      `_()` de `app/templates/` contra el catálogo, no de memoria. **Medido con la 4.4.8 ya
      aplicada: 517 `msgid` extraídos del repo (plantillas *y* Python), 126 sin entrada en
      `es_AR`.** (La cifra que este punto decía antes —165 de 515— se había tomado antes de
      que entraran las 41 traducciones de la 4.4.8: hay que re-medir al empezar el punto, no
      confiar en el número escrito.) La extracción necesita un archivo de mapeo con los
      patrones **relativos al directorio de entrada**, o `pybabel` devuelve un solo `msgid` y
      parece que no hay nada que traducir:

      ```ini
      [python: **.py]
      [jinja2: templates/**.html]
      extensions=jinja2.ext.i18n
      silent=false
      ```

      ```bash
      .venv/bin/pybabel extract -F <cfg> -o /tmp/gp.pot --no-location --sort-output app
      ```
- [ ] **Los `msgid` con contexto que agregó la 4.5.8, y la razón por la que existen.** Los ocho
      grupos musculares de `dm.muscle_group_label` van con `pgettext('muscle group', …)` porque
      `_('Back')` ya estaba en el catálogo como el "Volver" de los dos botones de la app, así que
      el grupo `back` salía rotulado "Volver" — traducido, sin fallar, y mal. Dos consecuencias
      para este punto: la extracción tiene que llevar el keyword `pgettext:1c,2` (está en los
      defaults de Babel; **confirmarlo contra el mapping file** en vez de asumirlo, porque un
      keyword que no matchea no falla: los ocho simplemente no aparecen en el `.pot`), y las
      entradas llevan `msgctxt`, así que un `.po` editado a mano tiene que escribirlo. Y
      "Volver" es una colisión medida, no hipotética: al revisar el resto de los rótulos cortos
      —`Core`, `Arms`, `Cardio`, `Back`, y los valores de enum que este mismo punto va a mapear—
      la pregunta a hacerse es si la palabra ya significa otra cosa en otra pantalla.
- [ ] Recompilar el catálogo `es_AR` (`_ensure_mo_compiled` ya recompila `.po`→`.mo` al
      arrancar).

**5.2 — Accesibilidad**

- [ ] `aria-label` en todos los botones icon-only.
- [ ] Labels en los inputs que no los tienen.
- [ ] `role="tab"`/`aria-selected` en los grupos de tabs.
- [ ] `role="status"` en los fragmentos que HTMX intercambia.
- [ ] Confirmación en el borrado destructivo de `health/detail.html`.

**5.3 — Los primeros tests de la capa web**

Usando el fixture `client` de `tests/conftest.py:51` que hoy nunca se usa:

- [ ] Smoke de las 29 páginas contra 200.
- [ ] El gate de onboarding.
- [ ] **Que cada `hx-post`/`hx-get` de las plantillas apunte a una ruta que existe** — el
      test que hubiera atrapado los 4 defectos de ruteo de la sección A.
- [ ] Horario de silencio.
- [ ] Dedup por sujeto.
- [ ] Que un sujeto rechazado quede suprimido **sin arrastrar candidatos no relacionados**.
- [ ] El bucle de aprendizaje de 4.4: que una comida registrada emita señal implícita, que
      una señal vieja pese menos que una reciente (decaimiento), que un solo descarte **no**
      vete un sujeto pero seis sí, que el nivel de atributo exija más evidencia que el
      puntual, y que la señal de un miembro **no** afecte las sugerencias del otro.

---

### Fase 6 — Los dos documentos de cierre

Pedido explícito del usuario, a entregar **cuando la v3 esté terminada**. Son dos
documentos con dos lectores distintos, y por eso son dos y no uno: mezclar "qué cambió"
con "cómo se usa" produce un changelog que nadie puede seguir y un tutorial que envejece
en cada commit.

Se planifican acá, en vez de improvisarse al final, por una razón práctica: buena parte
del material se pierde si se junta después. La explicación de por qué el nivel atributo
pesa la mitad, o de por qué `"other"` no es una franja horaria, está fresca en el commit
que la introdujo y hay que ir juntándola a medida que cada fase cierra — el registro por
sección de este mismo plan es la materia prima.

**6.1 — `docs/gaiapulse-v3.md`: qué hay, qué se hizo, qué queda de la v2**

Para alguien que quiere entender el estado de la app, no usarla. Estructura pensada:

- [ ] Qué es GaiaPulse en un párrafo y para quién (dos personas, un hogar, un objetivo).
- [ ] **El recorrido de un dato**, que es lo que hace entendible todo lo demás: una frase
      escrita en Capture → NLP capa 1/capa 2 → la pantalla de confirmación → las tablas de
      dominio → las señales de aprendizaje → una sugerencia. Un diagrama y cinco párrafos.
- [ ] Por área, tres columnas honestas: **lo que ya estaba en la v2**, **lo que la v3
      cambió**, y **lo que sigue igual a propósito**. Áreas: funcionamiento (los 13
      defectos y el onboarding huérfano), diseño (tokens, modo oscuro, macros), la capa de
      inteligencia (el reloj, la memoria por sujeto, el aprendizaje de 4.4 **y el panel que
      lo hace visible y reversible**), i18n y accesibilidad, y la red de tests.
- [ ] Dentro de esa área, la vuelta de tuerca que conviene contar aparte porque es la que
      cambia la relación con la app y no solo su comportamiento: en la v2 el aprendizaje era
      inauditable —`behavior_signals` no tenía ninguna lectura de usuario— y en la v3 se ve,
      se cuestiona con las cifras que lo respaldan y se borra. Y el rótulo falso que había
      que corregir para que eso fuera legible: dos pantallas llamaban "aprendido" a lo que la
      persona había **declarado**.
- [ ] **Lo que quedó afuera y por qué**, con nombre y razón: la validación de CSRF, el LLM
      en el camino de recomendación, la edición inline del NLP, el build de Tailwind, la
      normalización de los marcadores de sangre. Un documento que solo cuenta lo que se hizo
      es propaganda; lo valioso para el que llega después es el mapa de lo que falta.
- [ ] Los números verificados al momento de escribirlo (tablas, migraciones, tests,
      pantallas), sacados del árbol y no de la memoria — es exactamente el error que hoy
      tiene `README.md:81`.

**6.2 — `docs/guia-de-uso.md`: cómo usarla y sacarle provecho**

Didáctico, para Diego y Rocío, no para un desarrollador. Nada de nombres de módulo.

- [ ] **La primera semana**, en orden: el onboarding, la primera captura, el primer
      registro de peso, cargar la despensa. Qué esperar de la app cuando todavía no sabe
      nada de vos — y por qué las primeras sugerencias son genéricas.
- [ ] **Cómo hablarle**: qué frases entiende bien, con ejemplos reales en castellano de los
      dos idiomas que el parser acepta, y qué conviene escribir para que una comida quede
      con su hora (que es lo que le enseña *cuándo* te gusta algo).
- [ ] **Cómo aprende y cómo enseñarle**: qué pasa cuando aceptás, cuando descartás y cuando
      posponés una sugerencia —y que desde la 4.4.7 "Ahora no" calla el tema por tres días en
      vez de contar como un "no"—; por qué un solo toque es una pista y seis son una regla; por
      qué un "no" caduca; y que rechazar tres verduras le enseña algo sobre la cuarta. Es la
      sección que convierte la 4.4 en algo que se puede usar a propósito en vez de sufrir.
- [ ] **Decirle por qué**, que es la forma más rápida de enseñarle y la menos evidente: el
      "¿Preferís decir por qué?" de la tarjeta (4.4.7). Que conviene **nombrar la comida o la
      actividad** —"no nos gusta el brócoli", no "no nos convence"—, porque la app busca en esa
      frase los nombres que conoce y aprende sobre ellos en lugar de sobre cómo estaba redactada
      la tarjeta; y que si el nombre no le suena, la frase no le enseña nada y no pasa nada malo.
      Dos cosas más que conviene decir en la guía porque se notan al usarla: que **una frase corta
      enseña mejor que una lista** —de un motivo se aprenden hasta cinco cosas, y un signo por
      frase, así que "no nos gusta el brócoli, preferimos el pollo" baja los dos—, y que de las
      actividades entiende las que se dicen igual en inglés y en castellano (yoga, pilates,
      spinning, crossfit, cardio, running, hiit, zumba) pero **no** las que solo se dicen en
      castellano ("correr", "caminar", "pesas"), hasta que la 4.5 las saque de la base.
- [ ] **Cómo leer una sugerencia**: el "¿por qué esta sugerencia?", qué significa el
      porcentaje de confianza, y qué **no** significa (no es una recomendación médica).
- [ ] **La lista de compras es de los dos** (4.5.7): entre las sugerencias personales aparecen
      algunas del hogar —qué se acabó, qué está por acabarse, qué se compra siempre y hoy no
      está— y esas las ven las dos personas, son la misma tarjeta y no una copia para cada uno.
      Dos cosas que conviene saber para no pelearse con ellas: **cargar la despensa es lo que
      las enciende** (sin stock cargado no hay nada que contar, y sin umbral de "poco" solo se
      avisa cuando algo llega a cero), y una restricción alimentaria de **una** de las dos saca
      ese alimento de la lista de la casa, mientras que un "no" a una sugerencia no —para eso
      tienen que decir las dos que no—.
- [ ] **Cuando se equivoca**: qué hacer si insiste con algo que no querés, cómo corregir una
      captura mal interpretada, y qué mira la app para dejar de repetirse.
- [ ] **Ver lo que aprendió, y desdecirlo** (4.4.8): que el perfil muestra lo que la app
      dedujo sola, con cuántas veces lo vio y cuándo fue la última, y que se puede olvidar
      cualquier cosa de esa lista en un toque. Y las dos cosas que hay que entender para que
      el gesto sirva: que **olvidar no es prohibir** —si la conducta se repite se vuelve a
      aprender, así que lo que no se quiere nunca más va en las restricciones alimentarias o
      en las actividades imposibles— y que la lista es **de cada uno**: olvidar lo tuyo no
      toca lo del otro, aunque compartan la casa y la despensa. Más la distinción que la
      pantalla ahora nombra: *"lo que nos dijiste"* se corrige diciendo otra cosa, *"lo que
      GaiaPulse fue aprendiendo"* se corrige olvidándolo.
- [ ] Una página final de "trucos": las acciones rápidas del Home, el modo oscuro, la
      despensa como fuente de las sugerencias de comida.

---

## Estrategia de commits

Branch: **`v3`**. Commits **parciales y atómicos por funcionalidad** — no un commit gigante
por fase. Un commit por unidad de trabajo que quede en verde por sí sola.

Convención de mensajes:

```
<fase>: <qué cambió, en imperativo>

<por qué, si no es obvio>

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

Ejemplos del corte esperado en la Fase 1:

| Commit | Alcance |
|---|---|
| `docs: plan de trabajo de v3` | este documento |
| `fase1: apuntar las acciones HTMX a rutas web existentes` | los 4 targets mal apuntados + las 2 rutas web nuevas |
| `fase1: eliminar appStore() y el flash de Flask de base.html` | dead code del layout + reglas CSS faltantes |
| `fase1: agregar el wizard de onboarding y registrar su router` | template + router + gate |
| `fase1: arreglar el prefill de capture y el include de history` | 1.4 |
| `fase1: arreglar la paleta, el email_value y las promesas muertas del login` | 1.5 |
| `fase1: calcular la ventana de comida en hora local` | bug de inteligencia |
| `fase1: no suprimir la notificación del otro miembro del hogar` | bug de inteligencia |
| `fase1: arreglar el mapeo food_name de la capa 2 del NLP` | bug de inteligencia |

Corte real de la Fase 2 (ya en `v3`):

| Commit | Alcance |
|---|---|
| `fase2: capa de tokens semanticos, modo oscuro y JS global` | `app.css`, `app.js` |
| `fase3: un solo reloj local para el hogar` | `core/clock.py`, el global `now`, `home.py`, `tzdata` |
| `fase2: shell canonico, macros de UI e iconos` | `layouts/shell.html`, `components/{ui,icons}.html`, los globals `ui`/`ic`, `tests/test_components.py` |
| `fase2: migrar las cuatro entradas HTML al shell canonico` | `base.html`, login, 404, 500, perfil, badge, catálogo `es_AR` |
| `fase2: hacer que los cuatro graficos del dashboard se dibujen` | `dashboard/index.html` (defecto 14) |
| `fase2: validar avatar_color en la escritura` | `schemas/user.py` |
| `docs: marcar la fase 2 y anotar los defectos pendientes` | este documento |

**Una verruga honesta en esa historia:** `app/templates/capture/preview.html` estaba
*staged* como borrado desde antes, y un `git commit -F -` sin pathspec se lo llevó al
primer commit (`fase2: capa de tokens…`) en vez de al de migración de plantillas, donde
correspondía. El árbol final es idéntico y nada está pusheado, así que no se reescribió la
historia: un rebase no interactivo acá es más riesgoso que la verruga.

Reglas:

- **Nada de push ni tags** salvo pedido explícito.
- `git commit` **siempre con pathspec explícito** (`-- <paths>`). Sin pathspec commitea
  todo el índice, incluido lo que alguien dejó staged antes — ver la verruga de arriba.
- Antes de cualquier comando que pueda descartar trabajo (`checkout`/`restore`/`reset`/
  `clean`, `rm -rf`), correr `git status` primero.
- La capa de instrucciones (`.agents/`, `.claude/`, `AGENTS.md`, `CLAUDE.md`, `scripts/`)
  vivía sin trackear y **se versionó en la `caa4294`** (`chore: versionar la capa de
  instrucciones`). Sigue yendo en commits propios: **no** se mezcla en un commit de fase.

---

## Verificación

**Levantar la app** por Docker, que es lo que trae PostgreSQL:

```bash
docker compose up          # http://localhost:8000
```

**Recorrido manual, por fase:**

- **F1**: usuario nuevo → cae en el onboarding, lo completa, aterriza en Home; las 6 quick
  actions llegan a Capture **con texto**; el badge de notificaciones muestra un número;
  descartar una sugerencia desde Home la saca de la lista de verdad; ajustar stock desde la
  grilla funciona; el tab Workouts de History muestra tarjetas; la consola del navegador sin
  errores de Alpine.
- **F2/F3**: toggle claro/oscuro en las 29 pantallas sin flash al cargar y sin texto
  ilegible; ancho de 400px sin scroll horizontal; login, 404 y 500 con la misma identidad
  visual que el resto.
- **F4**: con `ENABLE_BACKGROUND_JOBS=true`, verificar que un ítem que sigue bajo **no**
  vuelve a notificar, que cada notificación lleva a su pantalla, y que el "¿Por qué esta
  sugerencia?" cita datos reales del usuario y no una frase genérica. El filtro por persona
  de la 4.4.9 se recorre desde la 4.5.7, que es cuando `generate_for_household` pasó a tener
  llamador: dejar un alimento en cero en la despensa, marcarlo como imposible para **una** de
  las dos personas desde `/profile/`, correr `run_suggestion_generation()` y ver que la lista
  de compras del hogar sale sin él —y con él cuando nadie lo bloquea—. Las dos personas ven la
  misma tarjeta, porque es del hogar y no tiene dueño.
  De la 4.4.8, en `/profile/`: rechazar una sugerencia y ver aparecer el sujeto en el panel
  de lo aprendido, con su dirección y su cantidad de registros; apretar "olvidar" y ver que
  desaparece y que el sujeto vuelve a poder salir sugerido.
  De la 4.4.10, **el paso del tiempo no se puede recorrer**: la ausencia se escribe siete
  días después de la tarjeta, así que a mano solo se puede forzar retrocediendo el
  `created_at` de una sugerencia pendiente en la base y corriendo `run_absence_sweep()` a
  mano; lo que sí se ve sin trucos es el resultado, en la línea "sugerencias sin usar" del
  mismo panel. Por eso el punto llega con 15 tests y no con un recorrido.
- **F5**: pantallas en `es_AR` sin cadenas en inglés; navegación por teclado y lector de
  pantalla en los botones icon-only.

**Compuerta de calidad** (`AGENTS.md`):

```bash
.venv/bin/python -m pytest tests/
.venv/bin/python -m ruff check .
.venv/bin/python -m black --check .
.venv/bin/python -m mypy app
alembic check
python3 scripts/agents/sync_agent_assets.py --check
```

**Línea de base medida, para no confundir deuda vieja con daño nuevo.** Los tres números
que ya estaban rotos antes de v3 no se tocan dentro de un rediseño visual, y cada
checkpoint reporta el número, no una impresión:

| Comando | Antes de v3 | Después de la Fase 2 | Después de la 4.4.7 | Después de la 4.4.8 | Después de la 4.4.9 | Después de la 4.4.10 |
|---|---|---|---|---|---|---|
| `pytest tests/` | 117 passed | **163 passed** | **498 passed** | **531 passed** | **545 passed** | **569 passed** |
| `ruff check .` | 292 findings | **288** | **256** | **260** | **257** | **261** |
| `black --check .` | 66 would reformat | 66 (sin cambio: reformatear 66 archivos adentro de un rediseño visual esconde el diff que importa) | **50** | **48** | **47** | **47** |
| `mypy app` | 47 errors / 8 files | 47 (sin cambio) | **46 / 8 files** | **46 / 8 files** | **46 / 8 files** | **46 / 8 files** |
| `sync_agent_assets.py --check` | ok | ok | ok | ok | ok | ok |

La deuda de `ruff`/`black`/`mypy` baja sola a medida que el código viejo se reescribe, y
ninguna de esas bajas es un barrido: el barrido repo-wide sigue siendo un commit aparte y
pendiente. Los números de cada columna se midieron con la forma `.venv/bin/python -m` sobre
el árbol con ese punto aplicado, **incluido el seguimiento del `instruction-steward`**: el
test 545 de la 4.4.9 es el que cierra el agujero del miembro desactivado, y por eso esa
columna no coincide con el `544` que reportó el checkpoint del commit `b116ab8`. La columna
de la 4.4.10 es la del árbol ya corregido —ventana de barrido, gate de silencio derivado y
los seis tests de la corrección incluidos—, no la de la primera pasada.

> **`ruff` no baja monótonamente, y conviene saber por qué antes de leer un alza como un
> daño.** De 250 en `393ec82` pasó a 252 con la 4.4.6 y a 256 con la 4.4.7: las 4 nuevas
> son todas `UP017` (`datetime.UTC` en lugar de `datetime.timezone.utc`) en
> `engine.py`, `suggestion_service.py` y `test_recommendations.py`, o sea código nuevo
> escrito con la forma que esos mismos archivos ya usaban en cada línea vecina. La regla
> aplica a todo el repo y su corrección es el barrido pendiente, no un arreglo local que
> dejaría un archivo con dos convenciones de la misma cosa. La 4.4.8 sumó otras 4 del
> mismo `UP017` (`learning_service.py` y `test_web_learned_panel.py`) y **bajó una** de
> largo de línea en `web/suggestions.py`, que era código propio: la deuda ajena se
> reporta, la propia se arregla. Los dos archivos nuevos y los seis tocados pasan
> `black --check` limpios, que es por qué la columna baja de 50 a 48.
>
> La 4.4.10 sube de 257 a **261** por **cuatro** `UP017` en `tests/test_learning_signals.py`,
> que es un archivo escrito entero con `timezone.utc`: las clases y los helpers nuevos usan
> la forma de sus 900 líneas vecinas en lugar de dejar un archivo con dos convenciones para
> la misma cosa (una del punto original y tres de la corrección, que agrega el envejecido de
> señales en varios tests nuevos). Los otros tres hallazgos que había introducido este punto
> —un `E501` en `suggestion_jobs.py` y dos en el test— sí se arreglaron antes de commitear,
> porque eran código propio y no una convención del archivo. `black` queda en **47** sin
> subir: los dos archivos de tests que el punto agranda vuelven formateados —estaban limpios
> en `HEAD` y se verificó archivo por archivo que la lista no creciera por ellos—, y
> `suggestion_jobs.py` ya estaba en la lista desde antes de v3 (lo que `black` le pide es
> todo anterior a este cambio).
>
> La 4.4.9 baja de 260 a **257** por la misma regla aplicada al revés: sumó **una**
> `UP017` en `tests/test_household_learning.py` —la forma que usan sus líneas vecinas y
> los tres `timezone.utc` que `engine.py` ya tenía— y **bajó cuatro** en `filters.py`, que
> es el archivo que el punto reescribe: dos `SIM102` de `if` anidados, un `SIM110` de loop
> que devolvía booleanos y un `E501`. Ese archivo queda limpio de `ruff` **y** de `black`,
> y por eso la columna de `black` baja de 48 a 47.

> `alembic check` **no corre localmente**: no hay PostgreSQL en la máquina
> (`connection to server at "localhost" (127.0.0.1), port 5432 failed: Connection
> refused`). Va por `docker compose`, y se reporta explícitamente en cada checkpoint en
> lugar de saltearlo en silencio. `ruff` además emite un warning de config propio
> (`ignore`/`select` → `lint.ignore`/`lint.select`), que también es previo a v3.

**Ruteo de agentes** (tabla de `AGENTS.md`): `frontend` para plantillas/Tailwind/Alpine/
HTMX/i18n; `backend` para las rutas web nuevas que devuelven parciales;
`nlp-recommendations` para la Fase 4; `data-persistence` para la migración `0003`;
`security-privacy` para el encuadre del consejo de sangre y el bypass de filtros;
`code-health-qa` antes de cerrar cada fase; `integrator` porque el cambio cruza capas;
`documentation-steward` al final (el onboarding no está documentado en el README y las
líneas de stack/tradeoffs cambian).

---

## Fuera de alcance de v3

Explícito, para que no se cuele por la ventana:

- **LLM en el camino de recomendación.** Añade latencia, costo y no-determinismo a un job de
  fondo, y hay muchísimo dato ya recolectado sin explotar antes de necesitarlo.
- **Superficie conversacional** ("preguntale a tus datos"): no existe ninguna ruta hoy; es
  una feature nueva, no un upgrade.
- **Edición inline completa de los intents del NLP**: requiere cambiar el contrato del
  backend.
- **Build step de Tailwind** (está en el roadmap del README): prohibido por la restricción de
  no introducir build de frontend.
- **Remember-me y recuperación de contraseña**: tocan sesión y auth; en v3 se quitan las
  promesas muertas de la UI en vez de implementarlas a medias.
- **Normalizar los marcadores de sangre en filas**: hoy viven en un blob JSON, lo que impide
  tendencia por SQL. Es un refactor de datos que merece su propio cambio.
- **Validación de CSRF.** `csrf_token` se genera y se renderiza en los formularios pero
  **ninguna ruta POST lo valida jamás** (`app/web/helpers.py:43`). Es un hueco real de
  seguridad que por `AGENTS.md` exige una revisión de `security-privacy` propia; queda
  señalado en vez de arreglado de contrabando dentro de un rediseño visual.
- **Cabeceras de seguridad.** La app **no manda ninguna**: no hay
  `Content-Security-Policy`, ni `X-Frame-Options`, ni `Referrer-Policy`, ni HSTS. Y una CSP
  útil no es posible mientras el CDN de Tailwind inyecte `<style>` en runtime: haría falta
  `style-src 'unsafe-inline'` para siempre, que es justamente lo que una CSP viene a
  cerrar. O sea que esto está atado al build step de Tailwind, que también está fuera de
  alcance. La consecuencia práctica queda anotada donde importa: **cualquier dato de
  request o de DB interpolado en un atributo `style` o en el parámetro `attrs` de un macro
  no tiene red de contención**, y por eso `components/ui.html` lleva escrita la regla de
  no interpolar nunca ahí, y `avatar_color` se valida en la escritura.
- **Objetivos nutricionales declarados** (macros o calorías objetivo por persona). Sí es una
  buena feature, y de las mejores que quedan: es el dato que le falta a la parte más nueva del
  motor. La 4.5.3 tuvo que comparar a cada persona **consigo misma a la misma hora** —su propio
  promedio de proteína y fibra en el desayuno— justamente porque no hay un objetivo contra el
  que medir, y ese es el techo de honestidad de esa tarjeta: sabe decir "hoy vas por debajo de
  tu propio promedio", no "te faltan 40 g para tu objetivo". Con un objetivo declarado, la escala
  dejaría de tener un solo lado (hoy solo mira hacia abajo, porque "vas pesado de grasa" sin
  objetivo es consejo dietario sin referencia), la despensa podría ordenarse por aporte al hueco
  del día y no por antigüedad, y el hueco sería restable en vez de comparativo. Queda afuera de
  v3 por tres razones concretas, no por falta de ganas:
  1. **Es una feature de producto, no un ajuste del motor.** Necesita una pantalla donde
     declararlos, validación de rangos plausibles, y una decisión de si son por persona o por
     casa (por persona: `goals_json` y `target_weight_kg` ya existen en `User` y **nadie los
     lee**, así que el lugar está, pero la UI no).
  2. **Toca terreno clínico.** Un objetivo calórico o proteico que la app propone —en vez de uno
     que la persona declara— es prescripción nutricional, y el mismo encuadre no diagnóstico que
     la 4.5.6 le pone a la sangre habría que diseñarlo acá. Derivar un objetivo de edad, sexo,
     peso y actividad es fácil de escribir y difícil de justificar.
  3. **El dato de entrada todavía es flojo.** Los macros salen del catálogo de `FoodItem` y de
     una cantidad estimada por el NLP; medir contra un objetivo exacto un total con ese margen
     de error da una precisión falsa. Comparar a alguien consigo mismo tolera el sesgo porque
     está en los dos lados de la comparación; restar contra un número absoluto no.
  Candidata fuerte para v4, y el orden natural sería: pantalla de objetivos → los macros dejan
  de ser comparativos → un panel de "cómo viene el día" que hoy no existe.
- **Anclar la fecha del panel a su etiqueta.** `blood_analysis_parser._extract_date` toma la
  **primera** cadena con forma de fecha de todo el documento, así que puede devolver una fecha
  de nacimiento o de impresión. Eso acota lo que la frescura de la 4.5.6 puede prometer: el
  generador confía en la fecha que recibe y no tiene forma de dudar de ella. Arreglarlo es un
  cambio del parser —patrones anclados a etiquetas ("Fecha de extracción", "Collected")— con su
  propio juego de fixtures de laboratorios reales, y el camino del LLM ya pide `analysis_date`
  explícito y es mejor. Tampoco hay hoy ninguna ruta que permita **corregir** la fecha de un
  panel: `app/web/health.py` tiene índice, alta, detalle y borrado, nada más. Las dos cosas van
  juntas y son un cambio propio.
- **Botón de acción en la tarjeta de "repetí el panel".** Hoy nombra la pantalla de Salud en el
  texto porque `web.actions.suggestion_action` devuelve `None` para todo `source_type ==
  "blood_analysis"`, y esa regla existe por un bug real: mirar la categoría le ponía "Anotar una
  comida" a un consejo de **no** comer algo. Darle acción a esta tarjeta necesita un
  discriminador que no sea `source_type` —un `category="reminder"`, por ejemplo— y reordenar la
  función para que lo consulte antes de la exclusión. Es poco código y una decisión de diseño de
  la taxonomía de tarjetas; no vale abrirla por una tarjeta.
