# Tutorial de GaiaPulse

Guia completa, practica y amigable para aprender a usar GaiaPulse y sacarle provecho a todas sus secciones.

GaiaPulse es una app de bienestar pensada para un hogar compartido. Sirve para registrar comidas, entrenamientos, metricas corporales, despensa, analisis de sangre, datos de wearables, sugerencias personalizadas e historial. La idea central es simple: la casa comparte algunas cosas, como la despensa o una cena, pero cada persona conserva sus propios datos, preferencias, objetivos y progreso.

Este tutorial esta escrito para usuarios finales. No hace falta saber programar, entender bases de datos ni conocer la arquitectura de la app. Si podes contar que comiste, que entrenaste o que compraste, GaiaPulse puede ayudarte a ordenarlo.

---

## 1. La idea en una frase

GaiaPulse funciona como un diario inteligente de bienestar para dos personas que viven juntas.

En vez de cargar todo en formularios largos, muchas acciones se pueden registrar desde **Capture**, escribiendo o diciendo algo en lenguaje natural:

> "Comimos pollo con arroz para la cena"

> "Diego corrio 30 minutos y Rocio hizo yoga"

> "Compre 2 kg de pollo, arroz y tomates"

> "Mi peso hoy es 74.5 kg"

La app interpreta el texto, muestra una vista previa y recien guarda cuando confirmas. Esa confirmacion es importante: GaiaPulse no guarda automaticamente lo que cree haber entendido.

---

## 2. Como empezar

### 2.1. Iniciar sesion

Entrá a GaiaPulse desde el navegador. Si estas usando el entorno local, normalmente sera:

```text
http://localhost:8000
```

En la pantalla de login vas a ver los campos de email y contraseña. Si estas usando los datos de ejemplo del proyecto, las credenciales son:

| Persona | Email | Contraseña |
|---|---|---|
| Diego | `diego@gaiapulse.app` | `diego123` |
| Rocio | `rocio@gaiapulse.app` | `rocio123` |

Tambien podes marcar **Remember me for 30 days** para mantener la sesion iniciada por mas tiempo.

### 2.2. Completar el onboarding

La primera vez que entres, GaiaPulse puede mostrar un onboarding. Es una configuracion inicial corta para personalizar recomendaciones y metricas.

Vas a completar:

- Año de nacimiento y sexo, si queres informarlo.
- Altura, peso actual y peso objetivo.
- Nivel de actividad: sedentario, ligero, moderado, activo o muy activo.
- Restricciones o alergias alimentarias, separadas por comas.

No hace falta que sea perfecto desde el primer dia. Despues podes ajustar parte de esta informacion desde **Profile**.

### 2.3. Entender el modelo de hogar

GaiaPulse esta pensada para una casa con mas de una persona.

Esto significa:

- La **despensa** es compartida: si alguien compra arroz, la casa tiene arroz.
- Las **comidas** pueden ser compartidas, pero cada persona puede haber comido cosas o porciones distintas.
- Los **entrenamientos** pueden registrarse como una sesion compartida, pero los ejercicios y cargas quedan separados por persona.
- Las **metricas corporales**, preferencias y objetivos son individuales.

Un ejemplo:

> "Rocio comio ensalada y Diego comio pollo con arroz"

GaiaPulse intenta guardar una comida del hogar, pero separando que comio cada persona.

---

## 3. Navegacion general

En escritorio, el menu principal aparece en la barra lateral. En celular, se abre desde el boton de menu.

Las secciones principales son:

| Seccion | Para que sirve |
|---|---|
| **Home** | Ver resumen rapido del dia, alertas, acciones frecuentes y sugerencias. |
| **Capture** | Registrar casi todo escribiendo o hablando en lenguaje natural. |
| **Meals** | Revisar, filtrar, editar y eliminar comidas. |
| **Workouts** | Revisar, filtrar, editar y eliminar entrenamientos. |
| **Pantry** | Ver stock, buscar alimentos, filtrar por categoria y ajustar cantidades. |
| **Body Metrics** | Ver peso, grasa corporal, cintura y sueño por persona. |
| **Blood Analysis** | Subir analisis de laboratorio y revisar biomarcadores. |
| **Wearables** | Importar datos desde Apple Health o Samsung Health. |
| **Suggestions** | Ver recomendaciones personalizadas y dar feedback. |
| **Dashboard** | Analizar tendencias, graficos, metas y actividad. |
| **History** | Revisar registros historicos por tipo y persona. |
| **Notifications** | Leer alertas, recordatorios y avisos. |
| **Profile** | Editar datos personales, restricciones y preferencias. |

