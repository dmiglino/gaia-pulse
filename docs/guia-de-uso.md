# GaiaPulse — cómo usarla

Esta guía es para Diego y Rocío. No explica cómo está hecha la app: explica cómo se usa y,
sobre todo, **cómo enseñarle**, que es la parte que no es obvia y la que hace que valga la
pena. Si lo que buscás es el estado técnico de la app, el otro documento es
[`gaiapulse-v3.md`](gaiapulse-v3.md).

Una sola idea antes de empezar, porque ordena todo lo demás: **GaiaPulse no adivina, deduce
y pregunta.** Escribís una frase, te muestra qué entendió, y recién cuando confirmás se
guarda algo. Nada de lo que escribís entra a la app sin pasar por esa pantalla.

---

## 1. La primera semana

### Día 1 — el cuestionario de bienvenida

La primera vez que entrás, la app no te deja pasar a nada más hasta que respondas cuatro
pantallas. Son cortas a propósito:

1. **Año de nacimiento y sexo.** Sirven para estimar cuánta energía gastás.
2. **Tus medidas**: altura, peso de hoy y —si querés— peso objetivo. El peso de hoy queda
   guardado como tu primera medición, así que la tendencia arranca ese mismo día. Si no
   querés apuntar a ningún número, dejá el objetivo vacío: la app te acompaña igual, solo
   deja de comparar contra una meta.
3. **Cuánta actividad hacés normalmente.** Es la línea de base contra la que después compara
   tu semana. Si dudás, elegí lo más bajo: es más fácil que la app note que hacés más de lo
   que dijiste que al revés.
4. **Si hay algo que no comés.** Hay atajos para lo común (gluten, lactosa, lácteos, cerdo,
   carne roja, mariscos) y podés escribir lo que quieras.

Esa última pantalla merece una aclaración, porque es el control más fuerte que tiene la app:
**las restricciones alimentarias no bajan de puesto una sugerencia, la descartan.** Si
escribís "gluten", ninguna sugerencia con gluten va a aparecer nunca, sin importar cuánto
encaje con todo lo demás. Todo lo que respondas acá se puede cambiar después desde **Perfil**.

### Día 1, un rato más tarde — la primera captura

Desde **Inicio**, el botón grande dice *"Empezar a capturar"*. Escribí una frase, como se la
dirías a alguien:

> cenamos milanesas y ensalada

La app te muestra una pantalla de confirmación con **qué entendió, de quién es y con cuánta
seguridad**. Si está bien, confirmás. Si está mal, descartás y volvés a escribir — descartar
no deja nada anotado en ningún lado.

Hacé esto tres o cuatro veces el primer día, con cosas que ya pasaron. No hace falta que sea
exhaustivo. Lo importante es que la app empiece a tener con qué.

### Días 2 y 3 — el peso y la despensa

**El peso**: escribilo como sea más natural (`pesé 81,5 kg`). Cargarlo cada mañana, más o
menos a la misma hora, es lo que le permite ver tendencia en lugar de ver saltos. Un día
sí y un día no también sirve; lo que no sirve es medirse tres veces el mismo día.

**La despensa** es la que enciende media app, y conviene saberlo desde el principio: sin
stock cargado, las sugerencias de comida no pueden mirar qué tenés y las alertas de
"se está acabando" no tienen nada que contar. Cargarla es escribir lo que compraste:

> compré 1 kg de avena y 6 huevos

No hace falta cargar todo de una. Se va llenando con las compras normales.

### El resto de la semana — por qué las primeras sugerencias son flojas

Las sugerencias se arman **dos veces por día**, a las 7:40 y a las 18:40. Las primeras van a
ser genéricas, y eso no es un defecto: es que todavía no tiene con qué. La app ordena lo que
te propone según lo que registraste y según cómo respondiste a las tarjetas anteriores, y en
el día uno esas dos cosas están vacías.

Lo que sí cambia rápido es el tono. Al cuarto o quinto día empiezan a aparecer tarjetas que
citan **tus** datos —tu propio promedio de proteína en el desayuno, cuántos días pasaron
desde tu último entrenamiento, qué grupo muscular te queda descansado— en vez de frases de
catálogo. La sección **"¿Por qué esta sugerencia?"** de cada tarjeta es la forma de ver si
la app ya te conoce o todavía está adivinando: si dice que no tiene ni datos ni aprendizaje
para respaldarla, **te lo dice**.

