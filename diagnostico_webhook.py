#!/usr/bin/env python3
"""Script de diagnóstico para verificar el webhook de Telegram"""
import os
import sys
import requests
import json

# Obtener token del archivo .env o variable de entorno
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8576789817:AAFxotQdbH7eMgULQcueRp0ny3ZPD7JApZA')

def print_header(text):
    print("\n" + "=" * 60)
    print(f"🔍 {text}")
    print("=" * 60)

def check_token():
    """Verifica que el token sea válido"""
    print_header("1. Verificando Token del Bot")
    
    if not TELEGRAM_BOT_TOKEN:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN no está configurado")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if data.get('ok'):
            bot_info = data.get('result', {})
            print(f"✅ Token válido")
            print(f"   Bot: @{bot_info.get('username', 'N/A')}")
            print(f"   Nombre: {bot_info.get('first_name', 'N/A')}")
            return True
        else:
            print(f"❌ Token inválido: {data.get('description', 'Unknown error')}")
            return False
    except Exception as e:
        print(f"❌ Error al verificar token: {e}")
        return False

def check_webhook_status():
    """Verifica el estado actual del webhook"""
    print_header("2. Verificando Estado del Webhook")
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if data.get('ok'):
            webhook_info = data.get('result', {})
            current_url = webhook_info.get('url', '')
            pending_updates = webhook_info.get('pending_update_count', 0)
            last_error = webhook_info.get('last_error_message', '')
            last_error_date = webhook_info.get('last_error_date', None)
            
            print(f"📊 Estado del webhook:")
            print(f"   URL configurada: {current_url if current_url else '❌ NO CONFIGURADO'}")
            print(f"   Actualizaciones pendientes: {pending_updates}")
            
            if last_error:
                print(f"   ⚠️  Último error: {last_error}")
                if last_error_date:
                    from datetime import datetime
                    error_date = datetime.fromtimestamp(last_error_date)
                    print(f"   Fecha del error: {error_date}")
            
            if not current_url:
                print("\n❌ PROBLEMA ENCONTRADO: El webhook NO está configurado")
                print("   Telegram no sabe dónde enviar los mensajes")
                return False
            else:
                print(f"\n✅ Webhook configurado: {current_url}")
                return True
        else:
            print(f"❌ Error al obtener información: {data.get('description', 'Unknown error')}")
            return False
    except Exception as e:
        print(f"❌ Error al verificar webhook: {e}")
        return False