Tambien hay modo claro/oscuro desde la barra lateral o la barra superior movil.

---

## 4. Home: tu tablero de inicio

**Home** es la pantalla para responder rapido: "Como viene mi dia?"

Vas a encontrar:

- Saludo personalizado y fecha.
- Alertas de despensa baja.
- Acciones rapidas para registrar comida, entrenamiento, peso o compra.
- Insights o observaciones relevantes.
- Comidas y entrenamientos de hoy.
- Peso registrado hoy, si existe.
- Sugerencias pendientes.
- Resumen de notificaciones sin leer.

### Como usar Home en la practica

Pensalo como tu centro de control diario.

Por la mañana:

1. Revisá si hay recordatorios o alertas.
2. Registrá peso o sueño si corresponde.
3. Mirá si la despensa tiene algo bajo.

Durante el dia:

1. Usá las acciones rapidas para registrar comidas o compras.
2. Mirá si ya cargaste entrenamientos.

Al final del dia:

1. Confirmá que las comidas y entrenamientos aparecen.
2. Revisá sugerencias o notificaciones.

---

## 5. Capture: la forma mas rapida de cargar datos

**Capture** es la pantalla mas importante de GaiaPulse. Sirve para contarle a la app que paso, sin navegar por formularios especificos.

La pantalla tiene:

- Un cuadro de texto para escribir.
- Atajos rapidos: comida, entrenamiento, compra, peso.
- Busqueda de alimentos en la base de datos.
- Boton de microfono para registrar por voz, si esta configurado.
- Boton **Parse & Preview** para interpretar.
- Vista previa con **Confirm & Save** o **Discard**.
- Accesos a capturas recientes para repetir registros.

### 5.1. Flujo basico

1. Entra a **Capture**.
2. Escribi que paso.
3. Presiona **Parse & Preview**.
4. Lee la vista previa.
5. Si esta bien, presiona **Confirm & Save**.
6. Si esta mal, presiona **Discard** y reformula el texto.

La regla de oro:

> Si no confirmas, no se guarda.

### 5.2. Ejemplos para registrar comidas

Podes escribir frases simples:

```text
Comimos pasta para la cena
```

```text
Rocio almorzo ensalada con atun y Diego comio arroz con pollo
```

```text
Desayune cafe con leche y dos tostadas
```

```text
Diego comio 200 g de pollo y Rocio 150 g de arroz con verduras
```

Consejos:

- Menciona a Diego o Rocio si la comida no fue igual para ambos.
- Inclui cantidades cuando las tengas.
- Usa palabras como desayuno, almuerzo, merienda, cena o snack para que la app clasifique mejor.
- Si no sabes cantidades exactas, igual registra. Es mejor un registro aproximado que ninguno.

### 5.3. Ejemplos para entrenamientos

```text
Hice 30 minutos de running esta mañana
```

```text
Diego hizo gimnasio 45 minutos, pecho y triceps
```

```text
Rocio hizo yoga 40 minutos
```

```text
Entrenamos juntos: sentadillas 4x10 con 60 kg y caminata 20 minutos
```

Si queres que el dashboard de musculos entrenados sea mas util, intenta mencionar ejercicios concretos: sentadillas, press banca, remo, peso muerto, abdominales, caminata, bici, correr, yoga.

### 5.4. Ejemplos para despensa

```text
Compre 2 kg de pollo, 1 bolsa de arroz y tomates
```

```text
Agregue 6 huevos y 1 litro de leche a la despensa
```

```text
Usamos 500 g de arroz
```

```text
Se termino la leche
```

Capture puede registrar compras o consumos. Si queres corregir un valor exacto, tambien podes hacerlo desde **Pantry** con **Adjust Stock**.

### 5.5. Ejemplos para metricas corporales

```text
Mi peso hoy es 74.5 kg
```

```text
Rocio pesa 61.2 kg
```

```text
Dormi 7.5 horas
```

```text
Mi cintura hoy mide 82 cm y grasa corporal 18%
```

Cuanto mas seguido cargues estas metricas, mas utiles seran los graficos del Dashboard.

### 5.6. Ejemplos para preferencias

Las preferencias ayudan a que las sugerencias sean mejores.

```text
No me gusta el brocoli
```

```text
Rocio evita la lactosa
```

```text
Me encanta correr
```

