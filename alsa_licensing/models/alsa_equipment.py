import html
import re

from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError


class AlsaEquipment(models.Model):
    _name = "alsa.equipment"
    _description = "Equipo de cliente"
    _order = "partner_id, name"

    name = fields.Char(string="Nombre del equipo", required=True, index=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Cliente",
        required=True,
        index=True,
        ondelete="restrict",
    )
    equipment_type = fields.Selection(
        selection=[("terminal", "Terminal"), ("server", "Servidor")],
        string="Tipo de equipo",
        required=True,
        default="terminal",
        index=True,
    )
    specifications = fields.Html(
        string="Características / Especificaciones técnicas",
        sanitize=True,
    )
    observations = fields.Html(
        string="Observaciones sobre el equipo",
        sanitize=True,
    )
    specifications_summary = fields.Char(
        string="Resumen de características",
        compute="_compute_specifications_summary",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Producto / Servicio principal",
        ondelete="restrict",
    )
    product_ids = fields.Many2many(
        comodel_name="product.product",
        relation="alsa_equipment_product_rel",
        column1="equipment_id",
        column2="product_id",
        string="Productos / Servicios relacionados",
    )
    product_summary = fields.Char(
        string="Productos / Servicios relacionados",
        compute="_compute_product_summary",
    )
    license_id = fields.Many2one(
        comodel_name="alsa.license",
        string="Licencia asociada",
        ondelete="set null",
        domain="[('partner_id', '=', partner_id)]",
    )
    license_product_summary = fields.Char(
        related="license_id.product_summary",
        string="Productos / Servicios de la licencia",
        readonly=True,
    )
    license_product_line_ids = fields.Many2many(
        comodel_name="alsa.license.line",
        string="Productos / Servicios de la licencia",
        compute="_compute_license_product_line_ids",
        inverse="_inverse_license_product_line_ids",
        readonly=False,
    )
    license_expiration_date = fields.Date(
        related="license_id.expiration_date",
        string="Vencimiento de licencia",
        readonly=True,
    )
    license_state = fields.Selection(
        related="license_id.state",
        string="Estado de licencia",
        readonly=True,
    )

    @api.depends("specifications")
    def _compute_specifications_summary(self):
        for equipment in self:
            plain_text = re.sub(r"<[^>]+>", " ", equipment.specifications or "")
            plain_text = " ".join(html.unescape(plain_text).split())
            equipment.specifications_summary = (
                plain_text[:97] + "..." if len(plain_text) > 100 else plain_text
            )

    @api.depends("product_id", "product_ids")
    def _compute_product_summary(self):
        for equipment in self:
            products = equipment.product_ids
            if equipment.product_id:
                products |= equipment.product_id
            equipment.product_summary = ", ".join(products.mapped("display_name"))

    @api.depends("license_id", "license_id.product_line_ids")
    def _compute_license_product_line_ids(self):
        for equipment in self:
            equipment.license_product_line_ids = equipment.license_id.product_line_ids

    def _inverse_license_product_line_ids(self):
        for equipment in self.filtered("license_id"):
            equipment.license_id.product_line_ids = [
                Command.set(equipment.license_product_line_ids.ids)
            ]

    def action_open_support_details(self):
        self.ensure_one()
        form_view = self.env.ref(
            "alsa_licensing.view_alsa_equipment_support_modal_form"
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Información del equipo",
            "res_model": "alsa.equipment",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(form_view.id, "form")],
            "target": "new",
        }

    @api.model_create_multi
    def create(self, vals_list):
        prepared_values = []
        for values in vals_list:
            values = dict(values)
            license_id = values.get("license_id")
            if license_id and not values.get("partner_id"):
                license_record = self.env["alsa.license"].browse(license_id).exists()
                if license_record:
                    values["partner_id"] = license_record.partner_id.id
            product_id = values.get("product_id")
            if product_id:
                product_commands = list(values.get("product_ids") or [])
                product_commands.append(Command.link(product_id))
                values["product_ids"] = product_commands
            prepared_values.append(values)
        return super().create(prepared_values)

    def write(self, values):
        values = dict(values)
        license_id = values.get("license_id")
        if license_id and "partner_id" not in values:
            license_record = self.env["alsa.license"].browse(license_id).exists()
            if license_record:
                values["partner_id"] = license_record.partner_id.id
        product_id = values.get("product_id")
        if product_id:
            product_commands = list(values.get("product_ids") or [])
            product_commands.append(Command.link(product_id))
            values["product_ids"] = product_commands
        return super().write(values)

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.license_id and self.license_id.partner_id != self.partner_id:
            self.license_id = False
        return {
            "domain": {
                "license_id": [("partner_id", "=", self.partner_id.id)]
                if self.partner_id
                else [("id", "=", False)]
            }
        }

    @api.onchange("license_id")
    def _onchange_license_id(self):
        if self.license_id:
            self.partner_id = self.license_id.partner_id
            if not self.product_id:
                self.product_id = self.license_id.product_id
            if self.license_id.product_id:
                self.product_ids = self.product_ids | self.license_id.product_id

    @api.constrains("partner_id", "license_id")
    def _check_license_partner(self):
        for equipment in self:
            if (
                equipment.license_id
                and equipment.partner_id != equipment.license_id.partner_id
            ):
                raise ValidationError(
                    "La licencia asociada debe pertenecer al mismo cliente que el equipo."
                )

    def init(self):
        """Preserve the former single product as the first related product."""
        self.env.cr.execute(
            """
            INSERT INTO alsa_equipment_product_rel (equipment_id, product_id)
            SELECT equipment.id, equipment.product_id
              FROM alsa_equipment AS equipment
             WHERE equipment.product_id IS NOT NULL
            ON CONFLICT DO NOTHING
            """
        )
