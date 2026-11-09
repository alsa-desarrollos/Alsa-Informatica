import math

from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    alsa_is_licensable = fields.Boolean(
        string="Genera licencia ALSA",
        help="Al confirmar una venta, las líneas de este producto crean o renuevan una licencia.",
    )
    alsa_license_duration_months = fields.Integer(
        string="Duración de licencia (meses)",
        default=12,
    )
    alsa_accesses_per_unit = fields.Integer(
        string="Accesos por unidad vendida",
        default=1,
    )

    @api.constrains("alsa_license_duration_months", "alsa_accesses_per_unit")
    def _check_alsa_license_configuration(self):
        for product in self.filtered("alsa_is_licensable"):
            if product.alsa_license_duration_months < 1:
                raise ValidationError(
                    "La duración de una licencia debe ser de al menos un mes."
                )
            if product.alsa_accesses_per_unit < 1:
                raise ValidationError(
                    "Los accesos por unidad vendida deben ser mayores que cero."
                )


class SaleOrder(models.Model):
    _inherit = "sale.order"

    alsa_active_license_ids = fields.Many2many(
        comodel_name="alsa.license",
        string="Licencias vigentes del cliente",
        compute="_compute_alsa_customer_assets",
    )
    alsa_active_license_count = fields.Integer(
        string="Cantidad de licencias vigentes",
        compute="_compute_alsa_customer_assets",
    )
    alsa_license_ids = fields.Many2many(
        comodel_name="alsa.license",
        relation="alsa_sale_order_license_rel",
        column1="order_id",
        column2="license_id",
        string="Licencias utilizadas en la venta",
        copy=False,
    )
    alsa_license_selection_line_ids = fields.One2many(
        comodel_name="alsa.sale.order.license.line",
        inverse_name="order_id",
        string="Licencias del cliente",
        copy=False,
    )
    alsa_customer_equipment_ids = fields.One2many(
        related="partner_id.alsa_equipment_ids",
        string="Equipos disponibles del cliente",
        readonly=False,
    )
    alsa_equipment_ids = fields.Many2many(
        comodel_name="alsa.equipment",
        relation="alsa_sale_order_equipment_rel",
        column1="order_id",
        column2="equipment_id",
        string="Equipos utilizados en la venta",
        copy=False,
    )
    alsa_equipment_selection_line_ids = fields.One2many(
        comodel_name="alsa.sale.order.equipment.line",
        inverse_name="order_id",
        string="Selección de equipos para la venta",
        copy=False,
    )

    @api.depends("partner_id")
    def _compute_alsa_customer_assets(self):
        today = fields.Date.context_today(self)
        License = self.env["alsa.license"]
        for order in self:
            licenses = License
            if order.partner_id:
                licenses = License.search(
                    [
                        ("partner_id", "=", order.partner_id.id),
                        ("expiration_date", ">=", today),
                    ]
                )
            order.alsa_active_license_ids = licenses
            order.alsa_active_license_count = len(licenses)

    @api.onchange("partner_id")
    def _onchange_alsa_partner_equipment(self):
        for order in self:
            if not order.partner_id:
                order.alsa_equipment_ids = False
                order.alsa_equipment_selection_line_ids = [Command.clear()]
                continue

            selected_equipment = order.alsa_equipment_ids.filtered(
                lambda equipment: equipment.partner_id == order.partner_id
            )
            selected_equipment |= order.alsa_equipment_selection_line_ids.filtered(
                lambda line: line.selected
                and line.equipment_id.partner_id == order.partner_id
            ).equipment_id
            order.alsa_equipment_ids = selected_equipment
            order.alsa_equipment_selection_line_ids = [
                Command.clear(),
                *[
                    Command.create(
                        {
                            "equipment_id": equipment.id,
                            "selected": equipment in selected_equipment,
                        }
                    )
                    for equipment in order.partner_id.alsa_equipment_ids
                ],
            ]

    @api.onchange("partner_id")
    def _onchange_alsa_partner_licenses(self):
        License = self.env["alsa.license"]
        today = fields.Date.context_today(self)
        for order in self:
            if not order.partner_id:
                order.alsa_license_ids = False
                order.alsa_license_selection_line_ids = [Command.clear()]
                continue

            available_licenses = License.search(
                [
                    ("partner_id", "=", order.partner_id.id),
                    ("expiration_date", ">=", today),
                ]
            )
            selected_licenses = order.alsa_license_ids.filtered(
                lambda license_record: license_record.partner_id == order.partner_id
            )
            selected_licenses |= order.alsa_license_selection_line_ids.filtered(
                lambda line: line.selected
                and line.license_id.partner_id == order.partner_id
            ).license_id
            order.alsa_license_ids = selected_licenses
            order.alsa_license_selection_line_ids = [
                Command.clear(),
                *[
                    Command.create(
                        {
                            "license_id": license_record.id,
                            "selected": license_record in selected_licenses,
                        }
                    )
                    for license_record in available_licenses
                ],
            ]

    @api.onchange("alsa_equipment_selection_line_ids")
    def _onchange_alsa_equipment_selection_lines(self):
        for order in self:
            order.alsa_equipment_ids = order.alsa_equipment_selection_line_ids.filtered(
                "selected"
            ).equipment_id

    @api.onchange("alsa_license_selection_line_ids")
    def _onchange_alsa_license_selection_lines(self):
        for order in self:
            order.alsa_license_ids = order.alsa_license_selection_line_ids.filtered(
                "selected"
            ).license_id

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order, values in zip(orders, vals_list):
            if "alsa_equipment_selection_line_ids" in values:
                order._alsa_sync_selected_equipment()
            if "alsa_license_selection_line_ids" in values:
                order._alsa_sync_selected_licenses()
        return orders

    def write(self, values):
        result = super().write(values)
        if (
            "alsa_equipment_selection_line_ids" in values
            and not self.env.context.get("alsa_skip_equipment_selection_sync")
        ):
            self._alsa_sync_selected_equipment()
        if (
            "alsa_license_selection_line_ids" in values
            and not self.env.context.get("alsa_skip_license_selection_sync")
        ):
            self._alsa_sync_selected_licenses()
        return result

    def _alsa_sync_selected_equipment(self):
        for order in self:
            if not order.alsa_equipment_selection_line_ids:
                continue
            equipment_ids = order.alsa_equipment_selection_line_ids.filtered(
                "selected"
            ).equipment_id.ids
            order.with_context(alsa_skip_equipment_selection_sync=True).write(
                {"alsa_equipment_ids": [Command.set(equipment_ids)]}
            )

    def _alsa_sync_selected_licenses(self):
        for order in self:
            if not order.alsa_license_selection_line_ids:
                continue
            license_ids = order.alsa_license_selection_line_ids.filtered(
                "selected"
            ).license_id.ids
            order.with_context(alsa_skip_license_selection_sync=True).write(
                {"alsa_license_ids": [Command.set(license_ids)]}
            )

    @api.constrains("partner_id", "alsa_equipment_ids")
    def _check_alsa_equipment_partner(self):
        for order in self:
            invalid_equipment = order.alsa_equipment_ids.filtered(
                lambda equipment: equipment.partner_id != order.partner_id
            )
            if invalid_equipment:
                raise ValidationError(
                    "Todos los equipos seleccionados deben pertenecer al cliente de la venta."
                )

    def action_confirm(self):
        self._alsa_sync_selected_equipment()
        self._alsa_sync_selected_licenses()
        result = super().action_confirm()
        for order in self:
            for line in order.order_line.filtered(
                lambda sale_line: not sale_line.display_type
                and sale_line.product_id
                and sale_line.alsa_is_licensable
            ):
                line._alsa_create_or_update_license(order.partner_id)
        return result


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    alsa_is_licensable = fields.Boolean(
        string="Genera licencia",
        related="product_id.product_tmpl_id.alsa_is_licensable",
    )
    alsa_license_id = fields.Many2one(
        comodel_name="alsa.license",
        string="Licencia a renovar",
        copy=False,
        ondelete="set null",
        help="Déjelo vacío para crear una licencia nueva al confirmar la venta.",
    )
    alsa_license_start_date = fields.Date(string="Inicio de licencia", copy=False)
    alsa_license_expiration_date = fields.Date(
        string="Vencimiento acordado",
        copy=False,
        help="Si se deja vacío, se aplicará la duración configurada en el producto.",
    )
    alsa_license_access_count = fields.Integer(
        string="Accesos contratados",
        copy=False,
    )

    @api.onchange("product_id", "product_uom_qty")
    def _onchange_alsa_license_values(self):
        for line in self:
            if line.alsa_is_licensable:
                accesses_per_unit = line.product_id.product_tmpl_id.alsa_accesses_per_unit
                line.alsa_license_access_count = max(
                    1, math.ceil(line.product_uom_qty * accesses_per_unit)
                )
                if not line.alsa_license_start_date:
                    line.alsa_license_start_date = fields.Date.context_today(line)
            else:
                line.alsa_license_id = False
                line.alsa_license_start_date = False
                line.alsa_license_expiration_date = False
                line.alsa_license_access_count = 0

    @api.onchange("alsa_license_id")
    def _onchange_alsa_license_id(self):
        for line in self:
            if line.alsa_license_id:
                line.alsa_license_start_date = fields.Date.context_today(line)
                line.alsa_license_access_count = max(
                    line.alsa_license_access_count,
                    line.alsa_license_id.allowed_access_count,
                )

    def _alsa_create_or_update_license(self, partner):
        self.ensure_one()
        today = fields.Date.context_today(self)
        product_template = self.product_id.product_tmpl_id
        license_record = self.alsa_license_id

        if license_record and license_record.partner_id != partner:
            raise ValidationError(
                "La licencia seleccionada en la línea de venta no pertenece al cliente del pedido."
            )

        start_date = self.alsa_license_start_date or today
        expiration_date = self.alsa_license_expiration_date
        if not expiration_date:
            if (
                license_record
                and license_record.sale_order_id == self.order_id
                and license_record.expiration_date
            ):
                # A second product line assigned to the same license in this
                # order must not renew the license for a second full period.
                expiration_date = license_record.expiration_date
            else:
                renewal_base = start_date
                if (
                    license_record
                    and license_record.expiration_date
                    and license_record.expiration_date >= today
                ):
                    renewal_base = license_record.expiration_date
                expiration_date = renewal_base + relativedelta(
                    months=product_template.alsa_license_duration_months
                )

        access_count = self.alsa_license_access_count or max(
            1,
            math.ceil(
                self.product_uom_qty * product_template.alsa_accesses_per_unit
            ),
        )
        values = {
            "partner_id": partner.id,
            "start_date": start_date,
            "expiration_date": expiration_date,
            "allowed_access_count": max(
                access_count,
                license_record.allowed_access_count if license_record else 0,
            ),
            "sale_order_id": self.order_id.id,
            "sale_order_line_id": self.id,
        }

        # Keep the first product as the historical/main product, while every
        # sold product is represented by its own editable license line.
        if not license_record or not license_record.product_id:
            values["product_id"] = self.product_id.id

        if license_record:
            license_record.write(values)
        else:
            values["name"] = "%s / %s" % (
                self.order_id.name,
                self.product_id.display_name,
            )
            license_record = self.env["alsa.license"].create(values)
            self.alsa_license_id = license_record

        product_line = license_record.product_line_ids.filtered(
            lambda item: item.sale_order_line_id == self
        )[:1]
        if not product_line:
            product_line = license_record.product_line_ids.filtered(
                lambda item: item.product_id == self.product_id
                and not item.sale_order_line_id
            )[:1]
        line_values = {
            "product_id": self.product_id.id,
            "quantity": self.product_uom_qty,
            "allowed_access_count": access_count,
            "sale_order_line_id": self.id,
        }
        if product_line:
            product_line.write(line_values)
        else:
            line_values["license_id"] = license_record.id
            self.env["alsa.license.line"].create(line_values)
        return license_record


