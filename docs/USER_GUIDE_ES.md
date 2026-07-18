# Guía Rápida del Usuario — DT Beach Control de Suministro

## Ingresar al sistema

Vaya a la dirección web que le indique su administrador e ingrese con el
usuario y contraseña que le fueron entregados. Al ingresar, el sistema lo
llevará automáticamente a su panel según su rol (Compras, China, Finanzas,
Almacén, Obra o Dirección).

## Compras (Markeris)

- **Órdenes de compra:** menú "Compras". Cada orden muestra el proveedor,
  el documento de origen (PI/cotización) vinculado, y si el documento
  fuente trae un sello de "recibido en full" sin verificar — esto es solo
  una alerta, nunca significa que el material ya fue recibido físicamente.
- **Saldo abierto:** en el detalle de cada orden se muestra cuánto de la
  cantidad original ya fue asignada al embarque actual y cuánto queda
  pendiente para un embarque futuro.

## China / Origen (Harrison, Edison)

- **Embarques:** menú "Embarques" muestra todos los contenedores en
  proceso. Al abrir un embarque verá dos paneles lado a lado: el
  **Resumen Oficial** (tal como aparece en el BL, nunca se modifica) y el
  **Manifiesto Operativo Interno** (el detalle completo de lo que
  realmente va o llegó en el contenedor).

## Finanzas (Lucía)

- **Costos:** menú "Costos" muestra las versiones de costo de importación
  por embarque, distinguiendo claramente entre "Provisional" y "Final".

## Recepción y Almacén (Manuel, Óscar)

1. Abra "Recepción" y seleccione el contenedor que está descargando.
2. Para cada línea, registre la **cantidad recibida**, la **cantidad
   dañada**, la **cantidad faltante**, y si corresponde, marque el tipo
   de excepción (faltante, sobrante, daño, modelo/color/orientación
   incorrecta, componente faltante, falla de empaque).
3. Al guardar, el sistema automáticamente:
   - registra el inventario disponible (la cantidad buena),
   - pone en cuarentena la cantidad dañada,
   - crea una discrepancia visible si hubo alguna excepción.
4. Use "Almacén → Ubicaciones" para ver las existencias actuales por
   ubicación — estas siempre se calculan a partir del historial de
   movimientos, nunca se editan directamente.

## Obra (Miguel)

1. Abra "Solicitudes → Nueva solicitud".
2. Indique el proyecto, edificio (si aplica), artículo, cantidad
   necesaria y fecha requerida.
3. Podrá seguir el estado de su solicitud (aprobada, reservada,
   despachada, entregada) desde "Solicitudes".

## Dirección / Gerencia (María Luisa, Harrison)

El panel ejecutivo muestra las variaciones oficial-vs-operativo sin
explicar y las discrepancias críticas abiertas en toda la organización —
es el primer lugar para revisar qué requiere decisión gerencial.

## Documentos (todos los roles)

- "Documentos → Cargar documento" para subir cualquier archivo
  (factura, lista de empaque, BL, foto, etc.). El sistema calcula una
  huella digital del archivo y le avisará si detecta que ya existe un
  documento idéntico.
- Cada documento conserva su archivo original sin cambios — cualquier
  corrección se guarda como una nueva versión, nunca se sobrescribe.

## Generar una instantánea (reporte HTML) de un embarque

Desde el detalle de un embarque, la opción de instantánea genera un
archivo HTML autónomo (sin dependencias externas) que puede compartirse
por correo o WhatsApp como archivo. El archivo indica claramente que es
una instantánea de un momento dado, no la fuente de verdad en vivo.

## ¿Problemas para entrar o usar el sistema?

Contacte a su administrador del sistema. Nunca comparta su contraseña ni
la escriba en documentos o mensajes.
