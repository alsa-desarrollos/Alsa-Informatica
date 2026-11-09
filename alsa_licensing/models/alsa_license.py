from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AlsaLicense(models.Model):
    _name = "alsa.license"
    _description = "Licencia de cliente"
    _order = "expiration_date desc, name"

    name = fields.Char(string="Licencia / Código", required=True, index=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Cliente",
        required=True,
        index=True,
        ondelete="restrict",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Producto / Servicio principal",
        index=True,
        ondelete="restrict",
        help="Producto principal conservado por compatibilidad. Use las líneas para asociar varios productos.",
    )
    product_line_ids = fields.One2many(
        comodel_name="alsa.license.line",
        inverse_name="license_id",
        string="Productos / Servicios",
        copy=True,
    )
    product_count = fields.Integer(
        string="Productos",
        compute="_compute_product_summary",
    )
    product_summary = fields.Char(
        string="Resumen de productos / servicios",
        compute="_compute_product_summary",
    )
    start_date = fields.Date(
        string="Fecha de inicio",
        required=True,
        default=fields.Date.context_today,
    )
    expiration_date = fields.Date(
        string="Fecha de vencimiento",
        required=True,
        index=True,
    )
    state = fields.Selection(
        selection=[("active", "Activa"), ("expired", "Expirada")],
        string="Estado",
        compute="_compute_state",
        search="_search_state",
    )
    allowed_access_count = fields.Integer(
        string="Accesos / Usuarios permitidos",
        required=True,
        default=1,
    )
    sale_order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Último pedido de venta",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    sale_order_line_id = fields.Many2one(
        comodel_name="sale.order.line",
        string="Última línea de venta",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    equipment_ids = fields.One2many(
        comodel_name="alsa.equipment",
        inverse_name="license_id",
        string="Equipos",
    )
    equipment_count = fields.Integer(
        string="Cantidad de equipos",
        compute="_compute_equipment_count",
    )

    def action_open_sales_details(self):
        self.ensure_one()
        form_view = self.env.ref(
            "alsa_licensing.view_alsa_license_sales_modal_form"
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Información de la licencia",
            "res_model": "alsa.license",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(form_view.id, "form")],
            "target": "new",
        }

    @api.depends("product_id", "product_line_ids.product_id")
    def _compute_product_summary(self):
        for license_record in self:
            products = license_record.product_line_ids.mapped("product_id")
            if license_record.product_id:
                products |= license_record.product_id
            license_record.product_count = len(products)
            license_record.product_summary = ", ".join(products.mapped("display_name"))

    @api.depends("equipment_ids")
    def _compute_equipment_count(self):
        for license_record in self:
            license_record.equipment_count = len(license_record.equipment_ids)

    @api.model_create_multi
    def create(self, vals_list):
        licenses = super().create(vals_list)
        for license_record, values in zip(licenses, vals_list):
            product_id = values.get("product_id")
            if product_id and product_id not in license_record.product_line_ids.product_id.ids:
                self.env["alsa.license.line"].create(
                    {
                        "license_id": license_record.id,
                        "product_id": product_id,
                        "allowed_access_count": license_record.allowed_access_count,
                    }
                )
        return licenses

    def write(self, values):
        result = super().write(values)
        product_id = values.get("product_id")
        if product_id:
            for license_record in self:
                if product_id not in license_record.product_line_ids.product_id.ids:
                    self.env["alsa.license.line"].create(
                        {
                            "license_id": license_record.id,
                            "product_id": product_id,
                            "allowed_access_count": license_record.allowed_access_count,
                        }
                    )
        return result

    @api.depends("expiration_date")
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for license_record in self:
            license_record.state = (
                "active"
                if license_record.expiration_date
                and license_record.expiration_date >= today
                else "expired"
            )

    @api.model
    def _search_state(self, operator, value):
        today = fields.Date.context_today(self)
        active_domain = [("expiration_date", ">=", today)]
        expired_domain = [("expiration_date", "<", today)]

        if operator in ("=", "=="):
            if value == "active":
                return active_domain
            if value == "expired":
                return expired_domain
        elif operator == "!=":
            if value == "active":
                return expired_domain
            if value == "expired":
                return active_domain
        elif operator in ("in", "not in"):
            values = set(value or [])
            if operator == "not in":
                values = {"active", "expired"} - values
            if values == {"active", "expired"}:
                return []
            if values == {"active"}:
                return active_domain
            if values == {"expired"}:
                return expired_domain
            if not values:
                return [("id", "=", 0)]
        return NotImplemented

    @api.constrains("start_date", "expiration_date")
    def _check_dates(self):
        for license_record in self:
            if (
                license_record.start_date
                and license_record.expiration_date
                and license_record.expiration_date < license_record.start_date
            ):
                raise ValidationError(
                    "La fecha de vencimiento no puede ser anterior a la fecha de inicio."
                )

    @api.constrains("allowed_access_count")
    def _check_allowed_access_count(self):
        for license_record in self:
            if license_record.allowed_access_count < 1:
                raise ValidationError(
                    "La cantidad de accesos o usuarios permitidos debe ser mayor que cero."
                )


class AlsaLicenseLine(models.Model):
    _name = "alsa.license.line"
    _description = "Producto o servicio de una licencia"
    _order = "sequence, id"
    _rec_name = "product_id"

    sequence = fields.Integer(default=10)
    license_id = fields.Many2one(
        comodel_name="alsa.license",
        string="Licencia",
        required=True,
        index=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Producto / Servicio",
        required=True,
        index=True,
        ondelete="restrict",
    )
    product_default_code = fields.Char(
        related="product_id.default_code",
        string="Código/Número de referencia",
        readonly=True,
    )
    quantity = fields.Float(string="Cantidad", required=True, default=1.0)
    allowed_access_count = fields.Integer(
        string="Accesos / Usuarios",
        required=True,
        default=1,
    )
    sale_order_line_id = fields.Many2one(
        comodel_name="sale.order.line",
        string="Línea de venta",
        readonly=True,
        copy=False,
        ondelete="set null",
    )

    @api.constrains("quantity", "allowed_access_count")
    def _check_positive_values(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError("La cantidad del producto debe ser mayor que cero.")
            if line.allowed_access_count < 1:
                raise ValidationError(
                    "Los accesos o usuarios de la línea deben ser mayores que cero."
                )

    def init(self):
        """Create a product line for licenses created before multi-product support."""
        self.env.cr.execute(
            """
            INSERT INTO alsa_license_line
                (license_id, sequence, product_id, quantity, allowed_access_count)
            SELECT license.id, 10, license.product_id, 1.0,
                   GREATEST(license.allowed_access_count, 1)
              FROM alsa_license AS license
             WHERE license.product_id IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1
                      FROM alsa_license_line AS line
                     WHERE line.license_id = license.id
                       AND line.product_id = license.product_id
               )
            """
        )
