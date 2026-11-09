import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError, RedirectWarning

_logger = logging.getLogger(__name__)

DEFAULT_TERMS_HTML = (
    '<p>1.- Los precios incluyen IVA y son válidos por 10 días y/o agotar existencias.</p>'
    '<p>2.- Tiempo de entrega: 2 - 3 días contra confirmación del pedido.</p>'
    '<p>3.- Forma de pago: Contado anticipado para ordenar.</p>'
)

class CotizacionExpressStage(models.Model):
    _name = 'cotizacion.express.stage'
    _description = 'Etapa de Cotización Express'
    _order = 'sequence, id'

    name = fields.Char(string='Nombre de Etapa', required=True, translate=True)
    sequence = fields.Integer(string='Secuencia', default=10)
    fold = fields.Boolean(string='Plegado en Kanban', default=False)
    state_type = fields.Selection([
        ('draft', 'Borrador'),
        ('sent', 'Enviado al Cliente'),
        ('confirmed', 'Confirmado'),
        ('cancelled', 'Cancelado'),
    ], string='Tipo de Estado', default='draft', required=True, help="Define el comportamiento de negocio/botones para esta etapa.")


class CotizacionExpress(models.Model):
    _name = 'cotizacion.express'
    _description = 'Cotización Express'
    _rec_name = 'name'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    @api.model
    def init(self):
        try:
            template_ref = self.env.ref('cotizaciones_express.cotizacion_preview_template', raise_if_not_found=False)
            new_ref = self.env.ref('cotizaciones_express.cotizacion_preview_new', raise_if_not_found=False)
            inherit_ids = []
            if template_ref:
                inherit_ids.append(template_ref.id)
            if new_ref:
                inherit_ids.append(new_ref.id)
            if inherit_ids:
                views = self.env['ir.ui.view'].search([
                    ('inherit_id', 'in', inherit_ids)
                ])
                if views:
                    # Desactivar vistas conflictivas
                    views.write({'active': False})
        except Exception as e:
            pass
        super(CotizacionExpress, self).init()

    name = fields.Char(string='Cotización', required=True, copy=False, readonly=True, default='Nueva')
    partner_id = fields.Many2one('res.partner', string='Cliente', required=True, tracking=True)
    partner_name = fields.Char(related='partner_id.name', string='Nombre del Cliente')
    partner_email = fields.Char(related='partner_id.email', string='Email')
    partner_phone = fields.Char(related='partner_id.phone', string='Teléfono')
    partner_address = fields.Char(related='partner_id.contact_address', string='Dirección')

    date = fields.Date(string='Fecha', default=fields.Date.today, required=True)
    city = fields.Char(string='Ciudad', default='Colima')
    state_location = fields.Char(string='Estado (Ubicación)', default='Col.')

    user_id = fields.Many2one('res.users', string='Vendedor', default=lambda self: self.env.user, required=True)
    reply_to = fields.Char(
        string='Responder a (Email)',
        help="El cliente responderá a esta dirección de correo al contestar el correo de la cotización.",
        default='julio.hardware@alsainformatica.com.mx'
    )
    signature_id = fields.Many2one('seller.signature', string='Firma del Vendedor',
                                   domain="[('user_id', '=', user_id)]")

    option_ids = fields.One2many('cotizacion.express.option', 'cotizacion_id', string='Opciones')

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('sent', 'Enviado al Cliente'),
        ('confirmed', 'Confirmado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True)

    stage_id = fields.Many2one(
        'cotizacion.express.stage', 
        string='Etapa', 
        ondelete='restrict', 
        tracking=True,
        default=lambda self: self._default_stage_id(),
        group_expand='_read_group_stage_ids'
    )

    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', string='Moneda')
    notes = fields.Html(
        string='Notas / Términos',
        default=DEFAULT_TERMS_HTML,
    )
    preview_html = fields.Html(string='Vista Previa', compute='_compute_preview_html', sanitize=False)
    template_id = fields.Many2one('cotizacion.express.template', string='Cargar Plantilla')
    tag_ids = fields.Many2many('cotizacion.express.tag', string='Etiquetas')
    amount_total = fields.Monetary(string='Total', compute='_compute_amount_total', store=True)
    amount_confirmed = fields.Monetary(string='Monto Confirmado', compute='_compute_amount_confirmed', store=True, currency_field='currency_id')

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'notes' in fields_list and not values.get('notes'):
            values['notes'] = DEFAULT_TERMS_HTML
        return values

    @api.depends('amount_total', 'state')
    def _compute_amount_confirmed(self):
        for rec in self:
            if rec.state == 'confirmed':
                rec.amount_confirmed = rec.amount_total
            else:
                rec.amount_confirmed = 0.0

    @api.model
    def _read_group_stage_ids(self, stages, domain, order=None):
        return self.env['cotizacion.express.stage'].search([], order=order)

    def _default_stage_id(self):
        return self.env['cotizacion.express.stage'].search([('state_type', '=', 'draft')], limit=1).id

    @api.onchange('stage_id')
    def _onchange_stage_id(self):
        if self.stage_id:
            self.state = self.stage_id.state_type

    @api.depends(
        'write_date',
        'name',
        'option_ids.sequence',
        'option_ids.write_date',
        'option_ids.line_ids.sequence',
        'option_ids.line_ids.write_date',
    )
    def _compute_preview_html(self):
        for rec in self:
            if rec.id and isinstance(rec.id, int):
                # Las líneas y opciones se guardan en modelos hijos, por lo que
                # moverlas no siempre actualiza el write_date de la cotización.
                # Usar la fecha más reciente evita mostrar un PDF en caché.
                write_dates = [rec.write_date]
                write_dates += rec.option_ids.mapped('write_date')
                write_dates += rec.option_ids.line_ids.mapped('write_date')
                latest_write = max((date for date in write_dates if date), default=False)
                t = int(latest_write.timestamp() * 1000000) if latest_write else 0
                safe_name = (rec.name or '').replace('/', '_').replace('\\', '_').strip()
                filename = f"Cotizacion_{safe_name}.pdf" if safe_name else "Cotizacion.pdf"
                pdf_url = f"/cotizacion/pdf/{rec.id}"
                rec.preview_html = (
                    f'<div style="width: 100%; height: 100%; min-height: 650px;">'
                    f'<iframe src="{pdf_url}?filename={filename}&t={t}#zoom=page-width&view=FitH" '
                    f'style="width: 100%; height: 100%; border: none; min-height: 650px;" '
                    f'title="Preview PDF"></iframe>'
                    f'</div>'
                )
            else:
                rec.preview_html = (
                    '<div style="display: flex; align-items: center; justify-content: center; height: 650px; '
                    'background: #f8f9fa; color: #666; font-size: 14px;">'
                    'Guarde la cotización para ver la vista previa del PDF.</div>'
                )

    @api.depends('option_ids.total', 'option_ids.selected')
    def _compute_amount_total(self):
        for rec in self:
            selected_options = rec.option_ids.filtered('selected')
            if selected_options:
                rec.amount_total = sum(selected_options.mapped('total'))
            elif rec.option_ids:
                rec.amount_total = rec.option_ids[0].total
            else:
                rec.amount_total = 0.0

    def action_load_template(self):
        self.ensure_one()
        if not self.template_id:
            return
        self.notes = self.template_id.notes
        
        self.option_ids.unlink()
        
        for t_option in self.template_id.option_ids:
            option = self.env['cotizacion.express.option'].create({
                'cotizacion_id': self.id,
                'name': t_option.name,
                'discount_general': t_option.discount_general,
            })
            for t_line in t_option.line_ids:
                self.env['cotizacion.express.option.line'].create({
                    'option_id': option.id,
                    'name': t_line.name,
                    'description': t_line.description,
                    'quantity': t_line.quantity,
                    'price_unit': t_line.price_unit,
                    'discount': t_line.discount,
                    'iva_percent': t_line.iva_percent,
                })


    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == 'Nueva':
                seq_name = self.env['ir.sequence'].next_by_code('cotizacion.express') or 'Nueva'
                # Eliminar prefijos antiguos como COT- o COT si la secuencia los tuviera
                for prefix in ['COT-', 'COT', 'cot-', 'cot']:
                    if seq_name.startswith(prefix):
                        seq_name = seq_name[len(prefix):]
                # Asegurar que termine en el año en curso (ej. -26 para 2026, -27 para 2027)
                import datetime
                import re
                year_suffix = f"-{str(datetime.date.today().year)[-2:]}"
                if not re.search(r'-\d{2}$', seq_name):
                    seq_name = f"{seq_name}{year_suffix}"
                vals['name'] = seq_name
            if 'state' in vals and 'stage_id' not in vals:
                stage = self.env['cotizacion.express.stage'].search([('state_type', '=', vals['state'])], limit=1)
                if stage:
                    vals['stage_id'] = stage.id
            elif 'stage_id' in vals and 'state' not in vals:
                stage = self.env['cotizacion.express.stage'].browse(vals['stage_id'])
                if stage:
                    vals['state'] = stage.state_type
        records = super(CotizacionExpress, self).create(vals_list)
        return records

    def write(self, vals):
        if 'stage_id' in vals:
            stage = self.env['cotizacion.express.stage'].browse(vals['stage_id'])
            if stage:
                vals['state'] = stage.state_type
        if 'state' in vals and 'stage_id' not in vals:
            stage = self.env['cotizacion.express.stage'].search([('state_type', '=', vals['state'])], limit=1)
            if stage:
                vals['stage_id'] = stage.id

        is_confirming = vals.get('state') == 'confirmed'
        needs_confirmation = is_confirming and any(
            record.state != 'confirmed' for record in self
        )
        if needs_confirmation and not self.env.context.get('from_wizard'):
            self.ensure_one()

            pending_vals = {
                key: value
                for key, value in vals.items()
                if key not in ('state', 'stage_id')
            }
            if pending_vals:
                super(CotizacionExpress, self).write(pending_vals)

            if not self.option_ids:
                raise UserError(_('Debe agregar al menos una opción antes de confirmar la cotización.'))

            if len(self.option_ids) == 1:
                self.option_ids.selected = True
                self.with_context(from_wizard=True).action_confirm_sale_order()
                return True

            action = self.env.ref('cotizaciones_express.action_cotizacion_confirm_wizard')
            raise RedirectWarning(
                _("Para confirmar esta cotización, por favor seleccione las opciones de venta confirmadas."),
                action.id,
                _("Seleccionar Opciones"),
                {'default_cotizacion_id': self.id, 'active_id': self.id}
            )
                
        return super(CotizacionExpress, self).write(vals)

    def copy(self, default=None):
        if not self:
            return self
        if len(self) > 1:
            copied_records = self.env[self._name]
            for record in self:
                copied_records |= record.copy(default)
            return copied_records

        self.ensure_one()
        default = dict(default or {})
        
        # Resetear estado a borrador y etapa por defecto
        default['state'] = 'draft'
        default['stage_id'] = self._default_stage_id()
        
        seq_name = self.env['ir.sequence'].next_by_code('cotizacion.express') or 'Nueva'
        for prefix in ['COT-', 'COT', 'cot-', 'cot']:
            if seq_name.startswith(prefix):
                seq_name = seq_name[len(prefix):]
        import datetime
        import re
        year_suffix = f"-{str(datetime.date.today().year)[-2:]}"
        if not re.search(r'-\d{2}$', seq_name):
            seq_name = f"{seq_name}{year_suffix}"
        default['name'] = seq_name
        
        copied_options = []
        for option in self.option_ids:
            copied_lines = []
            for line in option.line_ids:
                copied_lines.append((0, 0, {
                    'name': line.name,
                    'description': line.description,
                    'quantity': line.quantity,
                    'price_unit': line.price_unit,
                    'iva_percent': line.iva_percent,
                    'discount': line.discount,
                    'sequence': line.sequence,
                }))
            copied_options.append((0, 0, {
                'name': option.name,
                'description': option.description,
                'selected': option.selected,
                'sequence': option.sequence,
                'discount_general': option.discount_general,
                'line_ids': copied_lines,
            }))
        default['option_ids'] = copied_options
        return super(CotizacionExpress, self).copy(default=default)

    def action_send_to_client(self):
        self.ensure_one()
        template = self.env.ref('cotizaciones_express.email_template_cotizacion', raise_if_not_found=False)
        compose_form = self.env.ref('mail.email_compose_message_wizard_form')
        ctx = {
            'default_model': 'cotizacion.express',
            'default_res_ids': self.ids,
            'default_template_id': template.id if template else False,
            'default_composition_mode': 'comment',
            'default_reply_to': self.reply_to or (self.env.user.email or self.env.user.login),
            'force_email': True,
        }
        return {
            'name': _('Enviar Correo Electrónico'),
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(compose_form.id, 'form')],
            'view_id': compose_form.id,
            'target': 'new',
            'context': ctx,
        }

    def _message_post_after_hook(self, message, msg_dict):
        res = super()._message_post_after_hook(message, msg_dict)
        if self.env.context.get('skip_stage_update_on_message'):
            return res
        for record in self:
            if record.state == 'draft' and msg_dict.get('message_type') in ('email', 'comment'):
                stage = self.env['cotizacion.express.stage'].search([('state_type', '=', 'sent')], limit=1)
                record.with_context(skip_stage_update_on_message=True).write({
                    'state': 'sent',
                    'stage_id': stage.id if stage else False
                })
        return res

    def action_confirm(self):
        self.ensure_one()
        if not self.option_ids:
            raise UserError(_('Debe agregar al menos una opción antes de confirmar la cotización.'))

        if len(self.option_ids) == 1:
            self.option_ids.selected = True
            self.with_context(from_wizard=True).action_confirm_sale_order()
            return {
                'type': 'ir.actions.client',
                'tag': 'reload',
            }

        wizard_lines = []
        for option in self.option_ids:
            wizard_lines.append((0, 0, {
                'option_id': option.id,
                'selected': option.selected,
            }))
        
        wizard = self.env['cotizacion.confirm.wizard'].create({
            'cotizacion_id': self.id,
            'line_ids': wizard_lines,
        })
        
        return {
            'name': _('Confirmar Venta - Seleccionar Opciones'),
            'type': 'ir.actions.act_window',
            'res_model': 'cotizacion.confirm.wizard',
            'view_mode': 'form',
            'res_id': wizard.id,
            'target': 'new',
            'context': self.env.context,
        }

    def action_confirm_sale_order(self):
        self.ensure_one()
        selected = self.option_ids.filtered('selected')
        if not selected:
            raise UserError(_('Debe seleccionar al menos una opción como "Seleccionada por el Cliente"'))
        
        notes = [opt.description for opt in selected if opt.description]
        vals = {
            'partner_id': self.partner_id.id,
            'origin': self.name,
            'user_id': self.user_id.id,
            'note': '\n\n'.join(notes) if notes else '',
            'order_line': [],
            'is_express': True,
        }
        order = self.env['sale.order'].sudo().create(vals)
        for option in selected:
            for line in option.line_ids:
                product = self.env['product.product'].sudo().search([('name', '=', line.name)], limit=1)
                if not product:
                    product = self.env['product.product'].sudo().search([('name', '=', 'Concepto Cotización')], limit=1)
                if not product:
                    product = self.env['product.product'].sudo().create({
                        'name': 'Concepto Cotización',
                        'type': 'service',
                        'sale_ok': True,
                        'purchase_ok': False,
                    })
                # Calculamos el descuento combinado (descuento por línea + descuento general de la opción)
                final_discount = 100.0 * (1.0 - (1.0 - line.discount / 100.0) * (1.0 - option.discount_general / 100.0))
                order_line_vals = {
                    'order_id': order.id,
                    'product_id': product.id,
                    'name': line.name + ('\n' + line.description if line.description else ''),
                    'product_uom_qty': line.quantity,
                    'price_unit': line.price_unit,
                    'discount': final_discount,
                    'tax_ids': [(6, 0, self.env['account.tax'].sudo().search([
                        ('amount', '=', line.iva_percent),
                        ('type_tax_use', '=', 'sale'),
                    ], limit=1).ids)] if line.iva_percent else False,
                }
                self.env['sale.order.line'].sudo().create(order_line_vals)
                
        if 'crm_lead_id' in self._fields and self.crm_lead_id and 'sale_order_id' in self.crm_lead_id._fields:
            self.crm_lead_id.sudo().write({'sale_order_id': order.id})
        self.state = 'confirmed'
        
        has_access = self.env['sale.order'].check_access_rights('read', raise_exception=False)
        if has_access:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'sale.order',
                'view_mode': 'form',
                'res_id': order.id,
                'name': _('Orden de Venta'),
                'context': {'show_express_orders': True},
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Cotización Confirmada'),
                    'message': _('La cotización se ha confirmado y el pedido de venta fue creado exitosamente por el sistema.'),
                    'sticky': False,
                    'type': 'success',
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }

    def action_print_pdf(self):
        self.ensure_one()
        return self.env.ref('cotizaciones_express.report_cotizacion_express').report_action(self)

    def action_print_direct(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': f'/report/html/cotizaciones_express.cotizacion_preview_v3/{self.id}',
            'target': 'new',
        }

    def action_cancel(self):
        self.state = 'cancelled'

    def action_draft(self):
        self.state = 'draft'



