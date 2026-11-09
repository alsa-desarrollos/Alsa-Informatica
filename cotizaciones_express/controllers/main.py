import re
from urllib.parse import quote

from odoo import http
from odoo.addons.web.controllers.report import ReportController
from odoo.http import content_disposition, request


class CotizacionExpressReportController(ReportController):

    @http.route([
        '/report/<path:converter>/<reportname>',
        '/report/<path:converter>/<reportname>/<docids>',
    ], type='http', auth="user", website=True)
    def report_routes(self, reportname, docids=None, converter=None, **data):
        if (
            reportname.startswith('cotizaciones_express.')
            and not request.env.user.has_group(
                'cotizaciones_express.group_cotizaciones_express_user'
            )
        ):
            return request.not_found()

        response = super(CotizacionExpressReportController, self).report_routes(
            reportname, docids=docids, converter=converter, **data
        )
        
        if reportname == 'cotizaciones_express.cotizacion_preview_v3' and response and hasattr(response, 'headers'):
            filename = request.params.get('filename')
            if not filename and docids:
                try:
                    ids = [int(x) for x in docids.split(',')]
                    if ids:
                        rec = request.env['cotizacion.express'].browse(ids[0])
                        if rec.exists():
                            safe_name = (rec.name or '').replace('/', '_').replace('\\', '_').strip()
                            filename = f"{safe_name}.pdf" if safe_name else "Cotizacion.pdf"
                except Exception:
                    pass

            if filename:
                try:
                    encoded_filename = quote(filename)
                    response.headers.set('Content-Disposition', f'inline; filename="{filename}"; filename*=UTF-8\'\'{encoded_filename}')
                except Exception:
                    response.headers.set('Content-Disposition', f'inline; filename="{filename}"')
        
        return response