```text
No puedo hacer natacion
```

Cuando GaiaPulse aprende que algo no sirve para vos, puede evitar sugerirlo en el futuro.

### 5.7. Usar voz

Si la transcripcion por voz esta configurada, podes tocar el microfono, hablar y detener la grabacion. La app transcribe el audio y lo pasa por el mismo flujo de vista previa.

Buenas practicas:

- Habla en frases cortas.
- Menciona nombres propios cuando haya mas de una persona.
- Revisa siempre la vista previa antes de confirmar.

---

## 6. Meals: comidas del hogar

**Meals** muestra el registro de comidas del hogar.

Desde esta seccion podes:

- Ver comidas registradas.
- Filtrar por fecha.
- Filtrar por persona: todos, Diego o Rocio.
- Entrar al detalle de una comida.
- Editar o eliminar registros.
- Cargar una comida nueva desde **Log Meal**, que te lleva a Capture.

### Como leer una comida

Una comida puede tener varios participantes. Dentro de cada participante aparecen sus alimentos y cantidades.

Ejemplo conceptual:

```text
Cena · 21:00
Diego: pollo, arroz
Rocio: ensalada, atun
```

Esto permite que una comida compartida no mezcle los datos individuales.

### Cuando usar Meals

Usala para revisar lo ya cargado, detectar errores y corregirlos. Capture es ideal para cargar rapido; Meals es ideal para auditar.

---

## 7. Workouts: entrenamientos y actividad fisica

**Workouts** agrupa los entrenamientos registrados.

Podes:

- Ver sesiones de entrenamiento.
- Filtrar por fecha.
- Filtrar por persona.
- Revisar duracion, tipo de entrenamiento, ejercicios, series, repeticiones, carga, distancia y esfuerzo percibido.
- Editar o eliminar entrenamientos.
- Crear uno nuevo desde **Log Workout**, usando Capture.

### Como registrar mejor un entrenamiento

Para que los graficos sean utiles, intenta incluir:

- Tipo de actividad: gimnasio, running, bici, yoga, caminata.
- Duracion: 30 minutos, 1 hora, etc.
- Ejercicios, si corresponde.
- Series y repeticiones: 4x10, 3x12.
- Carga: 60 kg, 20 kg.
- Distancia: 5 km.
- Intensidad o esfuerzo, si lo usas.

Ejemplo:

```text
Diego hizo gimnasio 50 minutos: press banca 4x8 con 70 kg, remo 3x10 con 50 kg y abdominales
```

---

## 8. Pantry: despensa compartida

**Pantry** muestra el stock actual de alimentos del hogar.

Desde ahi podes:

- Buscar productos.
- Filtrar por stock bajo.
- Filtrar por categoria: vegetales, frutas, proteinas, cereales, lacteos, grasas, bebidas, procesados y otros.
- Ver cantidades actuales.
- Ver umbrales de bajo stock.
- Ajustar stock manualmente.
- Agregar compras desde Capture.

### 8.1. Como leer las tarjetas

Cada tarjeta muestra:

- Nombre del alimento.
- Categoria.
- Cantidad disponible.
- Unidad.
- Indicador **Low** si esta por debajo del umbral.
- Boton **Adjust Stock**.

### 8.2. Ajustar stock

En **Adjust Stock** podes elegir:

- **Set**: fijar la cantidad exacta.
- **Add**: sumar una compra.
- **Use**: descontar consumo.

Ejemplos:

- Si hay 3 huevos y compraste 6, usa **Add** con 6.
- Si contaste la despensa y hay exactamente 4 huevos, usa **Set** con 4.
- Si usaste 2 huevos, usa **Use** con 2.

### 8.3. Estrategia recomendada

No intentes que la despensa sea perfecta desde el dia uno. Empeza por los alimentos que mas importan:

- Proteinas principales.
- Arroz, avena, pasta, pan u otros basicos.
- Verduras y frutas frecuentes.
- Huevos, lacteos o bebidas.

La despensa se vuelve mas valiosa cuando se combina con Suggestions: la app puede sugerir comidas o compras segun lo que falta.

---

## 9. Body Metrics: metricas corporales

**Body Metrics** muestra metricas por persona.

Puede incluir:

- Peso.
- Porcentaje de grasa corporal.
- Cintura.
- Horas de sueño.

Cada persona tiene su propia pestaña. Esto evita mezclar datos entre miembros del hogar.

### Como usar esta seccion