class CotizacionExpressOption(models.Model):
    _name = 'cotizacion.express.option'
    _description = 'Opción / Paquete de Cotización'
    _rec_name = 'name'
    _order = 'sequence, id'

    cotizacion_id = fields.Many2one('cotizacion.express', string='Cotización', ondelete='cascade')
    name = fields.Char(string='Nombre de la Opción', required=True, default='Opción')
    description = fields.Html(string='Descripción de la Opción')
    line_ids = fields.One2many('cotizacion.express.option.line', 'option_id', string='Productos')
    selected = fields.Boolean(string='Seleccionada por el Cliente', default=False)
    sequence = fields.Integer(string='Secuencia', default=10)

    discount_general = fields.Float(string='Descuento General %', default=0.0)
    amount_lines_before_discount = fields.Monetary(string='Subtotal sin Descuento', compute='_compute_option_totals', store=True)
    discount_lines_amount = fields.Monetary(string='Descuento en Líneas', compute='_compute_option_totals', store=True)
    discount_total = fields.Monetary(string='Descuento Total', compute='_compute_option_totals', store=True)
    amount_lines_subtotal = fields.Monetary(string='Subtotal Líneas', compute='_compute_option_totals', store=True)
    amount_lines_iva = fields.Monetary(string='IVA Líneas', compute='_compute_option_totals', store=True)
    discount_general_amount = fields.Monetary(string='Monto Descuento General', compute='_compute_option_totals', store=True)
    subtotal = fields.Monetary(string='Subtotal', compute='_compute_option_totals', store=True)
    iva_total = fields.Monetary(string='IVA Total', compute='_compute_option_totals', store=True)
    total = fields.Monetary(string='Total', compute='_compute_option_totals', store=True)
    currency_id = fields.Many2one('res.currency', related='cotizacion_id.currency_id')

    @api.depends('line_ids.subtotal', 'line_ids.iva_amount', 'line_ids.price_unit', 'line_ids.quantity', 'discount_general')
    def _compute_option_totals(self):
        for rec in self:
            lines_before_discount = sum(line.price_unit * line.quantity for line in rec.line_ids)
            rec.amount_lines_before_discount = lines_before_discount
            rec.amount_lines_subtotal = sum(rec.line_ids.mapped('subtotal'))
            rec.discount_lines_amount = lines_before_discount - rec.amount_lines_subtotal
            rec.amount_lines_iva = sum(rec.line_ids.mapped('iva_amount'))
            rec.discount_general_amount = rec.amount_lines_subtotal * (rec.discount_general / 100.0)
            rec.subtotal = rec.amount_lines_subtotal - rec.discount_general_amount
            rec.iva_total = rec.amount_lines_iva * (1.0 - rec.discount_general / 100.0)
            rec.discount_total = rec.discount_lines_amount + rec.discount_general_amount
            rec.total = rec.subtotal + rec.iva_total

    def action_duplicate(self):
        self.ensure_one()
        lines = [(0, 0, {
            'name': line.name,
            'description': line.description,
            'quantity': line.quantity,
            'price_unit': line.price_unit,
            'iva_percent': line.iva_percent,
            'sequence': line.sequence,
        }) for line in self.line_ids]
        self.copy({'selected': False, 'name': self.name + ' (copia)', 'line_ids': lines})

    def unlink(self):
        parents = self.mapped('cotizacion_id')
        res = super(CotizacionExpressOption, self).unlink()
        return res


