from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    alsa_license_ids = fields.One2many(
        comodel_name="alsa.license",
        inverse_name="partner_id",
        string="Licencias",
    )
    alsa_equipment_ids = fields.One2many(
        comodel_name="alsa.equipment",
        inverse_name="partner_id",
        string="Equipos",
    )
    alsa_active_license_count = fields.Integer(
        string="Licencias vigentes",
        compute="_compute_alsa_active_license_count",
    )

    @api.depends("alsa_license_ids.expiration_date")
    def _compute_alsa_active_license_count(self):
        today = fields.Date.context_today(self)
        for partner in self:
            partner.alsa_active_license_count = len(
                partner.alsa_license_ids.filtered(
                    lambda license_record: license_record.expiration_date
                    and license_record.expiration_date >= today
                )
            )