class AlsaSaleOrderEquipmentLine(models.Model):
    _name = "alsa.sale.order.equipment.line"
    _description = "Selección de equipo en una venta"
    _order = "selected desc, equipment_id"
    _rec_name = "equipment_id"

    order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Venta",
        required=True,
        index=True,
        ondelete="cascade",
    )
    selected = fields.Boolean(string="Usar", default=True)
    equipment_id = fields.Many2one(
        comodel_name="alsa.equipment",
        string="Equipo",
        required=True,
        index=True,
        ondelete="cascade",
    )
    equipment_type = fields.Selection(
        related="equipment_id.equipment_type",
        string="Tipo",
        readonly=False,
    )
    specifications_summary = fields.Char(
        related="equipment_id.specifications_summary",
        string="Características",
        readonly=True,
    )
    product_id = fields.Many2one(
        related="equipment_id.product_id",
        string="Producto / Servicio relacionado",
        readonly=True,
    )
    product_summary = fields.Char(
        related="equipment_id.product_summary",
        string="Productos / Servicios relacionados",
        readonly=True,
    )
    license_id = fields.Many2one(
        related="equipment_id.license_id",
        string="Licencia asociada",
        readonly=True,
    )

    @api.onchange("equipment_id")
    def _onchange_equipment_id(self):
        if self.equipment_id:
            self.selected = True

    @api.constrains("order_id", "equipment_id")
    def _check_equipment_partner(self):
        for line in self:
            if (
                line.order_id.partner_id
                and line.equipment_id.partner_id != line.order_id.partner_id
            ):
                raise ValidationError(
                    "El equipo seleccionado debe pertenecer al cliente de la venta."
                )
            duplicate = self.search(
                [
                    ("order_id", "=", line.order_id.id),
                    ("equipment_id", "=", line.equipment_id.id),
                    ("id", "!=", line.id),
                ],
                limit=1,
            )
            if duplicate:
                raise ValidationError(
                    "Cada equipo sólo puede aparecer una vez en la tabla de la venta."
                )

    def init(self):
        """Preserve existing selections and prepare compact rows for saved orders."""
        self.env.cr.execute(
            """
            INSERT INTO alsa_sale_order_equipment_line
                (order_id, equipment_id, selected)
            SELECT sale_order.id,
                   equipment.id,
                   EXISTS (
                       SELECT 1
                         FROM alsa_sale_order_equipment_rel AS relation
                        WHERE relation.order_id = sale_order.id
                          AND relation.equipment_id = equipment.id
                   )
              FROM sale_order
              JOIN alsa_equipment AS equipment
                ON equipment.partner_id = sale_order.partner_id
             WHERE NOT EXISTS (
                       SELECT 1
                         FROM alsa_sale_order_equipment_line AS selection_line
                        WHERE selection_line.order_id = sale_order.id
                          AND selection_line.equipment_id = equipment.id
                   )
            """
        )