class CotizacionExpressOptionLine(models.Model):
    _name = 'cotizacion.express.option.line'
    _description = 'Producto de Opción'
    _rec_name = 'name'
    _order = 'sequence, id'

    option_id = fields.Many2one('cotizacion.express.option', string='Opción', ondelete='cascade')
    name = fields.Char(string='Producto', required=True)
    description = fields.Html(string='Descripción', sanitize=False)
    quantity = fields.Float(string='Cantidad', default=1.0, required=True)
    price_unit = fields.Monetary(string='Precio Unitario', required=True)
    currency_id = fields.Many2one('res.currency', related='option_id.currency_id')
    iva_percent = fields.Float(string='IVA %', default=16.0)
    discount = fields.Float(string='Descuento %', default=0.0)
    subtotal = fields.Monetary(string='Subtotal', compute='_compute_line_totals', store=True)
    iva_amount = fields.Monetary(string='IVA', compute='_compute_line_totals', store=True)
    total = fields.Monetary(string='Total', compute='_compute_line_totals', store=True)
    sequence = fields.Integer(string='Secuencia', default=10)

    cotizacion_id = fields.Many2one('cotizacion.express', related='option_id.cotizacion_id', store=True, string='Cotización')
    cotizacion_state = fields.Selection(related='option_id.cotizacion_id.state', store=True, string='Estado de Cotización')
    option_selected = fields.Boolean(related='option_id.selected', store=True, string='Opción Seleccionada')
    user_id = fields.Many2one('res.users', related='option_id.cotizacion_id.user_id', store=True, string='Vendedor')
    partner_id = fields.Many2one('res.partner', related='option_id.cotizacion_id.partner_id', store=True, string='Cliente')
    date = fields.Date(related='option_id.cotizacion_id.date', store=True, string='Fecha')

    def action_duplicate_line(self):
        self.ensure_one()
        self.copy({'name': self.name + ' (copia)'})

    @api.depends('price_unit', 'quantity', 'iva_percent', 'discount')
    def _compute_line_totals(self):
        for rec in self:
            subtotal_before_discount = rec.price_unit * rec.quantity
            rec.subtotal = subtotal_before_discount * (1.0 - rec.discount / 100.0)
            rec.iva_amount = rec.subtotal * (rec.iva_percent / 100.0)
            rec.total = rec.subtotal + rec.iva_amount

    def action_open_line_form(self):
        self.ensure_one()
        return {
            'name': _('Editar Producto'),
            'type': 'ir.actions.act_window',
            'res_model': 'cotizacion.express.option.line',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super(CotizacionExpressOptionLine, self).create(vals_list)
        return records

    def write(self, vals):
        res = super(CotizacionExpressOptionLine, self).write(vals)
        return res

    def unlink(self):
        parents = self.mapped('option_id.cotizacion_id')
        res = super(CotizacionExpressOptionLine, self).unlink()
        return res


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    is_express = fields.Boolean(string='Es Cotización Express', default=False)

    @api.model
    def _search(self, domain, *args, **kwargs):
        if not self.env.context.get('show_express_orders'):
            domain = [('is_express', '=', False)] + list(domain)
        return super(SaleOrder, self)._search(domain, *args, **kwargs)


class CotizacionConfirmWizard(models.TransientModel):
    _name = 'cotizacion.confirm.wizard'
    _description = 'Confirmar Opciones de Cotización'

    cotizacion_id = fields.Many2one('cotizacion.express', string='Cotización', required=True)
    line_ids = fields.One2many('cotizacion.confirm.wizard.line', 'wizard_id', string='Opciones')

    @api.model
    def default_get(self, fields_list):
        res = super(CotizacionConfirmWizard, self).default_get(fields_list)
        cotizacion_id = self.env.context.get('default_cotizacion_id') or self.env.context.get('active_id')
        if cotizacion_id:
            cotizacion = self.env['cotizacion.express'].browse(cotizacion_id)
            if cotizacion.exists():
                res['cotizacion_id'] = cotizacion.id
                lines = []
                for option in cotizacion.option_ids:
                    lines.append((0, 0, {
                        'option_id': option.id,
                        'selected': option.selected,
                    }))
                res['line_ids'] = lines
        return res

    def action_confirm(self):
        self.ensure_one()
        for line in self.line_ids:
            line.option_id.selected = line.selected
        
        self.cotizacion_id.with_context(from_wizard=True).action_confirm_sale_order()
        
        stage = self.env['cotizacion.express.stage'].search([('state_type', '=', 'confirmed')], limit=1)
        if stage:
            self.cotizacion_id.with_context(from_wizard=True).stage_id = stage.id
            
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }


class CotizacionConfirmWizardLine(models.TransientModel):
    _name = 'cotizacion.confirm.wizard.line'
    _description = 'Línea de Confirmación de Opción'

    wizard_id = fields.Many2one('cotizacion.confirm.wizard', required=True, ondelete='cascade')
    option_id = fields.Many2one('cotizacion.express.option', string='Opción', required=True)
    name = fields.Char(related='option_id.name', string='Nombre de la Opción', readonly=True)
    total = fields.Monetary(related='option_id.total', string='Total', currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', related='option_id.currency_id')
    selected = fields.Boolean(string='Seleccionada')


class CotizacionExpressTemplate(models.Model):
    _name = 'cotizacion.express.template'
    _description = 'Plantilla de Cotización Express'

    name = fields.Char(string='Nombre de la Plantilla', required=True)
    option_ids = fields.One2many('cotizacion.express.template.option', 'template_id', string='Opciones / Paquetes')
    notes = fields.Html(
        string='Notas / Términos',
        default=DEFAULT_TERMS_HTML,
    )


class CotizacionExpressTemplateOption(models.Model):
    _name = 'cotizacion.express.template.option'
    _description = 'Plantilla de Opción / Paquete'

    template_id = fields.Many2one('cotizacion.express.template', string='Plantilla de Cotización', ondelete='cascade')
    name = fields.Char(string='Nombre de la Opción', required=True)
    discount_general = fields.Float(string='Descuento General %', default=0.0)
    line_ids = fields.One2many('cotizacion.express.template.option.line', 'option_id', string='Líneas de Producto')


class CotizacionExpressTemplateOptionLine(models.Model):
    _name = 'cotizacion.express.template.option.line'
    _description = 'Plantilla de Línea de Opción'

    option_id = fields.Many2one('cotizacion.express.template.option', string='Opción', ondelete='cascade')
    name = fields.Char(string='Producto/Concepto', required=True)
    description = fields.Html(string='Descripción', sanitize=False)
    quantity = fields.Float(string='Cantidad', default=1.0)
    price_unit = fields.Float(string='Precio Unitario', default=0.0)
    discount = fields.Float(string='Descuento %', default=0.0)
    iva_percent = fields.Selection([('0', '0%'), ('8', '8%'), ('16', '16%')], string='IVA %', default='16')


class CotizacionExpressTag(models.Model):
    _name = 'cotizacion.express.tag'
    _description = 'Etiqueta de Cotización Express'

    name = fields.Char(string='Nombre', required=True)
    color = fields.Integer(string='Color')