1. Elegi la persona.
2. Mira las tarjetas de ultima medicion.
3. Revisa el historial.
4. Edita o elimina registros si hay errores.
5. Usa el enlace al Dashboard para ver tendencias.

### Frecuencia sugerida

- Peso: 2 a 7 veces por semana, segun tu preferencia.
- Cintura: 1 vez por semana.
- Grasa corporal: cuando tengas una medicion confiable.
- Sueño: idealmente diario, si queres cruzarlo con energia y entrenamiento.

No hace falta obsesionarse con cada numero. Lo mas util son las tendencias.

---

## 10. Blood Analysis: analisis de sangre

**Blood Analysis** permite subir un informe de laboratorio para extraer biomarcadores y generar observaciones orientadas al bienestar.

Formatos soportados desde la interfaz:

- PDF.
- JPG.
- PNG.
- WEBP.
- HEIC.

El limite indicado por la app es 20 MB.

### Flujo de uso

1. Entra a **Blood Analysis**.
2. Selecciona el archivo del laboratorio.
3. Presiona **Upload and analyze**.
4. Abre el analisis generado.
5. Revisa resumen, marcadores anormales, marcadores normales, texto extraido y tendencias.

### Como interpretar la pantalla de detalle

Vas a encontrar:

- **AI Summary**: resumen interpretativo, si esta disponible.
- **Abnormal markers**: valores fuera de rango o marcados como relevantes.
- **Normal markers**: valores detectados dentro de rango.
- **Raw text**: texto extraido del informe.
- **Tendencias**: graficos si hay suficientes analisis del mismo marcador.

Importante: GaiaPulse no reemplaza a un medico. Usa esta seccion para organizar informacion y detectar temas para conversar con un profesional.

---

## 11. Wearables: importar datos de salud

**Wearables** sirve para importar datos de Apple Health o Samsung Health.

### 11.1. Apple Health

La pantalla indica este recorrido:

1. Abrir la app Health en iPhone.
2. Entrar a tu perfil.
3. Elegir **Export All Health Data**.
4. Compartir o guardar el archivo ZIP.
5. Subir ese ZIP en GaiaPulse.

### 11.2. Samsung Health

La pantalla indica este recorrido:

1. Abrir Samsung Health.
2. Ir a Profile.
3. Entrar a Settings.
4. Elegir **Download personal data**.
5. Descargar Health data.
6. Subir el ZIP en GaiaPulse.

### 11.3. Que datos podes ver

Si la importacion trae datos compatibles, GaiaPulse puede mostrar metricas recientes como:

- Pasos.
- Sueño.
- Frecuencia cardiaca.
- HRV.
- Calorias.
- Minutos activos.
- Peso.

Tambien vas a ver un historial de sincronizaciones con proveedor, estado, fecha y cantidad de registros importados.

Si una importacion salio mal, revisa que el archivo sea ZIP y que corresponda al proveedor elegido.

---

## 12. Suggestions: recomendaciones que aprenden

**Suggestions** muestra recomendaciones personalizadas basadas en habitos, preferencias, despensa y actividad.

Pueden aparecer sugerencias de:

- Comidas.
- Actividad fisica.
- Despensa o compras.
- Habitos.
- Recuperacion.
- Recordatorios.

Cada sugerencia puede mostrar:

- Categoria.
- Persona objetivo, si aplica.
- Titulo y texto.
- Prioridad visual.
- Explicacion en **Why this suggestion?**

### Como dar feedback

Tenes tres acciones principales:

- **Accept**: te sirve o queres seguirla.
- **Dismiss**: no ahora, pero no necesariamente esta mal.
- **Not for us**: no corresponde para ustedes.

Este feedback es muy importante. GaiaPulse usa esas respuestas para aprender.

Ejemplo:

- Si rechazas varias veces sugerencias de natacion, la app aprende que no conviene insistir.
- Si aceptas comidas con pollo y verduras, puede reforzar ese estilo.
- Si marcas restricciones en Profile, puede filtrar sugerencias incompatibles.

### Learned Preferences

En la parte inferior pueden aparecer preferencias aprendidas. Ahi ves señales como likes, dislikes, avoid o impossible asociadas a comidas o actividades.

---

## 13. Dashboard: convertir registros en tendencias

**Dashboard** es donde los datos empiezan a contar una historia.

La vista esta enfocada en los ultimos 30 dias y permite elegir persona. Incluye:

- Peso actual y cambio en 30 dias.
- Entrenamientos de la semana.
- Dias activos.
- Items de despensa.
- Insights.
- Rachas de comidas o entrenamientos.
- Metas semanales.
- Resumen de hoy.
- Nutricion diaria.
- Ultimos biomarcadores.
- Alertas de despensa.
- Grafico de tendencia de peso.
- Entrenamientos por semana.
- Grupos musculares entrenados.
- Comidas por tipo.
- Relacion peso vs frecuencia de entrenamientos.
- Calendario de actividad.

### Como leerlo sin abrumarte

No intentes mirar todos los graficos todos los dias.

Una lectura simple:

1. Mira las tarjetas superiores: peso, entrenamientos, dias activos, despensa.
2. Revisa metas semanales.
3. Mira el resumen de hoy.
4. Una o dos veces por semana, mira tendencias: peso, entrenamientos, grupos musculares y comidas.

### Preguntas utiles para hacerte

- Estoy registrando comidas con suficiente constancia?
- Entrene esta semana lo que queria entrenar?
- Hay grupos musculares que nunca aparecen?
- Mi peso se mueve en la direccion esperada?
- La despensa se queda sin cosas clave?
- Mis datos de sueño o wearable explican energia baja o menor actividad?

El Dashboard es tan bueno como los datos que le das. No necesita perfeccion, necesita constancia.

---

## 14. History: el archivo completo

**History** es una vista historica por pestañas.

Podes revisar:

- Meals.
- Workouts.
- Body Metrics.
- Pantry.

Tambien podes filtrar por persona cuando corresponde. En despensa no hay filtro por persona porque el stock es del hogar.

### Diferencia entre History y las secciones individuales

- Usa **Meals**, **Workouts**, **Body Metrics** y **Pantry** para gestionar cada area con mas detalle.
- Usa **History** para una mirada cronologica y rapida de todo lo registrado.

History es especialmente util cuando pensas: "Se que cargue algo, pero no recuerdo donde quedo".

---

## 15. Notifications: avisos y recordatorios

**Notifications** centraliza avisos de GaiaPulse.

Puede haber categorias como:

- Low stock.
- Inactivity.
- Metric reminder.
- Suggestion.
- Trend.
- Info.

Desde esta pantalla podes:

- Filtrar por categoria.
- Marcar una notificacion como leida.
- Descartarla.
- Marcar todas como leidas.

### Como aprovecharlas

No las trates como ruido. Son señales para actuar:

- Bajo stock: revisar Pantry o hacer compra.
- Inactividad: registrar movimiento o planear entrenamiento.
- Recordatorio de metrica: cargar peso, sueño u otra medida.
- Tendencia: revisar Dashboard.
- Sugerencia: entrar a Suggestions y dar feedback.

---

## 16. Profile: tu configuracion personal

**Profile** permite editar datos personales y preferencias.

Podes modificar:

- Nombre.
- Altura.
- Peso objetivo.
- Nivel de actividad.
- Restricciones alimentarias.
- Actividades imposibles.

Tambien podes ver:

- Preferencias aprendidas.
- Miembros del hogar.

### Por que Profile importa tanto

Profile alimenta el motor de recomendaciones. Si marcas que una actividad es imposible, GaiaPulse deberia evitar sugerirla. Si agregas restricciones alimentarias, las sugerencias de comida deberian respetarlas.

Ejemplos:

```text
lactosa, gluten, mani
```

```text
natacion, correr, escalada
```

Usa restricciones solo para cosas reales. Si pones demasiadas restricciones por comodidad, las recomendaciones pueden volverse pobres.

---

## 17. Rutina recomendada para sacarle provecho

### Todos los dias, 3 a 5 minutos

1. Abrir **Home**.
2. Registrar comidas desde **Capture**.
3. Registrar entrenamiento o actividad, si hubo.
4. Registrar peso o sueño si forma parte de tu rutina.
5. Revisar alertas de despensa.

### Una vez por semana, 10 minutos

1. Abrir **Dashboard**.
2. Revisar peso, entrenamientos y dias activos.
3. Mirar comidas por tipo y nutricion diaria.
4. Revisar grupos musculares entrenados.
5. Responder sugerencias pendientes.
6. Corregir stock bajo en **Pantry**.

### Una vez por mes

1. Revisar tendencias largas.
2. Ajustar objetivos en **Profile**.
3. Importar datos de wearables si usas Apple Health o Samsung Health.
4. Subir analisis de laboratorio si tenes nuevos.
5. Limpiar o corregir registros erroneos.

