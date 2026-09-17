# GaiaPulse v4 — Candidatos, no un plan de fases

> Este documento **no** es un plan de implementación como `v3-plan.md`: es el backlog de
> lo que quedó deliberadamente afuera de v3, con el motivo por el que quedó afuera y una
> estimación honesta de qué tan grande es cada cosa. Cuando se decida arrancar v4, cada
> candidato se brainstormea por separado —approach, trade-offs, spec propia— igual que se
> hizo con cada fase de v3. Meter los quince acá adentro de una sola fase sería repetir el
> error que v3 evitó: "cerrar los huecos documentados" funcionó porque cada hueco tenía
> alcance propio, no porque fueran muchos a la vez.

---

## Índice

1. [Heredados de "fuera de alcance de v3"](#1-heredados-de-fuera-de-alcance-de-v3)
2. [Extensiones de una feature que v3 ya envió](#2-extensiones-de-una-feature-que-v3-ya-envió)
3. [Endurecimiento de seguridad aceptado como no bloqueante en la 7.10](#3-endurecimiento-de-seguridad-aceptado-como-no-bloqueante-en-la-710)
4. [Candidatos nuevos, no pedidos explícitamente por v3](#4-candidatos-nuevos-no-pedidos-explícitamente-por-v3)
5. [Cómo usar este documento](#5-cómo-usar-este-documento)

---

## 1. Heredados de "fuera de alcance de v3"

Los tres que `v3-plan.md` mantuvo fuera de alcance por decisión explícita del usuario en
el checkpoint de la Fase 7, más el único punto de sangre que no entró a la Fase 7 porque
nunca estuvo en la lista original de dieciséis.

### 1.1 LLM en el camino de recomendación

Hoy el motor es 100% determinista: reglas + aprendizaje bayesiano simple sobre
`BehaviorSignal`, sin ningún modelo de lenguaje en el camino que decide qué sugerir. La
razón para no meter un LLM ahí nunca fue técnica — es una decisión de **explicabilidad**:
una sugerencia que no se puede reproducir tampoco se puede explicar, y explicar el motivo
de cada tarjeta es justamente lo que v3 vino a arreglar (`docs/gaiapulse-v3.md` §4).

Dónde sí podría entrar sin pisar esa decisión: no en el *scoring* (que debe seguir siendo
determinista y auditable), sino en una capa de **redacción** posterior — tomar el
`rationale` ya calculado por el motor determinista y reescribirlo con más naturalidad, sin
que el LLM decida *qué* sugerir, solo *cómo* lo dice. Eso acota el no-determinismo a texto
cosmético, no a la decisión. Sigue siendo una feature de tamaño propio: necesita decidir
proveedor, costo por corrida (el job corre para dos personas, no para miles, así que el
costo real es bajo, pero la latencia de un job de fondo que hoy es instantáneo deja de
serlo), y qué pasa si la llamada falla — el fallback tiene que ser el texto determinista de
hoy, nunca un 500.

### 1.2 Superficie conversacional ("preguntale a tus datos")

No existe ninguna ruta hoy. Sería una feature nueva de punta a punta: una pantalla de
chat, una forma de traducir la pregunta a una consulta contra el modelo de datos existente
(comidas, entrenamientos, pesajes, sangre — todo ya vive en tablas por persona y por
hogar), y una decisión de qué tan literal es la respuesta (¿corre las mismas consultas que
ya usan las tarjetas, o abre la puerta a SQL generado?). La segunda opción es la que le
regala una superficie de ataque nueva a la app —una consulta generada que cruza el
`household_id` de otra persona sería una fuga de datos de salud entre las dos personas de
la casa, que es exactamente lo que `AGENTS.md` §2 (la capa de repositorios) existe para
evitar—, así que si esto se construye, la primera pregunta de diseño es "¿la respuesta sale
de correr uno de los repositorios ya existentes, con el `household_id`/`user_id` de quien
pregunta fijado por el mismo `require_auth` que usa el resto de la app, o de algo más
abierto?" — y la primera opción es la única que no necesita reabrir esa regla no
negociable. Necesita su propio diseño y su propia revisión de `security-privacy`, como ya
anotaba `v3-plan.md`.

### 1.3 Build step de Tailwind (y la CSP completa que depende de él)

Restricción permanente del proyecto, no una limitación técnica — no es un candidato de v4
a menos que la restricción misma se reabra primero, y esa es una decisión de alcance del
usuario, no de ingeniería. Se anota acá solo por completitud: si alguna vez se revisita,
lo que se destraba es la CSP completa (`style-src` sin `'unsafe-inline'`), que hoy vive
atada a que Tailwind inyecta `<style>` en runtime desde el CDN (`docs/gaiapulse-v3.md` §5).

### 1.4 Botón de acción en la tarjeta "repetí el panel"

El único punto de sangre que **nunca estuvo** en los dieciséis huecos originales de la
Fase 7 — surgió aparte, documentado en `v3-plan.md` → "Fuera de alcance de v3". Hoy
`web.actions.suggestion_action` devuelve `None` para todo `source_type == "blood_analysis"`
a propósito, porque mirar la categoría del panel le pondría "Anotar una comida" a un
consejo de **no** comer algo. Dar acción a esta tarjeta necesita un discriminador nuevo que
no sea `source_type` (por ejemplo `category="reminder"` en `Suggestion`) y reordenar
`suggestion_action` para que lo consulte antes de la exclusión actual. Es el candidato más
chico de este documento — una migración liviana (una columna) y una función que ya existe,
no una feature nueva.

---

## 2. Extensiones de una feature que v3 ya envió

La Fase 7.6 cerró **la base** del objetivo nutricional declarado: `User` tiene
`goal_protein_g`/`goal_fiber_g`/`goal_calories_kcal`, el formulario de perfil los valida, y
`meal_generator` compara contra el objetivo cuando existe. Lo que la 7.6 **no** hizo — y
que el propio texto de la fase, cuando todavía vivía en "fuera de alcance", ya adelantaba
como el orden natural de una segunda vuelta — son dos extensiones de producto sobre esa
misma base, no un cambio del motor:

- **La despensa ordenada por aporte al hueco del día**, en vez de por antigüedad. Hoy
  `pantry_generator` no sabe de objetivos; con `goal_protein_g`/`goal_fiber_g` ya poblados
  en `UserContext`, ordenar por cuánto de ese alimento falta para llegar al objetivo del
  día es una función nueva sobre datos que ya están, no una tabla nueva.
- **Un panel de "cómo viene el día"**, que hoy no existe: una vista que compare lo
  consumido hasta el momento contra el objetivo declarado, restable en vez de comparativo
  ("te faltan 40 g de proteína", no solo "hoy vas por debajo de tu propio promedio"). Es la
  pantalla que le falta al dato que la 7.6 ya declara y valida.

Las dos comparten la misma precondición que la 7.6 ya documentó y sigue siendo cierta: el
dato de entrada (macros estimados por el NLP contra el catálogo de `FoodItem`) tiene un
margen de error que una comparación contra el propio promedio tolera —está en los dos
lados— pero que un resto exacto contra un número absoluto no. Antes de construir el panel
de "cómo viene el día" vale la pena decidir si ese margen se comunica en la UI (un rango,
no un número puntual) en vez de ignorarlo.

---

## 3. Endurecimiento de seguridad aceptado como no bloqueante en la 7.10

`security-privacy` revisó `AuthService`/`app/web/auth.py` en la 7.10 y aprobó con tres
seguimientos explícitos, ya documentados en `v3-plan.md` como aceptados — no son huecos
que se pasaron por alto, son riesgo LOW que la persona con la decisión eligió no bloquear
el cierre de la fase por ellos. Quedan acá como candidatos de v4 porque cerrarlos bien
pide más que una línea:

- **TTL duplicado entre código y texto del mail.** `RESET_TOKEN_TTL = timedelta(hours=1)`
  en `auth_service.py` y el string "within an hour" del cuerpo del mail son el mismo dato
  escrito dos veces. El único de los tres que es un cambio de una línea (interpolar el TTL
  en el string) — se deja para v4 y no se corrige de paso en este cierre porque tocar
  código de auth sin que `security-privacy` lo vuelva a mirar completo iría en contra del
  motivo por el que esta lista existe: no reabrir una decisión de seguridad ya cerrada sin
  la misma revisión que la cerró.
- **El reset de contraseña no invalida otras sesiones activas.** Patrón ya existente en
  toda la app (`decode_session_token` no tiene ningún mecanismo de revocación — es firma +
  expiración, sin tabla de sesiones), no una regresión de la 7.10. Cerrarlo de verdad pide
  estado nuevo: algo como una columna `password_changed_at` en `User` y una comprobación
  contra el *timestamp* que `itsdangerous` ya firma dentro del token (`return_timestamp=True`
  en `URLSafeTimedSerializer.loads`, disponible sin cambiar el esquema del token) en el
  punto donde `require_auth` ya carga el usuario. Es la candidata más grande de las tres:
  toca el modelo de sesión de toda la app, no solo el flujo de reset.
- **Side-channel de tiempo entre email conocido y desconocido (LOW).** `request_password_reset`
  hace un `return` temprano para un email inexistente o inactivo, y el camino completo
  (generar token, escribir en la base, mandar el mail) para uno real — una diferencia de
  tiempo medible, aunque de severidad baja para una app de dos personas en una casa, no un
  servicio expuesto a fuerza bruta a escala. Mitigarlo bien (trabajo simulado de duración
  pareja en el camino corto) es la clásica sobre-ingeniería que v3 evitó a propósito en
  todos sus puntos; si se ataca en v4, que sea con una medición real de cuánto tarda cada
  camino primero, no con una mitigación genérica.

---

## 4. Candidatos nuevos, no pedidos explícitamente por v3

Identificados en este repaso de fases 1-7, no en el pedido original de v3. Se listan con
la misma vara: solo entran si resuelven algo que hoy falta, no porque "estaría bueno".

- **Gráfico de tendencia de marcadores de sangre.** La 7.7 normalizó `BloodAnalysis.values_json`
  a filas de `BloodMarker` (`analysis_id`, `marker_key`, `value`, ...) justamente para
  habilitar tendencia por SQL — pero nada en la UI la muestra todavía: `health/detail.html`
  sigue mostrando un panel a la vez. Con la tabla ya normalizada, un gráfico de un
  marcador a través de varios paneles (colesterol en el tiempo, por ejemplo) es la
  consecuencia natural de un cambio que v3 ya pagó; no pedir esto en v4 dejaría a la 7.7
  como una migración de datos sin usuario.
- **Plantillas de entrenamiento habituales ("Rutinas guardadas").** Poder guardar una
  sesión frecuente ("Día de Piernas", "Torso Fuerza", etc.) o marcar un entrenamiento
  previo como plantilla para instanciarlo en 1 click con sus ejercicios pre-cargados y
  solo actualizar series/pesos. Elimina fricción repetitiva en el gimnasio.
- **Ingestión de análisis de sangre por foto/OCR (Visión multimodal).** Permitir subir
  una foto o PDF del informe del laboratorio y que un extractor multimodal parsee los
  marcadores directamente hacia `NLPIngestionEvent` en estado `pending_confirmation`,
  respetando estrictamente la compuerta humana obligatoria de `AGENTS.md` §3 antes de
  escribir en `blood_markers`.
- **Insights y correlaciones cruzadas entre dominios.** Un módulo analítico que cruce
  datos entre dominios (p. ej. gramos de proteína consumidos vs progresión de peso en
  fuerza, o tendencias de glucemia/colesterol en sangre vs tipos de alimentos frecuentes
  en despensa) para generar sugerencias con evidencia cruzada.
- **Resumen semanal del hogar por notificación / email.** Un job periódico programado
  que consolide el balance semanal del hogar (sesiones de entrenamiento completadas,
  variedad de alimentos frescos en despensa, evolución conjunta) y lo entregue a los
  miembros para reforzar el hábito compartido.
- **Redacción del `rationale` con más naturalidad** (ver 1.1): la parte de "usar un LLM"
  que no compromete el determinismo del scoring, separada de la superficie conversacional
  porque no necesita ruta nueva ni cambia qué se sugiere, solo cómo se lo redacta.
- **Exportar los propios datos.** Hoy no hay ninguna forma de sacar de la app lo que la
  persona escribió (comidas, entrenamientos, pesajes, paneles de sangre) más que mirar la
  pantalla. Un export a CSV/JSON por persona es chico —los repositorios que ya filtran por
  usuario son exactamente los que alimentarían el export— y es la única feature de esta
  lista que no depende de ninguna otra decisión de v4 para empezarse.
- **Ampliar el hogar de dos personas.** El modelo de datos asume dos (`docs/gaiapulse-v3.md`
  §1) en varios lugares además del esquema —listas de participantes en pantallas de
  comida/entrenamiento, textos que asumen "los dos"—, no es un límite de una sola tabla.
  Se anota como candidato porque es la pregunta que más cambia el resto del roadmap si la
  respuesta es "sí" (una casa de tres o cuatro no es un cambio de UI, es un cambio de cómo
  se escribe cada pantalla que hoy dice "las dos personas"), no porque haya evidencia de
  que haga falta.

---

## 5. Cómo usar este documento

No hay orden de prioridad implícito en la numeración de arriba — es temático, no de
importancia. Antes de arrancar cualquiera de estos: releer `AGENTS.md` (la capa de
repositorios y las restricciones permanentes no cambian entre v3 y v4), confirmar que el
candidato sigue siendo cierto (este documento envejece igual que `gaiapulse-v3.md` §5
envejeció durante la Fase 7 — algo de esta lista puede terminar resuelto de paso por otro
cambio, y hay que verificarlo, no asumir que sigue pendiente), y brainstormear ese único
candidato con su propio approach y su propia revisión de `security-privacy` o
`data-persistence` según lo que toque — el mismo proceso que usó cada fase de v3, aplicado
de a un candidato por vez.
