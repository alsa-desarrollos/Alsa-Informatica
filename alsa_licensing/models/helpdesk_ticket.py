from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    alsa_active_license_ids = fields.Many2many(
        comodel_name="alsa.license",
        string="Licencias vigentes",
        compute="_compute_alsa_active_licenses",
    )
    alsa_customer_equipment_ids = fields.One2many(
        related="partner_id.alsa_equipment_ids",
        string="Equipos disponibles del cliente",
        readonly=False,
    )
    alsa_equipment_ids = fields.Many2many(
        comodel_name="alsa.equipment",
        relation="alsa_helpdesk_ticket_equipment_rel",
        column1="ticket_id",
        column2="equipment_id",
        string="Equipos atendidos",
        copy=False,
    )
    alsa_equipment_selection_line_ids = fields.One2many(
        comodel_name="alsa.helpdesk.ticket.equipment.line",
        inverse_name="ticket_id",
        string="Selección de equipos para atender",
        copy=False,
    )

    @api.depends("partner_id")
    def _compute_alsa_active_licenses(self):
        today = fields.Date.context_today(self)
        License = self.env["alsa.license"]
        for ticket in self:
            licenses = License
            if ticket.partner_id:
                licenses = License.search(
                    [
                        (
                            "partner_id.commercial_partner_id",
                            "=",
                            ticket.partner_id.commercial_partner_id.id,
                        ),
                        ("expiration_date", ">=", today),
                    ]
                )
            ticket.alsa_active_license_ids = licenses

    @api.onchange("partner_id")
    def _onchange_alsa_partner_id(self):
        for ticket in self:
            if not ticket.partner_id:
                ticket.alsa_equipment_ids = False
                ticket.alsa_equipment_selection_line_ids = [Command.clear()]
                continue

            selected_equipment = ticket.alsa_equipment_ids.filtered(
                lambda equipment: equipment.partner_id == ticket.partner_id
            )
            selected_equipment |= ticket.alsa_equipment_selection_line_ids.filtered(
                lambda line: line.selected
                and line.equipment_id.partner_id == ticket.partner_id
            ).equipment_id
            ticket.alsa_equipment_ids = selected_equipment
            ticket.alsa_equipment_selection_line_ids = [
                Command.clear(),
                *[
                    Command.create(
                        {
                            "equipment_id": equipment.id,
                            "selected": equipment in selected_equipment,
                        }
                    )
                    for equipment in ticket.partner_id.alsa_equipment_ids
                ],
            ]

    @api.onchange("alsa_equipment_selection_line_ids")
    def _onchange_alsa_equipment_selection_lines(self):
        for ticket in self:
            ticket.alsa_equipment_ids = (
                ticket.alsa_equipment_selection_line_ids.filtered(
                    "selected"
                ).equipment_id
            )

    @api.model_create_multi
    def create(self, vals_list):
        tickets = super().create(vals_list)
        for ticket, values in zip(tickets, vals_list):
            if "alsa_equipment_selection_line_ids" in values:
                ticket._alsa_sync_selected_equipment()
        return tickets

    def write(self, values):
        result = super().write(values)
        if (
            "alsa_equipment_selection_line_ids" in values
            and not self.env.context.get("alsa_skip_equipment_selection_sync")
        ):
            self._alsa_sync_selected_equipment()
        return result

    def _alsa_sync_selected_equipment(self):
        for ticket in self:
            if not ticket.alsa_equipment_selection_line_ids:
                continue
            equipment_ids = ticket.alsa_equipment_selection_line_ids.filtered(
                "selected"
            ).equipment_id.ids
            ticket.with_context(alsa_skip_equipment_selection_sync=True).write(
                {"alsa_equipment_ids": [Command.set(equipment_ids)]}
            )

    @api.constrains("partner_id", "alsa_equipment_ids")
    def _check_alsa_equipment_partner(self):
        for ticket in self:
            invalid_equipment = ticket.alsa_equipment_ids.filtered(
                lambda equipment: equipment.partner_id != ticket.partner_id
            )
            if invalid_equipment:
                raise ValidationError(
                    "Todos los equipos seleccionados deben pertenecer al cliente del ticket."
                )


class AlsaHelpdeskTicketEquipmentLine(models.Model):
    _name = "alsa.helpdesk.ticket.equipment.line"
    _description = "Selección de equipo en un ticket de soporte"
    _order = "selected desc, equipment_id"
    _rec_name = "equipment_id"

    ticket_id = fields.Many2one(
        comodel_name="helpdesk.ticket",
        string="Ticket",
        required=True,
        index=True,
        ondelete="cascade",
    )
    selected = fields.Boolean(string="Atender", default=True)
    equipment_id = fields.Many2one(
        comodel_name="alsa.equipment",
        string="Equipo",
        required=True,
        index=True,
        ondelete="cascade",
    )
    partner_commercial_id = fields.Many2one(
        related="equipment_id.partner_id.commercial_partner_id",
        string="Empresa del equipo",
        readonly=True,
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
    specifications = fields.Html(
        related="equipment_id.specifications",
        string="Características / Especificaciones",
        readonly=False,
    )
    product_id = fields.Many2one(
        related="equipment_id.product_id",
        string="Producto / Servicio",
        readonly=False,
    )
    product_summary = fields.Char(
        related="equipment_id.product_summary",
        string="Productos / Servicios",
        readonly=True,
    )
    product_preview = fields.Char(
        related="equipment_id.product_preview",
        string="Vista previa de productos relacionados",
        readonly=True,
    )
    license_id = fields.Many2one(
        related="equipment_id.license_id",
        string="Licencia",
        readonly=False,
        domain="[('partner_id.commercial_partner_id', '=', partner_commercial_id)]",
    )

    @api.onchange("equipment_id")
    def _onchange_equipment_id(self):
        if self.equipment_id:
            self.selected = True

    def action_open_equipment_details(self):
        self.ensure_one()
        return self.equipment_id.action_open_support_details()

    @api.constrains("ticket_id", "equipment_id")
    def _check_equipment_partner(self):
        for line in self:
            if (
                line.ticket_id.partner_id
                and line.equipment_id.partner_id != line.ticket_id.partner_id
            ):
                raise ValidationError(
                    "El equipo seleccionado debe pertenecer al cliente del ticket."
                )
            duplicate = self.search(
                [
                    ("ticket_id", "=", line.ticket_id.id),
                    ("equipment_id", "=", line.equipment_id.id),
                    ("id", "!=", line.id),
                ],
                limit=1,
            )
            if duplicate:
                raise ValidationError(
                    "Cada equipo sólo puede aparecer una vez en la tabla del ticket."
                )

    def init(self):
        """Preserve selections and prepare compact rows for existing tickets."""
        self.env.cr.execute(
            """
            INSERT INTO alsa_helpdesk_ticket_equipment_line
                (ticket_id, equipment_id, selected)
            SELECT ticket.id,
                   equipment.id,
                   EXISTS (
                       SELECT 1
                         FROM alsa_helpdesk_ticket_equipment_rel AS relation
                        WHERE relation.ticket_id = ticket.id
                          AND relation.equipment_id = equipment.id
                   )
              FROM helpdesk_ticket AS ticket
              JOIN alsa_equipment AS equipment
                ON equipment.partner_id = ticket.partner_id
             WHERE NOT EXISTS (
                       SELECT 1
                         FROM alsa_helpdesk_ticket_equipment_line AS selection_line
                        WHERE selection_line.ticket_id = ticket.id
                          AND selection_line.equipment_id = equipment.id
                   )
            """
        )