---

## 2. Cómo hablarle

Entiende **castellano y inglés**, y no hay que avisarle en cuál estás escribiendo. Lo que
sigue son formas verificadas: están probadas una por una contra el motor que lee las frases.

### Comidas

| Escribí | Entiende |
|---|---|
| `cenamos milanesas y ensalada` | cena, **de los dos**, dos alimentos |
| `desayuné café y tostadas` | desayuno, tuyo, dos alimentos |
| `almorzamos tarta de verduras` | almuerzo, de los dos |
| `merendé un té y una tostada` | merienda, tuya |
| `comí 2 bananas y un yogur` | dos bananas y un yogur, tuyos |
| `Diego almorzó tarta, Rocío almorzó ensalada` | **un** almuerzo con dos platos distintos |

Cuatro cosas que se ganan gratis si las tenés en cuenta:

**El verbo dice de quién es y qué comida fue.** `cenamos` es "los dos, cena". `cené` es "yo,
cena". No hace falta escribir "nosotros" ni aclarar "para la cena": ya está en el verbo. Y
si nombrás a las dos personas en la misma frase, sale **una** comida compartida con lo de
cada uno, no dos comidas separadas.

**Enumerá con "y", no con "con".** Es la primera de las dos reglas de escritura que conviene
memorizar:

> ✅ `desayuné café **y** tostadas` → guarda el café y las tostadas
> ⚠️ `desayuné café **con** tostadas` → guarda **solo** el café

