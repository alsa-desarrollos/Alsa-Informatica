{
    'name': 'Cotizaciones Express',
    'version': '19.0.1.0.60',
    'category': 'Sales/Sales',
    'summary': 'Generación ágil de cotizaciones comerciales con múltiples opciones, previsualización PDF interactiva en vivo e integración de firmas.',
    'description': """
Módulo de Cotizaciones Express - ALSA Informática
===================================================

Cotizaciones Express permite a los consultores y ejecutivos de ventas crear, estructurar y enviar propuestas comerciales de alto impacto técnico y visual en cuestión de segundos.

Características Principales:
----------------------------
* **Previsualización PDF en Vivo en Pantalla Dividida:** Visualiza en tiempo real el documento final impreso mientras escribes los datos de la cotización, productos y términos comerciales.
* **Gestión de Múltiples Opciones:** Configura opciones alternativas de productos o servicios (Opción 1, Opción 2, etc.).
* **Firma de Vendedor Integrada:** Vincula la firma digital del consultor en informática para emitir propuestas formales y autorizadas.
* **Gestión Kanban por Etapas:** Rastrea la evolución comercial desde Borrador hasta Venta Confirmada.
* **Totales Inteligentes en PDF:** Suma automáticamente las opciones seleccionadas únicamente cuando se confirma la venta, manteniendo las opciones como alternativas independientes en etapa de borrador.
* **Tipografía y Formato Profesional:** Diseñado con estándar ejecutivo en Times New Roman 11pt, optimizado para impresión perfecta en hoja tamaño Carta (US Letter).
""",
    'author': 'Alsa Informática',
    'website': 'https://alsainformatica.com.mx',
    'depends': ['base', 'contacts', 'sale', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/stages.xml',
        'views/seller_signature_views.xml',
        'views/cotizacion_views.xml',
        'report/cotizacion_preview.xml',
        'report/cotizacion_report.xml',
        'report/cotizacion_contpaqi_report.xml',
        'data/mail_template.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'cotizaciones_express/static/src/css/cotizacion_form.css',
            'cotizaciones_express/static/src/js/cotizacion_fix.js',
            'cotizaciones_express/static/src/js/cotizacion_list_totals.js',
        ],
    },
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
