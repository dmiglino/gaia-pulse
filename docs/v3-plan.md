# GaiaPulse v3 — Plan de trabajo

> Producto **bonito, intuitivo, dinámico, inmersivo, moderno e inteligente**.
> Branch: `v3`. Entrega en 5 fases con checkpoint al final de cada una.
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
  solo en `repositories/`.
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

- [ ] **Capa de tokens** en `app/static/css/app.css`: `--surface`, `--card`, `--line`,
      `--ink`, `--ink-muted` + acentos de dominio (food / move / body / stock / alert),
      definidos en `:root` y redefinidos bajo `[data-theme="dark"]` y
      `@media (prefers-color-scheme: dark)`. Se referencian desde la config de Tailwind como
      `rgb(var(--x) / <alpha-value>)`, de modo que **el modo oscuro es un swap de variables,
      no un barrido de clases `dark:`**. Elimina también los hex hardcodeados de `app.css`
      (scrollbar, toasts, nav-active).
- [ ] **`app/templates/layouts/shell.html`**: un `<head>` canónico único (meta, viewport,
      theme-color, **una** config de Tailwind, Inter, HTMX/Alpine/Chart, `app.css`, y el
      script de inicialización de tema **antes** del render para evitar el flash).
      `base.html`, `auth/login.html`, `errors/404.html` y `errors/500.html` lo extienden.
      Es necesario porque los handlers de error pasan solo `{"request": request}`
      (`app/main.py`), que es la razón por la que hoy son documentos HTML sueltos con configs
      divergentes.
- [ ] **`app/templates/components/icons.html`** con un macro `icon(name, class)` que absorbe
      los 91 SVG copiados y reemplaza los emoji-como-ícono.
- [ ] **`app/templates/components/ui.html`** con `card`, `page_header`, `stat`, `badge`,
      `btn`, `avatar`, `tabs`. Hoy hay **cero** macros en todo el proyecto.
- [ ] Toggle de tema en `app/static/js/app.js` (detección de sistema + persistencia en
      `localStorage`, con try/catch).
- [ ] Escala de radios y **una sola** gramática de tarjeta; consolidar las 12 paletas
      paralelas en los tokens.
- [ ] Borrar el huérfano `app/templates/capture/preview.html` (254 líneas).

---

### Fase 3 — Barrido visual + rediseño de IA

- [ ] Las 29 plantillas pasan a macros y tokens (patrón repetido; las que no se rediseñan
      conservan su layout).
- [ ] **Home** — rediseño de arquitectura de información: de "lista de tarjetas" a una
      jerarquía con un **estado de hoy** arriba, la acción primaria dominante, y las
      sugerencias accionables en un toque.
- [ ] **Capture** — el flujo core:
  - entrada más prominente,
  - chips de ejemplo con lenguaje natural real (hoy son plantillas con `[placeholders]`),
  - **preview de confirmación rediseñado**: atribución correcta (avatar + nombre por
    intent), leyenda que explica qué significa el % de confianza y qué pasa al confirmar,
    eliminación del estado `editing` muerto, y la rama `{% else %}` que hoy vuelca
    `{{ intent | tojson }}` crudo al usuario reemplazada por un fallback legible.
  - Edición inline completa **fuera de v3** (requiere cambiar el contrato del backend).
- [ ] **Dashboard** — Chart.js con los tokens y colores que funcionen en ambos temas.
- [ ] `components/empty_state.html` en los ~9 lugares que lo necesitan, no en 3.
- [ ] `errors/404.html` y `errors/500.html` pasan a ser páginas reales de la app.

---

### Fase 4 — Inteligencia oportuna, con memoria y explicable

Cinco cambios, en este orden. **Ninguno introduce un LLM en el camino de recomendación.**

**4.1 — Un solo reloj local**

- [ ] Nuevo `app/core/clock.py` con `local_now()` y `is_quiet_hours()`.
- [ ] `scheduler.py` pasa de `IntervalTrigger` a `CronTrigger` en la timezone configurada
      (stock a la mañana, inactividad al mediodía, peso al arrancar el día), de modo que la
      hora deje de depender de cuándo arrancó el proceso.
- [ ] Gate de horario de silencio antes de crear cualquier notificación.
- [ ] Unificar naive/aware en repositorios y servicios.
- [ ] Conectar `notification_job_interval_minutes` o eliminarlo del config, del README y de
      `.env.example`.

**4.2 — Dedup por sujeto, con escalada en vez de repetición**

- [ ] Los jobs pasan a poblar `related_entity_type`/`related_entity_id` (columnas que **ya
      existen**).
- [ ] Nuevo `has_recent_for_subject` reemplaza el chequeo por categoría: se avisa **una vez
      por sujeto**, y se vuelve a avisar solo si **empeora** (stock que baja más, inactividad
      que pasa de 4 a 8 días).
- [ ] Poda de notificaciones viejas.
- [ ] `get_unread_count` con `COUNT(*)` en SQL en vez de traer todas las filas.