class CotizacionExpressController(http.Controller):

    @staticmethod
    def _has_module_access():
        return request.env.user.has_group(
            'cotizaciones_express.group_cotizaciones_express_user'
        )

    @http.route('/cotizacion/pdf/<int:cotizacion_id>', type='http', auth='user', website=False)
    def cotizacion_pdf(self, cotizacion_id, **kwargs):
        if not self._has_module_access():
            return request.not_found()
        cotizacion = request.env['cotizacion.express'].browse(cotizacion_id)
        if not cotizacion.exists():
            return request.not_found(description='Cotización no encontrada')

        report = request.env.ref('cotizaciones_express.report_cotizacion_express')
        pdf_content, _ = report._render_qweb_pdf(
            report.report_name,
            res_ids=[cotizacion.id],
        )

        quotation_number = re.sub(
            r'[^\w.-]+',
            '_',
            cotizacion.name or str(cotizacion.id),
        ).strip('._') or str(cotizacion.id)
        filename = f'Cotizacion_{quotation_number}.pdf'

        return request.make_response(pdf_content, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Length', str(len(pdf_content))),
            ('Content-Disposition', content_disposition(filename, disposition_type='inline')),
            ('Cache-Control', 'no-cache, no-store, must-revalidate'),
            ('Pragma', 'no-cache'),
            ('Expires', '0'),
        ])

    @http.route('/cotizacion/preview_html/<int:cotizacion_id>', type='http', auth='user', website=False)
    def preview_cotizacion_html(self, cotizacion_id, **kwargs):
        if not self._has_module_access():
            return request.not_found()
        if cotizacion_id > 0:
            cotizacion = request.env['cotizacion.express'].browse(cotizacion_id)
            if not cotizacion.exists():
                cotizacion = request.env['cotizacion.express'].new({'name': 'Nueva'})
        else:
            cotizacion = request.env['cotizacion.express'].new({'name': 'Nueva'})

        response = request.render('cotizaciones_express.cotizacion_preview_v3', {
            'docs': cotizacion,
            'preview_mode': True,
        })
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    @http.route('/cotizacion/preview.js', type='http', auth='public', website=False)
    def preview_js(self, **kwargs):
        js = r"""(function() {
    try {
        function getParentDoc() {
            try {
                if (window.parent && window.parent !== window && window.parent.document) {
                    return window.parent.document;
                }
            } catch (e) {}
            return null;
        }

        function getParentWin() {
            try {
                if (window.parent && window.parent !== window) {
                    return window.parent;
                }
            } catch (e) {}
            return null;
        }

        function getRecordId() {
            try {
                var pDoc = getParentDoc();
                if (pDoc) {
                    var formEl = pDoc.querySelector('.o_form_view');
                    if (formEl && formEl.dataset && formEl.dataset.recordId) {
                        var id = parseInt(formEl.dataset.recordId, 10);
                        if (id > 0) return id;
                    }
                }

                var pWin = getParentWin();
                if (pWin && pWin.location) {
                    var pathname = pWin.location.pathname || '';
                    var match = pathname.match(/\/cotizacion\.express\/(\d+)/);
                    if (match) return parseInt(match[1], 10);

                    var hash = pWin.location.hash || '';
                    var hashMatch = hash.match(/[#&]id=(\d+)/);
                    if (hashMatch) return parseInt(hashMatch[1], 10);

                    var search = pWin.location.search || '';
                    var queryMatch = search.match(/[?&]id=(\d+)/);
                    if (queryMatch) return parseInt(queryMatch[1], 10);
                }
            } catch(e) {}
            return 0;
        }

        var parentId = getRecordId();
        var currentUrlMatch = window.location.pathname.match(/\/preview_html\/(\d+)/);
        var currentId = currentUrlMatch ? parseInt(currentUrlMatch[1], 10) : 0;

        try {
            var pDoc = getParentDoc();
            if (pDoc) {
                var pdfLink = pDoc.getElementById('cotizacion_pdf_link');
                if (pdfLink) {
                    if (parentId > 0) {
                        pdfLink.href = "/cotizacion/pdf/" + parentId;
                        pdfLink.style.display = "";
                    } else {
                        pdfLink.style.display = "none";
                    }
                }
            }
        } catch (e) {}

        if (currentId === 0 && parentId > 0) {
            window.location.href = "/cotizacion/preview_html/" + parentId + "?t=" + Date.now();
            return;
        }

        var reloadTimeout = null;
        function triggerReload(delay) {
            if (reloadTimeout) clearTimeout(reloadTimeout);
            reloadTimeout = setTimeout(function() {
                try {
                    var activeId = getRecordId();
                    if (activeId > 0 && activeId !== currentId) {
                        window.location.href = "/cotizacion/preview_html/" + activeId + "?t=" + Date.now();
                    } else if (activeId > 0) {
                        window.location.reload();
                    } else if (currentId > 0) {
                        window.location.href = "/cotizacion/preview_html/0?t=" + Date.now();
                    }
                } catch(e) {}
            }, delay || 1000);
        }

        setInterval(function() {
            try {
                var activeId = getRecordId();
                if (activeId > 0 && activeId !== currentId) {
                    triggerReload(100);
                }
            } catch(e) {}
        }, 3000);

    } catch (e) {
        console.warn("Preview iframe safe catch:", e);
    }
})();"""
        return request.make_response(js, [('Content-Type', 'application/javascript; charset=utf-8')])

    @http.route('/cotizacion/image/<string:model>/<int:record_id>/<string:field>', type='http', auth='user', website=False)
    def get_image(self, model, record_id, field, **kwargs):
        if not self._has_module_access():
            return request.not_found()
        Model = request.env.get(model)
        if not Model:
            return request.not_found(description='Modelo no encontrado')
        record = Model.browse(record_id)
        if not record.exists():
            return request.not_found(description='Registro no encontrado')
        value = record[field]
        if not value:
            return request.not_found(description='Sin imagen')
        if isinstance(value, bytes):
            value = value.decode('ascii')
        import base64
        raw = base64.b64decode(value)
        return request.make_response(raw, [('Content-Type', 'image/png')])