No es un error que vaya a arreglarse: "con" se usa para nombrar *un* plato ("café con
leche", "arroz con pollo", "tarta de verduras"), y si la app cortara ahí, un café con leche
se convertiría en dos alimentos. Se eligió perder el segundo alimento antes que inventar
uno. Usá "y" cuando son cosas distintas.

**Un tema por captura.** Y es la segunda. Dentro de un mismo tema podés poner todo junto:
varios alimentos, varias compras, el peso con el sueño. Mezclar temas distintos en una misma
frase es lo que no funciona:

> ✅ `cenamos milanesas y ensalada` · `pesé 81 kg y dormí 7 horas` · `compré avena y 6 huevos`
> ❌ `cené fideos y corrí 30 minutos` → no entiende **ninguna** de las dos cosas
> ⚠️ `compré leche y pesé 80 kg` → anota el peso y la leche, **y además** un producto
> fantasma llamado *"pesé 80 kg"*

Una comida y un entrenamiento en la misma frase se anulan entre sí: no es que entienda una y
pierda la otra, es que no entiende ninguna, y la pantalla de confirmación te llega vacía. Con
`compré` el problema es el otro: lo que viene después del "y" se lee como un producto más, así
que te queda basura en la despensa. Separar en dos capturas cuesta diez segundos y siempre
funciona. El punto no ayuda: `cené fideos. corrí 30 minutos` falla igual.

**Escribilo el mismo día.** La app guarda la hora en que **confirmás**, no la hora que menciona
la frase: escribir "ayer cenamos pizza" guarda una cena de hoy, y "anoche" ni se lee. No hay
forma de cargar algo con fecha de anteayer, así que lo viejo conviene dejarlo pasar: una comida
con la fecha equivocada ensucia la tendencia más de lo que la completa.

### Entrenamientos

| Escribí | Entiende |
|---|---|
| `hice yoga 40 minutos` | yoga, 40 minutos |
| `fuimos a spinning` | spinning, **de los dos** |
| `corrí 30 minutos` | entrenamiento de 30 minutos |
| `entrené pesas una hora` | entrenamiento de 60 minutos |
| `caminamos 45 minutos` | de los dos, 45 minutos |

**Decí los minutos.** La duración es el dato que el motor usa para casi todo lo demás
—cuánto entrenaste esta semana, si un músculo ya descansó, si estás muy por debajo de tu
línea de base—, así que una frase sin minutos guarda un entrenamiento medio ciego. Y ojo con
la distancia: `corrí 5 km` **sí** guarda el entrenamiento, pero sin duración, porque los
kilómetros no son un dato que la app tenga dónde poner. `corrí 30 minutos` vale más que
`corrí 5 km`.

Podés escribir la duración con números o con letras: `40 minutos`, `una hora`, `media hora`,
o con el apóstrofo como minutos: `entrené 30'`. Una forma que **no** lee: las duraciones
compuestas (`hora y media` le queda en una hora). Para una hora y media, escribí
`90 minutos`.

**Los nombres de actividad tienen dos niveles**, y conviene saber por qué:

- Las que se dicen igual en los dos idiomas —**yoga, pilates, spinning, crossfit, running,
  hiit, zumba, cardio**— entran al catálogo de ejercicios con su grupo muscular, así que
  además de quedar registradas alimentan el balance de músculos y la recuperación.
- Las que solo se dicen en castellano —**correr, caminar, pesas, nadar, bicicleta**— se
  entienden perfectamente como entrenamiento y como gusto, pero quedan como texto libre: se
  ven en pantalla, se guardan, y no alimentan el balance por grupo muscular. Si te interesa
  esa parte, escribí `running` en lugar de `correr`.

### Peso y sueño

| Escribí | Entiende |
|---|---|
| `pesé 81,5 kg` | tu peso de hoy |
| `dormí 6 horas` | tus horas de sueño |
| `pesé 81 kg y dormí 7 horas` | las dos cosas en una medición |

La coma decimal funciona (`81,5`) y el punto también. **El sueño se anota junto con el
peso**, no como un registro aparte: la app te lo va a pedir cuando falte, porque es uno de
los datos que más cambia lo que te sugiere y el más fácil de olvidar.

### Despensa

| Escribí | Entiende |
|---|---|
| `compré 1 kg de avena y 6 huevos` | dos altas de stock, con cantidad y unidad |
| `usamos 2 huevos` | consumo de 2 huevos |
| `se acabó la leche` | la leche llegó a cero |
| `no queda café` | el café llegó a cero |

Las tres formas de avisar que algo se terminó funcionan igual: `se acabó`, `se terminó`,
`no queda`, `no hay más`, `usamos`, `gastamos`. `docena` también se entiende: `compré una
docena de huevos` guarda 12 huevos, y `compré 2 docenas de huevos` guarda 24.

### Preferencias

| Escribí | Entiende |
|---|---|
| `no me gusta el brócoli` | no te gusta ese alimento (solo a vos) |
| `prefiero el pollo` | te gusta ese alimento |
| `no nos gusta el brócoli` | no le gusta **a los dos** |
| `no podemos comer gluten` | restricción de los dos |
| `prefiero correr` | te gusta esa **actividad** |

Igual que con las comidas, el verbo decide de quién es: `me gusta` es tuyo, `nos gusta` es de
los dos.

### Cuando no entiende

Si escribís algo que la app no sabe leer, no explota ni te avisa con un cartel rojo: te
muestra la pantalla de confirmación **sin nada claro que confirmar** y con la seguridad muy
baja. Eso es la señal. Descartá y reescribí más simple: **un tema por vez**, un verbo conocido
(`cenamos`, `corrí`, `compré`, `pesé`, `se acabó`), y las cosas separadas con "y". La causa más
común de una confirmación vacía es haber puesto una comida y un entrenamiento en la misma frase.

---

## 3. Cómo aprende, y cómo enseñarle

Cada tarjeta de sugerencia tiene tres botones, y **los tres significan cosas distintas**.
Esta es probablemente la sección más útil de la guía, porque los tres se parecen y hacen
cosas muy diferentes.

| Botón | Qué hace |
|---|---|
| **Buena idea** | Cuenta como voto a favor de ese tema. Sube su lugar en la lista de acá en adelante. |
| **Ahora no** | **Calla ese tema por tres días** y no cuenta como rechazo. Es el botón para "hoy no me da". |
| **No es para nosotros** | Es un rechazo deliberado. **Saca ese tema de la lista**, no lo baja de puesto. |

La distinción que importa: **"Ahora no" no te está enseñando nada.** Es una postergación. Si
lo que querés es que deje de proponerte algo, el botón es *"No es para nosotros"*.

### Un toque es una pista, seis son una regla

La app no trata todas las señales igual según cuántas veces las vio, y la escala no es
lineal a propósito:

- **Una vez** que registrás o aceptás algo, cuenta poco: podés haber comido pollo un día
  porque era lo que había.
- **Dos veces** ya cuenta como la mitad de lo que puede llegar a contar.
- **Seis veces** es prácticamente una regla, y de ahí en adelante casi no se mueve.

Está armado así para que un día raro no te cambie el perfil, y para que una costumbre real no
necesite cincuenta repeticiones para que la app la note. En la práctica: si querés que
aprenda algo, hacelo dos o tres veces. No hace falta más.

### Un "no" deliberado sí veta, y sí caduca

*"No es para nosotros"* es la única cosa que **saca** algo de la lista en vez de bajarlo de
puesto: un solo toque alcanza. Pero **caduca**: después de unas semanas ese tema puede volver
a aparecer.

Eso es intencional. Un "no" de marzo no debería seguir mandando en septiembre, porque los
gustos cambian y porque a veces se dice no por el momento y no por la cosa. Si algo no lo
querés **nunca más**, no uses el botón: escribilo en **Perfil → Restricciones alimentarias**
o **Actividades imposibles**. Esas dos listas no vencen nunca.

### Rechazar tres verduras le enseña algo sobre la cuarta

Además de aprender sobre cada cosa, la app saca conclusiones por **categoría**. Si rechazás
brócoli, espinaca y acelga, empieza a ofrecerte menos verduras en general, incluso las que
nunca nombraste. Es lo que le permite generalizar en lugar de esperar a que te pronuncies
sobre los 300 alimentos del catálogo.

Tiene una consecuencia práctica: si lo que te molesta es **un** alimento y no la familia,
conviene decirlo con palabras (ver la sección siguiente) en vez de rechazar tarjetas hasta
que deje de aparecer. Las conclusiones por categoría se ven en **Perfil**, en *"Patrones por
categoría"*, así que si generalizó de más lo vas a poder ver.

### Lo que la app deja de repetir por su cuenta

Dos cosas que ya hace sin que tengas que enseñarle nada, para que no las peleés:

- **No te avisa dos veces de lo mismo.** Cada alerta se cuenta por sujeto: si la leche está
  baja, te lo dice una vez y vuelve a decírtelo solo si **empeora**.
- **No te habla de noche.** Entre las 22 y las 8 no crea notificaciones.

---

## 4. Decirle por qué

Es la forma más rápida de enseñarle y la menos evidente, porque está escondida detrás de un
enlace chico. En cada tarjeta hay un *"¿Preferís decir por qué?"*, y ahí se abre un campo:
*"¿Qué no te gustó?"*.

**Nombrá la comida o la actividad.** La app busca en lo que escribís los nombres que conoce
y aprende sobre **esos**, no sobre cómo estaba redactada la tarjeta:

> ✅ `no nos gusta el brócoli` → aprende sobre el brócoli
> ❌ `no nos convence` → no aprende nada (y no pasa nada malo)

Tres cosas que se notan al usarlo:

**Una frase corta enseña mejor que una lista.** De un motivo se aprenden hasta cinco cosas, y
lo que escribís tiene **un solo signo**: todo lo que nombres en esa frase baja junto. Así que
`no nos gusta el brócoli, preferimos el pollo` baja los dos, incluido el pollo. Si querés
decir las dos cosas, decilas por separado: rechazá con el motivo del brócoli, y escribí
`prefiero el pollo` en una captura.

**Si el nombre no le suena, la frase no enseña nada.** No hay error, no hay aviso: el motivo
queda guardado como texto y listo. Es el modo de falla barato, y por eso conviene usar
nombres simples y conocidos.

**Sirve para lo positivo también.** Cuando aceptás algo porque *sí* te gusta un ingrediente
en particular, nombrarlo es más rápido que esperar a que la app lo deduzca de seis comidas.

---

## 5. Cómo leer una sugerencia

Cada tarjeta tiene, debajo, un *"¿Por qué esta sugerencia?"* que se abre y muestra dos cosas:

- **Sale de** — el dato concreto que la produjo: cuántos días pasaron desde tu último
  entrenamiento, qué grupo muscular está descansado, qué tenés en la despensa, cómo viene tu
  proteína del día contra tu propio promedio a esta hora.
- **Qué tan seguro** — un porcentaje.

Ese porcentaje es **la seguridad de la app sobre la sugerencia, no una promesa sobre vos**.
Un 90% quiere decir "esto encaja mucho con lo que registraste"; no quiere decir que te vaya a
hacer bien con 90% de probabilidad. Y una tarjeta puede tener 60% y ser exactamente lo que
necesitás: el número ordena la lista, no la valida.

Dos cosas más que conviene tener claras:

**Nada de esto es una recomendación médica.** Las tarjetas que salen de un análisis de sangre
llevan el aviso escrito arriba: son informativas, y un marcador fuera de rango es una
conversación con tu médico, no un cambio de dieta por tu cuenta. La app también dice **de
cuándo es** el análisis del que está hablando, justamente para que un panel de hace dos años
no suene como si fuera de esta semana.

**Si no dice de dónde sale, es que no sabe.** Cuando una tarjeta no tiene ni un dato tuyo ni
aprendizaje detrás, lo declara en vez de inventar una frase que suene bien. Esas son las que
conviene mirar con más distancia.

---

## 6. La lista de compras es de los dos

Entre tus sugerencias personales aparecen algunas con la etiqueta **"Para la casa"**: qué se
acabó, qué está por acabarse, qué se compra siempre y hoy no está. Esas **son la misma
tarjeta para los dos**, no una copia para cada uno: si uno la acepta, desaparece de la lista
del otro. Es a propósito — la despensa es una sola.

Tres cosas para no pelearse con esta parte:

**Cargar la despensa es lo que las enciende.** Sin stock cargado no hay nada que contar, y la
lista de compras va a estar vacía por más que compres cosas. Cada `compré …` que confirmás la
alimenta.

**"Poco" hoy significa cero.** La app puede avisar cuando algo baja de un umbral, pero **no
hay pantalla para fijar ese umbral**, así que en la práctica avisa cuando algo llega a cero:
cuando escribís `se acabó la leche` o cuando los consumos la agotan. Si querés adelantarte,
avisá vos. Y no intentes matizarlo con palabras: `no queda mucho café` te guarda un ítem
llamado *"mucho café"*, porque todo lo que viene después del verbo se lee como el nombre de la
cosa. Lo que funciona es cargar la compra antes de que se termine.

**Una restricción de uno saca el alimento de la casa; un "no" de uno, no.** Si Rocío pone
"lactosa" en sus restricciones, los lácteos desaparecen de la lista del hogar: una
restricción alimentaria vale para toda la casa porque la casa cocina una vez. En cambio si
Rocío toca *"No es para nosotros"* en una tarjeta con leche, eso solo cambia **su** lista de
sugerencias. Para que algo salga de la lista compartida por gusto y no por restricción,
tienen que decir las dos que no.

---

## 7. Cuando se equivoca

**Entendió mal una frase.** Todavía no se guardó nada: descartá en la pantalla de
confirmación y volvé a escribir. La versión más simple casi siempre funciona: un verbo
conocido, y las cosas separadas con "y".

**Y por eso vale mirar la confirmación antes de tocar el botón**, porque hoy es la única
oportunidad. Una comida, un entrenamiento o un pesaje ya confirmados **no se pueden editar ni
borrar desde la app**: no hay botón para eso en **Comidas**, **Entrenamientos** ni **Métricas
Corporales**. Las dos excepciones son la **Despensa** —donde sí se ajusta la cantidad de
cualquier ítem desde la grilla— y los **Análisis de Sangre**, que se pueden borrar.

No es tan grave como suena, y conviene saber por qué: un dato de más no rompe nada. Las señales
pesan por repetición y se van desvaneciendo con el tiempo, así que una cena duplicada o un peso
mal tipeado se diluye en unos días. Lo que no querés es confirmar sistemáticamente cosas mal
entendidas: eso sí le enseña algo falso.

**No se puede corregir a medias en la confirmación.** Si de tres cosas entendió dos bien y una
mal, no hay forma de arreglar solo esa: se descarta todo y se reescribe. Es una limitación
conocida, no un botón que falte encontrar.

**Insiste con algo que no querés.** En orden de menos a más definitivo:

1. *"Ahora no"* → tres días de silencio sobre ese tema.
2. *"No es para nosotros"* → lo saca de la lista, pero caduca en unas semanas.
3. *"No es para nosotros"* + el motivo con el nombre del alimento → lo saca **y** le enseña.
4. **Perfil → Restricciones alimentarias / Actividades imposibles** → nunca más, sin
   vencimiento.

Si insiste después del 2, casi siempre es porque el "no" caducó o porque la tarjeta habla de
otro sujeto que se parece. El paso 4 es el que corta de verdad.

**Aprendió algo que no es cierto.** Se borra. Es la sección que sigue.

---

## 8. Ver lo que aprendió, y desdecirlo

En **Perfil** hay dos bloques que se parecen y que **no son lo mismo**, y entender la
diferencia es lo que hace que el resto de la pantalla sirva:

- **"Lo que nos dijiste"** — lo que declaraste vos: restricciones alimentarias, actividades
  imposibles, gustos que escribiste. Se corrige **diciendo otra cosa**: cambiás el texto y
  guardás.
- **"Lo que GaiaPulse fue aprendiendo"** — lo que la app dedujo sola de lo que registrás y de
  cómo respondés las tarjetas. Se corrige **olvidándolo**.

El segundo bloque muestra, en cada línea, **cuántas veces lo vio y cuándo fue la última**.
Están esos números porque sin ellos la lista sería un montón de afirmaciones: *"no te gusta
el brócoli"* invita a discutir, *"lo vimos dos veces, la última hace once días"* invita a
decidir. Al lado de cada línea hay un **"Olvidalo"**.

Dos cosas que hay que entender para que el gesto sirva:

**Olvidar no es prohibir.** Se borran los registros que respaldan esa línea, no la conducta.
Si volvés a comer eso tres veces, se vuelve a aprender. Para descartar algo definitivamente
están las restricciones alimentarias y las actividades imposibles, que no vencen.

**La lista es de cada uno.** Olvidar lo tuyo no toca lo del otro, aunque compartan la casa y
la despensa. Lo que la app aprende es siempre de una persona; lo único compartido son la
despensa y la lista de compras.

Y una tercera, más chica: los *"Patrones por categoría"* —esas conclusiones sobre familias
enteras de alimentos— **no se guardan solos**. Salen de las líneas que los respaldan, así que
se van cuando esas líneas se van. No tienen botón propio y no hace falta.

---

## 9. Trucos

**Los cuatro atajos del Inicio.** Debajo del botón grande hay cuatro accesos —Comida,
Entrenamiento, Peso, Despensa— que abren la captura con la frase ya empezada y el cursor al
final. Son el mismo camino, más rápido. Un detalle: **el arranque que dejan escrito está en
inglés** (`We had …`, `I did …`). Borralo y escribí en castellano, o dejalo y seguí en
inglés: las dos formas se entienden igual.

**Se puede dictar en vez de escribir.** En la pantalla de captura hay un micrófono: grabás,
la app transcribe y de ahí sigue el mismo camino de siempre, con la misma pantalla de
confirmación. Necesita estar configurado del lado del servidor; si no lo está, te lo dice y te
deja seguir escribiendo. Es la forma más cómoda de cargar una cena mientras se levanta la mesa.

**Modo oscuro.** Está en **Perfil → Apariencia**. Se acuerda de tu elección y se aplica antes
de que la página se dibuje, así que no hay parpadeo blanco. Si no elegís nada, sigue lo que
tenga configurado el sistema.

**La despensa cambia lo que te sugiere para comer.** No es solo una lista de qué hay: el motor
la mira para proponer comidas con lo que ya tenés. Una despensa cargada hace que las
sugerencias de comida sean concretas ("hay avena y huevos") en lugar de genéricas. Y la lista
de compras mira cuatro cosas distintas: lo que está en cero, lo que está bajo, lo que
**siempre compran y hoy no está**, y lo que suelen comprar junto con algo que compraron.

**El "Actualizar" de Sugerencias.** Si querés ver qué te propondría *ahora* sin esperar a las
7:40 o las 18:40, el botón de arriba a la derecha en **Sugerencias** vuelve a correr el motor.
Sirve después de registrar varias cosas de golpe. Con un límite que conviene saber: recalcula
**tus** sugerencias, no las de la casa — las tarjetas *"Para la casa"* se arman solo en las dos
corridas del día.

**Los avisos viven adentro de la app.** No hay push ni mail: están en **Alertas**. Si algo
importa y no entrás por unos días, va a estar esperando.

**Un renglón, todos los días.** Es el único hábito que hace falta. La app está armada para
que un dato por día alcance: dos semanas de un renglón diario le enseñan más que un domingo
cargando todo el mes de memoria — entre otras cosas porque la hora que guarda es la de cuando
lo escribís.