**4.3 — Todo lo que la app dice lleva a algún lado**

- [ ] Exponer `related_entity_*` en `NotificationRead`.
- [ ] Renderizar cada notificación y cada sugerencia con **una acción primaria** que aterrice
      en la pantalla correcta con la captura preprellenada — aprovechando el prefill que la
      Fase 1 arregla.
- [ ] Detectar más ausencias además de las dos actuales (hoy solo entrenamiento y pesaje):
      comidas no registradas, sueño ausente.

**4.4 — Aprendizaje anclado al sujeto, no al título**

- [ ] **Migración `0003`**: `subject_type` + `subject_name` en `suggestions` (la única
      migración de todo v3).
- [ ] Cada generador declara el sujeto de su candidato (alimento, actividad, grupo muscular,
      marcador, ítem de stock).
- [ ] El feedback se guarda contra ese sujeto en lugar de `title.lower()[:200]`.
- [ ] El scorer matchea por sujeto normalizado en vez de bolsa-de-palabras.
- [ ] `snoozed` pasa a ser una supresión acotada en el tiempo usando `snoozed_until`, que ya
      existe y no tiene ninguna ruta que lo escriba.
- [ ] El motivo de texto libre se usa como señal en vez de morir en `notes`.
- [ ] Eliminar el `record_feedback` muerto.
- [ ] Acotar la dominancia monótona de los alimentos de todos los días, para que el loop
      pueda empujar variedad.
- [ ] **Dedup antes de persistir**: no crear una sugerencia pendiente cuyo sujeto ya tiene
      una pendiente.

**4.5 — Razonar con los datos que ya están, y explicar de verdad**

- [ ] Nuevo `app/recommendations/context.py`: un `UserContext` de solo lectura, armado **una
      vez por corrida** desde los repositorios y pasado a todos los generadores y al scorer —
      tendencia de peso/grasa, sueño reciente, RPE y balance de grupos musculares con
      ventanas de recuperación reales, totales de macros derivados del catálogo de `FoodItem`
      que ya está sembrado, `ExerciseType` en lugar de la lista hardcodeada de 8, y
      antigüedad del panel de sangre.
- [ ] Con ese contexto, `rationale` deja de ser un string fijo y pasa a ser **la explicación
      computada del score**: qué dato y qué señal produjeron esta sugerencia.
- [ ] Conectar `generate_for_household` para que las sugerencias de pantry/compras existan.
- [ ] **Arreglar el bypass de seguridad** de `category="habit"` en `_infer_category` más el
      atajo `return candidates` que saltea el filtro cuando no hay bloqueos.
- [ ] **Seguridad:** las sugerencias de sangre pasan a llevar la antigüedad del panel
      explícita y un encuadre no diagnóstico, y a atravesar los filtros como todas las demás.

**Archivos:** `app/jobs/{scheduler,notification_jobs}.py`,
`app/repositories/notification_repo.py`, `app/schemas/notification.py`,
`app/services/suggestion_service.py`, `app/recommendations/{engine,scorer,filters}.py` +
`generators/*.py`, `app/models/suggestion.py`, nuevos `app/core/clock.py` y
`app/recommendations/context.py`, nueva revisión `alembic/versions/0003_*.py`.

---

### Fase 5 — i18n, accesibilidad y red de seguridad

**5.1 — i18n**

- [ ] Traducir `health/detail.html` (hoy con **cero** `_()`) y
      `suggestions/partials/dismissed.html`.
- [ ] Mapear los valores de enum a etiquetas traducibles en vez de `|title`.
- [ ] Plurales con `ngettext`.
- [ ] Sacar el glifo de `_('✓ Confirm & Save')`.
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

Reglas:

- **Nada de push ni tags** salvo pedido explícito.
- Antes de cualquier comando que pueda descartar trabajo (`checkout`/`restore`/`reset`/
  `clean`, `rm -rf`), correr `git status` primero.
- Los archivos sin trackear que ya estaban antes de v3 (`.agents/`, `.claude/`, `AGENTS.md`,
  `CLAUDE.md`, `scripts/`) **no** se mezclan en los commits de v3; son una decisión aparte
  del usuario.

---

## Verificación

**Levantar la app** (no hay venv local con `passlib`/`psycopg2`, así que va por Docker):

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
  sugerencia?" cita datos reales del usuario y no una frase genérica.
- **F5**: pantallas en `es_AR` sin cadenas en inglés; navegación por teclado y lector de
  pantalla en los botones icon-only.

**Compuerta de calidad** (`AGENTS.md`):

```bash
pytest tests/ --cov=app --cov-report=term-missing
black --check .
mypy app
alembic check
python3 scripts/agents/sync_agent_assets.py --check
```

> `ruff` **no está instalado en este entorno** (`exit 127`). Se reporta explícitamente en
> cada checkpoint en lugar de saltearlo en silencio.

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
