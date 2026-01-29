"""Parser de intenciones y entidades usando reglas + regex + rapidfuzz"""
import re
import json
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
import dateparser
from rapidfuzz import fuzz, process
import database
import config
from utils import normalize_text, extract_client_mentions


class IntentParser:
    """Parser de intenciones y extracción de entidades"""
    
    # Patrones para intenciones
    INTENT_PATTERNS = {
        'CREAR': [
            r'(?:crear|nueva|añadir|agregar|poner|hacer|tengo que|necesito)',
            r'(?:tarea|recordatorio|nota|evento|cosa)',
        ],
        'LISTAR': [
            r'(?:listar|mostrar|ver|dame|muéstrame|qué|cuáles)',
            r'(?:tareas|pendientes|cosas|recordatorios)',
        ],
        'CERRAR': [
            r'(?:cerrar|completar|terminar|hecho|realizado|finalizar|marcar como hecha|da por hecha)',
            r'(?:tarea|tareas|cosa|cosas)',
        ],
        'REPROGRAMAR': [
            r'(?:reprogramar|cambiar fecha|mover|posponer|aplazar|cambiar a)',
        ],
        'CAMBIAR_PRIORIDAD': [
            r'(?:cambiar prioridad|cambiar la prioridad|modificar prioridad)',
        ],
    }
    
    # Patrones para fechas relativas
    DATE_PATTERNS = {
        'hoy': lambda: datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
        'mañana': lambda: (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
        'pasado mañana': lambda: (datetime.now() + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0),
        'esta semana': lambda: datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
        'próxima semana': lambda: (datetime.now() + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0),
        'el lunes': lambda: _next_weekday(0),
        'el martes': lambda: _next_weekday(1),
        'el miércoles': lambda: _next_weekday(2),
        'el jueves': lambda: _next_weekday(3),
        'el viernes': lambda: _next_weekday(4),
        'el sábado': lambda: _next_weekday(5),
        'el domingo': lambda: _next_weekday(6),
        'lunes': lambda: _next_weekday(0),
        'martes': lambda: _next_weekday(1),
        'miércoles': lambda: _next_weekday(2),
        'jueves': lambda: _next_weekday(3),
        'viernes': lambda: _next_weekday(4),
        'sábado': lambda: _next_weekday(5),
        'domingo': lambda: _next_weekday(6),
    }
    
    # Mapeo de días de la semana en español
    WEEKDAY_MAP = {
        'lunes': 0,
        'martes': 1,
        'miércoles': 2,
        'miercoles': 2,  # Sin tilde
        'jueves': 3,
        'viernes': 4,
        'sábado': 5,
        'sabado': 5,  # Sin tilde
        'domingo': 6,
    }
    
    # Mapeo de prioridades
    PRIORITY_MAP = {
        'urgente': 'urgent',
        'urgent': 'urgent',
        'alta': 'high',
        'high': 'high',
        'importante': 'high',
        'normal': 'normal',
        'media': 'normal',
        'baja': 'low',
        'low': 'low',
        'sin prisa': 'low',
        'sin prisa': 'low',
    }
    
    def __init__(self):
        self.db = database.db
    
    def parse(self, text: str) -> Dict:
        """Parsea texto y extrae intención y entidades"""
        text_normalized = normalize_text(text)
        
        # Detectar intención
        intent = self._detect_intent(text_normalized)
        
        # Extraer entidades: usar OpenAI si está disponible, sino usar métodos tradicionales
        if config.OPENAI_ENABLED and intent == 'CREAR':
            try:
                entities_openai = self._extract_entities_with_openai(text)
                # Combinar con extracción tradicional de cliente (fuzzy matching)
                client_info = self._extract_client(text)
                entities = {
                    'client': client_info,
                    'date': entities_openai.get('date'),
                    'priority': entities_openai.get('priority', 'normal'),
                    'title': entities_openai.get('title'),
                    'category': entities_openai.get('category'),
                }
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Error al usar OpenAI para extracción de entidades: {e}, usando método tradicional")
                # Fallback a método tradicional
                entities = {
                    'client': self._extract_client(text),
                    'date': self._extract_date(text),
                    'priority': self._extract_priority(text_normalized),
                    'title': self._extract_title(text, intent),
                }
        else:
            # Método tradicional
            entities = {
                'client': self._extract_client(text),
                'date': self._extract_date(text),
                'priority': self._extract_priority(text_normalized),
                'title': self._extract_title(text, intent),
            }
        
        return {
            'intent': intent,
            'entities': entities,
            'original_text': text,
        }
    
    def _detect_intent(self, text: str) -> str:
        """Detecta intención principal"""
        text_lower = text.lower()
        
        # Verificar cada intención
        for intent, patterns in self.INTENT_PATTERNS.items():
            # Todos los patrones deben coincidir (AND)
            matches = [bool(re.search(pattern, text_lower, re.IGNORECASE)) for pattern in patterns]
            if all(matches):
                return intent
        
        # Si no coincide ninguna, asumir CREAR por defecto
        return 'CREAR'
    
    def _extract_client(self, text: str) -> Optional[Dict]:
        """Extrae cliente del texto usando fuzzy matching"""
        mentions = extract_client_mentions(text)
        
        if not mentions:
            return None
        
        # Usar la primera mención encontrada
        client_name_raw = mentions[0]
        
        # Buscar en base de datos
        match_result = self._fuzzy_match_client(client_name_raw)
        
        return {
            'raw': client_name_raw,
            'match': match_result,
        }
    
    def _fuzzy_match_client(self, name: str) -> Dict:
        """Busca cliente usando fuzzy matching"""
        # Obtener todos los clientes
        clients = self.db.get_all_clients()
        
        if not clients:
            return {
                'found': False,
                'confidence': 0,
                'action': 'create',
            }
        
        # Preparar lista de nombres y aliases para matching
        candidates = []
        for client in clients:
            candidates.append({
                'id': client['id'],
                'name': client['name'],
                'normalized': client['normalized_name'],
                'aliases': json.loads(client['aliases'] or '[]'),
            })
        
        # Buscar match exacto normalizado primero
        normalized_input = normalize_text(name)
        for candidate in candidates:
            if candidate['normalized'] == normalized_input:
                return {
                    'found': True,
                    'client_id': candidate['id'],
                    'client_name': candidate['name'],
                    'confidence': 100,
                    'action': 'auto',
                }
        
        # Fuzzy matching contra nombres y aliases
        all_names = []
        for candidate in candidates:
            all_names.append((candidate['id'], candidate['name'], candidate['normalized']))
            for alias in candidate['aliases']:
                all_names.append((candidate['id'], alias, normalize_text(alias)))
        
        # Usar rapidfuzz para encontrar mejores matches
        matches = process.extract(
            normalized_input,
            [name_tuple[2] for name_tuple in all_names],
            scorer=fuzz.ratio,
            limit=config.CLIENT_MATCH_MAX_CANDIDATES
        )
        
        if not matches or matches[0][1] < config.CLIENT_MATCH_THRESHOLD_CONFIRM:
            return {
                'found': False,
                'confidence': matches[0][1] if matches else 0,
                'action': 'create',
            }
        
        best_match = matches[0]
        confidence = best_match[1]
        
        # Encontrar el cliente correspondiente
        matched_name = best_match[0]
        client_id = None
        client_name = None
        
        for name_tuple in all_names:
            if name_tuple[2] == matched_name:
                client_id = name_tuple[0]
                # Obtener nombre original (no normalizado)
                for candidate in candidates:
                    if candidate['id'] == client_id:
                        client_name = candidate['name']
                        break
                break
        
        if confidence >= config.CLIENT_MATCH_THRESHOLD_AUTO:
            action = 'auto'
        else:
            action = 'confirm'
            # Preparar candidatos para confirmación
            candidates_list = []
            for match in matches[:config.CLIENT_MATCH_MAX_CANDIDATES]:
                matched_norm = match[0]
                for name_tuple in all_names:
                    if name_tuple[2] == matched_norm:
                        for candidate in candidates:
                            if candidate['id'] == name_tuple[0]:
                                candidates_list.append({
                                    'id': candidate['id'],
                                    'name': candidate['name'],
                                    'confidence': match[1],
                                })
                                break
                        break
        
        result = {
            'found': True,
            'client_id': client_id,
            'client_name': client_name,
            'confidence': confidence,
            'action': action,
        }
        
        if action == 'confirm':
            result['candidates'] = candidates_list
        
        return result
    
    def _extract_date(self, text: str) -> Optional[datetime]:
        """Extrae fecha del texto usando dateparser"""
        text_lower = text.lower()
        
        # 1. Detectar "este lunes", "este miércoles", etc. → día más próximo (incluso si ya pasó esta semana)
        este_pattern = r'\beste\s+(lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)\b'
        este_match = re.search(este_pattern, text_lower)
        if este_match:
            weekday_name = este_match.group(1)
            weekday_name_normalized = weekday_name.replace('á', 'a').replace('é', 'e')
            weekday_num = None
            for key, value in self.WEEKDAY_MAP.items():
                if key.replace('á', 'a').replace('é', 'e') == weekday_name_normalized:
                    weekday_num = value
                    break
            if weekday_num is not None:
                # "este [día]" siempre es el más próximo, incluso si ya pasó esta semana
                return _next_weekday(weekday_num, force_next=True)
        
        # 2. Detectar "[día] de la semana que viene" → siguiente semana
        semana_que_viene_pattern = r'\b(lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)\s+de\s+la\s+semana\s+que\s+viene\b'
        semana_match = re.search(semana_que_viene_pattern, text_lower)
        if semana_match:
            weekday_name = semana_match.group(1)
            weekday_name_normalized = weekday_name.replace('á', 'a').replace('é', 'e')
            weekday_num = None
            for key, value in self.WEEKDAY_MAP.items():
                if key.replace('á', 'a').replace('é', 'e') == weekday_name_normalized:
                    weekday_num = value
                    break
            if weekday_num is not None:
                # "de la semana que viene" siempre es la siguiente semana
                return _next_weekday_in_next_week(weekday_num)
        
        # 3. Detectar "próximo [día]", "siguiente [día]" → siguiente ocurrencia
        proximo_pattern = r'\b(?:próximo|proximo|siguiente)\s+(lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)\b'
        proximo_match = re.search(proximo_pattern, text_lower)
        if proximo_match:
            weekday_name = proximo_match.group(1)
            weekday_name_normalized = weekday_name.replace('á', 'a').replace('é', 'e')
            weekday_num = None
            for key, value in self.WEEKDAY_MAP.items():
                if key.replace('á', 'a').replace('é', 'e') == weekday_name_normalized:
                    weekday_num = value
                    break
            if weekday_num is not None:
                # "próximo/siguiente [día]" siempre es el siguiente, incluso si hoy es ese día
                return _next_weekday(weekday_num, force_next=True)
        
        # 4. Detectar "el lunes", "lunes" (sin modificadores) → próximo día de esa semana
        weekday_pattern = r'\b(?:el\s+)?(lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)\b'
        weekday_match = re.search(weekday_pattern, text_lower)
        if weekday_match:
            weekday_name = weekday_match.group(1)
            weekday_name_normalized = weekday_name.replace('á', 'a').replace('é', 'e')
            weekday_num = None
            for key, value in self.WEEKDAY_MAP.items():
                if key.replace('á', 'a').replace('é', 'e') == weekday_name_normalized:
                    weekday_num = value
                    break
            if weekday_num is not None:
                # "el [día]" o "[día]" sin modificadores → próximo día más cercano
                return _next_weekday(weekday_num, force_next=False)
        
        # Verificar patrones relativos comunes
        for pattern, date_func in self.DATE_PATTERNS.items():
            if pattern in text_lower:
                return date_func()
        
        # Usar dateparser para fechas más complejas
        # Preferir fechas futuras
        settings = {
            'PREFER_DATES_FROM': 'future',
            'RELATIVE_BASE': datetime.now(),
        }
        
        # languages es un parámetro directo, no va en settings
        parsed_date = dateparser.parse(text, languages=['es'], settings=settings)
        
        if parsed_date:
            # Si no tiene hora, poner a las 9:00 AM por defecto
            if parsed_date.hour == 0 and parsed_date.minute == 0:
                parsed_date = parsed_date.replace(hour=9, minute=0)
            return parsed_date
        
        return None
    
    def _extract_priority(self, text: str) -> str:
        """Extrae prioridad del texto"""
        text_lower = text.lower()
        
        # Solo detectar "urgent" si se menciona explícitamente, sino "normal"
        urgent_keywords = ['urgente', 'urgent', 'muy urgente', 'es urgente', 'urgente por favor']
        for keyword in urgent_keywords:
            if keyword in text_lower:
                return 'urgent'
        
        return 'normal'
    
    def _get_category_synonyms(self, category_name: str) -> List[str]:
        """Retorna sinónimos y palabras clave para una categoría"""
        synonyms_map = {
            'personal': ['personal', 'privado', 'mío', 'propio'],
            'delegado': ['delegar', 'delegado', 'asignar', 'encargar', 'pasar a'],
            'en_espera': ['espera', 'esperando', 'pendiente', 'a la espera', 'esperar'],
            'ideas': ['idea', 'ideas', 'sugerencia', 'sugerencias', 'propuesta', 'propuestas', 'mejora', 'mejoras'],
            'llamar': ['llamar', 'llamada', 'llamadas', 'telefonear', 'contactar por teléfono', 'hablar por teléfono'],
            'presupuestos': ['presupuesto', 'presupuestos', 'cotización', 'cotizaciones', 'precio', 'precios', 'coste', 'costes'],
            'visitas': ['visita', 'visitas', 'ir a ver', 'reunión presencial', 'cita', 'citas'],
            'administracion': ['administración', 'admin', 'administrativo', 'papeles', 'documentación', 'trámite', 'trámites'],
            'reclamaciones': ['reclamación', 'reclamaciones', 'queja', 'quejas', 'reclamo', 'reclamos'],
            'calidad': ['calidad', 'control calidad', 'qc', 'aseguramiento calidad'],
            'comercial': ['comercial', 'ventas', 'venta', 'cliente nuevo', 'prospección'],
            'incidencias': ['incidencia', 'incidencias', 'problema', 'problemas', 'fallo', 'fallos', 'error', 'errores', 'bug', 'bugs']
        }
        return synonyms_map.get(category_name, [category_name])
    
    def _extract_entities_with_openai(self, text: str) -> Dict:
        """Extrae entidades usando GPT-4o-mini"""
        import logging
        logger = logging.getLogger(__name__)
        
        if not config.OPENAI_ENABLED:
            raise RuntimeError("OpenAI no está habilitado")
        
        # Obtener categorías disponibles
        categories = self.db.get_all_categories()
        category_names = [cat['name'] for cat in categories]
        
        # Crear mapeo de categorías con sus nombres de visualización y sinónimos
        category_info = {}
        for cat in categories:
            category_info[cat['name']] = {
                'display_name': cat.get('display_name', cat['name']),
                'icon': cat.get('icon', ''),
                'synonyms': self._get_category_synonyms(cat['name'])
            }
        
        # Fecha actual para contexto
        fecha_actual = datetime.now()
        fecha_mañana = (fecha_actual + timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Construir lista detallada de categorías para el prompt
        categories_list = []
        for cat_name in category_names:
            info = category_info[cat_name]
            synonyms_str = ', '.join(info['synonyms'])
            categories_list.append(
                f"- '{cat_name}' ({info['display_name']}): Busca palabras como: {synonyms_str}"
            )
        categories_text = '\n'.join(categories_list)
        
        system_prompt = f"""Eres un asistente experto en extraer información estructurada de mensajes sobre tareas en español.

TU TAREA es analizar el texto y extraer SIEMPRE estos campos:
1. CATEGORÍA: DEBE ser EXACTAMENTE una de las categorías disponibles (usa el nombre exacto) - ESTE ES EL CAMPO MÁS IMPORTANTE
2. PRIORIDAD: SOLO "urgent" o "normal" (si no se menciona "urgente", usa "normal")
3. FECHA: Si se menciona "mañana", "hoy", "el lunes", etc., conviértela a formato ISO (YYYY-MM-DD)
4. TÍTULO: Un resumen corto y claro de la tarea

⚠️ REGLAS CRÍTICAS PARA CATEGORÍAS (MUY IMPORTANTE - LEE CON ATENCIÓN):
Las categorías disponibles son EXACTAMENTE estas (usa SOLO estos nombres exactos):
{categories_text}

INSTRUCCIONES OBLIGATORIAS PARA CATEGORÍAS:
1. SIEMPRE debes intentar identificar una categoría. Solo devuelve null si es IMPOSIBLE determinar ninguna relación.
2. Analiza TODO el texto buscando palabras clave relacionadas con cada categoría
3. Si encuentras CUALQUIER palabra relacionada con una categoría, usa el NOMBRE EXACTO de esa categoría
4. Si el texto menciona múltiples categorías posibles, elige la MÁS RELEVANTE según el contexto principal de la tarea
5. IMPORTANTE: El nombre debe ser EXACTAMENTE uno de los nombres listados arriba, sin espacios extra ni mayúsculas
6. Busca sinónimos, variaciones y palabras relacionadas (ver ejemplos abajo)
7. Si el texto menciona acciones como "llamar", "visitar", "presupuesto", etc., asigna la categoría correspondiente

EJEMPLOS DE MATCHING (usa estos como referencia):
- "tengo que llamar" / "llamar a" / "hacer una llamada" / "contactar por teléfono" → "llamar"
- "hacer una visita" / "ir a ver" / "visitar" / "cita" → "visitas"
- "presupuesto" / "cotización" / "precio" / "coste" → "presupuestos"
- "reclamación" / "queja" / "reclamo" → "reclamaciones"
- "incidencia" / "problema" / "fallo" / "error" / "bug" → "incidencias"
- "administración" / "admin" / "papeles" / "documentación" / "trámite" → "administracion"
- "calidad" / "control calidad" / "qc" → "calidad"
- "comercial" / "ventas" / "venta" / "cliente nuevo" → "comercial"
- "idea" / "ideas" / "sugerencia" / "propuesta" / "mejora" → "ideas"
- "personal" / "privado" / "mío" → "personal"
- "delegar" / "asignar" / "encargar" / "pasar a" → "delegado"
- "en espera" / "esperando" / "pendiente" / "a la espera" → "en_espera"

ANÁLISIS DE CONTEXTO:
- Si el texto menciona "cliente" + acción comercial → "comercial"
- Si el texto menciona "problema" o "fallo" → "incidencias"
- Si el texto menciona "documentos" o "papeles" → "administracion"
- Si el texto menciona "revisar" o "verificar" → considera "calidad" o "incidencias"

FECHAS:
- "mañana" = fecha de mañana
- "hoy" = fecha de hoy
- "este lunes/martes/etc" = el lunes/martes/etc MÁS PRÓXIMO (incluso si ya pasó esta semana)
- "el lunes/martes/etc" o "lunes/martes/etc" (sin modificadores) = próximo día de esa semana (o hoy si es ese día)
- "próximo [día]" o "siguiente [día]" = siempre el siguiente día, incluso si hoy es ese día
- "[día] de la semana que viene" = siempre el día de la SIGUIENTE semana (no esta semana)
- "esta semana" = fecha de hoy
- "próxima semana" = fecha dentro de 7 días
- SIEMPRE usa fechas FUTURAS (hoy o después)
- La fecha actual es: {fecha_actual.strftime('%Y-%m-%d')}
- Mañana es: {fecha_mañana}
- NUNCA devuelvas fechas del pasado (antes de hoy)

PRIORIDADES:
- SOLO devuelve "urgent" si el texto menciona EXPLÍCITAMENTE palabras como: "urgente", "urgent", "muy urgente", "es urgente", "urgente por favor"
- Si NO se menciona ninguna palabra relacionada con urgente, SIEMPRE devuelve "normal"
- NUNCA preguntes ni devuelvas null para prioridad, SIEMPRE debe ser "urgent" o "normal"
- Por defecto, SIEMPRE usa "normal" a menos que se mencione explícitamente "urgente"

Responde SOLO con un JSON válido (sin texto adicional):
{{
  "category": "nombre_exacto_categoria" o null,
  "priority": "urgent" o "normal",
  "date": "YYYY-MM-DD" o null,
  "title": "resumen corto de la tarea" o null
}}

IMPORTANTE: 
- Responde SOLO con el JSON, sin explicaciones ni texto adicional
- La categoría debe ser EXACTAMENTE uno de los nombres listados arriba o null
- La prioridad SIEMPRE debe ser "urgent" o "normal", nunca null"""
        
        user_prompt = f"""Categorías disponibles (usa SOLO estos nombres exactos): {', '.join(category_names) if category_names else 'Ninguna'}

Texto a analizar: "{text}"

INSTRUCCIONES:
1. Analiza el texto COMPLETO buscando palabras clave relacionadas con las categorías
2. Identifica la categoría MÁS APROPIADA basándote en el contexto y las palabras clave
3. Si encuentras CUALQUIER relación con una categoría, úsala (no devuelvas null a menos que sea absolutamente imposible)
4. El texto puede mencionar acciones, objetos o conceptos que se relacionan con categorías específicas
5. Usa el NOMBRE EXACTO de la categoría de la lista de arriba

Ejemplos de análisis:
- "necesito llamar al cliente mañana" → categoría: "llamar" (menciona "llamar")
- "hacer presupuesto para el proyecto" → categoría: "presupuestos" (menciona "presupuesto")
- "hay una incidencia con el servidor" → categoría: "incidencias" (menciona "incidencia")
- "tengo que hacer papeles administrativos" → categoría: "administracion" (menciona "administrativos")

Analiza el texto y extrae TODOS los campos, especialmente la categoría."""
        
        try:
            from openai import OpenAI
            client = OpenAI(api_key=config.OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content
            result_json = json.loads(result_text)
            
            # Procesar fecha
            parsed_date = None
            if result_json.get('date'):
                try:
                    # Intentar parsear como ISO
                    parsed_date = datetime.fromisoformat(result_json['date'])
                    # Validar que sea futura
                    now = datetime.now()
                    if parsed_date < now.replace(hour=0, minute=0, second=0, microsecond=0):
                        logger.warning(f"[OPENAI] Fecha del pasado detectada ({parsed_date}), descartando")
                        parsed_date = None
                    else:
                        # Si no tiene hora, poner a las 9:00 AM
                        if parsed_date.hour == 0 and parsed_date.minute == 0:
                            parsed_date = parsed_date.replace(hour=9, minute=0)
                except (ValueError, TypeError):
                    logger.warning(f"[OPENAI] Error al parsear fecha: {result_json.get('date')}")
                    parsed_date = None
            
            # Procesar prioridad (solo "urgent" o "normal")
            # Asegurar que siempre sea "normal" por defecto
            priority = result_json.get('priority', 'normal')
            if priority and priority.lower() == 'urgent':
                priority = 'urgent'
            else:
                # Por defecto siempre "normal" si no se menciona explícitamente urgente
                priority = 'normal'
            
            # Validar y normalizar categoría
            category = result_json.get('category')
            if category:
                category = category.strip().lower()
                # Verificar que la categoría existe en la base de datos
                if category not in category_names:
                    # Intentar encontrar la categoría más similar usando fuzzy matching
                    from rapidfuzz import process
                    match = process.extractOne(category, category_names, scorer=fuzz.ratio)
                    if match and match[1] >= 80:  # Si la similitud es >= 80%
                        category = match[0]
                        logger.info(f"[OPENAI] Categoría '{result_json.get('category')}' normalizada a '{category}'")
                    else:
                        logger.warning(f"[OPENAI] Categoría '{category}' no encontrada en categorías disponibles. Descartando.")
                        category = None
            
            return {
                'category': category,
                'priority': priority,
                'date': parsed_date,
                'title': result_json.get('title'),
            }
            
        except Exception as e:
            logger.error(f"Error al extraer entidades con OpenAI: {e}")
            raise
    
    def _extract_title(self, text: str, intent: str) -> str:
        """Extrae título de la tarea"""
        # Remover palabras de intención y entidades conocidas
        text_clean = text
        
        # Remover palabras de intención
        for intent_name, patterns in self.INTENT_PATTERNS.items():
            for pattern in patterns:
                text_clean = re.sub(pattern, '', text_clean, flags=re.IGNORECASE)
        
        # Remover menciones de cliente
        mentions = extract_client_mentions(text_clean)
        for mention in mentions:
            text_clean = re.sub(
                rf'\b(?:cliente|del cliente|para el cliente)\s+{re.escape(mention)}\b',
                '',
                text_clean,
                flags=re.IGNORECASE
            )
        
        # Remover palabras de prioridad
        for keyword in self.PRIORITY_MAP.keys():
            text_clean = re.sub(rf'\b{re.escape(keyword)}\b', '', text_clean, flags=re.IGNORECASE)
        
        # Limpiar espacios extra
        text_clean = re.sub(r'\s+', ' ', text_clean).strip()
        
        # Si queda muy corto, usar el texto original
        if len(text_clean) < 5:
            text_clean = text
        
        return text_clean[:200]  # Limitar longitud


def _next_weekday(weekday: int, force_next: bool = False) -> datetime:
    """
    Obtiene el día de la semana más próximo (0=lunes, 6=domingo)
    - Si force_next=False: Si hoy es ese día, devuelve hoy. Si ya pasó esta semana, devuelve el próximo.
    - Si force_next=True: Siempre devuelve el próximo día, incluso si hoy es ese día.
    """
    today = datetime.now()
    current_weekday = today.weekday()
    days_ahead = weekday - current_weekday
    
    if days_ahead < 0:
        # Ya pasó esta semana, ir a la próxima semana
        days_ahead += 7
    elif days_ahead == 0:
        # Es hoy
        if force_next:
            # Forzar siguiente semana
            days_ahead = 7
        # Si no force_next, days_ahead = 0 (hoy)
    
    result_date = (today + timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0)
    return result_date


def _next_weekday_in_next_week(weekday: int) -> datetime:
    """
    Obtiene el día de la semana de la PRÓXIMA semana (0=lunes, 6=domingo)
    Siempre devuelve el día de la semana que viene, incluso si hoy es ese día.
    """
    today = datetime.now()
    current_weekday = today.weekday()
    days_ahead = weekday - current_weekday
    
    # Siempre sumar 7 días para ir a la siguiente semana
    if days_ahead < 0:
        # Ya pasó esta semana, sumar 7 para ir a la próxima
        days_ahead += 7
    elif days_ahead == 0:
        # Es hoy, sumar 7 para ir a la próxima semana
        days_ahead = 7
    else:
        # Es un día futuro de esta semana, sumar 7 para ir a la próxima semana
        days_ahead += 7
    
    result_date = (today + timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0, microsecond=0)
    return result_date

