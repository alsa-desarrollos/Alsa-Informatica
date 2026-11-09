from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError


class CotizacionExpressAccessSettings(models.TransientModel):
    _name = 'cotizacion.express.access.settings'
    _description = 'Permisos de acceso a Cotizaciones Express'

    authorized_user_ids = fields.Many2many(
        'res.users',
        string='Usuarios con acceso',
        domain="[('share', '=', False), ('active', '=', True)]",
        help='Usuarios internos autorizados para utilizar Cotizaciones Express.',
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'authorized_user_ids' in fields_list:
            access_group = self.env.ref(
                'cotizaciones_express.group_cotizaciones_express_user'
            )
            administrators = self.env.ref('base.group_system').all_user_ids.filtered('active')
            values['authorized_user_ids'] = [
                Command.set((access_group.user_ids | administrators).ids)
            ]
        return values

    def action_save(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(
                _('Solo los administradores pueden modificar estos permisos.')
            )

        access_group = self.env.ref(
            'cotizaciones_express.group_cotizaciones_express_user'
        )
        internal_users = self.authorized_user_ids.filtered(
            lambda user: user.active and not user.share
        )
        administrators = self.env.ref('base.group_system').all_user_ids.filtered('active')
        authorized_users = internal_users | administrators
        access_group.write({
            'user_ids': [Command.set(authorized_users.ids)],
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Permisos actualizados'),
                'message': _(
                    '%(count)s usuarios tienen acceso a Cotizaciones Express.',
                    count=len(authorized_users),
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