---

## 18. Mini desafios para aprender jugando

Si estas empezando, proba estos desafios.

### Dia 1: Primer registro completo

- Carga una comida.
- Carga una compra en Pantry.
- Carga tu peso.
- Mira Home y confirma que aparecen datos del dia.

### Dia 2: Separar datos por persona

Registra algo como:

```text
Rocio almorzo ensalada con atun y Diego comio pollo con arroz
```

Despues entra a **Meals** y verifica que cada persona tenga sus alimentos.

### Dia 3: Entrenamiento con detalle

Registra:

```text
Diego hizo gimnasio 45 minutos: sentadillas 4x10 con 60 kg y press banca 3x8 con 70 kg
```

Despues mira **Workouts** y, mas tarde, **Dashboard**.

### Dia 4: Feedback inteligente

Entra a **Suggestions** y responde al menos tres sugerencias:

- Una con Accept.
- Una con Dismiss.
- Una con Not for us.

Despues revisa **Learned Preferences**.

### Dia 5: Despensa util

Agrega 5 alimentos basicos a Pantry y ajusta uno como bajo stock. Mira si Home o Dashboard muestran alertas.

---

## 19. Buenas practicas de registro

### Escribi como persona, pero con pistas claras

Mejor:

```text
Rocio ceno ensalada con pollo y Diego ceno pasta con salsa
```

Menos claro:

```text
Comida normal
```

### Nombra personas cuando importe

Si ambos hicieron o comieron lo mismo, "comimos" suele alcanzar. Si fue distinto, usa nombres.

### Inclui cantidades cuando puedas

No hace falta precision de laboratorio, pero ayuda mucho:

```text
200 g de pollo
1 taza de arroz
2 huevos
30 minutos
5 km
```

### Revisa antes de confirmar

La vista previa existe para eso. Si GaiaPulse entendio mal, descarta y proba una frase mas concreta.

### Corrige errores pronto

Si cargaste mal una comida o entrenamiento, anda a la seccion correspondiente y editalo o eliminalo. Los dashboards y sugerencias dependen de esos datos.

### Dale feedback a las sugerencias

Aceptar, descartar o rechazar no es solo ordenar la pantalla: es enseñarle a GaiaPulse que sirve para ustedes.

---

## 20. Problemas comunes y como resolverlos

### "La app no entendio mi frase"

Proba reformular con estructura simple:

```text
[Persona] comio [alimento] para [comida]
```

```text
[Persona] hizo [actividad] durante [duracion]
```

```text
Compre [cantidad] de [producto]
```

### "Me guardo algo en la persona equivocada"

Usa nombres explicitos:

```text
Diego comio arroz. Rocio comio ensalada.
```

Despues revisa el registro desde Meals o Workouts.

### "La despensa quedo con una cantidad rara"

Entra a **Pantry**, busca el item y usa **Adjust Stock** con **Set** para fijar el valor correcto.

### "No veo graficos en Dashboard"

Probablemente faltan datos. Carga algunos registros durante varios dias:

- Peso para tendencia de peso.
- Entrenamientos para frecuencia y grupos musculares.
- Comidas para tipos de comida y nutricion.
- Pantry para alertas de stock.

### "Las sugerencias no me sirven"

Tres acciones ayudan:

- Completa restricciones y actividades imposibles en Profile.
- Usa **Not for us** cuando una sugerencia no corresponda.
- Registra comidas, entrenamientos y despensa con mas constancia.

### "La voz no funciona"

La entrada por voz requiere configuracion de transcripcion. Si no esta disponible, usa texto. El flujo de interpretacion es el mismo.

---

## 21. Mapa mental final

Si queres recordar GaiaPulse en pocas palabras:

- **Capture** es para cargar rapido.
- **Home** es para mirar el dia.
- **Meals** y **Workouts** son para revisar detalles.
- **Pantry** es para saber que hay en casa.
- **Body Metrics**, **Wearables** y **Blood Analysis** enriquecen tu contexto de salud.
- **Suggestions** aprende de tus decisiones.
- **Dashboard** convierte registros en tendencias.
- **History** te ayuda a encontrar el pasado.
- **Profile** le dice a GaiaPulse quien sos y que evitar.

El mejor uso de GaiaPulse no es cargar datos perfectos. Es construir, dia a dia, una memoria util del hogar: que comen, como entrenan, que tienen disponible, como evolucionan y que decisiones pequeñas pueden mejorar la semana.

