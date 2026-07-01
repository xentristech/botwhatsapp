"""
Catalogo de productos PLATIM — fuente de verdad.
65 productos en 19 categorias. Origen: LISTADO_PRODUCTOS_PLATIM.xlsx

Cada producto es un dict con las claves:
    codigo, categoria, nombre, descripcion, material, uso,
    tallas, colores, precio_publico, precio_mayoreo, marca, observaciones
"""

import unicodedata

PRODUCTOS = [
    # ── UNIFORMES ─────────────────────────────────────────────────────────
    {"codigo":"UNF-001","categoria":"Uniformes","nombre":"Camiseta tipo polo","descripcion":"Polo de trabajo","material":"Algodón/mezcla","uso":"Uso diario industrial","tallas":"S-3XL","colores":"Azul/Gris/Blanco","precio_publico":65000,"precio_mayoreo":52000,"marca":"—","observaciones":"Con o sin bordado"},
    {"codigo":"UNF-002","categoria":"Uniformes","nombre":"Camiseta manga corta","descripcion":"Camiseta uniforme","material":"Poliéster/algodón","uso":"Uso diario industrial","tallas":"S-3XL","colores":"Azul/Gris/Blanco","precio_publico":45000,"precio_mayoreo":36000,"marca":"—","observaciones":"Con impresión o logo"},
    {"codigo":"UNF-003","categoria":"Uniformes","nombre":"Camisa manga larga","descripcion":"Camisa uniforme","material":"Algodón/mezcla","uso":"Protección y presentación","tallas":"S-3XL","colores":"Azul/Gris","precio_publico":78000,"precio_mayoreo":62000,"marca":"—","observaciones":"Cierre botones"},
    {"codigo":"UNF-004","categoria":"Uniformes","nombre":"Conjunto camisa+pantalón","descripcion":"Uniforme completo","material":"Algodón/mezcla","uso":"Atención industrial","tallas":"S-3XL","colores":"Acorde a empresa","precio_publico":155000,"precio_mayoreo":123000,"marca":"—","observaciones":"Valor estimado total conjunto"},
    {"codigo":"UNF-005","categoria":"Uniformes","nombre":"Sudadera polerón","descripcion":"Sudadera tipo polerón","material":"Poliéster/algodón","uso":"Abrigo bodega/uso mixto","tallas":"S-3XL","colores":"Gris/Azul/Negro","precio_publico":105000,"precio_mayoreo":85000,"marca":"—","observaciones":"Cierre o sin cierre"},
    {"codigo":"UNF-006","categoria":"Uniformes","nombre":"Chaleco de trabajo","descripcion":"Chaleco laboral","material":"Tela de trabajo","uso":"Capa de uso en planta","tallas":"S-3XL","colores":"Azul/Gris","precio_publico":78000,"precio_mayoreo":62000,"marca":"—","observaciones":"Bolsillos frontales si aplica"},
    {"codigo":"UNF-007","categoria":"Uniformes","nombre":"Chaqueta de abrigo","descripcion":"Chaqueta tipo bomber/parka","material":"Tela impermeable o dril","uso":"Frío/lluvia","tallas":"S-3XL","colores":"Negro/Gris/Azul","precio_publico":165000,"precio_mayoreo":132000,"marca":"—","observaciones":"Impermeable si aplica"},
    # ── BUZOS/OVEROLES ────────────────────────────────────────────────────
    {"codigo":"BUZ-001","categoria":"Buzos/Overoles","nombre":"Buzo con cierre","descripcion":"Enterizo/overol con cierre","material":"Algodón o dril","uso":"Trabajo de campo","tallas":"S-3XL","colores":"Azul/Gris","precio_publico":135000,"precio_mayoreo":108000,"marca":"—","observaciones":"Cierre frontal"},
    {"codigo":"BUZ-002","categoria":"Buzos/Overoles","nombre":"Buzo con botones","descripcion":"Enterizo/overol con botones","material":"Algodón/mezcla","uso":"Trabajo industrial","tallas":"S-3XL","colores":"Azul/Gris","precio_publico":125000,"precio_mayoreo":100000,"marca":"—","observaciones":"Botonera frontal"},
    {"codigo":"BUZ-003","categoria":"Buzos/Overoles","nombre":"Buzo tipo peto","descripcion":"Overol con peto","material":"Tela de trabajo","uso":"Alta capacidad","tallas":"S-3XL","colores":"Azul/Gris","precio_publico":150000,"precio_mayoreo":120000,"marca":"—","observaciones":"Incluye peto/bolsillos"},
    {"codigo":"BUZ-004","categoria":"Buzos/Overoles","nombre":"Buzo multi-bolsillos","descripcion":"Overol cargo","material":"Tela de trabajo","uso":"Herramientas y uso intensivo","tallas":"S-3XL","colores":"Azul/Gris","precio_publico":165000,"precio_mayoreo":132000,"marca":"—","observaciones":"Bolsillos reforzados"},
    {"codigo":"BUZ-005","categoria":"Buzos/Overoles","nombre":"Buzo con refuerzo rodillas","descripcion":"Overol reforzado","material":"Algodón/dril reforzado","uso":"Mayor durabilidad","tallas":"S-3XL","colores":"Azul/Gris","precio_publico":175000,"precio_mayoreo":140000,"marca":"—","observaciones":"Refuerzo en rodillas/codos"},
    {"codigo":"BUZ-006","categoria":"Buzos/Overoles","nombre":"Buzo ignífugo","descripcion":"Enterizo ignífugo","material":"Ignífugo","uso":"Seguridad en procesos con fuego","tallas":"S-3XL","colores":"Color corporativo","precio_publico":320000,"precio_mayoreo":260000,"marca":"—","observaciones":"Según certificación"},
    {"codigo":"BUZ-007","categoria":"Buzos/Overoles","nombre":"Buzo antiflama/antiestático","descripcion":"Enterizo especial","material":"Antiestático/antiflama","uso":"Industria especial","tallas":"S-3XL","colores":"Color corporativo","precio_publico":340000,"precio_mayoreo":275000,"marca":"—","observaciones":"Solo si manejan línea"},
    # ── PANTALONES ────────────────────────────────────────────────────────
    {"codigo":"PAN-001","categoria":"Pantalones","nombre":"Pantalón industrial","descripcion":"Pantalón de trabajo","material":"Dril/algodón","uso":"Uso industrial general","tallas":"28-44","colores":"Azul/Gris/Negro","precio_publico":85000,"precio_mayoreo":68000,"marca":"—","observaciones":"Bolsillos laterales"},
    {"codigo":"PAN-002","categoria":"Pantalones","nombre":"Pantalón cargo","descripcion":"Pantalón con bolsillos cargo","material":"Dril/mezcla","uso":"Carga de herramientas","tallas":"28-44","colores":"Azul/Gris","precio_publico":98000,"precio_mayoreo":78000,"marca":"—","observaciones":"Bolsillos tipo cargo"},
    {"codigo":"PAN-003","categoria":"Pantalones","nombre":"Pantalón con rodilleras","descripcion":"Pantalón reforzado en rodillas","material":"Dril/tela reforzada","uso":"Alta exigencia física","tallas":"28-44","colores":"Azul/Gris","precio_publico":115000,"precio_mayoreo":92000,"marca":"—","observaciones":"Incluye bolsillos rodillera"},
    {"codigo":"PAN-004","categoria":"Pantalones","nombre":"Pantalón cintura ajustable","descripcion":"Pantalón regulable","material":"Tela de trabajo","uso":"Comodidad y ajuste","tallas":"28-44","colores":"Negro/Gris","precio_publico":93000,"precio_mayoreo":74000,"marca":"—","observaciones":"Elástico o reguladores"},
    {"codigo":"PAN-005","categoria":"Pantalones","nombre":"Pantalón cintura reforzada","descripcion":"Pantalón resistente","material":"Tela reforzada","uso":"Mayor resistencia","tallas":"28-44","colores":"Azul/Negro","precio_publico":110000,"precio_mayoreo":88000,"marca":"—","observaciones":"Costuras reforzadas"},
    # ── ALTA VISIBILIDAD ──────────────────────────────────────────────────
    {"codigo":"HV-001","categoria":"Alta visibilidad","nombre":"Pantalón alta visibilidad","descripcion":"Pantalón reflectante","material":"Tela alta visibilidad","uso":"Seguridad en planta/vía","tallas":"S-3XL/28-44","colores":"Naranja/Amarillo","precio_publico":165000,"precio_mayoreo":132000,"marca":"—","observaciones":"Cintas reflectivas"},
    {"codigo":"HV-002","categoria":"Alta visibilidad","nombre":"Chaleco alta visibilidad","descripcion":"Chaleco reflectante","material":"Tela HV","uso":"Señalización personal","tallas":"S-3XL","colores":"Naranja/Amarillo","precio_publico":98000,"precio_mayoreo":78000,"marca":"—","observaciones":"Con cintas reflectantes"},
    {"codigo":"HV-003","categoria":"Alta visibilidad","nombre":"Chaqueta reflectante","descripcion":"Casaca HV con cierre","material":"Tela HV","uso":"Protección y visibilidad","tallas":"S-3XL","colores":"Naranja/Amarillo","precio_publico":210000,"precio_mayoreo":168000,"marca":"—","observaciones":"Con cierre"},
    # ── SEGURIDAD COMPLEMENTOS ────────────────────────────────────────────
    {"codigo":"SEG-001","categoria":"Seguridad/Complementos","nombre":"Impermeable","descripcion":"Chaqueta impermeable","material":"PVC/tejido impermeable","uso":"Lluvia y exteriores","tallas":"S-3XL","colores":"Negro/Gris","precio_publico":155000,"precio_mayoreo":125000,"marca":"—","observaciones":"Costuras selladas si aplica"},
    {"codigo":"SEG-002","categoria":"Seguridad/Complementos","nombre":"Guantes de trabajo","descripcion":"Guantes protección general","material":"Cuero/nylon","uso":"Manipulación de materiales","tallas":"S-XL","colores":"Negro","precio_publico":25000,"precio_mayoreo":20000,"marca":"—","observaciones":"Varios tipos disponibles"},
    {"codigo":"SEG-003","categoria":"Seguridad/Complementos","nombre":"Casco de protección general","descripcion":"EPP cabeza general","material":"Según línea","uso":"Seguridad industrial","tallas":"Única","colores":"Varios","precio_publico":50000,"precio_mayoreo":40000,"marca":"—","observaciones":"Consultar disponibilidad"},
    # ── ACCESORIOS ────────────────────────────────────────────────────────
    {"codigo":"ACC-001","categoria":"Accesorios","nombre":"Personalización (bordado/logo)","descripcion":"Bordado o impresión de logos","material":"—","uso":"Identificación corporativa","tallas":"Según prenda","colores":"Según prenda","precio_publico":18000,"precio_mayoreo":14000,"marca":"—","observaciones":"Costo por prenda"},
    {"codigo":"ACC-002","categoria":"Accesorios","nombre":"Cinturón de trabajo","descripcion":"Cinturón laboral","material":"Cuero o similar","uso":"Soporte lumbar/herramientas","tallas":"Única","colores":"Negro","precio_publico":45000,"precio_mayoreo":36000,"marca":"—","observaciones":""},
    # ── EPP PROTECCIÓN CABEZA ─────────────────────────────────────────────
    {"codigo":"SST-001","categoria":"Protección de cabeza","nombre":"Casco de seguridad","descripcion":"Casco industrial ABS","material":"ABS","uso":"Impacto y caídas","tallas":"Única ajustable","colores":"Blanco/Amarillo/Naranja","precio_publico":85000,"precio_mayoreo":68000,"marca":"—","observaciones":"Con barbuquejo si aplica"},
    {"codigo":"SST-002","categoria":"Protección de cabeza","nombre":"Casco con protección facial","descripcion":"Casco + careta integrada","material":"ABS + policarbonato","uso":"Soldadura e impactos faciales","tallas":"Única","colores":"Blanco","precio_publico":240000,"precio_mayoreo":190000,"marca":"—","observaciones":"Según visor"},
    # ── EPP PROTECCIÓN OCULAR ─────────────────────────────────────────────
    {"codigo":"SST-003","categoria":"Protección ocular","nombre":"Gafas de seguridad","descripcion":"Gafas industriales antiimpacto","material":"Policarbonato","uso":"Antipartículas y proyecciones","tallas":"Única","colores":"Transparente/Ahumadas","precio_publico":35000,"precio_mayoreo":28000,"marca":"—","observaciones":"Con o sin ventilación"},
    {"codigo":"SST-004","categoria":"Protección ocular","nombre":"Careta de soldadura","descripcion":"Careta para soldadura/esmeril","material":"Policarbonato","uso":"Soldadura y esmeril","tallas":"Única","colores":"Negro","precio_publico":140000,"precio_mayoreo":112000,"marca":"—","observaciones":"Para esmeril/soldar"},
    # ── EPP RESPIRATORIA ──────────────────────────────────────────────────
    {"codigo":"SST-005","categoria":"Protección respiratoria","nombre":"Tapabocas desechable","descripcion":"Mascarilla quirúrgica/partículas","material":"Tela no tejida","uso":"Partículas y polvo","tallas":"Única","colores":"Blanco/Azul","precio_publico":4500,"precio_mayoreo":3500,"marca":"—","observaciones":"Tipo quirúrgico/partículas"},
    {"codigo":"SST-006","categoria":"Protección respiratoria","nombre":"Respirador media cara","descripcion":"Media cara reutilizable con filtros","material":"Plástico/metal","uso":"Partículas y vapores químicos","tallas":"Única","colores":"Negro","precio_publico":125000,"precio_mayoreo":99000,"marca":"—","observaciones":"Usar con filtros SST-007"},
    {"codigo":"SST-007","categoria":"Protección respiratoria","nombre":"Filtro para media cara","descripcion":"Filtro reemplazable carbón/HEPA","material":"Carbón activado/HEPA","uso":"Polvo, gases y vapores","tallas":"Única","colores":"—","precio_publico":98000,"precio_mayoreo":78000,"marca":"—","observaciones":"Definir por tipo de contaminante"},
    # ── EPP AUDITIVA ──────────────────────────────────────────────────────
    {"codigo":"SST-008","categoria":"Protección auditiva","nombre":"Tapones auditivos","descripcion":"Tapones para oídos","material":"Espuma o silicona","uso":"Reducción de ruido industrial","tallas":"Única","colores":"—","precio_publico":5000,"precio_mayoreo":4000,"marca":"—","observaciones":"Con o sin cordón"},
    {"codigo":"SST-009","categoria":"Protección auditiva","nombre":"Orejeras industriales","descripcion":"Protectores tipo copa","material":"Plástico + almohadillas","uso":"Alto ruido industrial","tallas":"Única ajustable","colores":"Negro/Gris","precio_publico":42000,"precio_mayoreo":34000,"marca":"—","observaciones":"Nivel de atenuación según modelo"},
    # ── EPP MANOS ─────────────────────────────────────────────────────────
    {"codigo":"SST-010","categoria":"Protección manos","nombre":"Guantes de nitrilo","descripcion":"Guantes desechables nitrilo","material":"Nitrilo","uso":"Químicos y líquidos","tallas":"S/M/L/XL","colores":"Azul/Negro","precio_publico":5500,"precio_mayoreo":4200,"marca":"—","observaciones":"Desechable por par"},
    {"codigo":"SST-011","categoria":"Protección manos","nombre":"Guantes anticorte","descripcion":"Guantes nivel corte A4/A5","material":"Tejido técnico/aramida","uso":"Corte y manipulación de filos","tallas":"S/M/L/XL","colores":"Gris/Negro","precio_publico":29000,"precio_mayoreo":23000,"marca":"—","observaciones":"Con o sin recubrimiento"},
    {"codigo":"SST-012","categoria":"Protección manos","nombre":"Guantes cuero","descripcion":"Guantes cuero industrial","material":"Cuero vacuno","uso":"Alta fricción y calor moderado","tallas":"S/M/L/XL","colores":"Café/Negro","precio_publico":52000,"precio_mayoreo":41000,"marca":"—","observaciones":"Para manipulación pesada"},
    {"codigo":"SST-013","categoria":"Protección manos","nombre":"Guantes térmicos","descripcion":"Guantes para altas temperaturas","material":"Kevlar/tejido térmico","uso":"Calor y resistencia térmica","tallas":"S/M/L/XL","colores":"—","precio_publico":78000,"precio_mayoreo":62000,"marca":"—","observaciones":"Para trabajos calientes"},
    {"codigo":"SST-030","categoria":"Protección manos","nombre":"Guantes laboratorio (nitrilo/latex)","descripcion":"Guantes para laboratorio","material":"Látex/nitrilo","uso":"Procedimientos de laboratorio","tallas":"S/M/L/XL","colores":"Transparente","precio_publico":8000,"precio_mayoreo":6500,"marca":"—","observaciones":"Usar según alergias"},
    # ── PROTECCIÓN CORPORAL ───────────────────────────────────────────────
    {"codigo":"SST-014","categoria":"Protección corporal","nombre":"Chaleco reflectivo","descripcion":"Chaleco alta visibilidad","material":"Alta visibilidad","uso":"Señalización en planta y vía","tallas":"S-3XL","colores":"Naranja/Amarillo","precio_publico":98000,"precio_mayoreo":78000,"marca":"—","observaciones":"Cintas reflectantes"},
    {"codigo":"SST-015","categoria":"Protección corporal","nombre":"Overol químico","descripcion":"Enterizo protección química","material":"Material barrera","uso":"Riesgo químico","tallas":"S-3XL","colores":"Blanco/Amarillo","precio_publico":260000,"precio_mayoreo":210000,"marca":"—","observaciones":"Según formulación química"},
    {"codigo":"SST-016","categoria":"Protección corporal","nombre":"Traje para lluvia","descripcion":"Impermeable industrial","material":"PVC o tela impermeable","uso":"Lluvia y ambiente húmedo","tallas":"S-3XL","colores":"Negro/Gris","precio_publico":155000,"precio_mayoreo":125000,"marca":"—","observaciones":"Costuras selladas si aplica"},
    # ── ROSTRO ────────────────────────────────────────────────────────────
    {"codigo":"SST-017","categoria":"Seguridad visual/rostro","nombre":"Protector facial (careta)","descripcion":"Careta facial completa","material":"Policarbonato","uso":"Salpicaduras e impacto facial","tallas":"Única","colores":"Transparente","precio_publico":65000,"precio_mayoreo":52000,"marca":"—","observaciones":"Usar con gafas según estándar"},
    # ── SEGURIDAD EN ALTURA ───────────────────────────────────────────────
    {"codigo":"SST-018","categoria":"Seguridad en altura","nombre":"Arnés cuerpo completo","descripcion":"Arnés anticaídas certificado","material":"Poliéster + herrajes acero","uso":"Trabajo en altura +1.8m","tallas":"Única ajustable","colores":"Negro/Naranja","precio_publico":320000,"precio_mayoreo":255000,"marca":"—","observaciones":"Con certificación"},
    {"codigo":"SST-019","categoria":"Seguridad en altura","nombre":"Línea de vida","descripcion":"Línea de vida retráctil","material":"Cuerda/cable + conectores","uso":"Protección anticaídas en altura","tallas":"Longitud según necesidad","colores":"—","precio_publico":180000,"precio_mayoreo":145000,"marca":"—","observaciones":"Longitud según necesidad"},
    {"codigo":"SST-020","categoria":"Seguridad en altura","nombre":"Eslinga con absorbedor","descripcion":"Eslinga anticaídas doble","material":"Cinta/cuerda + absorbedor","uso":"Amortiguación de caída","tallas":"Única","colores":"Naranja","precio_publico":160000,"precio_mayoreo":128000,"marca":"—","observaciones":"Usar con arnés SST-018"},
    # ── SEÑALIZACIÓN ──────────────────────────────────────────────────────
    {"codigo":"SST-021","categoria":"Señalización","nombre":"Cinta de seguridad","descripcion":"Cinta demarcación","material":"Poliéster/vinilo","uso":"Delimitación de zonas","tallas":"Rollo","colores":"Rojo/Amarillo","precio_publico":8000,"precio_mayoreo":6500,"marca":"—","observaciones":"Instalación rápida"},
    {"codigo":"SST-022","categoria":"Señalización","nombre":"Conos reflectivos","descripcion":"Conos de seguridad vial","material":"PVC + reflectivo","uso":"Señalización vial y de planta","tallas":"Única","colores":"Naranja","precio_publico":28000,"precio_mayoreo":22000,"marca":"—","observaciones":"Para desvíos y tránsito"},
    {"codigo":"SST-023","categoria":"Señalización","nombre":"Cinta demarcación piso","descripcion":"Cinta adhesiva para piso","material":"PVC","uso":"Delimitación permanente de pisos","tallas":"Rollo 33m","colores":"Amarillo/Blanco","precio_publico":26000,"precio_mayoreo":20000,"marca":"—","observaciones":"Con o sin reflectivo"},
    # ── PRIMEROS AUXILIOS ─────────────────────────────────────────────────
    {"codigo":"SST-024","categoria":"Primeros auxilios","nombre":"Botiquín industrial","descripcion":"Botiquín completo SGSST","material":"Contenido básico","uso":"Primeros auxilios en planta","tallas":"Unidades variadas","colores":"Rojo/Blanco","precio_publico":120000,"precio_mayoreo":98000,"marca":"—","observaciones":"Incluye contenidos estimados"},
    {"codigo":"SST-025","categoria":"Primeros auxilios","nombre":"Gel antibacterial","descripcion":"Gel desinfectante de manos","material":"Alcohol isopropílico","uso":"Higiene personal","tallas":"Única","colores":"—","precio_publico":18000,"precio_mayoreo":14000,"marca":"—","observaciones":"Complemento SST"},
    # ── EMERGENCIAS ───────────────────────────────────────────────────────
    {"codigo":"SST-026","categoria":"Emergencias","nombre":"Extintor","descripcion":"Extintor multipropósito","material":"PQS/CO2","uso":"Control de incendios","tallas":"Única","colores":"Rojo","precio_publico":420000,"precio_mayoreo":350000,"marca":"—","observaciones":"Requiere manejo y servicio"},
    # ── CALZADO DE SEGURIDAD ──────────────────────────────────────────────
    {"codigo":"SST-027","categoria":"Calzado de seguridad","nombre":"Zapato seguridad punta acero","descripcion":"Zapato industrial con puntera","material":"Cuero + puntera acero","uso":"Protección ante impacto en pie","tallas":"36-46","colores":"Negro","precio_publico":155000,"precio_mayoreo":125000,"marca":"—","observaciones":"Definir tipo puntera"},
    {"codigo":"SST-028","categoria":"Calzado de seguridad","nombre":"Bota seguridad impermeable","descripcion":"Bota con membrana impermeable","material":"Cuero + membrana","uso":"Lluvia y ambiente húmedo","tallas":"36-46","colores":"Negro/Café","precio_publico":205000,"precio_mayoreo":165000,"marca":"—","observaciones":"Antideslizante"},
    # ── ALTA VISIBILIDAD SET ──────────────────────────────────────────────
    {"codigo":"SST-029","categoria":"Protección de alta visibilidad","nombre":"Conjunto HV completo","descripcion":"Chaleco + gorra HV","material":"Tela alta visibilidad","uso":"Señalización total persona","tallas":"S-3XL","colores":"Naranja/Amarillo","precio_publico":135000,"precio_mayoreo":108000,"marca":"—","observaciones":"Depende del set real"},
    # ── CALZADO ESPECIALIZADO (marcas) ────────────────────────────────────
    {"codigo":"SST-BOT-001","categoria":"Calzado de seguridad","nombre":"Bota puntera acero/compósito","descripcion":"Bota industrial con puntera","material":"Cuero + puntera compósito","uso":"Impacto y compresión en pie","tallas":"36-46","colores":"Negro/Café","precio_publico":240000,"precio_mayoreo":195000,"marca":"CAT / Ryno / Bata Industrials / Steel Blue","observaciones":"Antideslizante"},
    {"codigo":"SST-BOT-002","categoria":"Calzado de seguridad","nombre":"Bota dieléctrica","descripcion":"Bota protección eléctrica","material":"Cuero + suela dieléctrica","uso":"Riesgo eléctrico","tallas":"36-46","colores":"Negro","precio_publico":320000,"precio_mayoreo":260000,"marca":"Ariat Work / CAT / Bata Industrials","observaciones":"Para electricidad si aplica"},
    {"codigo":"SST-BOT-003","categoria":"Calzado de seguridad","nombre":"Bota impermeable","descripcion":"Bota para lluvia/ambiente húmedo","material":"PVC o caucho","uso":"Ambientes húmedos","tallas":"36-46","colores":"Negro/Café","precio_publico":285000,"precio_mayoreo":230000,"marca":"Hunter Safety / CAT / Ryno","observaciones":"Membrana/malla impermeable"},
    {"codigo":"SST-BOT-004","categoria":"Calzado de seguridad","nombre":"Bota antiestática","descripcion":"Control de descarga estática","material":"Cuero + suela antiestática ESD","uso":"Industrias con riesgo electrostático","tallas":"36-46","colores":"Negro","precio_publico":260000,"precio_mayoreo":210000,"marca":"Steel Blue / Bata Industrials / CAT","observaciones":"Suelas ESD/AS según modelo"},
    {"codigo":"SST-BOT-005","categoria":"Calzado de seguridad","nombre":"Bota dieléctrica impermeable","descripcion":"Protección eléctrica + lluvia","material":"Cuero + membrana + suela dieléctrica","uso":"Electricidad en ambientes húmedos","tallas":"36-46","colores":"Negro","precio_publico":360000,"precio_mayoreo":290000,"marca":"CAT / Hunter Safety / Steel Blue","observaciones":"Doble protección"},
    {"codigo":"SST-BOT-006","categoria":"Calzado de seguridad","nombre":"Zapato de seguridad liviano","descripcion":"Tipo zapato sin bota","material":"Cuero liviano + puntera","uso":"Uso prolongado y confort en planta","tallas":"36-46","colores":"Negro","precio_publico":175000,"precio_mayoreo":140000,"marca":"Bata Industrials / Steel Blue / Ryno","observaciones":"Ideal planta/obra seca"},
    {"codigo":"SST-BOT-007","categoria":"Calzado de seguridad","nombre":"Bota resistente hidrocarburos","descripcion":"Bota para químicos leves","material":"Caucho resistente","uso":"Zonas con derrames de aceite/gasolina","tallas":"36-46","colores":"Negro","precio_publico":310000,"precio_mayoreo":250000,"marca":"Hunter Safety / Steel Blue / CAT","observaciones":"Para zonas con derrames"},
    {"codigo":"SST-BOT-008","categoria":"Calzado de seguridad","nombre":"Bota con plantilla amortiguadora","descripcion":"Confort y amortiguación","material":"Cuero + plantilla gel/EVA","uso":"Trabajo de pie por largo tiempo","tallas":"36-46","colores":"Negro","precio_publico":265000,"precio_mayoreo":215000,"marca":"Steel Blue / Bata Industrials / Ryno","observaciones":"Plantilla absorbente"},
]