class AlsaSaleOrderLicenseLine(models.Model):
    _name = "alsa.sale.order.license.line"
    _description = "Selección de licencia en una venta"
    _order = "selected desc, license_id"
    _rec_name = "license_id"

    order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Venta",
        required=True,
        index=True,
        ondelete="cascade",
    )
    order_partner_id = fields.Many2one(
        related="order_id.partner_id",
        string="Cliente de la venta",
        readonly=True,
    )
    selected = fields.Boolean(string="Usar", default=True)
    license_id = fields.Many2one(
        comodel_name="alsa.license",
        string="Licencia / Código",
        required=True,
        index=True,
        ondelete="cascade",
        domain="[('partner_id', '=', order_partner_id)]",
    )
    product_id = fields.Many2one(
        related="license_id.product_id",
        string="Producto / Servicio",
        readonly=False,
    )
    product_summary = fields.Char(
        related="license_id.product_summary",
        string="Productos / Servicios",
        readonly=True,
    )
    start_date = fields.Date(
        related="license_id.start_date",
        string="Fecha de inicio",
        readonly=False,
    )
    expiration_date = fields.Date(
        related="license_id.expiration_date",
        string="Fecha de vencimiento",
        readonly=False,
    )
    allowed_access_count = fields.Integer(
        related="license_id.allowed_access_count",
        string="Accesos / Usuarios",
        readonly=False,
    )
    state = fields.Selection(
        related="license_id.state",
        string="Estado",
        readonly=True,
    )

    @api.onchange("license_id")
    def _onchange_license_id(self):
        if self.license_id:
            self.selected = True

    def action_open_license_details(self):
        self.ensure_one()
        return self.license_id.action_open_sales_details()

    @api.constrains("order_id", "license_id")
    def _check_license_partner(self):
        for line in self:
            if (
                line.order_id.partner_id
                and line.license_id.partner_id != line.order_id.partner_id
            ):
                raise ValidationError(
                    "La licencia seleccionada debe pertenecer al cliente de la venta."
                )
            duplicate = self.search(
                [
                    ("order_id", "=", line.order_id.id),
                    ("license_id", "=", line.license_id.id),
                    ("id", "!=", line.id),
                ],
                limit=1,
            )
            if duplicate:
                raise ValidationError(
                    "Cada licencia sólo puede aparecer una vez en la tabla de la venta."
                )

    def init(self):
        """Prepare license rows and preserve explicit renewal selections."""
        self.env.cr.execute(
            """
            INSERT INTO alsa_sale_order_license_line
                (order_id, license_id, selected)
            SELECT sale_order.id,
                   license.id,
                   EXISTS (
                       SELECT 1
                         FROM sale_order_line AS sale_line
                        WHERE sale_line.order_id = sale_order.id
                          AND sale_line.alsa_license_id = license.id
                   )
              FROM sale_order
              JOIN alsa_license AS license
                ON license.partner_id = sale_order.partner_id
               AND license.expiration_date >= CURRENT_DATE
             WHERE NOT EXISTS (
                       SELECT 1
                         FROM alsa_sale_order_license_line AS selection_line
                        WHERE selection_line.order_id = sale_order.id
                          AND selection_line.license_id = license.id
                   )
            """
        )