def check_app_health(app_url):
    """Verifica que la aplicación esté funcionando"""
    print_header("3. Verificando Aplicación en Render")
    
    if not app_url:
        print("⚠️  No se proporcionó URL de la aplicación")
        print("   Ejecuta: python diagnostico_webhook.py <URL_DE_TU_APP>")
        return False
    
    # Asegurar que la URL termine en /health
    if not app_url.endswith('/health'):
        if app_url.endswith('/'):
            app_url = app_url + 'health'
        else:
            app_url = app_url + '/health'
    
    try:
        print(f"   Verificando: {app_url}")
        response = requests.get(app_url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Aplicación funcionando")
            print(f"   Telegram configurado: {data.get('telegram_configured', False)}")
            print(f"   Base de datos: {data.get('database_path', 'N/A')}")
            return True
        else:
            print(f"❌ Aplicación respondió con código {response.status_code}")
            print(f"   Respuesta: {response.text[:200]}")
            return False
    except requests.exceptions.Timeout:
        print(f"❌ Timeout: La aplicación no responde (puede estar 'sleeping' en Render)")
        print("   En Render free tier, la app se duerme después de 15 minutos")
        return False
    except Exception as e:
        print(f"❌ Error al verificar aplicación: {e}")
        return False

def check_webhook_endpoint(app_url):
    """Verifica que el endpoint /webhook esté accesible"""
    print_header("4. Verificando Endpoint /webhook")
    
    if not app_url:
        return False
    
    # Asegurar que la URL termine en /webhook
    webhook_url = app_url.replace('/health', '/webhook')
    if not webhook_url.endswith('/webhook'):
        if webhook_url.endswith('/'):
            webhook_url = webhook_url + 'webhook'
        else:
            webhook_url = webhook_url + '/webhook'
    
    try:
        print(f"   Verificando: {webhook_url}")
        # Enviar una petición de prueba (sin datos válidos, solo para ver si responde)
        response = requests.post(webhook_url, json={}, timeout=10)
        
        print(f"   Código de respuesta: {response.status_code}")
        
        if response.status_code == 400:
            print("✅ Endpoint accesible (esperado: 400 por falta de datos)")
            return True
        elif response.status_code == 503:
            print("❌ Bot no configurado (503)")
            return False
        elif response.status_code == 401:
            print("⚠️  Endpoint requiere secret token (401)")
            return True
        else:
            print(f"   Respuesta: {response.text[:200]}")
            return True
    except requests.exceptions.Timeout:
        print(f"❌ Timeout: El endpoint no responde")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def provide_solutions(webhook_configured, app_working):
    """Proporciona soluciones basadas en los problemas encontrados"""
    print_header("💡 Soluciones Recomendadas")
    
    if not webhook_configured:
        print("\n❌ PROBLEMA PRINCIPAL: Webhook no configurado")
        print("\n📝 Pasos para solucionarlo:")
        print("\n1. Obtén la URL de tu aplicación en Render")
        print("   Ejemplo: https://tu-app.onrender.com")
        print("\n2. Configura el webhook ejecutando:")
        print("   python setup_webhook.py set")
        print("\n   O manualmente con curl:")
        print("   curl -X POST \"https://api.telegram.org/bot<TU_TOKEN>/setWebhook\" \\")
        print("     -H \"Content-Type: application/json\" \\")
        print("     -d '{\"url\": \"https://tu-app.onrender.com/webhook\"}'")
        print("\n3. Verifica variables de entorno en Render:")
        print("   - TELEGRAM_BOT_TOKEN")
        print("   - TELEGRAM_WEBHOOK_URL (debe ser: https://tu-app.onrender.com/webhook)")
        print("   - TELEGRAM_WEBHOOK_SECRET (opcional)")
    
    if not app_working:
        print("\n❌ PROBLEMA: Aplicación no responde")
        print("\n📝 Pasos para solucionarlo:")
        print("\n1. Ve a Render Dashboard → Tu servicio → Logs")
        print("2. Busca errores de inicio")
        print("3. Verifica que todas las variables de entorno estén configuradas")
        print("4. Si está 'Sleeping', envía un mensaje al bot para despertarlo")
        print("   (puede tardar 30-60 segundos la primera vez)")
    
    if webhook_configured and app_working:
        print("\n✅ Todo parece estar configurado correctamente")
        print("\n📝 Si aún no funciona:")
        print("1. Revisa los logs en Render Dashboard")
        print("2. Verifica que TELEGRAM_WEBHOOK_SECRET coincida (si lo usas)")
        print("3. Espera 1-2 minutos después de configurar el webhook")
        print("4. Envía un mensaje de texto simple (no solo /start)")

def main():
    import sys
    import io
    # Configurar encoding UTF-8 para Windows
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    print("=" * 60)
    print("DIAGNOSTICO DE WEBHOOK DE TELEGRAM")
    print("=" * 60)
    
    # Obtener URL de la app desde argumentos
    app_url = None
    if len(sys.argv) > 1:
        app_url = sys.argv[1]
    
    # Ejecutar verificaciones
    token_ok = check_token()
    if not token_ok:
        print("\n❌ No se puede continuar sin un token válido")
        sys.exit(1)
    
    webhook_configured = check_webhook_status()
    
    if app_url:
        app_working = check_app_health(app_url)
        webhook_accessible = check_webhook_endpoint(app_url)
    else:
        print("\n⚠️  No se proporcionó URL de la aplicación")
        print("   Para verificación completa, ejecuta:")
        print("   python diagnostico_webhook.py https://tu-app.onrender.com")
        app_working = False
        webhook_accessible = False
    
    # Proporcionar soluciones
    provide_solutions(webhook_configured, app_working)
    
    print("\n" + "=" * 60)
    print("✅ Diagnóstico completado")
    print("=" * 60)

if __name__ == '__main__':
    main()