# ── Indices y utilidades ─────────────────────────────────────────────────

# Acceso rapido por codigo
PRODUCTOS_POR_CODIGO = {p["codigo"]: p for p in PRODUCTOS}

# Categorias unicas (en orden de aparicion)
CATEGORIAS = list(dict.fromkeys(p["categoria"] for p in PRODUCTOS))


# Palabras vacias que no aportan a la busqueda (se ignoran como tokens).
_STOPWORDS = {
    "de", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o",
    "para", "con", "del", "al", "en", "por", "que", "tipo", "necesito",
    "quiero", "busco", "me", "mi", "su", "sus", "es", "son",
}


def _norm(texto: str) -> str:
    """Minusculas y sin acentos, para comparar de forma robusta."""
    texto = (texto or "").lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def _token_en(tok: str, blob: str) -> bool:
    """True si el token (o su singular/plural simple) aparece en el blob."""
    if tok in blob:
        return True
    # Plural -> singular: "botas" coincide con "bota".
    if len(tok) > 3 and tok.endswith("s") and tok[:-1] in blob:
        return True
    # Singular -> plural: "bota" coincide con "botas".
    if len(tok) > 3 and (tok + "s") in blob:
        return True
    return False


def buscar(query: str = "", categoria: str = "") -> list[dict]:
    """Busca productos por texto libre (nombre, descripcion, uso, codigo, marca)
    y/o por categoria.

    El query se divide en tokens (ignora acentos y palabras vacias como 'de',
    'para', 'con'). Cada producto recibe una puntuacion = numero de tokens que
    coinciden (tolerando plural/singular). Se devuelven los productos con mejor
    coincidencia, ordenados por relevancia."""
    tokens = [t for t in _norm(query).split() if t and t not in _STOPWORDS]
    cat = _norm(categoria)

    candidatos = []  # (score, indice, producto)
    for idx, p in enumerate(PRODUCTOS):
        if cat and cat not in _norm(p["categoria"]):
            continue
        if not tokens:
            candidatos.append((0, idx, p))
            continue
        blob = _norm(" ".join([
            p["codigo"], p["nombre"], p["descripcion"],
            p["uso"], p["categoria"], p["material"], p["marca"],
            p["observaciones"],
        ]))
        score = sum(1 for tok in tokens if _token_en(tok, blob))
        if score > 0:
            candidatos.append((score, idx, p))

    if not tokens:
        return [p for _, _, p in candidatos]
    if not candidatos:
        return []

    # Umbral: en consultas cortas (1-2 tokens) exige todos; en consultas
    # largas basta con la mitad (tolera plurales/sinonimos y palabras de relleno).
    n = len(tokens)
    umbral = n if n <= 2 else (n + 1) // 2
    filtrados = [c for c in candidatos if c[0] >= umbral] or candidatos
    # Orden: mayor score primero; a igualdad, orden original del catalogo.
    filtrados.sort(key=lambda c: (-c[0], c[1]))
    return [p for _, _, p in filtrados]


def obtener(codigo: str) -> dict | None:
    """Devuelve un producto por su codigo exacto, o None."""
    return PRODUCTOS_POR_CODIGO.get((codigo or "").strip().upper())


def precio_de(producto: dict, es_mayorista: bool = False) -> int:
    """Devuelve el precio aplicable segun tipo de cliente."""
    return producto["precio_mayoreo"] if es_mayorista else producto["precio_publico"]
